"""Monetary type-safety checkers: ``shekel-money`` (W9901 + W9904).

W9901 (``shekel-decimal-from-float``) flags a ``Decimal`` built from a float,
which inherits binary float imprecision. W9904 (``shekel-bare-money-quantize``)
flags a cents-quantum ``.quantize()`` with no explicit rounding mode, which
silently uses banker's ``ROUND_HALF_EVEN`` instead of the project's
``ROUND_HALF_UP`` money boundary (``app/utils/money.py``).
"""

from astroid import nodes

from pylint.checkers import BaseChecker

from ._common import _is_string_const

# Constructor whose float argument loses precision.
_DECIMAL_CTOR = "Decimal"
# Builtin that produces a float; passing its result to Decimal is still a
# float-sourced Decimal even though the literal is hidden behind the call.
_FLOAT_BUILTIN = "float"

# The Decimal method that rounds to a fixed exponent.  A bare call (no rounding
# mode) falls back to the decimal context default, ROUND_HALF_EVEN.
_QUANTIZE_METHOD = "quantize"
# The keyword that selects the rounding mode explicitly.
_ROUNDING_KEYWORD = "rounding"
# Two-decimal "cents" quantum: the names the codebase uses for Decimal("0.01")
# (``CENTS`` from app/utils/money.py, plus the local ``TWO_PLACES`` /
# ``_TWO_PLACES`` redeclarations) and the literal itself.  A bare ``.quantize()``
# against one of these rounds MONEY; other quanta (one-decimal percentages, the
# five-decimal rate, the SWR slider) are not money and are not flagged.
_CENTS_QUANTUM_NAMES = frozenset({"CENTS", "TWO_PLACES", "_TWO_PLACES"})
_CENTS_QUANTUM_LITERAL = "0.01"


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


def _is_quantize_call(node: nodes.Call) -> bool:
    """Return True if ``node`` is an ``<expr>.quantize(...)`` method call.

    Matched syntactically on the method name (``Decimal.quantize`` is the only
    realistic ``.quantize`` in money code); name matching keeps the checker fast
    and avoids inference flakiness.
    """
    return (
        isinstance(node.func, nodes.Attribute)
        and node.func.attrname == _QUANTIZE_METHOD
    )


def _is_cents_quantum(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a cents quantum used for monetary rounding.

    Matches the literal ``Decimal("0.01")`` and the project's named cents
    constants (``CENTS`` / ``TWO_PLACES`` / ``_TWO_PLACES``, optionally qualified
    as ``money.CENTS``). A different quantum -- a one-decimal percentage
    ``Decimal("0.1")``, the five-decimal ``_RATE_PLACES``, the ``_PCT_QUANTUM``
    SWR slider -- is not money and is intentionally not matched, mirroring the
    audit's money-vs-percentage classification (06_dry_solid.md register (b)/(c)).
    """
    if isinstance(node, nodes.Name):
        return node.name in _CENTS_QUANTUM_NAMES
    if isinstance(node, nodes.Attribute):
        return node.attrname in _CENTS_QUANTUM_NAMES
    if _is_decimal_call(node) and node.args:
        first = node.args[0]
        return _is_string_const(first) and first.value == _CENTS_QUANTUM_LITERAL
    return False


def _has_explicit_rounding(node: nodes.Call) -> bool:
    """Return True if a ``.quantize()`` call selects its rounding mode explicitly.

    Either a second positional argument (the positional ``rounding`` parameter,
    ``x.quantize(CENTS, ROUND_HALF_UP)``) or the ``rounding=`` keyword
    (``x.quantize(CENTS, rounding=ROUND_HALF_UP)``) overrides the banker's
    default, so the call is not the bare-money antipattern.
    """
    if len(node.args) >= 2:
        return True
    return any(kw.arg == _ROUNDING_KEYWORD for kw in node.keywords or [])


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
        "W9904": (
            "Money value rounded with a bare .quantize(); round it through "
            "round_money() (ROUND_HALF_UP) instead",
            "shekel-bare-money-quantize",
            'A .quantize(Decimal("0.01")) call with no rounding= argument uses '
            "Python's decimal-context default ROUND_HALF_EVEN (banker's "
            "rounding), which disagrees with the project's ROUND_HALF_UP "
            "convention by a cent at every half-cent boundary. "
            "app/utils/money.py forbids reaching that mode implicitly: round "
            "monetary Decimals through round_money() (or the sanctioned "
            "round_money_ceiling()), the only auditable money boundary helpers "
            "(financial_calculations audit E-26 / HIGH-04). A quantize that "
            "passes an explicit rounding mode, or quantizes a non-cents quantum "
            "(a percentage or rate), is not flagged.",
        ),
    }

    def visit_call(self, node: nodes.Call) -> None:
        """Dispatch every call expression to the two monetary call checks.

        ``node`` is every call expression in the module; the two checks below
        match disjoint node shapes (a ``Decimal(...)`` constructor vs an
        ``<expr>.quantize(...)`` method call), so both run on every node.
        """
        self._check_decimal_from_float(node)
        self._check_bare_money_quantize(node)

    def _check_decimal_from_float(self, node: nodes.Call) -> None:
        """Flag ``Decimal(<float literal>)`` and ``Decimal(float(...))``.

        Only ``Decimal`` calls whose first argument is a float literal (optionally
        signed) or an explicit ``float()`` call are reported; string and integer
        arguments are exact and pass.
        """
        if not _is_decimal_call(node) or not node.args:
            return
        first = node.args[0]
        if _is_float_literal(first) or _is_float_builtin_call(first):
            self.add_message("shekel-decimal-from-float", node=node)

    def _check_bare_money_quantize(self, node: nodes.Call) -> None:
        """Flag ``<money>.quantize(<cents>)`` with no explicit rounding mode.

        Such a call rounds money with the banker's default; only cents quanta
        are matched (a percentage/rate quantum is not money), and a call that
        already passes a rounding mode is left alone.
        """
        if not _is_quantize_call(node) or not node.args:
            return
        if _is_cents_quantum(node.args[0]) and not _has_explicit_rounding(node):
            self.add_message("shekel-bare-money-quantize", node=node)
