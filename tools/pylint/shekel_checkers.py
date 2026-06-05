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

Deliberately NOT implemented as a checker: a blanket ``float()`` ban. The
codebase's real ``float()`` call sites are all legitimate (config timeouts that
are genuinely floats, and documented Decimal-to-float boundaries for Chart.js
JSON serialization). A static rule cannot distinguish a precision-losing
calculation from an end-of-pipeline serialization boundary without false
positives, so that judgment lives in the code-reviewer subagent instead.
"""

from astroid import nodes

from pylint.checkers import BaseChecker

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


def register(linter) -> None:
    """Register the Shekel checkers with the pylint ``linter`` (plugin entry point).

    Called by pylint when this module is named in ``.pylintrc``'s
    ``load-plugins``. ``linter`` is the active PyLinter instance.
    """
    linter.register_checker(ShekelMoneyChecker(linter))
    linter.register_checker(ShekelRefNameChecker(linter))
