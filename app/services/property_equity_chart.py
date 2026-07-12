"""
Shekel Budget App -- Property equity-over-time chart producer.

Builds the market-value / secured-debt / equity monthly series the property
detail band chart draws (``docs/design/account_detail_audit.md``, "Property
equity chart" + the 2026-07-11 Loop A lock, direction A "stacked shares"), on
a CALENDAR-DATE axis anchored at ``today`` -- the date-anchored rebuild
(``docs/plans/implementation_plan_property_equity_chart_rebuild.md``) that
replaces the index-space producer whose three correctness defects (H1 paid-off
fallback unreachable, H2 front-aligned merge, H3 fabricated past values) are
registered in ``docs/design/property_detail_followups.md``.

The producer is PURE: it takes each secured loan's already-resolved rows (a
:class:`SecuredLoanSeries`: the pre-tracking contractual back-projection, the
resolved schedule, and the authoritative current balance) plus the market
value, appreciation rate, and ``today`` -- no ``resolve_account_loan``, no DB.
The route resolves each loan ONCE and feeds those rows to both the equity hero
(``home_equity_service.compute_home_equity``) and this producer, so the two
surfaces reconcile by construction (they read the same resolution) and no loan
is resolved twice per page load.

Every per-month decision keys off a CALENDAR DATE relative to ``today``, never a
schedule index:

* the value line holds today's anchor flat for every month ``<= today`` (its
  past is unknown) and compounds at the appreciation rate strictly after, via
  the ONE appreciation primitive in the codebase
  (:func:`app.services.growth_engine.period_return_rate`);
* each loan's debt contributes ``$0.00`` before its origination and after its
  payoff, its contractual back-projection (``estimated`` tier) between
  origination and tracking start, its confirmed history (``confirmed``) up to
  today, and its committed projection (``projected``) after -- summed per
  CALENDAR MONTH, so a younger loan's balance never lands in a month before it
  existed; and
* "no outstanding secured debt" (drives the loan-less fallback) is decided by
  the loans the caller passes -- it passes only loans with a positive balance
  today -- so a fully-paid-off property reaches the 120-month appreciation
  fallback.

Boundary discipline (``CLAUDE.md``: services are isolated from Flask): no Flask
imports.  All money is :class:`~decimal.Decimal`; the ``float()`` cast for
Chart.js JSON is the route's serialization boundary, never here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.services.growth_engine import period_return_rate
from app.utils.dates import add_months
from app.utils.money import round_money

if TYPE_CHECKING:
    # Typing-only import (lazy string annotations): the module reads only
    # ``.remaining_balance`` / ``.payment_date`` / ``.is_confirmed`` off each
    # row, so it never needs the class at runtime.
    from app.services.amortization_engine import AmortizationRow

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

# ``debt_tier`` values: the per-month confidence of the summed debt line, so
# the renderer can style the pre-tracking contractual estimate apart from
# recorded history and forward projection.  ``_TIER_PRIORITY`` orders them
# least-confident-first: a calendar month whose debt sums loans of different
# tiers takes the least-confident contributor's tier (an estimated dollar and a
# confirmed dollar read as estimated).
TIER_ESTIMATED = "estimated"
TIER_CONFIRMED = "confirmed"
TIER_PROJECTED = "projected"
_TIER_PRIORITY = {TIER_ESTIMATED: 0, TIER_PROJECTED: 1, TIER_CONFIRMED: 2}

# The appreciation-rate zero sentinel (E-12): an unset rate stored as 0 holds
# market value flat and drives the "zero_rate" caption when debt still exists.
_ZERO_RATE = Decimal("0")
_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class SecuredLoanSeries:
    """One secured loan's already-resolved rows for the equity chart.

    The caller (the property route) resolves each loan ONCE and packs its rows
    here, so this producer stays pure.  ``back_projection`` and ``schedule``
    together span the loan's origination-first-payment .. payoff months, one
    row per calendar month.

    Attributes:
        back_projection: The contractual-from-origination rows for the months
            BEFORE the resolved schedule begins (a mid-life-imported loan's
            pre-tracking-start estimate;
            :func:`app.services.loan_resolution.contractual_schedule_from_origination`,
            clipped to ``payment_date < schedule[0].payment_date``).  Empty for
            a loan whose schedule already starts at origination.  Rendered as
            the ``estimated`` tier.
        schedule: The loan's resolved schedule
            (``resolve_account_loan(...).schedule`` == confirmed history +
            committed forward): ``is_confirmed`` rows are recorded history
            (``confirmed`` tier), the rest are the committed projection
            (``projected`` tier).
        current_balance: The loan's authoritative balance today
            (``LoanState.current_balance``) -- the same figure the equity hero
            nets, so the chart and the hero reconcile at the last confirmed
            month by construction.
    """

    back_projection: list["AmortizationRow"]
    schedule: list["AmortizationRow"]
    current_balance: Decimal


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


@dataclass(frozen=True)
class _AppreciationSpan:
    """A start / end date pair for :func:`period_return_rate`.

    ``period_return_rate`` scales an annual rate to a period's inclusive
    calendar-day span via ``(end_date - start_date).days + 1``.  Reusing it for
    the anchor-to-month appreciation span keeps the codebase's ONE appreciation
    formula (rather than re-deriving ``(1 + rate) ** (days / 365)`` here); this
    lightweight span is the object it reads ``start_date`` / ``end_date`` off,
    since a real pay period is overkill for one span.
    """

    start_date: date
    end_date: date


def _month_key(day: date) -> tuple[int, int]:
    """Return ``(year, month)`` -- the calendar-month key a row maps to."""
    return (day.year, day.month)


def _months_between_keys(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Return the whole-month count from ``start`` to ``end`` (>= 0 when ordered)."""
    return (end[0] - start[0]) * 12 + (end[1] - start[1])


