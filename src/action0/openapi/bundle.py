"""
Bundling multi-file OpenAPI documents into a single document.

OpenAPI schemas may reference sibling files, as in
``$ref: './components/geo.yaml#/components/schemas/Point'``. The rest
of the pipeline works on one document with local ``#/...`` pointers
only, so :py:func:`bundle_documents` merges a loaded file set (see
:py:func:`~action0.openapi.loader.load_documents`) up front: referenced
components are imported into the root document's ``components``
sections — renamed on a name collision, with a warning — and references
to anything that is not a component are inlined. The result parses
exactly like a hand-bundled single file.
"""

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urlsplit

from .errors import SchemaError
from .resolve import RefResolver

if TYPE_CHECKING:
    from .loader import Documents

#: one reference inside a document: the mapping holding the reference
#: string, the key it is stored under, and whether the site is a
#: ``discriminator`` mapping value (which must stay a reference string)
#: rather than a ``$ref`` (which may be replaced by an inlined node)
_RefSite = tuple[dict[str, Any], str, bool]


def bundle_documents(documents: "Documents") -> tuple[dict[str, Any], list[str]]:
    """
    Merge a loaded multi-file schema into one single-file document.

    Every reference into another file is either *imported* — the target
    sits under a ``#/components/<section>/<Name>`` pointer, so it moves
    into the root document's matching section, keeping its name unless
    that name is already taken — or *inlined* in place, when the target
    is not a component (a deep pointer, or a whole-file reference). A
    single-file document without file references is returned unchanged.

    >>> from action0.openapi.loader import Documents
    >>> documents = Documents(
    ...     root="/specs/zoo.json",
    ...     files={
    ...         "/specs/zoo.json": {
    ...             "openapi": "3.0.3",
    ...             "components": {"schemas": {"Cage": {"$ref": "./geo.json#/components/schemas/Point"}}},
    ...         },
    ...         "/specs/geo.json": {"components": {"schemas": {"Point": {"type": "object"}}}},
    ...     },
    ... )
    >>> document, warnings = bundle_documents(documents)
    >>> document["components"]["schemas"]["Cage"]
    {'$ref': '#/components/schemas/Point'}
    >>> document["components"]["schemas"]["Point"]
    {'type': 'object'}
    >>> warnings
    []

    :param documents: the loaded document set
    :return: the merged document and the bundling warnings (component
        renames)
    :raises SchemaError: on http(s) references, broken pointers, or
        circular references that cannot be represented locally
    """
    root = documents.files[documents.root]
    sites = _ref_sites(root)
    if len(documents.files) == 1 and all(
        isinstance(container[key], str) and container[key].startswith("#")
        for container, key, _ in sites
    ):
        # the common case: one self-contained document — no copy needed
        return root, []
    return _Bundler(documents.files, documents.root).run()


def referenced_files(document: Mapping[str, Any], *, base: str) -> list[str]:
    """
    List the canonical paths of the files a document references.

    >>> referenced_files(
    ...     {"$ref": "./components/geo.yaml#/components/schemas/Point"},
    ...     base="/specs/zoo.yaml",
    ... )
    ['/specs/components/geo.yaml']

    :param document: the decoded document
    :param base: the canonical path of the file holding the document
    :return: the referenced files, in document order, without
        duplicates and without ``base`` itself
    :raises SchemaError: on http(s) or otherwise non-file references
    """
    found: list[str] = []
    for container, key, _ in _ref_sites(document):
        reference = container[key]
        if not isinstance(reference, str):
            continue
        file_key, _pointer = _split_ref(reference, base=base)
        if file_key != base and file_key not in found:
            found.append(file_key)
    return found


