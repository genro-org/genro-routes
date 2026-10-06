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

"""Tests to cover remaining coverage gaps."""

import pytest
from pydantic import BaseModel

import genro_routes.plugins.logging  # noqa: F401
import genro_routes.plugins.pydantic  # noqa: F401
from genro_routes import Router, RoutingClass, route
from genro_routes.core.base_router import BaseRouter
from genro_routes.core.context import RoutingContext
from genro_routes.core.router import _PluginSpec
from genro_routes.core.router_node import RouterNode
from genro_routes.core.routing import ResultWrapper, is_result_wrapper
from genro_routes.exceptions import (
    NotAuthenticated,
    NotAuthorized,
    NotAvailable,
    NotFound,
)
from genro_routes.plugins._base_plugin import BasePlugin

# --- base_router.py - _describe_entry_extra returns extra ---


def test_nodes_with_plugin_returns_extra_info():
    """Test that nodes() includes plugin info when plugins are attached."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("pydantic")

        @route()
        def handler(self, text: str) -> str:
            return text

    svc = Svc()
    tree = svc.route.nodes()
    # Should have plugins info in entry
    entry_info = tree["entries"]["handler"]
    assert "plugins" in entry_info


# --- router.py - _PluginSpec.clone ---


def test_plugin_spec_clone():
    """Test _PluginSpec.clone() method."""

    class DummyPlugin(BasePlugin):
        plugin_code = "dummy_clone"
        plugin_description = "Dummy for clone test"

    spec = _PluginSpec(DummyPlugin, {"option": "value"})
    cloned = spec.clone()
    assert cloned.factory is spec.factory
    assert cloned.kwargs == spec.kwargs
    assert cloned.kwargs is not spec.kwargs  # Should be a copy


# --- router.py - empty plugin name error ---


def test_register_plugin_empty_name_raises():
    """Test that registering plugin with empty name raises ValueError."""

    class NoCodePlugin(BasePlugin):
        plugin_code = ""
        plugin_description = "No code"

    # Empty plugin_code is treated as missing plugin_code
    with pytest.raises(ValueError, match="missing plugin_code"):
        Router.register_plugin(NoCodePlugin)


# --- router.py - inherited plugin config lookup ---


def test_inherited_plugin_config_lookup():
    """Test that child router inherits parent plugin config."""

    class ChildSvc(RoutingClass):
        @route()
        def child_handler(self):
            return "child"

    class ParentSvc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")
            self.child = ChildSvc()  # Child must be an attribute of parent

        @route()
        def parent_handler(self):
            return "parent"

    parent = ParentSvc()

    # Set config on parent
    parent.route.logging.configure(before=False, after=True)

    # Attach child to parent
    parent.add_branches({"name": "child", "instance": parent.child})

    # Child should inherit the plugin and config
    assert "logging" in parent.child.route._plugins_by_name

    # The config lookup should work (callable resolution)
    child_logging = parent.child.route._plugins_by_name["logging"]
    cfg = child_logging.configuration("child_handler")
    # Should have inherited config (before=False, after=True from parent)
    assert cfg.get("after") is True
    assert cfg.get("before") is False


# --- _base_plugin.py - multi-target configure ---


def test_configure_multi_target():
    """Test configure() with comma-separated targets."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")

        @route()
        def handler_a(self):
            return "a"

        @route()
        def handler_b(self):
            return "b"

        @route()
        def handler_c(self):
            return "c"

    svc = Svc()
    # Configure multiple targets at once
    svc.route.logging.configure(_target="handler_a,handler_b", before=False)

    # Both should have before=False
    cfg_a = svc.route.logging.configuration("handler_a")
    cfg_b = svc.route.logging.configuration("handler_b")
    cfg_c = svc.route.logging.configuration("handler_c")

    assert cfg_a.get("before") is False
    assert cfg_b.get("before") is False
    # handler_c should not be affected (uses base)
    assert cfg_c.get("before") is not False or "before" not in cfg_c


# --- _base_plugin.py - base configure with flags ---


def test_base_plugin_configure_with_flags():
    """Test BasePlugin.configure() with flags parameter."""

    class FlagsPlugin(BasePlugin):
        plugin_code = "flags_test"
        plugin_description = "Test flags in base configure"

        # No custom configure - uses base implementation

    Router.register_plugin(FlagsPlugin)

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("flags_test")

        @route()
        def handler(self):
            return "ok"

    svc = Svc()
    # Call configure with flags - should use base implementation
    svc.route.flags_test.configure(flags="enabled,verbose:off")

    cfg = svc.route.flags_test.configuration("handler")
    assert cfg.get("enabled") is True
    assert cfg.get("verbose") is False


# --- pydantic.py - no parameter hints ---


