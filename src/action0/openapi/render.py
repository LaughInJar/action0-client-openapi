"""
Rendering the intermediate representation as Python source text.

The emitter builds the generated modules with plain string assembly —
no template engine — so the exact output is controlled in one place.
The produced text is already in the shape ``ruff format`` and
``ruff check`` (with this repository's isort settings) accept: import
blocks in isort order with one import per line, two blank lines between
top-level definitions, double quotes, magic trailing commas on
multi-line calls, and lines within the 99-column limit (over-long field
conversions are wrapped the way ruff format wraps them).
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Mapping
from collections.abc import Sequence

from .ir import Api
from .ir import ArrayType
from .ir import Body
from .ir import BodyKind
from .ir import EnumModel
from .ir import EnumType
from .ir import Field
from .ir import MapType
from .ir import Model
from .ir import ModelType
from .ir import OperationIR
from .ir import Param
from .ir import ParamLocation
from .ir import ResponseKind
from .ir import Scalar
from .ir import ScalarType
from .ir import SecurityKind
from .ir import SecurityScheme
from .ir import TypeExpr
from .ir import UnionCase
from .ir import UnionCheck
from .ir import UnionModel
from .ir import UnionType
from .names import converter_name
from .names import properties_constant_name
from .types import annotation
from .types import converter_expr
from .types import imports_for
from .types import needs_conversion

#: the line-length limit generated code must stay inside
_LINE_LIMIT = 99


class Lines:
    """
    An indentation-aware builder for one module's source text.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._level = 0

    def write(self, text: str = "") -> None:
        """
        Append one line at the current indentation.

        :param text: the line's text (empty for a blank line)
        """
        self._lines.append(f"{'    ' * self._level}{text}" if text else "")

    def indent(self) -> None:
        """Increase the indentation by one level."""
        self._level += 1

    def dedent(self) -> None:
        """Decrease the indentation by one level."""
        self._level -= 1

    @property
    def level(self) -> int:
        """The current indentation level."""
        return self._level

    def extend(self, other: Lines) -> None:
        """
        Append another builder's lines verbatim.

        :param other: the builder whose lines to append
        """
        self._lines.extend(other._lines)

    def separate(self, blank_lines: int = 2) -> None:
        """
        Ensure the given number of blank lines before what comes next.

        :param blank_lines: how many blank lines separate the blocks
        """
        if not self._lines:
            return
        while self._lines and self._lines[-1] == "":
            self._lines.pop()
        self._lines.extend([""] * blank_lines)

    def comment(self, text: str) -> None:
        """
        Append a ``#:`` documentation comment at the current indentation.

        Sphinx autodoc reads consecutive ``#:`` lines above an attribute
        as its documentation. Each line of the text is wrapped to the
        line-length limit; blank lines are dropped (they would render as
        stray empty comments between fields).

        :param text: the comment text
        """
        width = _LINE_LIMIT - self._level * 4 - len("#: ")
        for raw_line in text.splitlines():
            for line in textwrap.wrap(
                raw_line.strip(), width=width, break_long_words=False, break_on_hyphens=False
            ):
                self.write(f"#: {line}")

    def docstring(self, text: str) -> None:
        """
        Append a docstring at the current indentation.

        A single short line becomes a one-line docstring, anything else
        a block. Triple quotes inside the text are defused.

        :param text: the docstring text
        """
        text = text.replace('"""', "'''").strip()
        lines = text.splitlines()
        if len(lines) == 1 and len(lines[0]) + self._level * 4 + 6 <= _LINE_LIMIT:
            self.write(f'"""{lines[0]}"""')
            return
        self.write('"""')
        for line in lines:
            self.write(line.rstrip())
        self.write('"""')

    def text(self) -> str:
        """
        Render the collected module text.

        :return: the source text, with a trailing newline
        """
        return "\n".join(self._lines).rstrip("\n") + "\n"


class Imports:
    """
    Collects import statements and renders them in isort order.

    The order replicates this repository's ruff isort settings (one
    import per line, ``action0`` first-party): the ``__future__`` block,
    the stdlib block, the ``action0`` block, and relative imports last —
    within each block all plain ``import X`` lines first, then the
    ``from X import ...`` lines, each alphabetically.
    """

    def __init__(self) -> None:
        self._statements: set[str] = set()

    def add(self, *statements: str) -> None:
        """
        Collect import statements.

        :param statements: lines like ``import datetime`` or
            ``from typing import Any``
        """
        self._statements.update(statements)

    def _key(self, statement: str) -> tuple[int, str, str]:
        """
        The isort sort key of one statement.

        :param statement: the import line
        :return: import-style rank, module, imported name
        """
        words = statement.split()
        if words[0] == "import":
            return (0, words[1], "")
        return (1, words[1], words[3])

    def render(self, lines: Lines) -> None:
        """
        Write the collected imports as isort-ordered blocks.

        :param lines: the module builder to write into
        """
        blocks: dict[int, list[str]] = {0: [], 1: [], 2: [], 3: []}
        for statement in self._statements:
            module = self._key(statement)[1]
            if module == "__future__":
                blocks[0].append(statement)
            elif module.split(".")[0] == "action0":
                blocks[2].append(statement)
            elif module.startswith("."):
                blocks[3].append(statement)
            else:
                blocks[1].append(statement)
        for block in sorted(blocks):
            if not blocks[block]:
                continue
            lines.separate(1)
            for statement in sorted(blocks[block], key=self._key):
                lines.write(statement)


