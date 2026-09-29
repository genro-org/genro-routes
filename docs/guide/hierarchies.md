# Hierarchical Routers

Build complex routing structures with nested routers, path-based navigation, and automatic plugin inheritance.

## Overview

Genro Routes supports hierarchical router composition where:

- **Every RoutingClass owns exactly one router**, exposed as the read-only `route` property
- **Parent instances** attach **child instances** through explicit instance binding
- **Path separator** `/` navigates the hierarchy (`root.route.node("users/list")()`)
- **Plugins propagate** from parent to children automatically
- **Each level** maintains independent handler registration
- **Parent tracking** maintains the relationship between parent and child instances
- **Automatic cleanup** when child instances are replaced

## Managing Hierarchies

Genro Routes provides explicit methods for managing RoutingClass hierarchies:

- **`add_branches({"name": ..., "instance": child})`** - Attach a RoutingClass instance (instance form of `add_branches`) to create a parent-child relationship (method on `RoutingClass`)
- **`detach_instance(child)`** - Remove a RoutingClass instance from the hierarchy (method on `Router`)
- **Parent tracking** - Children track their parent via `_routing_parent` attribute
- **Auto-detachment** - Replacing a child attribute automatically detaches the old instance

**Important**: `add_branches` (instance form) is a method on `RoutingClass` (the owner), not on `Router`. `detach_instance` remains on `Router`.

## Basic Instance Attachment

<!-- test: test_router_edge_cases.py::test_attach_and_detach_instance_single_router_with_alias -->

Attach a child instance explicitly with an alias:

```python
from genro_routes import RoutingClass, route

class Child(RoutingClass):
    @route()
    def list(self):
        return "child:list"

class Parent(RoutingClass):
    def __init__(self):
        # Attach child directly — no need to store as attribute
        self.add_branches({"name": "sales", "instance": Child()})

parent = Parent()

# Access through hierarchy
assert parent.route.node("sales/list")() == "child:list"

# node() can also resolve to a child router (falls back to its default_entry)
child_node = parent.route.node("sales")
assert child_node.path == "sales"

# Retrieve the child instance later via nodes(basepath=...)
child = parent.route.nodes(basepath="sales")["instance"]
assert child._routing_parent is parent
```

**Key points**:

- The `name` parameter provides the alias under which the child's router is linked into the parent's router
- Storing the child as an attribute is **optional** — the router tree keeps a strong reference to the child instance via `router.instance`
- `nodes(basepath="child")` returns the child subtree; its `"instance"` key is the child RoutingClass instance
- Parent tracking is handled automatically

**`node()` return values**:

- Returns a **callable RouterNode** if the path resolves to a handler
- If the path points to a child router, uses that router's `default_entry`
- Check `node.error` to see if resolution succeeded

## Multiple Surfaces: Composition

A RoutingClass owns exactly one router. When a service must expose more than one
surface (e.g. a public API and an admin area), define **one class per surface**
and compose them with `add_branches` (instance form). Grouping levels without
handlers are `Section` instances:

```python
from genro_routes import RoutingClass, Section, route

class OrdersApi(RoutingClass):
    @route()
    def get_data(self):
        return "data"

class OrdersAdmin(RoutingClass):
    @route()
    def manage(self):
        return "manage"

class Application(RoutingClass):
    def __init__(self):
        api = Section("Public API")
        admin = Section("Admin area")
        self.add_branches({"name": "api", "instance": api})
        self.add_branches({"name": "admin", "instance": admin})
        api.add_branches({"name": "orders", "instance": OrdersApi()})
        admin.add_branches({"name": "orders", "instance": OrdersAdmin()})

app = Application()

assert app.route.node("api/orders/get_data")() == "data"
assert app.route.node("admin/orders/manage")() == "manage"
```

**Composition rules**:

- One class per surface: each `RoutingClass` exposes exactly one router
- Selective exposure and renaming happen at attach time via `name=`
- The same alias can be reused under different parents (`api/orders` vs `admin/orders`)
- Handlers shared by several surfaces belong in a plain (non-routing) collaborator
  class, or can be exposed as entry aliases via `include()` (see the
  [Visual Guide](add-branches-visual-guide.md))

