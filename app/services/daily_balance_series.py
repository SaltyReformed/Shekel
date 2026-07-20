"""
Shekel Budget App -- Daily end-of-day cash-flow balance series.

The one-pass daily producer behind the analytics calendar's flagship
running-balance line and its day-cell end-of-day hero.  It answers "what is
the projected checking balance at the end of each calendar day?" as a true
"checkbook" balance that STEPS on each day's projected flows -- rather than
the period-flat value :func:`app.services.balance_resolver.balance_as_of_date`
(the seam scalar :func:`app.services.balance_at.cash_balance_at`) returns for
every day of a pay period, which would leave the calendar's day cells
visibly disagreeing with a two-week-flat line.

**Reconciliation invariant (the whole point).**  Each projected transaction
contributes on its :func:`~app.utils.dates.attribution_date` -- its
``due_date`` (fallback: the pay period ``start_date``) CLAMPED into its own
period span -- using the SAME projected-only, entry-aware amount the period
roll-forward sums (:func:`~app.services.cash_ledger.sum_projected`).
Because every one of a period's flows therefore lands on or before the
period ``end_date``, the running balance summed through that day equals the
period-end balance the grid shows:

    series[P.end_date] == cash_balance_at(account, scenario, P.end_date)

for every pay period ``P`` whose ``end_date`` falls in the range (given the
normal invariant that a transaction's entries are dated within its own
period; a purchase entry dated after the period end is the one anomaly that
would let the undated sum here and the date-cut ``cash_balance_at`` drift,
which ``balance_as_of_date`` documents).  The line is continuous across
period boundaries, and a day's step equals that day's clamped PROJECTED net,
so the balance line reconciles with the grid's period-end.

This projected line is a distinct basis from the day cells' and summary
strip's nominal flows (``calendar_service`` renders those from
``effective_amount``): the two COINCIDE in ordinary data but diverge exactly
where the projection differs from the nominal amount -- a settled row already
reflected in the anchor (excluded here, shown on the cell as context), a
partially-reconciled envelope's entry-aware reservation, or a live salary /
loan override.  That divergence is the audit's measured-vs-modeled
distinction, which the presentation labels; it is not a disagreement to
eliminate.

**Semantics match the seam's cash-flow view (no per-kind dispatch):**

* **Projected-only.**  Only Projected rows move the line (settled / credit /
  cancelled contribute zero, filtered by ``sum_projected`` via
  :func:`~app.utils.balance_predicates.is_projected`), because the anchor
  already reflects settled activity -- identical to the grid.  Settled rows
  still belong on the calendar's day cells as context (a "Paid" badge); they
  do not move this projected line.
* **Entry-aware.**  A projected envelope expense holds back its entry-aware
  reservation, not its raw estimate; projected salary income honors the live
  override.  Both come straight from ``sum_projected``, so a day's step and
  the period roll-forward cannot drift.
* **Pre-anchor days stay flat at the anchor balance**, matching
  :func:`~app.services.balance_resolver.balance_as_of_date`'s "pre-anchor
  returns anchor" convention: the projection never walks backward from the
  anchor.

**Seam placement.**  This module is part of the ``balance_at`` seam cluster
(``app.services.balance_at`` exposes it as ``cash_daily_balance_series``): it
composes the fenced producer ``balance_as_of_date`` for the seed and reuses
:func:`~app.services.cash_ledger.load_balance_transactions` (one
entries-eager query) and ``sum_projected`` for the per-day distribution, so
no consumer re-invents the balance boundary the seam owns (W9906).  It
imports no Flask symbol and performs no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services.balance_resolver import balance_as_of_date
from app.services.cash_ledger import (
    live_amount_overrides,
    load_balance_transactions,
    resolve_anchor,
    sum_projected,
)
from app.services.pay_period_service import get_overlapping_periods
from app.utils.dates import attribution_date
from app.utils.money import round_money

_ZERO = Decimal("0")


def build_daily_series(
    account: Account,
    scenario_id: int,
    first_day: date,
    last_day: date,
    *,
    amount_overrides: "dict[int, Decimal] | None" = None,
) -> "OrderedDict[date, Decimal]":
    """Return the projected end-of-day cash-flow balance for each day in a range.

    The daily counterpart of
    :func:`~app.services.balance_resolver.balance_as_of_date`, built for the
    calendar's running-balance line: a true checkbook balance that steps on
    each day's projected, period-clamped, entry-aware flows and reconciles
    with the grid at every period end (see the module docstring's
    reconciliation invariant).

    Args:
        account: The :class:`~app.models.account.Account` to project.  Its
            kind is NOT consulted (cash-flow view); must be session-attached.
        scenario_id: The scenario id; scopes the transaction load, the seed,
            and the anchor.
        first_day: Inclusive first calendar day of the range.
        last_day: Inclusive last calendar day of the range.

        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (Workstream B).  Built once here when None so
            projected salary income and derived loan debits are live,
            consistent with :func:`balance_as_of_date`.

    Returns:
        An ``OrderedDict`` mapping every calendar ``date`` in
        ``[first_day, last_day]`` (ascending) to its projected end-of-day
        cash-flow balance, quantized to cents.  Every day in the range is a
        key: days in a gap between pay periods, before the first overlapping
        period, or beyond the projection horizon carry the last known
        balance forward.  An inverted range (``last_day < first_day``)
        returns an empty map.

    Raises:
        TypeError: When ``first_day`` or ``last_day`` is not a
            :class:`datetime.date`.
        ValueError: Propagated from :func:`resolve_anchor` when the account
            has no anchor history (the post-Commit-3 unreachable state).
    """
    for label, value in (("first_day", first_day), ("last_day", last_day)):
        if not isinstance(value, date):
            raise TypeError(
                f"build_daily_series expects a datetime.date for {label}, "
                f"got {value!r}"
            )

    if last_day < first_day:
        return OrderedDict()

    overlapping = get_overlapping_periods(account.user_id, first_day, last_day)
    if not overlapping:
        # The range is entirely outside the pay-period horizon (before the
        # first period or after the last) or sits inside a gap between two
        # periods.  The cash-flow balance is period-flat across such a span,
        # so a single seam read fills every day.
        return _flat_series(
            first_day, last_day,
            balance_as_of_date(account, scenario_id, last_day),
        )

    anchor = resolve_anchor(account, scenario_id)
    transactions = load_balance_transactions(
        account, scenario_id, [period.id for period in overlapping],
    )
    if amount_overrides is None:
        amount_overrides = live_amount_overrides(
            account, scenario_id, transactions,
        )

    net_by_day = _net_by_attribution_day(
        transactions, overlapping, anchor.period.period_index, amount_overrides,
    )

    # Seed: the projected end balance the day BEFORE the first overlapping
    # period begins -- a pay-period boundary, where the seam scalar equals
    # the grid's period-end.  A first period at or before the anchor seeds at
    # the anchor balance (balance_as_of_date's pre-anchor convention), so the
    # ramp begins from the anchor with no backward projection.
    seed = balance_as_of_date(
        account, scenario_id, overlapping[0].start_date - timedelta(days=1),
    )

    covered = _ramp_covered_days(
        overlapping, net_by_day, seed, first_day, last_day,
    )
    return _fill_in_order(covered, first_day, last_day, seed)


def _flat_series(
    first_day: date, last_day: date, value: Decimal,
) -> "OrderedDict[date, Decimal]":
    """Return every day in ``[first_day, last_day]`` mapped to one flat value.

    Used for a range with no overlapping pay period, where the cash-flow
    balance does not change day to day (pre-horizon: the anchor; post-horizon
    or in a gap: the last projected balance).

    Args:
        first_day: Inclusive first day.
        last_day: Inclusive last day.
        value: The flat balance to assign every day (quantized here).

    Returns:
        The ``OrderedDict`` day -> ``round_money(value)`` for the range.
    """
    rounded = round_money(value)
    series: "OrderedDict[date, Decimal]" = OrderedDict()
    day = first_day
    while day <= last_day:
        series[day] = rounded
        day += timedelta(days=1)
    return series


def _net_by_attribution_day(
    transactions: list[Transaction],
    periods: list[PayPeriod],
    anchor_period_index: int,
    amount_overrides: "dict[int, Decimal]",
) -> "dict[date, Decimal]":
    """Sum each attribution day's signed projected net.

    Groups the loaded transactions by their
    :func:`~app.utils.dates.attribution_date` (``due_date`` or period start,
    clamped into the owning period) and reduces each day's group through
    :func:`~app.services.cash_ledger.sum_projected` -- the SAME
    projected-only, entry-aware, override-aware sum the period roll-forward
    uses.  Because ``sum_projected`` is additive over disjoint transaction
    groups, the per-day nets of a period sum to that period's net exactly, so
    the ramp reconciles with the grid period-end (module invariant).

    Pre-anchor period rows are skipped: the seam keeps pre-anchor days flat
    at the anchor balance, so those rows must not move the line.

    Args:
        transactions: The balance-contributing rows for ``periods`` (entries
            eager-loaded by :func:`load_balance_transactions`).
        periods: The pay periods overlapping the render range, used to map
            each transaction to its owning period for the clamp and the
            pre-anchor test.
        anchor_period_index: The anchor period's ``period_index``; rows in a
            period earlier than this are pre-anchor and skipped.
        amount_overrides: The live ``{transaction_id: Decimal}`` map threaded
            into ``sum_projected``.

    Returns:
        A ``dict`` mapping each attribution ``date`` that carries at least
        one projected flow to its signed net (income minus expense) as a
        ``Decimal``.
    """
    period_by_id = {period.id: period for period in periods}
    day_transactions: "dict[date, list[Transaction]]" = defaultdict(list)
    for txn in transactions:
        period = period_by_id.get(txn.pay_period_id)
        if period is None or period.period_index < anchor_period_index:
            continue
        day = attribution_date(
            txn.due_date, period.start_date, period.end_date,
        )
        day_transactions[day].append(txn)

    net_by_day: "dict[date, Decimal]" = {}
    for day, txns in day_transactions.items():
        income, expense = sum_projected(txns, amount_overrides)
        net_by_day[day] = income - expense
    return net_by_day


def _ramp_covered_days(
    periods: list[PayPeriod],
    net_by_day: "dict[date, Decimal]",
    seed: Decimal,
    first_day: date,
    last_day: date,
) -> "dict[date, Decimal]":
    """Walk each period day by day, accumulating the running balance.

    Starts from ``seed`` (the balance the day before the first period) and
    adds each day's net as it is crossed, so a day's balance equals the seed
    plus every flow attributed on or before it.  Only in-range days are
    emitted, but days of the first period that precede ``first_day`` are
    still walked so their flows are folded into the running balance before
    the range begins.

    Args:
        periods: The overlapping pay periods, ordered by ``period_index``
            (contiguous or gapped; each period's day span is walked once).
        net_by_day: The signed projected net per attribution day from
            :func:`_net_by_attribution_day`.
        seed: The projected balance the day before ``periods[0]`` begins.
        first_day: Inclusive first day to emit.
        last_day: Inclusive last day to emit.

    Returns:
        A ``dict`` mapping each in-range day covered by a period to its
        cent-quantized running balance.
    """
    covered: "dict[date, Decimal]" = {}
    running = seed
    for period in periods:
        day = period.start_date
        while day <= period.end_date:
            running += net_by_day.get(day, _ZERO)
            if first_day <= day <= last_day:
                covered[day] = round_money(running)
            day += timedelta(days=1)
    return covered


def _fill_in_order(
    covered: "dict[date, Decimal]",
    first_day: date,
    last_day: date,
    seed: Decimal,
) -> "OrderedDict[date, Decimal]":
    """Assemble the ordered day -> balance map, carrying forward uncovered days.

    Iterates the range in date order so the returned map is ascending.  A day
    covered by :func:`_ramp_covered_days` takes its ramped balance; an
    uncovered day (a leading day before the first period, a gap between
    periods, or a trailing day past the horizon) carries the previous day's
    balance forward, seeded by ``seed`` for any leading days.

    Args:
        covered: The in-range period-covered balances from
            :func:`_ramp_covered_days`.
        first_day: Inclusive first day of the range.
        last_day: Inclusive last day of the range.
        seed: The balance to carry over leading days before the first covered
            day (the pre-first-period balance).

    Returns:
        The complete ``OrderedDict`` day -> ``Decimal`` balance for the range.
    """
    series: "OrderedDict[date, Decimal]" = OrderedDict()
    previous = round_money(seed)
    day = first_day
    while day <= last_day:
        if day in covered:
            previous = covered[day]
        series[day] = previous
        day += timedelta(days=1)
    return series
