# API reference

Everything public is importable from the package root:

```python
from action0.openapi import load_schema, RefResolver, SchemaError
from action0.openapi import Api, Model, EnumModel, Field, OperationIR, Param, Body
from action0.openapi import SecurityScheme
```

## Schema loading

```{eval-rst}
.. automodule:: action0.openapi.loader
   :members:
```

## Reference resolution

```{eval-rst}
.. automodule:: action0.openapi.resolve
   :members:
```

## Translation

```{eval-rst}
.. automodule:: action0.openapi.parse
   :members:
```

## Intermediate representation

```{eval-rst}
.. automodule:: action0.openapi.ir
   :members:
```

## Name mangling

```{eval-rst}
.. automodule:: action0.openapi.names
   :members:
```

## Type mapping

```{eval-rst}
.. automodule:: action0.openapi.types
   :members:
```

## Code emission

```{eval-rst}
.. automodule:: action0.openapi.render
   :members:
```

## Errors

```{eval-rst}
.. automodule:: action0.openapi.errors
   :members:
```
