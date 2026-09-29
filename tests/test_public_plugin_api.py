# Copyright 2025-2026 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Contract tests for external plugin authors."""

import builtins
import importlib
import subprocess
import sys

from genro_routes import Router, RoutingClass, route
from genro_routes.plugins import BasePlugin, MethodEntry


def test_public_plugin_types_preserve_legacy_identity():
    import genro_routes.plugins as plugins
    from genro_routes.plugins import _base_plugin

    assert {"BasePlugin", "MethodEntry"} <= set(plugins.__all__)
    assert BasePlugin is _base_plugin.BasePlugin
    assert MethodEntry is _base_plugin.MethodEntry


def test_public_plugin_import_in_fresh_interpreter():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from genro_routes.plugins import BasePlugin, MethodEntry
from genro_routes import Router
from genro_routes.plugins import _base_plugin
assert BasePlugin is _base_plugin.BasePlugin
assert MethodEntry is _base_plugin.MethodEntry
expected = {"auth", "env", "logging", "pydantic", "channel"}
assert set(Router.available_plugins()) == expected
assert {
    name.removeprefix("genro_routes.plugins.")
    for name in sys.modules
    if name.startswith("genro_routes.plugins.")
} == expected | {"_base_plugin"}
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_reexports_do_not_import_or_register_concrete_plugins(monkeypatch):
    import genro_routes.plugins as plugins

    concrete = set(Router.available_plugins())
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("genro_routes.plugins."):
            assert name.rsplit(".", 1)[-1] not in concrete
        if name == "genro_routes.plugins" or (
            level and globals and globals.get("__package__") == "genro_routes.plugins"
        ):
            assert name not in concrete
            assert not (set(fromlist or ()) & concrete)
        return original_import(name, globals, locals, fromlist, level)

    def unexpected_registration(*args, **kwargs):
        raise AssertionError("Public re-exports must not register plugins")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(Router, "register_plugin", unexpected_registration)
    importlib.reload(plugins)
    assert plugins.BasePlugin is BasePlugin
    assert plugins.MethodEntry is MethodEntry


def test_external_plugin_using_public_api(monkeypatch):
    router_module = importlib.import_module("genro_routes.core.router")
    monkeypatch.setattr(router_module, "_PLUGIN_REGISTRY", Router.available_plugins())
    seen = []

    class ExternalPlugin(BasePlugin):
        plugin_code = "external_public_api"
        plugin_description = "External plugin using public extension types"

        def configure(self, enabled: bool = True):
            pass

        def wrap_handler(self, router, entry: MethodEntry, call_next):
            assert isinstance(entry, MethodEntry)

            def wrapper(*args, **kwargs):
                seen.append(entry.name)
                return call_next(*args, **kwargs) + "!"

            return wrapper

    class Service(RoutingClass):
        @route()
        def greet(self, name):
            return f"Hello {name}"

    Router.register_plugin(ExternalPlugin)
    service = Service()
    service.route.plug("external_public_api")
    assert service.route.node("greet")("Ada") == "Hello Ada!"
    assert seen == ["greet"]
    assert Service().route.node("greet")("Ada") == "Hello Ada"
