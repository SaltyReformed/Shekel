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
from app.utils.balance_predicates import balance_contributing_clause
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


def balances_for(
    account: Account,
    scenario_id: int,
    periods: list[PayPeriod],
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

    Returns:
        :class:`BalanceResult` -- the period-id -> ``Decimal`` map
        and the stale-anchor flag.  Both fields are immutable; the
        map preserves insertion order matching the input ``periods``.
    """
    anchor = resolve_anchor(account, scenario_id)
    period_ids = [p.id for p in periods]
    transactions = _load_balance_transactions(account, scenario_id, period_ids)

    raw_balances, stale_anchor_warning = balance_calculator.calculate_balances(
        anchor_balance=anchor.balance,
        anchor_period_id=anchor.period.id,
        periods=periods,
        transactions=transactions,
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

    Returns:
        :class:`PeriodSubtotal` -- the three Decimals (income,
        expense, net), each quantized to cents via
        :func:`~app.utils.money.round_money`.
    """
    transactions = _load_balance_transactions(
        account, scenario_id, [period.id],
    )
    # pylint: disable=protected-access
    # ``_sum_all`` is an internal helper of ``balance_calculator``;
    # the resolver is its sibling canonical producer (see module
    # docstring) and the audit's E-25 mandate explicitly reuses the
    # engine's math rather than rewriting it (CLAUDE.md rule 10).
    income, expense = balance_calculator._sum_all(transactions)
    rounded_income = round_money(income)
    rounded_expense = round_money(expense)
    return PeriodSubtotal(
        income=rounded_income,
        expense=rounded_expense,
        net=rounded_income - rounded_expense,
    )