def render_models(api: Api, header: str) -> str:
    """
    Render the ``models.py`` module: enums, dataclass models, and the
    JSON-to-model converter functions.

    :param api: the intermediate representation
    :param header: the generated-by header comment line (without ``#``)
    :return: the module's source text
    """
    lines = Lines()
    lines.write(f"# {header}")
    body = Lines()
    imports = Imports()
    imports.add("from __future__ import annotations")
    for model in api.models:
        body.separate()
        if isinstance(model, EnumModel):
            _render_enum(model, body, imports)
        elif isinstance(model, UnionModel):
            _render_union(model, body, imports)
        else:
            _render_model(model, body, imports)
            body.separate()
            _render_converter(model, body, imports)
    imports.render(lines)
    lines.separate()
    lines.extend(body)
    return lines.text()


def _render_enum(model: EnumModel, lines: Lines, imports: Imports) -> None:
    """
    Render one enum class.

    :param model: the enum
    :param lines: the module builder
    :param imports: the module's import collector
    """
    imports.add("import enum")
    for line in _class_lines(model.name, "enum.Enum", lines.level):
        lines.write(line)
    lines.indent()
    lines.docstring(model.description or f"The ``{model.name}`` values.")
    lines.write()
    for member, value in model.members:
        lines.write(f"{member} = {_literal(value)}")
    lines.dedent()


#: the isinstance() argument of each JSON-type dispatch bucket; float
#: accepts ints too — JSON numbers decode as either
_ISINSTANCE_ARGS = {
    "bool": "bool",
    "int": "int",
    "float": "(int, float)",
    "str": "str",
    "list": "list",
    "dict": "dict",
}


def _render_union(model: UnionModel, lines: Lines, imports: Imports) -> None:
    """
    Render one union: the type alias and, when any member needs
    converting, the dispatching converter function.

    :param model: the union
    :param lines: the module builder
    :param imports: the module's import collector
    """
    imports.add("from typing import TypeAlias")
    for member in model.members:
        imports.add(*imports_for(member))
    if model.description:
        lines.comment(model.description)
    alias = " | ".join(annotation(member) for member in model.members)
    # the string form keeps the alias independent of definition order
    for line in _annotated_lines(model.name, "TypeAlias", f'"{alias}"', lines.level):
        lines.write(line)
    if not needs_conversion(UnionType(model.name, model.members)):
        return
    imports.add("from typing import Any")
    lines.separate()
    for line in _def_lines(converter_name(model.name), ["data: Any"], model.name, lines.level):
        lines.write(line)
    lines.indent()
    lines.docstring(
        f"Build a :py:data:`{model.name}` member from one decoded JSON value.\n"
        "\n"
        ":param data: the decoded JSON value\n"
        ":return: the matching member\n"
        ":raises ValueError: if the value matches no member"
    )
    # object checks only need the isinstance guard when non-object
    # payloads can reach them
    guarded = any(case.check is UnionCheck.JSON_TYPE for case in model.cases)
    for case in model.cases:
        lines.write(f"if {_union_condition(case, model.discriminator, guarded)}:")
        lines.indent()
        for line in _return_lines(converter_expr(case.member, "data"), lines.level):
            lines.write(line)
        lines.dedent()
    lines.write(f'raise ValueError("the payload matches no member of {model.name}")')
    lines.dedent()


def _union_condition(case: UnionCase, discriminator: str | None, guarded: bool) -> str:
    """
    Render the condition recognizing one union member.

    :param case: the dispatch branch
    :param discriminator: the wire property carrying the tag
    :param guarded: whether object checks need an isinstance(dict) guard
    :return: the condition text
    """
    if case.check is UnionCheck.JSON_TYPE:
        return f"isinstance(data, {_ISINSTANCE_ARGS[case.value]})"
    if case.check is UnionCheck.TAG:
        condition = f"data.get({_literal(discriminator)}) == {_literal(case.value)}"
    else:
        condition = f"{_literal(case.value)} in data"
    return f"isinstance(data, dict) and {condition}" if guarded else condition


