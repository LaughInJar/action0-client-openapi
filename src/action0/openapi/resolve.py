"""
Resolution of local ``$ref`` pointers inside an OpenAPI document.

OpenAPI schemas reference shared definitions as JSON pointers like
``#/components/schemas/Pet``. :py:class:`RefResolver` looks such
pointers up in the loaded document and follows chains of them, so the
translation stage can work with plain schema objects. Only *local*
references (into the same document) are supported — remote and file
references raise :py:class:`~action0.openapi.errors.SchemaError`.
"""

from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from .errors import SchemaError


class RefResolver:
    """
    Looks up local ``$ref`` JSON pointers in one OpenAPI document.

    :param document: the loaded schema document
    """

    def __init__(self, document: Mapping[str, Any]) -> None:
        self._document = document

    def lookup(self, ref: str) -> Any:
        """
        Return the node a local JSON pointer refers to.

        >>> resolver = RefResolver({"components": {"schemas": {"Pet": {"type": "object"}}}})
        >>> resolver.lookup("#/components/schemas/Pet")
        {'type': 'object'}

        :param ref: the pointer, e.g. ``#/components/schemas/Pet``
        :return: the referenced node
        :raises SchemaError: if the pointer is not local or does not
            resolve
        """
        if not ref.startswith("#/"):
            raise SchemaError(
                f"unsupported reference {ref!r} — only local references"
                ' ("#/...") into the same document are supported'
            )
        node: Any = self._document
        for token in ref[2:].split("/"):
            token = _unescape(token)
            if isinstance(node, Mapping):
                if token not in node:
                    raise SchemaError(f"broken reference {ref!r}: {token!r} does not exist")
                node = node[token]
            elif isinstance(node, Sequence) and not isinstance(node, str):
                try:
                    node = node[int(token)]
                except (ValueError, IndexError):
                    raise SchemaError(
                        f"broken reference {ref!r}: {token!r} is not a valid list index"
                    ) from None
            else:
                raise SchemaError(f"broken reference {ref!r}: {token!r} cannot be looked up")
        return node

    def deref(self, node: Mapping[str, Any]) -> Mapping[str, Any]:
        """
        Follow a (chain of) ``$ref`` to the actual schema object.

        A node without ``$ref`` is returned as-is, so this is safe to
        call on every schema-shaped node.

        >>> resolver = RefResolver({"components": {"schemas": {"Pet": {"type": "object"}}}})
        >>> resolver.deref({"$ref": "#/components/schemas/Pet"})
        {'type': 'object'}
        >>> resolver.deref({"type": "string"})
        {'type': 'string'}

        :param node: a schema node that may be a reference
        :return: the referenced (or given) schema object
        :raises SchemaError: on non-local, broken or circular
            references, or if the target is not an object
        """
        seen: list[str] = []
        while "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str):
                raise SchemaError(f"invalid $ref value {ref!r} — must be a string")
            if ref in seen:
                raise SchemaError(f"circular reference chain: {' -> '.join([*seen, ref])}")
            seen.append(ref)
            target = self.lookup(ref)
            if not isinstance(target, Mapping):
                raise SchemaError(f"reference {ref!r} does not point at an object")
            node = target
        return node

    @staticmethod
    def ref_name(ref: str) -> str:
        """
        Return the last pointer segment — the component's name.

        >>> RefResolver.ref_name("#/components/schemas/Pet")
        'Pet'

        :param ref: the pointer
        :return: the unescaped final segment
        """
        return _unescape(ref.rsplit("/", 1)[-1])


def _unescape(token: str) -> str:
    """
    Undo RFC 6901 escaping in one pointer segment.

    :param token: the raw segment
    :return: the segment with ``~1`` as ``/`` and ``~0`` as ``~``
    """
    return token.replace("~1", "/").replace("~0", "~")