def test_pydantic_handler_without_param_hints():
    """Test pydantic plugin with handler that has no parameter hints."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("pydantic")

        @route()
        def no_hints(self, x, y):  # No type hints on parameters
            return f"{x}:{y}"

    svc = Svc()
    # Should work without validation (passthrough)
    node = svc.route.node("no_hints")
    result = node("a", "b")
    assert result == "a:b"


def test_pydantic_handler_only_return_hint():
    """Test pydantic plugin with handler that has only return hint."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("pydantic")

        @route()
        def only_return(self, x, y) -> str:  # Only return type hint
            return f"{x}:{y}"

    svc = Svc()
    # Should work without validation (no param hints after removing return)
    node = svc.route.node("only_return")
    result = node("a", "b")
    assert result == "a:b"


# --- pydantic.py - param not in signature raises error ---


def test_pydantic_hint_not_in_signature_raises():
    """Test pydantic raises error when type hint doesn't match signature.

    When a handler has a type hint for a parameter that doesn't exist
    in the function signature, pydantic plugin should raise ValueError.
    """

    # Define function with annotation for non-existent param
    def handler(self, x: str) -> str:
        return x

    # Add annotation for parameter not in signature
    handler.__annotations__["phantom"] = int

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("pydantic")

    # Assign decorated handler - this triggers on_decore
    Svc.handler = route()(handler)

    # Error is raised at first use (lazy binding)
    svc = Svc()
    with pytest.raises(ValueError, match="type hint for 'phantom'.*not in the function signature"):
        svc.route.node("handler")


# --- pydantic.py - get_model disabled/no model ---


def test_pydantic_get_model_disabled():
    """Test get_model returns None when disabled."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("pydantic")

        @route()
        def handler(self, text: str) -> str:
            return text

    svc = Svc()
    svc.route.nodes()  # Trigger lazy binding
    entry = svc.route._entries["handler"]

    # Initially should return model
    result = svc.route.pydantic.get_model(entry)
    assert result is not None
    assert result[0] == "pydantic_model"

    # After disabling, should return None
    svc.route.pydantic.configure(disabled=True)
    result = svc.route.pydantic.get_model(entry)
    assert result is None


def test_pydantic_get_model_no_model():
    """Test get_model returns None when no model was created."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("pydantic")

        @route()
        def no_hints(self, x, y):  # No type hints
            return f"{x}:{y}"

    svc = Svc()
    svc.route.nodes()  # Trigger lazy binding
    entry = svc.route._entries["no_hints"]

    # No model was created
    result = svc.route.pydantic.get_model(entry)
    assert result is None


# --- pydantic.py - entry_metadata with signature info ---


def test_pydantic_entry_metadata_no_hints():
    """Test entry_metadata returns signature info even without type hints."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("pydantic")

        @route()
        def no_hints(self, x, y):  # No type hints but signature is captured
            return f"{x}:{y}"

    svc = Svc()
    svc.route.nodes()  # Trigger lazy binding
    entry = svc.route._entries["no_hints"]

    result = svc.route.pydantic.entry_metadata(svc.route, entry)
    # Now always returns signature info (accepts_varargs, hints)
    assert result["accepts_varargs"] is False
    assert result["hints"] == {}
    assert result["model"] is None


def test_pydantic_entry_metadata_with_meta():
    """Test entry_metadata returns model info when pydantic metadata exists."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("pydantic")

        @route()
        def with_hints(self, text: str, num: int) -> str:
            return f"{text}:{num}"

    svc = Svc()
    svc.route.nodes()  # Trigger lazy binding
    entry = svc.route._entries["with_hints"]

    result = svc.route.pydantic.entry_metadata(svc.route, entry)
    assert "model" in result
    assert "hints" in result
    assert "accepts_varargs" in result
    assert result["hints"] == {"text": str, "num": int}
    assert result["accepts_varargs"] is False


# --- Plugin inheritance: clone + config copy ---


def test_inherited_plugin_is_separate_instance():
    """Test that inherited plugin is a new instance, not shared with parent."""

    class ChildSvc(RoutingClass):
        @route()
        def child_handler(self):
            return "child"

    class ParentSvc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")
            self.child = ChildSvc()

        @route()
        def parent_handler(self):
            return "parent"

    parent = ParentSvc()
    parent.add_branches({"name": "child", "instance": parent.child})

    # Plugin instances should be DIFFERENT objects
    parent_plugin = parent.route._plugins_by_name["logging"]
    child_plugin = parent.child.route._plugins_by_name["logging"]

    assert parent_plugin is not child_plugin
    assert parent_plugin._router is parent.route
    assert child_plugin._router is parent.child.route