def _render_model(model: Model, lines: Lines, imports: Imports) -> None:
    """
    Render one dataclass model.

    :param model: the model
    :param lines: the module builder
    :param imports: the module's import collector
    """
    imports.add("from dataclasses import dataclass")
    lines.write("@dataclass")
    lines.write(f"class {model.name}:")
    lines.indent()
    lines.docstring(model.description or f"The ``{model.name}`` model.")
    lines.write()
    for field in model.fields:
        imports.add(*imports_for(field.type))
        optional = not field.required or field.nullable
        rendered = annotation(field.type, optional=optional)
        if field.description:
            lines.comment(field.description)
        default = "None" if optional else None
        for line in _annotated_lines(field.name, rendered, default, lines.level):
            lines.write(line)
    if model.additional_field is not None:
        # the catch-all always renders last: it is optional (defaulted),
        # so it may not precede a default-less field
        extra = model.additional_field
        imports.add(*imports_for(extra.type))
        if extra.description:
            lines.comment(extra.description)
        rendered = annotation(extra.type, optional=True)
        for line in _annotated_lines(extra.name, rendered, "None", lines.level):
            lines.write(line)
    lines.dedent()


def _render_converter(model: Model, lines: Lines, imports: Imports) -> None:
    """
    Render the JSON-to-model converter function of one model, preceded
    by the declared-properties set constant when the model has a
    catch-all ``additionalProperties`` field.

    :param model: the model
    :param lines: the module builder
    :param imports: the module's import collector
    """
    imports.add("from typing import Any")
    if model.additional_field is not None:
        lines.write(f"# payload keys outside this set land in {model.additional_field.name}")
        constant = properties_constant_name(model.name)
        wire_names = [field.wire_name for field in model.fields]
        for line in _constant_lines(constant, wire_names, lines.level):
            lines.write(line)
        lines.separate()
    for line in _def_lines(converter_name(model.name), ["data: Any"], model.name, lines.level):
        lines.write(line)
    lines.indent()
    lines.docstring(
        f"Build a :py:class:`{model.name}` from one decoded JSON object.\n"
        "\n"
        ":param data: the decoded JSON object\n"
        f":return: the {model.name}"
    )
    lines.write(f"return {model.name}(")
    lines.indent()
    for field in model.fields:
        imports.add(*imports_for(field.type))
        for line in _field_conversion(field, lines.level):
            lines.write(line)
    if model.additional_field is not None:
        imports.add(*imports_for(model.additional_field.type))
        for line in _additional_conversion(model, lines.level):
            lines.write(line)
    lines.dedent()
    lines.write(")")
    lines.dedent()


def _constant_lines(name: str, values: list[str], level: int) -> list[str]:
    """
    Lay out a module-level set constant within the line limit.

    Over the limit, the set display opens up with one element per line;
    the magic trailing comma also keeps ruff format from collapsing it
    back onto one line.

    :param name: the constant's name
    :param values: the (string) set elements, in order
    :param level: the indentation level the lines are written at
    :return: the laid-out lines, relative to ``level``
    """
    literals = [_literal(value) for value in values]
    line = f"{name} = {{{', '.join(literals)}}}"
    if _fits(line, level):
        return [line]
    return [f"{name} = {{", *(f"    {literal}," for literal in literals), "}"]


def _additional_conversion(model: Model, level: int) -> list[str]:
    """
    Render the catch-all keyword argument of a model's converter: a
    dict comprehension collecting every payload key outside the
    declared-properties constant.

    :param model: the model (with a catch-all field)
    :param level: the indentation level the lines are written at
    :return: the lines (one, or several when wrapped)
    """
    field = model.additional_field
    assert field is not None and isinstance(field.type, MapType)
    # depth 1: the comprehension itself claims the depth-0 variables
    element = converter_expr(field.type.value, "value", _depth=1)
    constant = properties_constant_name(model.name)
    expression = f"{{key: {element} for key, value in data.items() if key not in {constant}}}"
    return _wrapped(f"{field.name}=", expression, None, level)


def _field_conversion(field: Field, level: int) -> list[str]:
    """
    Render the ``name=<conversion>,`` keyword argument of one field.

    Required fields read ``data["..."]`` (a missing key is a bug worth
    the KeyError), optional fields read ``data.get("...")``; values that
    need converting are guarded against ``None`` where ``None`` is
    possible. A line over the column limit is wrapped the way ruff
    format wraps it.

    :param field: the field
    :param level: the indentation level the lines are written at
    :return: the lines (one, or several when wrapped)
    """
    source = f"data[{_literal(field.wire_name)}]"
    if not needs_conversion(field.type):
        if not field.required:
            source = f"data.get({_literal(field.wire_name)})"
        return _wrapped(f"{field.name}=", source, None, level)
    converted = converter_expr(field.type, source)
    if field.required and not field.nullable:
        return _wrapped(f"{field.name}=", converted, None, level)
    check = source if field.required else f"data.get({_literal(field.wire_name)})"
    return _wrapped(f"{field.name}=", converted, f"{check} is not None", level)


