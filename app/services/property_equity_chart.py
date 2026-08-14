"""
Shekel Budget App -- Property equity-over-time chart producer.

Builds the market-value / secured-debt / equity monthly series the property
detail band chart draws (``docs/design/account_detail_audit.md``, "Property
equity chart" + the 2026-07-11 Loop A lock, direction A "stacked shares"), on
a CALENDAR-DATE axis anchored at ``today`` -- the date-anchored rebuild
(``docs/plans/historical/implementation_plan_property_equity_chart_rebuild.md``) that
replaces the index-space producer whose three correctness defects (H1 paid-off
fallback unreachable, H2 front-aligned merge, H3 fabricated past values) are
registered in ``docs/design/property_detail_followups.md``.

The producer is PURE: it takes each secured loan's already-folded debt line (a
:class:`~app.services.balance_at.SecuredLoanSeries`: a tiered ``{(year, month):
(balance, tier)}`` map and the seam's ``is_retired``) plus the market value,
appreciation rate, and ``today`` -- no resolver, no DB.  The map is folded by the
balance-at seam (:func:`app.services.balance_at.secured_loan_series`, plan step C5)
off the read pass's ONE memoized resolution -- the same one the equity hero
(:func:`app.services.home_equity_service.resolve_home_equity`) reads -- so the two
surfaces reconcile by construction and no loan is resolved twice per page load.
This producer's job is presentation only: it unions the loans' months into one
axis, sums their debt, compounds the value line, and nets equity.

The debt itself is the seam's FOLD, not this producer's re-derivation.  Each loan's
map already carries, per calendar month, the balance the loan owed and its
confidence tier: the contractual back-projection before its tracking start
(``estimated``), the fold of recorded payments up to today (``confirmed``), and the
forward projection after (``projected``); a month outside the loan's origination-to-
payoff span is absent from its map, which this producer reads as ``$0.00`` -- so a
younger loan never lands a balance in a month before it existed, and a paid-off loan
drops cleanly.  Where two loans' tiers meet in one calendar month the summed line
takes the least-confident (an estimated dollar and a confirmed dollar read as
estimated).

Every axis decision keys off a CALENDAR DATE relative to ``today``, never a schedule
index:

* the value line holds today's anchor flat for every month ``<= today`` (its past
  is unknown) and compounds at the appreciation rate strictly after, via the ONE
  appreciation primitive in the codebase
  (:func:`app.services.growth_engine.span_return_rate`);
* the axis spans ``min(origination, today) .. max(payoff, today)`` across the
  outstanding loans, so a mortgage that has not closed yet reads ``$0.00`` from
  today until it originates (never its principal clamped onto today), and a
  past-maturity loan still owing runs to today; and
* "no outstanding secured debt" (drives the loan-less fallback) is decided HERE, by
  the seam's :attr:`~app.services.balance_at.LoanFigures.is_retired` carried on each
  series -- ONE predicate, applied in one place, so a fully-retired property reaches
  the 120-month appreciation fallback while a mortgage that has not closed yet (also
  owing ``$0.00``, and emphatically not retired) stays on the chart.  The caller
  passes EVERY configured secured loan and lets this rule decide; when the caller
  filtered too, the two copies disagreed.

Boundary discipline (``CLAUDE.md``: services are isolated from Flask): no Flask
imports.  All money is :class:`~decimal.Decimal`; the ``float()`` cast for
Chart.js JSON is the route's serialization boundary, never here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.balance_at import (
    TIER_CONFIRMED,
    TIER_ESTIMATED,
    TIER_PROJECTED,
    SecuredLoanSeries,
)
from app.services.growth_engine import span_return_rate
from app.utils.dates import add_months
from app.utils.money import round_money

# Market value with no securing debt (a loan-less OR fully-paid-off property):
# project the appreciation-only arc this many months forward -- 10 years, the
# Loop A fallback horizon (account_detail_audit.md, Loop A lock item 7; the
# developer confirmed one fallback covers "paid-off / loan-less").
_FALLBACK_MONTHS = 120

# Chart.js x-axis label format for a multi-year monthly axis: month
# abbreviation + full year (e.g. "Jun 2026") -- the loan band's convention.
_MONTH_YEAR_LABEL = "%b %Y"

# ``chart_state`` values: the caption / render variant the property template
# and ``property_detail.js`` switch on.  Presentation state tokens the template
# owns (not ref-table rows), so the IDs-not-name-strings invariant does not
# apply; naming them keeps the string literal in one place.
CHART_STATE_STANDARD = "standard"
CHART_STATE_ZERO_RATE = "zero_rate"
CHART_STATE_NO_LOANS = "no_loans"

# ``debt_tier`` values are owned by the seam that assigns them
# (:mod:`app.services.balance_at._secured_debt`) and imported above; the route and
# tests still read them as ``property_equity_chart.TIER_*``, so the re-export is
# deliberate.  ``_TIER_PRIORITY`` orders them least-confident-first: a calendar
# month whose debt sums loans of different tiers takes the least-confident
# contributor's tier (an estimated dollar and a confirmed dollar read as
# estimated).
_TIER_PRIORITY = {TIER_ESTIMATED: 0, TIER_PROJECTED: 1, TIER_CONFIRMED: 2}

# The appreciation-rate zero sentinel (E-12): an unset rate stored as 0 holds
# market value flat and drives the "zero_rate" caption when debt still exists.
_ZERO_RATE = Decimal("0")
_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class PropertyEquityChart:
    """The property equity chart's per-month ``Decimal`` series + render state.

    A frozen snapshot the route serializes to Chart.js JSON (floating each
    ``Decimal`` only at that boundary).  In the ``no_loans`` fallback ``debt``,
    ``equity`` and ``debt_tier`` are empty lists and ``today_index`` is 0 -- the
    whole value line is a forward appreciation projection with nothing to net
    out.

    Attributes:
        labels: Month labels (``%b %Y``) -- the shared calendar x-axis.
        value: Market value per month: flat at today's anchor for every month
            ``<= today``, compounding at the appreciation rate after.
        debt: Summed secured-debt balance per calendar month, or ``[]`` in the
            no-loans fallback.
        equity: ``value - debt`` per month, or ``[]`` in the no-loans fallback.
        today_index: Axis index of the calendar month containing ``today`` --
            the value flat / compounding boundary and the Today marker.  0 in
            the fallback (month 0 is today).
        debt_tier: Per-month confidence of the debt line
            (``estimated`` / ``confirmed`` / ``projected``), or ``[]`` in the
            fallback.  Parallel to ``debt``.
        chart_state: ``"standard"`` / ``"zero_rate"`` / ``"no_loans"``.
    """

    labels: list[str]
    value: list[Decimal]
    debt: list[Decimal]
    equity: list[Decimal]
    today_index: int
    debt_tier: list[str]
    chart_state: str


def _month_key(day: date) -> tuple[int, int]:
    """Return ``(year, month)`` -- the calendar-month key a row maps to."""
    return (day.year, day.month)


def _months_between_keys(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Return the whole-month count from ``start`` to ``end`` (>= 0 when ordered)."""
    return (end[0] - start[0]) * 12 + (end[1] - start[1])


