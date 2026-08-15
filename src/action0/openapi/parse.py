"""
Translating an OpenAPI 3.x document into the intermediate representation.

:py:func:`parse_api` walks a loaded schema document and produces the
:py:class:`~action0.openapi.ir.Api` the emitter renders: the
``components/schemas`` become models and enums (inline schemas are
synthesized into named models on the way), the ``paths`` become
operations with parameters, body and response type, and the referenced
``securitySchemes`` become client credentials. Everything outside the
supported subset raises :py:class:`~action0.openapi.errors.SchemaError`
naming the offending schema location; lesser omissions (an unsupported
security scheme, an ignored ``additionalProperties``) are collected as
:py:attr:`~action0.openapi.ir.Api.warnings`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
from .ir import UnionCase
from .ir import UnionCheck
from .ir import UnionModel
from .ir import UnionType
from .names import RESERVED_OPERATION_FIELDS
from .names import NameRegistry
from .names import class_name
from .names import constant_name
from .names import field_name
from .names import operation_class_name
from .names import path_placeholders
from .names import rewrite_path
from .resolve import RefResolver
from .types import scalar_type

#: the pointer prefix of reusable schema components
_SCHEMAS_PREFIX = "#/components/schemas/"

#: the HTTP methods a path item may carry (the other keys are
#: parameters, servers, summary, ...)
_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

#: class names generated modules import from elsewhere — pre-claimed so
#: no model or operation class can shadow them
_RESERVED_CLASS_NAMES = (
    "Any",
    "APIClient",
    "BackendT_co",
    "JsonOperation",
    "Method",
    "Operation",
    "Request",
    "Response",
)

#: __init__ parameters of the generated client — credential names must
#: not collide with them
_RESERVED_CREDENTIAL_NAMES = frozenset({"self", "backend", "base_url", "headers"})

#: locations of a parameter and their IR counterpart ("cookie" is
#: deliberately absent: unsupported)
_PARAM_LOCATIONS = {
    "path": ParamLocation.PATH,
    "query": ParamLocation.QUERY,
    "header": ParamLocation.HEADER,
}


def parse_api(document: Mapping[str, Any]) -> Api:
    """
    Translate a loaded OpenAPI 3.x document into an :py:class:`Api`.

    :param document: the document, as returned by
        :py:func:`~action0.openapi.loader.load_schema`
    :return: the intermediate representation
    :raises SchemaError: for constructs outside the supported subset
    """
    return _Parser(document).parse()


def _is_null_schema(node: Any) -> bool:
    """
    Whether a subschema matches only ``null`` (the 3.1 idiom inside
    ``oneOf``/``anyOf``).

    :param node: the subschema
    :return: whether it is exactly ``{"type": "null"}``
    """
    return isinstance(node, Mapping) and dict(node) == {"type": "null"}


class _Parser:
    """
    One document's translation state.

    :param document: the loaded schema document
    """

    def __init__(self, document: Mapping[str, Any]) -> None:
        self._document = document
        self._resolver = RefResolver(document)
        # models and operations end up in one generated package whose
        # modules import each other, so all class names share one scope
        self._class_names = NameRegistry()
        for reserved in _RESERVED_CLASS_NAMES:
            self._class_names.claim(reserved)
        # translated components by component name; None marks "being
        # translated right now" and makes reference cycles that need no
        # forward declaration (model -> model) work
        self._components: dict[str, tuple[TypeExpr, bool] | None] = {}
        self._models: list[Model | EnumModel | UnionModel] = []
        self._operations: list[OperationIR] = []
        self._warnings: list[str] = []

    def parse(self) -> Api:
        """
        Run the translation.

        :return: the intermediate representation
        """
        info = self._document.get("info") or {}
        schemas = (self._document.get("components") or {}).get("schemas") or {}
        for name in schemas:
            self._component_type(name, where=f"components.schemas.{name}")
        for path, path_item in (self._document.get("paths") or {}).items():
            self._parse_path(path, path_item)
        return Api(
            title=str(info.get("title", "API")),
            version=str(info.get("version", "0")),
            base_url=self._base_url(),
            models=tuple(self._models),
            operations=tuple(self._operations),
            security=self._parse_security(),
            warnings=tuple(self._warnings),
        )

    def _base_url(self) -> str | None:
        """
        The default base URL: the first server, variables at their
        defaults.

        :return: the URL, or ``None`` when the document names no servers
        """
        servers = self._document.get("servers") or []
        if not servers:
            return None
        url = str(servers[0].get("url", ""))
        for name, variable in (servers[0].get("variables") or {}).items():
            url = url.replace("{" + name + "}", str(variable.get("default", "")))
        return url or None

    # ------------------------------------------------------------------
    # schemas
    # ------------------------------------------------------------------

    def _component_type(self, name: str, where: str) -> tuple[TypeExpr, bool]:
        """
        Translate the schema component ``name`` (once).

        :param name: the component name under ``components/schemas``
        :param where: the schema location, for error messages
        :return: the component's type and whether it is nullable
        """
        if name in self._components:
            translated = self._components[name]
            if translated is None:
                # a reference cycle that is not simply model -> model
                # (e.g. an array component containing itself) has no
                # class name to break it with
                raise SchemaError(f"{where}: unsupported reference cycle through {name!r}")
            return translated
        node = self._resolver.lookup(_SCHEMAS_PREFIX + name)
        if not isinstance(node, Mapping):
            raise SchemaError(f"components.schemas.{name}: the schema must be an object")
        component_where = f"components.schemas.{name}"
        plain, nullable = self._split_nullable(node, where=component_where)
        if "allOf" in plain and (len(plain["allOf"]) != 1 or plain.get("properties")):
            # flatten before the model check, so an allOf component gets
            # the same self-reference pre-registration as a plain model
            plain, parts_nullable = self._merge_all_of(plain, where=component_where)
            nullable = nullable or parts_nullable
        if self._is_model_schema(plain):
            # model components register their class *before* their
            # properties are walked, so self references (Pet -> Pet)
            # resolve to the class instead of recursing forever
            cls = self._class_names.claim(class_name(name))
            self._components[name] = (ModelType(cls), nullable)
            self._model_type(plain, name=cls, where=component_where)
            return ModelType(cls), nullable
        self._components[name] = None
        result = self._schema_type(node, context=name, where=component_where)
        self._components[name] = result
        return result

    @staticmethod
    def _is_model_schema(plain: Mapping[str, Any]) -> bool:
        """
        Whether a (nullability-stripped) schema is a plain model object.

        :param plain: the schema node
        :return: whether it is an object schema with properties, without
            composition keywords
        """
        if any(keyword in plain for keyword in ("$ref", "allOf", "oneOf", "anyOf", "enum")):
            return False
        type_name = plain.get("type")
        return bool(plain.get("properties")) and (type_name == "object" or type_name is None)

    def _merge_all_of(self, node: Mapping[str, Any], *, where: str) -> tuple[dict[str, Any], bool]:
        """
        Flatten an ``allOf`` composition into one object schema.

        The common inheritance pattern — a ``$ref`` to the base plus an
        object with the extra properties — merges by uniting the parts'
        ``properties`` and ``required`` (properties defined by the node
        itself, next to ``allOf``, count as one more part). Conflicting
        definitions of the same property and non-object subschemas are
        outside the supported subset.

        :param node: the schema node with the ``allOf`` keyword
        :param where: the schema location, for error messages
        :return: the merged object schema, and whether any part was
            nullable
        :raises SchemaError: on non-object or conflicting subschemas
        """
        properties: dict[str, Any] = {}
        required: list[str] = []
        description = node.get("description")
        nullable = False
        parts = list(node["allOf"])
        if node.get("properties") or node.get("required"):
            parts.append(
                {key: node[key] for key in ("type", "properties", "required") if key in node}
            )
        for index, raw in enumerate(parts):
            part_where = f"{where}.allOf.{index}"
            if not isinstance(raw, Mapping):
                raise SchemaError(f"{part_where}: the subschema must be an object")
            part = self._resolver.deref(raw)
            part, part_nullable = self._split_nullable(part, where=part_where)
            nullable = nullable or part_nullable
            if "allOf" in part:
                part, deep_nullable = self._merge_all_of(part, where=part_where)
                nullable = nullable or deep_nullable
            if (
                any(keyword in part for keyword in ("oneOf", "anyOf", "enum"))
                or part.get("type", "object") != "object"
            ):
                raise SchemaError(
                    f"{part_where}: allOf can only merge object schemas —"
                    " flatten the schema or drop the non-object subschema"
                )
            for name, prop in (part.get("properties") or {}).items():
                if name in properties and properties[name] != prop:
                    raise SchemaError(
                        f"{where}: the allOf subschemas define the property {name!r}"
                        " differently — flatten the schema"
                    )
                properties.setdefault(name, prop)
            for name in part.get("required") or ():
                if name not in required:
                    required.append(name)
            if description is None:
                description = part.get("description")
        merged: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            merged["required"] = required
        if description is not None:
            merged["description"] = description
        return merged, nullable

    def _schema_type(
        self, node: Mapping[str, Any], *, context: str, where: str
    ) -> tuple[TypeExpr, bool]:
        """
        Translate one schema node into a type.

        :param node: the schema node
        :param context: the name inline models/enums are derived from
        :param where: the schema location, for error messages
        :return: the type and whether it is nullable
        """
        if "$ref" in node:
            ref = node["$ref"]
            if isinstance(ref, str) and ref.startswith(_SCHEMAS_PREFIX):
                name = RefResolver.ref_name(ref)
                return self._component_type(name, where=where)
            # other local pointers are legal, just anonymous: translate
            # the target as an inline schema
            target = self._resolver.deref(node)
            return self._schema_type(target, context=context, where=where)

        node, nullable = self._split_nullable(node, where=where)

        if "allOf" in node:
            parts = list(node["allOf"])
            if len(parts) == 1 and not node.get("properties"):
                # a single subschema of any kind simply unwraps
                inner, inner_nullable = self._schema_type(parts[0], context=context, where=where)
                return inner, nullable or inner_nullable
            merged, parts_nullable = self._merge_all_of(node, where=where)
            inner, inner_nullable = self._schema_type(merged, context=context, where=where)
            return inner, nullable or parts_nullable or inner_nullable
        for keyword in ("oneOf", "anyOf"):
            if keyword in node:
                parts = [part for part in node[keyword] if not _is_null_schema(part)]
                nullable = nullable or len(parts) != len(node[keyword])
                if not parts:
                    raise SchemaError(f"{where}: {keyword} with only null alternatives")
                if len(parts) == 1:
                    # the common "X or null" idiom: unwrap the one subschema
                    inner, inner_nullable = self._schema_type(
                        parts[0], context=context, where=where
                    )
                    return inner, nullable or inner_nullable
                return (
                    self._union_type(node, parts, keyword, context=context, where=where),
                    nullable,
                )

        if "enum" in node:
            return self._enum_type(node, context=context, where=where), nullable

        type_name = node.get("type")
        if type_name == "array":
            items = node.get("items")
            if isinstance(items, Mapping):
                item_type, _ = self._schema_type(
                    items, context=f"{context} item", where=f"{where}.items"
                )
            else:
                item_type = ScalarType(Scalar.ANY)
            return ArrayType(item_type), nullable
        if type_name == "object" or (type_name is None and "properties" in node):
            return self._object_type(node, context=context, where=where), nullable
        if type_name is None and not node:
            # a completely empty schema accepts any JSON value
            return ScalarType(Scalar.ANY), nullable
        try:
            return scalar_type(type_name, node.get("format")), nullable
        except ValueError as error:
            raise SchemaError(f"{where}: {error}") from error

    def _union_type(
        self,
        node: Mapping[str, Any],
        parts: list[Any],
        keyword: str,
        *,
        context: str,
        where: str,
    ) -> TypeExpr:
        """
        Translate a multi-alternative ``oneOf``/``anyOf`` into a union.

        The members must be distinguishable in a decoded payload — by
        JSON type, by the ``discriminator`` tag, or by a required key
        unique to the member. Indistinguishable unions degrade to an
        untyped value with a warning.

        :param node: the schema node carrying the keyword
        :param parts: the non-null alternatives
        :param keyword: ``oneOf`` or ``anyOf``, for messages
        :param context: the name the union alias is derived from
        :param where: the schema location, for error messages
        :return: the union reference, or ``Any`` when it degrades
        """
        name = self._class_names.claim(class_name(context))
        members = []
        component_names: list[str | None] = []
        for index, part in enumerate(parts):
            part_where = f"{where}.{keyword}.{index}"
            if not isinstance(part, Mapping):
                raise SchemaError(f"{part_where}: the alternative must be a schema")
            member, _ = self._schema_type(
                part, context=f"{name} option {index + 1}", where=part_where
            )
            members.append(member)
            # remember the component name of $ref alternatives: it is the
            # implicit discriminator tag
            ref = part.get("$ref")
            component_names.append(
                RefResolver.ref_name(ref)
                if isinstance(ref, str) and ref.startswith(_SCHEMAS_PREFIX)
                else None
            )
        dispatch = self._union_cases(node, members, component_names, where=where)
        if dispatch is None:
            self._warnings.append(
                f"{where}: the {keyword} alternatives cannot be told apart in a payload"
                " (no JSON-type difference, discriminator or unique required key) —"
                " generated as an untyped value"
            )
            return ScalarType(Scalar.ANY)
        cases, discriminator = dispatch
        self._models.append(
            UnionModel(
                name=name,
                members=tuple(members),
                cases=cases,
                discriminator=discriminator,
                description=node.get("description"),
            )
        )
        return UnionType(name, tuple(members))

    #: the isinstance() bucket of each scalar in a decoded payload
    _JSON_TYPES = {
        Scalar.STR: "str",
        Scalar.DATE: "str",
        Scalar.DATETIME: "str",
        Scalar.UUID: "str",
        Scalar.BYTES: "str",
        Scalar.INT: "int",
        Scalar.FLOAT: "float",
        Scalar.BOOL: "bool",
    }

    def _json_type(self, member: TypeExpr) -> str | None:
        """
        The ``isinstance`` bucket of one union member.

        :param member: the member type
        :return: the Python type name a decoded value of the member
            satisfies, or ``None`` when the member matches anything
        """
        match member:
            case ScalarType(kind=kind):
                return self._JSON_TYPES.get(kind)
            case ArrayType():
                return "list"
            case MapType() | ModelType():
                return "dict"
            case EnumType(name=name):
                for model in self._models:
                    if isinstance(model, EnumModel) and model.name == name:
                        return "str" if model.base is Scalar.STR else "int"
                return None
            case UnionType():
                return None

    def _union_cases(
        self,
        node: Mapping[str, Any],
        members: list[TypeExpr],
        component_names: list[str | None],
        *,
        where: str,
    ) -> tuple[tuple[UnionCase, ...], str | None] | None:
        """
        Compute the dispatch of a union's converter.

        The ladder: members alone in their JSON-type bucket dispatch by
        ``isinstance`` (``bool`` before ``int`` — bools are ints);
        several object members dispatch by the ``discriminator`` tag or,
        without one, by a required key unique to each member.

        :param node: the schema node carrying the union
        :param members: the translated members
        :param component_names: the schema component name per member
            (``None`` for inline alternatives)
        :param where: the schema location, for error messages
        :return: the cases and the discriminator property, or ``None``
            when the members are indistinguishable
        """
        buckets: dict[str, list[int]] = {}
        for index, member in enumerate(members):
            json_type = self._json_type(member)
            if json_type is None:
                return None
            buckets.setdefault(json_type, []).append(index)
        cases = []
        for json_type in ("bool", "int", "float", "str", "list", "dict"):
            indexes = buckets.get(json_type, [])
            if len(indexes) == 1 and json_type != "dict":
                cases.append(
                    UnionCase(
                        member=members[indexes[0]], check=UnionCheck.JSON_TYPE, value=json_type
                    )
                )
            elif len(indexes) > 1 and json_type != "dict":
                return None
        object_indexes = buckets.get("dict", [])
        if len(object_indexes) == 1:
            cases.append(
                UnionCase(
                    member=members[object_indexes[0]], check=UnionCheck.JSON_TYPE, value="dict"
                )
            )
            return tuple(cases), None
        if not object_indexes:
            return tuple(cases), None
        if not all(isinstance(members[index], ModelType) for index in object_indexes):
            return None
        discriminator = node.get("discriminator") or {}
        tag_property = discriminator.get("propertyName")
        if tag_property:
            object_cases = self._tag_cases(
                discriminator, members, component_names, object_indexes, where=where
            )
            if object_cases is None:
                return None
            return tuple(cases) + object_cases, str(tag_property)
        object_cases = self._key_cases(members, object_indexes)
        if object_cases is None:
            return None
        return tuple(cases) + object_cases, None

    def _tag_cases(
        self,
        discriminator: Mapping[str, Any],
        members: list[TypeExpr],
        component_names: list[str | None],
        object_indexes: list[int],
        *,
        where: str,
    ) -> tuple[UnionCase, ...] | None:
        """
        Build the discriminator-tag cases of a union's object members.

        Explicit ``mapping`` entries (tag to reference) win; a member
        without one falls back to its component name, the spec's
        implicit convention.

        :param discriminator: the ``discriminator`` node
        :param members: the translated members
        :param component_names: the schema component name per member
        :param object_indexes: which members are object models
        :param where: the schema location, for error messages
        :return: the cases, or ``None`` when a member has no tag
        """
        tag_by_component = {}
        for tag, reference in (discriminator.get("mapping") or {}).items():
            reference_name = (
                RefResolver.ref_name(reference) if isinstance(reference, str) else str(reference)
            )
            tag_by_component[reference_name] = str(tag)
        cases = []
        for index in object_indexes:
            component = component_names[index]
            if component is None:
                return None
            cases.append(
                UnionCase(
                    member=members[index],
                    check=UnionCheck.TAG,
                    value=tag_by_component.get(component, component),
                )
            )
        return tuple(cases)

    def _key_cases(
        self, members: list[TypeExpr], object_indexes: list[int]
    ) -> tuple[UnionCase, ...] | None:
        """
        Build presence-of-key cases for object members without a
        discriminator.

        Each member needs a *required* property that no other object
        member even declares.

        :param members: the translated members
        :param object_indexes: which members are object models
        :return: the cases, or ``None`` when a member has no such key
        """
        models = {}
        for index in object_indexes:
            member = members[index]
            assert isinstance(member, ModelType)
            for model in self._models:
                if isinstance(model, Model) and model.name == member.name:
                    models[index] = model
                    break
            else:
                return None
        cases = []
        for index in object_indexes:
            other_properties = {
                field.wire_name
                for other, model in models.items()
                if other != index
                for field in model.fields
            }
            unique = [
                field.wire_name
                for field in models[index].fields
                if field.required and field.wire_name not in other_properties
            ]
            if not unique:
                return None
            cases.append(UnionCase(member=members[index], check=UnionCheck.KEY, value=unique[0]))
        return tuple(cases)

    def _split_nullable(
        self, node: Mapping[str, Any], *, where: str
    ) -> tuple[Mapping[str, Any], bool]:
        """
        Normalize the two nullability spellings.

        OpenAPI 3.0 says ``nullable: true``, 3.1 puts ``"null"`` into a
        type array. Both come out as a plain node plus the flag.

        :param node: the schema node
        :param where: the schema location, for error messages
        :return: the node without nullability markers, and the flag
        """
        nullable = bool(node.get("nullable", False))
        type_name = node.get("type")
        if isinstance(type_name, list):
            remaining = [entry for entry in type_name if entry != "null"]
            nullable = nullable or len(remaining) != len(type_name)
            plain = dict(node)
            if len(remaining) == 1:
                plain["type"] = remaining[0]
            elif not remaining:
                del plain["type"]
            elif "oneOf" in plain or "anyOf" in plain:
                # a type array next to an explicit union: too entangled
                self._warnings.append(
                    f"{where}: multi-type {type_name!r} is treated as an untyped value"
                )
                del plain["type"]
            else:
                # several concrete types at once: the 3.1 shorthand for a
                # union of bare types (structural keywords like items or
                # properties do not survive the split)
                del plain["type"]
                plain["oneOf"] = [{"type": entry} for entry in remaining]
            return plain, nullable
        if nullable:
            plain = dict(node)
            del plain["nullable"]
            return plain, True
        return node, False

    def _enum_type(self, node: Mapping[str, Any], *, context: str, where: str) -> TypeExpr:
        """
        Synthesize an enum class for a schema with ``enum`` values.

        :param node: the schema node
        :param context: the name the class is derived from
        :param where: the schema location, for error messages
        :return: the enum reference
        """
        values = [value for value in node["enum"] if value is not None]
        if not values:
            raise SchemaError(f"{where}: enum without usable values")
        if all(isinstance(value, str) for value in values):
            base = Scalar.STR
        elif all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            base = Scalar.INT
        else:
            raise SchemaError(f"{where}: only pure string or pure integer enums are supported")
        member_names = NameRegistry()
        members = tuple((member_names.claim(constant_name(str(value))), value) for value in values)
        name = self._class_names.claim(class_name(context))
        self._models.append(
            EnumModel(name=name, base=base, members=members, description=node.get("description"))
        )
        return EnumType(name)

    def _object_type(self, node: Mapping[str, Any], *, context: str, where: str) -> TypeExpr:
        """
        Translate an object schema: a model, a map, or a free-form dict.

        :param node: the schema node
        :param context: the name an inline model is derived from
        :param where: the schema location, for error messages
        :return: the object's type
        """
        properties = node.get("properties")
        additional = node.get("additionalProperties")
        if properties:
            if isinstance(additional, Mapping) or additional is True:
                self._warnings.append(
                    f"{where}: additionalProperties next to properties is ignored —"
                    " extra payload keys are dropped"
                )
            name = self._class_names.claim(class_name(context))
            return self._model_type(node, name=name, where=where)
        if isinstance(additional, Mapping):
            value_type, _ = self._schema_type(
                additional, context=f"{context} value", where=f"{where}.additionalProperties"
            )
            return MapType(value_type)
        return MapType(ScalarType(Scalar.ANY))

    def _model_type(self, node: Mapping[str, Any], *, name: str, where: str) -> ModelType:
        """
        Synthesize a model dataclass for an object schema with properties.

        :param node: the schema node
        :param name: the already-claimed Python class name
        :param where: the schema location, for error messages
        :return: the model reference
        """
        required = set(node.get("required") or ())
        registry = NameRegistry()
        fields = []
        for wire_name, prop in (node.get("properties") or {}).items():
            if not isinstance(prop, Mapping):
                raise SchemaError(f"{where}.properties.{wire_name}: the property must be a schema")
            prop_type, nullable = self._schema_type(
                prop,
                context=f"{name} {wire_name}",
                where=f"{where}.properties.{wire_name}",
            )
            is_required = wire_name in required
            fields.append(
                Field(
                    name=registry.claim(field_name(wire_name)),
                    wire_name=wire_name,
                    type=prop_type,
                    required=is_required,
                    nullable=nullable,
                    default=None if is_required else self._default_for(prop, prop_type),
                    description=self._description(prop),
                )
            )
        # plain (non-kw_only) dataclasses need default-less fields first;
        # the sort is stable, so schema order survives within the groups
        fields.sort(key=lambda field: not field.required)
        self._models.append(
            Model(name=name, fields=tuple(fields), description=node.get("description"))
        )
        return ModelType(name)

    def _default_for(self, node: Mapping[str, Any], prop_type: TypeExpr) -> object | None:
        """
        The schema default, if generated code can express it as a literal.

        :param node: the schema node
        :param prop_type: the property's translated type
        :return: the default value, or ``None`` for "no default"
        """
        default = node.get("default")
        if default is None:
            return None
        if isinstance(prop_type, ScalarType) and isinstance(default, (str, int, float, bool)):
            return default
        if isinstance(prop_type, EnumType) and isinstance(default, (str, int)):
            return default
        return None

    def _description(self, node: Mapping[str, Any]) -> str | None:
        """
        The description of a node, looked up through a possible ``$ref``.

        :param node: the schema node
        :return: the description, if any
        """
        if "description" in node:
            return str(node["description"])
        if "$ref" in node:
            target = self._resolver.deref(node)
            description = target.get("description")
            return str(description) if description is not None else None
        return None

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    def _parse_path(self, path: str, path_item: Mapping[str, Any]) -> None:
        """
        Translate all operations of one path item.

        :param path: the path template as spelled in the schema
        :param path_item: the path item node
        """
        path_item = self._resolver.deref(path_item)
        shared_parameters = path_item.get("parameters") or []
        for method, operation in path_item.items():
            if method in _METHODS and isinstance(operation, Mapping):
                self._parse_operation(path, method, operation, shared_parameters)

    def _parse_operation(
        self,
        path: str,
        method: str,
        operation: Mapping[str, Any],
        shared_parameters: list[Any],
    ) -> None:
        """
        Translate one operation into an :py:class:`OperationIR`.

        :param path: the path template as spelled in the schema
        :param method: the lowercase HTTP method
        :param operation: the operation node
        :param shared_parameters: the path item's shared parameters
        """
        where = f"paths.{path}.{method}"
        name = self._class_names.claim(
            operation_class_name(operation.get("operationId"), method, path)
        )
        field_names = NameRegistry()
        params, renames = self._parse_parameters(
            name, operation, shared_parameters, field_names, where=where
        )
        try:
            placeholders = path_placeholders(path)
        except ValueError as error:
            raise SchemaError(f"{where}: malformed path template ({error})") from error
        declared = {param.wire_name for param in params if param.location is ParamLocation.PATH}
        if set(placeholders) != declared:
            raise SchemaError(
                f"{where}: path parameters {sorted(declared)} do not match the template"
                f" placeholders {sorted(set(placeholders))}"
            )
        body = self._parse_body(name, operation.get("requestBody"), field_names, where=where)
        response_kind, response_type = self._parse_responses(name, operation, where=where)
        self._operations.append(
            OperationIR(
                class_name=name,
                method=method.upper(),
                path_template=rewrite_path(path, renames),
                wire_path=path,
                params=tuple(params),
                body=body,
                response_kind=response_kind,
                response_type=response_type,
                summary=operation.get("summary"),
                description=operation.get("description"),
                tag=str(operation["tags"][0]) if operation.get("tags") else None,
            )
        )

    def _parse_parameters(
        self,
        operation_name: str,
        operation: Mapping[str, Any],
        shared_parameters: list[Any],
        field_names: NameRegistry,
        *,
        where: str,
    ) -> tuple[list[Param], dict[str, str]]:
        """
        Merge and translate an operation's parameters.

        Path-item parameters apply to every operation of the path; an
        operation parameter with the same ``(name, in)`` pair overrides.

        :param operation_name: the operation's class name (context for
            inline enums)
        :param operation: the operation node
        :param shared_parameters: the path item's shared parameters
        :param field_names: the operation's field name scope
        :param where: the schema location, for error messages
        :return: the parameters, and the placeholder renames for the
            path template
        """
        merged: dict[tuple[str, str], Mapping[str, Any]] = {}
        for node in [*shared_parameters, *(operation.get("parameters") or [])]:
            parameter = self._resolver.deref(node)
            merged[(str(parameter.get("name")), str(parameter.get("in")))] = parameter
        params = []
        renames = {}
        for (wire_name, location_name), parameter in merged.items():
            if location_name not in _PARAM_LOCATIONS:
                raise SchemaError(
                    f"{where}: parameter {wire_name!r} in {location_name!r} is not supported"
                    " (path, query and header parameters are)"
                )
            location = _PARAM_LOCATIONS[location_name]
            schema = parameter.get("schema")
            if not isinstance(schema, Mapping):
                raise SchemaError(
                    f"{where}: parameter {wire_name!r} has no schema —"
                    " content-typed parameters are not supported"
                )
            param_type, nullable = self._schema_type(
                schema,
                context=f"{operation_name} {wire_name}",
                where=f"{where}.parameters.{wire_name}",
            )
            python_name = field_names.claim(
                field_name(wire_name, reserved=RESERVED_OPERATION_FIELDS)
            )
            # the spec mandates required: true for path parameters
            required = location is ParamLocation.PATH or bool(parameter.get("required", False))
            if location is ParamLocation.PATH:
                renames[wire_name] = python_name
            params.append(
                Param(
                    name=python_name,
                    wire_name=wire_name,
                    location=location,
                    type=param_type,
                    required=required,
                    nullable=nullable,
                    default=None if required else self._default_for(schema, param_type),
                    description=parameter.get("description"),
                )
            )
        return params, renames

    def _parse_body(
        self,
        operation_name: str,
        request_body: Any,
        field_names: NameRegistry,
        *,
        where: str,
    ) -> Body | None:
        """
        Translate an operation's request body.

        :param operation_name: the operation's class name (context for
            inline models)
        :param request_body: the ``requestBody`` node, if any
        :param field_names: the operation's field name scope
        :param where: the schema location, for error messages
        :return: the body, or ``None`` when the operation has none
        """
        if request_body is None:
            return None
        if not isinstance(request_body, Mapping):
            raise SchemaError(f"{where}: requestBody must be an object")
        request_body = self._resolver.deref(request_body)
        content = request_body.get("content") or {}
        required = bool(request_body.get("required", False))
        json_media = self._json_media_type(content)
        if json_media is not None:
            schema = content[json_media].get("schema") or {}
            return self._json_body(
                operation_name, schema, required, field_names, where=f"{where}.requestBody"
            )
        if "application/x-www-form-urlencoded" in content:
            schema = content["application/x-www-form-urlencoded"].get("schema") or {}
            return self._form_body(
                operation_name, schema, required, field_names, where=f"{where}.requestBody"
            )
        raise SchemaError(
            f"{where}: no supported request media type in {sorted(content)}"
            " (application/json and application/x-www-form-urlencoded are)"
        )

    def _json_media_type(self, content: Mapping[str, Any]) -> str | None:
        """
        The JSON media type of a content map, if there is one.

        :param content: the ``content`` node
        :return: ``application/json`` or an ``application/*+json``
            variant, ``None`` when the content offers neither
        """
        for media_type in content:
            plain = media_type.split(";")[0].strip()
            if plain == "application/json" or plain.endswith("+json"):
                return media_type
        return None

    def _json_body(
        self,
        operation_name: str,
        schema: Mapping[str, Any],
        required: bool,
        field_names: NameRegistry,
        *,
        where: str,
    ) -> Body:
        """
        Translate a JSON request body.

        An inline object schema explodes into one ``json_field()`` per
        property (the petstore style); referenced, array and scalar
        schemas become a single ``json_body()`` field.

        :param operation_name: the operation's class name
        :param schema: the body schema
        :param required: whether the request must carry the body
        :param field_names: the operation's field name scope
        :param where: the schema location, for error messages
        :return: the body
        """
        if (
            "$ref" not in schema
            and schema.get("type", "object") == "object"
            and "properties" in schema
        ):
            body_required = set(schema.get("required") or ()) if required else set()
            if not required and schema.get("required"):
                self._warnings.append(
                    f"{where}: the body is optional, so its required properties"
                    " are generated as optional fields"
                )
            fields = []
            for wire_name, prop in schema["properties"].items():
                prop_type, nullable = self._schema_type(
                    prop,
                    context=f"{operation_name} {wire_name}",
                    where=f"{where}.properties.{wire_name}",
                )
                is_required = wire_name in body_required
                fields.append(
                    Field(
                        name=field_names.claim(
                            field_name(wire_name, reserved=RESERVED_OPERATION_FIELDS)
                        ),
                        wire_name=wire_name,
                        type=prop_type,
                        required=is_required,
                        nullable=nullable,
                        default=None if is_required else self._default_for(prop, prop_type),
                        description=self._description(prop),
                    )
                )
            return Body(kind=BodyKind.JSON_FIELDS, fields=tuple(fields), required=required)
        body_type, _ = self._schema_type(schema, context=f"{operation_name} body", where=where)
        name = field_names.claim(field_name("payload", reserved=RESERVED_OPERATION_FIELDS))
        field = Field(
            name=name,
            wire_name=name,
            type=body_type,
            required=required,
            nullable=not required,
            description=self._description(schema),
        )
        return Body(kind=BodyKind.JSON_BODY, fields=(field,), type=body_type, required=required)

    def _form_body(
        self,
        operation_name: str,
        schema: Mapping[str, Any],
        required: bool,
        field_names: NameRegistry,
        *,
        where: str,
    ) -> Body:
        """
        Translate a form-urlencoded request body.

        :param operation_name: the operation's class name
        :param schema: the body schema
        :param required: whether the request must carry the body
        :param field_names: the operation's field name scope
        :param where: the schema location, for error messages
        :return: the body
        """
        schema = self._resolver.deref(schema)
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise SchemaError(
                f"{where}: a form-urlencoded body needs an object schema with properties"
            )
        body_required = set(schema.get("required") or ()) if required else set()
        fields = []
        for wire_name, prop in properties.items():
            prop_type, nullable = self._schema_type(
                prop,
                context=f"{operation_name} {wire_name}",
                where=f"{where}.properties.{wire_name}",
            )
            if not isinstance(prop_type, (ScalarType, EnumType)) and not (
                isinstance(prop_type, ArrayType)
                and isinstance(prop_type.item, (ScalarType, EnumType))
            ):
                raise SchemaError(
                    f"{where}.properties.{wire_name}: form fields must be scalars, enums"
                    " or arrays of those"
                )
            is_required = wire_name in body_required
            fields.append(
                Field(
                    name=field_names.claim(
                        field_name(wire_name, reserved=RESERVED_OPERATION_FIELDS)
                    ),
                    wire_name=wire_name,
                    type=prop_type,
                    required=is_required,
                    nullable=nullable,
                    default=None if is_required else self._default_for(prop, prop_type),
                    description=self._description(prop),
                )
            )
        return Body(kind=BodyKind.FORM_FIELDS, fields=tuple(fields), required=required)

    def _parse_responses(
        self, operation_name: str, operation: Mapping[str, Any], *, where: str
    ) -> tuple[ResponseKind, TypeExpr | None]:
        """
        Pick and translate the success response.

        The lowest 2xx status wins (a ``2XX`` range counts last); JSON
        content types the result, no content means ``None``, non-JSON
        content is returned as raw bytes.

        :param operation_name: the operation's class name
        :param operation: the operation node
        :param where: the schema location, for error messages
        :return: the response kind and the model type, if any
        """
        responses = operation.get("responses") or {}
        chosen: tuple[int, str] | None = None
        for status in responses:
            key = str(status)
            if key.isdigit() and 200 <= int(key) <= 299:
                rank = (int(key), key)
            elif key.upper() == "2XX":
                rank = (300, key)  # after every concrete 2xx status
            else:
                continue
            if chosen is None or rank < chosen:
                chosen = rank
        if chosen is None:
            # no success response documented: stay with None — check()
            # still validates the 2xx status at runtime
            return ResponseKind.NONE, None
        response = self._resolver.deref(responses[chosen[1]])
        content = response.get("content") or {}
        if not content or chosen[0] == 204:
            return ResponseKind.NONE, None
        json_media = self._json_media_type(content)
        if json_media is None:
            return ResponseKind.BYTES, None
        schema = content[json_media].get("schema")
        if not isinstance(schema, Mapping):
            return ResponseKind.MODEL, ScalarType(Scalar.ANY)
        response_type, _ = self._schema_type(
            schema,
            context=f"{operation_name} response",
            where=f"{where}.responses.{chosen[1]}",
        )
        return ResponseKind.MODEL, response_type

    # ------------------------------------------------------------------
    # security
    # ------------------------------------------------------------------

    def _parse_security(self) -> tuple[SecurityScheme, ...]:
        """
        Translate the security schemes the document actually uses.

        A scheme becomes client credentials when the document-level
        ``security`` or any operation's ``security`` references it.
        Unsupported kinds (OAuth2, OpenID Connect, cookies) are skipped
        with a warning. Per-operation differences are not modeled: all
        referenced schemes end up as constructor credentials.

        :return: the client credential schemes
        """
        schemes = (self._document.get("components") or {}).get("securitySchemes") or {}
        used = self._referenced_scheme_names()
        names = NameRegistry()
        for reserved in _RESERVED_CREDENTIAL_NAMES:
            names.claim(reserved)
        translated = []
        for scheme_name, node in schemes.items():
            if scheme_name not in used:
                continue
            node = self._resolver.deref(node)
            where = f"components.securitySchemes.{scheme_name}"
            kind = self._security_kind(node)
            if kind is None:
                self._warnings.append(
                    f"{where}: unsupported security scheme type"
                    f" {node.get('type')!r} — pass these credentials yourself"
                    " (default headers or an APIClient.prepare override)"
                )
                continue
            if kind is SecurityKind.HTTP_BEARER:
                param_name, wire_name = names.claim("token"), None
            elif kind is SecurityKind.HTTP_BASIC:
                # rendered as the two parameters username/password
                param_name, wire_name = names.claim("username"), None
                names.claim("password")
            else:
                param_name = names.claim(field_name(scheme_name))
                wire_name = str(node.get("name", ""))
                if not wire_name:
                    raise SchemaError(f"{where}: apiKey scheme without a parameter name")
            translated.append(
                SecurityScheme(kind=kind, param_name=param_name, wire_name=wire_name)
            )
        return tuple(translated)

    def _referenced_scheme_names(self) -> set[str]:
        """
        The names of all security schemes any requirement references.

        :return: the referenced scheme names
        """
        requirements = list(self._document.get("security") or [])
        for path_item in (self._document.get("paths") or {}).values():
            if not isinstance(path_item, Mapping):
                continue
            for method in _METHODS:
                operation = path_item.get(method)
                if isinstance(operation, Mapping):
                    requirements.extend(operation.get("security") or [])
        return {name for requirement in requirements for name in requirement}

    def _security_kind(self, node: Mapping[str, Any]) -> SecurityKind | None:
        """
        Map one security scheme node onto the supported kinds.

        :param node: the scheme node
        :return: the kind, or ``None`` when unsupported
        """
        scheme_type = str(node.get("type", "")).lower()
        if scheme_type == "http":
            http_scheme = str(node.get("scheme", "")).lower()
            if http_scheme == "bearer":
                return SecurityKind.HTTP_BEARER
            if http_scheme == "basic":
                return SecurityKind.HTTP_BASIC
            return None
        if scheme_type == "apikey":
            location = str(node.get("in", "")).lower()
            if location == "header":
                return SecurityKind.API_KEY_HEADER
            if location == "query":
                return SecurityKind.API_KEY_QUERY
            return None
        return None
