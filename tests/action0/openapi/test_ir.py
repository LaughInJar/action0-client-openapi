import dataclasses
import unittest

from action0.openapi import Api
from action0.openapi import ArrayType
from action0.openapi import Field
from action0.openapi import MapType
from action0.openapi import Model
from action0.openapi import ModelType
from action0.openapi import OperationIR
from action0.openapi import Scalar
from action0.openapi import ScalarType


class IrTestCase(unittest.TestCase):
    """
    tests for the :py:mod:`action0.openapi.ir` dataclasses
    """

    def test_nodes_are_frozen(self) -> None:
        """
        Test that IR nodes cannot be mutated after construction.
        """
        node = ScalarType(Scalar.STR)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            # the assignment is the point of the test; mypy and ty flag
            # it under different rules
            node.kind = Scalar.INT  # type: ignore[misc]  # ty: ignore[invalid-assignment]

    def test_structural_equality(self) -> None:
        """
        Test that equal trees compare equal (needed by the tests of the
        translation stage, which assert whole IR values).
        """
        self.assertEqual(
            ArrayType(MapType(ModelType("Pet"))),
            ArrayType(MapType(ModelType("Pet"))),
        )
        self.assertNotEqual(ScalarType(Scalar.STR), ScalarType(Scalar.INT))

    def test_field_defaults(self) -> None:
        """
        Test the optional Field attributes' defaults.
        """
        field = Field(name="tag", wire_name="tag", type=ScalarType(Scalar.STR), required=False)
        self.assertFalse(field.nullable)
        self.assertIsNone(field.default)
        self.assertIsNone(field.description)

    def test_api_defaults(self) -> None:
        """
        Test that an Api can be built from just the info fields.
        """
        api = Api(title="Petstore", version="1.0.0")
        self.assertIsNone(api.base_url)
        self.assertEqual(api.models, ())
        self.assertEqual(api.operations, ())
        self.assertEqual(api.security, ())

    def test_operation_defaults(self) -> None:
        """
        Test the OperationIR defaults for a bare no-content endpoint.
        """
        operation = OperationIR(
            class_name="Ping", method="GET", path_template="/ping", wire_path="/ping"
        )
        self.assertEqual(operation.params, ())
        self.assertIsNone(operation.body)
        self.assertIsNone(operation.response_type)

    def test_models_can_nest_and_hash(self) -> None:
        """
        Test that frozen models are hashable (usable in sets/dict keys).
        """
        model = Model(
            name="Pet",
            fields=(Field(name="id", wire_name="id", type=ScalarType(Scalar.INT), required=True),),
        )
        self.assertEqual(len({model, model}), 1)
