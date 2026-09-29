# Installation

## Requirements

- Python 3.11 or higher
- pip package manager

## From PyPI

```bash
pip install genro-routes
```

## With Optional Dependencies

### Development Tools

For development with all optional dependencies:

```bash
pip install genro-routes[dev]
```

### Documentation Tools

For building documentation:

```bash
pip install genro-routes[docs]
```

### All Dependencies

To install everything:

```bash
pip install genro-routes[all]
```

## From Source

For development or to use the latest unreleased features:

```bash
git clone https://github.com/genropy/genro-routes.git
cd genro-routes
pip install -e ".[all]"
```

This installs Genro Routes in editable mode with all optional dependencies.

## Verify Installation

<!-- test: test_router_basic.py::test_orders_quick_example -->

Test your installation:

```python
python -c "from genro_routes import Router, RoutingClass, route; print('Genro Routes installed successfully!')"
```

## Next Steps

- [Quick Start Guide](quickstart.md) - Get started in 5 minutes
- [Basic Usage Guide](guide/basic-usage.md) - Learn the fundamentals
- [Plugin Guide](guide/plugins.md) - Extend Genro Routes with plugins