def test_inherited_plugin_copies_parent_config():
    """Test that child inherits a copy of parent's config at attach time."""

    class ChildSvc(RoutingClass):
        @route()
        def child_handler(self):
            return "child"

    class ParentSvc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")
            self.child = ChildSvc()

        @route()
        def parent_handler(self):
            return "parent"

    parent = ParentSvc()

    # Configure parent BEFORE attach
    parent.route.logging.configure(before=False, after=True)

    # Attach child - should copy config
    parent.add_branches({"name": "child", "instance": parent.child})

    # Child should have same config values
    child_cfg = parent.child.route.logging.configuration()
    assert child_cfg.get("before") is False
    assert child_cfg.get("after") is True


def test_child_config_independent_from_parent():
    """Test that after attach, child's config is independent from parent."""

    class ChildSvc(RoutingClass):
        @route()
        def child_handler(self):
            return "child"

    class ParentSvc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")
            self.child = ChildSvc()

        @route()
        def parent_handler(self):
            return "parent"

    parent = ParentSvc()
    parent.route.logging.configure(before=True, after=False)
    parent.add_branches({"name": "child", "instance": parent.child})

    # Child modifies its own config
    parent.child.route.logging.configure(before=False, after=True)

    # Configs should be different
    parent_cfg = parent.route.logging.configuration()
    child_cfg = parent.child.route.logging.configuration()

    assert parent_cfg.get("before") is True
    assert parent_cfg.get("after") is False
    assert child_cfg.get("before") is False
    assert child_cfg.get("after") is True


# --- on_parent_config_changed notification ---


def test_parent_config_change_notifies_children():
    """Test that changing parent config calls on_parent_config_changed on children."""
    notifications = []

    class TrackingPlugin(BasePlugin):
        plugin_code = "tracking"
        plugin_description = "Tracks parent config changes"

        def configure(self, value: int = 0):
            pass

        def on_parent_config_changed(self, old_config, new_config):
            notifications.append({"router": self._router, "config": new_config})

    Router.register_plugin(TrackingPlugin)

    class ChildSvc(RoutingClass):
        @route()
        def child_handler(self):
            return "child"

    class ParentSvc(RoutingClass):
        def __init__(self):
            self.route.plug("tracking")
            self.child = ChildSvc()

        @route()
        def parent_handler(self):
            return "parent"

    parent = ParentSvc()
    parent.add_branches({"name": "child", "instance": parent.child})

    # Clear any notifications from __init__
    notifications.clear()

    # Change parent config
    parent.route.tracking.configure(value=42)

    # Child should have been notified
    assert len(notifications) == 1
    assert notifications[0]["router"] is parent.child.route
    assert notifications[0]["config"]["value"] == 42


def test_cascading_notifications():
    """Test that if child applies config, its children are also notified."""
    notifications = []

    class CascadePlugin(BasePlugin):
        plugin_code = "cascade"
        plugin_description = "Cascades config to children"

        def configure(self, level: int = 0, enabled: bool = True):
            pass

        def on_parent_config_changed(self, old_config, new_config):
            notifications.append({"router": self._router, "config": new_config})
            # Apply the config - this should cascade to our children
            self.configure(**new_config)

    Router.register_plugin(CascadePlugin)

    class GrandchildSvc(RoutingClass):
        @route()
        def grandchild_handler(self):
            return "grandchild"

    class ChildSvc(RoutingClass):
        def __init__(self):
            self.grandchild = GrandchildSvc()

        @route()
        def child_handler(self):
            return "child"

    class ParentSvc(RoutingClass):
        def __init__(self):
            self.route.plug("cascade")
            self.child = ChildSvc()

        @route()
        def parent_handler(self):
            return "parent"

    parent = ParentSvc()
    parent.add_branches({"name": "child", "instance": parent.child})
    parent.child.add_branches({"name": "grandchild", "instance": parent.child.grandchild})

    # Clear notifications
    notifications.clear()

    # Change parent config
    parent.route.cascade.configure(level=99)

    # Both child and grandchild should have been notified
    assert len(notifications) == 2
    routers_notified = [n["router"] for n in notifications]
    assert parent.child.route in routers_notified
    assert parent.child.grandchild.route in routers_notified


def test_child_ignores_parent_config_no_cascade():
    """Test that if child ignores parent config, grandchildren are NOT notified."""
    notifications = []

    class IgnorePlugin(BasePlugin):
        plugin_code = "ignore"
        plugin_description = "Ignores parent config changes"

        def configure(self, value: int = 0):
            pass

        def on_parent_config_changed(self, old_config, new_config):
            notifications.append({"router": self._router, "config": new_config})
            # Do NOT call configure - stop the cascade

    Router.register_plugin(IgnorePlugin)

    class GrandchildSvc(RoutingClass):
        @route()
        def grandchild_handler(self):
            return "grandchild"

    class ChildSvc(RoutingClass):
        def __init__(self):
            self.grandchild = GrandchildSvc()

        @route()
        def child_handler(self):
            return "child"

    class ParentSvc(RoutingClass):
        def __init__(self):
            self.route.plug("ignore")
            self.child = ChildSvc()

        @route()
        def parent_handler(self):
            return "parent"

    parent = ParentSvc()
    parent.add_branches({"name": "child", "instance": parent.child})
    parent.child.add_branches({"name": "grandchild", "instance": parent.child.grandchild})

    # Clear notifications
    notifications.clear()

    # Change parent config
    parent.route.ignore.configure(value=77)

    # Only child should be notified (grandchild NOT because child ignores)
    assert len(notifications) == 1
    assert notifications[0]["router"] is parent.child.route


