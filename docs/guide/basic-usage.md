# Basic Usage

This guide covers Genro Routes' core features with practical examples derived from the test suite.

## Overview

Genro Routes provides instance-scoped routing with hierarchical organization and plugin support. Each router instance is independent with its own plugin state.

**Key concepts**:

- Every `RoutingClass` owns exactly one router, auto-created lazily and exposed as `self.route`
- Methods are marked with the `@route()` decorator (keyword-only options)
- Each instance gets isolated routing state
- Plugins apply per-instance, not globally

## Creating Your First Router

Create a service with instance-scoped routing:

```python
from genro_routes import RoutingClass, route

class Service(RoutingClass):
    def __init__(self, label: str):
        self.label = label

    @route()
    def describe(self):
        return f"service:{self.label}"

# Each instance is isolated
first = Service("alpha")
second = Service("beta")

assert first.route.node("describe")() == "service:alpha"
assert second.route.node("describe")() == "service:beta"
```

**Key points**:

- The router is created automatically on first access of `self.route` — never call `Router(...)` yourself
- `@route()` marks a method for registration
- `RoutingClass` is **required** - only its instances can own a router
- Each instance has independent routing state

## Registering Handlers

<!-- test: test_router_edge_cases.py::test_router_auto_registers_marked_methods_and_validates_plugins -->

Methods are automatically registered when decorated with `@route`:

```python
class API(RoutingClass):
    @route()
    def echo(self, value: str):
        return value

    @route(name="alt_name")
    def action(self):
        return "executed"

api = API()

# Direct name resolution
assert api.route.node("echo")("hello") == "hello"

# Custom name resolution
assert api.route.node("alt_name")() == "executed"
```

**Registration happens automatically**: the router discovers marked methods lazily on first use — no explicit bind call, and no `__init__` needed unless you configure plugins or router options.

<!-- test: test_router_basic.py::TestSingleRouteMarker::test_second_route_raises_at_decoration -->

**One `@route` per method**: a function carries a single marker. Applying the decorator twice to the same function raises `TypeError` at decoration time:

```python
class API(RoutingClass):
    @route(name="second")
    @route(name="first")      # TypeError: @route applied twice to 'action'
    def action(self):
        return "executed"
```

To expose one handler under a second name, alias it with `include()`.

## One Class, One Router

<!-- test: test_router_basic.py::TestSingleRouterDefault::test_route_without_args_uses_single_router -->

`@route()` always registers the method on the class's single router:

```python
class Table(RoutingClass):
    @route()
    def add(self, data):
        return f"added:{data}"

    @route()
    def remove(self, id):
        return f"removed:{id}"

t = Table()
assert t.route.node("add")("x") == "added:x"
assert t.route.node("remove")(1) == "removed:1"
```

Router options (`description`, `prefix`, `default_entry`) are set on the existing router in `__init__` (binding is lazy, so this is race-free):

```python
class DocumentedTable(RoutingClass):
    def __init__(self):
        self.route.description = "Table operations"

    @route()
    def add(self, data):
        return f"added:{data}"
```

## Database Access via Context

Handlers access shared state (database, user, session) through `self.ctx`.
The adapter creates a `RoutingContext`, attaches what it needs, and sets it
on any `RoutingClass` instance — all instances in the same task share it
via the `_routing_parent` chain.

```python
from genro_routes import RoutingClass, RoutingContext, route

class UsersModule(RoutingClass):
    @route()
    def list_users(self):
        return self.ctx.db.execute("SELECT * FROM users")

# Adapter creates a layered context:
server_ctx = RoutingContext()
server_ctx.config = global_config

app_ctx = RoutingContext(parent=server_ctx)
app_ctx.app = app

request_ctx = RoutingContext(parent=app_ctx)
request_ctx.db = db_connection
request_ctx.user = current_user

# Set it — now every handler in this task sees it
svc = UsersModule()
svc.ctx = request_ctx

# Handler reads:
#   self.ctx.db      → local (request_ctx)
#   self.ctx.config  → walks up to server_ctx
```

