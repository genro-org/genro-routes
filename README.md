# Genro Routes

<p align="center">
  <img src="assets/logo.png" alt="Genro Routes Logo" width="200"/>
</p>

[![PyPI version](https://img.shields.io/pypi/v/genro-routes?cacheSeconds=300)](https://pypi.org/project/genro-routes/)
[![Tests](https://github.com/genropy/genro-routes/actions/workflows/test.yml/badge.svg)](https://github.com/genropy/genro-routes/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/genropy/genro-routes/branch/main/graph/badge.svg)](https://codecov.io/gh/genropy/genro-routes)
[![Documentation](https://readthedocs.org/projects/genro-routes/badge/?version=latest)](https://genro-routes.readthedocs.io/en/latest/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

**Genro Routes** is a **transport-agnostic routing engine** that decouples method routing from how those methods are exposed. Define your handlers once, then expose them via HTTP, CLI, WebSocket, or any other transport layer.

The routing logic lives in your application objects - the transport adapter (like [genro-asgi](https://github.com/genropy/genro-asgi) for HTTP) simply maps external requests to router entries.

## Why Transport-Agnostic?

Traditional web frameworks tightly couple routing to HTTP. Genro Routes separates these concerns:

| Layer | Responsibility |
|-------|---------------|
| **genro-routes** | Method registration, hierarchies, plugins, introspection |
| **Transport adapter** | Protocol handling, request/response mapping |

This separation enables:

- **Same handlers, multiple transports** - Expose your API via HTTP and CLI without duplication
- **Runtime introspection** - Query available routes, generate documentation, build admin UIs
- **Testability** - Test business logic without HTTP overhead
- **Flexibility** - Swap transports without changing application code

## Use Cases

- **HTTP APIs** - Via [genro-asgi](https://github.com/genropy/genro-asgi) adapter
- **CLI tools** - Via the built-in `RoutingCli` adapter (see below)
- **Internal services** - Direct method invocation with plugin pipeline
- **Admin dashboards** - Runtime introspection for dynamic UIs

## Key Features

1. **One class, one router** - Every `RoutingClass` owns exactly one router with isolated state, auto-created and exposed as the `route` property.
2. **Friendly registration** - `@route(...)` accepts explicit names, auto-strips prefixes, and supports custom metadata.
3. **Simple hierarchies** - `add_branches({"name": "alias", "instance": child})` (method on `RoutingClass`) connects an already-built instance with path access (`parent.route.node("child/method")`).
4. **Plugin pipeline** - `BasePlugin` provides `on_decore`/`wrap_handler` hooks and plugins inherit from parents automatically.
5. **Runtime configuration** - `routing.configure()` applies global or per-handler overrides with wildcards and returns reports (`"?"`).
6. **Built-in plugins** - `logging`, `pydantic`, `auth`, `env`, and `channel` plugins are included out of the box.
7. **Response schema generation** - Return type annotations (TypedDict, dataclass, etc.) are automatically converted to JSON Schema and exposed in route metadata for bridges to consume.
8. **Full coverage** - The package ships with a comprehensive test suite and no hidden compatibility layers.

## Quick Example

```python
from genro_routes import RoutingClass, route

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
print(orders.route.node("list")())        # ["order-1", "order-2"]
print(orders.route.node("retrieve")("42"))  # acme:42

overview = orders.route.nodes()
print(overview["entries"].keys())      # dict_keys(['list', 'retrieve', 'create'])
```

## Hierarchical Routing

Build nested service structures with path access:

```python
class UsersAPI(RoutingClass):
    @route()
    def list(self):
        return ["alice", "bob"]

class Application(RoutingClass):
    def __init__(self):
        self.users = UsersAPI()

        # Attach child service (instance form: eager, linked immediately)
        self.add_branches({"name": "users", "instance": self.users})

app = Application()
print(app.route.node("users/list")())  # ["alice", "bob"]

# Introspect hierarchy
info = app.route.nodes()
print(info["routers"].keys())  # dict_keys(['users'])
```

## Branches: Lazy Subtrees and Aliases

On large trees (thousands of leaves), building every child instance at startup
is wasteful. **Branches** declare subtrees as factory specs — nothing is
constructed until actually needed:

```python
class Application(RoutingClass):
    def __init__(self):
        self.add_branches([
            {"name": "sales",  "cls": SalesAPI},                    # factory: lazy, built at first traversal
            {"name": "users",  "instance": UsersAPI()},             # instance: eager, linked immediately
            {"name": "shop",   "alias": "sales"},                   # alias: symlink to another branch
        ])

app = Application()
app.route.node("sales/report")()   # first traversal builds SalesAPI here
app.route.node("shop/report")()    # same subtree via the alias (target's plugins)
```

- Each spec is one of three mutually exclusive forms: a **factory**
  (`{"cls": ...}`) is **lazy** — built the first time a path traverses it; an
  **instance** (`{"instance": ...}`) is **eager** — already built, linked at
  the `add_branches` call; an **alias** (`{"alias": ...}`) is a symlink.
  Factory constructor errors surface at first traversal.
- `add_branches` accepts one dict, a list, or a generator — so a discovery
  function can `yield` thousands of factory specs with zero construction cost.
- **Aliases** are transparent symlinks by absolute path from the tree root:
  the whole target subtree is reachable, with the *target's* plugins.
- Introspection never builds implicitly: `nodes()` shows lazy factories and
  aliases as unresolved markers (with their class-declared `@route` leaves,
  read without instantiating). Use `nodes(_eager=True)` to expand everything
  (e.g. to generate a full OpenAPI document) or `nodes(basepath="sales")` to
  open one branch explicitly.
- `add_branches` is the single entry point: pass a class for lazy
  construction, or an instance to attach an already-built child eagerly.

See the [Branches Guide](docs/guide/branches.md) for the full lifecycle.

## Learn by Example

We provide a comprehensive gallery of examples in the [examples/](https://github.com/genropy/genro-routes/tree/main/examples) directory:

- **[Standard Faker](https://github.com/genropy/genro-routes/blob/main/examples/faker_standard.py)** - Explicit routing and Pydantic validation.
- **[Magic Faker](https://github.com/genropy/genro-routes/blob/main/examples/faker_magic.py)** - Dynamic mapping of library methods at runtime.
- **[Syntax Highlighting](https://github.com/genropy/genro-routes/blob/main/examples/pygments_highlighting.py)** - Creating a service wrapper around the Pygments library.
- **[Auth & Roles](https://github.com/genropy/genro-routes/blob/main/examples/auth_roles.py)** - Implementing role-based access control.
- **[Service Composition](https://github.com/genropy/genro-routes/blob/main/examples/service_composition.py)** - Building complex apps from independent modules.

Read our guide on **[Why wrap a library with Genro-Routes?](https://github.com/genropy/genro-routes/blob/main/examples/WHY_GENRO_ROUTES.md)** for more specialized insights.

## CLI Adapter

Expose any RoutingClass as a full-featured command-line tool with tab completion, help, and typed parameters — automatically generated from router introspection.

```bash
pip install genro-routes[cli]
```

```python
#!/usr/bin/env python
from genro_routes.cli import RoutingCli
from myapp import OrdersAPI

cli = RoutingCli(OrdersAPI("acme"))
cli.run()
```

```bash
$ myapp list                        # call handler directly
["order-1", "order-2"]

$ myapp retrieve 42                 # positional arguments
acme:42

$ myapp --help                      # auto-generated help
Usage: myapp [OPTIONS] COMMAND [ARGS]...

Commands:
  create    Create a new order.
  list      List all orders.
  retrieve  Retrieve a single order.

$ myapp retrieve --help             # per-command help with types
Usage: myapp retrieve [OPTIONS] IDENT

Arguments:
  IDENT  (str)
```

Features:

- **Child routers become command groups** - Attached child services create nested subcommands
- **Parameters from signatures** - Type hints map to click types (int, bool flags, Choice for Literal/Enum, multiple for list)
- **Tab completion** - Native bash/zsh/fish via click (`eval "$(_MYAPP_COMPLETE=bash_source myapp)"`)
- **Output formatting** - Auto (JSON for dicts, plain for strings), or force `json`/`table`/`raw`
- **Accepts class or instance** - `RoutingCli(MyClass)` or `RoutingCli(MyClass(config=cfg))`

## Installation

```bash
pip install genro-routes
```

With CLI support:

```bash
pip install genro-routes[cli]
```

For development:

```bash
git clone https://github.com/genropy/genro-routes.git
cd genro-routes
pip install -e ".[all]"
```

## Strict Input Validation

The `pydantic` plugin validates parameters against their type annotations in **strict mode**: an argument must already have the annotated type. Nothing is converted for you.

```python
class UsersAPI(RoutingClass):
    def __init__(self):
        self.route.plug("pydantic")

    @route()
    def get_user(self, user_id: int) -> dict:
        return {"id": user_id}

api = UsersAPI()
api.route.node("get_user")(user_id=123)                  # OK
api.route.node("get_user")(user_id="123")                # ValidationError
api.route.node("get_user")(user_id="123", _coerce=True)  # OK -> 123
```

`_coerce=True` is a reserved keyword on the node call. It asks for conversion of every parameter of that single call (`"12"` → `12`, `"2026-09-01"` → `date`), which is what a caller decoding a path segment or a query string needs. It is always consumed by the router and never reaches the handler.

- `_coerce=True` on a router without the `pydantic` plugin raises `NotAvailable` (or the class mapped to `not_available` via `node(..., errors=...)`).
- `_coerce=True` on an entry with no type hints, or with `pydantic_disabled=True`, is silently ignored: there is nothing to convert.
- `Annotated[int, Field(strict=False)]` opts a single parameter out of strict mode for every call, with no `_coerce`.

## Typed Response Schemas

Annotate return types to generate response schemas automatically. genro-routes exposes them as dialect-neutral metadata (the per-entry `result` block in `nodes()`, plus `entry.metadata`); external bridges (MCP, or OpenAPI via genro-asgi) consume it without extra work — the routing core does not generate OpenAPI itself:

```python
from typing import TypedDict
from genro_routes import RoutingClass, route

class UserResponse(TypedDict):
    id: int
    name: str
    active: bool

class UsersAPI(RoutingClass):
    def __init__(self):
        self.route.plug("pydantic")

    @route()
    def get_user(self, user_id: int) -> UserResponse:
        return {"id": user_id, "name": "alice", "active": True}

api = UsersAPI()

# The return schema is exposed in the neutral result block of nodes()
entry = api.route.nodes()["entries"]["get_user"]
result = entry["result"]
# {"schema": {"type": "object", "properties": {"id": {"type": "integer"}, ...}},
#  "media_type": None}

# A transport adapter (e.g. genro-asgi) reads this block to build the
# OpenAPI/MCP output schema — genro-routes does not translate it in-core.
```

Supported types: `TypedDict`, `dict[str, int]`, `list[...]`, `str`, `int`, `bool`, and any type Pydantic can serialize.

## Core Concepts

- **`Router`** - Runtime router owned by a `RoutingClass` instance, auto-created lazily and exposed as the read-only `route` property (never instantiated by user code)
- **`@route()`** - Decorator that marks bound methods for the class's single router (keyword-only options: `name`, `endpoint_id`, plugin flags)
- **`RoutingClass`** - Mixin that binds a class to its single router and exposes the `routing` proxy
- **`Section`** - Empty `RoutingClass` used as a grouping node: `svc.add_branches({"name": "admin", "instance": Section("Admin area")})`
- **`RoutingContext`** - Extensible execution context with parent chain delegation. Attach any attribute (`ctx.db`, `ctx.user`, `ctx.session`); missing lookups walk up `RoutingContext(parent=...)`. Stored in a `_ctx` slot on each instance — children inherit via `_routing_parent` chain. See [Execution Context Guide](docs/guide/context.md).
- **`BasePlugin`** - Base class for creating plugins with `on_decore` and `wrap_handler` hooks
- **`obj.routing`** - Proxy exposed by every RoutingClass that provides `configure(...)` for managing plugin settings without polluting the instance namespace.
- **`RouterNode`** - Callable wrapper returned by `node()`, with `path`, `error`, `doc`, `metadata` properties.
- **`NotFound` / `NotAuthenticated` / `NotAuthorized` / `NotAvailable`** - Exceptions for routing errors (not found, auth required, auth denied, capabilities missing)

## One Name Per Operation

Genro Routes uses **unique names** for handlers rather than overloading the same path with different HTTP methods. Each entry is an **operation** (`list_orders`, `create_order`, `approve_order`), not a resource acted upon by a verb.

This matches how modern API paradigms work: GraphQL, gRPC, tRPC, and MCP all identify operations by name, not by HTTP method. The HTTP verb is inferred automatically at the transport layer (e.g., genro-asgi) when generating OpenAPI schemas or mapping to HTTP endpoints.

See [Why One Name Per Operation](docs/guide/why-one-name-per-operation.md) for the full rationale.

## Pattern Highlights

- **Explicit naming + prefixes** - `@route(name="detail")` and `self.route.prefix = "handle_"` separate method names from public route names.
- **Explicit instance hierarchies** - `self.add_branches({"name": "alias", "instance": child})` connects an already-built RoutingClass instance eagerly. Navigate with `route.node("alias/handler")` or inspect with `route.nodes(basepath="alias")`.
- **Declarative branches** - `self.add_branches({"name": "sales", "cls": Sales})` declares a factory subtree, built lazily at first traversal; `{"name": "users", "instance": UsersAPI()}` attaches an already-built instance eagerly. See [Branches](#branches-lazy-subtrees-and-aliases).
- **Branch aliases** - `{"name": "fake", "alias": "real/path"}` exposes an existing subtree under a second name, like a filesystem symlink.
- **Endpoint ID** - `@route(endpoint_id="USR-001")` assigns a stable identifier for reverse lookup via `router.node("@USR-001")`.
- **Grouping nodes** - `add_branches({"name": "admin", "instance": Section("Admin area")})` creates pure organizational nodes without handlers.
- **Built-in and custom plugins** - `self.route.plug("logging")`, `self.route.plug("pydantic")`, or custom plugins.
- **Shorthand plugin syntax** - `@route(auth="admin")` instead of `@route(auth_rule="admin")`. Plugins declare their default parameter via `plugin_default_param`.
- **Channel filtering** - `@route(channel="mcp,bot_.*")` controls which transport channels can access each handler. Supports regex patterns.
- **Runtime configuration** - `routing.configure("logging/_all_", enabled=False)` applies targeted overrides with wildcards or batch updates.
- **Lazy binding** - Routers auto-bind on first use; no explicit `bind()` call needed.

## Documentation

- **[Full Documentation](https://genro-routes.readthedocs.io/)** - Complete guides, tutorials, and API reference
- **[Quick Start](docs/quickstart.md)** - Get started in 5 minutes
- **[Execution Context](docs/guide/context.md)** - RoutingContext, parent chain, slot-based ctx
- **[FAQ](docs/FAQ.md)** - Common questions and answers

## Testing

Genro Routes ships with a comprehensive test suite:

```bash
PYTHONPATH=src pytest --cov=src/genro_routes --cov-report=term-missing
```

All examples in documentation are verified by the test suite.

## Repository Structure

```text
genro-routes/
├── src/genro_routes/
│   ├── __init__.py          # Public API exports
│   ├── exceptions.py        # NotFound, NotAuthorized, NotAuthenticated, NotAvailable
│   ├── core/                # Core router implementation
│   │   ├── base_router.py   # BaseRouter (plugin-free runtime)
│   │   ├── router.py        # Router (with plugin support)
│   │   ├── router_node.py   # RouterNode (callable wrapper from node())
│   │   ├── router_interface.py  # RouterInterface (abstract base)
│   │   ├── context.py       # RoutingContext (extensible execution context)
│   │   ├── decorators.py    # @route decorator
│   │   └── routing.py       # RoutingClass, ResultWrapper
│   ├── cli/                 # CLI transport adapter
│   │   ├── __init__.py      # RoutingCli (public API)
│   │   ├── _builder.py      # CliBuilder (click tree from nodes())
│   │   ├── _type_map.py     # ParamConverter (Python → click types)
│   │   └── _formatters.py   # OutputFormatter (JSON/table/raw)
│   └── plugins/             # Built-in plugins
│       ├── _base_plugin.py  # BasePlugin, MethodEntry
│       ├── logging.py       # LoggingPlugin
│       ├── pydantic.py      # PydanticPlugin
│       ├── auth.py          # AuthPlugin
│       ├── env.py           # EnvPlugin (+ CapabilitiesSet)
│       └── channel.py       # ChannelPlugin (channel-based filtering)
├── examples/                # Example applications
├── tests/                   # Comprehensive test suite
└── docs/                    # Documentation (Sphinx)
```

## Project Status

Genro Routes is currently in **beta**. The core API is stable with complete documentation.

- **Python Support**: 3.11, 3.12, 3.13
- **License**: Apache 2.0

## Current Limitations

- **Instance methods only** - Routers assume decorated functions are bound methods (no static/class method or free function support)
- **Minimal plugin system** - Intentionally simple; advanced features must be added manually

## Roadmap

- **[genro-asgi](https://github.com/genropy/genro-asgi)** - ASGI adapter for HTTP exposure (in development)
- Additional plugins (async, storage, audit trail, metrics)
- Example applications and use cases

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

## Origin

This project was originally developed as "smartroute" under MIT license and has been renamed and relicensed.