# --- exceptions.py: test selector format ---


def test_not_found_selector():
    """Test NotFound exception with selector."""

    exc = NotFound("my_router:my_path")
    assert exc.selector == "my_router:my_path"
    assert "my_router:my_path" in str(exc)


def test_not_authorized_selector():
    """Test NotAuthorized exception with selector."""

    exc = NotAuthorized("my_router:my_path")
    assert exc.selector == "my_router:my_path"
    assert "my_router:my_path" in str(exc)


def test_not_authenticated_selector():
    """Test NotAuthenticated exception with selector."""

    exc = NotAuthenticated("my_router:my_path")
    assert exc.selector == "my_router:my_path"
    assert "my_router:my_path" in str(exc)


def test_not_available_selector():
    """Test NotAvailable exception with selector."""

    exc = NotAvailable("my_router:my_path")
    assert exc.selector == "my_router:my_path"
    assert "my_router:my_path" in str(exc)


# --- auth.py: deny_reason with RouterInterface ---


def test_auth_deny_reason_with_router_interface():
    """Test auth plugin deny_reason when passed a RouterInterface (child router)."""

    class ChildSvc(RoutingClass):
        @route(auth_rule="admin")
        def admin_only(self):
            return "admin"

        @route()
        def public(self):
            return "public"

    class ParentSvc(RoutingClass):
        def __init__(self):
            self.route.plug("auth")
            self.child = ChildSvc()

        @route()
        def parent_handler(self):
            return "parent"

    parent = ParentSvc()
    parent.add_branches({"name": "child", "instance": parent.child})

    # Get child router via parent
    child_router = parent.route._children["child"]

    # Test deny_reason with RouterInterface
    auth_plugin = parent.route._plugins_by_name["auth"]

    # Without tags - should return "" because public handler exists
    result = auth_plugin.deny_reason(child_router)
    assert result == ""  # At least one entry is allowed (public)

    # With admin tags - should return ""
    result = auth_plugin.deny_reason(child_router, tags="admin")
    assert result == ""


def test_auth_deny_reason_router_empty():
    """Test auth deny_reason with empty router returns empty string."""

    class ChildSvc(RoutingClass):
        pass  # No routes defined

    class ParentSvc(RoutingClass):
        def __init__(self):
            self.route.plug("auth")
            self.child = ChildSvc()

        @route()
        def parent_handler(self):
            return "parent"

    parent = ParentSvc()
    parent.add_branches({"name": "child", "instance": parent.child})

    child_router = parent.route._children["child"]
    auth_plugin = parent.route._plugins_by_name["auth"]

    # Empty router returns "" (no entries to check)
    result = auth_plugin.deny_reason(child_router)
    assert result == ""


# --- base_router.py: additional coverage ---


def test_get_default_handler_constructor_param():
    """Test get_default_handler constructor parameter."""

    class Svc(RoutingClass):
        @route()
        def index(self):
            return "index"

        @route()
        def other(self):
            return "other"

    # Construct the router explicitly BEFORE accessing .route:
    # the constructed router becomes the instance's router.
    svc = Svc()
    router = Router(svc, get_default_handler="index")
    assert svc.route is router
    # Default handler should be index
    assert svc.route.default_entry == "index"


def test_add_entry_with_meta_kwargs():
    """Test add_entry with meta_* kwargs."""

    class Svc(RoutingClass):
        @route(meta_author="John", meta_version="1.0")
        def handler(self):
            return "ok"

    svc = Svc()
    node = svc.route.node("handler")
    # meta_ kwargs should be grouped under "meta" key
    assert node.metadata.get("author") == "John"
    assert node.metadata.get("version") == "1.0"


def test_nodes_with_pattern_filter():
    """Test nodes() with pattern filter."""

    class Svc(RoutingClass):
        @route()
        def get_users(self):
            return []

        @route()
        def get_orders(self):
            return []

        @route()
        def create_user(self):
            return {}

    svc = Svc()
    # Filter by pattern
    nodes = svc.route.nodes(pattern="^get_")
    entries = nodes.get("entries", {})
    assert "get_users" in entries
    assert "get_orders" in entries
    assert "create_user" not in entries