**Key points**:

- `RoutingContext` has no required properties — attach any attribute freely
- `RoutingContext(parent=...)` creates layered contexts; missing attributes walk up the chain
- `svc.ctx = ctx` stores the context on the instance slot
- Child instances walk up the `_routing_parent` chain to find the context
- `svc.ctx = None` clears the local slot (children fall through to parent)

See the **[Execution Context Guide](context.md)** for the full explanation
including adapter patterns and subclassing.

### Multiple Surfaces via Composition

A class has exactly one router. When you need distinct surfaces (e.g. a public
API and an admin area), compose separate `RoutingClass` instances:

```python
class PublicAPI(RoutingClass):
    @route()
    def ping(self):
        return "pong"

class AdminAPI(RoutingClass):
    @route()
    def reset(self):
        return "reset done"

class App(RoutingClass):
    def __init__(self):
        self.api = PublicAPI()
        self.admin = AdminAPI()
        self.add_branches({"name": "api", "instance": self.api})
        self.add_branches({"name": "admin", "instance": self.admin})

app = App()
assert app.route.node("api/ping")() == "pong"
assert app.route.node("admin/reset")() == "reset done"
```

For a grouping level without a dedicated class, attach a `Section` (an empty
`RoutingClass`): `app.add_branches({"name": "admin", "instance": Section("Admin area")})`.

## Calling Handlers

<!-- test: test_router_runtime_extras.py::test_router_node_and_nodes_structure -->

Use `node()` to retrieve handlers - it returns a callable `RouterNode`:

```python
class Calculator(RoutingClass):
    @route()
    def add(self, a: int, b: int):
        return a + b

calc = Calculator()

# node() returns a RouterNode which is callable
node = calc.route.node("add")
assert node(2, 3) == 5

# RouterNode also provides metadata access
assert node.path == "add"
assert node.error is None
```

**RouterNode features**:

- Callable: invoke directly with `node(*args, **kwargs)`
- Metadata: access `node.path`, `node.metadata`, `node.doc`
- Error handling: check `node.error` before calling

## Using Prefixes and Custom Names

<!-- test: test_router_basic.py::test_prefix_and_name_override -->

Clean up method names with prefixes and provide alternative names with the `name` option:

```python
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

sub = SubService("users")

# Prefix stripped: "handle_list" → "list"
assert sub.route.node("list")() == "users:list"

# Custom name used: "handle_detail" → "detail"
assert sub.route.node("detail")(10) == "users:detail:10"
```

**Benefits**:

- Prefixes keep method names organized in code
- Explicit names provide cleaner external APIs
- Router resolves both automatically

## Checking Node Errors

Use `node.error` to check if a path resolved correctly:

```python
class Fallback(RoutingClass):
    @route()
    def known_action(self):
        return "success"

fb = Fallback()

# Existing handler - no error
node = fb.route.node("known_action")
if not node.error:
    assert node() == "success"

# Non-existing - node has error
missing = fb.route.node("missing")
if missing.error:
    print(f"Handler error: {missing.error}")
```

**RouterNode error handling**:

- `node.error` is `None` if path resolved correctly
- `node.error` contains error code string (e.g., `"not_found"`) if resolution failed
- Calling a node with error raises the appropriate exception

**Note**: `node()` always returns a `RouterNode`. If the path points to a child router without specifying an entry, best-match resolution will use the child's `default_entry` (see [Hierarchies](hierarchies.md)).

## Exceptions: NotFound, NotAuthenticated, NotAuthorized, NotAvailable

Genro Routes provides exceptions for handling routing errors:

```python
from genro_routes import NotFound, NotAuthenticated, NotAuthorized, NotAvailable

# NotFound - raised when calling node() on non-existent entry
# NotAuthenticated - raised when entry requires auth but none provided (401)
# NotAuthorized - raised when auth provided but doesn't match (403)
# NotAvailable - raised when entry exists but capabilities are missing
```

