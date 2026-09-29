# Plugin Development

Create custom plugins to extend Genro Routes with reusable functionality like logging, validation, caching, and authorization.

## Overview

Plugins in Genro Routes:

- **Extend behavior** without modifying handler code
- **Per-instance state** - each router gets independent plugin instances
- **Two hooks**: `on_decore()` for metadata, `wrap_handler()` for execution
- **Configurable** - runtime configuration via `routing.configure()`
- **Composable** - multiple plugins work together automatically
- **Inherit automatically** - parent plugins apply to child routers

## Built-in Plugins

Genro Routes includes five production-ready plugins:

**LoggingPlugin** (`logging`):

```python
from genro_routes import RoutingClass, route

class Service(RoutingClass):
    def __init__(self):
        self.route.plug("logging")

    @route()
    def process(self, data: str):
        return f"processed:{data}"

svc = Service()
result = svc.route.node("process")("test")  # Automatically logged
```

**PydanticPlugin** (`pydantic`):

Validates input parameters and generates response schemas from return type annotations:

```python
from typing import TypedDict

class UserResponse(TypedDict):
    id: int
    name: str

class ValidatedService(RoutingClass):
    def __init__(self):
        self.route.plug("pydantic")

    @route()
    def concat(self, text: str, number: int = 1) -> str:
        return f"{text}:{number}"

    @route()
    def get_user(self, user_id: int) -> UserResponse:
        return {"id": user_id, "name": "alice"}

svc = ValidatedService()
svc.route.node("concat")("hello", 3)  # Valid
# svc.route.node("concat")(123, "oops")  # ValidationError
# svc.route.node("concat")("hello", "3")  # ValidationError: strict, no conversion
svc.route.node("concat")("hello", "3", _coerce=True)  # OK, "3" becomes 3

# Response schema available in metadata
entry = svc.route._entries["get_user"]
schema = entry.metadata["pydantic"]["response_schema"]
# {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}, ...}
```

**AuthPlugin** (`auth`):

```python
class SecureService(RoutingClass):
    def __init__(self):
        self.route.plug("auth")

    @route(auth_rule="admin")
    def admin_only(self):
        return "secret"

svc = SecureService()
node = svc.route.node("admin_only", auth_tags="admin")  # Authorized
node = svc.route.node("admin_only", auth_tags="guest")  # Not authorized
```

**EnvPlugin** (`env`):

```python
from genro_routes.plugins.env import CapabilitiesSet, capability

class ServerCapabilities(CapabilitiesSet):
    @capability
    def redis(self) -> bool:
        return True  # Check if redis is available

class CapabilityService(RoutingClass):
    def __init__(self):
        self.route.plug("env")
        self.capabilities = ServerCapabilities()

    @route(env_requires="redis")
    def cached_action(self):
        return "cached"

svc = CapabilityService()
entries = svc.route.nodes().get("entries", {})  # "cached_action" visible
```

For dynamic capabilities that change at runtime, use `CapabilitiesSet`:

```python
from genro_routes.plugins.env import CapabilitiesSet, capability

class ServerCapabilities(CapabilitiesSet):
    @capability
    def redis(self) -> bool:
        return "redis" in sys.modules

    @capability
    def maintenance_window(self) -> bool:
        # Only active during first 5 minutes of each hour
        return datetime.now().minute < 5

class DynamicService(RoutingClass):
    def __init__(self):
        self.route.plug("env")
        self.capabilities = ServerCapabilities()

    @route(env_requires="maintenance_window")
    def maintenance_task(self):
        return "maintenance"
```

Capabilities are evaluated dynamically on each `nodes()` call.

**ChannelPlugin** (`channel`):

```python
class MultiChannelAPI(RoutingClass):
    def __init__(self):
        self.route.plug("channel")
        self.route.channel.configure(channels="*")  # default: all channels

    @route(channel="mcp")  # shorthand for channel_channels="mcp"
    def mcp_only(self):
        return "MCP exclusive"

    @route(channel="mcp,bot_.*")  # regex patterns supported
    def mcp_and_bots(self):
        return "MCP + any bot channel"

    @route()  # inherits "*" from router config
    def everywhere(self):
        return "all channels"

svc = MultiChannelAPI()
entries = svc.route.nodes(channel_channel="mcp")["entries"]   # all three
entries = svc.route.nodes(channel_channel="rest")["entries"]   # only everywhere
```

