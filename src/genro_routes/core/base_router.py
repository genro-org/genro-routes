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

"""Plugin-free router runtime for Genro Routes.

This module exposes :class:`BaseRouter`, which binds methods on an object
instance, resolves path selectors (using '/' separator), and exposes rich
introspection without any plugin logic. Subclasses add middleware but must
preserve these semantics.

Constructor and slots
---------------------
Constructor signature::

    BaseRouter(owner, *, prefix=None, description=None,
               default_entry="index", get_default_handler=None,
               get_kwargs=None)

- ``owner`` is required; ``None`` raises ``ValueError``. Routers are bound to
  this instance and never re-bound. Every router is created by its owning
  RoutingClass (lazy ``route`` property) and registers itself with the owner;
  its ``name`` is always ``"route"``.
- ``description``: optional human-readable description of this router's purpose.
  Included in ``nodes()`` output for documentation/introspection.
- ``default_entry``: the fallback entry name (default: "index") used when a path
  cannot be fully resolved. The router returns this entry with any unconsumed
  path segments passed as positional arguments when invoked.
- Slots: ``instance``, ``name``, ``prefix``, ``description``, ``default_entry``,
  ``__entries_raw`` (logical name → MethodEntry with handler), ``_children``
  (alias → child router), ``_get_defaults``, ``_bound``.

Lazy binding
------------
Routers use lazy binding: methods decorated with ``@route`` are discovered and
registered automatically on first use (node/nodes). No explicit bind() call
is needed.

Marker discovery
----------------
``_iter_marked_methods`` walks the MRO of ``type(owner)`` (child classes
first, so derived overrides win), scans ``__dict__`` for plain functions
carrying a ``_route_decorator_kw`` marker. Every marker belongs to this router.

Handler table and wrapping
--------------------------
- ``_register_callable`` creates a ``MethodEntry`` and stores it in ``_entries``.
- ``_rebuild_handlers`` updates each entry's ``handler`` attribute by passing through
  ``_wrap_handler`` (default: passthrough). Subclasses may inject middleware.

Lookup and execution
--------------------
- ``node(path, **kwargs)`` resolves ``path`` using best-match resolution.
  Returns a ``RouterNode`` wrapper that is callable. The RouterNode contains
  metadata about the resolved entry. Unconsumed path segments are passed as
  positional arguments when the node is invoked.

Children (instance hierarchies)
-------------------------------
``include(source, name=...)`` links a Router or RouterNode into this router.
Accepts a Router (child hierarchy) or RouterNode (entry alias).

``detach_instance(child)`` removes all routers belonging to a child instance.

Instance attachment is handled by ``add_branches({"name", "instance"})``, which
links the instance's router via ``include()`` and sets ``_routing_parent``.
On the first (primary) attachment ``include()`` triggers plugin inheritance
via ``_on_attached_to_parent``; subsequent includes of the same router are
navigational shortcuts only.

Introspection
-------------
- ``nodes(**kwargs)`` builds a nested dict of routers and entries respecting
  filters. Returns dict with ``entries`` and ``routers`` keys only if non-empty.
  Output includes ``description`` (router's description) and ``owner_doc``
  (owner class docstring) for documentation purposes.
- Each entry may carry a dialect-neutral ``result`` block
  ``{"schema": <json schema | None>, "media_type": <str | None>}`` describing
  the return type and declared media type. It is produced by the plugin-enabled
  ``Router`` (from the pydantic ``response_schema`` and ``@route(media_type=...)``)
  so OpenAPI/MCP translators read it instead of re-deriving the output.

Hooks for subclasses
--------------------
- ``_wrap_handler``: override to wrap callables (middleware stack).
- ``_after_entry_registered``: invoked after registering a handler.
- ``_on_attached_to_parent``: invoked when a child router is linked into this router.
- ``_describe_entry_extra``: allow subclasses to extend per-entry description.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterator
from typing import Any

from genro_toolbox.typeutils import safe_is_instance

from genro_routes.plugins._base_plugin import MethodEntry

from .router_interface import RouterInterface
from .router_node import RouterNode

__all__ = ["BaseRouter"]


class BaseRouter(RouterInterface):
    """Plugin-free router bound to an object instance.

    Responsibilities:
        - Register bound methods/functions with logical names (optionally via markers)
        - Resolve path selectors (using '/' separator) across child routers
        - Expose handler tables and introspection data
        - Provide hooks for subclasses to wrap handlers or filter introspection
    """

    __slots__ = (
        "instance",
        "name",
        "prefix",
        "description",
        "default_entry",
        "__entries_raw",
        "_children",
        "_branches",
        "_get_defaults",
        "_bound",
    )

    def __init__(
        self,
        owner: Any,
        *,
        prefix: str | None = None,
        description: str | None = None,
        default_entry: str = "index",
        get_default_handler: Callable | None = None,
        get_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if owner is None:
            raise ValueError("Router requires a parent instance")
        if not safe_is_instance(owner, "genro_routes.core.routing.RoutingClass"):
            raise TypeError(
                f"Router owner must be a RoutingClass instance, got {type(owner).__name__}. "
                "Inherit from RoutingClass to use Router."
            )
        self.instance = owner
        self.name = "route"
        self.prefix = prefix or ""
        self.description = description
        self.default_entry = default_entry
        self._bound = False
        self.__entries_raw: dict[str, MethodEntry] = {}
        self._children: dict[str, BaseRouter] = {}
        self._branches: dict[str, dict[str, Any]] = {}
        defaults: dict[str, Any] = dict(get_kwargs or {})
        if get_default_handler is not None:
            defaults.setdefault("default_handler", get_default_handler)
        self._get_defaults: dict[str, Any] = defaults
        self.instance._register_router(self)

    # ------------------------------------------------------------------
    # Lazy binding property
    # ------------------------------------------------------------------
    @property
    def _entries(self) -> dict[str, MethodEntry]:
        """Access entries dict, triggering lazy binding if needed."""
        if not self._bound:
            self._bind()
        return self.__entries_raw

    @property
    def current_capabilities(self) -> set[str]:
        """Collect capabilities from instance and parent chain.

        Walks up the _routing_parent chain accumulating capabilities from
        each RoutingClass instance.

        Returns:
            Combined set of capabilities from all instances in the hierarchy.
        """
        accumulated: set[str] = set()
        instance = self.instance

        while instance is not None:
            instance_caps = getattr(instance, "capabilities", None)
            if instance_caps:
                accumulated.update(instance_caps)
            instance = getattr(instance, "_routing_parent", None)

        return accumulated

    def _is_known_plugin(self, prefix: str) -> bool:
        """Check if prefix corresponds to a registered plugin name."""
        try:
            from genro_routes.core.router import Router  # type: ignore
        except Exception:  # pragma: no cover - import safety
            return False
        return prefix in Router.available_plugins()

    def _get_plugin_default_param(self, plugin_name: str) -> str | None:
        """Return the default parameter name for a plugin, or None."""
        try:
            from genro_routes.core.router import Router  # type: ignore
        except Exception:  # pragma: no cover - import safety
            return None
        registry = Router.available_plugins()
        plugin_class = registry.get(plugin_name)
        if plugin_class is None:
            return None
        return getattr(plugin_class, "plugin_default_param", None)

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------
    def add_entry(
        self,
        target: Any,
        *,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        replace: bool = False,
        **options: Any,
    ) -> BaseRouter:
        """Register handler(s) on this router.

        Note:
            For most use cases, prefer the ``@route`` decorator.
            Use ``add_entry`` directly only for dynamic registration
            (e.g., introspection-based mapping of external libraries).

        Args:
            target: Callable, attribute name(s), comma-separated string, or wildcard marker.
            name: Logical name override for this entry.
            metadata: Extra metadata stored on the MethodEntry.
            replace: Allow overwriting an existing logical name.
            options: Extra metadata merged into entry metadata.

        Returns:
            self (to allow chaining).

        Raises:
            ValueError: on handler name collision when replace is False.
            AttributeError: when resolving missing attributes on owner.
            TypeError: on unsupported target type.
        """
        entry_name = name
        # Split plugin-scoped options (<plugin>_<key>) and meta_* from core options
        plugin_options: dict[str, dict[str, Any]] = {}
        core_options: dict[str, Any] = {}
        for key, value in options.items():
            # Handle meta_* kwargs - group under "meta" key
            if key.startswith("meta_"):
                meta_key = key[5:]  # strip "meta_"
                core_options.setdefault("meta", {})[meta_key] = value
                continue
            if "_" in key:
                plugin_name, plug_key = key.split("_", 1)
                if plugin_name and plug_key and self._is_known_plugin(plugin_name):
                    plugin_options.setdefault(plugin_name, {})[plug_key] = value
                    continue
            elif self._is_known_plugin(key):
                default_param = self._get_plugin_default_param(key)
                if default_param:
                    plugin_options.setdefault(key, {})[default_param] = value
                    continue
            core_options[key] = value

        if isinstance(target, (list, tuple, set)):
            for entry in target:
                self.add_entry(
                    entry,
                    name=entry_name,
                    metadata=dict(metadata or {}),
                    replace=replace,
                    **core_options,
                )
            return self

        if isinstance(target, str):
            target = target.strip()
            if not target:
                return self
            if target in {"*", "_all_", "__all__"}:
                self._register_marked(
                    name=entry_name,
                    metadata=metadata,
                    replace=replace,
                    extra=core_options,
                    plugin_options=plugin_options,
                )
                self._bound = True  # Mark as bound after marker discovery
                return self
            if "," in target:
                for chunk in target.split(","):
                    chunk = chunk.strip()
                    if chunk:
                        self.add_entry(
                            chunk,
                            name=entry_name,
                            metadata=dict(metadata or {}),
                            replace=replace,
                            **core_options,
                        )
                return self
            bound = getattr(self.instance, target)
        elif callable(target):
            bound = (
                target
                if inspect.ismethod(target)
                else target.__get__(self.instance, type(self.instance))
            )
        else:
            raise TypeError(f"Unsupported entry target: {target!r}")

        entry_meta = dict(metadata or {})
        entry_meta.update(core_options)
        self._register_callable(
            bound,
            name=entry_name,
            metadata=entry_meta,
            replace=replace,
            plugin_options=plugin_options,
        )
        return self

    def _register_callable(
        self,
        bound: Callable,
        *,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        replace: bool = False,
        plugin_options: dict[str, dict[str, Any]] | None = None,
        endpoint_id: str | None = None,
    ) -> None:
        """Create a MethodEntry and store it in the entries table.

        Args:
            bound: The bound method to register.
            name: Optional name override (otherwise uses func name with prefix stripped).
            metadata: Extra metadata to attach to the entry.
            replace: If True, allow overwriting existing entry.
            plugin_options: Per-plugin configuration from decorator kwargs.
            endpoint_id: Optional globally unique identifier for reverse lookup.
        """
        logical_name = self._resolve_name(bound.__name__, name_override=name)
        if logical_name in self._entries and not replace:
            raise ValueError(f"Handler name collision: {logical_name}")
        entry = MethodEntry(
            name=logical_name,
            func=bound,
            router=self,
            plugins=[],
            metadata=dict(metadata or {}),
            endpoint_id=endpoint_id,
        )
        # Attach plugin-scoped config to metadata for later consumption by plugin-enabled routers.
        if plugin_options:
            entry.metadata["plugin_config"] = plugin_options
        self._entries[logical_name] = entry
        self._after_entry_registered(entry)
        self._rebuild_handlers()

    def _register_marked(
        self,
        *,
        name: str | None,
        metadata: dict[str, Any] | None,
        replace: bool,
        extra: dict[str, Any],
        plugin_options: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Discover and register all @route-decorated methods for this router.

        Iterates through methods carrying a _route_decorator_kw marker and
        registers each as an entry.
        """
        for func, marker in self._iter_marked_methods():
            entry_override = marker.pop("entry_name", None)
            marker_endpoint_id = marker.pop("endpoint_id", None)
            entry_name = name if name is not None else entry_override
            entry_meta = dict(metadata or {})
            entry_meta.update(marker)
            entry_meta.update(extra)
            # Split plugin-scoped options and meta_* from marker payload
            marker_plugin_opts: dict[str, dict[str, Any]] = {}
            core_marker: dict[str, Any] = {}
            for key, value in entry_meta.items():
                # Handle meta_* kwargs - group under "meta" key
                if key.startswith("meta_"):
                    meta_key = key[5:]  # strip "meta_"
                    core_marker.setdefault("meta", {})[meta_key] = value
                    continue
                if "_" in key:
                    plugin_name, plug_key = key.split("_", 1)
                    if plugin_name and plug_key and self._is_known_plugin(plugin_name):
                        marker_plugin_opts.setdefault(plugin_name, {})[plug_key] = value
                        continue
                elif self._is_known_plugin(key):
                    default_param = self._get_plugin_default_param(key)
                    if default_param:
                        marker_plugin_opts.setdefault(key, {})[default_param] = value
                        continue
                core_marker[key] = value
            entry_meta = core_marker
            merged_plugin_opts: dict[str, dict[str, Any]] = {}
            if plugin_options:
                merged_plugin_opts.update(plugin_options)
            for pname, pdata in marker_plugin_opts.items():
                merged_plugin_opts.setdefault(pname, {}).update(pdata)
            bound = func.__get__(self.instance, type(self.instance))
            self._register_callable(
                bound,
                name=entry_name,
                metadata=entry_meta,
                replace=replace,
                plugin_options=merged_plugin_opts or None,
                endpoint_id=marker_endpoint_id,
            )

    def _iter_marked_methods(self) -> Iterator[tuple[Callable, dict[str, Any]]]:
        """Yield (func, marker_dict) for methods decorated with @route.

        Walks the MRO (child classes first) and scans __dict__ for functions
        carrying a _route_decorator_kw marker. Every marker belongs to this
        router (one router per class).
        """
        cls = type(self.instance)
        # Track seen method names to respect MRO (derived class wins)
        # Track seen function ids to avoid duplicate registration of aliases (alias = original)
        seen_names: set[str] = set()
        seen_funcs: set[int] = set()
        for base in cls.__mro__:
            base_dict = vars(base)
            for attr_name, value in base_dict.items():
                if not inspect.isfunction(value):
                    continue
                # Skip if method name already seen (MRO: derived wins)
                if attr_name in seen_names:
                    continue
                seen_names.add(attr_name)
                # Skip if same function already yielded (alias deduplication)
                func_id = id(value)
                if func_id in seen_funcs:
                    continue
                seen_funcs.add(func_id)
                marker = getattr(value, "_route_decorator_kw", None)
                if marker is None:
                    continue
                yield value, dict(marker)

    def _resolve_name(self, func_name: str, *, name_override: str | None) -> str:
        """Compute the logical entry name from the function name.

        Args:
            func_name: The __name__ of the function being registered.
            name_override: Explicit name to use (bypasses prefix stripping).

        Returns:
            The entry name: either name_override, or func_name with prefix stripped.
        """
        if name_override:
            return name_override
        if self.prefix and func_name.startswith(self.prefix):
            return func_name[len(self.prefix) :]
        return func_name

    def _wrap_handler(
        self, entry: MethodEntry, call_next: Callable
    ) -> Callable:  # pragma: no cover - overridden by plugin routers
        """Wrap a handler callable (hook for subclasses to add middleware).

        Default implementation returns call_next unchanged. Subclasses (Router)
        override this to build a middleware pipeline from attached plugins.

        Args:
            entry: The MethodEntry being wrapped.
            call_next: The next callable in the chain (initially entry.func).

        Returns:
            The wrapped callable to use as entry.handler.
        """
        return call_next

    # ------------------------------------------------------------------
    # Binding (finalization)
    # ------------------------------------------------------------------
    def _bind(self) -> None:
        """Finalize router configuration and trigger route discovery.

        Internal method called automatically on first use (lazy binding).
        Discovers @route decorated methods and registers them.
        """
        if self._bound:
            return  # Already bound, no-op
        self._bound = True  # Set BEFORE work to avoid recursion via properties
        self.add_entry("*")

    def _require_bound(self, operation: str) -> None:
        """Ensure the router is bound, auto-binding if needed.

        Args:
            operation: Name of the operation being attempted (unused, kept for API).

        This implements lazy binding: the router auto-binds on first use.
        """
        if not self._bound:
            self._bind()

    # ------------------------------------------------------------------
    # Handler rebuilding
    # ------------------------------------------------------------------
    def _rebuild_handlers(self) -> None:
        """Rebuild wrapped handlers for all entries owned by this router."""
        for entry in self.__entries_raw.values():
            if entry.router is not self:
                continue  # alias — handler belongs to the source router
            entry.handler = self._wrap_handler(entry, entry.func)

    # ------------------------------------------------------------------
    # Children management
    # ------------------------------------------------------------------
    def include(self, source: Any, *, name: str | None = None) -> None:
        """Include a Router or RouterNode (entry alias) into this router.

        Accepts two source types:

        - **Router**: links the source router as a child of this router.
          Plugin inheritance is triggered via ``_on_attached_to_parent``.
          If the source router belongs to a RoutingClass, ``_routing_parent``
          is set on the owner.
        - **RouterNode**: creates an alias for a single entry in this router.
          The original handler is shared — no copy is made.

        Args:
            source: A Router instance or a RouterNode from ``node()``.
            name: Alias in this router. For Router sources, defaults to
                ``source.name``. For RouterNode sources, required.

        Raises:
            TypeError: If source is not a Router or RouterNode.
            ValueError: If name is required but not provided.
            ValueError: If alias collision in _children or _entries.

        Examples::

            # Include a router
            self._sys.include(swagger.route, name="swagger")

            # Include an entry as alias
            fatture.route.include(
                pagamenti.route.node("collega_a_fattura"),
                name="collega_pagamento",
            )
        """
        from .router_node import RouterNode

        if isinstance(source, BaseRouter):
            self._include_router(source, name)
        elif isinstance(source, RouterNode):
            self._include_node(source, name)
        else:
            raise TypeError(
                f"include() accepts Router or RouterNode, got {type(source).__name__}"
            )

    def _include_router(self, source: BaseRouter, name: str | None) -> None:
        """Link a Router as a child of this router.

        If the source router has no parent yet (first include), this is a
        primary attachment: plugin inheritance is triggered and _routing_parent
        is set on the owner.

        If the source already has a parent (subsequent include), this creates
        a secondary link — a navigational shortcut only. No plugin inheritance,
        no _routing_parent change.
        """
        alias = name or source.name
        if not alias:
            raise ValueError("include() requires a name (source router has no name)")
        if alias in self._children and self._children[alias] is not source:
            raise ValueError(f"Child name collision: {alias}")
        self._children[alias] = source
        owner = source.instance
        is_primary = owner is not None and getattr(owner, "_routing_parent", None) is None
        if is_primary:
            source._on_attached_to_parent(self)
            object.__setattr__(owner, "_routing_parent", self.instance)

    # ------------------------------------------------------------------
    # Branches: declarative factory-based subrouters (lazy/eager)
    # ------------------------------------------------------------------
    def add_branches(self, specs: Any) -> None:
        """Declare one or more child branches. The single entry point.

        Accepts a single spec dict, a list of dicts, or any iterable/generator
        of dicts. Each spec is one of three mutually exclusive forms:

        - factory ``{"name", "cls", "params"}`` — **lazy**: the instance is
          constructed on first traversal of the branch;
        - instance ``{"name", "instance"}`` — **eager**: an already-built
          instance, linked as a child immediately;
        - alias ``{"name", "alias"}`` — a symlink to an absolute path.

        The timing is derived from the form (``cls`` → lazy, ``instance`` →
        eager); there is no ``lazy`` flag.
        """
        items = [specs] if isinstance(specs, dict) else list(specs)
        for spec in items:
            self._add_branch_spec(spec)

    def _add_branch_spec(self, spec: dict[str, Any]) -> None:
        """Validate and store/materialize a single branch spec.

        ``cls``, ``instance`` and ``alias`` are mutually exclusive.
        """
        name = spec["name"]
        if name in self._branches or name in self._children:
            raise ValueError(f"Branch name collision: {name}")
        forms = [k for k in ("cls", "instance", "alias") if k in spec]
        if len(forms) != 1:
            raise ValueError(
                f"Branch '{name}': exactly one of 'cls', 'instance', 'alias' is required"
            )
        form = forms[0]
        if form == "alias":
            # Alias branch: a symlink to an absolute path from the tree root.
            # No instance is ever built for it; navigation rewrites the path.
            self._branches[name] = {"name": name, "alias": spec["alias"]}
            return
        if form == "instance":
            if "params" in spec:
                raise ValueError(f"Branch '{name}': 'params' is not allowed with 'instance'")
            child = spec["instance"]
            if not safe_is_instance(child, "genro_routes.core.routing.RoutingClass"):
                raise TypeError(f"Branch '{name}': 'instance' must be a RoutingClass instance")
            bound = getattr(child, "_routing_parent", None)
            if bound is not None and bound is not self.instance:
                raise ValueError(
                    f"Branch '{name}': instance already bound to another parent"
                )
            # Eager: an already-built instance is linked as a child now.
            self._include_router(child.route, name)
            return
        # Factory: always lazy — built on first traversal.
        self._branches[name] = {
            "name": name,
            "cls": spec["cls"],
            "params": dict(spec.get("params") or {}),
        }

    def remove_branch(self, name: str) -> None:
        """Remove a declared branch. If already materialized, detach its child."""
        self._branches.pop(name, None)
        child = self._children.get(name)
        if child is not None:
            self.detach_instance(child.instance)

    @property
    def branches(self) -> dict[str, dict[str, Any]]:
        """Read-only view of declared (not-yet-materialized) branch specs."""
        return dict(self._branches)

    def _materialize_branch(self, name: str) -> BaseRouter:
        """Construct the branch's instance and link it as a child.

        Single point where a branch instance is built: pop the spec, construct
        ``cls(**params)``, and include its router (which wires ``_routing_parent``
        and triggers plugin inheritance). Idempotent per name.
        """
        existing = self._children.get(name)
        if existing is not None:
            return existing
        spec = self._branches[name]
        instance = spec["cls"](**spec["params"])
        self._include_router(instance.route, name)
        # Pop only after a successful build: a failing constructor leaves the
        # spec declared, so the error is repeatable instead of the branch
        # silently disappearing.
        self._branches.pop(name, None)
        return self._children[name]

    def _root_router(self) -> BaseRouter:
        """Return the tree's root router by walking up the _routing_parent chain."""
        router = self
        instance = router.instance
        parent = getattr(instance, "_routing_parent", None)
        while parent is not None:
            router = parent.route
            instance = parent
            parent = getattr(instance, "_routing_parent", None)
        return router

    def _alias_spec(self, name: str) -> dict[str, Any] | None:
        """Return the branch spec for ``name`` if it is an alias, else None."""
        spec = self._branches.get(name)
        if spec is not None and "alias" in spec:
            return spec
        return None

    def _lazy_branch_node_info(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Build the ``nodes()`` description of a non-materialized lazy branch."""
        cls = spec["cls"]
        info: dict[str, Any] = {
            "name": spec["name"],
            "lazy": True,
            "owner_doc": cls.__doc__,
        }
        leaves = self._branch_class_leaves(cls)
        if leaves:
            info["entries"] = leaves
        return info

    def _branch_class_leaves(self, cls: type) -> dict[str, Any]:
        """Collect a lazy branch's ``@route`` leaves from the CLASS alone.

        Reads the class-level ``_route_decorator_kw`` marker without building an
        instance, so ``nodes()`` can describe a lazy branch's leaves cheaply.
        """
        entries: dict[str, Any] = {}
        seen_names: set[str] = set()
        seen_funcs: set[int] = set()
        for base in cls.__mro__:
            for attr_name, value in vars(base).items():
                if not inspect.isfunction(value) or attr_name in seen_names:
                    continue
                seen_names.add(attr_name)
                func_id = id(value)
                if func_id in seen_funcs:
                    continue
                seen_funcs.add(func_id)
                marker = getattr(value, "_route_decorator_kw", None)
                if marker is None:
                    continue
                entry_name = marker.get("entry_name") or attr_name
                entries[entry_name] = {
                    "name": entry_name,
                    "callable": value,
                    "metadata": {},
                    "doc": inspect.getdoc(value) or "",
                }
        return entries

    def _include_node(self, source: Any, name: str | None) -> None:
        """Create an entry alias from a RouterNode."""
        if name is None:
            raise ValueError("include() requires name when including a RouterNode")
        entry = source._entry
        if entry is None:
            raise ValueError(
                f"Cannot include RouterNode: no entry resolved (error={source.error})"
            )
        # Use _entries (triggers lazy binding) so collision check works
        # even before the router has been accessed
        if name in self._entries and self._entries[name] is not entry:
            raise ValueError(f"Entry name collision: {name}")
        self._BaseRouter__entries_raw[name] = entry  # type: ignore[attr-defined]

    def detach_instance(self, routing_child: Any) -> BaseRouter:
        """Detach all routers belonging to a RoutingClass instance."""
        if not safe_is_instance(routing_child, "genro_routes.core.routing.RoutingClass"):
            raise TypeError("detach_instance() requires a RoutingClass instance")
        removed: list[str] = []
        for alias, router in list(self._children.items()):
            if router.instance is routing_child:
                removed.append(alias)
                self._children.pop(alias, None)

        if getattr(routing_child, "_routing_parent", None) is self.instance:
            object.__setattr__(routing_child, "_routing_parent", None)

        # Clean up plugin children references to avoid memory leaks
        plugin_children = getattr(self, "_plugin_children", None)
        if plugin_children is not None:
            for plugin_name, children_list in list(plugin_children.items()):
                plugin_children[plugin_name] = [
                    r for r in children_list if r.instance is not routing_child
                ]

        # No hard error if nothing was removed; detach is best-effort.
        return routing_child  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------
    def get_url(self, path: str, **kwargs: Any) -> str:
        """Build a URL path for a handler, appending positional parameters.

        Accepts a path (e.g., ``"users/detail"``) or an endpoint_id with
        ``@`` prefix (e.g., ``"@invoice.detail"``). Keyword arguments that
        match positional parameters in the handler's signature are appended
        as path segments in declaration order.

        Args:
            path: Handler path or ``"@endpoint_id"``.
            **kwargs: Parameter values to append to the path. Only
                positional-or-keyword parameters are appended (in
                declaration order). Keyword-only parameters are ignored.

        Returns:
            The full URL path string.

        Raises:
            ValueError: If the path does not resolve to a valid handler.

        Example::

            # Given: @route(endpoint_id="invoice.detail")
            #        def detail(self, invoice_id): ...
            # Mounted at: billing/detail

            router.get_url("@invoice.detail", invoice_id=123)
            # → "billing/detail/123"

            router.get_url("billing/detail", invoice_id=123)
            # → "billing/detail/123"
        """
        node = self._find_candidate_node(path)
        if node._entry is None:
            raise ValueError(f"get_url: path '{path}' does not resolve to a handler")
        base: str = node.path or ""
        if not kwargs:
            return base
        # Get positional parameter names from the handler signature
        sig = inspect.signature(node._entry.func)
        positional_names = [
            p.name
            for p in sig.parameters.values()
            if p.name != "self"
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        segments = [str(kwargs[name]) for name in positional_names if name in kwargs]
        if segments:
            return f"{base}/{'/'.join(segments)}"
        return base

    # ------------------------------------------------------------------
    # Node resolution
    # ------------------------------------------------------------------
    def _find_candidate_node(
        self, path: str, _alias_seen: frozenset[int] | None = None
    ) -> RouterNode:
        """Resolve path to a candidate RouterNode without permission checks.

        Pure path resolution: walks through children, finds entry or falls back
        to default_entry. No auth checks applied.

        Supports endpoint_id lookup with ``@`` prefix: ``"@my-endpoint-id"``
        resolves via recursive search instead of path traversal.

        Args:
            path: Path to resolve (e.g., "entry", "child/entry/arg1/arg2",
                  or "@endpoint_id" for reverse lookup).

        Returns:
            RouterNode with entry reference. If path not found, node.error
            will be set to "not_found" when permission checks are applied.
        """
        stripped = path.strip("/")
        if not stripped:
            return RouterNode(self, path="")

        if stripped.startswith("@"):
            return self._find_by_endpoint_id(stripped[1:])

        parts = stripped.split("/")
        router: BaseRouter | None = self
        pathlist: list[str] = []

        while parts and router:
            last_router = router
            head = parts.pop(0)
            # Alias branch: rewrite the path to the target (absolute, from root)
            # and resolve from there. Guard against alias cycles.
            alias_spec = router._alias_spec(head)
            if alias_spec is not None:
                return router._resolve_alias(alias_spec, parts, _alias_seen)
            pathlist.append(head)
            if head in router._entries:
                return RouterNode(router, entry_name=head, partial=parts, path="/".join(pathlist))
            if head not in router._children and head in router._branches:
                router._materialize_branch(head)
            router = router._children.get(head)

        if router:
            return RouterNode(router, path="/".join(pathlist))

        return RouterNode(last_router, partial=[head] + parts, path="/".join(pathlist[:-1]))

    def _resolve_alias(
        self, spec: dict[str, Any], rest: list[str], seen: frozenset[int] | None
    ) -> RouterNode:
        """Resolve an alias branch: rewrite to its absolute target + rest, from root.

        ``seen`` tracks alias spec ids already followed in this resolution to
        detect cycles (a -> b -> a).
        """
        spec_id = id(spec)
        seen = seen or frozenset()
        if spec_id in seen:
            raise ValueError(f"Alias cycle detected at '{spec['name']}' -> '{spec['alias']}'")
        target = spec["alias"].strip("/")
        full = "/".join([target, *rest]) if rest else target
        return self._root_router()._find_candidate_node(full, _alias_seen=seen | {spec_id})

    def _find_by_endpoint_id(self, endpoint_id: str) -> RouterNode:
        """Resolve an endpoint_id to a RouterNode by recursive search.

        Walks the entire router tree looking for a MethodEntry whose
        endpoint_id matches the given value.

        Args:
            endpoint_id: The endpoint identifier to search for.

        Returns:
            RouterNode pointing to the matched entry, or a not-found node.
        """
        result = self._search_endpoint_id(endpoint_id, [])
        if result is not None:
            router, entry_name, path_parts = result
            return RouterNode(router, entry_name=entry_name, path="/".join(path_parts))
        return RouterNode(self, path=f"@{endpoint_id}")

    def _search_endpoint_id(
        self, endpoint_id: str, path_parts: list[str]
    ) -> tuple[BaseRouter, str, list[str]] | None:
        """Recursively search for endpoint_id in entries and children.

        Args:
            endpoint_id: The endpoint identifier to find.
            path_parts: Accumulated path segments for building the full path.

        Returns:
            Tuple of (router, entry_name, full_path_parts) or None.
        """
        for entry_name, entry in self._entries.items():
            if entry.endpoint_id == endpoint_id:
                return self, entry_name, [*path_parts, entry_name]
        # Only already-materialized children are searched (eager instances and
        # factories already traversed). Lazy factory branches stay in _branches
        # and are intentionally skipped — searching them would force the whole
        # tree to build.
        for child_alias, child_router in self._children.items():
            result = child_router._search_endpoint_id(
                endpoint_id, [*path_parts, child_alias]
            )
            if result is not None:
                return result
        return None

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    def router_at_path(
        self, path: str, _alias_seen: frozenset[int] | None = None
    ) -> BaseRouter | None:
        """Find the router at the given path.

        Args:
            path: Path to navigate (e.g., "child/grandchild").
            _alias_seen: Internal — alias spec ids already followed (cycle guard).

        Returns:
            The router at the path, or None if not found.
        """
        parts = [p for p in path.strip("/").split("/") if p]
        router: BaseRouter | None = self
        while parts and router:
            head = parts.pop(0)
            # Alias branch: rewrite to the absolute target + rest, from root.
            alias_spec = router._alias_spec(head)
            if alias_spec is not None:
                spec_id = id(alias_spec)
                seen = _alias_seen or frozenset()
                if spec_id in seen:
                    raise ValueError(
                        f"Alias cycle detected at '{alias_spec['name']}' -> '{alias_spec['alias']}'"
                    )
                target = alias_spec["alias"].strip("/")
                full = "/".join([target, *parts]) if parts else target
                return router._root_router().router_at_path(full, _alias_seen=seen | {spec_id})
            # Navigating into a real branch materializes it (open the folder).
            if head not in router._children and head in router._branches:
                router._materialize_branch(head)
            router = router._children.get(head)
        return router

    def nodes(
        self,
        basepath: str | None = None,
        lazy: bool = False,
        pattern: str | None = None,
        forbidden: bool = False,
        _eager: bool = False,
        _alias_seen: frozenset[int] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return a tree of routers/entries/metadata respecting filters.

        Output is dialect-neutral: each entry carries name/doc/metadata, plugin
        config, and (when available) a ``result`` block ``{schema, media_type}``.
        Dialect translators (OpenAPI, MCP) are readers of this tree and live in
        the transport layer, not in the routing core.

        Args:
            basepath: Optional path to start from (e.g., "child/grandchild").
                      If provided, returns nodes starting from that point
                      in the hierarchy instead of from this router.
            lazy: If True, child routers are returned as router references
                  instead of recursively expanded. Use basepath to navigate
                  and expand specific children on demand.
            pattern: Optional regex pattern to filter entry names.
                     Only entries whose name matches the pattern are included.
                     Applied before plugin deny_reason() checks.
            forbidden: If True, include entries that are not allowed (e.g.,
                       due to authorization or capability requirements). These
                       entries will have a ``forbidden`` field with the reason
                       (e.g., "not_authorized", "not_available"). Default False.
            _eager: If True, expand EVERYTHING: materialize all declared lazy
                    branches and resolve alias branches to their target subtree.
                    Default False: lazy branches and aliases appear as
                    unresolved markers, nothing is constructed.
            _alias_seen: Internal — alias spec ids already expanded (cycle guard).
            **kwargs: Filter arguments passed to plugins via deny_reason().

        Returns:
            A dict containing:

            - ``name``: Router name
            - ``description``: Router description (if set)
            - ``owner_doc``: Owner class docstring (for documentation)
            - ``router``: Reference to this router
            - ``instance``: Owner instance
            - ``plugin_info``: Plugin configuration info
            - ``entries``: Dict of entry names to entry info (if any)
            - ``routers``: Dict of child names to child nodes (if any)
        """
        if basepath:
            router = self.router_at_path(basepath)
            if router:
                return router.nodes(
                    lazy=lazy, pattern=pattern, forbidden=forbidden, _eager=_eager, **kwargs
                )
            return {}
        # nodes() never builds: factory branches stay declared and are
        # described (not built) further down, unless _eager is requested.
        if _eager:
            # Full expansion requested: materialize every declared factory
            # branch (lazy included). Aliases stay as specs, resolved below.
            for branch_name in [n for n, s in list(self._branches.items()) if "alias" not in s]:
                self._materialize_branch(branch_name)
        # Compile pattern once if provided
        pattern_re = re.compile(pattern) if pattern else None

        entries: dict[str, Any] = {}
        for entry in self._entries.values():
            if pattern_re is not None and not pattern_re.search(entry.name):
                continue
            allow_result = self._entry_invalid_reason(entry, **kwargs)
            if allow_result == "":
                entries[entry.name] = self._entry_node_info(entry)
            elif forbidden:
                entry_info = self._entry_node_info(entry)
                entry_info["forbidden"] = allow_result
                entries[entry.name] = entry_info

        routers: dict[str, Any]
        if lazy:
            # In lazy mode, just return the router references - use basepath to expand
            routers = dict(self._children)
        else:
            routers = {
                child_name: child.nodes(
                    pattern=pattern,
                    forbidden=forbidden,
                    _eager=_eager,
                    _alias_seen=_alias_seen,
                    **kwargs,
                )
                for child_name, child in self._children.items()
            }
            # Remove empty routers only in non-lazy mode (unless forbidden=True)
            if not forbidden:
                routers = {k: v for k, v in routers.items() if v}

        # Describe declared branches not yet in _children. Default: unresolved
        # markers — nothing is constructed (describe != build). With _eager=True
        # (non-lazy mode) aliases are resolved and expanded, cycle-guarded.
        for branch_name, spec in list(self._branches.items()):
            if "alias" in spec:
                marker: dict[str, Any] = {"name": branch_name, "alias": spec["alias"]}
                if _eager and not lazy:
                    spec_id = id(spec)
                    seen = _alias_seen or frozenset()
                    if spec_id in seen:
                        raise ValueError(
                            f"Alias cycle detected at '{branch_name}' -> '{spec['alias']}'"
                        )
                    target_router = self.router_at_path(spec["alias"])
                    if target_router is not None:
                        marker = target_router.nodes(
                            pattern=pattern,
                            forbidden=forbidden,
                            _eager=True,
                            _alias_seen=seen | {spec_id},
                            **kwargs,
                        )
                        marker["alias"] = spec["alias"]
                        marker["name"] = branch_name
                routers[branch_name] = marker
            else:
                routers[branch_name] = self._lazy_branch_node_info(spec)

        # If nothing, return empty dict
        if not entries and not routers:
            return {}

        result: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "owner_doc": self.instance.__class__.__doc__,
            "router": self,
            "instance": self.instance,
            "plugin_info": self._get_plugin_info(),
        }
        if entries:
            result["entries"] = entries
        if routers:
            result["routers"] = routers

        return result

    def node(
        self,
        path: str,
        errors: dict[str, type[Exception]] | None = None,
        **kwargs: Any,
    ) -> RouterNode:
        """Return info about a single node (router or entry) at the given path.

        Unlike nodes() which returns the full subtree, this method returns
        information about just one specific node without recursion.

        This method always performs best-match resolution: it walks the path
        as far as possible, tracking the last valid callable node (entry or
        router with default_entry). If the exact path is not found, it falls
        back to that last valid node and passes unconsumed path segments as
        positional arguments when the node is invoked.

        The returned RouterNode is callable - invoking it executes the handler.

        Args:
            path: Path to the node (e.g., "entry_name" or "child/grandchild/entry").
            errors: Optional dict mapping error codes to custom exception classes.
                    Available codes (see ``RouterNode.ERROR_CODES``):

                    - ``not_found``: Path not found or varargs_required
                    - ``not_authorized``: Auth tags don't match (403)
                    - ``not_authenticated``: Auth required but not provided (401)
                    - ``signature_error``: The call does not fit the handler
                      signature - unknown keyword, missing required argument,
                      too many positionals (default class: TypeError)
                    - ``validation_error``: The signature is satisfied and
                      pydantic rejects the values

                    An exception raised by the handler body propagates
                    untouched, TypeError included.

                    Example::

                        node = router.node("handler", errors={
                            'not_found': HTTPNotFound,
                            'not_authorized': HTTPForbidden,
                        })

            **kwargs: Plugin-prefixed filter kwargs (e.g., auth_tags="x").

        Returns:
            A RouterNode with these public properties:

                - ``path``: Full path to this node
                - ``error``: Error code (None if ok, else "not_found", "not_authorized", etc.)
                - ``doc``: Entry docstring
                - ``metadata``: Entry metadata dict

            The RouterNode is callable::

                node = router.node("my_handler")
                result = node()  # Invoke the handler

            If error is set, calling the node raises the mapped exception.
        """
        # Find candidate node (pure path resolution)
        candidate = self._find_candidate_node(path)
        candidate.set_custom_exceptions(errors)

        # Set error via _entry_invalid_reason (handles both missing entry and plugin checks)
        candidate.error = candidate._router._entry_invalid_reason(candidate._entry, **kwargs) or None

        return candidate

    def _entry_node_info(self, entry: MethodEntry) -> dict[str, Any]:
        """Build info dict for a single entry."""
        info: dict[str, Any] = {
            "name": entry.name,
            "callable": entry.func,
            "metadata": entry.metadata,
            "doc": inspect.getdoc(entry.func) or entry.func.__doc__ or "",
        }
        extra = self._describe_entry_extra(entry, info)
        if extra:
            info.update(extra)
        return info

    def _get_plugin_info(self) -> dict[str, Any]:
        """Build plugin_info dict from _plugin_info store."""
        info_source = getattr(self, "_plugin_info", {}) or {}
        return {
            pname: {
                key: {
                    "config": dict(slot.get("config", {})),
                    "locals": dict(slot.get("locals", {})),
                }
                for key, slot in pdata.items()
            }
            for pname, pdata in info_source.items()
        }

    # ------------------------------------------------------------------
    # Plugin hooks (no-op for BaseRouter)
    # ------------------------------------------------------------------
    def iter_plugins(self) -> list[Any]:  # pragma: no cover - base router has no plugins
        return []

    def _on_attached_to_parent(
        self, parent: BaseRouter
    ) -> None:  # pragma: no cover - hook for subclasses
        """Hook for plugin-enabled routers to override when attached."""
        return None

    def _after_entry_registered(
        self, entry: MethodEntry
    ) -> None:  # pragma: no cover - hook for subclasses
        """Hook invoked after a handler is registered (subclasses may override)."""
        return None

    def _describe_entry_extra(
        self, entry: MethodEntry, base_description: dict[str, Any]
    ) -> dict[str, Any]:  # pragma: no cover - overridden when plugins present
        """Hook used by subclasses to inject extra description data."""
        return {}

    def _entry_invalid_reason(self, entry: MethodEntry | None, **filters: Any) -> str:
        """Hook used by subclasses to decide if an entry is exposed.

        Args:
            entry: The entry to check (None if not found).
            **filters: Filter kwargs.

        Returns:
            "": Entry is allowed.
            "not_found": Entry is None (path not resolved).
            "not_authenticated": Entry requires auth but no credentials provided (401).
            "not_authorized": Credentials provided but insufficient (403).
        """
        if entry is None:
            return "not_found"
        return ""