**Using `node()` with filters**:

```python
from genro_routes import RoutingClass, route, NotFound, NotAuthorized

class SecureAPI(RoutingClass):
    def __init__(self):
        self.route.plug("auth")

    @route(auth_rule="admin")
    def admin_action(self):
        return "admin only"

    @route(auth_rule="public")
    def public_action(self):
        return "public"

api = SecureAPI()

# Entry exists and tag matches - node is callable
node = api.route.node("admin_action", auth_tags="admin")
assert node() == "admin only"

# Entry exists but tag doesn't match - node has error
node = api.route.node("admin_action", auth_tags="public")
assert node.error == "not_authorized"  # Error reason
# Calling raises NotAuthorized
try:
    node()
except NotAuthorized as e:
    print(f"Access denied: {e.selector}")  # "route:admin_action"

# Entry doesn't exist - node has error
node = api.route.node("nonexistent")
# Calling raises NotFound
try:
    node()
except NotFound as e:
    print(f"Not found: {e.selector}")  # "route" (nothing resolved, path is empty)
```

**Exception attributes**:

- `selector`: The full selector in format `"router_name:path"` (e.g., `"route:admin_action"` — the router name is always `"route"`; when nothing resolves, the path part is omitted)

**RouterNode properties**:

- `node.error`: Error code string (e.g., `"not_authorized"`, `"not_available"`) or `None`
- Calling a node with error raises the appropriate exception

**Best-match resolution**:

The `node()` method uses best-match resolution - it walks the path as far as possible and passes unconsumed segments as arguments to the handler:

```python
node = router.node("unknown/path")
# If default_entry="index" accepts *args, unconsumed segments
# are passed as positional arguments when calling the handler
result = node()  # calls handler("unknown", "path")
```

## Custom Exception Mapping

Map router error codes to your framework's exception classes using the `errors` parameter in `node()`:

```python
from genro_routes import RoutingClass, route

# Define your framework's exceptions
class HTTPNotFound(Exception):
    pass

class HTTPForbidden(Exception):
    pass

class HTTPUnauthorized(Exception):
    pass

class MyAPI(RoutingClass):
    def __init__(self):
        self.route.plug("auth")

    @route(auth_rule="admin")
    def admin_only(self):
        return "secret"

api = MyAPI()

# Map error codes to custom exceptions
node = api.route.node("admin_only", auth_tags="guest", errors={
    "not_found": HTTPNotFound,
    "not_authorized": HTTPForbidden,
    "not_authenticated": HTTPUnauthorized,
})

# Calling raises your custom exception
try:
    node()
except HTTPForbidden:
    print("Access denied!")  # Your exception type
```

**Available error codes** (see `RouterNode.ERROR_CODES`):

| Code | Default Exception | HTTP Status | When |
|------|-------------------|-------------|------|
| `not_found` | `NotFound` | 404 | Path doesn't resolve |
| `not_authenticated` | `NotAuthenticated` | 401 | Auth required, none provided |
| `not_authorized` | `NotAuthorized` | 403 | Auth provided, insufficient |
| `not_available` | `NotAvailable` | 501 | Capability missing |
| `signature_error` | `TypeError` | — | The call does not fit the handler signature: unknown keyword, missing required argument, too many positionals |
| `validation_error` | `ValidationError` | 422 | The signature is satisfied and pydantic rejects the values |

A call to a node ends in exactly one of three ways:

1. the arguments do not fit the signature → `signature_error`, raised before
   the handler runs;
2. the signature is satisfied and pydantic rejects the values →
   `validation_error`;
3. the handler body raises → that exception propagates untouched, `TypeError`
   included, with no error code and no wrapping.

