"""
Turning OpenAPI spellings into the Python names of the generated code.

OpenAPI documents name things in whatever style the API grew up with —
``camelCase`` properties, ``kebab-case`` headers, ``PascalCase`` or
dotted component names. Generated code follows PEP 8: classes are
``PascalCase``, fields ``snake_case``, enum members ``UPPER_SNAKE``.
The functions here perform that conversion and keep the results *valid*:
Python keywords and context-reserved names get a trailing underscore
(the original spelling survives as the field's wire name), identifiers
that would start with a digit get a ``V``/``v_`` prefix, and
:py:class:`NameRegistry` de-duplicates within one scope.
"""

import keyword
import re
import string
import unicodedata
from collections.abc import Collection
from collections.abc import Mapping

#: splits a raw name into words: pluralized acronyms ("APIs" -> APIs,
#: two capitals at least so "Rs232" stays one word), acronym runs
#: ("HTTPServer" -> HTTP, Server), capitalized words ("petId" -> pet,
#: Id), and lowercase/digit runs; everything non-alphanumeric separates
_WORDS = re.compile(r"[A-Z]{2,}s(?![a-z])|[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

#: names an operation dataclass field must not use: the Operation
#: ClassVars (reserved by action0-client) and the field specifiers the
#: generated operations module imports at module level (a field binding
#: one of these in the class body would shadow the specifier for every
#: later field of the same class)
RESERVED_OPERATION_FIELDS = frozenset(
    {
        "method",
        "path",
        "accept",
        "default_location",
        "query",
        "header",
        "path_param",
        "json_field",
        "json_body",
        "form_field",
        "body",
    }
)


def _words(raw: str) -> list[str]:
    """
    Split a raw name into its words.

    Accented letters lose their accents first ("Poké" becomes "Poke")
    instead of splitting the word; anything still non-ASCII after that
    separates words, as all other punctuation does.

    :param raw: the name as spelled in the schema
    :return: the words, ``["x"]`` if nothing alphanumeric remains
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _WORDS.findall(plain) or ["x"]


def class_name(raw: str) -> str:
    """
    Turn a schema name into a ``PascalCase`` class name.

    >>> class_name("pet-store")
    'PetStore'
    >>> class_name("petStatus")
    'PetStatus'
    >>> class_name("HTTPValidationError")
    'HttpValidationError'
    >>> class_name("APIs.guru")  # pluralized acronyms stay one word
    'ApisGuru'
    >>> class_name("PokéAPI")  # accents are dropped, not word breaks
    'PokeApi'
    >>> class_name("1password")  # leading digit: prefixed
    'V1password'

    :param raw: the name as spelled in the schema
    :return: a valid Python class name
    """
    name = "".join(word.capitalize() for word in _words(raw))
    if name[0].isdigit():
        name = "V" + name
    return name


def field_name(raw: str, *, reserved: Collection[str] = ()) -> str:
    """
    Turn a schema name into a ``snake_case`` field name.

    >>> field_name("petId")
    'pet_id'
    >>> field_name("X-Request-Id")
    'x_request_id'
    >>> field_name("numAPIs")  # pluralized acronyms stay one word
    'num_apis'
    >>> field_name("class")  # Python keyword
    'class_'
    >>> field_name("path", reserved=RESERVED_OPERATION_FIELDS)
    'path_'
    >>> field_name("1st")  # leading digit: prefixed
    'v_1st'

    :param raw: the name as spelled in the schema
    :param reserved: additional names to avoid (e.g.
        :py:data:`RESERVED_OPERATION_FIELDS` for operation fields)
    :return: a valid, non-reserved Python field name
    """
    name = "_".join(word.lower() for word in _words(raw))
    if name[0].isdigit():
        name = "v_" + name
    while keyword.iskeyword(name) or name in reserved:
        name += "_"
    return name


def constant_name(raw: str) -> str:
    """
    Turn an enum value into an ``UPPER_SNAKE`` member name.

    >>> constant_name("on-sale")
    'ON_SALE'
    >>> constant_name("notAvailable")
    'NOT_AVAILABLE'
    >>> constant_name("1st")  # digit-led: enum members must not start with "_"
    'V_1ST'

    :param raw: the enum value as spelled in the schema
    :return: a valid Python enum member name
    """
    name = "_".join(word.upper() for word in _words(raw))
    if name[0].isdigit():
        name = "V_" + name
    return name


def operation_class_name(operation_id: "str | None", method: str, path: str) -> str:
    """
    Name the operation class after the ``operationId``, if there is one,
    and after method and path otherwise.

    >>> operation_class_name("listPets", "get", "/pets")
    'ListPets'
    >>> operation_class_name(None, "get", "/pets/{petId}")
    'GetPetsPetId'

    :param operation_id: the schema's ``operationId``, if any
    :param method: the HTTP method
    :param path: the path as spelled in the schema
    :return: a valid Python class name
    """
    return class_name(operation_id if operation_id else f"{method} {path}")


def converter_name(model_class: str) -> str:
    """
    Name the JSON-to-model converter function for a model class.

    >>> converter_name("Pet")
    'pet_from_json'
    >>> converter_name("HttpError")
    'http_error_from_json'

    :param model_class: the model's Python class name
    :return: the converter function's name
    """
    return f"{field_name(model_class)}_from_json"


def properties_constant_name(model_class: str) -> str:
    """
    Name the declared-properties set constant of a model with a
    catch-all ``additionalProperties`` field.

    The constant holds the wire names of the declared properties; the
    model's converter fills the catch-all field with every payload key
    outside the set.

    >>> properties_constant_name("Pet")
    '_PET_PROPERTIES'
    >>> properties_constant_name("HttpError")
    '_HTTP_ERROR_PROPERTIES'

    :param model_class: the model's Python class name
    :return: the constant's name
    """
    return f"_{field_name(model_class).upper()}_PROPERTIES"


def path_placeholders(path: str) -> tuple[str, ...]:
    """
    Return the ``{placeholder}`` names of a path template, in order.

    >>> path_placeholders("/stores/{storeId}/pets/{petId}")
    ('storeId', 'petId')

    :param path: the path template
    :return: the placeholder names
    :raises ValueError: if the template's braces are malformed
    """
    return tuple(name for _, name, _, _ in string.Formatter().parse(path) if name is not None)


def rewrite_path(path: str, renames: Mapping[str, str]) -> str:
    """
    Rename the ``{placeholder}``\\ s of a path template.

    Placeholder names must equal the Python names of the operation's
    ``path_param()`` fields (action0-client validates that, and the
    specifier deliberately has no wire-name parameter), so the template
    is rewritten to the renamed fields.

    >>> rewrite_path("/pets/{petId}", {"petId": "pet_id"})
    '/pets/{pet_id}'

    :param path: the path template as spelled in the schema
    :param renames: schema spelling to Python name, per placeholder
    :return: the rewritten template
    """
    return re.sub(
        r"\{([^{}]*)\}",
        lambda match: "{" + renames.get(match.group(1), match.group(1)) + "}",
        path,
    )


class NameRegistry:
    """
    De-duplicates names within one scope (module, enum, class).

    The first claim of a name gets it as-is, later claims of the same
    name get a numeric suffix:

    >>> registry = NameRegistry()
    >>> registry.claim("Pet")
    'Pet'
    >>> registry.claim("Pet")
    'Pet2'
    >>> registry.claim("Pet")
    'Pet3'
    """

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def claim(self, preferred: str) -> str:
        """
        Return the preferred name, made unique within this registry.

        :param preferred: the name to claim
        :return: the name, or the first free numbered variant of it
        """
        name = preferred
        count = 1
        while name in self._taken:
            count += 1
            name = f"{preferred}{count}"
        self._taken.add(name)
        return name