def _ref_sites(node: Any) -> list[_RefSite]:
    """
    Collect every reference site in a document, in document order.

    ``$ref`` values are always references; ``discriminator`` mapping
    values are references exactly when they contain a ``/`` or ``#``
    (bare component names are legal there too).

    :param node: the document or any node of it
    :return: the reference sites
    """
    sites: list[_RefSite] = []
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            sites.append((node, "$ref", False))
        mapping = node.get("discriminator")
        if isinstance(mapping, dict):
            mapping = mapping.get("mapping")
            if isinstance(mapping, dict):
                for tag, target in mapping.items():
                    if isinstance(target, str) and ("/" in target or "#" in target):
                        sites.append((mapping, tag, True))
        for value in node.values():
            sites.extend(_ref_sites(value))
    elif isinstance(node, list):
        for item in node:
            sites.extend(_ref_sites(item))
    return sites


def _split_ref(reference: str, *, base: str) -> tuple[str, str]:
    """
    Canonicalize a reference against the file containing it.

    :param reference: the reference string
    :param base: the canonical path of the containing file
    :return: the canonical path of the referenced file and the JSON
        pointer into it (empty for a whole-file reference)
    :raises SchemaError: for references that are not a local pointer or
        a file path
    """
    if reference.startswith("#"):
        return base, reference[1:]
    scheme = urlsplit(reference).scheme
    if scheme in ("http", "https"):
        raise SchemaError(
            f"unsupported reference {reference!r} — referenced schemas are not"
            " downloaded; save the file next to the schema and reference it by"
            " a relative path"
        )
    if len(scheme) > 1:  # a single letter would be a Windows drive, not a scheme
        raise SchemaError(
            f'unsupported reference {reference!r} — only local "#/..." pointers'
            " and file paths relative to the referencing document are supported"
        )
    path, _, pointer = reference.partition("#")
    return str((Path(base).parent / path).resolve()), pointer


def _escape(token: str) -> str:
    """
    Apply RFC 6901 escaping to one pointer segment.

    :param token: the plain segment
    :return: the segment with ``~`` as ``~0`` and ``/`` as ``~1``
    """
    return token.replace("~", "~0").replace("/", "~1")


def _unescape(token: str) -> str:
    """
    Undo RFC 6901 escaping in one pointer segment.

    :param token: the raw segment
    :return: the segment with ``~1`` as ``/`` and ``~0`` as ``~``
    """
    return token.replace("~1", "/").replace("~0", "~")