## Grouping with Section

Create pure organizational nodes with `Section` — an empty RoutingClass used as
a grouping level:

```python
from genro_routes import RoutingClass, Section, route

class OrganizedService(RoutingClass):
    def __init__(self):
        # Section: pure container, no handlers
        api = Section("API surface")
        self.add_branches({"name": "api", "instance": api})

        # Attach handler services as children of the section
        api.add_branches({"name": "users", "instance": UserService()})
        api.add_branches({"name": "products", "instance": ProductService()})

service = OrganizedService()

# Access through the section
service.route.node("api/users/list")()
service.route.node("api/products/create")()
```

**Section characteristics**:

- **No handlers of its own** - Just an empty router used as container
- **Optional description** - `Section("Admin area")` sets the router description
- **Useful for** - API namespacing and logical grouping without a dedicated class

**When to use Sections**:

```python
# Good: Organize related services under an /api namespace
api = Section("API")
self.add_branches({"name": "api", "instance": api})
api.add_branches({"name": "auth", "instance": self.auth})
api.add_branches({"name": "users", "instance": self.users})
# Routes: api/auth/login, api/users/list
```

**When NOT to use Sections**:

Sections add a level to your URL hierarchy. Use them only when you need pure
organizational containers:

```python
# DON'T: Section with a single child (unnecessary nesting)
api = Section()
self.add_branches({"name": "api", "instance": api})
api.add_branches({"name": "users", "instance": UserService()})
# Result: api/users/list - the "api" level adds nothing

# DO: attach children directly; root-level handlers live on the class itself
class Application(RoutingClass):
    def __init__(self):
        self.add_branches({"name": "users", "instance": UserService()})

    @route()
    def health(self):  # health - root level handler
        return "ok"
# Result: health + users/list - no empty intermediate level
```

**Decision guide**:

| Scenario | Use Section? |
| -------- | ------------ |
| Pure namespace (no root handlers) | Yes |
| Root class with handlers + children | No |
| Single child service | No (attach directly) |
| Multiple children, common namespace | Yes |

## Composing Services

Hierarchies are always built by composing RoutingClass instances — there is no
way (and no need) to create several routers on the same instance:

```python
class UsersService(RoutingClass):
    @route()
    def list_users(self):
        return ["alice", "bob"]

class OrdersService(RoutingClass):
    @route()
    def list_orders(self):
        return ["order1", "order2"]

class Service(RoutingClass):
    def __init__(self):
        self.add_branches({"name": "users", "instance": UsersService()})
        self.add_branches({"name": "orders", "instance": OrdersService()})

    @route()
    def health(self):
        return "ok"

svc = Service()

# Access through hierarchy
assert svc.route.node("users/list_users")() == ["alice", "bob"]
assert svc.route.node("orders/list_orders")() == ["order1", "order2"]
assert svc.route.node("health")() == "ok"
```

**Key characteristics**:

- **One router per instance**: every level of the tree is a RoutingClass instance
- **Plugin inheritance**: the child router inherits the parent's plugins on attach
- **Name required**: the `name=` alias is the hierarchy key
- **Collision detection**: attaching raises `ValueError` if the alias already exists

## Auto-Detachment

<!-- test: test_router_edge_cases.py::test_auto_detach_on_attribute_replacement -->

Replacing a child attribute automatically detaches the old instance:

```python
class Parent(RoutingClass):
    def __init__(self):
        self.child = Child()
        self.add_branches({"name": "child", "instance": self.child})

parent = Parent()
assert parent.child._routing_parent is parent
assert "child" in parent.route._children

# Replacing the attribute triggers auto-detach
parent.child = None

# Old child is automatically removed from hierarchy
assert "child" not in parent.route._children
```

**Auto-detachment behavior**:

- Triggered when setting `parent.attribute = new_value`
- Only detaches if old value's `_routing_parent` is this parent
- Clears `_routing_parent` on detached instance
- Removes the child's router (all aliases) from the parent router
- Best-effort: ignores errors to avoid blocking attribute assignment

**Use cases**:

