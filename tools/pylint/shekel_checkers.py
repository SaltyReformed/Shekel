"""Project-specific pylint checkers for the Shekel budget app.

These AST checkers encode two financial-correctness rules from
``docs/coding-standards.md`` that generic pylint does not cover, so the rules
are enforced by a deterministic tool instead of relying on a reviewer (human or
LLM) to remember them. They are loaded through ``.pylintrc`` (``load-plugins``),
so every pylint invocation -- the per-edit hook, the Stop-hook full run, CI, and
pre-commit -- applies them automatically.

Rules implemented:

* ``shekel-decimal-from-float`` (W9901): flags ``Decimal`` constructed from a
  float, which inherits binary float imprecision. Monetary values must be built
  from strings.
* ``shekel-refname-compare`` (W9902): flags comparing a ``.name`` attribute
  against a string literal. Reference-table name columns are for display only;
  logic must key off IDs or enums.
* ``shekel-disable-rationale`` (W9903): flags any ``# pylint: disable=`` that
  lacks a standard ``Pylint:`` why-comment in the mandated location (the
  docstring for a def/class-scoped directive, a comment immediately above
  otherwise). Keeps every suppression auditable with one grep.

Deliberately NOT implemented as a checker: a blanket ``float()`` ban. The
codebase's real ``float()`` call sites are all legitimate (config timeouts that
are genuinely floats, and documented Decimal-to-float boundaries for Chart.js
JSON serialization). A static rule cannot distinguish a precision-losing
calculation from an end-of-pipeline serialization boundary without false
positives, so that judgment lives in the code-reviewer subagent instead.
"""

import io
import re
import tokenize

from astroid import nodes

from pylint.checkers import BaseChecker, BaseRawFileChecker

# Comparison operators where a name-vs-string-literal comparison is the
# reference-table antipattern. ``<`` / ``>`` on a name column are not the
# documented smell and are left alone.
_EQUALITY_OPS = frozenset({"==", "!="})
_MEMBERSHIP_OPS = frozenset({"in", "not in"})
# Attribute name of the reference-table display column. IDs are the logic key;
# this column is display-only (CLAUDE.md: "IDs for logic, strings for display").
_DISPLAY_COLUMN = "name"
# Constructor whose float argument loses precision.
_DECIMAL_CTOR = "Decimal"
# Builtin that produces a float; passing its result to Decimal is still a
# float-sourced Decimal even though the literal is hidden behind the call.
_FLOAT_BUILTIN = "float"

# Marker that prefixes every disable rationale.  Greppable via
# ``grep -rn "Pylint:" app/``; capitalized so it can never collide with pylint's
# own lowercase ``# pylint:`` pragma parser (which is case-sensitive).
_RATIONALE_MARKER = "Pylint:"
# Matches an inline ``# pylint: disable=<rules>`` directive inside a comment token
# and captures the comma-separated rule list.  ``enable=`` and ``disable-next=``
# are intentionally not matched: the codebase uses plain ``disable=`` only.
_DISABLE_RE = re.compile(r"#\s*pylint:\s*disable=([\w,\-]+)")


def _is_decimal_call(node: nodes.Call) -> bool:
    """Return True if ``node`` calls ``Decimal`` (bare or ``decimal.Decimal``).

    Matched syntactically by name rather than by inference: ``Decimal`` is a
    distinctive identifier and name matching keeps the checker fast and
    false-positive-free. ``node`` is the call expression under inspection.
    """
    func = node.func
    if isinstance(func, nodes.Name):
        return func.name == _DECIMAL_CTOR
    if isinstance(func, nodes.Attribute):
        return func.attrname == _DECIMAL_CTOR
    return False


