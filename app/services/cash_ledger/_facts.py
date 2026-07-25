"""
Shekel Budget App -- Cash ledger: the FACTS (what happened to this account).

The stored, user-asserted, or recorded events a cash balance is folded from,
and nothing that folds them:

  * :class:`AnchorPoint` / :func:`resolve_anchor` -- the user's balance
    ASSERTION, read from the dated ``AccountAnchorHistory`` source of truth
    (E-19).  A stored fact, not a computed projection.
  * :func:`load_balance_transactions` -- the account's balance-contributing
    transaction rows over a period window, with ``entries`` eager-loaded.
  * :func:`planned_cash_rows` -- the account's still-PROJECTED
    balance-contributing rows, unwindowed: the PLAN, which the seam's cash fold
    dates and values (ruling R-G) because a plan's effective date depends on the
    reader's as-of and this leaf reads no clock.

The loan analog is :mod:`app.services.loan_ledger._events`: both answer "what
happened, and in what order", never "what is the balance at T" -- which is the
:mod:`app.services.balance_at` seam's question.  What one of these rows is
WORTH lives beside this in :mod:`._amounts`; what a SET of them sums to lives
in :mod:`._flows`.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclass out; no ``flask`` / ``request`` / ``session`` /
``current_app`` / ``render_template`` import.  ``log_event`` is from
``app.utils.log_events``, the project's Flask-free structured-logging helper.

Decimal discipline (``docs/coding-standards.md``).  :attr:`AnchorPoint.balance`
is constructed via ``Decimal(str(...))`` from the storage value.
``Account.current_anchor_balance`` and ``AccountAnchorHistory.anchor_balance``
are ``Numeric(12,2)`` columns, so the SQLAlchemy adapter already returns
``Decimal`` -- but routing through ``str`` is the project convention and is the
cheap insurance against a future column-type change silently coercing through
float.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_ANCHOR_CACHE_RECONCILED,
    log_event,
)


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
            truncates the same column at UTC.  A canonical UTC identity,
            not a display value (convert ``created_at`` instead).
        created_at: The anchor event's stored UTC instant, carried so a
            reader renders the anchor "as of" in the DISPLAY timezone via
            ``local_datetime`` (not the UTC-day ``as_of_date``).
    """

    balance: Decimal
    period: PayPeriod
    as_of_date: date
    created_at: datetime


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
            reasons: API symmetry with the
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
        created_at=latest.created_at,
    )


