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

from .ir import Api
from .ir import EnumModel
from .ir import Field
from .ir import Model
from .names import converter_name
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
    lines.write(f"class {model.name}(enum.Enum):")
    lines.indent()
    lines.docstring(model.description or f"The ``{model.name}`` values.")
    lines.write()
    for member, value in model.members:
        lines.write(f"{member} = {_literal(value)}")
    lines.dedent()


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
        default = " = None" if optional else ""
        lines.write(f"{field.name}: {rendered}{default}")
    lines.dedent()


def _render_converter(model: Model, lines: Lines, imports: Imports) -> None:
    """
    Render the JSON-to-model converter function of one model.

    :param model: the model
    :param lines: the module builder
    :param imports: the module's import collector
    """
    imports.add("from typing import Any")
    lines.write(f"def {converter_name(model.name)}(data: Any) -> {model.name}:")
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
    lines.dedent()
    lines.write(")")
    lines.dedent()


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
    if level * 4 + len(prefix) + len(guarded) + 1 <= _LINE_LIMIT:
        return [f"{prefix}{guarded},"]
    # over the limit: parenthesize like ruff format — the whole
    # expression on its own (deeper indented) line if it fits there,
    # a conditional split before "if" and "else" otherwise
    if (level + 1) * 4 + len(guarded) <= _LINE_LIMIT or not condition:
        return [f"{prefix}(", f"    {guarded}", "),"]
    return [
        f"{prefix}(",
        f"    {expression}",
        f"    if {condition}",
        "    else None",
        "),",
    ]


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