def _loan_month_tiers(
    loan: SecuredLoanSeries,
) -> dict[tuple[int, int], tuple[Decimal, str]]:
    """Map a loan's rows to ``{(year, month): (balance, tier)}``.

    The back-projection rows are the ``estimated`` pre-tracking contractual
    curve; the schedule rows are ``confirmed`` history (``is_confirmed``) or the
    ``projected`` committed forward.  Schedule rows are applied AFTER the
    back-projection so that, in the (guarded-against) event a clipped
    back-projection month coincides with the schedule's first month, the
    recorded schedule wins.  One row per calendar month (the resolver
    redistributes biweekly collisions), so the last write per month is the
    month's balance.

    Args:
        loan: The loan's resolved rows.

    Returns:
        The month-keyed ``(balance, tier)`` map across the loan's whole span.
    """
    tiers: dict[tuple[int, int], tuple[Decimal, str]] = {}
    for row in loan.back_projection:
        tiers[_month_key(row.payment_date)] = (
            row.remaining_balance, TIER_ESTIMATED,
        )
    for row in loan.schedule:
        tier = TIER_CONFIRMED if row.is_confirmed else TIER_PROJECTED
        tiers[_month_key(row.payment_date)] = (row.remaining_balance, tier)
    return tiers


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
    (:func:`app.services.growth_engine.period_return_rate`): the factor for a
    month dated ``d`` is ``(1 + rate) ** (((d - today).days + 1) / 365)``.  A
    zero ``appreciation_rate`` (the unset sentinel) leaves the whole line flat.
    Keying the flat / compounding split on the DATE -- not a confirmed-row count
    -- is what stops a past-dated projected month from compounding to a
    fabricated value (the H3 fix); a non-positive span additionally short-
    circuits to no growth, so the degenerate-span clamp inside
    ``period_return_rate`` can never fabricate appreciation here.

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
            span = _AppreciationSpan(start_date=today, end_date=month_date)
            factor = Decimal("1") + period_return_rate(appreciation_rate, span)
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
    loan_tiers: list[dict[tuple[int, int], tuple[Decimal, str]]],
    axis_keys: list[tuple[int, int]],
    today_index: int,
) -> tuple[list[Decimal], list[str]]:
    """Sum the loans' per-calendar-month balances into one debt line + tier line.

    For each axis month, each loan contributes the balance its rows give for
    that calendar month, or ``$0.00`` for a month outside its
    origination-to-payoff span (so a younger loan never adds a balance to a
    month before it existed -- the H2 fix).  The month's ``debt_tier`` is the
    least-confident contributing loan's tier; a month with no contributor
    (``$0.00`` debt, e.g. a gap between two non-overlapping loans) is tagged
    ``confirmed`` up to today and ``projected`` after, purely to style its
    zero-line segment.

    Args:
        loan_tiers: Each loan's month-keyed ``(balance, tier)`` map
            (:func:`_loan_month_tiers`), resolved once by the caller.
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
        for tiers in loan_tiers:
            if key in tiers:
                balance, tier = tiers[key]
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
    loan_tiers: list[dict[tuple[int, int], tuple[Decimal, str]]], today: date,
) -> _Axis:
    """Build the contiguous monthly axis spanning every loan's rows.

    Runs from the earliest month any loan has a row to the latest payoff, filled
    month by month so a gap between two non-overlapping loans shows ``$0.00``
    rather than collapsing the axis.  ``today_index`` is the calendar month
    containing ``today``, clamped into the axis (an outstanding loan originates
    before today and pays off after, so today's month is inside the span; the
    clamp is defensive).

    Args:
        loan_tiers: Each loan's month-keyed ``(balance, tier)`` map, resolved
            once by the caller.  Must be non-empty.
        today: The as-of date.

    Returns:
        The :class:`_Axis`.
    """
    all_keys = [key for tiers in loan_tiers for key in tiers]
    first_key = min(all_keys)
    span_months = _months_between_keys(first_key, max(all_keys))
    first_of_span = date(first_key[0], first_key[1], 1)
    dates = [add_months(first_of_span, offset) for offset in range(span_months + 1)]
    today_index = min(
        max(_months_between_keys(first_key, _month_key(today)), 0), span_months,
    )
    return _Axis(
        dates=dates,
        keys=[_month_key(day) for day in dates],
        labels=[day.strftime(_MONTH_YEAR_LABEL) for day in dates],
        today_index=today_index,
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
    secured loans (each carrying its pre-tracking back-projection, resolved
    schedule, and current balance); a loan with a zero balance today is paid off
    and is dropped here, so it never affects the debt line.  The axis spans the
    earliest outstanding-loan month to the latest payoff; the debt line sums
    each loan's per-calendar-month balance (:func:`_debt_series`); the value line
    compounds the anchor forward from ``today`` (:func:`_value_series`); equity
    is the per-month ``value - debt``.

    With no outstanding secured loan (none linked, none configured, or every one
    paid off), returns the loan-less fallback: 120 months of appreciation only
    (:func:`_fallback_chart`).  Otherwise ``chart_state`` is ``"zero_rate"`` when
    the appreciation rate is the unset sentinel (value holds flat, equity still
    grows as debt amortizes), else ``"standard"``.

    Args:
        secured_loans: The property's secured loans' resolved rows (each a
            :class:`SecuredLoanSeries`).  Empty, or all-paid-off, drives the
            fallback.
        market_value: The Property's ``current_anchor_balance`` -- today's
            honest valuation, the anchor the value line compounds from.
        appreciation_rate: The Property's annual appreciation rate (decimal
            fraction; ``AssetAppreciationParams.annual_appreciation_rate``).
        today: The as-of / compounding-origin date (``date.today()``).

    Returns:
        A :class:`PropertyEquityChart` -- ``Decimal`` series the route floats at
        its JSON serialization boundary.
    """
    # A loan is charted only while it has a balance to owe TODAY -- decided by
    # the current balance, NOT by schedule emptiness (a loan paid off through
    # confirmed payments keeps its whole confirmed schedule, so an
    # "is the schedule empty" test never fired the fallback; the H1 fix).  A
    # property whose every secured loan is paid off falls through to the arc.
    outstanding = [
        loan for loan in secured_loans if loan.current_balance > _ZERO_MONEY
    ]
    if not outstanding:
        return _fallback_chart(market_value, appreciation_rate, today)

    # Resolve each loan's month-keyed balances ONCE, then thread that state into
    # both the axis span and the debt sum (no loan is re-walked).
    loan_tiers = [_loan_month_tiers(loan) for loan in outstanding]
    axis = _build_axis(loan_tiers, today)
    debt, debt_tier = _debt_series(loan_tiers, axis.keys, axis.today_index)
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