def _fits(text: str, level: int) -> bool:
    """
    Whether a line fits the column limit at an indentation level.

    :param text: the line's text, without the indentation
    :param level: the indentation level
    :return: whether the indented line stays within the limit
    """
    return level * 4 + len(text) <= _LINE_LIMIT


def _wrapped(prefix: str, expression: str, condition: str | None, level: int) -> list[str]:
    """
    Lay out ``prefix<expression> if <condition> else None,`` within the
    line limit.

    :param prefix: the ``name=`` part
    :param expression: the value expression
    :param condition: the ``... is not None`` guard, if any
    :param level: the indentation level the lines are written at
    :return: the laid-out lines, relative to ``level``
    """
    guarded = f"{expression} if {condition} else None" if condition else expression
    if _fits(f"{prefix}{guarded},", level):
        return [f"{prefix}{guarded},"]
    # over the limit: parenthesize like ruff format — the whole
    # expression on its own (deeper indented) line if it fits there,
    # a conditional split before "if" and "else" otherwise; an
    # expression still over the limit is split further where possible
    if _fits(guarded, level + 1) or not condition:
        expression_lines = _expression_lines(guarded, level + 1)
        return [f"{prefix}(", *(f"    {line}" for line in expression_lines), "),"]
    return [
        f"{prefix}(",
        *(f"    {line}" for line in _expression_lines(expression, level + 1)),
        f"    if {condition}",
        "    else None",
        "),",
    ]


def _expression_lines(expression: str, level: int) -> list[str]:
    """
    Lay out one expression within the line limit.

    An over-long comprehension is opened up the way ruff format opens
    it: bracket, element (split recursively), ``for`` clause, closing
    bracket. Anything else over the limit is left on one line — the
    conversion grammar (see :py:func:`action0.openapi.types.converter_expr`)
    has nothing shorter to offer then.

    :param expression: the expression text
    :param level: the indentation level the lines are written at
    :return: the laid-out lines, relative to ``level``
    """
    if _fits(expression, level):
        return [expression]
    split = _split_comprehension(expression)
    if split is None:
        return [expression]
    opener, element, clause = split
    return [
        opener,
        *(f"    {line}" for line in _expression_lines(element, level + 1)),
        *(f"    {part}" for part in _clause_parts(clause, level + 1)),
        "]" if opener == "[" else "}",
    ]


def _split_comprehension(expression: str) -> tuple[str, str, str] | None:
    """
    Split a comprehension into bracket, element and ``for`` clause.

    :param expression: the expression text
    :return: the opening bracket, the element expression and the
        ``for ...`` clause — or ``None`` when the expression is not a
        comprehension
    """
    if not expression or expression[0] not in "[{":
        return None
    # find the top-level " for " inside the outer bracket; skip over
    # nested brackets and (double-quoted, json.dumps-produced) strings
    depth = 0
    in_string = False
    index = 0
    while index < len(expression):
        char = expression[index]
        if in_string:
            if char == "\\":
                index += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 1 and expression.startswith(" for ", index):
            return expression[0], expression[1:index], expression[index + 1 : -1]
        index += 1
    return None


def _clause_parts(clause: str, level: int) -> list[str]:
    """
    Split a comprehension clause at its top-level ``if`` filters.

    When ruff format opens a comprehension up, every ``for`` and ``if``
    clause gets a line of its own; an over-long ``if x not in y`` filter
    breaks once more, before the ``not in``.

    :param clause: the clause text (``for ...``, possibly with filters)
    :param level: the indentation level the parts are written at
    :return: the clause parts, one per line
    """
    parts = []
    depth = 0
    in_string = False
    start = 0
    index = 0
    while index < len(clause):
        char = clause[index]
        if in_string:
            if char == "\\":
                index += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and clause.startswith(" if ", index):
            parts.append(clause[start:index])
            start = index + 1
        index += 1
    parts.append(clause[start:])
    split = []
    for part in parts:
        if part.startswith("if ") and not _fits(part, level) and " not in " in part:
            head, _, tail = part.partition(" not in ")
            split.extend([head, f"not in {tail}"])
        else:
            split.append(part)
    return split


def _return_lines(expression: str, level: int) -> list[str]:
    """
    Lay out a ``return <expression>`` statement within the line limit.

    :param expression: the returned expression
    :param level: the indentation level the lines are written at
    :return: the laid-out lines, relative to ``level``
    """
    statement = f"return {expression}"
    if _fits(statement, level):
        return [statement]
    split = _split_comprehension(expression)
    if split is None:
        return [statement]
    opener, element, clause = split
    return [
        f"return {opener}",
        *(f"    {line}" for line in _expression_lines(element, level + 1)),
        *(f"    {part}" for part in _clause_parts(clause, level + 1)),
        "]" if opener == "[" else "}",
    ]