See [Quick Start - Plugins](../quickstart.md#adding-plugins) for more examples.

## Shorthand Plugin Syntax

Plugins that declare a `plugin_default_param` support shorthand syntax in the `@route` decorator. Instead of writing the full `plugin_param=value` form, you can use `plugin=value`:

```python
# These are equivalent:
@route(auth_rule="admin")        # longform
@route(auth="admin")             # shorthand

@route(env_requires="redis")     # longform
@route(env="redis")              # shorthand

@route(channel_channels="mcp")   # longform
@route(channel="mcp")            # shorthand
```

The shorthand is opt-in per plugin. Built-in plugins that support it:

| Plugin | Shorthand | Expands to |
|--------|-----------|------------|
| `auth` | `auth="admin"` | `auth_rule="admin"` |
| `env` | `env="redis"` | `env_requires="redis"` |
| `channel` | `channel="mcp"` | `channel_channels="mcp"` |

Custom plugins can declare their own default parameter:

```python
class MyPlugin(BasePlugin):
    plugin_code = "myplugin"
    plugin_default_param = "target"  # enables: myplugin="value"
```

## Built-in Plugins API Reference

This section provides detailed API documentation for each built-in plugin.

### AuthPlugin API

The AuthPlugin provides **tag-based authorization** with boolean rule expressions. It enables fine-grained access control at the handler level.

**Plugin code**: `auth`

**Route decorator parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `auth_rule` | `str` | Boolean expression defining required tags |

**Rule syntax**:

| Operator | Meaning | Example |
|----------|---------|---------|
| `\|` | OR - user must have at least one | `"admin\|manager"` |
| `&` | AND - user must have all | `"admin&internal"` |
| `!` | NOT - user must not have | `"!guest"` |
| `()` | Grouping | `"(admin\|manager)&!banned"` |

**Query parameters** (passed to `node()` or `nodes()`):

| Parameter | Type | Description |
|-----------|------|-------------|
| `auth_tags` | `str` | Comma-separated list of tags the user has |

**Error codes returned by `deny_reason()`**:

| Code | HTTP | Meaning |
|------|------|---------|
| `""` | - | Access allowed |
| `"not_authenticated"` | 401 | Entry requires tags but none provided |
| `"not_authorized"` | 403 | Tags provided but don't match rule |

**Complete example**:

```python
from genro_routes import RoutingClass, route
from genro_routes import NotAuthenticated, NotAuthorized

class SecureAPI(RoutingClass):
    def __init__(self):
        self.route.plug("auth")

    @route()  # No rule = always accessible
    def public_info(self):
        return "public"

    @route(auth_rule="user")  # Requires "user" tag
    def user_profile(self):
        return "profile"

    @route(auth_rule="admin|moderator")  # Requires admin OR moderator
    def manage_content(self):
        return "content"

    @route(auth_rule="admin&!banned")  # Requires admin AND NOT banned
    def admin_panel(self):
        return "admin"

api = SecureAPI()

# No tags - only public entries visible
public_entries = api.route.nodes()
assert "public_info" in public_entries["entries"]
assert "user_profile" not in public_entries["entries"]

# With user tag
user_entries = api.route.nodes(auth_tags="user")
assert "user_profile" in user_entries["entries"]

# Calling protected handler
node = api.route.node("admin_panel", auth_tags="admin")
assert node.error is None  # Authorized

node = api.route.node("admin_panel", auth_tags="admin,banned")
assert node.error == "not_authorized"  # Has admin but also banned

node = api.route.node("admin_panel")  # No tags
assert node.error == "not_authenticated"
```

### EnvPlugin API

The EnvPlugin provides **capability-based access control**. It filters entries based on system capabilities that can be static or dynamic.

**Plugin code**: `env`

**Route decorator parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `env_requires` | `str` | Boolean expression defining required capabilities |

**Rule syntax**: Same as AuthPlugin (`|`, `&`, `!`, `()`)

**Query parameters** (passed to `node()` or `nodes()`):

| Parameter | Type | Description |
|-----------|------|-------------|
| `env_capabilities` | `str` | Comma-separated list of additional capabilities |

**Capability sources** (combined automatically):

1. **Instance capabilities**: Via `self.capabilities` attribute (a `CapabilitiesSet` subclass)
2. **Request capabilities**: Via `env_capabilities` parameter

**Error codes returned by `deny_reason()`**:

| Code | HTTP | Meaning |
|------|------|---------|
| `""` | - | Access allowed |
| `"not_available"` | 501 | Required capabilities not present |

**CapabilitiesSet class**:

Create dynamic capabilities by subclassing `CapabilitiesSet` and decorating methods with `@capability`:

```python
from genro_routes.plugins.env import CapabilitiesSet, capability

class ServerCapabilities(CapabilitiesSet):
    def __init__(self, config):
        self._config = config

    @capability
    def redis(self) -> bool:
        """Check if Redis is available."""
        return self._config.get("redis_enabled", False)

    @capability
    def email(self) -> bool:
        """Check if email service is configured."""
        return "smtp_host" in self._config

    @capability
    def maintenance_mode(self) -> bool:
        """Dynamic capability based on time."""
        from datetime import datetime
        return datetime.now().hour < 6  # Only available 00:00-06:00
```

**CapabilitiesSet behavior**:

- `"redis" in caps` - Check if capability is currently active
- `list(caps)` - List all currently active capabilities
- `len(caps)` - Count of active capabilities

**Hierarchical capabilities** (`current_capabilities` property):

The router automatically collects capabilities from the instance and its entire parent chain. When a child is attached to a parent, the child's `current_capabilities` includes capabilities from all ancestors:

```python
parent.capabilities = ParentCapabilities()   # has "redis"
child.capabilities = ChildCapabilities()     # has "email"

parent.add_branches({"name": "child", "instance": child})

# child.route.current_capabilities returns {"redis", "email"}
# (accumulated from parent + child)
```

**Complete example**:

```python
from genro_routes import RoutingClass, route
from genro_routes.plugins.env import CapabilitiesSet, capability

class AppCapabilities(CapabilitiesSet):
    @capability
    def cache(self) -> bool:
        return True  # Always available

    @capability
    def premium(self) -> bool:
        return False  # Not available

class FeatureAPI(RoutingClass):
    def __init__(self):
        self.route.plug("env")
        self.capabilities = AppCapabilities()

    @route()  # No requirements = always available
    def basic_feature(self):
        return "basic"

    @route(env_requires="cache")
    def cached_data(self):
        return "cached"

    @route(env_requires="premium")
    def premium_feature(self):
        return "premium"

    @route(env_requires="cache|premium")
    def either_feature(self):
        return "either"

    @route(env_requires="cache&premium")
    def both_features(self):
        return "both"

api = FeatureAPI()

# Based on capabilities (cache=True, premium=False)
entries = api.route.nodes()["entries"]
assert "basic_feature" in entries
assert "cached_data" in entries      # cache is True
assert "premium_feature" not in entries  # premium is False
assert "either_feature" in entries   # cache OR premium
assert "both_features" not in entries    # cache AND premium

# Override with request capabilities
entries = api.route.nodes(env_capabilities="premium")["entries"]
assert "premium_feature" in entries  # Now available
assert "both_features" in entries    # cache (instance) + premium (request)
```

### PydanticPlugin API

The PydanticPlugin provides **automatic input validation and response schema generation**. It validates handler parameters against their type annotations and generates JSON Schema from return type annotations.

**Plugin code**: `pydantic`

**Route decorator parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `pydantic_disabled` | `bool` | Skip validation for this handler (default `False`) |

**Strict validation and per-call coercion**:

Validation runs in strict mode: an argument must already have the annotated type, and `"12"` for an `int` parameter is a `ValidationError` rather than a conversion. Pydantic's own strict-mode allowances still apply — an `int` is accepted for a `float` parameter.

A caller asks for conversion with the reserved keyword `_coerce=True` on the node call. It applies to every parameter of that call (`"12"` → `12`, `"2026-09-01"` → `date`) and is always consumed by the router, so the handler never sees it.

```python
svc.route.node("get_user")(user_id=123)                  # OK
svc.route.node("get_user")(user_id="123")                # ValidationError
svc.route.node("get_user")(user_id="123", _coerce=True)  # OK -> 123
```

| Situation | Result |
|-----------|--------|
| `_coerce=True`, router without the `pydantic` plugin | raises the exception mapped to `not_available` (`NotAvailable` by default) |
| `_coerce=True`, entry with no type hints | silently ignored: no model, nothing to convert |
| `_coerce=True`, entry with `pydantic_disabled=True` | silently ignored: validation is off |
| `_coerce=False` or absent | strict validation, the default |

A single parameter opts out of strict mode for every call with `Annotated[int, Field(strict=False)]`: the per-field setting overrides the model-level strict flag, with no `_coerce`.

**Response schema generation**:

When a handler has a return type annotation, the plugin automatically generates a JSON Schema using Pydantic's `TypeAdapter`. The schema is stored in `entry.metadata["pydantic"]["response_schema"]` and exposed through `nodes()` introspection.

Supported return types: `TypedDict`, `dict[str, T]`, `list[T]`, `str`, `int`, `bool`, Pydantic models, and any type Pydantic can serialize. TypedDict support requires Python 3.12+.

**Metadata exposed via `entry_metadata()`**:

| Key | Type | Description |
|-----|------|-------------|
| `model` | `type \| None` | Pydantic model for input validation |
| `hints` | `dict` | Input parameter type hints |
| `accepts_varargs` | `bool` | Whether handler accepts `*args` |
| `response_schema` | `dict \| None` | JSON Schema for the return type (if annotated) |

**Example**:

```python
from typing import TypedDict
from genro_routes import RoutingClass, route

class UserResponse(TypedDict):
    id: int
    name: str
    active: bool

class UserAPI(RoutingClass):
    def __init__(self):
        self.route.plug("pydantic")

    @route()
    def get_user(self, user_id: int) -> UserResponse:
        return {"id": user_id, "name": "alice", "active": True}

    @route()
    def list_users(self) -> list[UserResponse]:
        return []

api = UserAPI()

# Response schema in entry metadata
entry = api.route._entries["get_user"]
schema = entry.metadata["pydantic"]["response_schema"]
# {"type": "object", "properties": {"id": {...}, "name": {...}, "active": {...}}, ...}

# Also exposed via nodes() introspection
nodes = api.route.nodes()
pydantic_meta = nodes["entries"]["get_user"]["plugins"]["pydantic"]["metadata"]
assert "response_schema" in pydantic_meta
```

### OpenAPI generation (transport layer, not core)

OpenAPI translation is **not** part of genro-routes. The routing core exposes only the dialect-neutral introspection tree: each entry carries a `result` block (`{schema, media_type}`) and a `params` block, produced by the PydanticPlugin from return-type and parameter annotations. A transport adapter such as [genro-asgi](https://github.com/genropy/genro-asgi) reads that tree and builds the OpenAPI (or MCP) document — including HTTP-method inference, tags, and request/response schemas. See the genro-asgi documentation for the OpenAPI dialect.

### ChannelPlugin API

The ChannelPlugin provides **channel-based endpoint filtering**. It controls access based on the request channel (mcp, rest, bot_*, web, etc.), working alongside AuthPlugin (who) and EnvPlugin (what's available).

**Plugin code**: `channel`

**Route decorator parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `channel_channels` | `str` | Comma-separated list of allowed channel patterns |
| `channel` | `str` | Shorthand for `channel_channels` (via `plugin_default_param`) |

**Channel pattern syntax**:

| Pattern | Matches | Example |
|---------|---------|---------|
| `"mcp"` | Exact match | Only MCP channel |
| `"bot_.*"` | Regex via `re.fullmatch` | Any bot channel |
| `"mcp,rest"` | Comma-separated list | MCP or REST |
| `"*"` | Wildcard | Any channel |
| `""` | Nothing | Default closed |

**Query parameters** (passed to `node()` or `nodes()`):

| Parameter | Type | Description |
|-----------|------|-------------|
| `channel_channel` | `str` | The request channel to filter against |

**Error codes returned by `deny_reason()`**:

| Code | HTTP | Meaning |
|------|------|---------|
| `""` | - | Access allowed |
| `"not_available"` | 501 | Channel doesn't match or not configured |

**Key behaviors**:

- **Default closed**: entry without `channels` configured returns `"not_available"`
- **Config inheritance**: child routers inherit parent channel config
- **Regex patterns**: each pattern is matched with `re.fullmatch`

**Complete example**:

```python
from genro_routes import RoutingClass, route

class MultiChannelAPI(RoutingClass):
    def __init__(self):
        self.route.plug("channel")
        self.route.channel.configure(channels="*")  # default: all channels

    @route(channel="mcp")  # shorthand syntax
    def mcp_tool(self):
        return "only via MCP"

    @route(channel="mcp,bot_.*")
    def ai_accessible(self):
        return "MCP + any bot"

    @route(channel="rest,web")
    def browser_only(self):
        return "REST or web"

    @route()  # inherits "*" from router config
    def universal(self):
        return "everywhere"

api = MultiChannelAPI()

# MCP client sees: mcp_tool, ai_accessible, universal
entries = api.route.nodes(channel_channel="mcp")["entries"]
assert "mcp_tool" in entries
assert "browser_only" not in entries

# REST client sees: browser_only, universal
entries = api.route.nodes(channel_channel="rest")["entries"]
assert "browser_only" in entries
assert "mcp_tool" not in entries

# Combined with auth: plug both plugins on the same router
# self.route.plug("channel").plug("auth")
# Both filters must pass for entry to be visible
```

## Creating Custom Plugins

`genro_routes.plugins` is the supported public import path for external plugin
authors. Import `BasePlugin` to implement the plugin hooks and `MethodEntry` for
the handler metadata passed to those hooks. Register the subclass explicitly
with `Router.register_plugin()` and attach it with `service.route.plug()`.

The legacy imports from `genro_routes.plugins._base_plugin` remain compatible:
both paths expose the same class objects. The public re-exports add no concrete
plugin imports or registrations; the main package still registers its built-in
plugins as before.

Extend `BasePlugin` and implement hooks. Every plugin **must** define two class attributes:

- `plugin_code` - unique identifier used for registration (e.g. `"logging"`)
- `plugin_description` - human-readable description

### Basic Plugin Structure

```python
from genro_routes import Router, RoutingClass, route
from genro_routes.plugins import BasePlugin, MethodEntry

class CapturePlugin(BasePlugin):
    # Required class attributes
    plugin_code = "capture"
    plugin_description = "Captures handler calls for testing"

    # Optional: custom instance state (use __slots__ for efficiency)
    __slots__ = ("calls",)

    def __init__(self, router, **config):
        self.calls = []
        super().__init__(router, **config)

    def configure(self, enabled: bool = True):
        """Define accepted configuration parameters.

        The method body can be empty - the wrapper handles storage.
        Parameters become the configuration schema validated by Pydantic.
        """
        pass

    def on_decore(self, router, func, entry: MethodEntry):
        """Called once when handler is registered."""
        entry.metadata["capture"] = True

    def wrap_handler(self, router, entry: MethodEntry, call_next):
        """Called to build middleware chain."""
        def wrapper(*args, **kwargs):
            self.calls.append(entry.name)
            return call_next(*args, **kwargs)
        return wrapper

# Register plugin globally
Router.register_plugin(CapturePlugin)

# Use in service
class PluginService(RoutingClass):
    def __init__(self):
        self.route.plug("capture")

    @route()
    def do_work(self):
        return "ok"

svc = PluginService()
result = svc.route.node("do_work")()
assert svc.route.capture.calls == ["do_work"]
```

### Constructor Signature

The constructor **must** accept `router` as first argument and `**config`:

```python
def __init__(self, router, **config):
    # 1. Initialize your own state FIRST
    self.my_state = []

    # 2. Call super().__init__ which:
    #    - Sets self.name = self.plugin_code
    #    - Stores self._router = router
    #    - Initializes the config store
    #    - Calls self.configure(**config)
    super().__init__(router, **config)
```

**Important**: Initialize your state *before* calling `super().__init__()` because the parent constructor calls `configure()` which might need your state.

## Plugin Hooks

Genro Routes plugins can override these methods:

| Hook | When Called | Purpose | Required |
|------|-------------|---------|----------|
| `configure()` | At plugin init and runtime | Define configuration schema | No |
| `on_decore()` | Handler registration | Add metadata, validate signatures | No |
| `wrap_handler()` | Handler invocation | Middleware (logging, auth, etc.) | No |
| `deny_reason()` | `nodes()` introspection | Filter visible handlers | No |
| `entry_metadata()` | `nodes()` introspection | Add plugin metadata to output | No |
| `on_attached_to_parent()` | Child attached to parent | Handle plugin inheritance | No |
| `on_parent_config_changed()` | Parent config changes | React to parent updates | No |

**All hooks are optional.** Override only what you need. A minimal plugin can have just `plugin_code` and `plugin_description` with no hooks.

## Plugin Execution Order

The order in which you call `plug()` determines the middleware execution order.

### The Onion Model

Plugins wrap handlers like layers of an onion:

```
Request → [Last Plugin] → [Middle Plugin] → [First Plugin] → Handler
Response ← [Last Plugin] ← [Middle Plugin] ← [First Plugin] ← Handler
```

**The last plugin attached is the outermost layer** (first to receive requests, last to process responses).

### Example: Logging then Validation

```python
class Service(RoutingClass):
    def __init__(self):
        self.route.plug("logging").plug("pydantic")

    @route()
    def process(self, count: int):
        return f"processed:{count}"
```

**Execution flow**:

```
1. Request arrives
2. LoggingPlugin.wrap_handler (before)
   3. PydanticPlugin.wrap_handler (before - validates input)
      4. Handler executes: process(count=5)
   5. PydanticPlugin.wrap_handler (after)
6. LoggingPlugin.wrap_handler (after - logs result)
7. Response returned
```

### Practical Ordering Guidelines

| Plugin Order | Use Case |
|--------------|----------|
| `logging` then `auth` then `pydantic` | Log all requests, check auth, then validate |
| `auth` then `logging` then `pydantic` | Only log authenticated requests |
| `pydantic` then `logging` | Log includes validation errors |

**Common pattern for production**:

```python
self.route.plug("logging")\
    .plug("auth")\
    .plug("pydantic")
```

This order means:
1. **logging** - Log everything (including auth failures)
2. **auth** - Check authentication/authorization
3. **pydantic** - Validate input

### Visualizing the Stack

```
┌─────────────────────────────────┐
│         LoggingPlugin           │  ← Outermost (sees all)
│  ┌───────────────────────────┐  │
│  │       AuthPlugin          │  │
│  │  ┌─────────────────────┐  │  │
│  │  │  PydanticPlugin     │  │  │
│  │  │  ┌───────────────┐  │  │  │
│  │  │  │   Handler     │  │  │  │  ← Innermost
│  │  │  └───────────────┘  │  │  │
│  │  └─────────────────────┘  │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

### configure(**kwargs)

Define accepted configuration parameters. The method signature becomes the configuration schema, validated by Pydantic.

```python
def configure(
    self,
    enabled: bool = True,
    threshold: int = 10,
    level: str = "info"
):
    """Body can be empty - the wrapper handles storage."""
    pass
```

The wrapper added by `__init_subclass__` automatically:

- Parses `flags` string (e.g. `"enabled,before:off"`) into booleans
- Routes to `_target` (`"_all_"` for router-level, `"handler_name"` for per-handler)
- Validates parameters via Pydantic's `@validate_call`
- Writes config to the router's store

### on_decore(router, func, entry)

Called once when a handler is registered.

**Parameters**:

- `router` - The Router instance
- `func` - The original method
- `entry` - MethodEntry with `name`, `func`, `router`, `plugins`, `metadata`

**Use for**:

- Adding metadata to handlers
- Validating handler signatures
- Building handler indexes
- Pre-computing handler information (e.g., Pydantic models)

**Example**:

```python
def on_decore(self, router, func, entry):
    # Add timestamp to metadata
    entry.metadata["registered_at"] = time.time()

    # Validate signature
    sig = inspect.signature(func)
    if "user_id" not in sig.parameters:
        raise ValueError(f"{entry.name} must have user_id parameter")
```

### wrap_handler(router, entry, call_next)

Called to build the middleware chain. Return a callable that wraps `call_next`.

**Parameters**:

- `router` - The Router instance
- `entry` - MethodEntry for the handler
- `call_next` - Callable to invoke next plugin or handler

**Returns**: Wrapper function with same signature as `call_next`

**Use for**:

- Logging and monitoring
- Authorization checks
- Input/output transformation
- Caching
- Error handling

**Example**:

```python
def wrap_handler(self, router, entry, call_next):
    def wrapper(*args, **kwargs):
        # Before handler
        start = time.time()

        try:
            # Call handler (or next plugin)
            result = call_next(*args, **kwargs)

            # After handler
            duration = time.time() - start
            print(f"{entry.name} took {duration:.3f}s")

            return result
        except Exception as e:
            print(f"{entry.name} failed: {e}")
            raise

    return wrapper
```

### deny_reason(entry, **filters)

Control handler visibility during introspection (`nodes()`).

The plugin receives all filter arguments passed to `nodes(**filters)` and is responsible for:

- Interpreting the filters according to its own logic
- Validating filter values if needed
- Comparing filters against handler metadata or configuration

**Parameters**:

- `entry` - MethodEntry being checked
- `**filters` - All filter criteria passed to `nodes()`. The plugin decides which filters to handle and how to interpret them.

**Returns**: `""` (empty string) to allow, or a reason string to deny (e.g., `"not_authorized"`, `"not_available"`)

**Example**:

```python
def deny_reason(self, entry, visibility=None, **filters):
    # Plugin interprets 'visibility' filter against entry metadata
    if visibility:
        entry_visibility = entry.metadata.get("visibility", "public")
        if entry_visibility != visibility:
            return "not_visible"  # deny with reason
    return ""  # no reason to deny
```

### entry_metadata(router, entry)

Provide plugin-specific metadata for `nodes()` output.

**Parameters**:

- `router` - The Router instance
- `entry` - MethodEntry being described

**Returns**: Dict stored in `plugins[plugin_name]["metadata"]`

**Example**:

```python
def entry_metadata(self, router, entry):
    cfg = self.configuration(entry.name)
    return {
        "enabled": cfg.get("enabled", True),
        "threshold": cfg.get("threshold", 10),
    }
```

The result appears in `nodes()` output:

```python
{
    "entries": {
        "handler_name": {
            "plugins": {
                "my_plugin": {
                    "config": {"enabled": True, "threshold": 10},
                    "metadata": {"enabled": True, "threshold": 10}
                }
            }
        }
    }
}
```

### on_attached_to_parent(parent_plugin)

Called when a child router is attached to a parent that has this plugin.
The child plugin can decide how to handle the parent's configuration.

**Parameters**:

- `parent_plugin` - The parent's plugin instance of the same type

**Use for**:

- Inheriting configuration from parent
- Merging parent and child settings
- Custom inheritance logic (e.g., union of tags)

**Default behavior**:

- Copies parent's `_all_` config to child's `_all_` config
- Preserves child's entry-specific configurations (set via decorators)
- Does NOT overwrite child's `_all_` if child already configured it

**Example - Custom inheritance with union**:

```python
def on_attached_to_parent(self, parent_plugin):
    """Merge parent tags with child tags (union)."""
    parent_tags = parent_plugin.configuration().get("tags", "")
    my_tags = self.configuration().get("tags", "")

    # Union of tags
    parent_set = set(t.strip() for t in parent_tags.split(",") if t.strip())
    my_set = set(t.strip() for t in my_tags.split(",") if t.strip())
    merged = ",".join(sorted(parent_set | my_set))

    if merged:
        self.configure(tags=merged)
```

### on_parent_config_changed(old_config, new_config)

Called when the parent router modifies its plugin configuration after attachment.
The child plugin can decide whether to follow the change.

**Parameters**:

- `old_config` - The parent's previous `_all_` configuration
- `new_config` - The parent's new `_all_` configuration

**Use for**:

- Keeping child in sync with parent changes
- Selective updates based on alignment
- Custom propagation logic

**Default behavior**:

- Compares child's current `_all_` config with `old_config`
- If equal (child was following parent) → updates to `new_config`
- If different (child made own choices) → ignores the change

This preserves explicit child customizations while keeping "default" children in sync with parent changes.

**Example - Always follow parent**:

```python
def on_parent_config_changed(self, old_config, new_config):
    """Always update to match parent."""
    self.configure(**new_config)
```

**Example - Never follow parent**:

```python
def on_parent_config_changed(self, old_config, new_config):
    """Ignore parent changes, keep local config."""
    pass  # Do nothing
```

## Plugin Inheritance

When a child router is attached to a parent via `add_branches()` (instance form), plugins are inherited
based on what the child already has.

### Inheritance Rules

1. **Child does NOT have the plugin** → plugin is inherited from parent:
   - A new plugin instance is created on the child
   - `on_attached_to_parent(parent_plugin)` is called
   - Default behavior copies parent's `_all_` config
   - `on_decore` is applied to all child entries

2. **Child already HAS the plugin** → parent does NOT interfere:
   - Child keeps its own plugin instance and configuration
   - No hooks are called, no config is copied
   - The child made an explicit choice by having the plugin

### Why This Design?

This approach gives maximum flexibility:

- **Default behavior is sensible**: Children without the plugin inherit it naturally
- **Explicit choices are respected**: If child has the plugin, it knows what it's doing
- **Plugins control their inheritance**: Each plugin can customize via hooks
- **No magic or surprises**: The rules are simple and predictable

### Example: AuthPlugin Inheritance

AuthPlugin follows the default inheritance behavior: a child without its own
auth configuration inherits the parent's router-level rule:

```python
class Child(RoutingClass):
    @route(auth_rule="admin")
    def admin_only(self): ...

    @route()
    def open_action(self): ...

class Parent(RoutingClass):
    def __init__(self):
        self.route.plug("auth", rule="corporate")
        self.child = Child()
        self.add_branches({"name": "child", "instance": self.child})

parent = Parent()

# Result:
# - The child inherits the auth plugin with the parent's router-level
#   rule ("corporate"): open_action requires the "corporate" tag
# - admin_only keeps its own entry rule ("admin")
```

**Rule semantics**:

- Entry without a rule (and no router-level rule) → always accessible (public)
- Entry with a rule → accessible only if the `auth_tags` satisfy the rule expression

See [ARCHITECTURE.md](../ARCHITECTURE.md#plugin-inheritance) for detailed inheritance documentation.

## Plugin Registration

Register plugins globally with `Router.register_plugin()`:

```python
class CustomPlugin(BasePlugin):
    plugin_code = "custom"
    plugin_description = "My custom plugin"

    def __init__(self, router, **config):
        super().__init__(router, **config)

# Register once - uses plugin_code as the name
Router.register_plugin(CustomPlugin)

# Now available in all routers
class Service(RoutingClass):
    def __init__(self):
        self.route.plug("custom")
```

**Registration rules**:

- Plugin class must extend `BasePlugin`
- Plugin class must define `plugin_code` (used as registration name)
- Cannot re-register same name with different class
- Registration is global across all routers

**Check available plugins**:

```python
# List all registered plugins
plugins = Router.available_plugins()
assert "logging" in plugins
assert "pydantic" in plugins
assert "custom" in plugins
```

## Per-Instance State

Each router instance gets independent plugin state:

```python
class CapturePlugin(BasePlugin):
    plugin_code = "capture"
    plugin_description = "Captures handler calls"

    __slots__ = ("calls",)

    def __init__(self, router, **config):
        self.calls = []  # Per-instance state
        super().__init__(router, **config)

    def wrap_handler(self, router, entry, call_next):
        def wrapper(*args, **kwargs):
            self.calls.append(entry.name)
            return call_next(*args, **kwargs)
        return wrapper

Router.register_plugin(CapturePlugin)

# Each instance is isolated
svc1 = PluginService()
svc2 = PluginService()

svc1.route.node("do_work")()
assert svc1.route.capture.calls == ["do_work"]
assert svc2.route.capture.calls == []  # Independent state
```

**Benefits**:

- No global state pollution
- Thread-safe by default
- Independent configuration per instance
- Easy testing with isolated state

## Plugin Configuration

Plugins define their configuration schema via the `configure()` method. The configuration system provides:

- **Router-level defaults**: Apply to all handlers
- **Per-handler overrides**: Target specific handlers
- **Flags shorthand**: Boolean options as comma-separated string
- **Pydantic validation**: Type checking on all parameters

### Defining Configuration

```python
class MyPlugin(BasePlugin):
    plugin_code = "my_plugin"
    plugin_description = "Example plugin with configuration"

    def __init__(self, router, **config):
        super().__init__(router, **config)

    def configure(
        self,
        enabled: bool = True,
        level: str = "info",
        threshold: int = 10
    ):
        """Define accepted parameters. Body can be empty."""
        pass
```

### Reading Configuration

Use `configuration(method_name)` to read merged config (base + per-handler):

```python
def wrap_handler(self, router, entry, call_next):
    def wrapper(*args, **kwargs):
        # Get merged config for this handler
        cfg = self.configuration(entry.name)

        if not cfg.get("enabled", True):
            return call_next(*args, **kwargs)

        level = cfg.get("level", "info")
        # ... use configuration
        return call_next(*args, **kwargs)
    return wrapper
```

### Configuring at Runtime

```python
# At plugin attachment (initial config)
router.plug("my_plugin", enabled=True, level="debug")

# Or via the plugin instance
router.my_plugin.configure(threshold=20)

# Per-handler config
router.my_plugin.configure(_target="critical_handler", level="error")

# Multiple handlers
router.my_plugin.configure(_target="handler1,handler2", enabled=False)

# Using flags shorthand
router.my_plugin.configure(flags="enabled,log:off")
```

### The `_target` Parameter

- `"_all_"` (default): Router-level config, applies to all handlers
- `"handler_name"`: Config for specific handler only
- `"h1,h2,h3"`: Apply same config to multiple handlers

### The `flags` Parameter

Shorthand for boolean options:

```python
# These are equivalent:
router.my_plugin.configure(enabled=True, before=True, after=False)
router.my_plugin.configure(flags="enabled,before,after:off")
```

Format: `"flag1,flag2:off,flag3:on"` - bare names are `True`, `:off` is `False`.

## Complete Example: Authorization Plugin

Real-world plugin with configuration and state:

```python
import inspect
from genro_routes import Router, RoutingClass, route
from genro_routes.plugins import BasePlugin

class AuthPlugin(BasePlugin):
    plugin_code = "auth"
    plugin_description = "Authentication and authorization plugin"

    def __init__(self, router, **config):
        super().__init__(router, **config)

    def configure(
        self,
        enabled: bool = True,
        required: bool = True
    ):
        """Configure auth requirements."""
        pass

    def on_decore(self, router, func, entry):
        """Extract required roles from docstring."""
        doc = inspect.getdoc(func) or ""
        if "@roles:" in doc:
            roles = doc.split("@roles:")[1].split()[0].split(",")
            entry.metadata["required_roles"] = roles

    def wrap_handler(self, router, entry, call_next):
        def wrapper(*args, **kwargs):
            cfg = self.configuration(entry.name)

            if not cfg.get("enabled", True):
                return call_next(*args, **kwargs)

            # Extract user from first arg (assuming request object)
            request = args[0] if args else None
            user = getattr(request, "user", None)

            # Check authentication
            if cfg.get("required", True) and not user:
                raise PermissionError("Authentication required")

            # Check authorization
            required_roles = entry.metadata.get("required_roles", [])
            if required_roles:
                user_roles = getattr(user, "roles", [])
                if not any(role in user_roles for role in required_roles):
                    raise PermissionError(f"Requires roles: {required_roles}")

            return call_next(*args, **kwargs)

        return wrapper

    def entry_metadata(self, router, entry):
        """Expose auth config in nodes() output."""
        cfg = self.configuration(entry.name)
        return {
            "enabled": cfg.get("enabled", True),
            "required": cfg.get("required", True),
            "roles": entry.metadata.get("required_roles", []),
        }

# Register plugin
Router.register_plugin(AuthPlugin)

# Use in service
class API(RoutingClass):
    def __init__(self):
        self.route.plug("auth")

    @route()
    def public_endpoint(self, request):
        """@roles:guest"""
        return "public data"

    @route()
    def admin_endpoint(self, request):
        """@roles:admin"""
        return "admin data"

api = API()

# Configure: disable auth requirement for public endpoints
api.route.auth.configure(_target="public_endpoint", required=False)
```

## Best Practices

**Single responsibility**:

```python
# Good: One plugin, one concern
class LoggingPlugin(BasePlugin): ...
class ValidationPlugin(BasePlugin): ...
class CachingPlugin(BasePlugin): ...

# Bad: One plugin doing everything
class EverythingPlugin(BasePlugin): ...
```

**Composition over complexity**:

```python
# Good: Multiple simple plugins
self.route.plug("logging")\
    .plug("pydantic")\
    .plug("caching")\
    .plug("auth")

# Bad: One complex plugin
self.route.plug("monolith")
```

**Configuration defaults**:

```python
# Good: Sensible defaults in configure() signature
def configure(
    self,
    enabled: bool = True,      # Enabled by default
    level: str = "info",       # Reasonable default
    strict: bool = False       # Permissive by default
):
    pass
```

**Error handling**:

```python
def wrap_handler(self, router, entry, call_next):
    def wrapper(*args, **kwargs):
        try:
            return call_next(*args, **kwargs)
        except Exception as e:
            # Log error but don't suppress unless configured
            cfg = self.configuration(entry.name)
            if cfg.get("suppress_errors", False):
                return None
            raise
    return wrapper
```

## Runtime Plugin Management

The `Router` class provides methods to enable/disable plugins and store runtime data at the handler level, without modifying the static configuration.

### Enabling and Disabling Plugins

```python
svc = MyService()

# Disable pydantic validation for a specific handler at runtime
svc.route.set_plugin_enabled("handler_name", "pydantic", enabled=False)

# Disable globally for all handlers
svc.route.set_plugin_enabled("_all_", "logging", enabled=False)

# Check if a plugin is enabled for a handler
if svc.route.is_plugin_enabled("handler_name", "pydantic"):
    print("Validation active")
```

**Resolution order** for `is_plugin_enabled` (first found wins):

1. Entry-level runtime override (via `set_plugin_enabled` for specific handler)
2. Entry-level static config (via `configure(_target=handler_name, enabled=...)`)
3. Global runtime override (via `set_plugin_enabled` for `_all_`)
4. Global static config (via `configure(enabled=...)`)
5. Default: `True`

### Storing Runtime Data

Plugins can use `set_runtime_data` / `get_runtime_data` to store handler-specific state that persists across invocations but is not part of the configuration schema:

```python
# Store runtime data for a plugin/handler combination
svc.route.set_runtime_data("handler_name", "my_plugin", "call_count", 0)

# Retrieve runtime data
count = svc.route.get_runtime_data("handler_name", "my_plugin", "call_count", default=0)
```

**Use cases**:

- Counters and metrics per handler
- Rate limiting state
- Cache invalidation flags
- Plugin-specific state that should not be part of the config schema

## Next Steps

- **[Plugin Configuration](plugin-configuration.md)** - Configure plugins at runtime
- **[Built-in Plugins API](../api/plugins.md)** - LoggingPlugin and PydanticPlugin reference
- **[Hierarchies](hierarchies.md)** - Plugin inheritance in hierarchies
- **[API Reference](../api/reference.md)** - Complete API documentation
