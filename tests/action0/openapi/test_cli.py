import contextlib
import http.server
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from action0.openapi import SchemaError
from action0.openapi.cli import _ask
from action0.openapi.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


class RunCliTestCase(unittest.TestCase):
    """
    base class: an output directory and a captured CLI runner
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


class CliTestCase(RunCliTestCase):
    """
    tests for :py:func:`action0.openapi.cli.main`
    """

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
            ["__init__.py", "client.py", "errors.py", "models.py", "operations.py", "py.typed"],
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


class CliDownloadTestCase(RunCliTestCase):
    """
    tests for URL schemas and the --download flag, against a local
    server serving the multifile fixture
    """

    server: http.server.ThreadingHTTPServer
    base: str

    @classmethod
    def setUpClass(cls) -> None:
        def handler(*args: Any, **kwargs: Any) -> _QuietHandler:
            return _QuietHandler(*args, directory=str(FIXTURES / "multifile"), **kwargs)

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        # cleanups run last-in-first-out: stop serving, then close
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    def test_url_schema_with_download_flag(self) -> None:
        """
        Test generating from a URL: downloads announced on stderr, the
        schema name in the header is the URL's file name.
        """
        code, _, stderr = self.run_cli(
            f"{self.base}/zoo.yaml", "-o", str(self.output), "--download"
        )
        self.assertEqual(code, 0)
        self.assertIn(f"downloading {self.base}/components/animals.yaml", stderr)
        models = (self.output / "zoo_client" / "models.py").read_text("utf-8")
        self.assertIn("class Tag2:", models)
        self.assertIn("zoo.yaml", models.splitlines()[0])

    def test_referenced_download_needs_flag_or_terminal(self) -> None:
        """
        Test that without --download and without a terminal, the run
        stops with the flag hint (the test runner's stdin is no tty).
        """
        code, stdout, stderr = self.run_cli(f"{self.base}/zoo.yaml", "-o", str(self.output))
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("--download", stderr)


class _FakeTty(io.StringIO):
    """
    Canned interactive input: a StringIO that claims to be a terminal.
    """

    def isatty(self) -> bool:
        """
        Pretend to be interactive.

        :return: always ``True``
        """
        return True


class AskTestCase(unittest.TestCase):
    """
    tests for the interactive download prompt
    """

    def ask(self, stdin: io.StringIO) -> tuple[bool, str]:
        """
        Run the prompt with a canned stdin and captured stderr.

        :param stdin: the fake standard input
        :return: the answer and the prompt text
        """
        stderr = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stderr(stderr):
            answer = _ask("https://example.com/geo.yaml")
        return answer, stderr.getvalue()

    def test_yes(self) -> None:
        """
        Test that y/yes approve and the prompt names the URL.
        """
        for text in ["y\n", "yes\n", " Y \n"]:
            with self.subTest(text=text):
                answer, prompt = self.ask(_FakeTty(text))
                self.assertTrue(answer)
                self.assertIn("download https://example.com/geo.yaml? [y/N]", prompt)

    def test_no_is_the_default(self) -> None:
        """
        Test that anything else — and closed input — declines.
        """
        for text in ["n\n", "\n", ""]:
            with self.subTest(text=text):
                answer, _ = self.ask(_FakeTty(text))
                self.assertFalse(answer)

    def test_no_terminal_raises(self) -> None:
        """
        Test the --download hint when stdin is not a terminal.
        """
        with mock.patch.object(sys, "stdin", io.StringIO()):
            with self.assertRaisesRegex(SchemaError, "--download"):
                _ask("https://example.com/geo.yaml")