def _class_lines(name: str, base: str, level: int) -> list[str]:
    """
    Lay out a ``class`` header within the line limit.

    Over the limit, the base class moves onto its own line, the way
    ruff format wraps it.

    :param name: the class name
    :param base: the base class text
    :param level: the indentation level the lines are written at
    :return: the laid-out lines, relative to ``level``
    """
    header = f"class {name}({base}):"
    if _fits(header, level):
        return [header]
    return [f"class {name}(", f"    {base}", "):"]


def _def_lines(name: str, parameters: list[str], returns: str, level: int) -> list[str]:
    """
    Lay out a ``def`` header within the line limit.

    An over-long header is split the way ruff format splits it: several
    parameters go on one shared line when they fit, otherwise (and for
    a single parameter, always) each gets its own line with a trailing
    comma.

    :param name: the function name
    :param parameters: the parameter declarations
    :param returns: the return annotation
    :param level: the indentation level the lines are written at
    :return: the laid-out lines, relative to ``level``
    """
    joined = ", ".join(parameters)
    header = f"def {name}({joined}) -> {returns}:"
    if _fits(header, level):
        return [header]
    if len(parameters) > 1 and _fits(joined, level + 1):
        body = [f"    {joined}"]
    else:
        body = [f"    {parameter}," for parameter in parameters]
    return [f"def {name}(", *body, f") -> {returns}:"]


def _annotated_lines(name: str, annotation: str, value: str | None, level: int) -> list[str]:
    """
    Lay out an annotated assignment (no call on the right-hand side)
    within the line limit.

    Over the limit, ruff format first parenthesizes the value, then the
    annotation; a plain annotation without value has nothing to split
    and stays over-long.

    :param name: the target name
    :param annotation: the annotation text
    :param value: the assigned value expression, if any
    :param level: the indentation level the lines are written at
    :return: the laid-out lines, relative to ``level``
    """
    line = f"{name}: {annotation}" + (f" = {value}" if value is not None else "")
    if value is None or _fits(line, level):
        return [line]
    if _fits(f"{name}: {annotation} = (", level) and _fits(value, level + 1):
        return [f"{name}: {annotation} = (", f"    {value}", ")"]
    if _fits(annotation, level + 1) and _fits(f") = {value}", level):
        return [f"{name}: (", f"    {annotation}", f") = {value}"]
    return [line]


def _literal(value: object) -> str:
    """
    Render a value as the Python literal generated code spells.

    Strings use double quotes (as ruff format enforces), booleans and
    numbers their canonical form.

    :param value: the value (str, bool, int or float)
    :return: the literal text
    """
    if isinstance(value, str):
        return json.dumps(value)
    return repr(value)


#: the field specifier each parameter location uses
_SPECIFIERS = {
    ParamLocation.PATH: "path_param",
    ParamLocation.QUERY: "query",
    ParamLocation.HEADER: "header",
}


def render_operations(
    api: Api, header: str, operations: "Sequence[OperationIR] | None" = None
) -> str:
    """
    Render one operations module: one operation class per endpoint.

    :param api: the intermediate representation
    :param header: the generated-by header comment line (without ``#``)
    :param operations: the operations to render into this module (all of
        the API's when ``None`` — per-tag splitting passes subsets)
    :return: the module's source text
    """
    lines = Lines()
    lines.write(f"# {header}")
    body = Lines()
    imports = Imports()
    imports.add("from __future__ import annotations")
    enum_members = _enum_member_lookup(api)
    for operation in api.operations if operations is None else operations:
        imports.add("from action0.req import Method")
        body.separate()
        _render_operation(operation, body, imports, enum_members)
    imports.render(lines)
    lines.separate()
    lines.extend(body)
    return lines.text()


def _enum_member_lookup(api: Api) -> dict[str, dict[object, str]]:
    """
    Map every enum class to its value-to-member lookup.

    Schema defaults arrive as raw wire values; rendering them as field
    defaults needs the member names.

    :param api: the intermediate representation
    :return: enum class name to (value to member name)
    """
    return {
        model.name: {value: member for member, value in model.members}
        for model in api.models
        if isinstance(model, EnumModel)
    }


def _render_operation(
    operation: OperationIR,
    lines: Lines,
    imports: Imports,
    enum_members: dict[str, dict[object, str]],
) -> None:
    """
    Render one operation class.

    :param operation: the operation
    :param lines: the module builder
    :param imports: the module's import collector
    :param enum_members: the enum default lookup
    """
    base, result = _operation_base(operation, imports)
    for line in _class_lines(operation.class_name, base, lines.level):
        lines.write(line)
    lines.indent()
    title = f"``{operation.method} {operation.wire_path}``"
    if operation.summary:
        title += f" — {operation.summary}"
    lines.docstring(title + (f"\n\n{operation.description}" if operation.description else ""))
    lines.write()
    lines.write(f"method = Method.{operation.method}")
    lines.write(f"path = {_literal(operation.path_template)}")
    if operation.path_template != operation.wire_path:
        # keep the original spelling visible next to the rewritten one
        lines.write(f"# the schema spells the path {_literal(operation.wire_path)}")
    lines.write()
    for param in operation.params:
        specifier = _SPECIFIERS[param.location]
        rename = param.wire_name if param.location is not ParamLocation.PATH else None
        if param.description:
            lines.comment(param.description)
        for line in _field_lines(param, specifier, rename, enum_members, imports, lines.level):
            lines.write(line)
    if operation.body is not None:
        _render_body_fields(operation.body, lines, imports, enum_members)
    _render_load(operation, result, lines, imports)
    lines.dedent()


