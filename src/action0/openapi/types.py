"""
Mapping between schema scalar types and the Python of the generated code.

Three views of one :py:data:`~action0.openapi.ir.TypeExpr`:
:py:func:`scalar_type` builds the IR leaf from a schema's
``type``/``format`` pair, :py:func:`annotation` renders the type
annotation the generated code spells, :py:func:`converter_expr` renders
the expression that turns a decoded JSON value into the typed value
(with :py:func:`imports_for` supplying the imports the rendered text
needs). The request direction needs no counterpart: action0-client
serializes enums, dates and nested dataclasses on its own — only
:py:data:`~action0.openapi.ir.Scalar.UUID` request fields need a
``serialize=str`` argument, which the emitter adds.
"""

from .ir import ArrayType
from .ir import EnumType
from .ir import MapType
from .ir import ModelType
from .ir import Scalar
from .ir import ScalarType
from .ir import TypeExpr
from .names import converter_name

#: schema "format" values that refine a "string" into a richer type;
#: unknown formats (email, uri, ...) simply stay strings, "byte" and
#: "binary" too — base64 text passes through, raw-bytes bodies are not
#: part of the supported subset
_STRING_FORMATS = {
    "date": Scalar.DATE,
    "date-time": Scalar.DATETIME,
    "uuid": Scalar.UUID,
}

#: the annotation each scalar renders as
_ANNOTATIONS = {
    Scalar.STR: "str",
    Scalar.INT: "int",
    Scalar.FLOAT: "float",
    Scalar.BOOL: "bool",
    Scalar.DATE: "datetime.date",
    Scalar.DATETIME: "datetime.datetime",
    Scalar.UUID: "uuid.UUID",
    Scalar.BYTES: "bytes",
    Scalar.ANY: "Any",
}

#: the import statement each scalar's annotation/converter needs
_IMPORTS = {
    Scalar.DATE: "import datetime",
    Scalar.DATETIME: "import datetime",
    Scalar.UUID: "import uuid",
    Scalar.ANY: "from typing import Any",
}


def scalar_type(type_name: "str | None", format_name: "str | None") -> ScalarType:
    """
    Build the scalar for a schema's ``type``/``format`` pair.

    >>> scalar_type("string", None)
    ScalarType(kind=<Scalar.STR: 'str'>)
    >>> scalar_type("string", "date-time")
    ScalarType(kind=<Scalar.DATETIME: 'datetime'>)
    >>> scalar_type(None, None)  # no type: any JSON value is fine
    ScalarType(kind=<Scalar.ANY: 'any'>)

    :param type_name: the schema's ``type`` (a scalar one — ``object``
        and ``array`` are structural and handled by the translation
        stage)
    :param format_name: the schema's ``format``, if any
    :return: the scalar
    :raises ValueError: if the type is not a scalar type
    """
    match type_name:
        case None:
            return ScalarType(Scalar.ANY)
        case "string":
            return ScalarType(_STRING_FORMATS.get(format_name or "", Scalar.STR))
        case "integer":
            return ScalarType(Scalar.INT)
        case "number":
            return ScalarType(Scalar.FLOAT)
        case "boolean":
            return ScalarType(Scalar.BOOL)
    raise ValueError(f"not a scalar schema type: {type_name!r}")


def annotation(t: TypeExpr, *, optional: bool = False) -> str:
    """
    Render the type annotation generated code uses for a type.

    >>> annotation(ArrayType(ModelType("Pet")))
    'list[Pet]'
    >>> annotation(MapType(ScalarType(Scalar.DATE)), optional=True)
    'dict[str, datetime.date] | None'

    :param t: the type
    :param optional: whether to allow ``None`` (optional or nullable
        fields)
    :return: the annotation text
    """
    match t:
        case ScalarType(kind=kind):
            rendered = _ANNOTATIONS[kind]
        case ArrayType(item=item):
            rendered = f"list[{annotation(item)}]"
        case MapType(value=value):
            rendered = f"dict[str, {annotation(value)}]"
        case ModelType(name=name) | EnumType(name=name):
            rendered = name
    return f"{rendered} | None" if optional else rendered


def imports_for(t: TypeExpr) -> frozenset[str]:
    """
    Return the import statements a type's annotation and converter need.

    Imports of generated models and enums are not included — where they
    live relative to the rendered module is the emitter's business.

    >>> sorted(imports_for(MapType(ScalarType(Scalar.UUID))))
    ['import uuid']

    :param t: the type
    :return: the import statements
    """
    match t:
        case ScalarType(kind=kind):
            statement = _IMPORTS.get(kind)
            return frozenset([statement] if statement else [])
        case ArrayType(item=inner) | MapType(value=inner):
            return imports_for(inner)
        case ModelType() | EnumType():
            return frozenset()


def needs_conversion(t: TypeExpr) -> bool:
    """
    Whether a decoded JSON value of this type needs converting at all.

    Plain scalars come out of the JSON decoder ready to use; dates,
    UUIDs, enums and models need an expression around them.

    >>> needs_conversion(ArrayType(ScalarType(Scalar.STR)))
    False
    >>> needs_conversion(ArrayType(EnumType("Status")))
    True

    :param t: the type
    :return: whether :py:func:`converter_expr` is more than a
        pass-through
    """
    match t:
        case ScalarType(kind=kind):
            return kind in (Scalar.DATE, Scalar.DATETIME, Scalar.UUID)
        case ArrayType(item=inner) | MapType(value=inner):
            return needs_conversion(inner)
        case ModelType() | EnumType():
            return True


def converter_expr(t: TypeExpr, source: str, *, _depth: int = 0) -> str:
    """
    Render the expression converting a decoded JSON value to a type.

    >>> converter_expr(ScalarType(Scalar.STR), 'data["name"]')
    'data["name"]'
    >>> converter_expr(EnumType("Status"), 'data["status"]')
    'Status(data["status"])'
    >>> converter_expr(ArrayType(ModelType("Pet")), 'data["items"]')
    '[pet_from_json(item) for item in data["items"]]'

    :param t: the type
    :param source: the expression yielding the decoded JSON value
    :param _depth: internal — nesting level, used to keep comprehension
        variables of nested arrays/maps apart
    :return: the converting expression (``source`` itself when nothing
        needs converting)
    """
    if not needs_conversion(t):
        return source
    match t:
        case ScalarType(kind=Scalar.DATE):
            return f"datetime.date.fromisoformat({source})"
        case ScalarType(kind=Scalar.DATETIME):
            return f"datetime.datetime.fromisoformat({source})"
        case ScalarType(kind=Scalar.UUID):
            return f"uuid.UUID({source})"
        case EnumType(name=name):
            return f"{name}({source})"
        case ModelType(name=name):
            return f"{converter_name(name)}({source})"
        case ArrayType(item=item):
            var = "item" if _depth == 0 else f"item{_depth}"
            inner = converter_expr(item, var, _depth=_depth + 1)
            return f"[{inner} for {var} in {source}]"
        case MapType(value=value):
            key, var = ("key", "value") if _depth == 0 else (f"key{_depth}", f"value{_depth}")
            inner = converter_expr(value, var, _depth=_depth + 1)
            return f"{{{key}: {inner} for {key}, {var} in {source}.items()}}"
    raise AssertionError(f"unhandled type: {t!r}")  # pragma: no cover
