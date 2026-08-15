"""
Loading OpenAPI schema documents from disk.

:py:func:`load_schema` reads a JSON or YAML file into the plain
``dict`` the rest of the pipeline works on and verifies it actually is
an OpenAPI 3.x document. :py:func:`load_documents` additionally follows
file references (``./components/geo.yaml#/...``) and loads every
referenced file too, for :py:func:`~action0.openapi.bundle.bundle_documents`
to merge. JSON needs nothing beyond the stdlib; YAML needs
`PyYAML <https://pyyaml.org/>`_, installable as the ``yaml`` extra of
this package — the import happens lazily, so JSON-only users never
touch it.
"""

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .bundle import referenced_files
from .errors import SchemaError

#: file suffixes read as YAML (everything else is tried as JSON first)
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})

#: OpenAPI versions the generator understands
_SUPPORTED_VERSION_PREFIXES = ("3.0", "3.1")


@dataclasses.dataclass(frozen=True)
class Documents:
    """
    An OpenAPI document plus every file it references, loaded.

    File paths are canonical (absolute, symlinks and ``..`` resolved) so
    the same file referenced from two places is loaded — and later
    bundled — only once.

    :param root: the canonical path of the root document
    :param files: the decoded documents, canonical path → document
    """

    root: str
    files: Mapping[str, dict[str, Any]]


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

    return _check_document(path, _decode(path, text))


def load_documents(path: "Path | str") -> Documents:
    """
    Load a schema and, recursively, every file its ``$ref``\\ s point at.

    Relative reference paths resolve against the directory of the file
    containing them. Referenced files are decoded like schemas but not
    validated as OpenAPI documents — component-only fragment files have
    no ``openapi`` version field. The loaded set is merged into a
    single document by
    :py:func:`~action0.openapi.bundle.bundle_documents`.

    :param path: the root schema file
    :return: the loaded document set
    :raises SchemaError: if any file cannot be read or decoded, the
        root is not an OpenAPI 3.0/3.1 schema, or a reference is
        neither a local pointer nor a file path
    """
    document = load_schema(path)
    root_key = str(Path(path).resolve())
    files: dict[str, dict[str, Any]] = {root_key: document}
    queue = [root_key]
    while queue:
        base = queue.pop(0)
        for target in referenced_files(files[base], base=base):
            if target not in files:
                files[target] = _load_fragment(Path(target), referrer=base)
                queue.append(target)
    return Documents(root=root_key, files=files)


def _load_fragment(path: Path, *, referrer: str) -> dict[str, Any]:
    """
    Read a referenced schema file, without OpenAPI validation.

    :param path: the referenced file
    :param referrer: the canonical path of the referencing file, for
        error messages
    :return: the decoded document
    :raises SchemaError: if the file cannot be read or decoded, or is
        not an object at the top level
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SchemaError(
            f"{referrer}: cannot read the referenced schema file {path} ({error})"
        ) from error
    document = _decode(path, text)
    if not isinstance(document, dict):
        raise SchemaError(f"{path}: the referenced schema must be a JSON/YAML object")
    return document


def _decode(path: Path, text: str) -> Any:
    """
    Decode schema file content as JSON or YAML, by suffix.

    :param path: the schema file, for the suffix and error messages
    :param text: the file content
    :return: the decoded document
    :raises SchemaError: if the text is neither JSON nor YAML, or YAML
        support is not installed
    """
    if path.suffix.lower() in _YAML_SUFFIXES:
        return _load_yaml(path, text)
    try:
        return json.loads(text)
    except ValueError:
        # JSON is a subset of YAML 1.2: a schema with an unknown
        # suffix that is not JSON may still be YAML
        return _load_yaml(path, text)


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
