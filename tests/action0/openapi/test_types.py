import unittest

from action0.openapi import ArrayType
from action0.openapi import EnumType
from action0.openapi import MapType
from action0.openapi import ModelType
from action0.openapi import Scalar
from action0.openapi import ScalarType
from action0.openapi import TypeExpr
from action0.openapi.types import annotation
from action0.openapi.types import converter_expr
from action0.openapi.types import imports_for
from action0.openapi.types import needs_conversion
from action0.openapi.types import scalar_type


class ScalarTypeTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.types.scalar_type`
    """

    def test_plain_types(self) -> None:
        """
        Test the four scalar schema types.
        """
        self.assertEqual(scalar_type("string", None), ScalarType(Scalar.STR))
        self.assertEqual(scalar_type("integer", None), ScalarType(Scalar.INT))
        self.assertEqual(scalar_type("number", None), ScalarType(Scalar.FLOAT))
        self.assertEqual(scalar_type("boolean", None), ScalarType(Scalar.BOOL))

    def test_string_formats(self) -> None:
        """
        Test the string formats that map to richer types.
        """
        self.assertEqual(scalar_type("string", "date"), ScalarType(Scalar.DATE))
        self.assertEqual(scalar_type("string", "date-time"), ScalarType(Scalar.DATETIME))
        self.assertEqual(scalar_type("string", "uuid"), ScalarType(Scalar.UUID))

    def test_unknown_string_formats_stay_str(self) -> None:
        """
        Test that unknown/unsupported formats fall back to str.
        """
        for format_name in ["email", "uri", "byte", "binary", "password"]:
            with self.subTest(format=format_name):
                self.assertEqual(scalar_type("string", format_name), ScalarType(Scalar.STR))

    def test_formats_of_other_types_are_ignored(self) -> None:
        """
        Test that formats like int64 do not change the Python type.
        """
        self.assertEqual(scalar_type("integer", "int64"), ScalarType(Scalar.INT))
        self.assertEqual(scalar_type("number", "double"), ScalarType(Scalar.FLOAT))

    def test_missing_type_is_any(self) -> None:
        """
        Test that a schema without "type" accepts any JSON value.
        """
        self.assertEqual(scalar_type(None, None), ScalarType(Scalar.ANY))

    def test_structural_types_are_rejected(self) -> None:
        """
        Test that object/array (the translation stage's business) raise.
        """
        for type_name in ["object", "array", "null", "file"]:
            with self.subTest(type=type_name):
                with self.assertRaises(ValueError):
                    scalar_type(type_name, None)


class AnnotationTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.types.annotation`
    """

    def test_scalars(self) -> None:
        """
        Test the scalar annotations, including the qualified ones.
        """
        self.assertEqual(annotation(ScalarType(Scalar.STR)), "str")
        self.assertEqual(annotation(ScalarType(Scalar.DATETIME)), "datetime.datetime")
        self.assertEqual(annotation(ScalarType(Scalar.UUID)), "uuid.UUID")
        self.assertEqual(annotation(ScalarType(Scalar.ANY)), "Any")

    def test_nesting(self) -> None:
        """
        Test structural nesting.
        """
        self.assertEqual(annotation(ArrayType(MapType(ModelType("Pet")))), "list[dict[str, Pet]]")

    def test_optional(self) -> None:
        """
        Test the optional/nullable rendering.
        """
        self.assertEqual(annotation(EnumType("Status"), optional=True), "Status | None")


class ImportsForTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.types.imports_for`
    """

    def test_scalar_imports(self) -> None:
        """
        Test which scalars need which imports.
        """
        self.assertEqual(imports_for(ScalarType(Scalar.DATE)), frozenset({"import datetime"}))
        self.assertEqual(
            imports_for(ScalarType(Scalar.ANY)), frozenset({"from typing import Any"})
        )
        self.assertEqual(imports_for(ScalarType(Scalar.STR)), frozenset())

    def test_nested_imports_bubble_up(self) -> None:
        """
        Test that imports are collected through arrays and maps.
        """
        self.assertEqual(
            imports_for(ArrayType(MapType(ScalarType(Scalar.UUID)))), frozenset({"import uuid"})
        )

    def test_model_imports_are_the_emitters_business(self) -> None:
        """
        Test that model/enum references report no imports.
        """
        self.assertEqual(imports_for(ModelType("Pet")), frozenset())


class ConverterExprTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.types.converter_expr` (and
    :py:func:`action0.openapi.types.needs_conversion`)
    """

    def test_pass_through(self) -> None:
        """
        Test that decoder-ready values pass through untouched.
        """
        plain: list[TypeExpr] = [
            ScalarType(Scalar.STR),
            ScalarType(Scalar.ANY),
            ArrayType(ScalarType(Scalar.INT)),
            MapType(ScalarType(Scalar.BOOL)),
        ]
        for t in plain:
            with self.subTest(type=t):
                self.assertFalse(needs_conversion(t))
                self.assertEqual(converter_expr(t, 'data["x"]'), 'data["x"]')

    def test_rich_scalars(self) -> None:
        """
        Test date, datetime and UUID conversion.
        """
        self.assertEqual(
            converter_expr(ScalarType(Scalar.DATE), 'data["born"]'),
            'datetime.date.fromisoformat(data["born"])',
        )
        self.assertEqual(
            converter_expr(ScalarType(Scalar.UUID), 'data["id"]'), 'uuid.UUID(data["id"])'
        )

    def test_models_and_enums(self) -> None:
        """
        Test converter-function and enum-constructor calls.
        """
        self.assertEqual(
            converter_expr(ModelType("Pet"), 'data["pet"]'), 'pet_from_json(data["pet"])'
        )
        self.assertEqual(
            converter_expr(EnumType("Status"), 'data["status"]'), 'Status(data["status"])'
        )

    def test_array_comprehension(self) -> None:
        """
        Test the list comprehension over converting items.
        """
        self.assertEqual(
            converter_expr(ArrayType(ModelType("Pet")), 'data["items"]'),
            '[pet_from_json(item) for item in data["items"]]',
        )

    def test_nested_arrays_keep_variables_apart(self) -> None:
        """
        Test that nested comprehensions do not shadow their variables.
        """
        self.assertEqual(
            converter_expr(ArrayType(ArrayType(EnumType("Status"))), 'data["grid"]'),
            '[[Status(item1) for item1 in item] for item in data["grid"]]',
        )

    def test_map_comprehension(self) -> None:
        """
        Test the dict comprehension over converting values.
        """
        self.assertEqual(
            converter_expr(MapType(ModelType("Pet")), 'data["by_name"]'),
            '{key: pet_from_json(value) for key, value in data["by_name"].items()}',
        )

    def test_map_of_arrays(self) -> None:
        """
        Test mixing maps and arrays.
        """
        self.assertEqual(
            converter_expr(MapType(ArrayType(ScalarType(Scalar.DATE))), "data"),
            "{key: [datetime.date.fromisoformat(item1) for item1 in value]"
            " for key, value in data.items()}",
        )
