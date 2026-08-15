"""
The ``action0-openapi`` command line interface.

One command: read an OpenAPI 3.x schema — a file or an http(s) URL,
following references to other files, where each referenced *download*
needs an interactive yes or ``--download`` — generate the client
package, write it into the output directory. Expected input problems
(:py:class:`~action0.openapi.errors.SchemaError`, an existing output
without ``--force``) are printed as one-line errors without a
traceback; translation warnings go to stderr, the written files to
stdout.
"""

import argparse
import dataclasses
import sys
from collections.abc import Callable
from collections.abc import Sequence
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from .bundle import bundle_documents
from .bundle import is_url
from .errors import SchemaError
from .generate import default_client_name
from .generate import default_package_name
from .generate import generate_package
from .generate import write_package
from .loader import load_documents
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
        help="the OpenAPI 3.x document (.json, .yaml or .yml) — a file or an http(s) URL",
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
        "--split-by-tag",
        action="store_true",
        help=(
            "put each OpenAPI tag's operations into a module of its own"
            " (operations_<tag>.py) instead of one operations.py"
        ),
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help=(
            "download schema files referenced over http(s) without asking"
            " (otherwise each one needs an interactive yes)"
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


def _schema_name(source: str) -> str:
    """
    The schema's bare file name, quoted in the generated header.

    :param source: the schema file path or URL
    :return: the last path segment
    """
    if is_url(source):
        return PurePosixPath(urlsplit(source).path).name or source
    return Path(source).name


def _approve_all(url: str) -> bool:
    """
    The ``--download`` consent callback: approve, but say so.

    :param url: the referenced URL
    :return: always ``True``
    """
    print(f"action0-openapi: downloading {url}", file=sys.stderr)
    return True


def _ask(url: str) -> bool:
    """
    The interactive consent callback: one ``[y/N]`` prompt per URL.

    :param url: the referenced URL
    :return: whether the user approved the download
    :raises SchemaError: when there is no terminal to ask on
    """
    if not sys.stdin.isatty():
        raise SchemaError(
            f"the schema references {url} — downloading it needs an interactive"
            " yes or the --download flag"
        )
    print(f"action0-openapi: download {url}? [y/N] ", end="", file=sys.stderr, flush=True)
    try:
        return input().strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(argv: "Sequence[str] | None" = None) -> int:
    """
    Run the generator CLI.

    :param argv: the command line arguments (``sys.argv[1:]`` when
        ``None``)
    :return: the exit code — 0 on success, 1 on input errors
    """
    arguments = _parser().parse_args(argv)
    allow_download: Callable[[str], bool] = _approve_all if arguments.download else _ask
    try:
        document, bundle_warnings = bundle_documents(
            load_documents(arguments.schema, allow_download=allow_download)
        )
        api = parse_api(document)
        if arguments.base_url:
            api = dataclasses.replace(api, base_url=arguments.base_url)
        package_name = arguments.package_name or default_package_name(api.title)
        client_name = arguments.client_name or default_client_name(api.title)
        files = generate_package(
            api,
            client_name=client_name,
            schema_name=_schema_name(arguments.schema),
            split_by_tag=arguments.split_by_tag,
        )
        written = write_package(files, arguments.output / package_name, force=arguments.force)
    except (SchemaError, FileExistsError) as error:
        print(f"action0-openapi: error: {error}", file=sys.stderr)
        return 1
    for warning in [*bundle_warnings, *api.warnings]:
        print(f"action0-openapi: warning: {warning}", file=sys.stderr)
    for path in written:
        print(path)
    return 0