def _value_series(
    market_value: Decimal,
    appreciation_rate: Decimal,
    axis_dates: list[date],
    today_index: int,
    today: date,
) -> list[Decimal]:
    """Hold the market value flat through ``today``, then compound it forward.

    Flat-carries ``market_value`` for every axis month ``<= today``
    (``index <= today_index`` -- its past is unknown, today's anchor is the only
    honest value), then compounds it at ``appreciation_rate`` for each later
    month via the growth engine's one appreciation primitive
    (:func:`app.services.growth_engine.span_return_rate`): the factor for a
    month dated ``d`` is ``(1 + rate) ** (((d - today).days + 1) / 365)``.  A
    zero ``appreciation_rate`` (the unset sentinel) leaves the whole line flat.
    Keying the flat / compounding split on the DATE -- not a confirmed-row count
    -- is what stops a past-dated projected month from compounding to a
    fabricated value (the H3 fix); the same test additionally keeps every span
    reaching the primitive strictly forward, which is what makes its refusal of
    a crossed span unreachable from here (plan step C2-e).

    **The span used to be a fabricated period.**  This module carried an
    ``_AppreciationSpan`` dataclass whose only purpose was to expose
    ``start_date`` / ``end_date`` to a rate function that duck-typed a "period";
    C2-e gave that function the two dates directly and the impostor had nothing
    left to impersonate.

    Args:
        market_value: Today's anchored market value.
        appreciation_rate: The annual appreciation rate (decimal fraction).
        axis_dates: The x-axis month dates (first of each calendar month).
        today_index: The index of the calendar month containing ``today`` -- the
            flat / compounding boundary.
        today: The compounding origin -- the anchor "as of" date.

    Returns:
        One ``Decimal`` market value per axis month (parallel to ``axis_dates``).
    """
    values: list[Decimal] = []
    for index, month_date in enumerate(axis_dates):
        if index <= today_index or (month_date - today).days <= 0:
            values.append(market_value)
        else:
            factor = Decimal("1") + span_return_rate(
                appreciation_rate, today, month_date,
            )
            values.append(round_money(market_value * factor))
    return values


