import unittest
from typing import Any

from action0.openapi import RefResolver
from action0.openapi import SchemaError

#: a document with the reference shapes the resolver must handle
DOCUMENT: dict[str, Any] = {
    "components": {
        "schemas": {
            "Pet": {"type": "object", "properties": {"name": {"type": "string"}}},
            "Pets": {"type": "array", "items": {"$ref": "#/components/schemas/Pet"}},
            "NewPet": {"$ref": "#/components/schemas/Pet"},
            "Loop": {"$ref": "#/components/schemas/Loop"},
            "PingPong": {"$ref": "#/components/schemas/PongPing"},
            "PongPing": {"$ref": "#/components/schemas/PingPong"},
            "odd/name~x": {"type": "integer"},
        },
        "parameters": [{"name": "limit", "in": "query"}],
    },
}


class LookupTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.openapi.resolve.RefResolver.lookup`
    """

    def setUp(self) -> None:
        self.resolver = RefResolver(DOCUMENT)

    def test_lookup_mapping_path(self) -> None:
        """
        Test looking up a nested mapping node.
        """
        self.assertEqual(
            self.resolver.lookup("#/components/schemas/Pet/properties/name"),
            {"type": "string"},
        )

    def test_lookup_list_index(self) -> None:
        """
        Test that numeric segments index into lists.
        """
        self.assertEqual(
            self.resolver.lookup("#/components/parameters/0"),
            {"name": "limit", "in": "query"},
        )

    def test_lookup_escaped_segments(self) -> None:
        """
        Test RFC 6901 unescaping: ~1 is a slash, ~0 a tilde.
        """
        self.assertEqual(
            self.resolver.lookup("#/components/schemas/odd~1name~0x"),
            {"type": "integer"},
        )

    def test_remote_reference_rejected(self) -> None:
        """
        Test that non-local references are rejected with a clear message.
        """
        for ref in ["other.yaml#/components/schemas/Pet", "https://example.com/api.json#/x", ""]:
            with self.subTest(ref=ref):
                with self.assertRaisesRegex(SchemaError, "only local references"):
                    self.resolver.lookup(ref)

    def test_missing_key(self) -> None:
        """
        Test that a pointer to a missing key names the broken segment.
        """
        with self.assertRaisesRegex(SchemaError, "'Cat' does not exist"):
            self.resolver.lookup("#/components/schemas/Cat")

    def test_bad_list_index(self) -> None:
        """
        Test that non-numeric or out-of-range list indices are rejected.
        """
        for token in ["9", "first"]:
            with self.subTest(token=token):
                with self.assertRaisesRegex(SchemaError, "not a valid list index"):
                    self.resolver.lookup(f"#/components/parameters/{token}")

    def test_lookup_into_scalar(self) -> None:
        """
        Test that traversing into a scalar is rejected.
        """
        with self.assertRaisesRegex(SchemaError, "cannot be looked up"):
            self.resolver.lookup("#/components/schemas/Pet/type/whoops")


class DerefTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.openapi.resolve.RefResolver.deref`
    """

    def setUp(self) -> None:
        self.resolver = RefResolver(DOCUMENT)

    def test_plain_node_passes_through(self) -> None:
        """
        Test that a node without $ref is returned unchanged.
        """
        node = {"type": "string"}
        self.assertIs(self.resolver.deref(node), node)

    def test_single_reference(self) -> None:
        """
        Test following one $ref.
        """
        pet = DOCUMENT["components"]["schemas"]["Pet"]
        self.assertIs(self.resolver.deref({"$ref": "#/components/schemas/Pet"}), pet)

    def test_reference_chain(self) -> None:
        """
        Test following a $ref that points at another $ref.
        """
        pet = DOCUMENT["components"]["schemas"]["Pet"]
        self.assertIs(self.resolver.deref({"$ref": "#/components/schemas/NewPet"}), pet)

    def test_self_cycle(self) -> None:
        """
        Test that a self-referencing $ref chain is detected.
        """
        with self.assertRaisesRegex(SchemaError, "circular reference chain"):
            self.resolver.deref({"$ref": "#/components/schemas/Loop"})

    def test_mutual_cycle(self) -> None:
        """
        Test that a two-step $ref cycle is detected and spelled out.
        """
        with self.assertRaisesRegex(SchemaError, "PingPong.*PongPing.*PingPong"):
            self.resolver.deref({"$ref": "#/components/schemas/PingPong"})

    def test_non_string_ref(self) -> None:
        """
        Test that a non-string $ref value is rejected.
        """
        with self.assertRaisesRegex(SchemaError, "must be a string"):
            self.resolver.deref({"$ref": 42})

    def test_non_object_target(self) -> None:
        """
        Test that a $ref pointing at a non-object is rejected.
        """
        with self.assertRaisesRegex(SchemaError, "does not point at an object"):
            self.resolver.deref({"$ref": "#/components/schemas/Pet/type"})


class RefNameTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.openapi.resolve.RefResolver.ref_name`
    """

    def test_component_name(self) -> None:
        """
        Test extracting the component name from a pointer.
        """
        self.assertEqual(RefResolver.ref_name("#/components/schemas/Pet"), "Pet")

    def test_escaped_name(self) -> None:
        """
        Test that the extracted name is unescaped.
        """
        self.assertEqual(RefResolver.ref_name("#/components/schemas/odd~1name~0x"), "odd/name~x")
