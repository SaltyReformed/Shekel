"""
Shekel Budget App -- Cash balance PRODUCERS (the entries-aware fold).

The two producers that answer "what is this cash account's balance" -- and
only those.  Everything they fold OVER now lives beside them rather than in
here:

  * :func:`balances_for` -- the canonical entries-aware producer (E-25;
    CRIT-01 / F-009) that grid, dashboard, and every other balance consumer
    route through, via the :mod:`app.services.balance_at` seam.
  * :func:`balance_as_of_date` -- the same projection evaluated at a calendar
    date rather than a period boundary (E-27 / HIGH-02).
  * :class:`BalanceResult` -- the frozen dataclass locking the output against
    in-place mutation.

Split at plan step D1a.  This module held three separable concerns and sat at
exactly 1000 lines, pylint's default module ceiling, because of it.  The other
two moved out to modules named for what they are, leaving the producers ready
to move INTO the seam at D1d.  At D1c both landed in the one
:mod:`app.services.cash_ledger` leaf -- the cash counterpart of
:mod:`app.services.loan_ledger` -- alongside the per-row valuation rules that
had been stranded inside ``balance_calculator`` (finding N-30):

  * ``cash_ledger._facts`` -- the FACTS a balance is folded from:
    :func:`~app.services.cash_ledger.resolve_anchor` (the dated anchor SoT,
    E-19 / CRIT-01 / F-001) with its
    :class:`~app.services.cash_ledger.AnchorPoint`, and
    :func:`~app.services.cash_ledger.load_balance_transactions`.
  * ``cash_ledger._amounts`` -- what ONE row is WORTH:
    :func:`~app.services.cash_ledger.live_amount_overrides`,
    :func:`~app.services.cash_ledger.income_amount`, and the private
    three-bucket reservation formula they build on.  This module's own date-cut
    variant of that formula is GONE: D1c made the window a parameter of the one
    rule (see :func:`~app.services.cash_ledger.sum_projected`).
  * ``cash_ledger._flows`` -- what a SET of rows SUMS TO: ``sum_projected``
    and the per-period ``period_subtotal`` / ``period_subtotals`` /
    ``PeriodSubtotal``, a peer reduction over the same rows rather than a step
    toward a balance.

The arrow runs one way: this module imports ``cash_ledger`` (it folds those
facts and reuses those per-row rules); ``cash_ledger`` imports no producer.

Background (entries-aware producer, Commit 5).  CRIT-01 / F-009: the
audit's symptom #1 ($160 on grid vs $114.29 on /savings for the same
inputs) traced to ``cash_ledger._amounts._entry_aware_amount``'s
silent-degrade short-circuit: when the consuming query did NOT
``selectinload(Transaction.entries)``, the helper returned
``txn.effective_amount`` unchanged instead of applying the entry
reduction ``max(estimated - cleared_debit - sum_credit,
uncleared_debit)``.  That made every checking-style balance a
function of an arbitrary ORM eager-loading detail in the caller's
query rather than of the underlying data.  E-25's correction:
exactly one canonical producer owns the transaction query AND
guarantees entries are loaded, so the formula is unconditionally
applied; the seam is structurally gone for callers that route
through this producer.  Commit 5 also softens the seam inside the entry-aware
rule itself (now ``cash_ledger._amounts``) so that any not-yet-routed caller
lazy-loads entries via the relationship descriptor and gets the
correct entries-aware value (rather than silently the wrong one) --
the seam removal is therefore complete at the math layer even before
Commits 6-8 finish routing the remaining consumers (savings,
accounts checking, year-end/net-worth, calendar, investment x2,
retirement) through ``balances_for``.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).
This module takes plain data, returns a frozen dataclass, never
imports ``flask``/``request``/``session``/``current_app``/
``render_template``.

Decimal discipline (``docs/coding-standards.md``).  Every returned balance is
quantized to cents via :func:`~app.utils.money.round_money`
(``ROUND_HALF_UP``), never Python's implicit ``ROUND_HALF_EVEN`` (E-26 /
HIGH-04).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import balance_calculator
from app.services.cash_ledger import (
    AnchorPoint,
    live_amount_overrides,
    load_balance_transactions,
    resolve_anchor,
    sum_projected,
)
from app.utils.money import round_money


@dataclass(frozen=True)
class BalanceResult:
    """Immutable producer output for a multi-period balance projection.

    Returned by :func:`balances_for`.  Carries the period-keyed
    projected end balance map plus the stale-anchor signal so a
    consumer that wants to surface a warning badge (OPT-6 extension
    point) has the signal already computed -- it does not need to
    re-walk the transaction set.

    Attributes:
        balances: ``OrderedDict`` mapping ``pay_period.id`` to the
            projected end balance for that period as a 2dp
            ``Decimal``.  Pre-anchor periods are absent from the map
            (the producer does not project backwards from the
            anchor); the anchor period and every period forward are
            present.  Insertion order matches the input ``periods``
            list so a caller iterating the dict sees periods in
            their natural chronological order.
        stale_anchor_warning: ``True`` when at least one settled
            transaction exists in a post-anchor period -- a hint
            that the cached anchor may not reflect recent real-bank
            activity.  Informational only; consumers may surface a
            UI badge but the ``balances`` map is the authoritative
            projection regardless.
    """

    balances: OrderedDict
    stale_anchor_warning: bool


def balances_for(
    account: Account,
    scenario_id: int,
    periods: list[PayPeriod],
    *,
    amount_overrides: dict[int, Decimal] | None = None,
) -> BalanceResult:
    """Project end balances for ``account`` across ``periods`` (E-25 SoT).

    The canonical entries-aware producer.  Resolves the anchor via
    :func:`resolve_anchor`, owns the transaction query (which always
    eager-loads ``entries``), reuses
    :func:`~app.services.balance_calculator.calculate_balances` for
    the pure carry-forward math (CLAUDE.md rule 10: do not rewrite
    the engine), and applies :func:`~app.utils.money.round_money` at
    the boundary so every returned balance is a 2dp ``Decimal`` with
    ``ROUND_HALF_UP`` -- never Python's implicit ``ROUND_HALF_EVEN``
    (E-26 / HIGH-04).

    By construction the result does NOT depend on whether the caller
    pre-loaded entries: the producer issues the query itself with the
    required ``selectinload``.  Pre-Commit-5, grid/dashboard
    pre-loaded entries (correct value) while ``/savings``,
    ``/accounts``, calendar, year-end, investment, and retirement did
    not (silently wrong value); after Commit 5, calling
    ``balances_for`` yields the entries-aware value regardless of the
    caller's query habits.  This is the structural fix for CRIT-01 /
    F-009 / symptom #1.

    Algorithm:

      1. Resolve the anchor (raises ``RuntimeError`` for the
         post-Commit-3 unreachable no-history case; never silently
         degrades).
      2. Query the account's contributing transactions for the
         scenario across the period span, with entries eager-loaded.
      3. Delegate to ``calculate_balances`` for anchor + post-anchor
         period-by-period roll-forward (the engine's
         ``sum_projected`` applies the entry-aware reduction).
      4. Quantize each balance to cents with
         :func:`~app.utils.money.round_money`.

    Args:
        account: The :class:`~app.models.account.Account` to project.
            Must be attached to ``db.session``; Commit 3 guarantees
            it has a resolvable anchor.
        scenario_id: The scenario id; used to filter transactions
            and forwarded into :func:`resolve_anchor` for symmetry
            (anchors are not scenario-scoped at the storage tier
            today; see :func:`resolve_anchor`'s docstring).
        periods: Pay periods to project over, ordered by
            ``period_index``.  Must include the anchor period (the
            engine carries the running balance forward from the
            anchor period only); pre-anchor periods in the list are
            ignored by the engine and absent from the result.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (Workstream B).  When None (the default,
            and what every single-call consumer passes), it is built
            here via :func:`live_amount_overrides` so the surface gets
            live salary income for free; the grid builds it once and
            threads it to avoid recomputing across its per-period
            subtotal calls.

    Returns:
        :class:`BalanceResult` -- the period-id -> ``Decimal`` map
        and the stale-anchor flag.  Both fields are immutable; the
        map preserves insertion order matching the input ``periods``.
    """
    anchor = resolve_anchor(account, scenario_id)
    period_ids = [p.id for p in periods]
    transactions = load_balance_transactions(account, scenario_id, period_ids)

    # Workstream B: projected salary income is recomputed live from the
    # salary profile; the stored estimated_amount is a cache.  Built here
    # when the caller did not supply one, so single-call consumers
    # (/savings, /accounts, dashboard, net worth) get live income for
    # free; the grid builds it once and threads it to avoid recomputing
    # across its per-period subtotal calls.
    if amount_overrides is None:
        amount_overrides = live_amount_overrides(
            account, scenario_id, transactions,
        )

    raw_balances, stale_anchor_warning = balance_calculator.calculate_balances(
        anchor_balance=anchor.balance,
        anchor_period_id=anchor.period.id,
        periods=periods,
        transactions=transactions,
        amount_overrides=amount_overrides,
    )

    quantized: OrderedDict[int, Decimal] = OrderedDict(
        (period_id, round_money(balance))
        for period_id, balance in raw_balances.items()
    )
    return BalanceResult(
        balances=quantized,
        stale_anchor_warning=stale_anchor_warning,
    )


def balance_as_of_date(
    account: Account,
    scenario_id: int,
    as_of: date,
) -> Decimal:
    """Project the checking balance as of a calendar date ``as_of`` (E-27).

    The canonical "balance as of date D" producer, introduced to close
    HIGH-02 / W-277: the calendar month-end "End Balance" used to walk
    a separate code path that (a) selected the last pay period whose
    ``end_date <= last_day_of_month`` -- up to ~13 days stale when the
    period straddled the month boundary -- and (b) issued a transaction
    query with no ``selectinload(Transaction.entries)``, silently
    degrading to ``effective_amount`` (the CRIT-01 / F-009 seam on a
    second surface).  Routing the calendar through this single
    producer eliminates both defects: the projection runs through the
    real period containing ``as_of`` (so balances reflect the true
    date, not a days-stale period boundary), and entries are always
    loaded by :func:`load_balance_transactions` (so the entry-aware
    reduction is unconditional).

    Algorithm:

      1. Resolve the anchor via :func:`resolve_anchor` (E-19 dated
         SoT).
      2. Load the user's pay-period set, ordered by ``period_index``.
      3. Find ``target_period`` -- the latest period whose
         ``start_date <= as_of``.  If ``as_of`` falls before the
         anchor period (i.e. requesting a balance the projection
         cannot reach), return the anchor balance (E-27's
         "pre-anchor returns anchor" convention; the producer does
         not project backward).
      4. Run :func:`~app.services.balance_calculator.calculate_balances`
         over ``[anchor_period .. target_period - 1]`` (entries
         eager-loaded via :func:`load_balance_transactions`).  The
         result is ``prior_balance`` -- the projected end balance of
         the period immediately preceding ``target_period``.  When
         ``target_period == anchor_period`` there is no prior period
         and ``prior_balance = anchor.balance``.
      5. Sum ``target_period`` with the shared
         :func:`~app.services.cash_ledger.sum_projected`, passing ``as_of``:
         entries with ``entry_date > as_of`` are excluded from the cleared /
         uncleared / credit buckets, so a purchase that has not occurred yet
         cannot reduce the reservation prematurely.
      6. Return ``round_money(prior_balance + income - expense)``.

    Cross-checks against :func:`balances_for`:

      * When ``as_of`` is exactly ``target_period.end_date`` and
        ``target_period`` contains no entries dated after that date,
        the entry-date cut is a no-op and the result equals
        ``balances_for(account, scenario_id, periods).balances[target_period.id]``
        for the same period list -- the calendar-at-period-boundary
        invariant the test ``test_calendar_equals_resolver_at_period_boundary``
        locks (C9-3).
      * When ``as_of`` falls strictly between ``target_period.start_date``
        and ``target_period.end_date`` (mid-period), the result
        equals the producer's roll-forward up to the start of
        ``target_period`` plus the period's Projected net evaluated
        with the entry-date filter -- NOT the days-stale
        ``balances_for(...).balances[<earlier-period-id>]`` the
        deleted ``_compute_month_end_balance`` returned.

    Args:
        account: The :class:`~app.models.account.Account` to project.
            Must be attached to ``db.session``; Commit 3 guarantees a
            resolvable anchor.
        scenario_id: The scenario id; filters transactions and is
            forwarded into :func:`resolve_anchor`.
        as_of: The calendar date to evaluate the balance at.  Passing
            a ``datetime`` would silently truncate at the database
            comparison; callers must pass ``date``.

    Returns:
        ``Decimal`` -- the projected balance at end-of-day ``as_of``,
        quantized to cents via :func:`~app.utils.money.round_money`.

    Raises:
        TypeError: When ``as_of`` is not a :class:`datetime.date`.
    """
    if not isinstance(as_of, date):
        raise TypeError(
            f"balance_as_of_date expects a datetime.date for as_of, "
            f"got {as_of!r}"
        )

    anchor = resolve_anchor(account, scenario_id)

    all_periods = (
        db.session.query(PayPeriod)
        .filter_by(user_id=account.user_id)
        .order_by(PayPeriod.period_index)
        .all()
    )

    # ``target_period`` is the latest period whose ``start_date`` is on
    # or before ``as_of``.  ``as_of`` may fall in a gap between two
    # periods (unusual but possible for non-contiguous pay schedules);
    # the latest started period is the right home, matching the
    # post-deletion ``_compute_month_end_balance`` semantics where the
    # projection's running balance is the balance "as of the end of
    # the most recent period that has begun."
    target_period: PayPeriod | None = None
    for period in all_periods:
        if period.start_date <= as_of:
            target_period = period
        else:
            break

    anchor_period = anchor.period
    if target_period is None or (
        target_period.period_index < anchor_period.period_index
    ):
        # ``as_of`` is before the anchor period; no forward projection
        # applies.  Return the anchor balance (rounded to cents).
        return round_money(anchor.balance)

    # Workstream B: the projected salary income and loan-payment debit are
    # recomputed live (the stored estimated_amount is only a cache).  Build
    # the override map ONCE over the union of the prefix span and the target
    # period: it is keyed by transaction id and each value depends only on
    # the transaction, not on which span it came from, so a union map is
    # equivalent to two per-span maps.  Threading the one map into both the
    # prior-balance roll-forward and the target sum makes the paycheck/loan
    # recompute behind live_amount_overrides run once per call, not once per
    # span (the grid's established build-once-and-thread pattern).
    prefix_periods = [
        p for p in all_periods
        if anchor.period.period_index <= p.period_index < target_period.period_index
    ]
    prefix_txns = load_balance_transactions(
        account, scenario_id, [p.id for p in prefix_periods],
    )
    target_txns = load_balance_transactions(
        account, scenario_id, [target_period.id],
    )
    amount_overrides = live_amount_overrides(
        account, scenario_id, prefix_txns + target_txns,
    )

    prior_balance = _project_to_period_before(
        anchor, target_period, prefix_periods, prefix_txns, amount_overrides,
    )
    income, expense = sum_projected(target_txns, amount_overrides, as_of=as_of)

    return round_money(prior_balance + income - expense)


def _project_to_period_before(
    anchor: AnchorPoint,
    target_period: PayPeriod,
    prefix_periods: list[PayPeriod],
    prefix_txns: list[Transaction],
    amount_overrides: dict[int, Decimal],
) -> Decimal:
    """Return the projected end balance of the period before ``target_period``.

    When ``target_period`` is the anchor period itself the prior
    balance is simply ``anchor.balance`` (the engine starts here).
    Otherwise walk
    :func:`~app.services.balance_calculator.calculate_balances` over
    ``prefix_periods`` (the span ``[anchor_period .. target_period - 1]``,
    with ``prefix_txns`` entries eager-loaded) and return the engine's end
    balance for the period immediately before ``target_period``.

    The caller (:func:`balance_as_of_date`) loads ``prefix_periods`` /
    ``prefix_txns`` and builds ``amount_overrides`` once over the union of
    the prefix and target spans, then threads that single map here and into
    the as-of sum -- so the live salary/loan recompute behind
    :func:`live_amount_overrides` runs once per call, not once per span.

    Args:
        anchor: The resolved :class:`AnchorPoint`; ``anchor.balance``
            seeds the roll-forward and ``anchor.period`` is the engine's
            starting period.
        target_period: The period whose immediately-preceding end balance
            is wanted.  When it equals the anchor period there is no prior
            period and ``anchor.balance`` is returned unchanged.
        prefix_periods: The span ``[anchor_period .. target_period - 1]``
            ordered by ``period_index``; empty only in the anchor-period
            early-return case.
        prefix_txns: The contributing transactions for ``prefix_periods``,
            entries eager-loaded.
        amount_overrides: The shared ``{transaction_id: Decimal}`` live
            override map.  Keys outside ``prefix_txns`` are never looked up
            by the engine, so passing the caller's union map is equivalent
            to a prefix-only map.

    Returns:
        ``Decimal`` -- the projected end balance of the period before
        ``target_period`` (or ``anchor.balance`` when ``target_period`` is
        the anchor period).
    """
    if target_period.id == anchor.period.id:
        return anchor.balance

    raw_balances, _ = balance_calculator.calculate_balances(
        anchor_balance=anchor.balance,
        anchor_period_id=anchor.period.id,
        periods=prefix_periods,
        transactions=prefix_txns,
        amount_overrides=amount_overrides,
    )
    # The prefix walk always produces an end-balance for its last
    # period: it starts at the anchor period (so ``running_balance``
    # is set on iteration 1) and runs through ``prefix_periods[-1]``.
    return raw_balances[prefix_periods[-1].id]
