"""
The ``action0-openapi`` command line interface.

One command: read an OpenAPI 3.x schema file, generate the client
package, write it into the output directory. Expected input problems
(:py:class:`~action0.openapi.errors.SchemaError`, an existing output
without ``--force``) are printed as one-line errors without a
traceback; translation warnings go to stderr, the written files to
stdout.
"""

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from pathlib import Path

from .errors import SchemaError
from .generate import default_client_name
from .generate import default_package_name
from .generate import generate_package
from .generate import write_package
from .loader import load_schema
from .parse import parse_api


def _parser() -> argparse.ArgumentParser:
    """
    Build the argument parser.

    :return: the parser
    """
    from action0.openapi import __version__

    parser = argparse.ArgumentParser(
        prog="action0-openapi",
        description=(
            "Generate a fully typed action0-client API client package from an OpenAPI 3.x schema."
        ),
    )
    parser.add_argument(
        "schema",
        type=Path,
        help="the OpenAPI 3.x document (.json, .yaml or .yml)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="the directory to write the generated package into",
    )
    parser.add_argument(
        "--package-name",
        help="the generated package's name (default: derived from the schema title)",
    )
    parser.add_argument(
        "--client-name",
        help="the client class name (default: derived from the schema title)",
    )
    parser.add_argument(
        "--base-url",
        help=(
            "the client's default base URL (overrides the schema's servers; without"
            " either, the generated client requires base_url as an argument)"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing files in the output directory",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"action0-client-openapi {__version__}",
    )
    return parser


def main(argv: "Sequence[str] | None" = None) -> int:
    """
    Run the generator CLI.

    :param argv: the command line arguments (``sys.argv[1:]`` when
        ``None``)
    :return: the exit code — 0 on success, 1 on input errors
    """
    arguments = _parser().parse_args(argv)
    try:
        api = parse_api(load_schema(arguments.schema))
        if arguments.base_url:
            api = dataclasses.replace(api, base_url=arguments.base_url)
        package_name = arguments.package_name or default_package_name(api.title)
        client_name = arguments.client_name or default_client_name(api.title)
        files = generate_package(api, client_name=client_name, schema_name=arguments.schema.name)
        written = write_package(files, arguments.output / package_name, force=arguments.force)
    except (SchemaError, FileExistsError) as error:
        print(f"action0-openapi: error: {error}", file=sys.stderr)
        return 1
    for warning in api.warnings:
        print(f"action0-openapi: warning: {warning}", file=sys.stderr)
    for path in written:
        print(path)
    return 0
