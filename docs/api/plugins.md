# Plugin API Reference

<!-- test: test_router_edge_cases.py::test_builtin_plugins_registered -->

This section documents the built-in plugins provided by Genro Routes.

For additional reference, see:

- [Plugin Development Guide](../guide/plugins.md) - Create custom plugins
- [Plugin Configuration Guide](../guide/plugin-configuration.md) - Runtime configuration

## External plugin API

```python
from genro_routes.plugins import BasePlugin, MethodEntry
```

These are the supported extension types: subclass `BasePlugin` and use
`MethodEntry` for handler metadata in plugin hooks. See the
[custom plugin example](../guide/plugins.md#creating-custom-plugins) for
registration and attachment. The legacy `_base_plugin` imports refer to the
same classes and remain available for compatibility.

## PydanticPlugin: strict validation and `_coerce`

<!-- test: test_pydantic_plugin.py::test_strict_by_default_rejects_convertible_string -->

Input validation is strict: an argument must already have the annotated type, so `"12"` for an `int` parameter raises `ValidationError` instead of being converted. An `int` is still accepted for a `float` parameter, which is Pydantic's own strict-mode allowance.

Conversion is requested per call with the reserved keyword `_coerce=True` on the node call. It applies to every parameter of that call and is always consumed by the router, never reaching the handler.

```python
svc.route.node("count")(12)                  # OK
svc.route.node("count")("12")                # ValidationError
svc.route.node("count")("12", _coerce=True)  # OK -> 12
```

| Situation | Result |
|-----------|--------|
| `_coerce=True`, router without the `pydantic` plugin | raises the exception mapped to `not_available` (`NotAvailable` by default) |
| `_coerce=True`, entry with no type hints or `pydantic_disabled=True` | silently ignored: nothing to convert |
| `_coerce=False` or absent | strict validation, the default |

`Annotated[int, Field(strict=False)]` opts a single parameter out of strict mode for every call, with no `_coerce`.

## Auto-Generated Plugin API

```{eval-rst}
.. automodule:: genro_routes.plugins.auth
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: genro_routes.plugins.env
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: genro_routes.plugins.logging
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: genro_routes.plugins.channel
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: plugin_code, plugin_description, plugin_default_param

.. automodule:: genro_routes.plugins.pydantic
   :members:
   :undoc-members:
   :show-inheritance:
```
