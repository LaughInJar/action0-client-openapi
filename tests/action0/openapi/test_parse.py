import unittest
from pathlib import Path
from typing import Any

from action0.openapi import Api
from action0.openapi import ArrayType
from action0.openapi import BodyKind
from action0.openapi import EnumModel
from action0.openapi import MapType
from action0.openapi import Model
from action0.openapi import ModelType
from action0.openapi import ParamLocation
from action0.openapi import ResponseKind
from action0.openapi import Scalar
from action0.openapi import ScalarType
from action0.openapi import SchemaError
from action0.openapi import SecurityKind
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
            ["PetStatus", "Owner", "Pet", "CreateTokenResponse"],
        )

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
            ["id", "name", "status", "born_on", "owner", "labels", "friends"],
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
                "GetPetPhoto",
                "CreateToken",
            ],
        )

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

    def test_oneof_union_rejected(self) -> None:
        """
        Test that a real union is rejected with the location.
        """
        with self.assertRaisesRegex(SchemaError, "components.schemas.Box.*oneOf"):
            self.parse_component("Box", {"oneOf": [{"type": "string"}, {"type": "integer"}]})

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

    def test_allof_conflicting_property_rejected(self) -> None:
        """
        Test that parts disagreeing about a property are rejected.
        """
        with self.assertRaisesRegex(SchemaError, "'a' differently"):
            self.parse_component(
                "Box",
                {
                    "allOf": [
                        {"type": "object", "properties": {"a": {"type": "string"}}},
                        {"type": "object", "properties": {"a": {"type": "integer"}}},
                    ]
                },
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

    def test_placeholder_mismatch_rejected(self) -> None:
        """
        Test that undeclared template placeholders are rejected.
        """
        document = minimal(paths={"/things/{thingId}": {"get": {"responses": {}}}})
        with self.assertRaisesRegex(SchemaError, "placeholders"):
            parse_api(document)

    def test_unsupported_media_type_rejected(self) -> None:
        """
        Test that a body without JSON or form content is rejected.
        """
        document = self.operation_document(
            {"requestBody": {"content": {"application/xml": {"schema": {}}}}}
        )
        with self.assertRaisesRegex(SchemaError, "application/xml"):
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
