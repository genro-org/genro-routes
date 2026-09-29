# Genro Routes

<p align="center">
  <img src="_static/logo.png" alt="Genro Routes Logo" width="200"/>
</p>

**Genro Routes** is a **transport-agnostic routing engine** that decouples method routing from how those methods are exposed. Define your handlers once, then expose them via HTTP, CLI, WebSocket, or any other transport layer.

## Why Transport-Agnostic?

Traditional web frameworks tightly couple routing to HTTP. Genro Routes separates these concerns:

| Layer | Responsibility |
|-------|---------------|
| **genro-routes** | Method registration, hierarchies, plugins, introspection |
| **Transport adapter** | Protocol handling, request/response mapping |

The routing logic lives in your application objects - the transport adapter (like [genro-asgi](https://github.com/genropy/genro-asgi) for HTTP) simply maps external requests to router entries.

## What Does This Enable?

- **Same handlers, multiple transports** - Expose your API via HTTP and CLI without duplication
- **Runtime introspection** - Query available routes, generate documentation, build admin UIs
- **Testability** - Test business logic without HTTP overhead
- **Flexibility** - Swap transports without changing application code

## Use Cases

- **HTTP APIs** - Via [genro-asgi](https://github.com/genropy/genro-asgi) adapter
- **Internal services** - Direct method invocation with plugin pipeline
- **CLI tools** - Map commands to router entries
- **Admin dashboards** - Runtime introspection for dynamic UIs

## Key Features

- **One class, one router** - Every `RoutingClass` owns exactly one isolated router with its own plugin stack, exposed as the `route` property
- **Hierarchical organization** - Build router trees with `add_branches()` and `/` path traversal
- **Composable plugins** - Hook into decoration and handler execution with `BasePlugin`
- **Plugin inheritance** - Plugins propagate automatically from parent to child routers
- **Flexible registration** - Use `@route` decorator with prefixes, metadata, and explicit names
- **Runtime configuration** - Configure plugins with `routing.configure()` using target syntax
- **Comprehensive test suite** - Full coverage across all features

## Quick Example

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

## Documentation Sections

```{toctree}
:maxdepth: 2
:caption: Getting Started

installation
quickstart
guide/examples
FAQ
```

```{toctree}
:maxdepth: 2
:caption: User Guide

guide/basic-usage
guide/plugins
guide/plugin-configuration
guide/hierarchies
guide/branches
guide/add-branches-visual-guide
guide/context
guide/why-one-name-per-operation
guide/best-practices
```

```{toctree}
:maxdepth: 2
:caption: Reference

api/reference
api/plugins
ARCHITECTURE
CODE_READING_GUIDE
```

## Installation

```bash
pip install genro-routes
```

For development:

```bash
git clone https://github.com/genropy/genro-routes.git
cd genro-routes
pip install -e ".[all]"
```

## Next Steps

- **New to Genro Routes?** Start with the [Quick Start](quickstart.md)
- **Have questions?** Check the [FAQ](FAQ.md) for common questions and answers
- **Building plugins?** Read the [Plugin Development Guide](guide/plugins.md)
- **Need examples?** Check the [examples directory](https://github.com/genropy/genro-routes/tree/main/examples)

## Project Status

Genro Routes is currently in **beta**. The core API is stable with complete documentation.

- **Python Support**: 3.11, 3.12, 3.13
- **License**: Apache 2.0

## Contributing

Contributions are welcome! Please open an issue or pull request on [GitHub](https://github.com/genropy/genro-routes).

## Indices and Tables

- {ref}`genindex`
- {ref}`modindex`
- {ref}`search`