def test_base_router_entry_invalid_reason():
    """Test BaseRouter._entry_invalid_reason directly.

    BaseRouter provides a default implementation that returns "not_found"
    for None entries. Router overrides this, so we test BaseRouter directly.
    """

    class Svc(RoutingClass):
        @route()
        def handler(self):
            return "ok"

    svc = Svc()
    # Use BaseRouter directly, not Router (owner has no router yet)
    api = BaseRouter(svc)
    # BaseRouter._entry_invalid_reason with None returns "not_found"
    assert api._entry_invalid_reason(None) == "not_found"
    # BaseRouter._entry_invalid_reason with valid entry returns ""
    entry = api._entries.get("handler")
    assert api._entry_invalid_reason(entry) == ""


# --- base_router.py - meta_* kwargs shorthand ---


def test_add_entry_meta_kwargs_shorthand():
    """Test that meta_* kwargs are grouped under 'meta' key.

    This is a convenience syntax that allows:
        router.add_entry(handler, meta_foo="bar", meta_baz=123)

    Instead of:
        router.add_entry(handler, meta={"foo": "bar", "baz": 123})
    """

    class Svc(RoutingClass):
        @route()
        def handler(self):
            return "ok"

    svc = Svc()

    # Add entry using meta_* shorthand syntax
    def extra_handler():
        return "extra"

    svc.route.add_entry(extra_handler, name="extra", meta_author="john", meta_version=2)

    # Verify the metadata was correctly grouped
    entry = svc.route._entries["extra"]
    assert entry.metadata.get("meta", {}).get("author") == "john"
    assert entry.metadata.get("meta", {}).get("version") == 2


# --- base_router.py - non-plugin options as metadata ---


def test_add_entry_custom_options_as_metadata():
    """Test that non-plugin options are merged into entry metadata.

    Options that are not plugin-scoped (no underscore or unknown plugin)
    and not meta_* prefixed are merged directly into entry.metadata.
    """

    class Svc(RoutingClass):
        pass

    svc = Svc()

    def handler():
        return "ok"

    # Pass custom options without underscore (so they don't go through plugin check)
    svc.route.add_entry(handler, name="test", deprecated=True, priority=10)

    entry = svc.route._entries["test"]
    assert entry.metadata.get("deprecated") is True
    assert entry.metadata.get("priority") == 10


def test_add_entry_unknown_plugin_option_as_metadata():
    """Test that underscore options with unknown plugin prefix go to metadata.

    If an option like 'foo_bar=123' is passed and 'foo' is not a known plugin,
    the entire key 'foo_bar' is stored in metadata (not treated as plugin config).
    """

    class Svc(RoutingClass):
        pass

    svc = Svc()

    def handler():
        return "ok"

    # 'unknown' is not a registered plugin, so 'unknown_option' goes to metadata
    svc.route.add_entry(handler, name="test", unknown_option="value")

    entry = svc.route._entries["test"]
    assert entry.metadata.get("unknown_option") == "value"


# --- router_node.py - custom exceptions and properties when entry is None ---


def test_router_node_custom_exceptions_in_init():
    """Test RouterNode with custom exceptions passed to constructor."""

    class CustomNotFound(Exception):
        pass

    class Svc(RoutingClass):
        @route()
        def handler(self):
            return "ok"

    svc = Svc()

    # Create node with custom exceptions
    node = RouterNode(
        svc.route,
        errors={"not_found": CustomNotFound},
        entry_name="nonexistent",
    )

    # Should raise custom exception
    with pytest.raises(CustomNotFound):
        node()


def test_router_node_doc_and_metadata_when_entry_none():
    """Test doc and metadata properties return empty when entry is None."""

    class Svc(RoutingClass):
        @route()
        def handler(self):
            return "ok"

    svc = Svc()

    # Create node for nonexistent entry
    node = RouterNode(svc.route, entry_name="nonexistent")

    # Properties should return empty values when entry is None
    assert node.doc == ""
    assert node.metadata == {}


def test_router_node_custom_validation_error_exception():
    """Test RouterNode remaps ValidationError to custom exception."""

    class CustomValidationError(Exception):
        pass

    class Svc(RoutingClass):
        @route()
        def handler(self):
            # Raise a pydantic ValidationError

            class Model(BaseModel):
                value: int

            Model(value="not_an_int")  # This raises ValidationError

    svc = Svc()
    node = svc.route.node("handler")
    node.set_custom_exceptions({"validation_error": CustomValidationError})

    with pytest.raises(CustomValidationError):
        node()


# --- auth.py - deny_reason with RouterInterface where child is accessible ---


