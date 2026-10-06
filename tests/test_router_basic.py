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

"""Tests for instance-based Router core functionality."""

import pytest

from genro_routes import Router, RoutingClass, route
from genro_routes.plugins._base_plugin import BasePlugin  # Not public API


def test_orders_quick_example():
    class OrdersAPI(RoutingClass):
        def __init__(self, label: str):
            self.label = label

        @route()
        def list(self):
            return ["order-1", "order-2"]

        @route()
        def retrieve(self, ident: str):
            return f"{self.label}:{ident}"

        @route()
        def create(self, payload: dict):
            return {"status": "created", **payload}

    orders = OrdersAPI("acme")
    # Use node() and call it
    assert orders.route.node("list")() == ["order-1", "order-2"]
    assert orders.route.node("retrieve")("42") == "acme:42"
    overview = orders.route.nodes()
    assert set(overview["entries"].keys()) == {"list", "retrieve", "create"}


class Service(RoutingClass):
    def __init__(self, label: str):
        self.label = label

    @route()
    def describe(self):
        return f"service:{self.label}"


class SubService(RoutingClass):
    def __init__(self, prefix: str):
        self.prefix = prefix
        self.route.prefix = "handle_"

    @route()
    def handle_list(self):
        return f"{self.prefix}:list"

    @route(name="detail")
    def handle_detail(self, ident: int):
        return f"{self.prefix}:detail:{ident}"


class RootAPI(RoutingClass):
    def __init__(self):
        self.services: list[Service] = []


class CapturePlugin(BasePlugin):
    plugin_code = "capture"
    plugin_description = "Captures calls for testing"

    def __init__(self, router, **config):
        super().__init__(router, **config)
        self.calls = []

    def on_decore(self, router, func, entry):
        entry.metadata["capture"] = True

    def wrap_handler(self, router, entry, call_next):
        def wrapper(*args, **kwargs):
            self.calls.append("wrap")
            return call_next(*args, **kwargs)

        return wrapper


# Register custom plugin
Router.register_plugin(CapturePlugin)


class PluginService(RoutingClass):
    def __init__(self):
        self.touched = False
        self.route.plug("capture")

    @route()
    def do_work(self):
        self.touched = True
        return "ok"


class TogglePlugin(BasePlugin):
    plugin_code = "toggle"
    plugin_description = "Toggle test plugin"

    def __init__(self, router, **config):
        super().__init__(router, **config)

    def wrap_handler(self, router, entry, call_next):
        def wrapper(*args, **kwargs):
            router.set_runtime_data(entry.name, self.name, "last", True)
            return call_next(*args, **kwargs)

        return wrapper


# Register custom plugin
Router.register_plugin(TogglePlugin)


class ToggleService(RoutingClass):
    def __init__(self):
        self.route.plug("toggle")

    @route()
    def touch(self):
        return "done"


class DynamicRouterService(RoutingClass):
    def __init__(self):
        self.route.add_entry(self.dynamic_alpha)
        self.route.add_entry("dynamic_beta")

    def dynamic_alpha(self):
        return "alpha"

    def dynamic_beta(self):
        return "beta"


def test_prefix_and_name_override():
    sub = SubService("users")

    assert sub.route.node("list")() == "users:list"
    assert sub.route.node("detail")(10) == "users:detail:10"


def test_plugins_are_per_instance_and_accessible():
    svc = PluginService()
    assert svc.route.capture.calls == []
    result = svc.route.node("do_work")()
    assert result == "ok"
    assert svc.touched is True
    assert svc.route.capture.calls == ["wrap"]
    other = PluginService()
    assert other.route.capture.calls == []


def test_dynamic_router_add_entry_runtime():
    svc = DynamicRouterService()
    assert svc.route.node("dynamic_alpha")() == "alpha"
    assert svc.route.node("dynamic_beta")() == "beta"
    # Adding via string
    svc.route.add_entry("dynamic_alpha", name="alpha_alias")
    assert svc.route.node("alpha_alias")() == "alpha"


def test_plugin_enable_disable_runtime_data():
    svc = ToggleService()
    node = svc.route.node("touch")
    # Initially enabled
    node()
    assert svc.route.get_runtime_data("touch", "toggle", "last") is True
    # Disable and verify
    svc.route.set_plugin_enabled("touch", "toggle", False)
    svc.route.set_runtime_data("touch", "toggle", "last", None)
    node()
    assert svc.route.get_runtime_data("touch", "toggle", "last") is None
    # Re-enable
    svc.route.set_plugin_enabled("touch", "toggle", True)
    node()
    assert svc.route.get_runtime_data("touch", "toggle", "last") is True


def test_dotted_path_and_nodes_with_attached_child():
    class Child(RoutingClass):
        @route()
        def ping(self):
            return "pong"

    class Parent(RoutingClass):
        def __init__(self):
            self.child = Child()
            self.add_branches({"name": "child", "instance": self.child})

    parent = Parent()
    assert parent.route.node("child/ping")() == "pong"
    tree = parent.route.nodes()
    assert "child" in tree["routers"]


# ============================================================================
# Single router default tests (@route() without arguments)
# ============================================================================