def _operation_base(operation: OperationIR, imports: Imports) -> tuple[str, str]:
    """
    The base class and result annotation of one operation.

    :param operation: the operation
    :param imports: the module's import collector
    :return: the base class text and the result type text
    """
    if operation.response_kind is ResponseKind.MODEL:
        assert operation.response_type is not None
        result = annotation(operation.response_type)
        imports.add("from action0.client import JsonOperation")
        imports.add(*imports_for(operation.response_type))
        _import_classes(operation.response_type, imports)
        return f"JsonOperation[{result}]", result
    imports.add("from action0.client import Operation", "from action0.req import Response")
    result = "None" if operation.response_kind is ResponseKind.NONE else "bytes"
    return f"Operation[{result}]", result


def _render_body_fields(
    body: Body,
    lines: Lines,
    imports: Imports,
    enum_members: dict[str, dict[object, str]],
) -> None:
    """
    Render the field lines of an operation's request body.

    :param body: the body
    :param lines: the module builder
    :param imports: the module's import collector
    :param enum_members: the enum default lookup
    """
    if body.kind is BodyKind.JSON_BODY:
        (field,) = body.fields
        if field.description:
            lines.comment(field.description)
        for line in _field_lines(field, "json_body", None, enum_members, imports, lines.level):
            lines.write(line)
        return
    if body.kind is BodyKind.RAW_BODY:
        (field,) = body.fields
        if field.description:
            lines.comment(field.description)
        for line in _field_lines(field, "body", None, enum_members, imports, lines.level):
            lines.write(line)
        return
    specifier = "json_field" if body.kind is BodyKind.JSON_FIELDS else "form_field"
    for field in body.fields:
        rename = field.wire_name if field.wire_name != field.name else None
        if field.description:
            lines.comment(field.description)
        for line in _field_lines(field, specifier, rename, enum_members, imports, lines.level):
            lines.write(line)


def _field_lines(
    field: Field | Param,
    specifier: str,
    rename: str | None,
    enum_members: dict[str, dict[object, str]],
    imports: Imports,
    level: int,
) -> list[str]:
    """
    Render one operation field with its specifier.

    An over-long field is laid out the way ruff format lays it out: the
    specifier call's arguments move one line deeper when the head still
    fits, otherwise the whole call is parenthesized, otherwise the
    annotation is — and when nothing fits, the arguments split anyway,
    over-long (ruff format leaves such lines alone too).

    :param field: the field or parameter
    :param specifier: the specifier function name
    :param rename: the wire name to pass positionally, if it differs
    :param enum_members: the enum default lookup
    :param imports: the module's import collector
    :param level: the indentation level the lines are written at
    :return: the field's source lines, relative to ``level``
    """
    imports.add(f"from action0.client import {specifier}")
    imports.add(*imports_for(field.type))
    _import_classes(field.type, imports)
    arguments = []
    if rename is not None and rename != field.name:
        arguments.append(_literal(rename))
    default = None
    if not field.required:
        default = _default_literal(field, enum_members)
        arguments.append(f"default={default}")
    serialize = _serialize_argument(field.type)
    if serialize is not None:
        arguments.append(serialize)
    optional = field.nullable or (not field.required and default == "None")
    rendered = annotation(field.type, optional=optional)
    joined = ", ".join(arguments)
    call = f"{specifier}({joined})"
    line = f"{field.name}: {rendered} = {call}"
    if _fits(line, level) or not arguments:
        return [line]
    head = f"{field.name}: {rendered} = {specifier}("
    if not _fits(head, level):
        if _fits(f"{field.name}: {rendered} = (", level) and _fits(call, level + 1):
            return [f"{field.name}: {rendered} = (", f"    {call}", ")"]
        if _fits(rendered, level + 1) and _fits(f") = {call}", level):
            return [f"{field.name}: (", f"    {rendered}", f") = {call}"]
    if len(arguments) == 1 or _fits(joined, level + 1):
        return [head, f"    {joined}", ")"]
    return [head, *(f"    {argument}," for argument in arguments), ")"]


def _default_literal(field: Field | Param, enum_members: dict[str, dict[object, str]]) -> str:
    """
    Render an optional field's default value.

    :param field: the field or parameter
    :param enum_members: the enum default lookup
    :return: the default's literal text (``None`` when the schema
        declares none)
    """
    if field.default is None:
        return "None"
    if isinstance(field.type, EnumType):
        member = enum_members[field.type.name].get(field.default)
        if member is None:
            return "None"
        return f"{field.type.name}.{member}"
    return _literal(field.default)