def test_auth_deny_reason_router_with_accessible_child():
    """Test auth deny_reason returns empty when at least one child is accessible."""

    class Child(RoutingClass):
        @route(auth_rule="public")
        def public_handler(self):
            return "public"

        @route(auth_rule="admin")
        def admin_handler(self):
            return "admin"

    class Parent(RoutingClass):
        def __init__(self):
            self.route.plug("auth")
            self.child = Child()
            self.add_branches({"name": "child", "instance": self.child})

    parent = Parent()
    auth_plugin = parent.route._plugins_by_name["auth"]

    # Get child router
    child_router = parent.child.route

    # deny_reason on router should return "" if any child is accessible
    result = auth_plugin.deny_reason(child_router, auth_tags="public")
    assert result == ""


# --- routing.py coverage ---


def test_result_wrapper():
    """Test ResultWrapper class."""

    wrapper = ResultWrapper("test_value", {"mime": "text/plain"})
    assert wrapper.value == "test_value"
    assert wrapper.metadata == {"mime": "text/plain"}
    assert is_result_wrapper(wrapper) is True
    assert is_result_wrapper("not a wrapper") is False


def test_routing_class_result_wrapper_method():
    """Test RoutingClass.result_wrapper() method."""

    class Svc(RoutingClass):
        pass

    svc = Svc()
    result = svc.result_wrapper("content", media_type="application/json")

    assert isinstance(result, ResultWrapper)
    assert result.value == "content"
    assert result.metadata == {"media_type": "application/json"}


def test_routing_class_ctx_property():
    """Test RoutingClass.ctx getter and setter via slot + parent chain."""

    class Svc(RoutingClass):
        pass

    svc = Svc()

    # Initially None (slot not set)
    assert svc.ctx is None

    # Set ctx
    ctx = RoutingContext()
    ctx.user = "test_user"
    svc.ctx = ctx
    assert svc.ctx is ctx
    assert svc.ctx.user == "test_user"

    # Clear ctx
    svc.ctx = None
    assert svc.ctx is None


def test_routing_class_ctx_parent_chain():
    """Child walks up _routing_parent chain for ctx."""

    class Parent(RoutingClass):
        pass

    class Child(RoutingClass):
        pass

    parent = Parent()
    child = Child()
    parent.add_branches({"name": "child", "instance": child})

    ctx = RoutingContext()
    ctx.db = "shared_db"
    parent.ctx = ctx

    # Child walks up to parent's ctx
    assert child.ctx is ctx
    assert child.ctx.db == "shared_db"


def test_plugin_on_parent_config_changed_propagates():
    """Test that parent config changes propagate to aligned children."""

    class Child(RoutingClass):
        @route()
        def child_handler(self):
            return "child"

    class Parent(RoutingClass):
        def __init__(self):
            self.route.plug("logging")
            self.child = Child()
            self.add_branches({"name": "child", "instance": self.child})

        @route()
        def parent_handler(self):
            return "parent"

    parent = Parent()

    # Get child's logging plugin
    child_plugin = parent.child.route._plugins_by_name.get("logging")
    assert child_plugin is not None

    # Initially child should have same config as parent (aligned)
    parent_plugin = parent.route._plugins_by_name.get("logging")
    assert child_plugin.configuration() == parent_plugin.configuration()

    # Change parent config
    parent_plugin.configure(before=True)

    # Child should have been updated (was aligned with parent)
    assert child_plugin.configuration().get("before") is True


# =============================================================================
# Additional tests to reach 100% coverage in core/
# =============================================================================


# --- base_router.py - empty chunk in comma-separated add_entry ---


def test_add_entry_comma_separated_with_empty_chunks():
    """Test add_entry with comma-separated targets including empty chunks."""

    class Svc(RoutingClass):
        def foo(self):
            return "foo"

        def bar(self):
            return "bar"

    svc = Svc()
    # Include empty chunks via extra commas
    svc.route.add_entry("foo, , bar, ")

    assert "foo" in svc.route._entries
    assert "bar" in svc.route._entries


# --- base_router.py - plugin_options not None in _register_marked ---


