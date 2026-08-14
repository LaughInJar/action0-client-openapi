import unittest

from action0.openapi.names import RESERVED_OPERATION_FIELDS
from action0.openapi.names import NameRegistry
from action0.openapi.names import class_name
from action0.openapi.names import constant_name
from action0.openapi.names import converter_name
from action0.openapi.names import field_name
from action0.openapi.names import operation_class_name
from action0.openapi.names import path_placeholders
from action0.openapi.names import rewrite_path


class ClassNameTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.names.class_name`
    """

    def test_styles(self) -> None:
        """
        Test the schema spelling styles that must all converge.
        """
        for raw in ["PetStore", "petStore", "pet_store", "pet-store", "pet store", "pet.store"]:
            with self.subTest(raw=raw):
                self.assertEqual(class_name(raw), "PetStore")

    def test_acronyms(self) -> None:
        """
        Test that acronym runs become one word.
        """
        self.assertEqual(class_name("HTTPError"), "HttpError")
        self.assertEqual(class_name("APIKey"), "ApiKey")

    def test_digits(self) -> None:
        """
        Test digit handling: inside names kept, leading prefixed.
        """
        self.assertEqual(class_name("OAuth2Token"), "OAuth2Token")
        self.assertEqual(class_name("2fa"), "V2fa")

    def test_nothing_left(self) -> None:
        """
        Test that a name without alphanumerics still yields a name.
        """
        self.assertEqual(class_name("***"), "X")


class FieldNameTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.names.field_name`
    """

    def test_styles(self) -> None:
        """
        Test camelCase, kebab-case and header-style names.
        """
        self.assertEqual(field_name("petId"), "pet_id")
        self.assertEqual(field_name("pet-id"), "pet_id")
        self.assertEqual(field_name("X-API-Key"), "x_api_key")

    def test_keywords_get_a_trailing_underscore(self) -> None:
        """
        Test that Python keywords are escaped.
        """
        for raw in ["class", "import", "from", "for"]:
            with self.subTest(raw=raw):
                self.assertEqual(field_name(raw), raw.lower() + "_")

    def test_soft_keywords_stay(self) -> None:
        """
        Test that soft keywords (legal identifiers) are not escaped.
        """
        self.assertEqual(field_name("type"), "type")
        self.assertEqual(field_name("match"), "match")

    def test_reserved_names(self) -> None:
        """
        Test that context-reserved names are escaped, including the
        escaped name being reserved again.
        """
        self.assertEqual(field_name("method", reserved=RESERVED_OPERATION_FIELDS), "method_")
        self.assertEqual(field_name("body", reserved=frozenset({"body", "body_"})), "body__")

    def test_specifiers_are_reserved_for_operations(self) -> None:
        """
        Test that the field specifier names count as reserved (a field
        binding one would shadow the specifier for later fields).
        """
        self.assertEqual(field_name("query", reserved=RESERVED_OPERATION_FIELDS), "query_")

    def test_digits(self) -> None:
        """
        Test that a leading digit is prefixed.
        """
        self.assertEqual(field_name("2fa"), "v_2fa")


class ConstantNameTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.names.constant_name`
    """

    def test_values(self) -> None:
        """
        Test typical enum value spellings.
        """
        self.assertEqual(constant_name("available"), "AVAILABLE")
        self.assertEqual(constant_name("on-sale"), "ON_SALE")
        self.assertEqual(constant_name("notAvailable"), "NOT_AVAILABLE")

    def test_digits(self) -> None:
        """
        Test that a leading digit is prefixed (enum members must not
        start with an underscore — Enum would not treat them as members).
        """
        self.assertEqual(constant_name("1st"), "V_1ST")
        self.assertEqual(constant_name("2xx"), "V_2XX")


class OperationClassNameTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.names.operation_class_name`
    """

    def test_operation_id_wins(self) -> None:
        """
        Test that an operationId is used when present.
        """
        self.assertEqual(operation_class_name("listPets", "get", "/pets"), "ListPets")

    def test_fallback_from_method_and_path(self) -> None:
        """
        Test the method+path fallback, placeholders included.
        """
        self.assertEqual(operation_class_name(None, "get", "/pets/{petId}"), "GetPetsPetId")
        self.assertEqual(operation_class_name("", "delete", "/pets"), "DeletePets")


class ConverterNameTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.names.converter_name`
    """

    def test_from_class_name(self) -> None:
        """
        Test the converter naming scheme.
        """
        self.assertEqual(converter_name("Pet"), "pet_from_json")
        self.assertEqual(converter_name("OAuth2Token"), "o_auth2_token_from_json")


class PathTestCase(unittest.TestCase):
    """
    tests for the path template helpers
    """

    def test_placeholders(self) -> None:
        """
        Test placeholder extraction, in template order.
        """
        self.assertEqual(path_placeholders("/stores/{storeId}/pets/{petId}"), ("storeId", "petId"))
        self.assertEqual(path_placeholders("/pets"), ())

    def test_malformed_template(self) -> None:
        """
        Test that unbalanced braces raise ValueError (wrapped into a
        SchemaError by the translation stage).
        """
        with self.assertRaises(ValueError):
            path_placeholders("/pets/{petId")

    def test_rewrite(self) -> None:
        """
        Test placeholder renaming; unknown placeholders stay untouched.
        """
        self.assertEqual(
            rewrite_path("/stores/{storeId}/pets/{petId}", {"petId": "pet_id"}),
            "/stores/{storeId}/pets/{pet_id}",
        )


class NameRegistryTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.openapi.names.NameRegistry`
    """

    def test_dedup(self) -> None:
        """
        Test that repeated claims get numbered.
        """
        registry = NameRegistry()
        self.assertEqual(registry.claim("Pet"), "Pet")
        self.assertEqual(registry.claim("Pet"), "Pet2")
        self.assertEqual(registry.claim("Pet"), "Pet3")
        self.assertEqual(registry.claim("Order"), "Order")

    def test_claimed_suffix_is_skipped(self) -> None:
        """
        Test that a numbered variant someone else claimed is skipped.
        """
        registry = NameRegistry()
        self.assertEqual(registry.claim("Pet2"), "Pet2")
        self.assertEqual(registry.claim("Pet"), "Pet")
        self.assertEqual(registry.claim("Pet"), "Pet3")
