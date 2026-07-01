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

This module exposes three rounding helpers and the project's pay-period /
month conversion factors. ``round_money`` is the default boundary
rounding. ``round_money_ceiling`` is the explicitly-named sanctioned
variant for the savings-goal monthly contribution case, where under-
funding by a fraction of a cent must never round down. ``round_money_floor``
is the sanctioned variant for largest-remainder cent allocation, where a
set of display rows must sum exactly to their already-rounded total --
naming each exception at the call site makes the deviation auditable.

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
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

CENTS = Decimal("0.01")
ZERO = Decimal("0")
HUNDRED = Decimal("100")

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


def round_money_floor(value: Decimal) -> Decimal:
    """Round a monetary Decimal down to cents using ``ROUND_FLOOR``.

    Sanctioned variant for largest-remainder cent allocation (the
    escrow display rows, deep-hunt #17): each row starts from its
    floored value and the leftover cents -- the difference between the
    sum of floors and the sum-then-rounded total -- are handed out to
    the rows with the largest fractional remainders, so the rendered
    rows always add up to the stated total without changing the total
    itself. Naming the variant at the call site makes the deviation
    from default ``round_money`` explicit and auditable; callers must
    never reach a non-default rounding mode implicitly.

    Args:
        value: a Decimal in full precision. ``float`` is rejected for
            the same reason as ``round_money``.

    Returns:
        ``value`` quantized to ``Decimal("0.01")`` with
        ``rounding=ROUND_FLOOR``. ``Decimal("2.349")`` becomes
        ``Decimal("2.34")``; a negative ``Decimal("-2.341")`` becomes
        ``Decimal("-2.35")`` (floor moves toward negative infinity).

    Raises:
        TypeError: if ``value`` is not a ``Decimal``. Specifically
            rejects ``float``.
    """
    if not isinstance(value, Decimal):
        raise TypeError(
            f"round_money_floor expects Decimal, got {type(value).__name__}: {value!r}"
        )
    return value.quantize(CENTS, rounding=ROUND_FLOOR)


def percent_complete(total: Decimal, target: Decimal) -> Decimal:
    """Compute ``total`` as a percentage of ``target``, clamped to [0, 100].

    The single numeric contract behind every "percent funded" / progress-
    bar surface (the budget dashboard's savings-goal cards, the companion
    entry view).  Guards against division by zero and clamps the result so
    a render never receives a negative width or one exceeding 100%.

    Args:
        total: The amount accumulated so far (sum of entries / balance).
        target: The budgeted or goal amount.  When ``<= 0`` the function
            returns ``Decimal("0")`` rather than dividing by zero or
            producing a misleading negative percentage.

    Returns:
        A Decimal in ``[0, 100]`` quantized to two decimal places with
        ``ROUND_HALF_UP`` for the in-range case; the un-quantized
        ``Decimal("0")`` when ``target <= 0`` or the ratio is negative,
        and ``Decimal("100.00")`` when the ratio exceeds 100%.
    """
    if target <= ZERO:
        return ZERO
    pct = (total / target * HUNDRED).quantize(CENTS, rounding=ROUND_HALF_UP)
    if pct > HUNDRED:
        return Decimal("100.00")
    if pct < ZERO:
        return ZERO
    return pct


def accrue_monthly_interest(balance: Decimal, annual_rate: Decimal) -> Decimal:
    """Return one month's interest on ``balance`` at ``annual_rate``.

    The single monthly-accrual primitive every amortization surface shares:
    ``round_money(balance * annual_rate / 12)`` with a zero-rate guard.  The
    historical replay (``rate_period_engine._replay_payment_row``), the forward
    projection (``amortization_engine`` schedule), the contractual balance walk
    (``rate_period_engine._amortize_forward``), and the posting-ledger loan-payment
    split (``loan_posting_service``) ALL call this one function, so the interest
    they accrue is byte-identical by construction -- a drifting copy of the
    formula can no longer desynchronise a displayed loan balance from a posted
    one (the whole premise of the parallel-run posting ledger).  ``ROUND_HALF_UP``
    via :func:`round_money` is the project's only rounding boundary; the
    intermediate ``balance * (annual_rate / 12)`` stays at full Decimal precision
    and rounds exactly once.

    Args:
        balance: The outstanding balance before this month's payment.  ``float``
            is rejected by :func:`round_money` at the boundary, as everywhere.
        annual_rate: The governing period's annual rate as a decimal fraction
            (e.g. ``Decimal("0.06875")`` for 6.875%).  A non-positive rate
            accrues no interest (a zero-interest period).

    Returns:
        The month's interest quantized to cents, or ``Decimal("0.00")`` when
        ``annual_rate <= 0``.
    """
    if annual_rate <= 0:
        return Decimal("0.00")
    return round_money(balance * (annual_rate / MONTHS_PER_YEAR))