def _fallback_chart(
    market_value: Decimal, appreciation_rate: Decimal, today: date,
) -> PropertyEquityChart:
    """Build the loan-less / paid-off fallback: 120 months of appreciation only.

    No outstanding secured debt, so there is no payoff axis and nothing to net
    out: the chart is the market value's 10-year appreciation arc alone
    (``debt``, ``equity`` and ``debt_tier`` empty, ``today_index`` 0 -- month 0
    is today, the whole value line a forward projection).  ``chart_state`` is
    always ``"no_loans"`` here; the template's no-loans caption handles the
    unset-rate sub-case.

    Args:
        market_value: Today's anchored market value.
        appreciation_rate: The annual appreciation rate (decimal fraction).
        today: The compounding origin.

    Returns:
        A :class:`PropertyEquityChart` in the ``no_loans`` state.
    """
    axis_dates = [add_months(today, month) for month in range(_FALLBACK_MONTHS)]
    labels = [day.strftime(_MONTH_YEAR_LABEL) for day in axis_dates]
    value = _value_series(market_value, appreciation_rate, axis_dates, 0, today)
    return PropertyEquityChart(
        labels=labels,
        value=value,
        debt=[],
        equity=[],
        today_index=0,
        debt_tier=[],
        chart_state=CHART_STATE_NO_LOANS,
    )


def _debt_series(
    loan_month_balances: list[dict[tuple[int, int], tuple[Decimal, str]]],
    axis_keys: list[tuple[int, int]],
    today_index: int,
) -> tuple[list[Decimal], list[str]]:
    """Sum the loans' per-calendar-month debt maps into one debt line + tier line.

    Each loan's fold map (:func:`app.services.balance_at.secured_loan_series`) has
    an entry for every month in its origination-to-payoff span and none outside it,
    so a month the loan does not span contributes ``$0.00`` (its ``.get`` misses) --
    a younger loan never adds a balance to a month before it existed (the H2 fix)
    and a paid-off loan drops out.  Within the span the seam sampled the fold at
    every month, so there is no gap to fill: the balance is simply read.  Each axis
    month's summed balance is the debt line, and its ``debt_tier`` is the
    least-confident contributing tier (an estimated dollar and a confirmed dollar
    read as estimated); a month with no contributor at all (a gap between two
    non-overlapping loans) is tagged ``confirmed`` up to today and ``projected``
    after, purely to style its zero-line segment.

    Args:
        loan_month_balances: Each outstanding loan's month-keyed
            ``(balance, tier)`` fold map, from the seam.
        axis_keys: The ``(year, month)`` key of each axis month.
        today_index: The index of today's month (for the empty-month tier).

    Returns:
        ``(debt, debt_tier)`` -- parallel lists, one entry per axis month.
    """
    debt: list[Decimal] = []
    debt_tier: list[str] = []
    for index, key in enumerate(axis_keys):
        total = _ZERO_MONEY
        tiers_present: list[str] = []
        for month_balances in loan_month_balances:
            cell = month_balances.get(key)
            if cell is not None:
                balance, tier = cell
                total += balance
                tiers_present.append(tier)
        debt.append(total)
        if tiers_present:
            debt_tier.append(min(tiers_present, key=_TIER_PRIORITY.__getitem__))
        else:
            debt_tier.append(
                TIER_CONFIRMED if index <= today_index else TIER_PROJECTED
            )
    return debt, debt_tier


@dataclass(frozen=True)
class _Axis:
    """The chart's shared calendar x-axis.

    Attributes:
        dates: First-of-month date per axis month (the value line's compounding
            targets).
        keys: The ``(year, month)`` key of each axis month (the debt lookup).
        labels: The ``%b %Y`` label of each axis month.
        today_index: Index of the calendar month containing ``today``.
    """

    dates: list[date]
    keys: list[tuple[int, int]]
    labels: list[str]
    today_index: int


def _build_axis(
    loan_month_balances: list[dict[tuple[int, int], tuple[Decimal, str]]],
    today: date,
) -> _Axis:
    """Build the contiguous monthly axis spanning the loans and today.

    Runs from the earliest month any loan owes -- OR today, whichever is earlier --
    to the latest month any loan owes, or today, whichever is later: the
    ``min(origination, today) .. max(payoff, today)`` span of plan step C5.
    Including ``today`` in the span is what puts a not-yet-originated mortgage's
    ``$0.00`` months (today until it closes) on the axis rather than clamping its
    principal onto today, and what makes ``today_index`` a plain offset that needs
    no clamp: today is always inside the span by construction.  Filled month by
    month so a gap between two non-overlapping loans shows ``$0.00`` rather than
    collapsing the axis.

    Args:
        loan_month_balances: Each outstanding loan's month-keyed
            ``(balance, tier)`` fold map, from the seam.  Must be non-empty.
        today: The as-of date.

    Returns:
        The :class:`_Axis`.
    """
    today_key = _month_key(today)
    all_keys = [
        key for month_balances in loan_month_balances for key in month_balances
    ] + [today_key]
    first_key = min(all_keys)
    span_months = _months_between_keys(first_key, max(all_keys))
    first_of_span = date(first_key[0], first_key[1], 1)
    dates = [add_months(first_of_span, offset) for offset in range(span_months + 1)]
    return _Axis(
        dates=dates,
        keys=[_month_key(day) for day in dates],
        labels=[day.strftime(_MONTH_YEAR_LABEL) for day in dates],
        today_index=_months_between_keys(first_key, today_key),
    )


