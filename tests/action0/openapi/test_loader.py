import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from action0.openapi import SchemaError
from action0.openapi import load_documents
from action0.openapi import load_schema

#: a minimal valid OpenAPI document
MINIMAL = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {}}

#: the checked-in multi-file schema, also served over HTTP below
MULTIFILE_DIR = Path(__file__).parent / "fixtures" / "multifile"


class SchemaDirTestCase(unittest.TestCase):
    """
    base class holding a temporary directory to write schema files into
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = Path(self._tmpdir.name)

    def write(self, name: str, text: str) -> Path:
        """
        Write a schema file into the test's temporary directory.

        :param name: the file name
        :param text: the file content
        :return: the file's path
        """
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, name: str, document: dict[str, Any]) -> Path:
        """
        Write a document as a JSON schema file.

        :param name: the file name
        :param document: the document
        :return: the file's path
        """
        return self.write(name, json.dumps(document))


class LoadSchemaTestCase(SchemaDirTestCase):
    """
    tests for :py:func:`action0.openapi.loader.load_schema`
    """

    def test_json_file(self) -> None:
        """
        Test that a .json schema loads.
        """
        self.assertEqual(load_schema(self.write_json("api.json", MINIMAL)), MINIMAL)

    def test_yaml_file(self) -> None:
        """
        Test that a .yaml schema loads (PyYAML is a dev dependency).
        """
        path = self.write("api.yaml", "openapi: 3.0.3\ninfo:\n  title: t\npaths: {}\n")
        self.assertEqual(
            load_schema(path), {"openapi": "3.0.3", "info": {"title": "t"}, "paths": {}}
        )

    def test_yml_suffix_counts_as_yaml(self) -> None:
        """
        Test that .yml files are parsed as YAML too.
        """
        path = self.write("api.yml", "openapi: '3.1'\npaths: {}\n")
        self.assertEqual(load_schema(path), {"openapi": "3.1", "paths": {}})

    def test_unknown_suffix_json_content(self) -> None:
        """
        Test that JSON content behind an unknown suffix loads as JSON.
        """
        self.assertEqual(load_schema(self.write("api.txt", json.dumps(MINIMAL))), MINIMAL)

    def test_unknown_suffix_yaml_content(self) -> None:
        """
        Test that YAML content behind an unknown suffix falls back to YAML.
        """
        path = self.write("api.spec", "openapi: 3.0.0\npaths: {}\n")
        self.assertEqual(load_schema(path), {"openapi": "3.0.0", "paths": {}})

    def test_str_path_accepted(self) -> None:
        """
        Test that a plain string path works as well as a Path.
        """
        path = self.write_json("api.json", MINIMAL)
        self.assertEqual(load_schema(str(path)), MINIMAL)

    def test_missing_file(self) -> None:
        """
        Test that an unreadable file becomes a SchemaError naming the file.
        """
        with self.assertRaisesRegex(SchemaError, "gone.json.*cannot read"):
            load_schema(self.tmp / "gone.json")

    def test_missing_pyyaml(self) -> None:
        """
        Test the install-the-extra message when PyYAML is not installed.

        A ``None`` entry in ``sys.modules`` makes ``import yaml`` raise
        ImportError, exactly like an uninstalled package.
        """
        path = self.write("api.yaml", "openapi: 3.1.0\n")
        with mock.patch.dict(sys.modules, {"yaml": None}):
            with self.assertRaisesRegex(SchemaError, r"action0-client-openapi\[yaml\]"):
                load_schema(path)

    def test_undecodable_content(self) -> None:
        """
        Test that content that is neither JSON nor YAML is rejected.
        """
        with self.assertRaisesRegex(SchemaError, "cannot parse"):
            load_schema(self.write("api.yaml", "{ this is : neither: json nor yaml ]"))

    def test_non_object_top_level(self) -> None:
        """
        Test that a top-level array is rejected.
        """
        with self.assertRaisesRegex(SchemaError, "object at the top level"):
            load_schema(self.write("api.json", "[1, 2]"))

    def test_swagger_2_rejected(self) -> None:
        """
        Test the dedicated message for Swagger 2.0 documents.
        """
        path = self.write_json("api.json", {"swagger": "2.0", "info": {}})
        with self.assertRaisesRegex(SchemaError, "Swagger 2.0.*OpenAPI 3.x"):
            load_schema(path)

    def test_missing_version_field(self) -> None:
        """
        Test that a document without the "openapi" field is rejected.
        """
        with self.assertRaisesRegex(SchemaError, "missing.*openapi"):
            load_schema(self.write_json("api.json", {"info": {}}))

    def test_unsupported_version(self) -> None:
        """
        Test that versions other than 3.0.x/3.1.x are rejected.
        """
        for version in ["2.0", "3.2.0", "4.0.0", "3.10.0"]:
            with self.subTest(version=version):
                path = self.write_json("api.json", {"openapi": version})
                with self.assertRaisesRegex(SchemaError, "unsupported OpenAPI version"):
                    load_schema(path)

    def test_supported_versions(self) -> None:
        """
        Test that 3.0/3.1 versions load, with and without patch level.
        """
        for version in ["3.0", "3.1", "3.0.0", "3.0.3", "3.1.0", "3.1.1"]:
            with self.subTest(version=version):
                path = self.write_json("api.json", {"openapi": version})
                self.assertEqual(load_schema(path)["openapi"], version)


class LoadDocumentsTestCase(SchemaDirTestCase):
    """
    tests for :py:func:`action0.openapi.loader.load_documents`
    """

    def test_referenced_files_load_recursively(self) -> None:
        """
        Test that referenced files load, across formats, with the
        canonical root key, and that fragments need no openapi field.
        """
        root = dict(MINIMAL)
        root["components"] = {
            "schemas": {"Pet": {"$ref": "./parts/animals.yaml#/components/schemas/Animal"}}
        }
        (self.tmp / "parts").mkdir()
        self.write(
            "parts/animals.yaml",
            "components:\n"
            "  schemas:\n"
            "    Animal:\n"
            "      $ref: './shared.json#/components/schemas/Id'\n",
        )
        self.write_json(
            "parts/shared.json",
            {"components": {"schemas": {"Id": {"type": "integer"}}}},
        )
        path = self.write_json("api.json", root)
        loaded = load_documents(path)
        self.assertEqual(loaded.root, str(path.resolve()))
        self.assertEqual(len(loaded.files), 3)
        shared_key = str((self.tmp / "parts" / "shared.json").resolve())
        self.assertEqual(
            loaded.files[shared_key],
            {"components": {"schemas": {"Id": {"type": "integer"}}}},
        )

    def test_single_file_document(self) -> None:
        """
        Test that a schema without file references loads alone.
        """
        loaded = load_documents(self.write_json("api.json", MINIMAL))
        self.assertEqual(list(loaded.files.values()), [MINIMAL])

    def test_circular_file_references_load_once(self) -> None:
        """
        Test that files referencing each other load exactly once each.
        """
        a = dict(MINIMAL)
        a["components"] = {"schemas": {"A": {"$ref": "./b.json#/components/schemas/B"}}}
        self.write_json(
            "b.json",
            {"components": {"schemas": {"B": {"$ref": "./a.json#/components/schemas/A"}}}},
        )
        loaded = load_documents(self.write_json("a.json", a))
        self.assertEqual(len(loaded.files), 2)

    def test_missing_referenced_file(self) -> None:
        """
        Test that a missing referenced file names the referrer too.
        """
        root = dict(MINIMAL)
        root["components"] = {"schemas": {"X": {"$ref": "./gone.yaml#/components/schemas/X"}}}
        path = self.write_json("api.json", root)
        with self.assertRaisesRegex(
            SchemaError, r"api\.json: cannot read the referenced schema file.*gone\.yaml"
        ):
            load_documents(path)

    def test_non_object_fragment(self) -> None:
        """
        Test that a referenced file with a non-object top level is
        rejected.
        """
        root = dict(MINIMAL)
        root["components"] = {"schemas": {"X": {"$ref": "./list.json#/0"}}}
        self.write("list.json", "[1, 2]")
        with self.assertRaisesRegex(SchemaError, r"list\.json.*must be a JSON/YAML object"):
            load_documents(self.write_json("api.json", root))

    def test_referenced_url_needs_a_callback(self) -> None:
        """
        Test that a referenced URL is an error without allow_download.
        """
        root = dict(MINIMAL)
        root["components"] = {
            "schemas": {"X": {"$ref": "https://example.com/x.yaml#/components/schemas/X"}}
        }
        with self.assertRaisesRegex(SchemaError, "pass allow_download"):
            load_documents(self.write_json("api.json", root))

    def test_declined_download(self) -> None:
        """
        Test that a refusing callback stops the load, naming the URL.
        """
        root = dict(MINIMAL)
        root["components"] = {
            "schemas": {"X": {"$ref": "https://example.com/x.yaml#/components/schemas/X"}}
        }
        path = self.write_json("api.json", root)
        with self.assertRaisesRegex(SchemaError, "example.com/x.yaml declined"):
            load_documents(path, allow_download=lambda url: False)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """
    A fixture-serving request handler that does not log to stderr.
    """

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """
        Drop the request log line.

        :param format: the log format string
        :param args: the log arguments
        """


class LoadDocumentsHttpTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.loader.load_documents` with
    http(s) sources, against a local server serving the multifile
    fixture
    """

    server: http.server.ThreadingHTTPServer
    base: str

    @classmethod
    def setUpClass(cls) -> None:
        def handler(*args: Any, **kwargs: Any) -> _QuietHandler:
            return _QuietHandler(*args, directory=str(MULTIFILE_DIR), **kwargs)

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        # cleanups run last-in-first-out: stop serving, then close
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    def test_url_root_downloads_referenced_files(self) -> None:
        """
        Test a URL schema: the root needs no consent, every referenced
        file asks, and all keys are URLs.
        """
        asked: list[str] = []

        def allow(url: str) -> bool:
            asked.append(url)
            return True

        loaded = load_documents(f"{self.base}/zoo.yaml", allow_download=allow)
        self.assertEqual(loaded.root, f"{self.base}/zoo.yaml")
        self.assertEqual(
            sorted(loaded.files),
            [
                f"{self.base}/components/animals.yaml",
                f"{self.base}/components/point.yaml",
                f"{self.base}/components/shared.yaml",
                f"{self.base}/zoo.yaml",
            ],
        )
        self.assertEqual(sorted(asked), sorted(set(loaded.files) - {loaded.root}))
        animals = loaded.files[f"{self.base}/components/animals.yaml"]
        self.assertIn("Animal", animals["components"]["schemas"])

    def test_local_schema_referencing_a_url(self) -> None:
        """
        Test a local file with an absolute http reference: the set
        mixes path and URL keys.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = dict(MINIMAL)
            root["components"] = {
                "schemas": {
                    "Id": {"$ref": (f"{self.base}/components/shared.yaml#/components/schemas/Id")}
                }
            }
            path = Path(tmp) / "api.json"
            path.write_text(json.dumps(root), encoding="utf-8")
            loaded = load_documents(path, allow_download=lambda url: True)
        self.assertCountEqual(
            loaded.files,
            [f"{self.base}/components/shared.yaml", str(path.resolve())],
        )

    def test_download_failure(self) -> None:
        """
        Test that a 404 becomes a SchemaError naming the URL.
        """
        with self.assertRaisesRegex(SchemaError, "gone.yaml: cannot download"):
            load_documents(f"{self.base}/gone.yaml")
