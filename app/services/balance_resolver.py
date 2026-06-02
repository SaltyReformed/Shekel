"""
Shekel Budget App -- Balance Resolver (Anchor + entries-aware producer)

Single-source-of-truth resolver for "what is this account's anchor"
(governing intent E-19; finding CRIT-01 / F-001) plus the canonical
entries-aware balance/subtotal producer (E-25; CRIT-01 / F-009).  This
module exposes:

  * :func:`resolve_anchor` -- Commit 4, the dated anchor SoT.
  * :func:`balances_for` and :func:`period_subtotal` -- Commit 5, the
    canonical entries-aware producer that grid, dashboard, and
    (post-Commits 6-8) every other balance consumer route through.
  * :class:`AnchorPoint`, :class:`BalanceResult`,
    :class:`PeriodSubtotal` -- frozen dataclasses that lock the
    producer's outputs against in-place mutation.

Background (anchor section, Commit 4).  Before the remediation, five
balance producers (grid, ``/accounts``, ``/savings``, dashboard,
year-end / net-worth) each forked four different ways for the
NULL-anchor case and additionally each read the anchor by a slightly
different recipe.  Commit 3 (migration ``cfb15e782f86`` plus the
canonical ``account_service.create_account`` factory) made the NULL
state unreachable at the storage tier.  Commit 4 (this file) makes
the "which anchor is current" question answered in one place at the
service tier, reading the dated source of truth -- the latest
``AccountAnchorHistory`` row -- rather than the
``Account.current_anchor_*`` columns directly.  Those columns are
treated as a denormalized cache of the latest history row; if they
ever disagree (a future regression that updates the cache without
appending the matching event), the resolver wins from the history
row and emits ``EVT_ANCHOR_CACHE_RECONCILED`` so the divergence is
detectable in observability rather than silently shipping a wrong
balance.

Background (entries-aware producer, Commit 5).  CRIT-01 / F-009: the
audit's symptom #1 ($160 on grid vs $114.29 on /savings for the same
inputs) traced to ``balance_calculator._entry_aware_amount``'s
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
through this producer.  Commit 5 also softens the seam inside
``balance_calculator`` itself so that any not-yet-routed caller
lazy-loads entries via the relationship descriptor and gets the
correct entries-aware value (rather than silently the wrong one) --
the seam removal is therefore complete at the math layer even before
Commits 6-8 finish routing the remaining consumers (savings,
accounts checking, year-end/net-worth, calendar, investment x2,
retirement) through ``balances_for``.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).
This module takes plain data, returns a frozen dataclass, never
imports ``flask``/``request``/``session``/``current_app``/
``render_template``.  ``log_event`` is from ``app.utils.log_events``,
which is the project's Flask-free structured-logging helper.

Decimal discipline (``docs/coding-standards.md``).  The returned
``AnchorPoint.balance`` is constructed via ``Decimal(str(...))`` from
the storage value.  ``Account.current_anchor_balance`` and
``AccountAnchorHistory.anchor_balance`` are ``Numeric(12,2)`` columns,
so the SQLAlchemy adapter already returns ``Decimal`` -- but routing
through ``str`` is the project convention and is the cheap insurance
against a future column-type change silently coercing through float.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import balance_calculator
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_ANCHOR_CACHE_RECONCILED,
    log_event,
)
from app.utils.money import round_money


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnchorPoint:
    """Immutable date-anchored anchor (E-19 single source of truth).

    Attributes:
        balance: The real-money anchor balance as a ``Decimal``.  Zero
            is a legitimate value per E-12 and is preserved verbatim;
            consumers MUST NOT treat ``Decimal("0.00")`` as "missing".
        period: The :class:`~app.models.pay_period.PayPeriod` the
            anchor is anchored against.  The pay-period row is
            authoritative; the resolver returns the relationship-loaded
            object so callers can read ``period.id``, ``period.start_date``,
            ``period.end_date``, etc. without re-querying.
        as_of_date: The UTC calendar date the anchor event was created
            (``AccountAnchorHistory.created_at`` truncated to a UTC
            day).  UTC is chosen for consistency with the
            ``uq_anchor_history_account_period_balance_day`` partial
            unique index (see ``app/models/account.py``), which
            truncates the same column at UTC.
    """

    balance: Decimal
    period: PayPeriod
    as_of_date: date


def resolve_anchor(account: Account, scenario_id: int) -> AnchorPoint:
    """Return the canonical :class:`AnchorPoint` for ``account``.

    Reads the most recent ``AccountAnchorHistory`` row for the account
    (by ``created_at`` descending) as the dated source of truth (E-19).
    ``Account.current_anchor_balance`` and
    ``Account.current_anchor_period_id`` are treated as a denormalized
    cache of that latest event:

      * Cache matches latest event -- the canonical state.  Return the
        history row's values.
      * Cache disagrees -- the history row wins (E-19 dated SoT) and
        the divergence is logged via
        ``EVT_ANCHOR_CACHE_RECONCILED`` so the regression that wrote
        the cache without appending the matching event is detectable
        in observability.  The cache is NOT mutated here -- this
        resolver is read-only; correcting the cache is the
        responsibility of the next legitimate true-up path.

    The Decimal balance is constructed via ``Decimal(str(...))`` to
    obey the project's "construct Decimal from strings" rule
    (``docs/coding-standards.md``) even though the storage column is
    already ``Numeric(12,2)``.

    Never returns ``None``: Commit 3 (migration ``cfb15e782f86`` plus
    the canonical ``account_service.create_account`` factory)
    guarantees every account row has a matching origination history
    row from the moment it exists, so the latest-row query always
    succeeds.  The defensive ``RuntimeError`` exists so that a future
    regression -- e.g. a code path that bypasses the factory by
    calling ``db.session.add(Account(...))`` directly -- fails loudly
    here rather than silently returning a wrong number to every
    downstream consumer.

    Args:
        account: The :class:`~app.models.account.Account` to resolve.
            Must be attached to ``db.session`` (the history-row query
            reads via the session).
        scenario_id: The scenario the caller is operating under.  The
            current data model is per-account -- ``AccountAnchorHistory``
            carries no ``scenario_id`` column and accounts are not
            scenario-scoped at the storage tier -- so the anchor
            returned is identical across scenarios for the same
            account.  The parameter is kept in the signature for two
            reasons: API symmetry with the post-Commit-5
            ``balances_for(account, scenario_id, periods)`` producer
            that does need scenario for transaction filtering, and
            forward compatibility with a possible future per-scenario
            anchor override.  The value is included in the
            reconciliation log payload so a future scenario-scoped
            divergence is traceable.

    Returns:
        :class:`AnchorPoint` -- balance, period, and as-of-date.

    Raises:
        RuntimeError: When no ``AccountAnchorHistory`` row exists for
            the account.  Unreachable in production after Commit 3;
            see the function docstring above for the regression-trap
            rationale.
    """
    latest: AccountAnchorHistory | None = (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.created_at.desc())
        .first()
    )
    if latest is None:
        raise RuntimeError(
            f"resolve_anchor: account id={account.id} has zero "
            "AccountAnchorHistory rows.  Commit 3 (migration "
            "cfb15e782f86 plus account_service.create_account) makes "
            "this state unreachable; investigate any code path that "
            "constructed the Account row without routing through the "
            "canonical factory."
        )

    history_balance = Decimal(str(latest.anchor_balance))
    history_period_id = latest.pay_period_id

    cached_balance = account.current_anchor_balance
    cached_period_id = account.current_anchor_period_id
    cache_disagrees = (
        cached_balance is None
        or Decimal(str(cached_balance)) != history_balance
        or cached_period_id != history_period_id
    )
    if cache_disagrees:
        log_event(
            logger,
            logging.WARNING,
            EVT_ANCHOR_CACHE_RECONCILED,
            BUSINESS,
            "Account.current_anchor_* cache disagreed with the latest "
            "AccountAnchorHistory row; history row wins (E-19 SoT).",
            account_id=account.id,
            scenario_id=scenario_id,
            cached_balance=(
                str(cached_balance) if cached_balance is not None else None
            ),
            cached_period_id=cached_period_id,
            history_balance=str(history_balance),
            history_period_id=history_period_id,
            history_created_at=latest.created_at.isoformat(),
        )

    # ``created_at`` is TIMESTAMPTZ NOT NULL with a server default of
    # NOW() (see ``CreatedAtMixin``), so it is always populated and
    # always timezone-aware.  Convert to UTC before truncating to a
    # date so the resolver's as-of-date matches the
    # ``uq_anchor_history_account_period_balance_day`` index's UTC-day
    # bucket exactly.
    as_of_date = latest.created_at.astimezone(timezone.utc).date()

    return AnchorPoint(
        balance=history_balance,
        period=latest.pay_period,
        as_of_date=as_of_date,
    )


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


@dataclass(frozen=True)
class PeriodSubtotal:
    """Immutable producer output for one period's entries-aware subtotal.

    Returned by :func:`period_subtotal`.  All three Decimal fields use
    the same entries-aware reduction the balance calculator applies,
    so by construction
    ``balances[p] - balances[p-1] == period_subtotal(..., p).net`` --
    the same-page same-formula property F-002 Pair C / F-004 break
    and E-25 restore.

    Attributes:
        income: Sum of Projected income transactions in the period.
            Income uses :attr:`Transaction.effective_amount` (entries
            do not apply to income -- they live on expenses only).
        expense: Sum of Projected expense transactions, each reduced
            by the entries-aware formula
            ``max(estimated - cleared_debit - sum_credit,
            uncleared_debit)`` when the transaction carries entries.
            For an expense with no entries this collapses to
            ``effective_amount``, which matches the no-entries
            consumer behavior pre-Commit-5 (regression-safe for
            grid/dashboard whose pinned tests stay byte-identical).
        net: ``income - expense``.  Returned pre-computed so a
            consumer never has to re-derive it (and risk a divergent
            sign or rounding mode).
    """

    income: Decimal
    expense: Decimal
    net: Decimal


def _load_balance_transactions(
    account: Account,
    scenario_id: int,
    period_ids: list[int],
) -> list[Transaction]:
    """Return the transactions that participate in a balance projection.

    The single point where the producer's query lives.  Filters:

      * ``account_id`` -- balance is per-account; never mix accounts.
      * ``scenario_id`` -- balance is per-scenario.
      * ``pay_period_id IN period_ids`` -- only the periods the caller
        is projecting over.
      * :func:`balance_contributing_clause` -- the centralized
        ``is_deleted = FALSE AND status_id NOT IN (Credit, Cancelled)``
        gate from ``app.utils.balance_predicates`` (E-15, Commit 2).
        Using the shared clause here means the SQL filter and the
        Python summation predicate cannot disagree.

    The query MUST eager-load ``Transaction.entries`` so the
    entries-aware reduction in
    :func:`~app.services.balance_calculator._entry_aware_amount`
    applies unconditionally.  This is the structural fix for CRIT-01
    / F-009: by owning the query, the producer guarantees the
    selectinload that pre-remediation consumers each had to remember
    to add themselves (and ``/savings``, ``/accounts``, calendar,
    year-end, investment, and retirement collectively forgot to).
    ``Transaction.status`` is already ``lazy='joined'`` on the model
    so the joined load suffices; no extra ``selectinload(status)``
    is needed and adding one would emit a redundant SELECT.

    Args:
        account: The :class:`~app.models.account.Account` to project.
            Must be attached to ``db.session``.
        scenario_id: The scenario the balance is being projected
            under.
        period_ids: Pay period ids the projection covers.  An empty
            list yields an empty result (the empty-projection case;
            the caller is expected to handle that upstream).

    Returns:
        ``list[Transaction]`` with ``entries`` eagerly populated.
    """
    if not period_ids:
        return []
    return (
        db.session.query(Transaction)
        .options(selectinload(Transaction.entries))
        .filter(
            Transaction.account_id == account.id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            balance_contributing_clause(),
        )
        .all()
    )


def live_amount_overrides(account, scenario_id, transactions):
    """Build the live per-transaction amount-override map for ``transactions``.

    Merges two read-time live-recompute seams, both keyed by transaction
    id, both treating the stored amount as a cache a later profile,
    calibration, escrow/rate, or financial-calc CODE change may have
    invalidated without firing a regeneration:

    * :func:`app.services.income_service.live_projected_net` -- projected
      salary income reflects the current salary profile.
    * :func:`app.services.loan_payment_service.live_loan_transfer_amounts`
      -- a recurring loan-payment transfer's cash debit reflects the
      loan's current monthly payment (P&I + escrow).

    The two key sets are disjoint (salary income transactions vs
    loan-payment transfer shadows), so the merge cannot collide.  Both
    helpers are imported locally to keep their (paycheck/tax and
    loan-resolver) stacks off ``balance_resolver``'s module-load path and
    out of any import cycle.  Returns an empty dict when neither seam has
    a candidate -- the common case -- so the override threading stays a
    structural no-op for those surfaces.
    """
    # pylint: disable=import-outside-toplevel
    from app.services import income_service, loan_payment_service
    income_overrides = income_service.live_projected_net(
        account.user_id, scenario_id, transactions,
    )
    loan_overrides = loan_payment_service.live_loan_transfer_amounts(
        scenario_id, transactions,
    )
    return {**income_overrides, **loan_overrides}


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
         ``_sum_remaining`` / ``_sum_all`` apply the entry-aware
         reduction).
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
    transactions = _load_balance_transactions(account, scenario_id, period_ids)

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


def period_subtotal(
    account: Account,
    scenario_id: int,
    period: PayPeriod,
    *,
    amount_overrides: dict[int, Decimal] | None = None,
) -> PeriodSubtotal:
    """Entries-aware income / expense / net subtotal for one period (E-25).

    The single source of truth for "what is the projected net change
    in checking for this period."  The grid's footer subtotal, the
    obligations summary, and any future per-period roll-up must all
    consume this -- the F-002 Pair C / F-004 same-page divergence
    (the grid currently has an inline ``sum(... effective_amount
    ...)`` loop while the balance row uses the entries-aware
    reduction) closes when Commit 10 routes those callers through
    this function.  By construction
    ``balances_for(account, scenario_id, periods).balances[p]
    - balances_for(...).balances[prev]
    == period_subtotal(account, scenario_id, p).net``.

    Algorithm: re-uses :func:`_load_balance_transactions` (one query,
    entries eager-loaded, shared status clause) then delegates to
    :func:`~app.services.balance_calculator._sum_all`, whose math is
    identical to ``_sum_remaining`` post-Commit-5 (both gate on
    Projected, both apply the entry-aware reduction for expenses,
    both use ``effective_amount`` for income).  Calling ``_sum_all``
    directly avoids the carry-forward bookkeeping in
    ``calculate_balances`` that is irrelevant when only one period
    is needed.

    Args:
        account: The :class:`~app.models.account.Account`.  Must be
            attached to ``db.session``.
        scenario_id: The scenario id.
        period: The :class:`~app.models.pay_period.PayPeriod` to
            sum.  Can be the anchor period or any post-anchor period
            -- the same Projected-only entries-aware sum applies.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (Workstream B); built here when None so
            the subtotal's income line reflects the live paycheck
            consistently with the balance row.

    Returns:
        :class:`PeriodSubtotal` -- the three Decimals (income,
        expense, net), each quantized to cents via
        :func:`~app.utils.money.round_money`.
    """
    transactions = _load_balance_transactions(
        account, scenario_id, [period.id],
    )
    # Workstream B: live projected income (built here when not supplied,
    # so the same Projected-only, entries-aware subtotal the balance row
    # uses also reflects the live paycheck -- keeping the F-002 Pair C
    # invariant ``balances[p] - balances[p-1] == subtotal.net`` true).
    if amount_overrides is None:
        amount_overrides = live_amount_overrides(
            account, scenario_id, transactions,
        )
    # pylint: disable=protected-access
    # ``_sum_all`` is an internal helper of ``balance_calculator``;
    # the resolver is its sibling canonical producer (see module
    # docstring) and the audit's E-25 mandate explicitly reuses the
    # engine's math rather than rewriting it (CLAUDE.md rule 10).
    income, expense = balance_calculator._sum_all(transactions, amount_overrides)
    rounded_income = round_money(income)
    rounded_expense = round_money(expense)
    return PeriodSubtotal(
        income=rounded_income,
        expense=rounded_expense,
        net=rounded_income - rounded_expense,
    )


def _entry_aware_amount_dated(txn: Transaction, as_of: date) -> Decimal:
    """Date-cut variant of the balance-calculator entry-aware reduction (E-27).

    Mirrors :func:`~app.services.balance_calculator._entry_aware_amount`
    bucket-by-bucket but considers only entries whose ``entry_date`` is
    on or before ``as_of``.  A purchase that has not happened yet
    (entry dated after ``as_of``) cannot have cleared the bank as of
    that date and therefore must not contribute to either bucket --
    inclusion would reduce the reservation prematurely and ship a
    wrong balance for the calendar month-end (HIGH-02 / W-277).

    The formula is otherwise identical to the engine helper:

        cleared_debit   = sum(e.amount where
                              not is_credit and is_cleared
                              and entry_date <= as_of)
        uncleared_debit = sum(e.amount where
                              not is_credit and not is_cleared
                              and entry_date <= as_of)
        sum_credit      = sum(e.amount where
                              is_credit and entry_date <= as_of)

        impact = max(estimated_amount - cleared_debit - sum_credit, uncleared_debit)

    Non-Projected transactions short-circuit to ``effective_amount``
    (same as the engine helper) because Settled/Cancelled/Credit are
    already handled correctly by that property: Settled returns
    actual_amount (the realized hit, by definition dated on or before
    settlement), and Cancelled/Credit return Decimal("0").

    Args:
        txn: The :class:`~app.models.transaction.Transaction` to size.
            ``entries`` must be loaded (the canonical producer always
            ``selectinload``s them; lazy-load is a safe fallback).
        as_of: The calendar date that bounds entry inclusion.  Entries
            with ``entry_date > as_of`` are excluded.

    Returns:
        ``Decimal`` -- the entries-aware checking impact at ``as_of``.
    """
    entries = getattr(txn, "entries", ())
    # Non-Projected statuses short-circuit through ``effective_amount``
    # (Settled returns ``actual_amount``; Cancelled / Credit return zero
    # via ``excludes_from_balance``).  Routed through the centralized
    # ``is_projected`` predicate (D6-09 / MED-02) so this entry-formula
    # gate shares one definition with the engine helper and the
    # ``_sum_*`` loops in ``balance_calculator``.
    if not is_projected(txn):
        return txn.effective_amount

    cleared_debit = Decimal("0")
    uncleared_debit = Decimal("0")
    sum_credit = Decimal("0")
    any_in_window = False
    for entry in entries:
        if entry.entry_date > as_of:
            continue
        any_in_window = True
        if entry.is_credit:
            sum_credit += entry.amount
        elif entry.is_cleared:
            cleared_debit += entry.amount
        else:
            uncleared_debit += entry.amount

    if not any_in_window:
        # No purchase has occurred yet as of ``as_of``; the full
        # estimated reservation is still pending.  ``effective_amount``
        # collapses to estimated for an unfilled Projected expense
        # (actual_amount is unset until the transaction settles), so
        # this matches the engine helper's empty-entries branch.
        return txn.effective_amount

    return max(
        txn.estimated_amount - cleared_debit - sum_credit,
        uncleared_debit,
    )


def _sum_period_as_of(
    transactions: list[Transaction],
    as_of: date,
    amount_overrides: dict[int, Decimal] | None = None,
) -> tuple[Decimal, Decimal]:
    """Sum Projected income / expense for the as-of period (E-27).

    Mirrors :func:`~app.services.balance_calculator._sum_all` but
    routes expense impact through :func:`_entry_aware_amount_dated`
    so the entry-date cut applies inside the period containing
    ``as_of``.  Income uses the live projected-net override when present
    (Workstream B), else ``effective_amount`` -- income transactions do
    not carry entries (entries live on expense envelopes), so the
    entry-date cut is a no-op for income either way.

    Transactions are NOT filtered by ``due_date`` here.  ``balance
    as of date D`` is the projected balance once the period
    containing D has rolled forward; the date-sensitivity lives in
    the per-entry reduction, not in transaction inclusion (that is
    what the plan's "within the period containing as_of apply
    entry-aware reduction only for entries dated on/before as_of"
    specifies, and matches the calendar-surface UX where the
    "End Balance" reflects the period's full settled+projected
    delta but does not undo a not-yet-occurred purchase).

    Args:
        transactions: The Projected-gated, entries-loaded transaction
            list for the period containing ``as_of``.
        as_of: The calendar date that bounds entry inclusion.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (Workstream B); the income line uses it
            via :func:`~app.services.balance_calculator._income_amount`.

    Returns:
        ``(income, expense)`` as a ``Decimal`` tuple, both unquantized.
    """
    income = Decimal("0.00")
    expense = Decimal("0.00")
    for txn in transactions:
        # Centralized ``is_projected`` predicate (D6-09 / MED-02);
        # mirrors ``balance_calculator._sum_all`` exactly so the
        # date-cut path classifies non-Projected rows identically.
        if not is_projected(txn):
            continue
        if txn.is_income:
            # Workstream B live projected-net seam; reuse the engine
            # helper so the date-cut path and ``_sum_all`` cannot drift.
            # pylint: disable=protected-access
            income += balance_calculator._income_amount(txn, amount_overrides)
        elif txn.is_expense:
            # The live-derive seam applies to the expense leg too (e.g. a
            # derive-from-loan transfer's checking debit); fall back to
            # the date-cut entry-aware amount when no override applies.
            override = (
                amount_overrides.get(txn.id) if amount_overrides else None
            )
            expense += (
                override if override is not None
                else _entry_aware_amount_dated(txn, as_of)
            )
    return income, expense


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
    loaded by :func:`_load_balance_transactions` (so the entry-aware
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
         eager-loaded via :func:`_load_balance_transactions`).  The
         result is ``prior_balance`` -- the projected end balance of
         the period immediately preceding ``target_period``.  When
         ``target_period == anchor_period`` there is no prior period
         and ``prior_balance = anchor.balance``.
      5. Sum ``target_period`` with :func:`_sum_period_as_of`, which
         routes the entry-aware reduction through
         :func:`_entry_aware_amount_dated`: entries with
         ``entry_date > as_of`` are excluded from the cleared /
         uncleared / credit buckets, so a purchase that has not
         occurred yet cannot reduce the reservation prematurely.
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

    prior_balance = _project_to_period_before(
        account, scenario_id, anchor, target_period, all_periods,
    )

    target_txns = _load_balance_transactions(
        account, scenario_id, [target_period.id],
    )
    # Workstream B: live projected income for the target period.
    amount_overrides = live_amount_overrides(
        account, scenario_id, target_txns,
    )
    income, expense = _sum_period_as_of(target_txns, as_of, amount_overrides)

    return round_money(prior_balance + income - expense)


def _project_to_period_before(
    account: Account,
    scenario_id: int,
    anchor: AnchorPoint,
    target_period: PayPeriod,
    all_periods: list[PayPeriod],
) -> Decimal:
    """Return the projected end balance of the period before ``target_period``.

    When ``target_period`` is the anchor period itself the prior
    balance is simply ``anchor.balance`` (the engine starts here).
    Otherwise walk
    :func:`~app.services.balance_calculator.calculate_balances` over
    ``[anchor_period .. target_period - 1]`` with entries
    eager-loaded by :func:`_load_balance_transactions`, and return
    the engine's end balance for the period immediately before
    ``target_period``.  Used by :func:`balance_as_of_date` to seed
    the entry-date-cut sum of the target period.
    """
    anchor_period = anchor.period
    if target_period.id == anchor_period.id:
        return anchor.balance

    prefix_periods = [
        p for p in all_periods
        if anchor_period.period_index <= p.period_index < target_period.period_index
    ]
    prefix_txns = _load_balance_transactions(
        account, scenario_id, [p.id for p in prefix_periods],
    )
    # Workstream B: live projected income for the prefix span, so the
    # calendar's prior_balance reflects the live paycheck.
    amount_overrides = live_amount_overrides(
        account, scenario_id, prefix_txns,
    )
    raw_balances, _ = balance_calculator.calculate_balances(
        anchor_balance=anchor.balance,
        anchor_period_id=anchor_period.id,
        periods=prefix_periods,
        transactions=prefix_txns,
        amount_overrides=amount_overrides,
    )
    # The prefix walk always produces an end-balance for its last
    # period: it starts at the anchor period (so ``running_balance``
    # is set on iteration 1) and runs through ``prefix_periods[-1]``.
    return raw_balances[prefix_periods[-1].id]
