import unittest
from pathlib import Path
from typing import Any

from action0.openapi import Api
from action0.openapi import ArrayType
from action0.openapi import BodyKind
from action0.openapi import EnumModel
from action0.openapi import EnumType
from action0.openapi import MapType
from action0.openapi import Model
from action0.openapi import ModelType
from action0.openapi import ParamLocation
from action0.openapi import ResponseKind
from action0.openapi import Scalar
from action0.openapi import ScalarType
from action0.openapi import SchemaError
from action0.openapi import SecurityKind
from action0.openapi import UnionCheck
from action0.openapi import UnionModel
from action0.openapi import UnionType
from action0.openapi import load_schema
from action0.openapi import parse_api

FIXTURES = Path(__file__).parent / "fixtures"


def minimal(**extra: Any) -> dict[str, Any]:
    """
    Build a minimal OpenAPI document with the given extra top-level keys.

    :param extra: additional top-level fields
    :return: the document
    """
    return {"openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": {}, **extra}


class PetstoreTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.parse.parse_api` over the
    petstore fixture
    """

    api: Api

    @classmethod
    def setUpClass(cls) -> None:
        cls.api = parse_api(load_schema(FIXTURES / "petstore.json"))

    def model(self, name: str) -> Model:
        """
        Look up a (non-enum) model by class name.

        :param name: the class name
        :return: the model
        """
        for model in self.api.models:
            if model.name == name:
                assert isinstance(model, Model)
                return model
        raise AssertionError(f"no model named {name}")

    def operation(self, name: str) -> Any:
        """
        Look up an operation by class name.

        :param name: the class name
        :return: the operation
        """
        for operation in self.api.operations:
            if operation.class_name == name:
                return operation
        raise AssertionError(f"no operation named {name}")

    def test_info_and_base_url(self) -> None:
        """
        Test title, version, and the server URL with its variable
        substituted.
        """
        self.assertEqual(self.api.title, "Petstore")
        self.assertEqual(self.api.version, "1.0.0")
        self.assertEqual(self.api.base_url, "https://petstore.example.com/v1")

    def test_model_names_and_order(self) -> None:
        """
        Test that referenced components complete before their referrers
        and inline response models are synthesized.
        """
        self.assertEqual(
            [model.name for model in self.api.models],
            [
                "PetStatus",
                "Owner",
                "Cat",
                "Dog",
                "Companion",
                "Pet",
                "Animal",
                "HealthRecord",
                "CreateTokenResponse",
            ],
        )

    def test_health_record_model(self) -> None:
        """
        Test that a required-but-nullable field sorts with the defaulted
        fields: it renders ``= None``, so it must not precede a plain
        required field in the dataclass.
        """
        record = self.model("HealthRecord")
        self.assertEqual(
            [field.name for field in record.fields],
            ["clinic", "examined_on", "notes"],
        )
        by_name = {field.name: field for field in record.fields}
        self.assertTrue(by_name["examined_on"].required)
        self.assertTrue(by_name["examined_on"].nullable)
        self.assertTrue(by_name["clinic"].required)
        self.assertFalse(by_name["clinic"].nullable)

    def test_health_record_catch_all(self) -> None:
        """
        Test that additionalProperties next to properties becomes the
        catch-all field, typed after the additionalProperties schema.
        """
        record = self.model("HealthRecord")
        field = record.additional_field
        assert field is not None
        self.assertEqual(field.name, "additional_properties")
        self.assertEqual(field.type, MapType(ScalarType(Scalar.FLOAT)))
        self.assertFalse(field.required)
        self.assertEqual(field.description, "Measured values by name (weight, temperature, ...).")
        # models without additionalProperties have no catch-all
        self.assertIsNone(self.model("Pet").additional_field)

    def test_enum(self) -> None:
        """
        Test the PetStatus enum: base, members, dash handling.
        """
        enum = self.api.models[0]
        assert isinstance(enum, EnumModel)
        self.assertEqual(enum.base, Scalar.STR)
        self.assertEqual(
            enum.members,
            (("AVAILABLE", "available"), ("ON_SALE", "on-sale"), ("SOLD", "sold")),
        )
        self.assertEqual(enum.description, "The pet's sale status.")

    def test_pet_model(self) -> None:
        """
        Test the Pet model: required-first ordering, wire renames,
        nullability, maps, and self references.
        """
        pet = self.model("Pet")
        self.assertEqual(pet.description, "One pet of the store.")
        by_name = {field.name: field for field in pet.fields}
        self.assertEqual(
            [field.name for field in pet.fields],
            ["id", "name", "status", "born_on", "owner", "labels", "friends", "companion"],
        )
        self.assertTrue(by_name["id"].required)
        self.assertEqual(by_name["id"].type, ScalarType(Scalar.INT))
        self.assertFalse(by_name["status"].required)
        self.assertEqual(by_name["born_on"].wire_name, "bornOn")
        self.assertEqual(by_name["born_on"].type, ScalarType(Scalar.DATE))
        self.assertTrue(by_name["born_on"].nullable)
        self.assertEqual(by_name["owner"].type, ModelType("Owner"))
        self.assertEqual(by_name["labels"].type, MapType(ScalarType(Scalar.STR)))
        self.assertEqual(by_name["friends"].type, ArrayType(ModelType("Pet")))

    def test_discriminated_union(self) -> None:
        """
        Test the Companion union: explicit mapping tag for Cat, implicit
        component-name tag for Dog, allOf-flattened members.
        """
        union = next(model for model in self.api.models if model.name == "Companion")
        assert isinstance(union, UnionModel)
        self.assertEqual(union.description, "A pet's companion animal.")
        self.assertEqual(union.discriminator, "species")
        self.assertEqual(union.members, (ModelType("Cat"), ModelType("Dog")))
        self.assertEqual(
            [(case.check, case.value) for case in union.cases],
            [(UnionCheck.TAG, "cat"), (UnionCheck.TAG, "Dog")],
        )
        cat = self.model("Cat")
        self.assertEqual([field.name for field in cat.fields], ["species", "name", "meow"])
        pet = self.model("Pet")
        companion = {field.name: field for field in pet.fields}["companion"]
        self.assertEqual(companion.type, UnionType("Companion", union.members))

    def test_keyword_property(self) -> None:
        """
        Test that Owner's "class" property is escaped, keeping the wire
        name.
        """
        owner = self.model("Owner")
        field = {field.name: field for field in owner.fields}["class_"]
        self.assertEqual(field.wire_name, "class")

    def test_operation_names_and_order(self) -> None:
        """
        Test the operation classes, in path/method document order.
        """
        self.assertEqual(
            [operation.class_name for operation in self.api.operations],
            [
                "ListPets",
                "CreatePet",
                "GetPet",
                "ReplacePet",
                "DeletePet",
                "UploadPetPhoto",
                "GetPetPhoto",
                "GetInventory",
                "CreateToken",
            ],
        )

    def test_tags(self) -> None:
        """
        Test that the first tag is kept and untagged operations carry
        None.
        """
        self.assertEqual(self.operation("ListPets").tag, "pets")
        self.assertEqual(self.operation("CreateToken").tag, "auth")
        self.assertIsNone(self.operation("GetPetPhoto").tag)

    def test_query_and_header_params(self) -> None:
        """
        Test ListPets: query defaults, array params, header UUID params.
        """
        operation = self.operation("ListPets")
        self.assertEqual(operation.summary, "List all pets.")
        by_name = {param.name: param for param in operation.params}
        self.assertEqual(by_name["limit"].location, ParamLocation.QUERY)
        self.assertFalse(by_name["limit"].required)
        self.assertEqual(by_name["limit"].default, 20)
        self.assertEqual(by_name["tags"].type, ArrayType(ScalarType(Scalar.STR)))
        # declared explode: false, so the items join into one pair
        self.assertEqual(by_name["tags"].join_with, ",")
        self.assertIsNone(by_name["limit"].join_with)
        self.assertEqual(by_name["x_request_id"].location, ParamLocation.HEADER)
        self.assertEqual(by_name["x_request_id"].wire_name, "X-Request-Id")
        self.assertEqual(by_name["x_request_id"].type, ScalarType(Scalar.UUID))
        self.assertEqual(operation.response_kind, ResponseKind.MODEL)
        self.assertEqual(operation.response_type, ArrayType(ModelType("Pet")))

    def test_shared_path_parameter_and_template_rewrite(self) -> None:
        """
        Test that the path-item parameter applies to GetPet and the
        template is rewritten to the snake_case field name.
        """
        operation = self.operation("GetPet")
        self.assertEqual(operation.wire_path, "/pets/{petId}")
        self.assertEqual(operation.path_template, "/pets/{pet_id}")
        (param,) = operation.params
        self.assertEqual(param.name, "pet_id")
        self.assertEqual(param.location, ParamLocation.PATH)
        self.assertTrue(param.required)

    def test_inline_json_body(self) -> None:
        """
        Test CreatePet: an inline object body explodes into json fields.
        """
        body = self.operation("CreatePet").body
        self.assertEqual(body.kind, BodyKind.JSON_FIELDS)
        self.assertTrue(body.required)
        by_name = {field.name: field for field in body.fields}
        self.assertTrue(by_name["name"].required)
        self.assertFalse(by_name["tag"].required)

    def test_referenced_json_body(self) -> None:
        """
        Test ReplacePet: a $ref body becomes a single json_body payload.
        """
        body = self.operation("ReplacePet").body
        self.assertEqual(body.kind, BodyKind.JSON_BODY)
        self.assertEqual(body.type, ModelType("Pet"))
        (field,) = body.fields
        self.assertEqual(field.name, "payload")
        self.assertTrue(field.required)

    def test_form_body(self) -> None:
        """
        Test CreateToken: a form-urlencoded body becomes form fields.
        """
        body = self.operation("CreateToken").body
        self.assertEqual(body.kind, BodyKind.FORM_FIELDS)
        by_name = {field.name: field for field in body.fields}
        self.assertTrue(by_name["grant_type"].required)
        self.assertFalse(by_name["scope"].required)

    def test_raw_body(self) -> None:
        """
        Test UploadPetPhoto: an image/png body becomes a raw bytes
        payload with a preset Content-Type header parameter.
        """
        operation = self.operation("UploadPetPhoto")
        self.assertEqual(operation.body.kind, BodyKind.RAW_BODY)
        self.assertEqual(operation.body.media_type, "image/png")
        (payload,) = operation.body.fields
        self.assertEqual(payload.type, ScalarType(Scalar.BYTES))
        self.assertTrue(payload.required)
        by_name = {param.name: param for param in operation.params}
        self.assertEqual(by_name["content_type"].wire_name, "Content-Type")
        self.assertEqual(by_name["content_type"].default, "image/png")

    def test_no_content_response(self) -> None:
        """
        Test DeletePet: 204 parses into None.
        """
        operation = self.operation("DeletePet")
        self.assertEqual(operation.response_kind, ResponseKind.NONE)
        self.assertIsNone(operation.response_type)
        self.assertIsNone(operation.body)

    def test_non_json_response(self) -> None:
        """
        Test GetPetPhoto: image content comes back as raw bytes.
        """
        self.assertEqual(self.operation("GetPetPhoto").response_kind, ResponseKind.BYTES)

    def test_inline_response_model(self) -> None:
        """
        Test CreateToken's inline 200 schema became a synthesized model.
        """
        operation = self.operation("CreateToken")
        self.assertEqual(operation.response_type, ModelType("CreateTokenResponse"))
        token = self.model("CreateTokenResponse")
        self.assertEqual([field.name for field in token.fields], ["access_token", "expires_in"])

    def test_security(self) -> None:
        """
        Test the referenced schemes: bearer and apiKey-in-query become
        credentials, the OAuth2 scheme a warning.
        """
        bearer, api_key = self.api.security
        self.assertEqual(bearer.kind, SecurityKind.HTTP_BEARER)
        self.assertEqual(bearer.param_name, "token")
        self.assertEqual(api_key.kind, SecurityKind.API_KEY_QUERY)
        self.assertEqual(api_key.param_name, "api_key_auth")
        self.assertEqual(api_key.wire_name, "api_key")
        self.assertTrue(any("LegacyOAuth" in warning for warning in self.api.warnings))


class ServerFallbackTestCase(unittest.TestCase):
    """
    tests for the path/operation-level ``servers`` fallback of the
    default base URL
    """

    def operation(self) -> dict[str, Any]:
        """
        Build a minimal operation node.

        :return: the operation
        """
        return {"operationId": "getThing", "responses": {"204": {"description": "ok"}}}

    def test_path_level_servers(self) -> None:
        """
        Test that path-level servers (the Open-Meteo shape) provide the
        default base URL — the first entry wins.
        """
        api = parse_api(
            minimal(
                paths={
                    "/v1/forecast": {
                        "servers": [
                            {"url": "https://api.open-meteo.com"},
                            {"url": "https://customer-api.open-meteo.com"},
                        ],
                        "get": self.operation(),
                    }
                }
            )
        )
        self.assertEqual(api.base_url, "https://api.open-meteo.com")
        self.assertEqual(api.warnings, ())

    def test_operation_level_servers_and_variables(self) -> None:
        """
        Test that operation-level servers count too, with their
        variables substituted at the defaults.
        """
        operation = self.operation()
        operation["servers"] = [
            {"url": "https://{region}.example.com", "variables": {"region": {"default": "eu"}}}
        ]
        api = parse_api(minimal(paths={"/things": {"get": operation}}))
        self.assertEqual(api.base_url, "https://eu.example.com")

    def test_top_level_servers_take_precedence(self) -> None:
        """
        Test that top-level servers win over path-level ones.
        """
        api = parse_api(
            minimal(
                servers=[{"url": "https://top.example.com"}],
                paths={
                    "/things": {
                        "servers": [{"url": "https://path.example.com"}],
                        "get": self.operation(),
                    }
                },
            )
        )
        self.assertEqual(api.base_url, "https://top.example.com")

    def test_agreeing_paths_share_the_url(self) -> None:
        """
        Test that several paths declaring the same first URL agree on
        the default.
        """
        api = parse_api(
            minimal(
                paths={
                    "/a": {
                        "servers": [{"url": "https://api.example.com"}],
                        "get": self.operation(),
                    },
                    "/b": {
                        "servers": [{"url": "https://api.example.com"}],
                        "get": {**self.operation(), "operationId": "getOther"},
                    },
                }
            )
        )
        self.assertEqual(api.base_url, "https://api.example.com")

    def test_distinct_urls_leave_no_default_and_warn(self) -> None:
        """
        Test that paths disagreeing on their first URL produce no
        default base URL, with a warning naming the candidates.
        """
        api = parse_api(
            minimal(
                paths={
                    "/a": {
                        "servers": [{"url": "https://one.example.com"}],
                        "get": self.operation(),
                    },
                    "/b": {
                        "servers": [{"url": "https://two.example.com"}],
                        "get": {**self.operation(), "operationId": "getOther"},
                    },
                }
            )
        )
        self.assertIsNone(api.base_url)
        self.assertEqual(
            [warning for warning in api.warnings if "server URLs" in warning],
            [
                "paths declare several distinct server URLs"
                " (https://one.example.com, https://two.example.com) — the generated"
                " client has no default base URL; pass one explicitly (--base-url)"
            ],
        )

    def test_no_servers_anywhere(self) -> None:
        """
        Test that a document without any servers has no base URL.
        """
        api = parse_api(minimal(paths={"/things": {"get": self.operation()}}))
        self.assertIsNone(api.base_url)
        self.assertEqual(api.warnings, ())


class JoinedParameterTestCase(unittest.TestCase):
    """
    tests for the non-exploded (joined) array parameter styles
    """

    def parse_parameter(self, parameter: dict[str, Any]) -> Api:
        """
        Parse a document with one GET operation carrying one parameter.

        :param parameter: the parameter node (name/in filled in)
        :return: the parsed IR
        """
        return parse_api(
            minimal(
                paths={
                    "/things": {
                        "get": {
                            "operationId": "listThings",
                            "parameters": [{"name": "values", "in": "query", **parameter}],
                            "responses": {"204": {"description": "ok"}},
                        }
                    }
                }
            )
        )

    def join_of(self, api: Api) -> "str | None":
        """
        The parsed parameter's join separator.

        :param api: the parsed IR
        :return: the separator
        """
        return api.operations[0].params[0].join_with

    def test_styles_and_defaults(self) -> None:
        """
        Test the style table and the spec's explode defaults: only
        ``form`` defaults to exploded.
        """
        array = {"type": "array", "items": {"type": "integer"}}
        cases: list[tuple[dict[str, Any], str | None]] = [
            ({"schema": array}, None),
            ({"explode": False, "schema": array}, ","),
            ({"style": "form", "explode": False, "schema": array}, ","),
            ({"style": "spaceDelimited", "schema": array}, " "),
            ({"style": "pipeDelimited", "schema": array}, "|"),
            ({"style": "pipeDelimited", "explode": True, "schema": array}, None),
        ]
        for parameter, expected in cases:
            with self.subTest(parameter=parameter):
                self.assertEqual(self.join_of(self.parse_parameter(parameter)), expected)

    def test_non_arrays_and_other_locations_never_join(self) -> None:
        """
        Test that scalars and header parameters ignore explode.
        """
        api = self.parse_parameter({"explode": False, "schema": {"type": "string"}})
        self.assertIsNone(self.join_of(api))
        api = parse_api(
            minimal(
                paths={
                    "/things": {
                        "get": {
                            "operationId": "listThings",
                            "parameters": [
                                {
                                    "name": "values",
                                    "in": "header",
                                    "explode": False,
                                    "schema": {"type": "array", "items": {"type": "string"}},
                                }
                            ],
                            "responses": {"204": {"description": "ok"}},
                        }
                    }
                }
            )
        )
        self.assertIsNone(self.join_of(api))

    def test_unsupported_style_warns_and_explodes(self) -> None:
        """
        Test that deepObject falls back to exploded pairs, warning.
        """
        api = self.parse_parameter(
            {
                "style": "deepObject",
                "explode": False,
                "schema": {"type": "array", "items": {"type": "string"}},
            }
        )
        self.assertIsNone(self.join_of(api))
        self.assertTrue(any("unsupported style 'deepObject'" in w for w in api.warnings))

    def test_unjoinable_items_warn_and_explode(self) -> None:
        """
        Test that items without a text form (objects) fall back to
        exploded pairs, warning.
        """
        api = self.parse_parameter(
            {
                "explode": False,
                "schema": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"x": {"type": "integer"}}},
                },
            }
        )
        self.assertIsNone(self.join_of(api))
        self.assertTrue(any("no joinable text form" in w for w in api.warnings))


class EdgeCaseTestCase(unittest.TestCase):
    """
    tests for the translation of individual constructs
    """

    def parse_component(self, name: str, schema: dict[str, Any]) -> Api:
        """
        Parse a document containing one named component schema.

        :param name: the component name
        :param schema: the component schema
        :return: the parsed IR
        """
        return parse_api(minimal(components={"schemas": {name: schema}}))

    def test_required_nullable_field_sorts_after_defaultless(self) -> None:
        """
        Test that field sorting matches the rendered defaults: a
        required-but-nullable property (rendered ``= None``) listed
        before a plain required one must end up after it, or the
        generated dataclass would put a default-less field behind a
        defaulted one and fail to import (the weather.gov GeoJSON
        feature schema is shaped exactly like this).
        """
        api = self.parse_component(
            "Feature",
            {
                "type": "object",
                "required": ["geometry", "properties"],
                "properties": {
                    "geometry": {"type": "string", "nullable": True},
                    "properties": {"type": "string"},
                },
            },
        )
        model = api.models[0]
        assert isinstance(model, Model)
        self.assertEqual([field.name for field in model.fields], ["properties", "geometry"])

    def test_unknown_type_degrades_to_any(self) -> None:
        """
        Test that an unrecognized ``type`` (PokéAPI ships ``type: ""``)
        degrades to an untyped value with a warning instead of refusing
        the document.
        """
        api = self.parse_component(
            "Box",
            {
                "type": "object",
                "properties": {
                    "label": {"type": "", "nullable": True},
                    "tag": {"type": "file"},
                },
            },
        )
        model = api.models[0]
        assert isinstance(model, Model)
        by_name = {field.name: field for field in model.fields}
        self.assertEqual(by_name["label"].type, ScalarType(Scalar.ANY))
        self.assertTrue(by_name["label"].nullable)
        self.assertEqual(by_name["tag"].type, ScalarType(Scalar.ANY))
        self.assertEqual(
            [warning for warning in api.warnings if "unknown schema type" in warning],
            [
                "components.schemas.Box.properties.label: unknown schema type ''"
                " — generated as an untyped value",
                "components.schemas.Box.properties.tag: unknown schema type 'file'"
                " — generated as an untyped value",
            ],
        )

    def test_additional_properties_true_catch_all(self) -> None:
        """
        Test that ``additionalProperties: true`` next to properties
        yields an untyped catch-all with the default description.
        """
        api = self.parse_component(
            "Box",
            {
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "additionalProperties": True,
            },
        )
        model = api.models[0]
        assert isinstance(model, Model)
        field = model.additional_field
        assert field is not None
        self.assertEqual(field.type, MapType(ScalarType(Scalar.ANY)))
        self.assertEqual(field.description, "The payload keys not declared under properties.")
        self.assertEqual(api.warnings, ())

    def test_additional_properties_false_means_no_catch_all(self) -> None:
        """
        Test that ``additionalProperties: false`` declares no extras.
        """
        api = self.parse_component(
            "Box",
            {
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "additionalProperties": False,
            },
        )
        model = api.models[0]
        assert isinstance(model, Model)
        self.assertIsNone(model.additional_field)

    def test_additional_properties_name_collision(self) -> None:
        """
        Test that a declared ``additional_properties`` property keeps
        its name and the catch-all gets a numbered one.
        """
        api = self.parse_component(
            "Box",
            {
                "type": "object",
                "properties": {"additional_properties": {"type": "string"}},
                "additionalProperties": {"type": "integer"},
            },
        )
        model = api.models[0]
        assert isinstance(model, Model)
        self.assertEqual(model.fields[0].name, "additional_properties")
        field = model.additional_field
        assert field is not None
        self.assertEqual(field.name, "additional_properties2")

    def test_additional_properties_in_request_body_warns(self) -> None:
        """
        Test that an inline JSON body combining properties with
        additionalProperties warns: body fields have nowhere to send
        the extra keys.
        """
        api = parse_api(
            minimal(
                paths={
                    "/things": {
                        "post": {
                            "operationId": "createThing",
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"label": {"type": "string"}},
                                            "additionalProperties": {"type": "string"},
                                        }
                                    }
                                },
                            },
                            "responses": {"204": {"description": "created"}},
                        }
                    }
                }
            )
        )
        self.assertEqual(
            [warning for warning in api.warnings if "additionalProperties" in warning],
            [
                "paths./things.post.requestBody: additionalProperties of a request body"
                " are not sent — only the declared properties become fields"
            ],
        )

    def test_31_nullable_type_array(self) -> None:
        """
        Test the 3.1 spelling: type: [T, "null"].
        """
        api = self.parse_component(
            "Box",
            {
                "type": "object",
                "properties": {"label": {"type": ["string", "null"]}},
                "required": ["label"],
            },
        )
        model = api.models[0]
        assert isinstance(model, Model)
        self.assertTrue(model.fields[0].nullable)
        self.assertEqual(model.fields[0].type, ScalarType(Scalar.STR))

    def test_anyof_with_null(self) -> None:
        """
        Test the anyOf [T, null] idiom unwraps to nullable T.
        """
        api = self.parse_component(
            "Box",
            {
                "type": "object",
                "properties": {
                    "label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
        )
        model = api.models[0]
        assert isinstance(model, Model)
        self.assertTrue(model.fields[0].nullable)
        self.assertEqual(model.fields[0].type, ScalarType(Scalar.STR))

    def test_scalar_union(self) -> None:
        """
        Test a oneOf of scalars: JSON-type dispatch, no ambiguity.
        """
        api = self.parse_component(
            "Value", {"oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "boolean"}]}
        )
        union = api.models[0]
        assert isinstance(union, UnionModel)
        self.assertEqual(union.name, "Value")
        self.assertEqual(
            union.members,
            (ScalarType(Scalar.STR), ScalarType(Scalar.INT), ScalarType(Scalar.BOOL)),
        )
        # bool is checked before int (bools are ints in Python)
        self.assertEqual(
            [(case.check, case.value) for case in union.cases],
            [
                (UnionCheck.JSON_TYPE, "bool"),
                (UnionCheck.JSON_TYPE, "int"),
                (UnionCheck.JSON_TYPE, "str"),
            ],
        )

    def test_union_by_unique_required_key(self) -> None:
        """
        Test object members without a discriminator dispatching on a
        required key the other member does not declare.
        """
        api = parse_api(
            minimal(
                components={
                    "schemas": {
                        "Either": {
                            "anyOf": [
                                {"$ref": "#/components/schemas/A"},
                                {"$ref": "#/components/schemas/B"},
                            ]
                        },
                        "A": {
                            "type": "object",
                            "required": ["a"],
                            "properties": {"a": {"type": "string"}, "x": {"type": "string"}},
                        },
                        "B": {
                            "type": "object",
                            "required": ["b"],
                            "properties": {"b": {"type": "string"}, "x": {"type": "string"}},
                        },
                    }
                }
            )
        )
        union = next(model for model in api.models if model.name == "Either")
        assert isinstance(union, UnionModel)
        self.assertIsNone(union.discriminator)
        self.assertEqual(
            [(case.check, case.value) for case in union.cases],
            [(UnionCheck.KEY, "a"), (UnionCheck.KEY, "b")],
        )

    def test_ambiguous_union_degrades_to_any(self) -> None:
        """
        Test that indistinguishable members degrade to Any, warning
        attached, instead of failing the run.
        """
        api = parse_api(
            minimal(
                components={
                    "schemas": {
                        "Holder": {
                            "type": "object",
                            "properties": {
                                "value": {
                                    "oneOf": [
                                        {"type": "object", "properties": {"x": {}}},
                                        {"type": "object", "properties": {"y": {}}},
                                    ]
                                }
                            },
                        }
                    }
                }
            )
        )
        holder = next(model for model in api.models if model.name == "Holder")
        assert isinstance(holder, Model)
        self.assertEqual(holder.fields[0].type, ScalarType(Scalar.ANY))
        self.assertTrue(any("cannot be told apart" in warning for warning in api.warnings))

    def test_multi_type_array_becomes_union(self) -> None:
        """
        Test the 3.1 shorthand: type: [T1, T2, "null"] is a nullable
        union now, not Any.
        """
        api = self.parse_component(
            "Box",
            {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": ["string", "integer", "null"]}},
            },
        )
        box = next(model for model in api.models if model.name == "Box")
        assert isinstance(box, Model)
        field = box.fields[0]
        self.assertTrue(field.nullable)
        assert isinstance(field.type, UnionType)
        self.assertEqual(field.type.name, "BoxValue")
        self.assertEqual(field.type.members, (ScalarType(Scalar.STR), ScalarType(Scalar.INT)))

    def test_union_of_two_strings_degrades(self) -> None:
        """
        Test that two members in the same JSON-type bucket degrade to
        Any (a date and a plain string both arrive as strings).
        """
        api = self.parse_component(
            "Value",
            {"oneOf": [{"type": "string", "format": "date"}, {"type": "string"}]},
        )
        self.assertEqual(api.models, ())
        self.assertTrue(any("cannot be told apart" in warning for warning in api.warnings))

    def test_allof_single_unwraps(self) -> None:
        """
        Test that allOf with one subschema unwraps to it, whatever its
        kind.
        """
        api = self.parse_component("Box", {"allOf": [{"type": "string"}]})
        self.assertEqual(api.models, ())

    def test_allof_inheritance_flattens(self) -> None:
        """
        Test the base-plus-extension pattern: properties and required
        merge, the base description carries over, self references
        through the child work.
        """
        api = parse_api(
            minimal(
                components={
                    "schemas": {
                        "Cat": {
                            "allOf": [
                                {"$ref": "#/components/schemas/Pet"},
                                {
                                    "type": "object",
                                    "required": ["meow"],
                                    "properties": {
                                        "meow": {"type": "boolean"},
                                        "friend": {"$ref": "#/components/schemas/Cat"},
                                    },
                                },
                            ]
                        },
                        "Pet": {
                            "type": "object",
                            "description": "A pet.",
                            "required": ["name"],
                            "properties": {"name": {"type": "string"}},
                        },
                    }
                }
            )
        )
        cat = api.models[0]
        assert isinstance(cat, Model)
        self.assertEqual(cat.name, "Cat")
        self.assertEqual(cat.description, "A pet.")
        self.assertEqual(
            [(field.name, field.required) for field in cat.fields],
            [("name", True), ("meow", True), ("friend", False)],
        )
        self.assertEqual(cat.fields[2].type, ModelType("Cat"))
        # the base component still becomes its own model
        self.assertEqual(api.models[1].name, "Pet")

    def test_allof_sibling_properties_merge(self) -> None:
        """
        Test that properties spelled next to allOf count as one more
        part, and equal duplicate definitions are tolerated.
        """
        api = self.parse_component(
            "Box",
            {
                "allOf": [
                    {"type": "object", "properties": {"a": {"type": "string"}}},
                    {"type": "object", "properties": {"a": {"type": "string"}}},
                ],
                "properties": {"b": {"type": "integer"}},
                "required": ["b"],
            },
        )
        box = api.models[0]
        assert isinstance(box, Model)
        self.assertEqual(
            [(field.name, field.required) for field in box.fields],
            [("b", True), ("a", False)],
        )

    def test_allof_nested(self) -> None:
        """
        Test that allOf inside an allOf part flattens recursively.
        """
        api = self.parse_component(
            "Box",
            {
                "allOf": [
                    {"allOf": [{"type": "object", "properties": {"a": {"type": "string"}}}]},
                    {"type": "object", "properties": {"b": {"type": "integer"}}},
                ]
            },
        )
        box = api.models[0]
        assert isinstance(box, Model)
        self.assertEqual([field.name for field in box.fields], ["a", "b"])

    def test_allof_conflicting_property_later_wins(self) -> None:
        """
        Test that when parts disagree about a property, the later
        definition wins with a warning (the base-then-specialization
        idiom: weather.gov narrows generic GeoJSON ``features`` to
        alert features this way).
        """
        api = self.parse_component(
            "Box",
            {
                "allOf": [
                    {"type": "object", "properties": {"a": {"type": "string"}}},
                    {"type": "object", "properties": {"a": {"type": "integer"}}},
                ]
            },
        )
        model = api.models[0]
        assert isinstance(model, Model)
        self.assertEqual(model.fields[0].type, ScalarType(Scalar.INT))
        self.assertEqual(
            [warning for warning in api.warnings if "allOf" in warning],
            [
                "components.schemas.Box: the allOf subschemas define the property 'a'"
                " differently — the later definition overrides the earlier one"
            ],
        )

    def test_allof_non_object_part_rejected(self) -> None:
        """
        Test that mixing non-object subschemas into a merge is rejected.
        """
        with self.assertRaisesRegex(SchemaError, "allOf can only merge object schemas"):
            self.parse_component(
                "Box",
                {
                    "allOf": [
                        {"type": "object", "properties": {"a": {"type": "string"}}},
                        {"type": "string"},
                    ]
                },
            )

    def test_allof_nullable_part_propagates(self) -> None:
        """
        Test that a nullable subschema makes the merged type nullable.
        """
        api = parse_api(
            minimal(
                components={
                    "schemas": {
                        "Holder": {
                            "type": "object",
                            "properties": {
                                "box": {
                                    "allOf": [
                                        {
                                            "type": "object",
                                            "nullable": True,
                                            "properties": {"a": {"type": "string"}},
                                        },
                                        {"type": "object", "properties": {"b": {}}},
                                    ]
                                }
                            },
                        }
                    }
                }
            )
        )
        holder = api.models[-1]
        assert isinstance(holder, Model)
        self.assertEqual(holder.name, "Holder")
        self.assertTrue(holder.fields[0].nullable)

    def test_free_form_objects(self) -> None:
        """
        Test object schemas without properties and empty schemas.
        """
        api = self.parse_component(
            "Box",
            {
                "type": "object",
                "properties": {
                    "attributes": {"type": "object"},
                    "anything": {},
                },
            },
        )
        model = api.models[0]
        assert isinstance(model, Model)
        by_name = {field.name: field for field in model.fields}
        self.assertEqual(by_name["attributes"].type, MapType(ScalarType(Scalar.ANY)))
        self.assertEqual(by_name["anything"].type, ScalarType(Scalar.ANY))

    def test_mixed_enum_rejected(self) -> None:
        """
        Test that mixed-type enums are rejected.
        """
        with self.assertRaisesRegex(SchemaError, "enums"):
            self.parse_component("Box", {"enum": ["a", 1]})

    def test_integer_enum(self) -> None:
        """
        Test integer enums get INT base and V_-prefixed members.
        """
        api = self.parse_component("Level", {"type": "integer", "enum": [1, 2]})
        enum = api.models[0]
        assert isinstance(enum, EnumModel)
        self.assertEqual(enum.base, Scalar.INT)
        self.assertEqual(enum.members, (("V_1", 1), ("V_2", 2)))

    def test_duplicate_class_names(self) -> None:
        """
        Test that components converging on one class name are numbered.
        """
        api = parse_api(
            minimal(
                components={
                    "schemas": {
                        "pet": {"type": "object", "properties": {"a": {"type": "string"}}},
                        "Pet": {"type": "object", "properties": {"b": {"type": "string"}}},
                    }
                }
            )
        )
        self.assertEqual([model.name for model in api.models], ["Pet", "Pet2"])

    def test_array_component_cycle_rejected(self) -> None:
        """
        Test that a cycle not passing through a model is rejected.
        """
        with self.assertRaisesRegex(SchemaError, "reference cycle"):
            self.parse_component(
                "Tree", {"type": "array", "items": {"$ref": "#/components/schemas/Tree"}}
            )

    def operation_document(self, operation: dict[str, Any]) -> dict[str, Any]:
        """
        Build a document with one GET /things operation.

        :param operation: the operation node
        :return: the document
        """
        return minimal(paths={"/things": {"get": operation}})

    def test_cookie_parameter_rejected(self) -> None:
        """
        Test that cookie parameters are rejected with the location.
        """
        document = self.operation_document(
            {"parameters": [{"name": "session", "in": "cookie", "schema": {"type": "string"}}]}
        )
        with self.assertRaisesRegex(SchemaError, "paths./things.get.*cookie"):
            parse_api(document)

    def test_content_parameter_rejected(self) -> None:
        """
        Test that content-typed parameters (no schema) are rejected.
        """
        document = self.operation_document(
            {"parameters": [{"name": "filter", "in": "query", "content": {}}]}
        )
        with self.assertRaisesRegex(SchemaError, "content-typed"):
            parse_api(document)

    def test_bare_parameter_schema_salvaged(self) -> None:
        """
        Test that a Swagger-2.0-style parameter carrying its type
        keywords directly is parsed with a warning.
        """
        document = self.operation_document(
            {
                "parameters": [
                    {
                        "name": "bbox",
                        "in": "query",
                        "required": True,
                        "description": "the bounding box",
                        "type": "string",
                        "pattern": "^-?[0-9]+",
                        "example": "-20,-20,20,20",
                    }
                ]
            }
        )
        api = parse_api(document)
        parameter = api.operations[0].params[0]
        self.assertEqual(parameter.type, ScalarType(Scalar.STR))
        self.assertTrue(parameter.required)
        self.assertEqual(parameter.description, "the bounding box")
        self.assertEqual(
            api.warnings,
            (
                "paths./things.get: parameter 'bbox' declares its type directly on the"
                " parameter (Swagger 2.0 style) — treated as its schema",
            ),
        )

    def test_bare_enum_parameter_salvaged(self) -> None:
        """
        Test that a bare ``enum`` (without ``type``) is salvaged too and
        still synthesizes the inline enum class.
        """
        document = self.operation_document(
            {
                "operationId": "listThings",
                "parameters": [
                    {"name": "sort", "in": "query", "enum": ["asc", "desc"]},
                ],
            }
        )
        api = parse_api(document)
        enum = api.models[0]
        assert isinstance(enum, EnumModel)
        self.assertEqual(enum.name, "ListThingsSort")
        self.assertEqual(api.operations[0].params[0].type, EnumType(enum.name))

    def test_untyped_parameter_still_rejected(self) -> None:
        """
        Test that a parameter with neither schema nor bare type keywords
        keeps the original error.
        """
        document = self.operation_document(
            {"parameters": [{"name": "filter", "in": "query", "description": "no type"}]}
        )
        with self.assertRaisesRegex(SchemaError, "has no schema"):
            parse_api(document)

    def test_placeholder_mismatch_rejected(self) -> None:
        """
        Test that undeclared template placeholders are rejected.
        """
        document = minimal(paths={"/things/{thingId}": {"get": {"responses": {}}}})
        with self.assertRaisesRegex(SchemaError, "placeholders"):
            parse_api(document)

    def test_raw_media_type_body(self) -> None:
        """
        Test that a body without JSON or form content becomes a raw
        bytes payload plus a Content-Type header parameter.
        """
        document = self.operation_document(
            {
                "requestBody": {
                    "required": True,
                    "content": {"application/xml": {"schema": {}}},
                },
                "responses": {},
            }
        )
        operation = parse_api(document).operations[0]
        assert operation.body is not None
        self.assertEqual(operation.body.kind, BodyKind.RAW_BODY)
        self.assertEqual(operation.body.media_type, "application/xml")
        (payload,) = operation.body.fields
        self.assertEqual(payload.name, "payload")
        self.assertEqual(payload.type, ScalarType(Scalar.BYTES))
        self.assertTrue(payload.required)
        (content_type,) = operation.params
        self.assertEqual(content_type.name, "content_type")
        self.assertEqual(content_type.wire_name, "Content-Type")
        self.assertEqual(content_type.location, ParamLocation.HEADER)
        self.assertFalse(content_type.required)
        self.assertEqual(content_type.default, "application/xml")

    def test_raw_body_media_type_choice_warns(self) -> None:
        """
        Test that with several raw media types the first is sent and
        the rest are reported.
        """
        document = self.operation_document(
            {
                "requestBody": {
                    "content": {"image/png": {}, "image/jpeg": {}},
                },
                "responses": {},
            }
        )
        api = parse_api(document)
        assert api.operations[0].body is not None
        self.assertEqual(api.operations[0].body.media_type, "image/png")
        self.assertEqual(
            [warning for warning in api.warnings if "media types" in warning],
            [
                "paths./things.get.requestBody: several media types — the payload"
                " is sent as image/png; ignored: image/jpeg"
            ],
        )

    def test_empty_request_content_rejected(self) -> None:
        """
        Test that a requestBody without any media type is rejected.
        """
        document = self.operation_document({"requestBody": {"content": {}}, "responses": {}})
        with self.assertRaisesRegex(SchemaError, "no media type"):
            parse_api(document)

    def test_reserved_operation_field_names(self) -> None:
        """
        Test that parameters shadowing specifiers/ClassVars are escaped.
        """
        document = self.operation_document(
            {
                "operationId": "poke",
                "parameters": [
                    {"name": "method", "in": "query", "schema": {"type": "string"}},
                    {"name": "query", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": {},
            }
        )
        operation = parse_api(document).operations[0]
        self.assertEqual([param.name for param in operation.params], ["method_", "query_"])
        self.assertEqual([param.wire_name for param in operation.params], ["method", "query"])

    def test_lowest_success_status_wins(self) -> None:
        """
        Test response selection across several 2xx statuses.
        """
        document = self.operation_document(
            {
                "operationId": "poke",
                "responses": {
                    "202": {"description": "later"},
                    "200": {
                        "description": "now",
                        "content": {"application/json": {"schema": {"type": "string"}}},
                    },
                },
            }
        )
        operation = parse_api(document).operations[0]
        self.assertEqual(operation.response_kind, ResponseKind.MODEL)
        self.assertEqual(operation.response_type, ScalarType(Scalar.STR))

    def test_error_only_responses_mean_none(self) -> None:
        """
        Test that an operation without any 2xx response returns None.
        """
        document = self.operation_document(
            {"operationId": "poke", "responses": {"404": {"description": "nope"}}}
        )
        operation = parse_api(document).operations[0]
        self.assertEqual(operation.response_kind, ResponseKind.NONE)

    def test_operation_id_fallback(self) -> None:
        """
        Test the class name fallback when operationId is missing.
        """
        document = self.operation_document({"responses": {}})
        self.assertEqual(parse_api(document).operations[0].class_name, "GetThings")

    def test_optional_body_makes_fields_optional(self) -> None:
        """
        Test that an optional inline body demotes its required fields,
        with a warning.
        """
        document = self.operation_document(
            {
                "operationId": "poke",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {"name": {"type": "string"}},
                            }
                        }
                    }
                },
                "responses": {},
            }
        )
        api = parse_api(document)
        body = api.operations[0].body
        assert body is not None
        self.assertFalse(body.required)
        self.assertFalse(body.fields[0].required)
        self.assertTrue(any("optional" in warning for warning in api.warnings))