```python
# Replacing a service implementation
parent.auth_service = OldAuthService()
parent.add_branches({"name": "auth", "instance": parent.auth_service})

# Later: automatic cleanup
parent.auth_service = NewAuthService()  # Old service auto-detached
parent.add_branches({"name": "auth", "instance": parent.auth_service})
```

## Parent Tracking

Every attached RoutingClass tracks its parent:

```python
class Child(RoutingClass):
    @route()
    def ping(self):
        return "pong"

    def get_parent_info(self):
        if self._routing_parent:
            return f"My parent is {type(self._routing_parent).__name__}"
        return "No parent"

child = Child()
assert getattr(child, "_routing_parent", None) is None  # Not attached

parent = Parent()
parent.child = child
parent.add_branches({"name": "child", "instance": parent.child})
assert child._routing_parent is parent  # Parent tracked

parent.route.detach_instance(child)
assert child._routing_parent is None  # Cleared on detach
```

**Parent tracking enables**:

- Context awareness in child methods
- Access to parent's state and configuration
- Proper cleanup on detachment
- Preventing duplicate attachments

## Plugin Inheritance

<!-- test: test_router_runtime_extras.py::test_inherit_plugins_branches -->

Plugins propagate automatically from parent to children:

```python
class Service(RoutingClass):
    def __init__(self, name: str):
        self.name = name

    @route()
    def process(self):
        return f"{self.name}:process"

class Application(RoutingClass):
    def __init__(self):
        # Plugin attached to the application's router
        self.route.plug("logging")
        self.service = Service("main")

app = Application()

# Attach child - plugins inherit automatically
app.add_branches({"name": "service", "instance": app.service})

# Child router has the logging plugin
assert hasattr(app.service.route, "logging")

# Plugin applies to child handlers
result = app.service.route.node("process")()
# Logging plugin was active during call
```

**Inheritance rules**:

- Parent plugins apply to all child handlers
- Children can add their own plugins
- Plugin order: parent plugins -> child plugins
- Configuration inherits but can be overridden
- Inheritance is triggered by the **primary** attachment (the first parent);
  linking the same child elsewhere with `include()` is navigational only

## Path Navigation

Navigate the hierarchy with path separator `/` directly on the router, using
`router_at_path()`:

```python
class Child(RoutingClass):
    pass

class Parent(RoutingClass):
    def __init__(self):
        self.child = Child()
        self.add_branches({"name": "child", "instance": self.child})

parent = Parent()

# Find a child router by path
child_router = parent.route.router_at_path("child")
assert child_router.instance is parent.child
```

**Navigation features**:

- `router_at_path("child/grandchild")` traverses the hierarchy
- Returns `None` if the path doesn't resolve
- Opens declared branches along the way (lazy branches materialize, aliases
  are followed) — see the [Branches Guide](branches.md)
- For handler resolution use `node(path)`; for subtree inspection use
  `nodes(basepath=path)`

## Introspection

<!-- test: test_router_basic.py::test_dotted_path_and_nodes_with_attached_child -->

Inspect the full hierarchy structure:

```python
class Inspectable(RoutingClass):
    def __init__(self):
        self.service = Service("child")
        self.add_branches({"name": "sub", "instance": self.service})

    @route()
    def action(self):
        pass

insp = Inspectable()

# Get complete hierarchy metadata
info = insp.route.nodes()
assert "action" in info["entries"]
assert "sub" in info["routers"]

# Child routers included
child_info = info["routers"]["sub"]
assert child_info["name"] == "route"

# Get nodes starting from a child
sub_only = insp.route.nodes(basepath="sub")
assert "process" in sub_only["entries"]

# Lazy mode: children are router references
lazy = insp.route.nodes(lazy=True)
sub_router = lazy["routers"]["sub"]  # Router reference, not expanded
sub_expanded = sub_router.nodes()  # Expand on demand
```

**`nodes()` parameters**:

- `basepath`: Start from a specific point in the hierarchy (e.g., `"child/grandchild"`)
- `lazy`: Return router references instead of expanding recursively
- `pattern`: Regex pattern to filter entry names
- `forbidden`: Include blocked entries with their rejection reason

