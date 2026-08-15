import unittest
from pathlib import Path
from typing import Any

from action0.openapi import Documents
from action0.openapi import SchemaError
from action0.openapi import bundle_documents
from action0.openapi import load_documents
from action0.openapi import parse_api
from action0.openapi.bundle import referenced_files

#: the checked-in multi-file schema
MULTIFILE = Path(__file__).parent / "fixtures" / "multifile" / "zoo.yaml"

#: canonical paths used by the in-memory documents of these tests
ROOT = "/specs/root.json"
PARTS = "/specs/parts/parts.json"


def documents(root: dict[str, Any], **files: dict[str, Any]) -> Documents:
    """
    Build an in-memory document set.

    :param root: the root document, stored as ``/specs/root.json``
    :param files: further documents, ``name`` → ``/specs/parts/<name>.json``
    :return: the document set
    """
    return Documents(
        root=ROOT,
        files={ROOT: root, **{f"/specs/parts/{name}.json": doc for name, doc in files.items()}},
    )


class ReferencedFilesTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.bundle.referenced_files`
    """

    def test_local_refs_are_not_files(self) -> None:
        """
        Test that "#/..." pointers do not show up as referenced files.
        """
        document = {"a": {"$ref": "#/components/schemas/Pet"}}
        self.assertEqual(referenced_files(document, base=ROOT), [])

    def test_relative_paths_resolve_against_the_referencing_file(self) -> None:
        """
        Test path resolution, deduplication and document order.
        """
        document = {
            "a": {"$ref": "./x.json#/components/schemas/A"},
            "b": {"$ref": "sub/y.json#/components/schemas/B"},
            "c": {"$ref": "./x.json#/components/schemas/C"},
            "d": {"$ref": "../z.json"},
        }
        self.assertEqual(
            referenced_files(document, base=PARTS),
            ["/specs/parts/x.json", "/specs/parts/sub/y.json", "/specs/z.json"],
        )

    def test_self_reference_by_file_name_is_not_external(self) -> None:
        """
        Test that a file referencing itself by name is not listed.
        """
        document = {"a": {"$ref": "root.json#/components/schemas/A"}}
        self.assertEqual(referenced_files(document, base=ROOT), [])

    def test_discriminator_mapping_values_are_scanned(self) -> None:
        """
        Test that mapping values that are references count; bare
        component names do not.
        """
        document = {
            "discriminator": {
                "propertyName": "kind",
                "mapping": {
                    "cat": "Cat",
                    "dog": "./x.json#/components/schemas/Dog",
                },
            }
        }
        self.assertEqual(referenced_files(document, base=ROOT), ["/specs/x.json"])

    def test_http_reference_is_listed_as_url(self) -> None:
        """
        Test that an absolute http(s) reference becomes a URL key.
        """
        document = {"a": {"$ref": "https://example.com/geo.yaml#/components/schemas/P"}}
        self.assertEqual(referenced_files(document, base=ROOT), ["https://example.com/geo.yaml"])

    def test_url_base_resolves_relative_references(self) -> None:
        """
        Test that references in a downloaded document resolve against
        its URL — including absolute paths, which stay on the host, so
        a downloaded document can never reach local files.
        """
        document = {
            "a": {"$ref": "./geo.yaml#/components/schemas/P"},
            "b": {"$ref": "../shared.yaml"},
            "c": {"$ref": "/etc/passwd"},
        }
        self.assertEqual(
            referenced_files(document, base="https://example.com/api/v1/root.yaml"),
            [
                "https://example.com/api/v1/geo.yaml",
                "https://example.com/api/shared.yaml",
                "https://example.com/etc/passwd",
            ],
        )

    def test_unknown_scheme_rejected(self) -> None:
        """
        Test the error for other URL schemes.
        """
        document = {"a": {"$ref": "ftp://example.com/geo.yaml"}}
        with self.assertRaisesRegex(SchemaError, "unsupported reference"):
            referenced_files(document, base=ROOT)


class BundleDocumentsTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.bundle_documents`
    """

    def test_single_file_passes_through(self) -> None:
        """
        Test that a self-contained document is returned unchanged, as
        the same object, without warnings.
        """
        root = {"components": {"schemas": {"Pet": {"$ref": "#/components/schemas/Pet"}}}}
        document, warnings = bundle_documents(documents(root))
        self.assertIs(document, root)
        self.assertEqual(warnings, [])

    def test_self_file_reference_becomes_local(self) -> None:
        """
        Test that a reference into the root by its own file name is
        rewritten to a plain local pointer.
        """
        root = {
            "components": {
                "schemas": {
                    "Pet": {"type": "object"},
                    "Alias": {"$ref": "root.json#/components/schemas/Pet"},
                }
            }
        }
        document, warnings = bundle_documents(documents(root))
        self.assertEqual(
            document["components"]["schemas"]["Alias"],
            {"$ref": "#/components/schemas/Pet"},
        )
        self.assertEqual(warnings, [])

    def test_component_import(self) -> None:
        """
        Test that a referenced component moves into the root under its
        name and the reference goes local.
        """
        root = {"a": {"$ref": "./parts/parts.json#/components/schemas/Pet"}}
        parts = {"components": {"schemas": {"Pet": {"type": "object"}}}}
        document, warnings = bundle_documents(documents(root, parts=parts))
        self.assertEqual(document["a"], {"$ref": "#/components/schemas/Pet"})
        self.assertEqual(document["components"]["schemas"]["Pet"], {"type": "object"})
        self.assertEqual(warnings, [])

    def test_transitive_import(self) -> None:
        """
        Test that imports follow references inside imported components,
        across a third file.
        """
        root = {"a": {"$ref": "./parts/parts.json#/components/schemas/Pet"}}
        parts = {
            "components": {"schemas": {"Pet": {"$ref": "./deep.json#/components/schemas/Leaf"}}}
        }
        deep = {"components": {"schemas": {"Leaf": {"type": "string"}}}}
        document, _ = bundle_documents(documents(root, parts=parts, deep=deep))
        schemas = document["components"]["schemas"]
        self.assertEqual(schemas["Pet"], {"$ref": "#/components/schemas/Leaf"})
        self.assertEqual(schemas["Leaf"], {"type": "string"})

    def test_import_happens_once(self) -> None:
        """
        Test that two references to the same component share one import.
        """
        root = {
            "a": {"$ref": "./parts/parts.json#/components/schemas/Pet"},
            "b": {"$ref": "./parts/parts.json#/components/schemas/Pet"},
        }
        parts = {"components": {"schemas": {"Pet": {"type": "object"}}}}
        document, _ = bundle_documents(documents(root, parts=parts))
        self.assertEqual(document["a"], document["b"])
        self.assertEqual(list(document["components"]["schemas"]), ["Pet"])

    def test_name_collision_renames_with_warning(self) -> None:
        """
        Test that a differing definition under a taken name is imported
        under a numbered name, with a warning, and both stay reachable.
        """
        root = {
            "a": {"$ref": "./parts/parts.json#/components/schemas/Tag"},
            "components": {"schemas": {"Tag": {"type": "object"}}},
        }
        parts = {"components": {"schemas": {"Tag": {"type": "string"}}}}
        document, warnings = bundle_documents(documents(root, parts=parts))
        schemas = document["components"]["schemas"]
        self.assertEqual(document["a"], {"$ref": "#/components/schemas/Tag2"})
        self.assertEqual(schemas["Tag"], {"type": "object"})
        self.assertEqual(schemas["Tag2"], {"type": "string"})
        self.assertEqual(len(warnings), 1)
        self.assertIn("'Tag' is already taken", warnings[0])
        self.assertIn("imported as 'Tag2'", warnings[0])

    def test_collision_counter_skips_taken_names(self) -> None:
        """
        Test that the numbered fallback name skips names in use.
        """
        root = {
            "a": {"$ref": "./parts/parts.json#/components/schemas/Tag"},
            "components": {"schemas": {"Tag": {"type": "object"}, "Tag2": {"type": "integer"}}},
        }
        parts = {"components": {"schemas": {"Tag": {"type": "string"}}}}
        document, warnings = bundle_documents(documents(root, parts=parts))
        self.assertEqual(document["a"], {"$ref": "#/components/schemas/Tag3"})
        self.assertEqual(document["components"]["schemas"]["Tag3"], {"type": "string"})
        self.assertEqual(len(warnings), 1)

    def test_identical_definition_is_shared(self) -> None:
        """
        Test that an identical, reference-free twin is not duplicated
        and produces no warning.
        """
        money = {"type": "object", "properties": {"amount": {"type": "number"}}}
        root = {
            "a": {"$ref": "./parts/parts.json#/components/schemas/Money"},
            "components": {"schemas": {"Money": dict(money)}},
        }
        parts = {"components": {"schemas": {"Money": dict(money)}}}
        document, warnings = bundle_documents(documents(root, parts=parts))
        self.assertEqual(document["a"], {"$ref": "#/components/schemas/Money"})
        self.assertEqual(list(document["components"]["schemas"]), ["Money"])
        self.assertEqual(warnings, [])

    def test_back_reference_to_the_root(self) -> None:
        """
        Test that an imported component referencing the root document
        gets a plain local pointer.
        """
        root = {
            "a": {"$ref": "./parts/parts.json#/components/schemas/Pet"},
            "components": {"schemas": {"Home": {"type": "object"}}},
        }
        parts = {
            "components": {"schemas": {"Pet": {"$ref": "../root.json#/components/schemas/Home"}}}
        }
        document, _ = bundle_documents(documents(root, parts=parts))
        self.assertEqual(
            document["components"]["schemas"]["Pet"],
            {"$ref": "#/components/schemas/Home"},
        )

    def test_cross_file_component_cycle(self) -> None:
        """
        Test that mutually referencing components in different files
        bundle into an ordinary local cycle.
        """
        root = {"a": {"$ref": "./parts/parts.json#/components/schemas/A"}}
        parts = {"components": {"schemas": {"A": {"$ref": "./deep.json#/components/schemas/B"}}}}
        deep = {"components": {"schemas": {"B": {"$ref": "./parts.json#/components/schemas/A"}}}}
        document, _ = bundle_documents(documents(root, parts=parts, deep=deep))
        schemas = document["components"]["schemas"]
        self.assertEqual(schemas["A"], {"$ref": "#/components/schemas/B"})
        self.assertEqual(schemas["B"], {"$ref": "#/components/schemas/A"})

    def test_non_component_pointer_is_inlined(self) -> None:
        """
        Test that a deep pointer into another file is inlined in place.
        """
        root = {"a": {"$ref": "./parts/parts.json#/components/schemas/Pet/properties/name"}}
        parts = {
            "components": {
                "schemas": {"Pet": {"type": "object", "properties": {"name": {"type": "string"}}}}
            }
        }
        document, _ = bundle_documents(documents(root, parts=parts))
        self.assertEqual(document["a"], {"type": "string"})

    def test_whole_file_reference_is_inlined(self) -> None:
        """
        Test that a reference without a pointer inlines the whole file.
        """
        root = {"a": {"$ref": "./parts/parts.json"}}
        parts = {"type": "object", "required": ["x"]}
        document, _ = bundle_documents(documents(root, parts=parts))
        self.assertEqual(document["a"], {"type": "object", "required": ["x"]})

    def test_inlined_node_keeps_no_stale_siblings(self) -> None:
        """
        Test that keys next to an inlined ``$ref`` are dropped, like a
        reference lookup would.
        """
        root = {"a": {"$ref": "./parts/parts.json", "stale": True}}
        parts = {"type": "object"}
        document, _ = bundle_documents(documents(root, parts=parts))
        self.assertEqual(document["a"], {"type": "object"})

    def test_anonymous_cycle_rejected(self) -> None:
        """
        Test that a reference cycle outside components cannot be
        inlined and names the chain.
        """
        root = {"a": {"$ref": "./parts/parts.json#/loop"}}
        parts = {"loop": {"$ref": "./parts.json#/loop"}}
        with self.assertRaisesRegex(SchemaError, "circular.*#/loop"):
            bundle_documents(documents(root, parts=parts))

    def test_non_object_inline_target_rejected(self) -> None:
        """
        Test that inlining a non-object node is an error.
        """
        root = {"a": {"$ref": "./parts/parts.json#/components/schemas/Pet/required"}}
        parts = {"components": {"schemas": {"Pet": {"required": ["name"]}}}}
        with self.assertRaisesRegex(SchemaError, "does not point at an object"):
            bundle_documents(documents(root, parts=parts))

    def test_broken_pointer_names_the_file(self) -> None:
        """
        Test that a broken pointer error carries the file's path.
        """
        root = {"a": {"$ref": "./parts/parts.json#/components/schemas/Gone"}}
        parts: dict[str, Any] = {"components": {"schemas": {}}}
        with self.assertRaisesRegex(SchemaError, "parts.json.*broken reference"):
            bundle_documents(documents(root, parts=parts))

    def test_missing_file_rejected(self) -> None:
        """
        Test the error when a referenced file is not in the set.
        """
        root = {"a": {"$ref": "./gone.json#/components/schemas/Pet"}}
        with self.assertRaisesRegex(SchemaError, "gone.json is not loaded"):
            bundle_documents(documents(root))

    def test_discriminator_mapping_rewritten(self) -> None:
        """
        Test that a mapping value referencing another file's component
        is rewritten to the imported local reference.
        """
        root = {
            "a": {
                "discriminator": {
                    "propertyName": "kind",
                    "mapping": {"cat": "./parts/parts.json#/components/schemas/Cat"},
                }
            }
        }
        parts = {"components": {"schemas": {"Cat": {"type": "object"}}}}
        document, _ = bundle_documents(documents(root, parts=parts))
        self.assertEqual(
            document["a"]["discriminator"]["mapping"],
            {"cat": "#/components/schemas/Cat"},
        )

    def test_discriminator_mapping_to_non_component_rejected(self) -> None:
        """
        Test that a mapping value must reference a component — it has
        to stay a reference string, so nothing can be inlined there.
        """
        root = {
            "a": {
                "discriminator": {
                    "propertyName": "kind",
                    "mapping": {"cat": "./parts/parts.json#/x"},
                }
            }
        }
        parts = {"x": {"type": "object"}}
        with self.assertRaisesRegex(SchemaError, "only components can be mapped"):
            bundle_documents(documents(root, parts=parts))

    def test_url_keyed_documents_bundle(self) -> None:
        """
        Test that a document set loaded from URLs bundles like one
        loaded from disk.
        """
        loaded = Documents(
            root="https://example.com/api/root.json",
            files={
                "https://example.com/api/root.json": {
                    "a": {"$ref": "./parts.json#/components/schemas/Pet"}
                },
                "https://example.com/api/parts.json": {
                    "components": {"schemas": {"Pet": {"type": "object"}}}
                },
            },
        )
        document, warnings = bundle_documents(loaded)
        self.assertEqual(document["a"], {"$ref": "#/components/schemas/Pet"})
        self.assertEqual(document["components"]["schemas"]["Pet"], {"type": "object"})
        self.assertEqual(warnings, [])

    def test_escaped_pointer_segments(self) -> None:
        """
        Test that RFC 6901 escapes in component names survive the
        import round trip.
        """
        root = {"a": {"$ref": "./parts/parts.json#/components/schemas/a~1b"}}
        parts = {"components": {"schemas": {"a/b": {"type": "string"}}}}
        document, _ = bundle_documents(documents(root, parts=parts))
        self.assertEqual(document["a"], {"$ref": "#/components/schemas/a~1b"})
        self.assertEqual(document["components"]["schemas"]["a/b"], {"type": "string"})


class MultiFileFixtureTestCase(unittest.TestCase):
    """
    the multifile fixture through the load → bundle → parse pipeline
    """

    def test_pipeline(self) -> None:
        """
        Test loading, bundling and parsing the zoo fixture: transitive
        imports, the Tag collision, the shared Money, the inlined
        whole-file schema, and the imported parameter component.
        """
        loaded = load_documents(MULTIFILE)
        self.assertEqual(len(loaded.files), 4)
        document, warnings = bundle_documents(loaded)
        self.assertEqual(len(warnings), 1)
        self.assertIn("'Tag' is already taken", warnings[0])
        schemas = document["components"]["schemas"]
        # the imported Animal references the renamed Tag2, the shared
        # Money, the imported Id, and the root's Exhibit
        self.assertEqual(
            schemas["Animal"]["properties"]["tag"], {"$ref": "#/components/schemas/Tag2"}
        )
        self.assertEqual(
            schemas["Animal"]["properties"]["price"],
            {"$ref": "#/components/schemas/Money"},
        )
        self.assertEqual(
            schemas["Animal"]["properties"]["exhibit"],
            {"$ref": "#/components/schemas/Exhibit"},
        )
        self.assertEqual(schemas["Animal"]["properties"]["nickname"], {"type": "string"})
        # the whole-file point.yaml reference was inlined into Tag
        self.assertEqual(schemas["Tag"]["properties"]["location"]["type"], "object")
        self.assertIn("limit", document["components"]["parameters"])

        api = parse_api(document)
        model_names = sorted(model.name for model in api.models)
        self.assertEqual(
            model_names, ["Animal", "Exhibit", "Id", "Money", "Tag", "Tag2", "TagLocation"]
        )
        self.assertEqual(
            [operation.class_name for operation in api.operations],
            ["ListAnimals", "ListTags"],
        )
        list_animals = api.operations[0]
        self.assertEqual([param.name for param in list_animals.params], ["limit"])
