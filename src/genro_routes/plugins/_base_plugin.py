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

"""Plugin contract definitions for Genro Routes.

This module defines the base classes and data structures used by the Router
plugin system.

Objects
-------
``MethodEntry``
    Dataclass capturing handler metadata at registration time. Fields:
        - ``name``: logical handler name (after prefix stripping)
        - ``func``: bound callable invoked by the Router
        - ``router``: Router instance that owns the handler
        - ``plugins``: list of plugin names applied to the handler
        - ``metadata``: mutable dict used by plugins to store annotations
        - ``signature``: ``inspect.Signature`` of ``func``, cached on first access

``BasePlugin``
    Abstract base class that every plugin must subclass. Provides:
        - Configuration helpers that delegate to the router's ``plugin_info`` store
        - Optional hooks ``on_decore`` and ``wrap_handler`` for the Router pipeline

    Required class attributes:
        - ``plugin_code``: unique identifier used for registration (e.g. "logging")
        - ``plugin_description``: human-readable description of the plugin

    Constructor signature: ``BasePlugin(router, **config)``

    Key methods:
        - ``configure(**config)``: Define accepted configuration parameters
        - ``configuration(method_name=None)``: Read merged configuration
        - ``on_decore(router, func, entry)``: Called when handler is registered
        - ``wrap_handler(router, entry, call_next)``: Build middleware chain
        - ``deny_reason(entry, **filters)``: Control handler visibility
        - ``entry_metadata(router, entry)``: Provide plugin-specific metadata

Example::

    from genro_routes.plugins import BasePlugin, MethodEntry

    class MyPlugin(BasePlugin):
        plugin_code = "myplugin"
        plugin_description = "My custom plugin"

        def configure(self, enabled: bool = True, threshold: int = 10):
            pass  # Storage handled by wrapper

        def wrap_handler(self, router, entry, call_next):
            def wrapper(*args, **kwargs):
                print(f"Before {entry.name}")
                result = call_next(*args, **kwargs)
                print(f"After {entry.name}")
                return result
            return wrapper
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

from pydantic import validate_call

__all__ = ["BasePlugin", "MethodEntry"]


@dataclass
class MethodEntry:
    """Metadata for a registered route handler.

    Attributes:
        name: Logical handler name (after prefix stripping).
        func: Bound callable (original method).
        handler: Wrapped callable after plugin processing.
        router: Router instance that owns this handler.
        plugins: List of plugin names applied to this handler.
        metadata: Mutable dict for plugins to store annotations.
        endpoint_id: Optional globally unique identifier for reverse lookup.
        signature: Signature of ``func``, computed on first access and reused
            by every caller that needs to bind arguments.
    """

    name: str
    func: Callable
    router: Any
    plugins: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    handler: Callable = field(default=None)  # type: ignore[assignment]
    endpoint_id: str | None = field(default=None)
    _signature: inspect.Signature | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Set handler to func if not provided."""
        if self.handler is None:
            self.handler = self.func

    @property
    def signature(self) -> inspect.Signature:
        """Signature of func, computed on first access and cached."""
        if self._signature is None:
            self._signature = inspect.signature(self.func)
        return self._signature


def _wrap_configure(original_configure: Callable) -> Callable:
    """Wrap a plugin's configure() method to handle flags, _target, validation, and storage."""
    validated = validate_call(original_configure)

    @wraps(original_configure)
    def wrapper(
        self: BasePlugin, *, _target: str = "_all_", flags: str | None = None, **kwargs: Any
    ) -> None:
        # Parse flags into boolean kwargs
        if flags:
            kwargs.update(self._parse_flags(flags))

        # Handle multiple targets (comma-separated)
        if "," in _target:
            targets = [t.strip() for t in _target.split(",") if t.strip()]
            for t in targets:
                wrapper(self, _target=t, **kwargs)
            return

        # Validate kwargs against original configure signature
        validated(self, **kwargs)

        # Write to store
        self._write_config(_target, kwargs)

    return wrapper


