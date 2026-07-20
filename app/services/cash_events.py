"""
Shekel Budget App -- Cash account event sources (the balance fold's inputs).

The FACTS a cash account's balance is folded from, and nothing that folds
them.  Three things live here, and they share one job: answering "what
happened to this account, and what was it worth?" -- never "what is the
balance at T", which is the :mod:`app.services.balance_at` seam's question.

  * :class:`AnchorPoint` / :func:`resolve_anchor` -- the user's balance
    ASSERTION, read from the dated ``AccountAnchorHistory`` source of truth
    (E-19).  A stored fact, not a computed projection.
  * :func:`load_balance_transactions` -- the account's balance-contributing
    transaction rows, with ``entries`` eager-loaded.
  * :func:`live_amount_overrides` -- what those rows are actually worth
    right now, when the stored amount is a stale cache.

Why its own module (plan step D1a).  These were four of the ten public names
in ``balance_resolver``, which sat at exactly 1000 lines -- pylint's default
module ceiling -- because it held three separable concerns: these event
SOURCES, the per-period FLOW sums (now :mod:`app.services.period_flows`), and
the balance PRODUCERS that fold the first over the second.  Only the producers
belong inside the balance seam; a fact reader that answers no balance-at-T
question would have had to be re-exported through the seam's public surface to
keep four consumers working, which is the seam carrying names that are not its
job (``docs/audits/balance_architecture/README.md``, step D1).

The dependency arrow runs ONE way: the producers import this module, and this
module imports none of them.  That is what makes the split safe, and it is
verified by the call graph rather than asserted.

Fence status, stated precisely because the two halves differ.  This module is
NOT on the W9906 call allowlist -- it calls no balance producer, and W9906
correctly flags it if it ever tries.  It IS scoped for the W9909 completeness
check, so a new public function here must be classified as a producer or a
non-producer rather than defaulting to unguarded.  D1a's adversarial review
proved that second half load-bearing: a cash balance-at-T folded from the names
in this module touches no fenced NAME, so without the W9909 scope it -- and a
route rendering it -- both rated 10.00/10.

Forward note: plan step X2 ("a cash account is an event stream") folds cash
from exactly these sources, the way the loan fold already reads its anchors
and payments.  This module is that stream's read side, assembled early so X2
extends a layer rather than re-splitting a leftover.

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

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.balance_predicates import balance_contributing_clause
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
    loan-resolver) stacks off this module's load path and out of any
    import cycle.  Returns an empty dict when neither seam has a
    candidate -- the common case -- so the override threading stays a
    structural no-op for those surfaces.

    Args:
        account: The :class:`~app.models.account.Account` whose rows are
            being priced; only its ``user_id`` is read (the income seam
            scopes its salary lookup by user).
        scenario_id: The scenario the amounts are resolved under.
        transactions: The loaded rows to price.  Each seam picks its own
            candidates out of this list and ignores the rest.

    Returns:
        ``dict`` mapping ``transaction_id`` to the live ``Decimal``
        amount, empty when neither seam has a candidate.
    """
    # Pylint: ``import-outside-toplevel`` -- imported locally to keep the
    # income_service (paycheck/tax) and loan_payment_service (loan-resolver)
    # stacks off this module's load path and out of any import cycle; the
    # helpers are only needed at call time.
    # pylint: disable=import-outside-toplevel
    from app.services import income_service, loan_payment_service
    income_overrides = income_service.live_projected_net(
        account.user_id, scenario_id, transactions,
    )
    loan_overrides = loan_payment_service.live_loan_transfer_amounts(
        scenario_id, transactions,
    )
    return {**income_overrides, **loan_overrides}
