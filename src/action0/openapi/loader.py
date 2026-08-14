"""
Loading OpenAPI schema documents from disk.

:py:func:`load_schema` reads a JSON or YAML file into the plain
``dict`` the rest of the pipeline works on and verifies it actually is
an OpenAPI 3.x document. JSON needs nothing beyond the stdlib; YAML
needs `PyYAML <https://pyyaml.org/>`_, installable as the ``yaml``
extra of this package — the import happens lazily, so JSON-only users
never touch it.
"""

import json
from pathlib import Path
from typing import Any

from .errors import SchemaError

#: file suffixes read as YAML (everything else is tried as JSON first)
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})

#: OpenAPI versions the generator understands
_SUPPORTED_VERSION_PREFIXES = ("3.0", "3.1")


def load_schema(path: "Path | str") -> dict[str, Any]:
    """
    Read an OpenAPI 3.x document from a JSON or YAML file.

    ``.yaml``/``.yml`` files are parsed as YAML, everything else is
    parsed as JSON first and — since JSON is a subset of YAML — as YAML
    if that fails.

    :param path: the schema file
    :return: the decoded document
    :raises SchemaError: if the file cannot be decoded, YAML support is
        not installed, or the document is not an OpenAPI 3.0/3.1 schema
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SchemaError(f"{path}: cannot read schema file ({error})") from error

    if path.suffix.lower() in _YAML_SUFFIXES:
        document = _load_yaml(path, text)
    else:
        try:
            document = json.loads(text)
        except ValueError:
            # JSON is a subset of YAML 1.2: a schema with an unknown
            # suffix that is not JSON may still be YAML
            document = _load_yaml(path, text)

    return _check_document(path, document)


def _load_yaml(path: Path, text: str) -> Any:
    """
    Parse YAML text, with a helpful error if PyYAML is missing.

    :param path: the schema file, for error messages
    :param text: the file content
    :return: the decoded document
    :raises SchemaError: if PyYAML is not installed or the text is not
        valid YAML
    """
    try:
        # deliberately lazy: JSON schemas must work without PyYAML
        import yaml
    except ImportError as error:
        raise SchemaError(
            f"{path}: reading YAML schemas requires PyYAML — install it with the"
            ' "yaml" extra: pip install "action0-client-openapi[yaml]"'
        ) from error

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SchemaError(f"{path}: cannot parse the schema as JSON or YAML ({error})") from error


def _check_document(path: Path, document: Any) -> dict[str, Any]:
    """
    Verify the decoded document is an OpenAPI 3.0/3.1 schema.

    :param path: the schema file, for error messages
    :param document: the decoded document
    :return: the document, now known to be a ``dict``
    :raises SchemaError: if it is not a mapping, is a Swagger 2.0
        document, or declares an unsupported ``openapi`` version
    """
    if not isinstance(document, dict):
        raise SchemaError(f"{path}: the schema must be a JSON/YAML object at the top level")
    if "swagger" in document:
        raise SchemaError(
            f"{path}: Swagger 2.0 documents are not supported — convert the schema to OpenAPI 3.x"
        )
    version = document.get("openapi")
    if not isinstance(version, str):
        raise SchemaError(f'{path}: not an OpenAPI document (missing the "openapi" version field)')
    if version not in _SUPPORTED_VERSION_PREFIXES and not version.startswith(
        tuple(prefix + "." for prefix in _SUPPORTED_VERSION_PREFIXES)
    ):
        raise SchemaError(
            f"{path}: unsupported OpenAPI version {version!r} — supported are 3.0.x and 3.1.x"
        )
    return document