The default class for `signature_error` is `TypeError`, so a caller that does
not map the code keeps receiving the plain `TypeError` from the argument bind.
Mapping only `validation_error` does not cover a signature failure.

**Use cases**:

- Integrate with web frameworks (FastAPI, Starlette, Flask)
- Consistent error handling across your application
- Custom error responses with framework-specific exception types

## Catch-All Routing with `default_entry`

Routers have a `default_entry` option (default: `"index"`) that enables catch-all routing patterns via best-match resolution:

```python
class FileServer(RoutingClass):
    def __init__(self):
        # default_entry="index" is the default, but can be customized
        self.route.default_entry = "serve"

    @route()
    def serve(self, *path_segments):
        return f"Serving: {'/'.join(path_segments)}"

server = FileServer()

# node() uses best-match resolution - when path can't be fully resolved,
# unconsumed segments become arguments to the handler
node = server.route.node("docs/api/reference")
assert node() == "Serving: docs/api/reference"
```

**Key behaviors**:

- `default_entry` specifies which handler to use for unresolved paths (default: `"index"`)
- Best-match resolution walks the path as far as possible
- Unconsumed path segments are passed as arguments when calling `node()`
- If `default_entry` handler doesn't exist, `node.error` is set

**Use cases**:

- File servers with arbitrary path depth
- Catch-all handlers for dynamic routing
- Pass-through routes to external services

## Building Hierarchies

<!-- test: test_router_basic.py::test_dotted_path_and_nodes_with_attached_child -->

Create nested router structures with path-based access:

```python
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
        self.users = SubService("users")
        self.products = SubService("products")

        self.add_branches({"name": "users", "instance": self.users})
        self.add_branches({"name": "products", "instance": self.products})

root = RootAPI()

# Access with path separator
assert root.route.node("users/list")() == "users:list"
assert root.route.node("products/detail")(5) == "products:detail:5"
```

**Key points**:

- `add_branches` (instance form) is a method on `RoutingClass`, not on `Router`
- the `"name"` key links the child's router into the parent's router under that alias
- For grouping levels without handlers, attach a `Section` (see [Hierarchies Guide](hierarchies.md))

**Hierarchies enable**:

- Organized service composition
- Logical grouping of related handlers
- Namespace isolation

## Introspection

<!-- test: test_router_basic.py::test_dotted_path_and_nodes_with_attached_child -->

Inspect router structure and registered handlers:

```python
class Inspectable(RoutingClass):
    def __init__(self):
        self.child_service = SubService("child")
        self.add_branches({"name": "sub", "instance": self.child_service})

    @route()
    def action(self):
        pass

insp = Inspectable()

# Get metadata (single source: nodes)
info = insp.route.nodes()
assert "action" in info["entries"]
assert "sub" in info["routers"]

# Get nodes starting from a specific path
sub_info = insp.route.nodes(basepath="sub")
assert "list" in sub_info["entries"]

# Use lazy=True for on-demand expansion of children
lazy_info = insp.route.nodes(lazy=True)
sub_router = lazy_info["routers"]["sub"]  # Router reference, not expanded
sub_expanded = sub_router.nodes()  # Expand on demand
```

**`nodes()` parameters**:

- `basepath`: Start from a specific point in the hierarchy
- `lazy`: Return router references instead of expanding recursively
- `pattern`: Regex pattern to filter entry names (only matching entries are included)
- `forbidden`: Include blocked entries with their rejection reason (default `False`)

`nodes()` always returns the dialect-neutral introspection tree (entries, routers, plugin metadata, and the per-entry `result` / `params` blocks). It does not produce OpenAPI or any other dialect: transport adapters (e.g. genro-asgi) read this tree to build OpenAPI/MCP output.

```python
# Filter entries by name pattern
admin_entries = insp.route.nodes(pattern="admin_.*")
```

**Including blocked entries**:

Use `forbidden=True` to include entries that are blocked by plugins (e.g., due to missing capabilities or authorization). Blocked entries have a `forbidden` field with the rejection reason:

```python
# Include blocked entries for full tree introspection
entries = router.nodes(forbidden=True).get("entries", {})
# {"public": {"name": "public", ...},
#  "admin_only": {"name": "admin_only", "forbidden": "not_authorized", ...},
#  "needs_redis": {"name": "needs_redis", "forbidden": "not_available", ...}}
```

**Use `nodes()` to**:

- Generate API documentation (feed the neutral tree to a transport-layer dialect translator)
- Debug routing issues
- Validate configuration
- Build dynamic UIs that expand on demand (with `lazy=True`)
- Show full tree with blocked entries greyed out (with `forbidden=True`)

## Custom Metadata with `meta_*`

Add custom metadata to handlers using the `meta_` prefix in `@route()`:

```python
class MetadataAPI(RoutingClass):
    @route(meta_mimetype="application/json", meta_deprecated=True)
    def get_data(self):
        """Return data in JSON format."""
        return {"foo": "bar"}

    @route(meta_version="2.0", meta_auth_required=True)
    def get_data_v2(self):
        return {"foo": "bar", "extra": True}

api = MetadataAPI()

# Access metadata via node() - returns meta dict directly
node = api.route.node("get_data")
assert node.metadata["mimetype"] == "application/json"
assert node.metadata["deprecated"] is True

# Or via nodes() for all entries - uses full path
all_info = api.route.nodes()
entry_meta = all_info["entries"]["get_data_v2"]["metadata"]["meta"]
assert entry_meta["version"] == "2.0"
assert entry_meta["auth_required"] is True
```

**Key behaviors**:

- `meta_*` kwargs are stored under `metadata["meta"]` in the entry
- `node.metadata` property returns the meta dict directly (convenience)
- `nodes()` returns the full structure with `["metadata"]["meta"]` path
- The `meta_` prefix is stripped from the key name
- Separate from plugin configuration (which uses `<plugin>_<key>` format)

**Use cases**:

- API versioning information
- Deprecation markers
- Content-type hints
- Custom authorization requirements
- Any handler-specific metadata not tied to plugins

## Execution Context

See the **[Execution Context Guide](context.md)** for the complete reference.

Quick summary: `RoutingContext` is an extensible container with parent chain
delegation. Adapters create layered contexts (server → app → request), set
them via `svc.ctx = ctx` (stored in a `_ctx` slot on the instance), and handlers read
shared state with `self.ctx.db`, `self.ctx.user`, etc. Missing
attributes walk up the parent chain automatically.

## Wrapping Handler Results

Use `ResultWrapper` to return handler results with metadata that the transport layer can use (e.g., for content-type negotiation).

```python
from genro_routes import RoutingClass, route, is_result_wrapper

class APIService(RoutingClass):
    @route()
    def render_html(self):
        content = "<html><body>Hello</body></html>"
        return self.result_wrapper(content, mime_type="text/html")

svc = APIService()
result = svc.route.node("render_html")()

# Check if result is wrapped
if is_result_wrapper(result):
    print(result.value)       # "<html><body>Hello</body></html>"
    print(result.metadata)    # {"mime_type": "text/html"}
```

**Key points**:

- `self.result_wrapper(value, **metadata)` creates a `ResultWrapper` with arbitrary metadata
- `is_result_wrapper(obj)` checks if an object is a `ResultWrapper`
- The transport adapter (e.g., genro-asgi) inspects the wrapper to set response headers
- `ResultWrapper.value` contains the actual result
- `ResultWrapper.metadata` contains the metadata dict

## Next Steps

Now that you understand the basics:

- **[Plugin Guide](plugins.md)** - Extend functionality with plugins
- **[Hierarchies Guide](hierarchies.md)** - Advanced nested routing patterns
- **[Best Practices](best-practices.md)** - Production-ready patterns
- **[API Reference](../api/reference.md)** - Complete API documentation