class BasePlugin:
    """Hook interface and configuration helpers for router plugins.

    Subclass this to create custom plugins. Override the hooks you need
    and define your configuration schema in ``configure()``.
    """

    __slots__ = ("name", "_router")

    # Subclasses MUST define these class attributes
    plugin_code: str = ""
    plugin_description: str = ""
    plugin_default_param: str | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Wrap configure() if the subclass defines its own
        if "configure" in cls.__dict__:
            cls.configure = _wrap_configure(cls.__dict__["configure"])  # type: ignore[method-assign]

    def __init__(
        self,
        router: Any,
        **config: Any,
    ):
        self.name = self.plugin_code
        self._router = router
        self._init_store()
        # Call configure with initial config
        self.configure(**config)

    def _init_store(self) -> None:
        """Initialize plugin bucket in router's store."""
        store = self._get_store()
        store.setdefault(self.name, {}).setdefault(
            "_all_", {"config": {"enabled": True}, "locals": {}}
        )

    def _write_config(self, target: str, config: dict[str, Any]) -> None:
        """Write config to the appropriate bucket in the store."""
        if not config:
            return
        store = self._get_store()
        plugin_bucket = store.setdefault(self.name, {})
        bucket = plugin_bucket.setdefault(target, {"config": {}, "locals": {}})
        # Capture old config before update (only for _all_ target)
        old_config = dict(bucket["config"]) if target == "_all_" else None
        bucket["config"].update(config)
        # Notify children about config change (only for _all_ target)
        if target == "_all_" and old_config is not None:
            new_config = dict(bucket["config"])
            self._notify_children(old_config, new_config)

    def configuration(self, method_name: str | None = None) -> dict[str, Any]:
        """Read merged configuration (base + optional per-handler override).

        Args:
            method_name: If provided, merge per-handler config with base config.

        Returns:
            Dict of configuration values.
        """
        store = self._get_store()
        plugin_bucket = store.get(self.name)
        if not plugin_bucket:
            return {}
        base_bucket = plugin_bucket.get("_all_", {})
        merged = dict(base_bucket.get("config", {}))
        if method_name:
            entry_bucket = plugin_bucket.get(method_name, {})
            merged.update(entry_bucket.get("config", {}))
        return merged

    def _parse_flags(self, flags: str) -> dict[str, bool]:
        """Parse flag string like "enabled,before:off" into boolean dict."""
        mapping: dict[str, bool] = {}
        for chunk in flags.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" in chunk:
                name, value = chunk.split(":", 1)
                mapping[name.strip()] = value.strip().lower() != "off"
            else:
                mapping[chunk] = True
        return mapping

    def _get_store(self) -> dict[str, Any]:
        """Get the router's plugin_info store."""
        return self._router._plugin_info  # type: ignore[no-any-return]

    def _notify_children(self, old_config: dict[str, Any], new_config: dict[str, Any]) -> None:
        """Notify child routers about config change for this plugin."""
        plugin_children = getattr(self._router, "_plugin_children", {})
        child_routers = plugin_children.get(self.name, [])
        for child_router in child_routers:
            child_plugin = child_router._plugins_by_name.get(self.name)
            if child_plugin:
                child_plugin.on_parent_config_changed(old_config, new_config)

    def _configure_params(self) -> set[str]:
        """Return the parameter names this plugin's configure() accepts.

        Reads the original (pre-wrap) configure signature so callers can pass
        only the keys it understands. BasePlugin's own configure takes none.
        """
        configure = type(self).configure
        original = getattr(configure, "__wrapped__", configure)
        return {
            name
            for name, p in inspect.signature(original).parameters.items()
            if name != "self"
            and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        }

    # =========================================================================
    # METHODS TO OVERRIDE IN CUSTOM PLUGINS
    # =========================================================================

    def configure(self, *, _target: str = "_all_", flags: str | None = None) -> None:
        """Override to define accepted configuration parameters.

        Define your plugin's configuration options as method parameters.
        The wrapper added by __init_subclass__ handles:
            - Parsing ``flags`` (e.g. "enabled,before:off") into booleans
            - Routing to ``_target`` ("_all_", handler name, or comma-separated)
            - Pydantic validation via @validate_call
            - Writing to the router's config store

        Example::

            def configure(self, enabled: bool = True, threshold: int = 10):
                pass  # Storage is handled by the wrapper

        Args:
            _target: Where to write config. "_all_" for router-level,
                     "handler_name" for per-handler, or "h1,h2" for multiple.
            flags: String like "enabled,before:off" parsed into booleans.
        """
        # Base configure just handles flags if provided
        if flags:
            kwargs = self._parse_flags(flags)
            self._write_config(_target, kwargs)

    def on_decore(
        self, router: Any, func: Callable, entry: MethodEntry
    ) -> None:  # pragma: no cover - default no-op
        """Override to run logic when a handler is registered.

        Called once per handler at decoration time. Use this to:
            - Inspect type hints and store metadata
            - Pre-compute validation models
            - Annotate ``entry.metadata`` for later use in wrap_handler

        Args:
            router: The Router instance registering the handler.
            func: The original handler function.
            entry: MethodEntry with name, func, router, plugins, metadata.
        """

    def wrap_handler(
        self,
        router: Any,
        entry: MethodEntry,
        call_next: Callable,
    ) -> Callable:
        """Override to wrap handler invocation with custom logic.

        Called to build the middleware chain. Return a callable that:
            - Optionally does pre-processing
            - Calls ``call_next(*args, **kwargs)``
            - Optionally does post-processing
            - Returns the result

        Example::

            def wrap_handler(self, router, entry, call_next):
                def wrapper(*args, **kwargs):
                    print(f"Before {entry.name}")
                    result = call_next(*args, **kwargs)
                    print(f"After {entry.name}")
                    return result
                return wrapper

        Args:
            router: The Router instance.
            entry: MethodEntry for the handler being wrapped.
            call_next: The next callable in the chain.

        Returns:
            A callable with the same signature as call_next.
        """
        return call_next

    def deny_reason(self, entry: Any, **filters: Any) -> str:  # pragma: no cover - optional hook
        """Override to control entry visibility during introspection.

        Called by ``router.nodes()`` and ``router.node()`` to decide if an entry
        should be included in results or allowed for execution.

        Args:
            entry: MethodEntry being checked.
            **filters: All filter criteria passed to ``nodes()`` or ``node()``.

        Returns:
            "": Entry is allowed (no reason to deny).
            "not_authenticated": Entry requires auth but no credentials (401).
            "not_authorized": Credentials provided but insufficient (403).
            "not_available": Capability not available (501).
        """
        return ""

    def entry_metadata(
        self, router: Any, entry: MethodEntry
    ) -> dict[str, Any]:  # pragma: no cover - optional hook
        """Override to provide plugin-specific metadata for a handler.

        Called by ``router.nodes()`` to gather plugin metadata.

        Args:
            router: The Router instance.
            entry: MethodEntry being described.

        Returns:
            Dict of plugin-specific metadata for this handler.
        """
        return {}

    def on_attached_to_parent(self, parent_plugin: BasePlugin) -> None:
        """Handle attachment to a parent router with this plugin.

        Called when a child router is attached to a parent that has this plugin.
        The child plugin can decide how to handle the parent's configuration.

        Default behavior:
        - Copies parent's _all_ config to child's _all_ config
        - Does NOT overwrite if child already has _all_ config (beyond defaults)

        Override to customize inheritance behavior (e.g., AuthPlugin does
        union of tags instead of replacement).

        Args:
            parent_plugin: The parent's plugin instance of the same type.
        """
        parent_config = parent_plugin.configuration()
        my_config = self.configuration()
        # Only copy if child has just the default config
        default_config = {"enabled": True}
        if my_config == default_config and parent_config != default_config:
            # configuration() always carries "enabled" (seeded in _all_), but a
            # plugin's configure() need not accept it (e.g. PydanticPlugin only
            # takes "disabled"). Pass only the keys its signature accepts, so
            # inheriting a non-default parent config never raises.
            accepted = self._configure_params()
            copyable = {k: v for k, v in parent_config.items() if k in accepted}
            if copyable:
                self.configure(**copyable)

    def on_parent_config_changed(
        self, old_config: dict[str, Any], new_config: dict[str, Any]
    ) -> None:
        """React when parent router's plugin config changes.

        Called when the parent router modifies its configuration for this
        plugin type. The child plugin can decide how to handle the change.

        Default behavior:
        - If child's _all_ config equals old_config (was aligned) → update to new_config
        - If child's _all_ config differs (was customized) → ignore change

        This preserves explicit child customizations while keeping "default"
        children in sync with parent changes.

        Args:
            old_config: The parent's previous _all_ configuration.
            new_config: The parent's new _all_ configuration.
        """
        my_config = self.configuration()
        if my_config == old_config:
            # Child was aligned with parent, update to follow. Pass only the
            # keys configure() accepts — new_config always carries "enabled",
            # which a strict-signature configure() would reject.
            accepted = self._configure_params()
            copyable = {k: v for k, v in new_config.items() if k in accepted}
            if copyable:
                self.configure(**copyable)
