import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from action0.openapi.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


class CliTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.openapi.cli.main`
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.output = Path(self._tmpdir.name)

    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        """
        Run the CLI with captured output.

        :param arguments: the command line arguments
        :return: exit code, stdout, stderr
        """
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_generates_a_package(self) -> None:
        """
        Test the happy path: files written and listed, warnings on
        stderr, derived package name.
        """
        code, stdout, stderr = self.run_cli(
            str(FIXTURES / "petstore.json"), "-o", str(self.output)
        )
        self.assertEqual(code, 0)
        package = self.output / "petstore_client"
        self.assertEqual(
            sorted(path.name for path in package.iterdir()),
            ["__init__.py", "client.py", "models.py", "operations.py", "py.typed"],
        )
        self.assertIn(str(package / "models.py"), stdout)
        self.assertIn("warning: components.securitySchemes.LegacyOAuth", stderr)
        self.assertIn("class PetstoreClient", (package / "client.py").read_text("utf-8"))

    def test_yaml_schema(self) -> None:
        """
        Test that the YAML twin generates the identical package.
        """
        code, _, _ = self.run_cli(str(FIXTURES / "petstore.yaml"), "-o", str(self.output))
        self.assertEqual(code, 0)
        json_dir = self.output / "json" / "petstore_client"
        self.run_cli(str(FIXTURES / "petstore.json"), "-o", str(self.output / "json"))
        for path in (self.output / "petstore_client").iterdir():
            with self.subTest(file=path.name):
                twin = json_dir / path.name
                # the header quotes the schema file name, which differs
                self.assertEqual(
                    path.read_text("utf-8").replace("petstore.yaml", "petstore.json"),
                    twin.read_text("utf-8"),
                )

    def test_multifile_schema(self) -> None:
        """
        Test that a schema referencing other files generates, with the
        bundling warning on stderr.
        """
        code, stdout, stderr = self.run_cli(
            str(FIXTURES / "multifile" / "zoo.yaml"), "-o", str(self.output)
        )
        self.assertEqual(code, 0)
        self.assertIn("warning:", stderr)
        self.assertIn("'Tag' is already taken", stderr)
        models = (self.output / "zoo_client" / "models.py").read_text("utf-8")
        self.assertIn("class Animal:", models)
        self.assertIn("class Tag2:", models)

    def test_name_and_base_url_overrides(self) -> None:
        """
        Test --package-name, --client-name and --base-url.
        """
        code, _, _ = self.run_cli(
            str(FIXTURES / "petstore.json"),
            "-o",
            str(self.output),
            "--package-name",
            "zoo",
            "--client-name",
            "ZooClient",
            "--base-url",
            "https://zoo.example.com",
        )
        self.assertEqual(code, 0)
        client = (self.output / "zoo" / "client.py").read_text("utf-8")
        self.assertIn("class ZooClient", client)
        self.assertIn('base_url: str = "https://zoo.example.com"', client)

    def test_split_by_tag(self) -> None:
        """
        Test that --split-by-tag produces the per-tag layout.
        """
        code, _, _ = self.run_cli(
            str(FIXTURES / "petstore.json"), "-o", str(self.output), "--split-by-tag"
        )
        self.assertEqual(code, 0)
        package = self.output / "petstore_client"
        self.assertTrue((package / "operations_pets.py").exists())
        self.assertTrue((package / "operations_auth.py").exists())
        self.assertIn("GetPetPhoto", (package / "operations.py").read_text("utf-8"))

    def test_existing_output_needs_force(self) -> None:
        """
        Test the refusal to overwrite, and that --force overrides it.
        """
        schema = str(FIXTURES / "petstore.json")
        self.assertEqual(self.run_cli(schema, "-o", str(self.output))[0], 0)
        code, _, stderr = self.run_cli(schema, "-o", str(self.output))
        self.assertEqual(code, 1)
        self.assertIn("error:", stderr)
        self.assertIn("force", stderr)
        self.assertEqual(self.run_cli(schema, "-o", str(self.output), "--force")[0], 0)

    def test_schema_errors_are_one_line(self) -> None:
        """
        Test that input problems exit 1 with a readable message.
        """
        bad = Path(self._tmpdir.name) / "bad.json"
        bad.write_text(json.dumps({"swagger": "2.0"}), encoding="utf-8")
        code, stdout, stderr = self.run_cli(str(bad), "-o", str(self.output))
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("action0-openapi: error:", stderr)
        self.assertIn("Swagger 2.0", stderr)
