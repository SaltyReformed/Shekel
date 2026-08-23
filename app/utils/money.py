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

This module exposes three rounding helpers and the calendar-month
denominator every monthly figure divides by. ``round_money`` is the default
boundary rounding. ``round_money_ceiling`` is the explicitly-named sanctioned
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

``MONTHS_PER_YEAR`` is the calendar-month denominator, and it belongs here
because 12 is a property of the calendar rather than of any owner.

**Its partner ``PAY_PERIODS_PER_YEAR = Decimal("26")`` LEFT at the recurrence
arc's plan step R7a-2a, and it was never a constant.** It stood for how often
the owner is paid, which is ``budget.pay_schedule.cadence_days`` and is
user-selectable 1..365 -- so every monthly-equivalent figure on
``/savings``, the Recurring surface and ``/retirement`` was wrong for anyone
not paid biweekly: a weekly-paid owner's ``$100`` per-paycheck bill reported
``$216.67`` a month against a true ``$433.33``. It is now DERIVED per owner by
:class:`app.services.pay_calendar.PayCadence`, which also owns the named unit
conversions the nine reader files used to spell inline. The E-24 / HIGH-05 rule
that produced this paragraph still stands and is stronger: the biweekly-to-
monthly factor has one home, and it is no longer a number this module can
state.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

CENTS = Decimal("0.01")
ZERO = Decimal("0")
HUNDRED = Decimal("100")

MONTHS_PER_YEAR = Decimal("12")


#: The largest magnitude any of this app's money columns can hold.  Every one
#: of them is ``Numeric(12, 2)``, so ten integer digits and two decimal places,
#: and a figure at or above this is UNSTORABLE rather than merely large.
#:
#: **Named because a DERIVED figure can exceed it where an entered one cannot.**
#: A schema bound keeps a typed figure inside the domain (see
#: ``app.schemas.validation._helpers._NON_NEGATIVE_MONETARY``, and plan step
#: X-f2-c3 for what omitting one cost), but a door that SUMS stored columns is
#: bounded by the count of terms rather than by any one of them -- so the sum,
#: and anything derived from it, needs the domain stated somewhere it can be
#: compared against.  Reaching the database with a larger figure is
#: ``psycopg2.errors.NumericValueOutOfRange``, which is an unhandled 500 and,
#: inside a batch, one that discards every item applied beside it.
MONEY_COLUMN_MAX: Decimal = Decimal("9999999999.99")


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
