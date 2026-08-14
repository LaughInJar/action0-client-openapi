import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from action0.openapi import Api
from action0.openapi import SecurityKind
from action0.openapi import SecurityScheme
from action0.openapi import load_schema
from action0.openapi import parse_api
from action0.openapi.generate import default_client_name
from action0.openapi.generate import default_package_name
from action0.openapi.generate import generate_package
from action0.openapi.generate import write_package
from action0.openapi.render import render_client

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden" / "petstore_client"


def normalized(text: str) -> str:
    """
    Erase the generator version from a header for comparison.

    :param text: the module text
    :return: the text with the version replaced by ``vX``
    """
    return re.sub(r"action0-client-openapi v\S+", "action0-client-openapi vX", text)


def generate_fixture() -> dict[str, str]:
    """
    Generate the petstore fixture's whole package.

    :return: file name to file content
    """
    api = parse_api(load_schema(FIXTURES / "petstore.json"))
    return generate_package(api, client_name="PetstoreClient", schema_name="petstore.json")


class DefaultNamesTestCase(unittest.TestCase):
    """
    tests for the package/client naming defaults
    """

    def test_package_name(self) -> None:
        """
        Test the package name derivation from schema titles.
        """
        self.assertEqual(default_package_name("Petstore"), "petstore_client")
        self.assertEqual(default_package_name("Tarif API v2"), "tarif_api_v2_client")

    def test_client_name(self) -> None:
        """
        Test the client class name derivation from schema titles.
        """
        self.assertEqual(default_client_name("Petstore"), "PetstoreClient")
        self.assertEqual(default_client_name("tarif-api"), "TarifApiClient")


class GeneratePackageTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.generate.generate_package`
    """

    def test_file_set(self) -> None:
        """
        Test the generated package's file list, py.typed included.
        """
        self.assertEqual(
            sorted(generate_fixture()),
            ["__init__.py", "client.py", "models.py", "operations.py", "py.typed"],
        )

    def test_matches_golden(self) -> None:
        """
        Test that every generated file equals the checked-in golden
        package (modulo the generator version). The golden files are
        exercised for real elsewhere: imported by pytest, type-checked
        by the repository-wide mypy/pyright/ty runs, and ruff-checked in
        test_render.
        """
        for name, content in generate_fixture().items():
            with self.subTest(file=name):
                expected = (GOLDEN / name).read_text(encoding="utf-8")
                self.assertEqual(normalized(content), normalized(expected))


class WritePackageTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.generate.write_package`
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.package_dir = Path(self._tmpdir.name) / "petstore_client"

    def test_writes_all_files(self) -> None:
        """
        Test that all files land on disk, in file-name order.
        """
        files = {"__init__.py": "# a\n", "py.typed": ""}
        written = write_package(files, self.package_dir)
        self.assertEqual(
            written, [self.package_dir / "__init__.py", self.package_dir / "py.typed"]
        )
        self.assertEqual((self.package_dir / "__init__.py").read_text(encoding="utf-8"), "# a\n")

    def test_refuses_to_overwrite(self) -> None:
        """
        Test that an existing file stops the write before anything is
        touched.
        """
        self.package_dir.mkdir(parents=True)
        (self.package_dir / "models.py").write_text("mine", encoding="utf-8")
        with self.assertRaisesRegex(FileExistsError, "models.py.*force"):
            write_package({"__init__.py": "# a\n", "models.py": "# b\n"}, self.package_dir)
        self.assertFalse((self.package_dir / "__init__.py").exists())
        self.assertEqual((self.package_dir / "models.py").read_text(encoding="utf-8"), "mine")

    def test_force_overwrites(self) -> None:
        """
        Test that force replaces existing files.
        """
        self.package_dir.mkdir(parents=True)
        (self.package_dir / "models.py").write_text("mine", encoding="utf-8")
        write_package({"models.py": "# b\n"}, self.package_dir, force=True)
        self.assertEqual((self.package_dir / "models.py").read_text(encoding="utf-8"), "# b\n")


class RenderClientVariantsTestCase(unittest.TestCase):
    """
    tests for the client shapes the petstore golden does not cover
    """

    def test_no_security_no_base_url(self) -> None:
        """
        Test the bare client: no credentials, base_url required.
        """
        text = render_client(Api(title="T", version="1"), "h", "TClient")
        self.assertIn("base_url: str,\n", text)
        self.assertIn("super().__init__(backend, base_url)", text)
        self.assertNotIn("prepare", text)
        self.assertNotIn("headers", text)

    def test_basic_auth(self) -> None:
        """
        Test HTTP basic: username/password become one encoded header.
        """
        api = Api(
            title="T",
            version="1",
            security=(SecurityScheme(kind=SecurityKind.HTTP_BASIC, param_name="username"),),
        )
        text = render_client(api, "h", "TClient")
        self.assertIn("import base64", text)
        self.assertIn("username: str,", text)
        self.assertIn("password: str,", text)
        self.assertIn(
            'credentials = base64.b64encode(f"{username}:{password}".encode()).decode()', text
        )
        self.assertIn('headers={"Authorization": f"Basic {credentials}"}', text)

    def test_api_key_header(self) -> None:
        """
        Test apiKey-in-header: the key lands in the default headers.
        """
        api = Api(
            title="T",
            version="1",
            security=(
                SecurityScheme(
                    kind=SecurityKind.API_KEY_HEADER, param_name="api_key", wire_name="X-API-Key"
                ),
            ),
        )
        text = render_client(api, "h", "TClient")
        self.assertIn('headers={"X-API-Key": api_key}', text)

    def test_executable(self) -> None:
        """
        Test that a rendered client with every scheme kind imports and
        builds its headers (executed against the real action0-client).
        """
        from action0.client.testing import StubBackend

        api = Api(
            title="T",
            version="1",
            base_url="https://t.example.com",
            security=(
                SecurityScheme(kind=SecurityKind.HTTP_BEARER, param_name="token"),
                SecurityScheme(
                    kind=SecurityKind.API_KEY_QUERY, param_name="api_key", wire_name="key"
                ),
            ),
        )
        namespace: dict[str, Any] = {}
        exec(compile(render_client(api, "h", "TClient"), "client.py", "exec"), namespace)
        client = namespace["TClient"](StubBackend(), "secret", "k123")
        self.assertEqual(client.headers.get("Authorization"), "Bearer secret")
