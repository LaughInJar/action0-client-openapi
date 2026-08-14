"""
Generate fully typed :py:mod:`action0.client` API clients from OpenAPI schemas.

Where `action0-client <https://laughinjar.github.io/action0-client/>`_
lets you *hand-write* an API as typed operation dataclasses, this package
*generates* that code from an OpenAPI schema file: one operation class per
endpoint, plus the model classes their results are parsed into. The
generated code is plain, readable ``action0-client`` code — it depends on
``action0-client`` only, not on this package, and runs on whichever
backend (sync, asyncio, Twisted, ...) is plugged in.

The pipeline: :py:func:`load_schema` reads the document,
:py:class:`RefResolver` resolves local ``$ref`` pointers, the translation
stage produces the intermediate representation around :py:class:`Api`,
and the emitter renders it as a Python package. Input problems raise
:py:class:`SchemaError`.
"""

from .errors import SchemaError
from .ir import Api
from .ir import ArrayType
from .ir import Body
from .ir import BodyKind
from .ir import EnumModel
from .ir import EnumType
from .ir import Field
from .ir import MapType
from .ir import Model
from .ir import ModelType
from .ir import OperationIR
from .ir import Param
from .ir import ParamLocation
from .ir import ResponseKind
from .ir import Scalar
from .ir import ScalarType
from .ir import SecurityKind
from .ir import SecurityScheme
from .ir import TypeExpr
from .loader import load_schema
from .parse import parse_api
from .resolve import RefResolver

__version__: str = "0.1.0"

__all__ = [
    "Api",
    "ArrayType",
    "Body",
    "BodyKind",
    "EnumModel",
    "EnumType",
    "Field",
    "MapType",
    "Model",
    "ModelType",
    "OperationIR",
    "Param",
    "ParamLocation",
    "RefResolver",
    "ResponseKind",
    "Scalar",
    "ScalarType",
    "SchemaError",
    "SecurityKind",
    "SecurityScheme",
    "TypeExpr",
    "load_schema",
    "parse_api",
]
