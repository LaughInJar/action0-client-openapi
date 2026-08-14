import re
import unittest

import action0.openapi


class PackageTestCase(unittest.TestCase):
    """
    tests for the :py:mod:`action0.openapi` package root
    """

    def test_version(self) -> None:
        """
        Test that the version is a non-empty x.y.z string.
        """
        self.assertRegex(action0.openapi.__version__, re.compile(r"^\d+\.\d+\.\d+$"))

    def test_all_exports_exist(self) -> None:
        """
        Test that everything listed in __all__ is actually importable.
        """
        for name in action0.openapi.__all__:
            self.assertTrue(hasattr(action0.openapi, name), f"missing export: {name}")

    def test_dependencies_importable(self) -> None:
        """
        Test that the action0-client dependency (and, through it,
        action0-req and action0-url) resolves inside the same namespace.
        """
        from action0.client import Operation
        from action0.req import Request
        from action0.url import Url

        self.assertTrue(hasattr(Operation, "as_request"))
        self.assertEqual(Request("https://example.com/a").method, "GET")
        self.assertEqual(Url("https://example.com/a").path, "/a")
