# genro-routes — Code Reading Guide

**Version**: 0.22.0
**Status**: 🔴 DA REVISIONARE — Documento non ancora approvato

This guide accompanies a first-time reader of the source code. For each module
it explains **what it does**, **how it works** and above all **why it is built
that way** when the pattern is not obvious.

> **Recommended reading order**: follow the chapter numbering.
> Each chapter assumes you understood the previous ones.

---

## Table of Contents

1. [The big idea](#1-the-big-idea)
2. [File map](#2-file-map)
3. [The `@route` decorator — markers, not mutations](#3-the-route-decorator--markers-not-mutations)
4. [RouterInterface — the minimal contract](#4-routerinterface--the-minimal-contract)
5. [BaseRouter — the routing core](#5-baserouter--the-routing-core)
6. [RouterNode — the callable wrapper](#6-routernode--the-callable-wrapper)
7. [RoutingClass — the mixin that ties everything together](#7-routingclass--the-mixin-that-ties-everything-together)
8. [Router — BaseRouter + plugin pipeline](#8-router--baserouter--plugin-pipeline)
9. [The plugin system](#9-the-plugin-system)
10. [The built-in plugins](#10-the-built-in-plugins)
11. [Exceptions](#11-exceptions)
12. [Non-standard patterns — reasoned recap](#12-non-standard-patterns--reasoned-recap)
13. [The CLI adapter](#13-the-cli-adapter)
14. [Quick glossary](#14-quick-glossary)

---

## 1. The big idea

genro-routes is a **transport-agnostic routing engine**.

In Flask/FastAPI/Django routes are tied to HTTP: verbs, URL patterns, status
codes. Here routes are **named operations** (`list_orders`, `create_user`)
registered on **object instances**. The protocol (HTTP, WebSocket, MCP,
Telegram, CLI) is a separate adapter living in another package (e.g.
`genro-asgi`).

```text
┌──────────────────────────────┐     ┌────────────────────────┐
│  genro-routes                │     │  Transport adapter     │
│  - registers handlers        │     │  (genro-asgi, etc.)    │
│  - organizes hierarchies     │     │  - maps HTTP → node()  │
│  - applies plugins           │     │  - handles I/O         │
│  - exposes introspection     │     │  - serializes results  │
└──────────────────────────────┘     └────────────────────────┘
```

Two fundamental consequences:

- **Routers live on instances, not as global singletons.** Every `MyService()`
  creates its own router with its own plugins. No shared state.
- **One class, one router.** Every `RoutingClass` owns exactly one `Router`,
  created lazily and exposed as the read-only property `route`. Hierarchy and
  multiple "surfaces" are expressed by **composing instances**
  (`add_branches`), never by adding routers to a class.

---

## 2. File map

```text
src/genro_routes/
├── __init__.py              ← public API + plugin auto-import
├── exceptions.py            ← 4 exceptions (NotFound, NotAuthorized, ...)
├── core/
│   ├── __init__.py          ← re-export aggregator
│   ├── router_interface.py  ← ABC with 2 methods: node() and nodes()
│   ├── decorators.py        ← @route — pure marker, zero side effects
│   ├── context.py           ← RoutingContext — extensible container with parent chain
│   ├── base_router.py       ← ~1050 lines — core: binding, resolution, introspection
│   ├── router_node.py       ← callable wrapper returned by node()
│   ├── router.py            ← extends BaseRouter with plugins and middleware
│   └── routing.py           ← RoutingClass mixin + Section + _RoutingProxy
├── cli/
│   ├── __init__.py          ← RoutingCli — public API of the CLI adapter
│   ├── _builder.py          ← CliBuilder — builds a click tree from nodes()
│   ├── _type_map.py         ← ParamConverter — Python types → click
│   └── _formatters.py       ← OutputFormatter — JSON / table / raw
└── plugins/
    ├── __init__.py           ← docstring only, no imports
    ├── _base_plugin.py       ← MethodEntry + BasePlugin + _wrap_configure
    ├── logging.py            ← LoggingPlugin — timing and logging
    ├── pydantic.py            ← PydanticPlugin — input validation + response schema
    ├── auth.py                ← AuthPlugin — RBAC with tag matching
    ├── env.py                 ← EnvPlugin + CapabilitiesSet — dynamic feature flags
    └── channel.py             ← ChannelPlugin — transport-channel filtering
```

**Size**: ~4,700 lines of source code in total.

---

## 3. The `@route` decorator — markers, not mutations

**File**: `core/decorators.py` (78 lines)

```python
@route()
def list_orders(self):
    ...

@route(name="detail", auth="admin")
def handle_detail(self, order_id: int):
    ...
```

### What it does

Stores a dict on `func._route_decorator_kw`. It touches no router,
imports no heavy module, and returns the function **unchanged**.

### Why it is surprising

In Flask `@app.route("/path")` immediately mutates the global router. Here the
decorator runs at **class definition time** (import time), when the router
instance does not exist yet.

### Why it is built this way

The router is instance-scoped. Class `MyService` can be instantiated N times,
each with its own router. The decorator is shared at class level (via the MRO),
so it cannot talk to any specific instance. The solution: annotate the function
and let the router discover the markers later (lazy binding).

### Details worth noting

- **Keyword-only**: `route()` takes no positional arguments. There is no router
  selector — all markers belong to the class's single router.
- **Payload keys**: `name` becomes `entry_name` (explicit logical name),
  `endpoint_id` enables reverse lookup via `node("@endpoint_id")` and
  `get_url()`. Any extra `**kwargs` (e.g. `auth="admin"`,
  `logging_before=False`) are copied verbatim into the payload and later
  dispatched to plugins.
- **One per function**: a function carries a single `@route` marker. Applying
  a second `@route` to the same function raises `TypeError` at decoration time.

---

## 4. RouterInterface — the minimal contract

**File**: `core/router_interface.py` (83 lines)

An ABC with only two abstract methods:

| Method | Purpose |
|--------|---------|
| `node(path, **kwargs)` | Resolve a path → callable RouterNode |
| `nodes(basepath, lazy, pattern, forbidden, **kwargs)` | Introspection: return the tree of entries and child routers |

Any object implementing this interface can be used where a router is expected
(duck typing). This lets external packages such as `genro-asgi` create
router-compatible objects without depending on the BaseRouter implementation.

---

## 5. BaseRouter — the routing core

**File**: `core/base_router.py` (~1050 lines)

This is the most important class. It implements all the routing with no plugin
logic. Router (the subclass) only adds plugins and middleware.

### 5.1 Constructor and slots

```python
BaseRouter(owner, *, prefix=None, description=None,
           default_entry="index", get_default_handler=None,
           get_kwargs=None)
```

- `owner` is required and must be a `RoutingClass` instance (`ValueError` if
  `None`, `TypeError` otherwise). Routers are bound to that instance and never
  re-bound.
- The router's `name` is always `"route"` — users never pass a name.
- At the end of `__init__` the router registers itself unconditionally:
  `self.instance._register_router(self)`. RoutingClass stores it in a single
  private slot (see chapter 7).

```python
__slots__ = (
    "instance",        # owner: the RoutingClass instance
    "name",            # always "route"
    "prefix",          # prefix to strip (e.g. "handle_")
    "description",     # human-readable description
    "default_entry",   # fallback entry (default: "index")
    "__entries_raw",   # dict name → MethodEntry (⚠️ name-mangled!)
    "_children",       # dict alias → child router
    "_get_defaults",   # default kwargs (get_kwargs / get_default_handler)
    "_bound",          # lazy-binding flag
)
```

**⚠️ Pattern: name mangling of `__entries_raw`**

The attribute `__entries_raw` (double underscore) undergoes Python name
mangling → it becomes `_BaseRouter__entries_raw`. This is intentional: it
protects the attribute from accidental access by consumers. In `router.py` you
will see the explicit access:

```python
for entry in self._BaseRouter__entries_raw.values():
```

This is needed when Router wants the raw data without triggering lazy binding
(the `_entries` property would call `_bind()`).

### 5.2 Lazy binding

```python
@property
def _entries(self):
    if not self._bound:
        self._bind()
    return self.__entries_raw
```

The first time anything accesses `_entries` (via `node()`, `nodes()`, or any
operation), `_bind()` runs and executes `add_entry("*")`.

`add_entry("*")` triggers `_register_marked()`, which iterates
`_iter_marked_methods()`:

1. Walks the MRO of `type(self.instance)` (derived classes first, so overrides
   win)
2. For each class in the MRO, scans `__dict__` looking for plain functions
   carrying the `_route_decorator_kw` attribute
3. Deduplicates by method name (MRO priority) and by function identity
   (so a class-level alias of the same function is not registered twice)
4. Registers each function's marker as an entry — **every marker belongs to this router**
   (one router per class, so there is no per-router filtering)

**Why lazy**: the order of router and plugin setup inside `__init__` does not
matter. Plugins can be added after the router is created. Everything resolves
at first use.

### 5.3 Handler registration

`add_entry(target)` accepts:

| Target type | Behavior |
|-------------|----------|
| `"*"`, `"_all_"`, `"__all__"` | Discover all `@route` markers (wildcard) |
| Comma string `"a,b,c"` | Register each name as a separate entry |
| Plain string `"my_method"` | Look up `getattr(self.instance, "my_method")` |
| Callable | Register directly as entry |
| List/tuple/set | Iterate and register each one |

**Plugin option dispatch**: keywords containing `_` are analyzed. If the part
before the underscore is a known plugin name, the keyword is grouped as a
plugin option. E.g.:

```python
@route(auth_rule="admin", logging_before=False, meta_category="users")
```

becomes:

- `plugin_options = {"auth": {"rule": "admin"}, "logging": {"before": False}}`
- `core_options = {"meta": {"category": "users"}}`

**Shorthand**: if the keyword **without underscore** is a known plugin name and
that plugin declares `plugin_default_param`, it is mapped to the default
parameter. So `auth="admin"` is equivalent to `auth_rule="admin"`.

### 5.4 Path resolution — `_find_candidate_node(path)`

This is the routing algorithm. No regex, no URL patterns.

```text
Path: "orders/create"
         │       │
         │       └─ looked up in the child router's entries
         └─ looked up in the current router's _children
```

Step by step:

1. Empty path → return the router's `default_entry`
2. Split on `/` → `["orders", "create"]`
3. For each segment:
   - If it is an entry → **found**; remaining segments become `partial`
   - If it is a child router → **navigate** into the child and continue
4. If navigation runs out without an entry → use the `default_entry` of the
   last router reached, with the leftover segments as `partial`

The `partial` segments are then mapped to the handler's positional parameters
via signature inspection (see RouterNode).

**Example**:

```text
router.node("users/get_user/42")
→ navigates into child "users"
→ finds entry "get_user"
→ partial = ["42"]
→ "42" is mapped to the first positional parameter of get_user()
```

**Reverse lookup**: a path starting with `@` is an `endpoint_id` lookup:
`node("@invoice.detail")` searches the whole tree recursively for the entry
registered with `@route(endpoint_id="invoice.detail")`. The companion
`get_url(path_or_id, **kwargs)` builds a URL path, appending keyword values
that match the handler's positional parameters as path segments.

### 5.5 Children — `include()` and `detach_instance()`

`include(source, name=...)` links a source into this router:

- **Router source**: `self._children[alias] = source`. If the source's owner
  has no parent yet, this is the **primary** attachment: plugin inheritance is
  triggered via `_on_attached_to_parent` and `_routing_parent` is set on the
  owner. Subsequent includes of the same router are **secondary** links —
  navigational shortcuts only, no inheritance, no parent change.
- **RouterNode source**: creates an **entry alias** — the same `MethodEntry`
  is stored under a new name in this router. No copy; `_rebuild_handlers`
  skips aliases (`entry.router is not self`) so the wrapping pipeline of the
  source router stays in charge.

`detach_instance(child)` removes every alias whose router belongs to the child
instance and clears `child._routing_parent`.

User code normally calls neither directly: `RoutingClass.add_branches`
(chapter 7) sets the parent relation and delegates the link to `include()`.

### 5.6 Introspection — `nodes()`

`nodes()` builds a nested dict of the whole routing tree. It supports:

| Parameter | Effect |
|-----------|--------|
| `basepath="child/grand"` | Navigate and return only that subtree |
| `lazy=True` | Child routers stay as references (not expanded) |
| `pattern="^get_"` | Filter entries by regex |
| `forbidden=True` | Include blocked entries with the reason |
| `**kwargs` | Plugin filters (e.g. `auth_tags="admin"`) |

The tree is dialect-neutral: OpenAPI/MCP output is produced by a transport adapter (e.g. `genro-asgi`) that reads `nodes()`, not by a `mode` parameter.

### 5.7 Hooks for subclasses

BaseRouter defines four no-op hooks that Router overrides:

| Hook | When it runs | What Router does |
|------|--------------|------------------|
| `_wrap_handler(entry, call_next)` | Handler rebuild | Builds the middleware pipeline |
| `_after_entry_registered(entry)` | After registration | Applies plugin config and `on_decore` |
| `_on_attached_to_parent(parent)` | Primary `include()` of this router into a parent | Inherits the parent's plugins |
| `_describe_entry_extra(entry, desc)` | During nodes() | Adds plugin info for introspection |

---

## 6. RouterNode — the callable wrapper

**File**: `core/router_node.py` (240 lines)

RouterNode is what `router.node("path")` returns. It is a **callable** object:
`node()()` invokes the handler.

### What it does

1. Receives the router, the entry name (optional) and the `partial` segments
2. Resolves the entry (from the name or from `default_entry`)
3. Maps the partial segments to the handler's positional parameters
   (`_assign_partial`)
4. When called (`__call__`), merges the partials with the explicit args and
   invokes `entry.handler`

It also exposes `endpoint_id`, `doc` and `metadata` (the `meta_*` kwargs from
the decorator) as read-only properties.

### Path → parameter mapping (`_assign_partial`)

```python
# Handler: def get_user(self, user_id, detail=None): ...
# Path:    "get_user/42/full"
# partial: ["42", "full"]
# → partial_kwargs = {"user_id": "42", "detail": "full"}
```

Inspection happens via `inspect.signature`. If there are extra segments and the
function has no `*args`, the node is invalid (NotFound).

### Precedence: path > kwargs

```python
filtered_kwargs = {k: v for k, v in kwargs.items() if k not in self._partial_kwargs}
```

Values extracted from the path **win** over those passed as keywords.

### Exception mapping

Every RouterNode has an `_exceptions` dict mapping error codes to exception
classes. The caller can customize them:

```python
node = router.node("action", errors={"not_found": MyHTTPNotFound})
```

If the node has `error` set, `__call__` raises the mapped exception.

---

## 7. RoutingClass — the mixin that ties everything together

**File**: `core/routing.py` (518 lines)

This mixin is the bridge between user classes and routers. Users of
genro-routes inherit from `RoutingClass`.

### 7.1 `__slots__` — state isolation

```python
__slots__ = (
    "__routing_proxy__",
    "__genro_routes_router__",
    "_routing_parent",
    "_ctx",
    "_capabilities",
)
```

All framework attributes live in dedicated slots → they do not pollute the
user's namespace. `__routing_proxy__` and `__genro_routes_router__` have
deliberately long names to avoid collisions.

### 7.2 The `route` property — one router per instance

```python
@property
def route(self) -> Router:
    router = getattr(self, _ROUTER_ATTR_NAME, None)
    if router is None:
        router = Router(self)
    return router
```

The router is created **lazily on first access** and stored in the
`__genro_routes_router__` slot by `_register_router` (called by the Router
constructor). Users never call `Router(...)` directly; configuration happens on
the existing router in `__init__` (binding is lazy, so this is race-free):

```python
class MyService(RoutingClass):
    def __init__(self):
        self.route.description = "My service API"
        self.route.plug("logging")
```

`_register_router` raises `ValueError` if a *different* router is already
registered — the one-router invariant is enforced, not just conventional.
A class with no plugins and no options needs no `__init__` at all.

### 7.3 `__setattr__` — child auto-detach ⚠️

```python
def __setattr__(self, name, value):
    current = self._get_current_routing_attr(name)
    if current is not None:
        self._auto_detach_child(current)
    object.__setattr__(self, name, value)
```

**This is an important side effect**: every attribute assignment on a
RoutingClass goes through this logic. If the previous attribute value was a
child RoutingClass bound to this parent, it is automatically detached from the
router.

**Why**: it avoids memory leaks and orphan routers. If you do
`parent.child = OtherChild()`, the old child is removed from the hierarchy
without a manual `detach_instance()` call.

**Note**: `object.__setattr__` is used in several places to *bypass* this
custom `__setattr__` when the auto-detach check is not wanted (e.g. during
internal initialization).

### 7.4 `add_branches` — hierarchical composition

`add_branches` is a method of **RoutingClass** (not of Router or the proxy).
Each branch is a dict; two forms are accepted:

```python
# instance form — eager: attach an already-built child instance now
self.add_branches({"name": "sales", "instance": child})

# factory form — lazy: the child is created on first access
self.add_branches({"name": "sales", "cls": SalesService, "params": {...}})
```

The instance form sets `child._routing_parent = self` and delegates the routing
link to `self.route.include(child.route, name=name)` (which triggers plugin
inheritance on the primary attachment). The factory form records the class and
params and materializes the child lazily on first resolution of the branch.

`detach_instance` stays on **Router**.

### 7.5 `Section` — the grouping node

```python
svc.add_branches({"name": "admin", "instance": Section("Admin area")})
```

`Section` is a minimal concrete RoutingClass carrying an empty router. It is
used to build intermediate levels of a routing tree (namespacing, dynamically
discovered hierarchies) without defining a dedicated class. Multiple surfaces
(api + admin) are separate RoutingClass instances composed by attach.

### 7.6 `routing` property — the proxy

```python
@property
def routing(self):
    proxy = getattr(self, "__routing_proxy__", None)
    if proxy is None:
        proxy = _RoutingProxy(self)
        setattr(self, "__routing_proxy__", proxy)
    return proxy
```

Returns a cached `_RoutingProxy`. The proxy groups the management operations
without polluting the class namespace:

| Proxy method | Purpose |
|--------------|---------|
| `configure(target, **opts)` | Plugin configuration via target syntax |
| `configure("?")` | Introspection: returns the router description dict |
| `add_branches(*branches)` | Delegates to the owner's `add_branches` |

For navigation and introspection use the router directly: `route.node(path)`
resolves (and executes) a path, `route.nodes(basepath=...)` inspects and opens
a subtree.

**Target syntax for configure**: `"plugin/selector"`

```python
svc.routing.configure("logging/_all_", before=False)
svc.routing.configure("auth/admin_*", rule="admin")
```

The selector supports glob patterns (`fnmatchcase`). Child routers belong to
child instances: configure them through the child's own `routing` proxy.

### 7.7 `ctx` property — slot + parent chain

The context (`RoutingContext`) is set by the adapter (ASGI, etc.) in the
instance's `_ctx` slot. Reading walks up the `_routing_parent` chain:

```python
@property
def ctx(self):
    result = getattr(self, "_ctx", None)
    if result is not None:
        return result
    parent = getattr(self, "_routing_parent", None)
    if parent is not None:
        return parent.ctx
    return None

@ctx.setter
def ctx(self, value):
    object.__setattr__(self, "_ctx", value)
```

Layering (server → app → request) is handled by the parent chain inside
`RoutingContext`, not by `RoutingClass`:

```python
server_ctx = RoutingContext()
server_ctx.server = server

app_ctx = RoutingContext(parent=server_ctx)
app_ctx.app = app

ctx = RoutingContext(parent=app_ctx)
ctx.db = db_connection

svc.ctx = ctx
# svc.ctx.db → local
# svc.ctx.server → walks the parent chain up to server_ctx
```

Concurrency isolation (ContextVar for async, threading.local for threads) is
the adapter's responsibility, not genro-routes'.

---

## 8. Router — BaseRouter + plugin pipeline

**File**: `core/router.py` (551 lines)

Router extends BaseRouter adding:

- The global plugin registry (`_PLUGIN_REGISTRY` — the only global state)
- Per-router plugin instances
- The middleware pipeline
- Plugin inheritance across hierarchies

### 8.1 Global registry

```python
_PLUGIN_REGISTRY: dict[str, type[BasePlugin]] = {}
```

Plugins self-register at the end of their module:

```python
# In auth.py, last line:
Router.register_plugin(AuthPlugin)
```

The import happens in `__init__.py`:

```python
for _plugin in ("logging", "pydantic", "auth", "env", "channel"):
    import_module(f"{__name__}.plugins.{_plugin}")
```

### 8.2 `plug(name)` — attaching a plugin

```python
self.route.plug("logging").plug("auth")
```

`plug()` is chainable (returns `self`). It creates a `_PluginSpec`,
instantiates the plugin, and adds it to the internal structures. If the router
is already bound, `on_decore` is applied to existing entries and handlers are
rebuilt.

### 8.3 `__getattr__` — fluent plugin access

```python
def __getattr__(self, name):
    plugin = self._plugins_by_name.get(name)
    if plugin is None:
        raise AttributeError(...)
    return plugin
```

This enables `router.logging.configure(before=False)`. Plugins become virtual
attributes of the router.

### 8.4 Middleware pipeline — `_wrap_handler`

```python
wrapped = call_next  # = entry.func (the original method)
for plugin in reversed(self._plugins):
    plugin_call = plugin.wrap_handler(self, entry, wrapped)
    wrapped = self._create_wrapper(plugin, entry, plugin_call, wrapped)
```

The last attached plugin is closest to the real handler. The first one is the
outermost (first to run, last to complete).

**The wrapper checks `is_plugin_enabled` at runtime**: if the plugin is
disabled, it jumps straight to `next_handler`. This allows enabling/disabling
plugins **without rebuilding the chain**.

### 8.5 Plugin inheritance — `_on_attached_to_parent`

When a child router is attached to a parent (primary `include()`):

1. For each parent plugin **not present** in the child: a new instance of the
   same plugin class is created and added to the child
2. For each parent plugin **already present** in the child:
   `on_attached_to_parent(parent_plugin)` is called so the plugin decides how
   to handle inheritance

The `_inherited_from` set (parent ids) prevents inheriting twice from the same
parent; `_plugin_children` tracks children for configuration-change
propagation.

### 8.6 `is_plugin_enabled` — 5-level cascade

Resolution order (first match wins):

1. **entry locals** (runtime override via `set_plugin_enabled`)
2. **entry config** (static via `configure(_target=handler)`)
3. **global locals** (runtime override via `set_plugin_enabled("_all_")`)
4. **global config** (static via `configure()`)
5. **default**: `True`

The config/locals separation allows runtime overrides that can be set and
removed independently of the static configuration.

---

## 9. The plugin system

**File**: `plugins/_base_plugin.py` (381 lines)

### 9.1 `MethodEntry` — the record of a handler

```python
@dataclass
class MethodEntry:
    name: str                        # logical name (e.g. "list_orders")
    func: Callable                   # the original bound method
    router: Any                      # the router that owns it
    plugins: list[str]               # names of the applied plugins
    metadata: dict[str, Any]         # metadata (plugin_config, meta_*, ...)
    handler: Callable = None         # the method after middleware wrapping
    endpoint_id: str | None = None   # optional globally unique id (reverse lookup)
```

`handler` starts equal to `func` and is replaced by the middleware pipeline
when plugins build the wrappers.

The `signature` property returns `inspect.signature(func)`, computed on first
access and cached. `RouterNode.__call__` binds the call arguments against it
before invoking the handler, so a call that does not fit the signature raises
the `signature_error` class instead of reaching the handler; `_assign_partial`
reads it to place the unresolved path segments.

### 9.2 `BasePlugin` — the contract

Every plugin inherits from `BasePlugin` and defines:

**Class attributes (required)**:

```python
plugin_code = "auth"            # unique identifier
plugin_description = "..."      # human-readable description
plugin_default_param = "rule"   # shorthand parameter (optional)
```

**Overridable hooks**:

| Hook | When | Purpose |
|------|------|---------|
| `configure(*, _target, flags, ...)` | Configuration | Declare the schema via the signature |
| `on_decore(router, func, entry)` | Handler registration | Analyze/transform the entry |
| `wrap_handler(router, entry, call_next)` | Middleware build | Return a wrapper callable |
| `deny_reason(entry, **filters)` | `node()` / `nodes()` | Decide accessibility |
| `entry_metadata(router, entry)` | `nodes()` | Provide introspection metadata |
| `on_attached_to_parent(parent_plugin)` | Primary attachment | Handle config inheritance |
| `on_parent_config_changed(old, new)` | Parent changes config | Decide whether to follow or ignore |

### 9.3 `__init_subclass__` — the most elegant pattern ⚠️

```python
class BasePlugin:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "configure" in cls.__dict__:
            cls.configure = _wrap_configure(cls.__dict__["configure"])
```

Every subclass defining `configure()` gets it automatically wrapped by
`_wrap_configure`. The plugin author writes:

```python
class AuthPlugin(BasePlugin):
    def configure(self, *, rule: str = "", enabled: bool = True,
                  _target: str = "_all_", flags: str | None = None):
        pass  # The body is literally empty!
```

The **parameter signature IS the configuration schema**.

`_wrap_configure` adds:

1. Pydantic validation via `@validate_call` on the original signature
2. `flags` parsing (string `"enabled,before:off"` → boolean dict)
3. Multiple-target handling (comma-separated)
4. Automatic persistence in the router's `_plugin_info` store

**Why**: it removes all configuration boilerplate. Zero validation code, zero
persistence code, zero serialization code. The plugin developer only declares
the accepted parameters and their types.

### 9.4 Configuration store

Configuration lives in `Router._plugin_info`:

```python
{
    "auth": {
        "_all_": {"config": {"rule": "user"}, "locals": {}},
        "admin_action": {"config": {"rule": "admin"}, "locals": {}}
    }
}
```

`_all_` is the global configuration; specific entries override it.

### 9.5 Inheritance: "follow if aligned, ignore if customized"

When the parent changes its config, `_notify_children` calls
`on_parent_config_changed(old, new)` on each child. The default implementation:

- If the child's config was **equal** to the parent's old config → update to
  the new one
- If the child's config had been **customized** → ignore

This avoids both the rigidity of forced inheritance and the chaos of total
independence. The cascade propagates recursively to grandchildren but stops
where the config was customized.

---

## 10. The built-in plugins

### 10.1 LoggingPlugin (`plugins/logging.py`, 175 lines)

**Hook**: `wrap_handler`

Adds logging with timing to every handler call. The `logged` closure reads the
configuration **at runtime** (not at wrap time), allowing dynamic toggles.

```python
configure(enabled=True, before=True, after=True, log=True, print=False)
```

Note: `print` as a parameter name shadows the builtin — intentional (it carries
the `# noqa: A002` comment).

### 10.2 PydanticPlugin (`plugins/pydantic.py`, 247 lines)

**Hooks**: `on_decore`, `wrap_handler`, `entry_metadata`

**In `on_decore`** (at registration):

- Inspects signature and type hints
- Creates a dynamic Pydantic model for the input parameters
- Generates the JSON response schema from the return type hint

**In `wrap_handler`** (at runtime):

- Validates only the parameters with a type hint (untyped ones pass through)
- The `disabled` check happens on every call, not at wrap time

Note: the configuration parameter is `disabled` (negative), not `enabled`.
Different from the other plugins.

### 10.3 AuthPlugin (`plugins/auth.py`, 170 lines)

**Hook**: `deny_reason`

Implements RBAC with expressive rules:

| Rule | Meaning |
|------|---------|
| `"admin"` | Requires the "admin" tag |
| `"admin\|manager"` | OR: either one suffices |
| `"admin&internal"` | AND: both required |
| `"!guest"` | NOT: must not have "guest" |
| `"(admin\|manager)&!guest"` | Combination |

**401 vs 403 distinction**:

- Entry with a rule but no tags provided → `"not_authenticated"` (401)
- Tags provided but not matching → `"not_authorized"` (403)

**Recursion over RouterInterface**: if the entry is a router (not a handler),
it iterates over all its entries and children. If at least one is accessible,
the router is visible.

### 10.4 EnvPlugin + CapabilitiesSet (`plugins/env.py`, 307 lines)

**Hook**: `deny_reason`

Filters entries based on the **capabilities** available in the system. Unlike
AuthPlugin (who you are), EnvPlugin is about **what is available** (is Redis
up? is Stripe configured?).

**`CapabilitiesSet`** is the base class for defining dynamic capabilities:

```python
class MyCapabilities(CapabilitiesSet):
    @capability
    def redis(self):
        return self._redis_client.ping()

    @capability
    def stripe(self):
        return self._stripe_key is not None
```

The pattern is surprising: `CapabilitiesSet.__iter__` uses `dir(self)` to
discover the methods marked with `@capability`, **calls** them at runtime, and
yields the name only if they return `True`. There is no cache: every iteration
re-evaluates the capabilities.

**Accumulation in the hierarchy**: capabilities add up walking the
`_routing_parent` chain (`BaseRouter.current_capabilities`). Parent has
"redis", child has "pyjwt" → an entry requiring `redis&pyjwt` is satisfied.

### 10.5 ChannelPlugin (`plugins/channel.py`, 143 lines)

**Hook**: `deny_reason`

Filters entries based on the **transport channel** (mcp, rest, bot_*, etc.).

```python
@route(channel="mcp,bot_.*")
def mcp_and_bots_only(self): ...
```

- **Default closed**: without channel configuration, the entry is not
  available on any channel
- Patterns are **full-match regexes** (`re.fullmatch`)
- `"*"` is a special case (wildcard, everything open)

### 10.6 OpenAPI (out of core)

OpenAPI translation is not part of genro-routes. The routing core only exposes
the dialect-neutral introspection tree: each entry carries a `result` block
(`{schema, media_type}`) and a `params` block, built by the PydanticPlugin from
return-type and parameter annotations. A transport adapter such as `genro-asgi`
reads that tree and produces the OpenAPI (or MCP) document — HTTP-method
inference, tags, security and request/response schemas all live there, not here.

---

## 11. Exceptions

**File**: `exceptions.py` (73 lines)

Four exceptions, all with a `selector: str` attribute:

| Exception | Typical HTTP code | When |
|-----------|-------------------|------|
| `NotFound` | 404 | Path not resolved |
| `NotAuthenticated` | 401 | Credentials required, not provided |
| `NotAuthorized` | 403 | Credentials provided but insufficient |
| `NotAvailable` | 501 | Missing capability or unsupported channel |

The `selector` has the format `"router_name:path"` (e.g. `"route:admin/create"`).

---

## 12. Non-standard patterns — reasoned recap

### 12.1 `@route` as pure marker

| Mainstream pattern | genro-routes pattern | Reason |
|--------------------|----------------------|--------|
| `@app.route("/path")` mutates the global router | `@route()` annotates the function | Instance-scoped router, no singleton |

### 12.2 Lazy binding

| Mainstream pattern | genro-routes pattern | Reason |
|--------------------|----------------------|--------|
| Explicit registration or at decorator time | Binding at first `_entries` access | Setup order irrelevant |

### 12.3 `__init_subclass__` on BasePlugin

| Mainstream pattern | genro-routes pattern | Reason |
|--------------------|----------------------|--------|
| Metaclass or ABC with concrete methods | `__init_subclass__` wraps `configure` | The signature = the schema, zero boilerplate |

### 12.4 `__setattr__` on RoutingClass

| Mainstream pattern | genro-routes pattern | Reason |
|--------------------|----------------------|--------|
| Explicit `detach()` call | Auto-detach on attribute reassignment | Implicit GC of the hierarchy |

### 12.5 `__getattr__` on Router

| Mainstream pattern | genro-routes pattern | Reason |
|--------------------|----------------------|--------|
| `router.get_plugin("logging")` | `router.logging` | Natural syntax |

### 12.6 `object.__setattr__` scattered through the code

Used to **bypass** RoutingClass's custom `__setattr__` when setting internal
attributes that must not trigger the auto-detach. You will see it in:
`add_branches`, `_register_router`, the `ctx` and `capabilities` setters,
`_RoutingProxy.__init__`, and in `base_router.py` inside `_include_router` and
`detach_instance` (which set/clear `_routing_parent` on the owner).

### 12.7 `safe_is_instance` with a string

```python
safe_is_instance(obj, "genro_routes.core.routing.RoutingClass")
```

Type check via the full class name instead of a direct import. It breaks the
circular dependencies between `routing.py`, `base_router.py` and `router.py`,
which reference each other.

### 12.8 Intentional name mangling (`__entries_raw`)

Double-underscore attribute for protection. Router explicitly accesses
`self._BaseRouter__entries_raw` when it needs raw access without triggering
lazy binding.

### 12.9 Operations-first, not REST

| REST | genro-routes | Reason |
|------|--------------|--------|
| `GET /users/{id}` | `node("get_user/42")()` | The protocol is a transport detail |
| Explicit HTTP verb | HTTP method inferred from the signature | Transport-agnostic |

### 12.10 CapabilitiesSet with `dir()` and dynamic calls

A set whose content is computed on every iteration by calling the methods
marked `@capability`. There is no equivalent in web frameworks. The pattern
allows capabilities that change at runtime (Redis goes down, a feature gets
activated, etc.).

---

## 13. The CLI adapter

**Package**: `cli/` (4 files, ~375 lines)

The CLI adapter is a **transport adapter** like genro-asgi, but for the command
line. Given a RoutingClass, it automatically generates a complete click
interface with help and typed parameters.

```python
from genro_routes.cli import RoutingCli

cli = RoutingCli(MyService)
cli.run()
```

### Internal architecture

The flow is: **RoutingCli → CliBuilder → click.Group tree**.

1. `RoutingCli` (`__init__.py`) — accepts a class or an instance. If it
   receives a class it instantiates it with no arguments. Delegates to
   `CliBuilder`.

2. `CliBuilder` (`_builder.py`) — calls `instance.route.nodes()` and walks the
   output recursively:
   - Entries become commands on the root group
   - Child routers (`routers` in nodes) become nested sub-groups
   - Names are converted from `snake_case` to `kebab-case` (CLI convention)

3. `ParamConverter` (`_type_map.py`) — maps `inspect.Parameter` + type hints
   to click parameters:
   - No default → `click.Argument` (positional)
   - With default → `click.Option` (`--name`)
   - `bool` → flag (`--verbose/--no-verbose`)
   - `Literal` / `Enum` → `click.Choice`
   - `list[X]` → `multiple=True`
   - `dict` / complex types → JSON string

4. `OutputFormatter` (`_formatters.py`) — formats the handler's return value:
   `auto` (plain str, JSON for dict/list), `json`, `table` (rich if available),
   `raw`.

### CLI design choices

- **Not a plugin**: the CLI does not add behavior to handlers, it invokes
  them. It is an external adapter like genro-asgi.
- **click as optional dependency**: `pip install genro-routes[cli]`. Importing
  `genro_routes.cli` fails with `ImportError` if click is not installed.
- **Enum roundtrip**: click `Choice` returns strings. The callback converts
  strings back into Enum members before invoking the handler.
- **Async handlers**: detected with `inspect.iscoroutinefunction`, invoked
  with `asyncio.run()`.

---

## 14. Quick glossary

| Term | Meaning |
|------|---------|
| **Entry** | A handler registered with a logical name in a router |
| **Router** | Container of entries and child routers, bound to an instance |
| **RouterNode** | Callable wrapper returned by `node()` |
| **RoutingClass** | Mixin for classes exposing a router (`route` property) |
| **Section** | Empty RoutingClass used as a grouping node |
| **Plugin** | Component adding behavior (logging, auth, ...) |
| **Marker** | `_route_decorator_kw` attribute on a decorated function |
| **Lazy binding** | The router discovers markers at first use |
| **Partial** | Unresolved path segments, passed as arguments |
| **endpoint_id** | Globally unique entry id for reverse lookup (`node("@id")`, `get_url`) |
| **Plugin store** | `Router._plugin_info` — per-plugin per-entry configuration |
| **CapabilitiesSet** | Set of dynamic feature flags evaluated at runtime |
| **Transport adapter** | External package mapping a protocol to `node()` |
| **RoutingCli** | Built-in CLI adapter generating click commands from `nodes()` |

---

**Recommended source reading order**:

1. `core/decorators.py` — 78 lines, the conceptual entry point
2. `core/router_interface.py` — 83 lines, the contract
3. `plugins/_base_plugin.py` — 381 lines, MethodEntry and BasePlugin
4. `core/base_router.py` — ~1050 lines, the core (read the first 500)
5. `core/router_node.py` — 240 lines, how a handler gets invoked
6. `core/routing.py` — 518 lines, the mixin, Section and the proxy
7. `core/router.py` — 551 lines, the plugin pipeline
8. The plugins, in any order
9. `cli/__init__.py` → `cli/_builder.py` — the CLI adapter

**For the tests**, start from:

- `test_router_basic.py` — basic usage
- `test_node_resolution.py` — how path resolution works
- `test_auth_plugin.py` — the authorization system
- `test_env_plugin.py` — dynamic capabilities
- `test_cli.py` — the CLI adapter