`nodes()` returns the dialect-neutral introspection tree; OpenAPI/MCP schemas are produced by transport-layer translators (e.g. genro-asgi) that read this tree, not by a `nodes()` mode parameter.

**Introspection provides**:

- Complete handler list at each level
- Child router names and structure
- Plugin configuration per level
- Nested hierarchy representation
- On-demand expansion with `lazy=True`
- Neutral `result` / `params` blocks that transport-layer translators turn into OpenAPI/MCP schemas

## Catch-All Routes with `default_entry`

Routers can handle paths that don't fully resolve by delegating to a `default_entry` handler (best-match resolution):

```python
class FileService(RoutingClass):
    # default_entry="index" is the router default

    @route()
    def index(self, *path_segments):
        return f"File: {'/'.join(path_segments)}"

class Application(RoutingClass):
    def __init__(self):
        self.files = FileService()
        self.add_branches({"name": "files", "instance": self.files})

app = Application()

# Path "files/docs/readme.md" - "files" is a child router,
# "docs/readme.md" doesn't exist, so best-match resolution uses default_entry
node = app.route.node("files/docs/readme.md")
# Unconsumed segments passed as args: ["docs", "readme.md"]
assert node() == "File: docs/readme.md"
```

**Behavior by scenario** (best-match resolution):

| Path | Scenario | Result |
| ---- | -------- | ------ |
| `/` or `""` | Empty path | Uses this router's `default_entry` |
| `child/handler` | Handler exists | Returns RouterNode with handler |
| `child/unknown/path` | Child exists, path unresolved | Uses child's `default_entry` with args |
| `child` (router only) | Single segment, is router | Uses child's `default_entry` |
| `unknown/path` | Nothing found | Uses this router's `default_entry` with args |

**Root node** (empty path):

When you call `node("/")` or `node("")`, you get a **root node**:

```python
class Service(RoutingClass):
    @route()
    def index(self):
        return "home"

svc = Service()
node = svc.route.node("/")

# Root node properties
assert node.path == ""
assert node.error is None  # default_entry exists

# If default_entry exists, it's callable
assert node() == "home"
```

If no `default_entry` exists, calling raises `NotFound`.

**Custom `default_entry`**:

```python
class CustomService(RoutingClass):
    def __init__(self):
        self.route.default_entry = "catch_all"

    @route()
    def catch_all(self, *args):
        return f"Caught: {args}"
```

**Error handling**:

If `default_entry` handler doesn't exist in the target router, an empty RouterNode is returned:

```python
class EmptyService(RoutingClass):
    pass  # No "index" entry!

svc = EmptyService()
node = svc.route.node("unknown/path")
# node.error will indicate the path couldn't be resolved
```

## Real-World Examples

### Microservice-Style Organization

```python
class AuthService(RoutingClass):
    @route()
    def login(self, username: str, password: str):
        return {"token": "..."}

    @route()
    def logout(self, token: str):
        return {"status": "ok"}

class UserService(RoutingClass):
    @route()
    def list_users(self):
        return ["alice", "bob"]

    @route()
    def get_user(self, user_id: int):
        return {"id": user_id, "name": "..."}

class Application(RoutingClass):
    def __init__(self):
        # Root router with logging
        self.route.plug("logging")

        # Create and attach services
        self.add_branches({"name": "auth", "instance": AuthService()})
        self.add_branches({"name": "users", "instance": UserService()})

app = Application()

# Access through hierarchy
token = app.route.node("auth/login")("alice", "secret123")
users = app.route.node("users/list_users")()

# Logging applies to all handlers automatically
```

### Multi-Level Organization

```python
class ReportsAPI(RoutingClass):
    @route()
    def sales_report(self):
        return "sales data"

    @route()
    def inventory_report(self):
        return "inventory data"

class AdminAPI(RoutingClass):
    def __init__(self):
        self.add_branches({"name": "users", "instance": UserService()})
        self.add_branches({"name": "reports", "instance": ReportsAPI()})

class Application(RoutingClass):
    def __init__(self):
        # Public API (simplified interface)
        self.add_branches({"name": "public", "instance": UserService()})

        # Admin API (protected, more capabilities)
        self.add_branches({"name": "admin", "instance": AdminAPI()})

app = Application()

# Clean hierarchy
app.route.node("public/list_users")()           # Public access
app.route.node("admin/users/get_user")(123)     # Admin user access
app.route.node("admin/reports/sales_report")()  # Admin reports
```

