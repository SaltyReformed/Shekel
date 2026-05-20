"""Centralized monetary rounding boundary and shared financial constants
(E-24, E-26; HIGH-04, HIGH-05).

Full-precision Decimal arithmetic everywhere in the codebase; rounding
happens once, here, at the display or persistence boundary. ``ROUND_HALF_UP``
is the only default -- it is the convention every hand-computed financial
test in this project assumes, and the convention every financial display
in the app implies. Python's Decimal default of ``ROUND_HALF_EVEN``
(banker's rounding) is a silent source of one-cent drift at half-cent
boundaries and must never be reached implicitly through a bare
``.quantize(Decimal("0.01"))``.

This module exposes two rounding helpers and the project's pay-period /
month conversion factors. ``round_money`` is the default boundary
rounding. ``round_money_ceiling`` is the explicitly-named sanctioned
variant for the savings-goal monthly contribution case, where under-
funding by a fraction of a cent must never round down -- naming the
exception at the call site makes the deviation auditable.

Both helpers reject ``float`` input with ``TypeError``. Construction of a
Decimal from a float (``Decimal(0.1)``) re-introduces the float
imprecision the helper exists to eliminate, so the helper refuses the
input at the boundary rather than silently rounding an already-imprecise
value. Callers must construct Decimal from strings (CLAUDE.md / coding
standards: "Construct Decimals from strings").

``PAY_PERIODS_PER_YEAR`` and ``MONTHS_PER_YEAR`` are the canonical
biweekly-to-monthly conversion factors (Shekel is organised around 26
biweekly pay periods per year). Co-locating them with the rounding
helpers gives every monetary code path one import for the small fixed
set of cross-service financial constants. Per E-24 / HIGH-05 the factor
is defined exactly once here; any future 26/12 inlining is a regression
of D6-05.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

CENTS = Decimal("0.01")

PAY_PERIODS_PER_YEAR = Decimal("26")
MONTHS_PER_YEAR = Decimal("12")


def round_money(value: Decimal) -> Decimal:
    """Round a monetary Decimal to cents using ``ROUND_HALF_UP``.

    The default boundary rounding for every displayed or persisted
    monetary amount in the app. Use this at the boundary; keep
    intermediate arithmetic at full Decimal precision.

    Args:
        value: a Decimal in full precision. ``float`` is rejected to
            prevent the float-imprecision leak the helper exists to
            eliminate; callers construct Decimal from strings.

    Returns:
        ``value`` quantized to ``Decimal("0.01")`` with
        ``rounding=ROUND_HALF_UP``. ``Decimal("2.345")`` becomes
        ``Decimal("2.35")``, never ``Decimal("2.34")`` (which is what
        Python's default ``ROUND_HALF_EVEN`` would produce).

    Raises:
        TypeError: if ``value`` is not a ``Decimal``. Specifically
            rejects ``float`` so a caller cannot bypass the Decimal
            contract by accident.
    """
    if not isinstance(value, Decimal):
        raise TypeError(
            f"round_money expects Decimal, got {type(value).__name__}: {value!r}"
        )
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def round_money_ceiling(value: Decimal) -> Decimal:
    """Round a monetary Decimal up to cents using ``ROUND_CEILING``.

    Sanctioned variant for cases where under-funding by a fraction of a
    cent must never round down -- specifically the savings-goal monthly
    contribution computation, which by design over-funds rather than
    under-funds the target. Naming the variant at the call site makes
    the deviation from default ``round_money`` explicit and auditable;
    callers must never reach a non-default rounding mode implicitly.

    Args:
        value: a Decimal in full precision. ``float`` is rejected for
            the same reason as ``round_money``.

    Returns:
        ``value`` quantized to ``Decimal("0.01")`` with
        ``rounding=ROUND_CEILING``. ``Decimal("2.341")`` becomes
        ``Decimal("2.35")``; an already-exact ``Decimal("2.340")``
        stays ``Decimal("2.34")``.

    Raises:
        TypeError: if ``value`` is not a ``Decimal``. Specifically
            rejects ``float``.
    """
    if not isinstance(value, Decimal):
        raise TypeError(
            f"round_money_ceiling expects Decimal, got {type(value).__name__}: {value!r}"
        )
    return value.quantize(CENTS, rounding=ROUND_CEILING)