def load_balance_transactions(
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
    :func:`app.services.cash_ledger._amounts._entry_aware_amount`
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


def planned_cash_rows(
    account_id: int, scenario_id: int,
) -> list[Transaction]:
    """Return an account's still-PROJECTED balance-contributing rows.

    The PLAN half of the cash event stream, and the exact structural twin of
    :func:`app.services.cash_ledger.settled_cash_facts` beside it: same account /
    scenario scope, same shared eligibility gate, same eager loads, same absence
    of a period window.  The two differ in their status narrowing (settled there,
    Projected here) and in what they RETURN, and that second difference is the
    ruling:

    * a SETTLED row can be dated by this leaf -- its instant is
      ``COALESCE(paid_at, period start)``, a stored fact -- so ``_events`` returns
      it valued and dated, as a
      :class:`~app.services.cash_ledger.CashSourceFact`;
    * a PROJECTED row cannot.  Its effective date is
      ``max(its attribution date, as_of + 1 day)`` (ruling R-G: "a plan cannot
      have already happened"), which is a function of the READER's as-of -- and
      this package reads no clock, deliberately (a walk that read one made the
      posted ledger a function of when the sync happened to run, the corruption
      shape plan step A3 removed from the loan side).

    So this returns the rows THEMSELVES and the seam's cash fold owns the dating
    and the valuation, exactly as the loan plan's PLANNED tier lives in
    ``balance_at._plan`` rather than in ``loan_ledger`` (plan step C6a's ruling,
    restated for cash).  That is also why it is a plain loader and not a
    ``CashPlannedFact``: a fact type here would have to carry either no date (a
    dataclass earning nothing over the row) or a clock-derived one (the thing the
    ruling forbids).

    **It takes no period window, for the same reason its settled twin does not.**
    An argument a caller can get wrong is a defect, not a contract (plan
    Section 8): the loan fold once TOOK the period list its visibility rule
    needed, and the grid passing a WINDOW moved a balance by $150,000.00 (plan
    step B1).  A fold over a windowed plan is a fold over a different account.

    The eligibility gate is the shared
    :func:`~app.utils.balance_predicates.balance_contributing_clause`
    (``is_deleted = FALSE AND status_id NOT IN (Credit, Cancelled)``) composed
    with :func:`~app.utils.balance_predicates.is_projected_clause` -- the SQL form
    of the very ``is_projected`` predicate
    :func:`~app.services.cash_ledger.sum_projected` re-applies when it values
    these rows, so the loader and the reduction cannot disagree about which rows
    are in the plan.  The status pair is redundant by construction (Projected is
    neither Credit nor Cancelled) and composed anyway, so this loader and its
    settled twin state "which rows exist at all" through one shared clause rather
    than two hand-written filters.

    Args:
        account_id: The account whose plan to load.
        scenario_id: The budget scenario the rows live in.

    Returns:
        ``list[Transaction]`` -- every still-Projected contributing row for the
        account in the scenario, unordered (the fold groups them by day), with
        ``entries`` and ``pay_period`` populated.
    """
    return _unwindowed_contributing_rows(
        account_id, scenario_id, is_projected_clause(Transaction),
    )


def _unwindowed_contributing_rows(
    account_id: int, scenario_id: int, status_clause,
) -> list[Transaction]:
    """Return an account's contributing rows in one scenario, narrowed by status.

    The ONE unwindowed row load behind both halves of the cash event stream --
    :func:`planned_cash_rows` above (still-Projected) and
    :func:`app.services.cash_ledger.settled_cash_facts` (settled).  The two halves
    partition the contributing set exactly: ``balance_contributing_clause``
    admits Projected, Paid, Received and Settled, and the two callers narrow to
    the first and the last three respectively.

    Extracted when the second half was written and ``duplicate-code`` reported
    the eight shared lines.  PRIVATE, and imported across sibling modules exactly
    as ``_amounts._expense_amount`` already is: it is an implementation detail of
    the two loaders, not a leaf surface a consumer should reach -- which is also
    what keeps it out of the W9909 registry, structure doing what a fence entry
    would otherwise have to.  Sharing it is not tidiness: the account / scenario
    scope, the contributing gate, and BOTH eager loads are individually
    load-bearing (a missing ``selectinload(entries)`` is the seam that shipped two
    different balances for one row in CRIT-01 / F-009, and the fold reads
    ``pay_period`` for its attribution clamp), so a second hand-written copy is
    exactly where one of them would go missing on one half only.

    **The status narrowing is a clause PARAMETER, and stays in SQL.**  Loading the
    whole contributing set and partitioning in Python would be one query instead
    of two, and is rejected: the Projected half is roughly two years of forward
    projection, so a post-filter would eager-load entries for the whole horizon to
    keep the ~130 settled rows the walk wants -- and the settled half has a
    consumer (the walk, and at plan step X-d the posting writer) that never wants
    the plan at all.

    Args:
        account_id: The account whose rows to load.
        scenario_id: The budget scenario the rows live in.
        status_clause: The caller's status narrowing as a SQLAlchemy boolean
            expression over ``Transaction`` -- one of the shared builders in
            :mod:`app.utils.balance_predicates`
            (:func:`~app.utils.balance_predicates.is_projected_clause` for the
            plan, ``status_id.in_(settled_status_ids())`` for the settled half),
            never a literal written at the call site.

    Returns:
        ``list[Transaction]`` -- the matching rows, unordered, with ``entries``
        and ``pay_period`` populated.
    """
    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.pay_period),
            selectinload(Transaction.entries),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            status_clause,
            balance_contributing_clause(),
        )
        .all()
    )