### Dynamic Service Replacement

```python
class ServiceV1(RoutingClass):
    @route()
    def process(self, data: str):
        return f"v1:{data}"

class ServiceV2(RoutingClass):
    @route()
    def process(self, data: str):
        return f"v2:{data}"

class Application(RoutingClass):
    def __init__(self):
        self.service = ServiceV1()
        self.add_branches({"name": "processor", "instance": self.service})

    def upgrade_service(self):
        # Auto-detachment happens here
        self.service = ServiceV2()
        self.add_branches({"name": "processor", "instance": self.service})

app = Application()
assert app.route.node("processor/process")("test") == "v1:test"

app.upgrade_service()  # Seamless replacement
assert app.route.node("processor/process")("test") == "v2:test"
```

## Best Practices

### Logical Grouping

```python
# Compose related services under the root class
class API(RoutingClass):
    def __init__(self):
        self.add_branches({"name": "auth", "instance": AuthService()})
        self.add_branches({"name": "users", "instance": UserService()})
        self.add_branches({"name": "orders", "instance": OrderService()})
```

### Shared Plugins at Root

```python
# Apply common plugins to entire hierarchy
self.route.plug("logging").plug("pydantic")

# All children inherit both plugins
self.add_branches({"name": "auth", "instance": self.auth})
self.add_branches({"name": "users", "instance": self.users})
```

### Deep Hierarchies

```python
# Organize by domain and subdomain
app.add_branches({"name": "admin", "instance": admin})
admin.add_branches({"name": "users", "instance": user_admin})
admin.add_branches({"name": "reports", "instance": report_admin})

# Access: app.route.node("admin/users/create_user")()
#         app.route.node("admin/reports/sales_report")()
```

### Retrieve Child Instances

```python
# Retrieve attached child instances via nodes(basepath=...)
child = parent.route.nodes(basepath="child")["instance"]  # the RoutingClass instance
```

### Explicit Detachment

```python
# Explicit detachment for clarity
if should_remove_service:
    self.route.detach_instance(self.old_service)
    self.old_service = None  # Clear reference
```

### Prevent Name Collisions

```python
# Use descriptive aliases
self.add_branches({"name": "auth_v1", "instance": self.auth})
self.add_branches({"name": "auth_v2", "instance": self.new_auth})

# Access both versions
self.route.node("auth_v1/login")()
self.route.node("auth_v2/login")()
```

## Common Patterns

### Parent-Aware Children

```python
class ChildService(RoutingClass):
    @route()
    def get_config(self):
        # Access parent context
        if self._routing_parent:
            return self._routing_parent.config
        return {}
```

### Conditional Attachment

```python
class Application(RoutingClass):
    def __init__(self, config):
        # Attach based on configuration
        if config.get("enable_auth"):
            self.add_branches({"name": "auth", "instance": AuthService()})

        if config.get("enable_admin"):
            self.add_branches({"name": "admin", "instance": AdminService()})
```

### Multi-Surface Services

```python
class PublicOrders(RoutingClass):
    @route()
    def public_endpoint(self):
        return "public data"

class AdminOrders(RoutingClass):
    @route()
    def admin_endpoint(self):
        return "admin data"

# Compose: one class per surface, attached where needed
api = app.route.nodes(basepath="api")["instance"]
admin = app.route.nodes(basepath="admin")["instance"]
api.add_branches({"name": "orders", "instance": PublicOrders()})
admin.add_branches({"name": "orders", "instance": AdminOrders()})
```

## Next Steps

- **[Branches Guide](branches.md)** - Lazy/eager subtrees and aliases (declarative hierarchies)
- **[Visual Guide](add-branches-visual-guide.md)** - Mermaid diagrams for all connection scenarios
- **[Plugin Configuration](plugin-configuration.md)** - Configure plugins across hierarchies
- **[Best Practices](best-practices.md)** - Production-ready patterns
- **[API Reference](../api/reference.md)** - Complete API documentation
