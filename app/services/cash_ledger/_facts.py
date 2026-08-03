"""
Shekel Budget App -- Cash ledger: the FACTS (what happened to this account).

The stored, user-asserted, or recorded events a cash balance is folded from,
and nothing that folds them:

  * :class:`AnchorPoint` / :func:`resolve_anchor` -- the user's balance
    ASSERTION, read from the dated ``AccountAnchorHistory`` source of truth
    (E-19).  A stored fact, not a computed projection.
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
from datetime import date, datetime
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

from ._days import ObservedOn, ReconciledThrough


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
        observed_on: The civil day the asserted balance was TRUE -- the
            stored ``AccountAnchorHistory.observed_on`` (ruling R-DH, plan
            step 2).  **This is the "as of" a caption means.**  It was the same
            day as ``created_at``'s until the column became user-supplied; now
            an account opened today and back-dated to 2026-01-01 is a balance
            that was true on Jan 1, and a surface captioning it "anchored Jul
            31" is naming the keystroke rather than the fact.  On a modelled
            account it is also the day the return starts accruing from
            (``balance_at._asset_fold``), so a "growth since" caption that
            reads ``created_at`` contradicts the figure beside it.
        created_at: The anchor event's RECORDING instant, aware-UTC.  For a
            surface that genuinely shows when the row was entered; a reader
            wanting the day the balance was true wants
            :attr:`observed_on`.

    **It carried an ``as_of_date`` until 2026-07-31, and deleting it is
    finding N-133 / F12's other half.**  That field was ``created_at``
    truncated to a UTC day, justified in its own docstring by matching the
    ``uq_anchor_history_account_period_balance_day`` index -- which now keys
    on the stored ``observed_on`` instead, so the justification was gone.
    No production code read it: the account-detail route deliberately reads
    ``created_at`` and says why (a UTC day renders a late-evening Eastern
    anchor on the wrong day), and its only reader in the repository was one
    test assertion.  A reader that wants the day an assertion was TRUE reads
    ``CashAnchorFact.observed_on``, which is a stored fact rather than a
    seventh derivation of "which day".
    """

    balance: Decimal
    period: PayPeriod
    observed_on: date
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
            reasons: API symmetry with the row loaders beside it
            (``planned_cash_rows`` / ``settled_cash_facts``), which DO
            need the scenario to filter transactions, and forward
            compatibility with a possible future per-scenario anchor
            override.  It cited the anchor-forward ``balances_for``
            producer until plan step X-g4b deleted it; the symmetry
            argument is unchanged, only its example is.  The value is included in the
            reconciliation log payload so a future scenario-scoped
            divergence is traceable.

    Returns:
        :class:`AnchorPoint` -- balance, period, the day the balance was true,
        and the recording instant.

    **"Latest" is the latest BUSINESS day, and the tie-breaks are the WALK's,
    key for key.**  The order here is ``(observed_on, created_at, id)``
    descending -- the exact reverse of
    :func:`app.services.cash_ledger.cash_anchor_facts` -- so the row this names
    as current is by construction the row the walk replays LAST.  Both halves
    are load-bearing.  Two assertions can share an instant (a same-second
    true-up, or any fixture that stamps both), and ``created_at DESC`` alone
    returns whichever row the plan happens to yield, so ``id`` breaks it.  And
    since plan step 2 made ``observed_on`` a user-supplied column, the
    RECORDING order and the BUSINESS order can differ outright: a balance
    asserted for an earlier day but recorded later is not the current one, and
    ordering on ``created_at`` first would have named it.  One question answered
    two ways is how the resolver and the fold come to disagree about which
    balance is authoritative.

    Raises:
        RuntimeError: When no ``AccountAnchorHistory`` row exists for
            the account.  Unreachable in production after Commit 3;
            see the function docstring above for the regression-trap
            rationale.
    """
    latest: AccountAnchorHistory | None = (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(
            AccountAnchorHistory.observed_on.desc(),
            AccountAnchorHistory.created_at.desc(),
            AccountAnchorHistory.id.desc(),
        )
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

    return AnchorPoint(
        balance=history_balance,
        period=latest.pay_period,
        observed_on=latest.observed_on,
        created_at=latest.created_at,
    )


def reconciled_through(account_id: int) -> ReconciledThrough:
    """Return the coverage boundary *account_id*'s latest assertion establishes.

    The boundary every "is this already inside the balance the user declared"
    question is asked through
    (:meth:`~app.services.cash_ledger.ReconciledThrough.covers`), for the
    callers that do NOT already hold a walk: the posting self-heal's skip
    predicate, the entry list's reconciled indicator, and the reconcile panel.
    One indexed lookup (``idx_anchor_history_account`` leads on
    ``account_id``), no rows materialised, no anchor resolution.

    **It is the SQL twin of
    :attr:`~app.services.cash_ledger.CashLedgerWalk.reconciled_through`, and
    the two exist for a reason rather than by accident.**  A caller holding the
    walk already has the answer in memory and must not pay a query for it; a
    caller rendering one template row must not walk an account to get it.  They
    are provably equal -- ``MAX`` over the same column against the last element
    of a list the loader orders ``(observed_on, created_at, id)`` ascending --
    and that equality is pinned by a test rather than assumed, because "two
    statements that happen to agree" is the exact shape this arc exists to
    remove.  A THIRD statement is not acceptable: the account posting sync
    grew one (``MAX(created_at)`` as an instant, compared against a civil date
    pushed through midnight UTC) and it carried a silent timezone-sign
    dependency for the whole time it lived (finding N-133 / F4).

    Args:
        account_id: The account whose coverage boundary to resolve.

    Returns:
        The account's :class:`~app.services.cash_ledger.ReconciledThrough`.
        Its ``observed_day`` is ``None`` for an account with no anchor history
        (fixture-only -- migration ``cfb15e782f86`` plus
        ``account_service.create_account`` guarantee production accounts one)
        or a missing account, and such a boundary reconciles nothing, which is
        the honest answer when no balance has ever been declared.
    """
    return ReconciledThrough(
        ObservedOn.recorded(
            db.session.query(db.func.max(AccountAnchorHistory.observed_on))
            .filter(AccountAnchorHistory.account_id == account_id)
            .scalar()
        )
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