def test_register_marked_with_plugin_options():
    """Test that plugin_options passed to add_entry propagate through _register_marked."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")

        @route(logging_before=False)
        def handler(self):
            return "ok"

    svc = Svc()
    # Plugin options from @route should be in entry metadata
    entry = svc.route._entries["handler"]
    assert "plugin_config" in entry.metadata
    assert entry.metadata["plugin_config"].get("logging", {}).get("before") is False


# --- base_router.py - _require_bound when already bound ---


def test_require_bound_when_already_bound():
    """Test _require_bound does nothing when already bound."""

    class Svc(RoutingClass):
        @route()
        def handler(self):
            return "ok"

    svc = Svc()
    # Force binding
    _ = svc.route._entries

    # Call _require_bound again - should be no-op
    svc.route._require_bound("test_op")
    assert svc.route._bound is True


# --- instance branch reachable under the given alias ---


def test_instance_branch_reachable_under_alias():
    """The instance form links the child router under the given alias."""

    class Child(RoutingClass):
        @route()
        def api_handler(self):
            return "api"

    class Parent(RoutingClass):
        def __init__(self):
            self.child = Child()

    parent = Parent()
    parent.add_branches({"name": "child_api", "instance": parent.child})

    # Reachable under the given alias
    assert parent.route.node("child_api/api_handler")() == "api"
    assert "child_api" in parent.route.nodes().get("routers", {})


# --- instance branch when _routing_parent already set correctly ---


def test_instance_branch_routing_parent_already_correct():
    """Re-linking an instance already bound to the correct parent is a no-op."""

    class Child(RoutingClass):
        @route()
        def handler(self):
            return "ok"

    class Parent(RoutingClass):
        def __init__(self):
            self.child = Child()

    parent = Parent()
    # Instance already bound to the correct parent
    object.__setattr__(parent.child, "_routing_parent", parent)

    # Adding it as an instance branch must not fail nor rebind
    parent.add_branches({"name": "child", "instance": parent.child})
    assert parent.child._routing_parent is parent
    assert parent.route.node("child/handler")() == "ok"


# --- base_router.py - detach_instance with plugin_children cleanup ---


def test_detach_instance_cleans_plugin_children():
    """Test detach_instance cleans up _plugin_children references."""

    class Child(RoutingClass):
        @route()
        def handler(self):
            return "ok"

    class Parent(RoutingClass):
        def __init__(self):
            self.route.plug("logging")
            self.child = Child()
            self.add_branches({"name": "child", "instance": self.child})

        @route()
        def parent_handler(self):
            return "parent"

    parent = Parent()

    # Verify plugin_children has the child
    assert "logging" in parent.route._plugin_children
    assert any(r.instance is parent.child for r in parent.route._plugin_children["logging"])

    # Detach
    parent.route.detach_instance(parent.child)

    # Plugin children should be cleaned up
    assert not any(r.instance is parent.child for r in parent.route._plugin_children["logging"])


# --- router.py - _get_plugin_bucket initializing _all_ ---


def test_get_plugin_bucket_initializes_all():
    """Test _get_plugin_bucket creates _all_ bucket when missing."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")

        @route()
        def handler(self):
            return "ok"

    svc = Svc()

    # Manually remove _all_ to test re-initialization
    bucket = svc.route._plugin_info["logging"]
    del bucket["_all_"]

    # Now call _get_plugin_bucket
    result = svc.route._get_plugin_bucket("logging")

    # Should have recreated _all_
    assert "_all_" in result


# --- router.py - is_plugin_enabled via config (not locals) ---


def test_is_plugin_enabled_via_config_only():
    """Test is_plugin_enabled returns value from config when no locals set."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")

        @route()
        def handler(self):
            return "ok"

    svc = Svc()

    # Set enabled=False via config (not locals)
    svc.route.logging.configure(enabled=False)

    # Should read from config
    assert svc.route.is_plugin_enabled("handler", "logging") is False


def test_is_plugin_enabled_global_config():
    """Test is_plugin_enabled reads _all_ config when entry has no override."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")

        @route()
        def handler(self):
            return "ok"

    svc = Svc()

    # Set enabled=False globally via config
    svc.route.logging.configure(enabled=False)

    # Should read from _all_ config
    assert svc.route.is_plugin_enabled("handler", "logging") is False


def test_is_plugin_enabled_global_locals():
    """Test is_plugin_enabled reads _all_ locals when set."""

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")

        @route()
        def handler(self):
            return "ok"

    svc = Svc()

    # Set enabled globally via set_plugin_enabled (_all_)
    svc.route.set_plugin_enabled("_all_", "logging", False)

    # Should read from _all_ locals
    assert svc.route.is_plugin_enabled("handler", "logging") is False


# --- router.py - _apply_plugin_to_entries skip already-present plugin ---


def test_apply_plugin_to_entries_skips_duplicate():
    """Test _apply_plugin_to_entries doesn't double-add plugin name."""

    class Svc(RoutingClass):
        @route()
        def handler(self):
            return "ok"

    svc = Svc()
    # Force binding
    _ = svc.route._entries

    # Now plug - should apply to existing entries
    svc.route.plug("logging")

    entry = svc.route._entries["handler"]
    # Plugin name should appear only once
    assert entry.plugins.count("logging") == 1


# --- router.py - _on_attached_to_parent child already has plugin ---


def test_on_attached_to_parent_child_has_same_plugin():
    """Test plugin inheritance when child already has the same plugin."""

    class Child(RoutingClass):
        def __init__(self):
            # Child creates its own logging plugin
            self.route.plug("logging")

        @route()
        def handler(self):
            return "ok"

    class Parent(RoutingClass):
        def __init__(self):
            self.route.plug("logging")
            self.child = Child()

    parent = Parent()
    parent.add_branches({"name": "child", "instance": parent.child})

    # Child should still have exactly one logging plugin
    assert len([p for p in parent.child.route._plugins if p.name == "logging"]) == 1