def _serialize_argument(t: TypeExpr) -> str | None:
    """
    The ``serialize=`` argument a field needs, if any.

    action0-client serializes enums, dates and dataclasses on its own;
    UUIDs are the one generated type its serializers reject, so UUID
    fields get an explicit ``str`` conversion.

    :param t: the field's type
    :return: the argument text, or ``None``
    """
    if t == ScalarType(Scalar.UUID):
        return "serialize=str"
    if isinstance(t, ArrayType) and t.item == ScalarType(Scalar.UUID):
        return "serialize=lambda values: [str(value) for value in values]"
    return None


def _render_load(operation: OperationIR, result: str, lines: Lines, imports: Imports) -> None:
    """
    Render the response-loading method of one operation.

    :param operation: the operation
    :param result: the result type text
    :param lines: the module builder
    :param imports: the module's import collector
    """
    # separate() rather than write(): a field-less operation already
    # has the blank line after its method/path block
    lines.separate(1)
    if operation.response_kind is ResponseKind.MODEL:
        assert operation.response_type is not None
        imports.add("from typing import Any")
        _import_converters(operation.response_type, imports)
        for line in _def_lines("load_json", ["self", "data: Any"], result, lines.level):
            lines.write(line)
        lines.indent()
        lines.docstring(":param data: the decoded JSON payload\n:return: the parsed result")
        expression = converter_expr(operation.response_type, "data")
        if expression == "data" and result != "Any":
            # nothing to convert, but the decoded payload is typed Any —
            # returning it as-is would fail mypy strict (no-any-return)
            imports.add("from typing import cast")
            expression = f"cast({result}, data)"
        for line in _return_lines(expression, lines.level):
            lines.write(line)
        lines.dedent()
        return
    for line in _def_lines("load", ["self", "response: Response"], result, lines.level):
        lines.write(line)
    lines.indent()
    lines.docstring(":param response: the checked response\n:return: the parsed result")
    if operation.response_kind is ResponseKind.NONE:
        lines.write("return None")
    else:
        lines.write('return response.body_bytes() or b""')
    lines.dedent()


def _import_classes(t: TypeExpr, imports: Imports) -> None:
    """
    Collect the ``.models`` imports a type annotation needs.

    :param t: the type
    :param imports: the module's import collector
    """
    match t:
        case ModelType(name=name) | EnumType(name=name) | UnionType(name=name):
            imports.add(f"from .models import {name}")
        case ArrayType(item=inner) | MapType(value=inner):
            _import_classes(inner, imports)
        case ScalarType():
            pass


def _import_converters(t: TypeExpr, imports: Imports) -> None:
    """
    Collect the ``.models`` converter imports a load expression needs.

    :param t: the type
    :param imports: the module's import collector
    """
    match t:
        case ModelType(name=name):
            imports.add(f"from .models import {converter_name(name)}")
        case UnionType(name=name) as union:
            if needs_conversion(union):
                imports.add(f"from .models import {converter_name(name)}")
        case ArrayType(item=inner) | MapType(value=inner):
            _import_converters(inner, imports)
        case ScalarType() | EnumType():
            pass


def render_client(api: Api, header: str, client_name: str) -> str:
    """
    Render the ``client.py`` module: the API client subclass with the
    base URL and the security schemes baked in.

    :param api: the intermediate representation
    :param header: the generated-by header comment line (without ``#``)
    :param client_name: the client class name
    :return: the module's source text
    """
    lines = Lines()
    lines.write(f"# {header}")
    body = Lines()
    imports = Imports()
    imports.add(
        "from __future__ import annotations",
        "from action0.client import APIClient",
        "from action0.client import BackendT_co",
    )
    query_schemes = [s for s in api.security if s.kind is SecurityKind.API_KEY_QUERY]
    if any(s.kind is SecurityKind.HTTP_BASIC for s in api.security):
        imports.add("import base64")
    if query_schemes:
        imports.add("from action0.req import Request")
    for line in _class_lines(client_name, "APIClient[BackendT_co]", body.level):
        body.write(line)
    body.indent()
    body.docstring(f"The {api.title} API client.")
    body.write()
    _render_client_init(api, body)
    if query_schemes:
        body.write()
        _render_client_prepare(query_schemes, body)
    body.dedent()
    imports.render(lines)
    lines.separate()
    lines.extend(body)
    return lines.text()


