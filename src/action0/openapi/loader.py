"""
Loading OpenAPI schema documents from disk or via http(s).

:py:func:`load_schema` reads a JSON or YAML file into the plain
``dict`` the rest of the pipeline works on and verifies it actually is
an OpenAPI 3.x document. :py:func:`load_documents` additionally follows
file references (``./components/geo.yaml#/...``) and loads every
referenced file too, for :py:func:`~action0.openapi.bundle.bundle_documents`
to merge; its schema source may also be an http(s) URL, and referenced
files download — each one gated by the caller's ``allow_download``
consent callback. JSON needs nothing beyond the stdlib; YAML needs
`PyYAML <https://pyyaml.org/>`_, installable as the ``yaml`` extra of
this package — the import happens lazily, so JSON-only users never
touch it.
"""

import dataclasses
import json
import urllib.request
from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from .bundle import is_url
from .bundle import referenced_files
from .errors import SchemaError

#: file suffixes read as YAML (everything else is tried as JSON first)
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})

#: OpenAPI versions the generator understands
_SUPPORTED_VERSION_PREFIXES = ("3.0", "3.1")

#: seconds before a schema download is abandoned
_DOWNLOAD_TIMEOUT = 30.0


@dataclasses.dataclass(frozen=True)
class Documents:
    """
    An OpenAPI document plus every file it references, loaded.

    Keys are canonical file paths (absolute, symlinks and ``..``
    resolved) or http(s) URLs, so the same file referenced from two
    places is loaded — and later bundled — only once.

    :param root: the canonical path or URL of the root document
    :param files: the decoded documents, canonical path/URL → document
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

    return _check_document(str(path), _decode(str(path), path.suffix, text))


def load_documents(
    source: "Path | str", *, allow_download: "Callable[[str], bool] | None" = None
) -> Documents:
    """
    Load a schema and, recursively, every file its ``$ref``\\ s point at.

    The source may be a file path or an http(s) URL; naming a URL as
    the source is the consent to fetch it. Relative reference paths
    resolve against the file containing them — against its URL, for a
    downloaded document, which can therefore only ever reference
    further URLs, never local files. Referenced *URLs* were not named
    by the caller, so each one downloads only after ``allow_download``
    approves it; without the callback, any referenced URL is an error.
    Referenced files are decoded like schemas but not validated as
    OpenAPI documents — component-only fragment files have no
    ``openapi`` version field. The loaded set is merged into a single
    document by :py:func:`~action0.openapi.bundle.bundle_documents`.

    :param source: the root schema file or URL
    :param allow_download: called with each referenced URL; returning
        ``True`` permits the download
    :return: the loaded document set
    :raises SchemaError: if any file cannot be read, downloaded or
        decoded, a download is not approved, the root is not an
        OpenAPI 3.0/3.1 schema, or a reference has an unsupported
        scheme
    """
    source = str(source)
    if is_url(source):
        # the caller spelled this URL out — no extra consent needed
        document = _check_document(source, _decode(source, _url_suffix(source), _download(source)))
        root_key = source
    else:
        document = load_schema(source)
        root_key = str(Path(source).resolve())
    files: dict[str, dict[str, Any]] = {root_key: document}
    queue = [root_key]
    while queue:
        base = queue.pop(0)
        for target in referenced_files(files[base], base=base):
            if target in files:
                continue
            if is_url(target):
                files[target] = _download_fragment(
                    target, referrer=base, allow_download=allow_download
                )
            else:
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
    return _check_fragment(str(path), _decode(str(path), path.suffix, text))


def _download_fragment(
    url: str, *, referrer: str, allow_download: "Callable[[str], bool] | None"
) -> dict[str, Any]:
    """
    Download a referenced schema file, if the caller consents.

    :param url: the referenced URL
    :param referrer: the canonical path or URL of the referencing
        file, for error messages
    :param allow_download: the consent callback; ``None`` refuses
    :return: the decoded document
    :raises SchemaError: if the download is not approved or fails, or
        the document is not an object at the top level
    """
    if allow_download is None:
        raise SchemaError(
            f"{referrer}: references {url} — pass allow_download to permit downloading it"
        )
    if not allow_download(url):
        raise SchemaError(f"download of {url} declined")
    return _check_fragment(url, _decode(url, _url_suffix(url), _download(url)))


def _download(url: str) -> str:
    """
    Fetch a schema document over http(s).

    :param url: the schema URL
    :return: the document text
    :raises SchemaError: if the download fails or is not UTF-8
    """
    # lazy import: the package's __init__ imports this module
    from action0.openapi import __version__

    request = urllib.request.Request(
        url, headers={"User-Agent": f"action0-client-openapi/{__version__}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as response:
            payload: bytes = response.read()
        return payload.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SchemaError(f"{url}: cannot download the schema ({error})") from error


def _url_suffix(url: str) -> str:
    """
    Return the file suffix of a URL's path, for format detection.

    :param url: the schema URL
    :return: the suffix, e.g. ``.yaml``, or an empty string
    """
    return PurePosixPath(urlsplit(url).path).suffix


def _check_fragment(source: str, document: Any) -> dict[str, Any]:
    """
    Verify a referenced document is an object at the top level.

    :param source: the file path or URL, for error messages
    :param document: the decoded document
    :return: the document, now known to be a ``dict``
    :raises SchemaError: if it is not a mapping
    """
    if not isinstance(document, dict):
        raise SchemaError(f"{source}: the referenced schema must be a JSON/YAML object")
    return document


def _decode(source: str, suffix: str, text: str) -> Any:
    """
    Decode schema content as JSON or YAML, by file suffix.

    :param source: the file path or URL, for error messages
    :param suffix: the file suffix deciding the format
    :param text: the content
    :return: the decoded document
    :raises SchemaError: if the text is neither JSON nor YAML, or YAML
        support is not installed
    """
    if suffix.lower() in _YAML_SUFFIXES:
        return _load_yaml(source, text)
    try:
        return json.loads(text)
    except ValueError:
        # JSON is a subset of YAML 1.2: a schema with an unknown
        # suffix that is not JSON may still be YAML
        return _load_yaml(source, text)


def _load_yaml(source: str, text: str) -> Any:
    """
    Parse YAML text, with a helpful error if PyYAML is missing.

    :param source: the file path or URL, for error messages
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
            f"{source}: reading YAML schemas requires PyYAML — install it with the"
            ' "yaml" extra: pip install "action0-client-openapi[yaml]"'
        ) from error

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SchemaError(
            f"{source}: cannot parse the schema as JSON or YAML ({error})"
        ) from error


def _check_document(source: str, document: Any) -> dict[str, Any]:
    """
    Verify the decoded document is an OpenAPI 3.0/3.1 schema.

    :param source: the schema file path or URL, for error messages
    :param document: the decoded document
    :return: the document, now known to be a ``dict``
    :raises SchemaError: if it is not a mapping, is a Swagger 2.0
        document, or declares an unsupported ``openapi`` version
    """
    if not isinstance(document, dict):
        raise SchemaError(f"{source}: the schema must be a JSON/YAML object at the top level")
    if "swagger" in document:
        raise SchemaError(
            f"{source}: Swagger 2.0 documents are not supported — convert the schema to"
            " OpenAPI 3.x"
        )
    version = document.get("openapi")
    if not isinstance(version, str):
        raise SchemaError(
            f'{source}: not an OpenAPI document (missing the "openapi" version field)'
        )
    if version not in _SUPPORTED_VERSION_PREFIXES and not version.startswith(
        tuple(prefix + "." for prefix in _SUPPORTED_VERSION_PREFIXES)
    ):
        raise SchemaError(
            f"{source}: unsupported OpenAPI version {version!r} — supported are 3.0.x and 3.1.x"
        )
    return document