# --- router.py - _describe_entry_extra with config/metadata ---


def test_describe_entry_extra_with_plugin_data():
    """Test _describe_entry_extra includes plugin that has config."""

    class TestPlugin(BasePlugin):
        """Plugin with default config but no metadata."""
        plugin_code = "testplugin2"
        plugin_description = "Test plugin"

        def configure(self, custom_option: str = "default"):
            pass

        def entry_metadata(self, router, entry):
            return {}  # Empty dict - no metadata

    Router.register_plugin(TestPlugin)

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("testplugin2")

        @route()
        def handler(self):
            return "ok"

    svc = Svc()
    tree = svc.route.nodes()

    entry_info = tree["entries"]["handler"]
    # Plugin with config should appear (has enabled: True by default)
    assert "plugins" in entry_info
    assert "testplugin2" in entry_info["plugins"]
    # Should have config but not metadata
    plugin_info = entry_info["plugins"]["testplugin2"]
    assert "config" in plugin_info
    # metadata is empty so should not be included
    assert "metadata" not in plugin_info or plugin_info.get("metadata") == {}


# --- router_node.py - ValidationError is None ---


def test_router_node_default_exceptions_without_pydantic():
    """Test DEFAULT_EXCEPTIONS when ValidationError import fails."""

    # Just verify the class loads correctly and has default exceptions
    assert "not_found" in RouterNode.DEFAULT_EXCEPTIONS
    assert "not_authorized" in RouterNode.DEFAULT_EXCEPTIONS


# --- router_node.py - __call__ with non-pydantic ValidationError ---


def test_router_node_call_non_pydantic_exception_reraises():
    """Test RouterNode.__call__ re-raises non-ValidationError exceptions."""

    class Svc(RoutingClass):
        @route()
        def handler(self):
            raise RuntimeError("test error")

    svc = Svc()
    node = svc.route.node("handler")

    with pytest.raises(RuntimeError, match="test error"):
        node()


# --- base_router.py - plugin_options passed to _register_marked ---


def test_add_entry_star_with_plugin_options():
    """Test add_entry('*') with plugin_* kwargs merged via _register_marked.

    The plugin_options dict gets populated from plugin-prefixed kwargs
    passed to add_entry (e.g., logging_before=False) and merged in
    _register_marked.
    """

    # Create a fresh class for this test
    class SvcForPluginOpts(RoutingClass):
        def my_handler(self):
            return "ok"

    # Mark the method BEFORE instantiation
    SvcForPluginOpts.my_handler._route_decorator_kw = {}

    svc = SvcForPluginOpts()
    api = BaseRouter(svc)

    # Trigger initial binding (registers handler without plugin_options)
    _ = api._entries
    assert "my_handler" in api._BaseRouter__entries_raw

    # Now call add_entry("*") with plugin kwargs AND replace=True
    # logging_before=False gets split into plugin_options={"logging": {"before": False}}
    # by add_entry, then merged in _register_marked
    api.add_entry("*", logging_before=False, replace=True)

    entry = api._BaseRouter__entries_raw["my_handler"]
    # Plugin options should be in entry metadata under plugin_config
    config = entry.metadata.get("plugin_config", {})
    assert config.get("logging", {}).get("before") is False


# --- base_router.py - _bind when already bound ---


def test_bind_when_already_bound():
    """Test _bind() returns early when already bound.

    Covers the early-return statement when self._bound is True.
    """

    class Svc(RoutingClass):
        @route()
        def handler(self):
            return "ok"

    svc = Svc()
    api = BaseRouter(svc)

    # Force binding via _entries access
    _ = api._entries
    assert api._bound is True

    # Call _bind directly - should return early (no error, no-op)
    api._bind()
    assert api._bound is True


# --- router.py - is_plugin_enabled returns True when no enabled config ---


def test_is_plugin_enabled_returns_true_default():
    """Test is_plugin_enabled returns True when no enabled config is set.

    Covers the return True fallback.
    """

    class Svc(RoutingClass):
        def __init__(self):
            self.route.plug("logging")

        @route()
        def handler(self):
            return "ok"

    svc = Svc()

    # Don't set any enabled config - should return True by default
    # Note: we need to clear any implicit config
    bucket = svc.route._plugin_info.get("logging", {})
    handler_data = bucket.get("handler", {})
    # Remove enabled from both locals and config if present
    handler_data.pop("locals", None)
    handler_data.pop("config", None)

    # Also clear _all_ enabled settings
    all_data = bucket.get("_all_", {})
    all_data.pop("locals", None)
    # Keep only non-enabled config entries
    if "config" in all_data:
        all_data["config"].pop("enabled", None)

    # Now is_plugin_enabled should return True (default)
    assert svc.route.is_plugin_enabled("handler", "logging") is True
