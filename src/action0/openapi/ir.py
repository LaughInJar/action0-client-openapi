"""
The intermediate representation (IR) between OpenAPI and generated code.

The translation stage turns a loaded OpenAPI document into one
:py:class:`Api` value — plain, frozen dataclasses that carry everything
the code emitter needs and nothing else: models with their fields,
enums, operations with parameters and body, and the security schemes.
All names in the IR are the final *Python* names (classes PascalCase,
fields snake_case, path templates rewritten to match the field names);
the original schema spellings survive as the ``wire_name``.

Keeping this layer independent of both the OpenAPI document shape and
the emitted source text is deliberate: a future dynamic mode (building
operation classes at import time instead of writing files) would
consume the very same :py:class:`Api`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from dataclasses import field
from typing import TypeAlias


class Scalar(enum.Enum):
    """The scalar types generated code distinguishes."""

    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DATE = "date"
    DATETIME = "datetime"
    UUID = "uuid"
    BYTES = "bytes"
    #: free-form values that stay whatever the JSON decoder produced
    ANY = "any"


@dataclass(frozen=True)
class ScalarType:
    """A scalar type."""

    kind: Scalar


@dataclass(frozen=True)
class ArrayType:
    """A JSON array — ``list[item]`` in generated code."""

    item: TypeExpr


@dataclass(frozen=True)
class MapType:
    """
    A JSON object with ``additionalProperties`` only — ``dict[str, value]``
    in generated code.
    """

    value: TypeExpr


@dataclass(frozen=True)
class ModelType:
    """A reference to a generated model dataclass, by Python class name."""

    name: str


@dataclass(frozen=True)
class EnumType:
    """A reference to a generated enum class, by Python class name."""

    name: str


@dataclass(frozen=True)
class UnionType:
    """
    A reference to a generated union alias, by Python name.

    The members ride along so the type logic (annotations, whether a
    conversion is needed at all) works without looking the union up.

    :param name: the alias name
    :param members: the union's member types, in schema order
    """

    name: str
    members: tuple[TypeExpr, ...]


#: any type generated code can express
TypeExpr: TypeAlias = "ScalarType | ArrayType | MapType | ModelType | EnumType | UnionType"


@dataclass(frozen=True)
class Field:
    """
    One property of a model, or one field of a request body.

    :param name: the Python field name
    :param wire_name: the property name in the JSON/form payload
    :param type: the field's type
    :param required: whether the payload must contain the property
    :param nullable: whether ``null`` is a legal payload value
    :param default: the schema's default value (scalars only), or
        ``None`` when the schema declares none
    :param description: the schema's description, for docstrings
    """

    name: str
    wire_name: str
    type: TypeExpr
    required: bool
    nullable: bool = False
    default: object | None = None
    description: str | None = None


@dataclass(frozen=True)
class Model:
    """
    One generated dataclass model.

    :param name: the Python class name
    :param fields: the model's fields; the ones rendered without a
        dataclass default (required and not nullable) come first
    :param description: the schema's description, for the docstring
    """

    name: str
    fields: tuple[Field, ...]
    description: str | None = None


@dataclass(frozen=True)
class EnumModel:
    """
    One generated :py:class:`enum.Enum` class.

    :param name: the Python class name
    :param base: the scalar kind of the values (:py:attr:`Scalar.STR`
        or :py:attr:`Scalar.INT`)
    :param members: ``(member_name, value)`` pairs
    :param description: the schema's description, for the docstring
    """

    name: str
    base: Scalar
    members: tuple[tuple[str, str | int], ...]
    description: str | None = None


class UnionCheck(enum.Enum):
    """How one union member is recognized in a decoded payload."""

    #: an ``isinstance`` check against a JSON-level Python type
    JSON_TYPE = "json-type"
    #: the discriminator property equals a tag value
    TAG = "tag"
    #: a required key only this member has is present
    KEY = "key"


@dataclass(frozen=True)
class UnionCase:
    """
    One branch of a union's dispatching converter.

    :param member: the member built when the check matches
    :param check: how the member is recognized
    :param value: the check's argument — the Python type name (e.g.
        ``str``, ``(int, float)``) for :py:attr:`UnionCheck.JSON_TYPE`,
        the tag value for :py:attr:`UnionCheck.TAG`, the property name
        for :py:attr:`UnionCheck.KEY`
    """

    member: TypeExpr
    check: UnionCheck
    value: str


@dataclass(frozen=True)
class UnionModel:
    """
    One generated union: a type alias plus a dispatching converter.

    :param name: the Python alias name
    :param members: the member types, in schema order
    :param cases: the dispatch branches, in the order they are emitted
    :param discriminator: the wire property carrying the tag (for
        :py:attr:`UnionCheck.TAG` cases)
    :param description: the schema's description, for the docstring
    """

    name: str
    members: tuple[TypeExpr, ...]
    cases: tuple[UnionCase, ...]
    discriminator: str | None = None
    description: str | None = None


class ParamLocation(enum.Enum):
    """Where an operation parameter is placed."""

    PATH = "path"
    QUERY = "query"
    HEADER = "header"


@dataclass(frozen=True)
class Param:
    """
    One path, query or header parameter of an operation.

    :param name: the Python field name
    :param wire_name: the parameter name on the wire
    :param location: where the parameter goes
    :param type: the parameter's type
    :param required: whether the parameter must be sent
    :param nullable: whether the schema allows ``null``
    :param default: the schema's default value (scalars only), or
        ``None`` when the schema declares none
    :param description: the parameter's description, for docstrings
    """

    name: str
    wire_name: str
    location: ParamLocation
    type: TypeExpr
    required: bool
    nullable: bool = False
    default: object | None = None
    description: str | None = None


class BodyKind(enum.Enum):
    """How an operation's request body is expressed as fields."""

    #: an inline JSON object schema, one ``json_field()`` per property
    JSON_FIELDS = "json-fields"
    #: a referenced/array/scalar JSON schema, one ``json_body()`` field
    JSON_BODY = "json-body"
    #: ``application/x-www-form-urlencoded``, one ``form_field()`` per property
    FORM_FIELDS = "form-fields"