class TestSingleRouterDefault:
    """Test @route() without arguments on the class's single router."""

    def test_route_without_args_uses_single_router(self):
        """@route() without arguments uses the single router."""

        class Table(RoutingClass):
            @route()
            def add(self, data):
                return f"added:{data}"

            @route()
            def get(self, key):
                return f"got:{key}"

        t = Table()
        assert t.route.node("add")("x") == "added:x"
        assert t.route.node("get")("y") == "got:y"
        assert set(t.route.nodes()["entries"].keys()) == {"add", "get"}

    def test_route_with_custom_entry_name(self):
        """@route(name='custom') works with the single router."""

        class Table(RoutingClass):
            @route(name="custom_add")
            def add_record(self, data):
                return f"added:{data}"

        t = Table()
        assert t.route.node("custom_add")("x") == "added:x"
        assert "custom_add" in t.route.nodes()["entries"]
        assert "add_record" not in t.route.nodes()["entries"]

    def test_route_inheritance_with_single_router(self):
        """Subclass inherits @route() methods."""

        class BaseTable(RoutingClass):
            @route()
            def list(self):
                return "base_list"

        class ExtendedTable(BaseTable):
            @route()
            def add(self):
                return "extended_add"

        t = ExtendedTable()
        assert t.route.node("list")() == "base_list"
        assert t.route.node("add")() == "extended_add"
        entries = t.route.nodes()["entries"]
        assert "list" in entries
        assert "add" in entries

    def test_route_with_plugin_kwargs(self):
        """@route() with kwargs works with the single router."""

        class TaggedTable(RoutingClass):
            def __init__(self):
                self.route.plug("auth")

            @route(auth_rule="admin")
            def admin_only(self):
                return "admin"

            @route(auth_rule="public")
            def public_action(self):
                return "public"

        t = TaggedTable()
        entries = t.route.nodes(auth_tags="admin")["entries"]
        assert "admin_only" in entries
        assert "public_action" not in entries


# ============================================================================
# RoutingClass requirement tests
# ============================================================================


class TestRoutingClassRequirement:
    """Test that Router requires RoutingClass."""

    def test_router_requires_routed_class(self):
        """Router raises TypeError if owner is not a RoutingClass."""

        class PlainClass:
            pass

        with pytest.raises(TypeError, match="must be a RoutingClass"):
            Router(PlainClass())

    def test_router_requires_owner(self):
        """Router raises ValueError if owner is None."""
        with pytest.raises(ValueError, match="requires a parent instance"):
            Router(None)

    def test_router_accepts_routed_class(self):
        """Router accepts RoutingClass instances."""

        class MyService(RoutingClass):
            pass

        svc = MyService()
        router = Router(svc)
        assert router.instance is svc

    def test_second_router_on_same_owner_raises(self):
        """Creating a second Router on the same owner raises ValueError."""

        class MyService(RoutingClass):
            pass

        svc = MyService()
        _ = svc.route  # auto-created router occupies the single slot
        with pytest.raises(ValueError, match="already has a router"):
            Router(svc)


# -----------------------------------------------------------------------------
# Tests for default_entry behavior with node()
# -----------------------------------------------------------------------------


class TestDefaultEntry:
    """Tests for Router.default_entry behavior with node()."""

    def test_default_entry_is_index_by_default(self):
        """Router default_entry is 'index' by default."""

        class Service(RoutingClass):
            @route()
            def action(self):
                return "action"

        svc = Service()
        assert svc.route.default_entry == "index"

    def test_default_entry_can_be_customized(self):
        """Router default_entry can be set to a custom value."""

        class Service(RoutingClass):
            def __init__(self):
                self.route.default_entry = "handle"

            @route()
            def handle(self):
                return "handle"

        svc = Service()
        assert svc.route.default_entry == "handle"

    def test_leading_slash_is_stripped(self):
        """node() strips leading slash from path."""

        class Service(RoutingClass):
            @route()
            def action(self):
                return "action result"

        svc = Service()

        # Both should work identically
        assert svc.route.node("action")() == "action result"
        assert svc.route.node("/action")() == "action result"

    def test_leading_slash_with_hierarchy(self):
        """node() strips leading slash in hierarchical paths."""

        class Child(RoutingClass):
            @route()
            def handler(self):
                return "child handler"

        class Root(RoutingClass):
            def __init__(self):
                self.child = Child()
                self.add_branches({"name": "child", "instance": self.child})

        root = Root()

        # Both should work identically
        assert root.route.node("child/handler")() == "child handler"
        assert root.route.node("/child/handler")() == "child handler"


class TestSingleRouteMarker:
    """A function carries one @route marker; a second decorator is refused."""

    def test_second_route_raises_at_decoration(self):
        with pytest.raises(TypeError) as excinfo:

            class Svc(RoutingClass):
                @route(name="second")
                @route(name="first")
                def op(self):
                    return "ok"

        assert "op" in str(excinfo.value)

    def test_single_route_registers_the_entry(self):
        class Svc(RoutingClass):
            @route()
            def op(self):
                return "ok"

        svc = Svc()
        assert set(svc.route.nodes()["entries"].keys()) == {"op"}
        assert svc.route.node("op")() == "ok"

    def test_explicit_name_still_wins(self):
        class Svc(RoutingClass):
            @route(name="custom")
            def op(self):
                return "ok"

        svc = Svc()
        assert set(svc.route.nodes()["entries"].keys()) == {"custom"}
        assert svc.route.node("custom")() == "ok"

    def test_lazy_branch_leaves_read_from_single_marker(self):
        class Leaf(RoutingClass):
            @route()
            def ping(self):
                return "pong"

            @route(name="renamed")
            def info(self):
                return "info"

        class Root(RoutingClass):
            def __init__(self):
                self.add_branches({"name": "leaf", "cls": Leaf, "params": {}})

        root = Root()
        branch = root.route.nodes()["routers"]["leaf"]
        assert branch.get("lazy") is True
        assert set(branch["entries"].keys()) == {"ping", "renamed"}