class _Bundler:
    """
    One bundling run: merges a file set into a copy of its root.

    :param files: the decoded documents, canonical path → document
    :param root_key: the canonical path of the root document
    """

    def __init__(self, files: Mapping[str, Mapping[str, Any]], root_key: str) -> None:
        self._files = files
        self._root_key = root_key
        self._out: dict[str, Any] = copy.deepcopy(dict(files[root_key]))
        #: canonical (file, pointer) → the local reference it became
        self._assigned: dict[tuple[str, str], str] = {}
        self._warnings: list[str] = []

    def run(self) -> tuple[dict[str, Any], list[str]]:
        """
        Bundle the file set.

        :return: the merged document and the warnings
        :raises SchemaError: on broken or unrepresentable references
        """
        self._rewrite(self._out, base=self._root_key, stack=())
        return self._out, self._warnings

    def _rewrite(self, node: Any, *, base: str, stack: tuple[tuple[str, str], ...]) -> None:
        """
        Rewrite every reference under ``node`` to a local one.

        :param node: the node to rewrite in place
        :param base: the canonical path of the file the node came from
        :param stack: the chain of anonymous targets currently being
            inlined, for cycle detection
        """
        for container, key, is_mapping_value in _ref_sites(node):
            reference = container[key]
            if not isinstance(reference, str):
                continue  # the parser reports invalid $ref values later
            file_key, pointer = _split_ref(reference, base=base)
            if file_key == self._root_key:
                container[key] = "#" + pointer
                continue
            tokens = pointer[1:].split("/") if pointer.startswith("/") else []
            if len(tokens) == 3 and tokens[0] == "components":
                container[key] = self._import_component(file_key, pointer, tokens)
            elif is_mapping_value:
                raise SchemaError(
                    f"discriminator mapping reference {reference!r} does not point at"
                    f" a component of {file_key} — only components can be mapped"
                )
            else:
                self._inline(container, reference, file_key, pointer, stack=stack)

    def _import_component(self, file_key: str, pointer: str, tokens: list[str]) -> str:
        """
        Import one component of another file into the root document.

        The component keeps its name unless the root's section already
        has a different definition under it — then a numbered name is
        picked and a warning recorded. An identical, reference-free
        definition is shared silently instead.

        :param file_key: the canonical path of the defining file
        :param pointer: the pointer to the component
        :param tokens: the pointer's three segments, still escaped
        :return: the local reference to the imported component
        :raises SchemaError: if the target is missing or not an object
        """
        assigned = self._assigned.get((file_key, pointer))
        if assigned is not None:
            return assigned
        target = self._lookup(file_key, pointer)
        if not isinstance(target, Mapping):
            raise SchemaError(f"reference {file_key}#{pointer} does not point at an object")
        section, name = _unescape(tokens[1]), _unescape(tokens[2])
        components: dict[str, Any] = self._out.setdefault("components", {})
        entries: dict[str, Any] = components.setdefault(section, {})
        local_name = name
        if name in entries:
            if entries[name] == target and not _ref_sites(target):
                # the same self-contained definition again: share it
                local_name = name
            else:
                suffix = 2
                while f"{name}{suffix}" in entries:
                    suffix += 1
                local_name = f"{name}{suffix}"
                self._warnings.append(
                    f"{file_key}#{pointer}: the name {name!r} is already taken in"
                    f" components.{section} — imported as {local_name!r}"
                )
        local_ref = f"#/components/{_escape(section)}/{_escape(local_name)}"
        # register before rewriting: circular components must find it
        self._assigned[(file_key, pointer)] = local_ref
        if local_name not in entries:
            copied = copy.deepcopy(target)
            entries[local_name] = copied
            self._rewrite(copied, base=file_key, stack=())
        return local_ref

    def _inline(
        self,
        container: dict[str, Any],
        reference: str,
        file_key: str,
        pointer: str,
        *,
        stack: tuple[tuple[str, str], ...],
    ) -> None:
        """
        Replace a reference to a non-component with its target.

        :param container: the mapping holding the ``$ref``
        :param reference: the original reference string, for messages
        :param file_key: the canonical path of the defining file
        :param pointer: the pointer to the target
        :param stack: the chain of targets currently being inlined
        :raises SchemaError: if the target is missing, not an object,
            or part of a reference cycle (which has no local spelling
            outside ``components``)
        """
        target_key = (file_key, pointer)
        if target_key in stack:
            chain = " -> ".join(f"{file}#{ptr}" for file, ptr in [*stack, target_key])
            raise SchemaError(f"circular reference chain cannot be inlined: {chain}")
        target = self._lookup(file_key, pointer)
        if not isinstance(target, Mapping):
            raise SchemaError(f"reference {reference!r} does not point at an object")
        copied = copy.deepcopy(dict(target))
        self._rewrite(copied, base=file_key, stack=(*stack, target_key))
        container.clear()
        container.update(copied)

    def _lookup(self, file_key: str, pointer: str) -> Any:
        """
        Return the node a pointer into another file refers to.

        :param file_key: the canonical path of the file
        :param pointer: the pointer into it (empty: the whole document)
        :return: the referenced node
        :raises SchemaError: if the file is not loaded or the pointer
            does not resolve
        """
        document = self._files.get(file_key)
        if document is None:
            raise SchemaError(f"referenced schema file {file_key} is not loaded")
        if not pointer:
            return document
        try:
            return RefResolver(document).lookup("#" + pointer)
        except SchemaError as error:
            raise SchemaError(f"{file_key}: {error}") from error