def _credential_parameters(api: Api) -> list[tuple[str, str]]:
    """
    The credential ``__init__`` parameters, with their docstring text.

    :param api: the intermediate representation
    :return: ``(parameter name, description)`` pairs
    """
    parameters = []
    for scheme in api.security:
        if scheme.kind is SecurityKind.HTTP_BEARER:
            parameters.append((scheme.param_name, "the bearer token"))
        elif scheme.kind is SecurityKind.HTTP_BASIC:
            parameters.append(("username", "the basic-auth user name"))
            parameters.append(("password", "the basic-auth password"))
        elif scheme.kind is SecurityKind.API_KEY_HEADER:
            parameters.append(
                (scheme.param_name, f"the API key sent as the {scheme.wire_name} header")
            )
        else:
            parameters.append(
                (
                    scheme.param_name,
                    f"the API key sent as the {scheme.wire_name} query parameter",
                )
            )
    return parameters


def _render_client_init(api: Api, lines: Lines) -> None:
    """
    Render the client's ``__init__``.

    :param api: the intermediate representation
    :param lines: the module builder
    """
    credentials = _credential_parameters(api)
    base_url = f"base_url: str = {_literal(api.base_url)}" if api.base_url else "base_url: str"
    lines.write("def __init__(")
    lines.indent()
    lines.write("self,")
    lines.write("backend: BackendT_co,")
    for name, _ in credentials:
        lines.write(f"{name}: str,")
    lines.write(f"{base_url},")
    lines.dedent()
    lines.write(") -> None:")
    lines.indent()
    documentation = ":param backend: any sync, async or Twisted backend\n"
    for name, description in credentials:
        documentation += f":param {name}: {description}\n"
    documentation += ":param base_url: the API root"
    lines.docstring(documentation)
    headers = []
    for scheme in api.security:
        if scheme.kind is SecurityKind.HTTP_BEARER:
            headers.append(f'"Authorization": f"Bearer {{{scheme.param_name}}}"')
        elif scheme.kind is SecurityKind.HTTP_BASIC:
            lines.write(
                'credentials = base64.b64encode(f"{username}:{password}".encode()).decode()'
            )
            headers.append('"Authorization": f"Basic {credentials}"')
        elif scheme.kind is SecurityKind.API_KEY_HEADER:
            headers.append(f"{_literal(scheme.wire_name)}: {scheme.param_name}")
    if headers:
        call = f"super().__init__(backend, base_url, headers={{{', '.join(headers)}}})"
        if lines.level * 4 + len(call) <= _LINE_LIMIT:
            lines.write(call)
        else:
            lines.write("super().__init__(")
            lines.indent()
            lines.write("backend,")
            lines.write("base_url,")
            lines.write("headers={")
            lines.indent()
            for entry in headers:
                lines.write(f"{entry},")
            lines.dedent()
            lines.write("},")
            lines.dedent()
            lines.write(")")
    else:
        lines.write("super().__init__(backend, base_url)")
    for scheme in api.security:
        if scheme.kind is SecurityKind.API_KEY_QUERY:
            lines.write(f"self._{scheme.param_name} = {scheme.param_name}")
    lines.dedent()


def _render_client_prepare(query_schemes: list[SecurityScheme], lines: Lines) -> None:
    """
    Render the ``prepare`` override adding query credentials.

    :param query_schemes: the apiKey-in-query schemes
    :param lines: the module builder
    """
    lines.write("def prepare(self, request: Request) -> Request:")
    lines.indent()
    lines.docstring(
        "Add the query credentials to every request.\n"
        "\n"
        ":param request: the request built from an operation\n"
        ":return: the request to actually send"
    )
    lines.write("request = super().prepare(request)")
    for scheme in query_schemes:
        lines.write(
            f"request.url.query.add({_literal(scheme.wire_name)}, self._{scheme.param_name})"
        )
    lines.write("return request")
    lines.dedent()


def render_init(
    api: Api,
    header: str,
    client_name: str,
    operation_modules: "Mapping[str, str] | None" = None,
) -> str:
    """
    Render the generated package's ``__init__.py``: docstring and
    re-exports of the client, the models and the operations.

    :param api: the intermediate representation
    :param header: the generated-by header comment line (without ``#``)
    :param client_name: the client class name
    :param operation_modules: operation class name to the module it
        lives in (every class in ``operations`` when ``None`` — per-tag
        splitting passes the actual layout)
    :return: the module's source text
    """
    lines = Lines()
    lines.write(f"# {header}")
    lines.docstring(f"Typed API client for {api.title} {api.version}.")
    imports = Imports()
    imports.add(f"from .client import {client_name}")
    names = [client_name]
    for model in api.models:
        imports.add(f"from .models import {model.name}")
        names.append(model.name)
    for operation in api.operations:
        module = (operation_modules or {}).get(operation.class_name, "operations")
        imports.add(f"from .{module} import {operation.class_name}")
        names.append(operation.class_name)
    imports.render(lines)
    lines.separate(1)
    lines.write("__all__ = [")
    lines.indent()
    for name in sorted(names):
        lines.write(f"{_literal(name)},")
    lines.dedent()
    lines.write("]")
    return lines.text()