def build_property_equity_chart(
    secured_loans: list[SecuredLoanSeries],
    market_value: Decimal,
    appreciation_rate: Decimal,
    today: date,
) -> PropertyEquityChart:
    """Build a Property's market-value / secured-debt / equity monthly series.

    The property detail band's equity-over-time chart (Loop A direction A), on a
    calendar axis anchored at ``today``.  ``secured_loans`` are the property's
    secured loans (each carrying its fold-derived per-month debt map and the seam's
    RETIRED predicate); a RETIRED loan is dropped here, so it never affects the debt
    line.  Retired means borrowed and now owing nothing; a loan that has not been
    borrowed yet also owes ``$0.00`` and is NOT retired, so it stays charted -- its
    debt line is entirely ahead of it.  The axis spans ``min(origination, today) ..
    max(payoff, today)`` across the outstanding loans; the debt line sums each
    loan's per-calendar-month fold balance (:func:`_debt_series`); the value line
    compounds the anchor forward from ``today`` (:func:`_value_series`); equity is
    the per-month ``value - debt``.

    With no outstanding secured loan (none linked, none configured, or every one
    retired), returns the loan-less fallback: 120 months of appreciation only
    (:func:`_fallback_chart`).  Otherwise ``chart_state`` is ``"zero_rate"`` when
    the appreciation rate is the unset sentinel (value holds flat, equity still
    grows as debt amortizes), else ``"standard"``.

    Args:
        secured_loans: The property's secured loans' fold-derived debt maps (each a
            :class:`~app.services.balance_at.SecuredLoanSeries`, assembled by the
            balance-at seam).  Empty, or all-retired, drives the fallback.
        market_value: The Property's latest asserted valuation (resolved by
            ``home_equity_service`` from ``account_anchor_history``) -- today's
            honest value, the anchor the value line compounds from.
        appreciation_rate: The Property's annual appreciation rate (decimal
            fraction; ``AssetAppreciationParams.annual_appreciation_rate``).
        today: The as-of / compounding-origin date (``date.today()``).

    Returns:
        A :class:`PropertyEquityChart` -- ``Decimal`` series the route floats at
        its JSON serialization boundary.
    """
    # A loan is charted unless it is RETIRED -- decided by the SEAM's one predicate
    # (``LoanFigures.is_retired``, handed over on the series), NOT by a local
    # ``balance <= 0`` test.  This module and the property route each used to carry
    # their own copy of that test, and two copies of one rule is how BOTH came to
    # drop a mortgage closing next month -- it owes $0.00 today, which is true, and
    # it is not remotely retired.  ``is_retired`` carries the origination guard, so
    # a not-yet-BORROWED loan stays charted: its debt line is entirely ahead of it.
    # A property whose every secured loan is retired falls through to the
    # appreciation-only arc.
    #
    # NOT ``is_paid_off``: that is ``is_retired`` PLUS a confirmed-payment guard,
    # which exists for BADGING (do not congratulate a degenerate $0-anchor loan).
    # A mortgage paid off by a lump sum recorded as a balance true-up has no payment
    # rows, so it reads ``is_paid_off=False`` while owing $0.00; charting on it drew
    # $197,049.32 of phantom debt beside an equity hero reporting $0.00.
    outstanding = [loan for loan in secured_loans if not loan.is_retired]
    if not outstanding:
        return _fallback_chart(market_value, appreciation_rate, today)

    # The seam already folded each loan's per-month debt; thread those maps into
    # both the axis span and the debt sum (no loan is re-walked).
    loan_month_balances = [loan.month_balances for loan in outstanding]
    axis = _build_axis(loan_month_balances, today)
    debt, debt_tier = _debt_series(
        loan_month_balances, axis.keys, axis.today_index,
    )
    value = _value_series(
        market_value, appreciation_rate, axis.dates, axis.today_index, today,
    )
    equity = [value[index] - debt[index] for index in range(len(axis.dates))]
    chart_state = (
        CHART_STATE_ZERO_RATE
        if appreciation_rate == _ZERO_RATE
        else CHART_STATE_STANDARD
    )
    return PropertyEquityChart(
        labels=axis.labels,
        value=value,
        debt=debt,
        equity=equity,
        today_index=axis.today_index,
        debt_tier=debt_tier,
        chart_state=chart_state,
    )