def _is_float_builtin_call(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a call to the builtin ``float`` constructor.

    Used to catch ``Decimal(float(x))``, where the float imprecision is laundered
    through an explicit ``float()`` call rather than written as a literal.
    """
    return (
        isinstance(node, nodes.Call)
        and isinstance(node.func, nodes.Name)
        and node.func.name == _FLOAT_BUILTIN
    )


def _is_float_literal(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a float constant, including ``-0.1`` forms.

    A unary plus/minus wrapping a float constant (``-0.1``) parses as a
    ``UnaryOp`` over a ``Const``; unwrap one level so the sign does not hide the
    float. Integer constants are intentionally NOT matched: ``Decimal(5)`` is
    exact, and flagging it would be noise (the imprecision is unique to floats).
    """
    target = node.operand if isinstance(node, nodes.UnaryOp) else node
    return isinstance(target, nodes.Const) and isinstance(target.value, float)


def _is_string_const(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a string literal constant."""
    return isinstance(node, nodes.Const) and isinstance(node.value, str)


def _is_display_name_attr(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` reads a ``.name`` attribute (e.g. ``status.name``).

    Matches the outermost attribute, so ``txn.status.name`` qualifies. Comparing
    such an attribute against a string literal is the reference-table antipattern;
    comparing it against a variable or column (``AccountType.name == data["name"]``)
    is legitimate and is not matched because the other operand is not a literal.
    """
    return isinstance(node, nodes.Attribute) and node.attrname == _DISPLAY_COLUMN


class ShekelMoneyChecker(BaseChecker):
    """Enforce monetary type-safety rules that generic pylint does not cover."""

    name = "shekel-money"
    msgs = {
        "W9901": (
            "Decimal constructed from a float; construct it from a string instead",
            "shekel-decimal-from-float",
            "Decimal(0.1) inherits binary float imprecision because 0.1 is not "
            "representable in binary floating point; the resulting Decimal is "
            "0.1000000000000000055511151231257827021181583404541015625. Build "
            'monetary Decimals from strings -- Decimal("0.1") -- per the Type '
            "Safety section of docs/coding-standards.md. Integer arguments are "
            "exact and are not flagged.",
        ),
    }

    def visit_call(self, node: nodes.Call) -> None:
        """Flag ``Decimal(<float literal>)`` and ``Decimal(float(...))``.

        ``node`` is every call expression in the module. Only ``Decimal`` calls
        whose first argument is a float literal (optionally signed) or an explicit
        ``float()`` call are reported; string and integer arguments are exact and
        pass.
        """
        if not _is_decimal_call(node) or not node.args:
            return
        first = node.args[0]
        if _is_float_literal(first) or _is_float_builtin_call(first):
            self.add_message("shekel-decimal-from-float", node=node)


class ShekelRefNameChecker(BaseChecker):
    """Forbid logic that compares reference-table display names to string literals."""

    name = "shekel-refname"
    msgs = {
        "W9902": (
            "Comparison of a .name attribute against a string literal; "
            "key logic off IDs or enums, not display names",
            "shekel-refname-compare",
            "Reference-table 'name' columns are for display only (CLAUDE.md: "
            '"IDs for logic, strings for display only. NEVER compare against '
            'string name columns"). A literal comparison such as '
            "status.name == \"Projected\" silently breaks if the display label is "
            "renamed and bypasses the cached-enum model in app/enums.py / "
            "app/ref_cache.py. Compare the *_id column or the enum constant. "
            "Comparing .name against a variable or column is not flagged.",
        ),
    }

    def visit_compare(self, node: nodes.Compare) -> None:
        """Flag ``<expr>.name`` compared to a string literal via ==, !=, in, not in.

        ``node`` is every comparison expression. astroid stores a comparison as a
        left operand plus a list of ``(operator, operand)`` pairs (chained
        comparisons such as ``a == b == c`` produce several pairs); each adjacent
        operand pair is inspected. Only string-literal operands trigger a report,
        so ``AccountType.name == data["name"]`` (a non-literal right side) passes.
        """
        operands = [node.left] + [operand for _, operand in node.ops]
        operators = [operator for operator, _ in node.ops]
        for index, operator in enumerate(operators):
            left = operands[index]
            right = operands[index + 1]
            if operator in _EQUALITY_OPS and self._is_name_literal_equality(left, right):
                self.add_message("shekel-refname-compare", node=node)
                return
            if operator in _MEMBERSHIP_OPS and self._is_name_literal_membership(left, right):
                self.add_message("shekel-refname-compare", node=node)
                return

    @staticmethod
    def _is_name_literal_equality(left: nodes.NodeNG, right: nodes.NodeNG) -> bool:
        """Return True if one side reads ``.name`` and the other is a string literal.

        Order-independent so both ``status.name == "X"`` and ``"X" == status.name``
        are caught.
        """
        return (_is_display_name_attr(left) and _is_string_const(right)) or (
            _is_display_name_attr(right) and _is_string_const(left)
        )

    @staticmethod
    def _is_name_literal_membership(left: nodes.NodeNG, right: nodes.NodeNG) -> bool:
        """Return True for ``<expr>.name in (<string literals>)`` and ``not in``.

        The membership form lists allowed display labels (``status.name in
        ("done", "credit")``); flagged when the left side reads ``.name`` and the
        right side is a literal collection containing at least one string.
        """
        if not _is_display_name_attr(left):
            return False
        if isinstance(right, (nodes.Tuple, nodes.List, nodes.Set)):
            return any(_is_string_const(element) for element in right.elts)
        return _is_string_const(right)


class ShekelDisableRationaleChecker(BaseRawFileChecker):
    """Require every ``# pylint: disable=`` to carry a standard why-comment.

    ``docs/coding-standards.md`` mandates a rationale in a fixed location and
    format so every suppression is auditable with one grep (``Pylint:``):

    * Definition-scoped (the directive sits on a ``def``/``class`` line -- the
      ``too-many-*`` and ``too-many-instance-attributes`` smells): the rationale
      is a ``Pylint:`` note in that symbol's docstring.
    * Statement-scoped (any other line -- ``broad-except``, ``protected-access``,
      ``import-outside-toplevel``): the rationale is a ``# Pylint:`` comment
      immediately above the disabled line.

    Either way the rationale must name every rule the directive disables.  This
    checker enforces marker presence, location, and rule-naming; the
    ``(<count>/<limit>)`` shape is a documented convention, not machine-checked.
    """

    name = "shekel-disable-rationale"
    msgs = {
        "W9903": (
            "pylint disable has no standard rationale: add a ``Pylint:`` note "
            "naming %s %s",
            "shekel-disable-rationale",
            "Every ``# pylint: disable=`` must carry a why-comment in the "
            "standard location and format (docs/coding-standards.md): a "
            "``Pylint:`` note in the docstring when the directive is on a "
            "def/class line, or a ``# Pylint:`` comment immediately above the "
            "line otherwise, naming each disabled rule. One grep for ``Pylint:`` "
            "must then surface a justification for every suppression.",
        ),
    }

    def process_module(self, node: nodes.Module) -> None:
        """Flag every disable directive in the module lacking a standard rationale.

        ``node`` is the astroid module. The source is tokenized to locate comment
        tokens -- so a ``# pylint: disable=`` written inside a string literal is
        never matched -- and the AST is walked to map each def/class line to its
        docstring.
        """
        try:
            with node.stream() as stream:
                content = stream.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return

        comment_only: dict[int, str] = {}
        disables: list[tuple[int, list[str]]] = []
        try:
            for tok_type, tok_str, start, _end, line in tokenize.generate_tokens(
                io.StringIO(content).readline,
            ):
                if tok_type != tokenize.COMMENT:
                    continue
                lineno, col = start
                if line[:col].strip() == "":
                    comment_only[lineno] = tok_str
                match = _DISABLE_RE.search(tok_str)
                if match:
                    disables.append(
                        (lineno, [r for r in match.group(1).split(",") if r]),
                    )
        except tokenize.TokenError:
            return

        if not disables:
            return

        def_lines = self._definition_docstrings(node)
        for lineno, rules in disables:
            if lineno in def_lines:
                text = def_lines[lineno] or ""
                hint = "in the docstring"
            else:
                text = self._comment_block_above(comment_only, lineno)
                hint = "in a comment immediately above"
            if self._rationale_ok(text, rules):
                continue
            self.add_message(
                "shekel-disable-rationale",
                line=lineno,
                args=(", ".join(rules), hint),
            )

    @staticmethod
    def _definition_docstrings(node: nodes.Module) -> dict[int, str | None]:
        """Map each def/class signature line to its docstring (or ``None``).

        Keyed by ``fromlineno`` -- the ``def``/``class`` line (decorators
        excluded), which is where a definition-scoped disable directive sits.
        """
        defs = node.nodes_of_class(
            (nodes.FunctionDef, nodes.AsyncFunctionDef, nodes.ClassDef),
        )
        return {
            definition.fromlineno: (
                definition.doc_node.value if definition.doc_node is not None else None
            )
            for definition in defs
        }

    @staticmethod
    def _comment_block_above(comment_only: dict[int, str], lineno: int) -> str:
        """Join the contiguous comment-only lines immediately above ``lineno``.

        Stops at the first non-comment or blank line, so only an adjacent
        rationale block counts -- a comment separated from the directive by code
        or a blank line does not.
        """
        collected: list[str] = []
        cursor = lineno - 1
        while cursor in comment_only:
            collected.append(comment_only[cursor])
            cursor -= 1
        return "\n".join(collected)

    @staticmethod
    def _rationale_ok(text: str, rules: list[str]) -> bool:
        """Return True if ``text`` carries the marker and names every rule."""
        if _RATIONALE_MARKER not in text:
            return False
        return all(rule in text for rule in rules)


def register(linter) -> None:
    """Register the Shekel checkers with the pylint ``linter`` (plugin entry point).

    Called by pylint when this module is named in ``.pylintrc``'s
    ``load-plugins``. ``linter`` is the active PyLinter instance.
    """
    linter.register_checker(ShekelMoneyChecker(linter))
    linter.register_checker(ShekelRefNameChecker(linter))
    linter.register_checker(ShekelDisableRationaleChecker(linter))