@dataclass(frozen=True)
class Body:
    """
    An operation's request body.

    :param kind: how the body maps to operation fields
    :param fields: the properties (for :py:attr:`BodyKind.JSON_FIELDS`
        and :py:attr:`BodyKind.FORM_FIELDS`; empty otherwise)
    :param type: the whole-body type (for :py:attr:`BodyKind.JSON_BODY`;
        ``None`` otherwise)
    :param required: whether the request must carry the body
    """

    kind: BodyKind
    fields: tuple[Field, ...] = ()
    type: TypeExpr | None = None
    required: bool = True


class ResponseKind(enum.Enum):
    """What an operation's success response parses into."""

    #: a JSON payload loaded into a typed value
    MODEL = "model"
    #: no content (e.g. 204) — the operation returns ``None``
    NONE = "none"
    #: non-JSON content returned as raw ``bytes``
    BYTES = "bytes"


@dataclass(frozen=True)
class OperationIR:
    """
    One generated operation class.

    :param class_name: the Python class name
    :param method: the HTTP method, uppercase
    :param path_template: the path with ``{placeholder}`` names already
        rewritten to the Python parameter names
    :param wire_path: the original path as spelled in the schema
    :param params: the path/query/header parameters
    :param body: the request body, if any
    :param response_kind: what the success response parses into
    :param response_type: the parsed type (for
        :py:attr:`ResponseKind.MODEL`; ``None`` otherwise)
    :param summary: the schema's summary, for the docstring
    :param description: the schema's description, for the docstring
    :param tag: the operation's first ``tags`` entry, if any — the
        grouping key when the generated package splits operations into
        per-tag modules
    """

    class_name: str
    method: str
    path_template: str
    wire_path: str
    params: tuple[Param, ...] = ()
    body: Body | None = None
    response_kind: ResponseKind = ResponseKind.NONE
    response_type: TypeExpr | None = None
    summary: str | None = None
    description: str | None = None
    tag: str | None = None


class SecurityKind(enum.Enum):
    """The supported OpenAPI security scheme kinds."""

    HTTP_BEARER = "http-bearer"
    HTTP_BASIC = "http-basic"
    API_KEY_HEADER = "api-key-header"
    API_KEY_QUERY = "api-key-query"


@dataclass(frozen=True)
class SecurityScheme:
    """
    One security scheme, turned into client credentials.

    :param kind: the scheme kind
    :param param_name: the Python name of the credential parameter on
        the generated client's ``__init__`` (e.g. ``token``, ``api_key``)
    :param wire_name: the header or query parameter carrying the
        credential (``None`` for HTTP bearer/basic, which fix the
        ``Authorization`` header)
    """

    kind: SecurityKind
    param_name: str
    wire_name: str | None = None


@dataclass(frozen=True)
class Api:
    """
    Everything the emitter needs to generate one client package.

    :param title: the schema's ``info.title``
    :param version: the schema's ``info.version``
    :param base_url: the default base URL from ``servers``, if any
    :param models: the models and enums, in schema order
    :param operations: the operations, in path order
    :param security: the security schemes becoming client credentials
    :param warnings: notes about constructs the translation flattened
        or skipped (printed by the CLI, documented in the generated
        code where possible)
    """

    title: str
    version: str
    base_url: str | None = None
    models: tuple[Model | EnumModel | UnionModel, ...] = field(default=())
    operations: tuple[OperationIR, ...] = field(default=())
    security: tuple[SecurityScheme, ...] = field(default=())
    warnings: tuple[str, ...] = field(default=())
