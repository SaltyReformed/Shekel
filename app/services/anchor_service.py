"""
Shekel Budget App -- Anchor True-up Service

Single authoritative implementation of the anchor true-up mutation
for every account kind that carries an append-only anchor history:
checking accounts via :class:`AccountAnchorHistory` and loan
accounts via :class:`LoanAnchorEvent` (E-18 / Commit 16, decision
D-C).  Both call sites switch on the same
:class:`AnchorTrueUpOutcome` enum so the route layer's response
composition is uniform.

The checking-anchor path -- :func:`apply_anchor_true_up` -- backs the
two HTMX anchor-edit endpoints (``inline_anchor_update`` from the
accounts list and ``true_up`` from the grid).  They share an
identical transactional core:

  1. Mutate ``account.current_anchor_balance`` and
     ``current_anchor_period_id``.
  2. Append an ``AccountAnchorHistory`` row.
  3. When the account is checking, bulk-clear past-dated entries on
     projected parents (the entry-reconcile contract -- see
     ``entry_service.clear_entries_for_anchor_true_up`` for the
     rationale).
  4. Commit.

The loan-anchor path -- :func:`apply_loan_anchor_true_up` -- backs
the loan dashboard's "Record loan balance as of date D" form.  It
shares the enum contract but operates on a different model and a
different mutation set:

  1. Append a ``user_trueup`` :class:`LoanAnchorEvent` row (the
     table is structurally append-only; no UPDATE/DELETE).
  2. Commit.

A loan trueup never mutates ``LoanParams`` -- the resolver
(:func:`app.services.loan_resolver.resolve_loan`) reads the latest
event to derive the displayed current balance, monthly payment,
schedule and payoff date, so a new event immediately changes every
loan surface consistently without writing a column.  The
:class:`LoanAnchorEvent` row has no ``version_id`` column, so the
``STALE_CONFLICT`` outcome from the checking path is unreachable for
loans; the function nevertheless returns the same enum (COMMITTED or
DUPLICATE_SAME_DAY) so call-site response composition is identical.

Two failure modes are part of the contract:

  * **C-17 optimistic lock (F-009).** The SQLAlchemy ``version_id_col``
    on ``Account`` raises ``StaleDataError`` at flush time when a
    concurrent commit has bumped ``version_id`` between the route's
    SELECT and this commit's UPDATE.  Routes additionally perform a
    pre-flush ``version_id`` check on the submitted form value to
    catch the sequential Tab-1/Tab-2 race documented in the C-17
    plan; the SQLAlchemy-tier check here covers the truly-concurrent
    interleavings the form-side check cannot see.

  * **F-103 / C-22 same-day same-balance idempotency.** The partial
    unique expression index
    ``uq_anchor_history_account_period_balance_day`` on
    ``(account_id, pay_period_id, anchor_balance,
    ((created_at AT TIME ZONE 'UTC')::date))`` rejects a second history
    INSERT with identical values inserted on the same calendar day --
    a network retry, a double-click on Save, or a back-and-resubmit.
    We translate that ``IntegrityError`` into ``DUPLICATE_SAME_DAY``
    so the caller renders an idempotent success (the prior request
    committed the same value the current request was trying to
    submit).  The loan path uses the analogous expression index
    ``uq_loan_anchor_events_acct_date_bal_day`` covering
    ``(account_id, anchor_date, anchor_balance,
    ((created_at AT TIME ZONE 'UTC')::date))`` -- mirrors the checking
    semantics so a double-click on the loan dashboard's "Record
    balance" button is idempotent in the same way.

Pre-Commit-16 this consolidation eliminates two byte-identical
``try/except`` blocks in ``app/routes/accounts.py``; the loan
principal true-up (E-18) introduced by Commit 16 will extend this
service rather than paste a third copy.

Services boundary: no Flask imports, no ``request``/``session``/
``current_app``/``render_template``.  The route owns the response
rendering; this module returns an outcome enum the route translates
into its template/header pair.  The session itself is the project's
SQLAlchemy ``db.session`` proxy, which IS Flask-bound -- consistent
with every other service in ``app/services/`` (e.g. ``entry_service``,
``balance_resolver``).

``update_account`` (the full-form POST handler in
``app/routes/accounts.py``) deliberately does NOT route through this
service.  Its mutation set is multi-field and its conflict UX is
flash+redirect rather than a partial swap, and its history-row write
is conditional on ``anchor_changed`` -- folding it in would require
optional-parameter shapes that re-grow the helper.  The C-17 contract
in ``update_account`` is preserved by its own inline ``StaleDataError``
catch; the F-103 path is statistically unreachable there because the
version_id bump catches every double-submit first.
"""

from __future__ import annotations

import enum
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import AcctTypeEnum, LoanAnchorSourceEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.pay_period import PayPeriod
from app.services import entry_service
from app.utils.db_errors import is_unique_violation


logger = logging.getLogger(__name__)


# Name of the partial unique expression index that backstops the F-103
# / C-22 same-day same-balance idempotency rule.  Mirrors the literal
# in ``app/models/account.py:AccountAnchorHistory.__table_args__``
# and ``migrations/versions/e8b14f3a7c22_c22_idempotency_uniqueness_constraints.py``;
# renaming the index requires a coordinated edit across all three sites.
ANCHOR_HISTORY_UNIQUE_INDEX = "uq_anchor_history_account_period_balance_day"


# Name of the partial unique expression index that backstops the
# same-day same-balance idempotency rule on loan anchor events
# (Commit 16, mirrors the checking-anchor index above).  Mirrors the
# literal in ``app/models/loan_anchor_event.py:LoanAnchorEvent.__table_args__``
# and Commit 12's loan_anchor_events migration; renaming the index
# requires a coordinated edit across all three sites.
LOAN_ANCHOR_EVENT_UNIQUE_INDEX = "uq_loan_anchor_events_acct_date_bal_day"


class AnchorTrueUpOutcome(enum.Enum):
    """Discriminant returned by :func:`apply_anchor_true_up`.

    The route picks a partial template + status code + headers from
    this; the service never touches the response layer.

    Members:
        COMMITTED: The new ``AccountAnchorHistory`` row was written
            and the commit succeeded.  Route renders the success
            partial (200) and, where relevant, the OOB swap + HX-Trigger.
        STALE_CONFLICT: SQLAlchemy raised ``StaleDataError`` at flush
            time and the session was rolled back.  Route re-fetches
            ``Account`` from the database (the in-memory mutations
            were discarded by the rollback) and renders the 409
            conflict partial.
        DUPLICATE_SAME_DAY: The F-103 partial unique index rejected
            the second INSERT for the same ``(account, period, balance,
            UTC day)`` tuple; the session was rolled back.  Route
            treats this as idempotent success (the first request
            committed the same value the second was trying to submit)
            and renders the success partial without re-issuing the
            commit.
    """

    COMMITTED = "committed"
    STALE_CONFLICT = "stale_conflict"
    DUPLICATE_SAME_DAY = "duplicate_same_day"


def apply_anchor_true_up(
    *,
    account: Account,
    new_balance: Decimal,
    anchor_period: PayPeriod,
    user_id: int,
) -> AnchorTrueUpOutcome:
    """Apply an anchor balance true-up to ``account`` and commit.

    Performs the in-memory mutation, appends the audit-trail history
    row, reconciles past-dated entries when the account is checking,
    and commits the transaction.  Returns an
    :class:`AnchorTrueUpOutcome` discriminant the caller translates
    into its rendered response.

    The conditional ``entry_service.clear_entries_for_anchor_true_up``
    call is wrapped in the same ``try`` as ``commit()`` for autoflush
    ordering: the bulk ``UPDATE TransactionEntry`` issued there forces
    a session autoflush of the pending ``Account`` mutation, and the
    version-pinned WHERE on that UPDATE is what actually raises
    ``StaleDataError`` for the truly-concurrent race.  Catching only
    around ``commit()`` would let the autoflush error propagate as a
    500 instead of a clean ``STALE_CONFLICT`` outcome.

    Why entries clear on a checking true-up: the user is declaring
    "my real checking is now $X" -- every past-dated debit purchase
    recorded against a projected transaction is already in that
    number, so flipping ``is_cleared = TRUE`` stops the balance
    calculator from double-counting them.  Debit purchases only hit
    checking, so the reconcile fires only for that account type.

    Args:
        account: An attached :class:`Account` row.  Caller is
            responsible for the ownership check (route uses 404 for
            cross-owner access) and the pre-flush ``version_id``
            comparison against the submitted form value.
        new_balance: The validated :class:`Decimal` anchor balance to
            write.  Caller is responsible for constructing this from
            schema-validated form data via ``Decimal(str(...))``.
        anchor_period: The :class:`PayPeriod` to anchor against.
            Resolved by the caller (typically
            ``pay_period_service.get_current_period``).
        user_id: ``auth.users.id`` of the account owner.  Forwarded to
            ``entry_service.clear_entries_for_anchor_true_up`` for
            the per-owner entry-reconcile filter.

    Returns:
        AnchorTrueUpOutcome -- which response the route should render.

    Raises:
        IntegrityError: When the IntegrityError raised at commit time
            is NOT the F-103 unique-index violation -- a different
            constraint failed and we must not swallow it.  Caller
            propagates (Flask will surface as 500, which is the
            correct disposition for an unexpected DB-level failure).
    """
    account.current_anchor_balance = new_balance
    account.current_anchor_period_id = anchor_period.id

    db.session.add(AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=anchor_period.id,
        anchor_balance=new_balance,
    ))

    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    try:
        if account.account_type_id == checking_type_id:
            entry_service.clear_entries_for_anchor_true_up(user_id)
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on anchor true-up account_id=%d",
            account.id,
        )
        return AnchorTrueUpOutcome.STALE_CONFLICT
    except IntegrityError as exc:
        db.session.rollback()
        if not is_unique_violation(exc, ANCHOR_HISTORY_UNIQUE_INDEX):
            # Some other constraint failed -- do not silently treat as
            # idempotent success; re-raise so the unexpected DB-level
            # failure surfaces (Flask returns 500).
            raise
        logger.info(
            "Duplicate same-day anchor history prevented for account %d "
            "(idempotent success)",
            account.id,
        )
        return AnchorTrueUpOutcome.DUPLICATE_SAME_DAY

    return AnchorTrueUpOutcome.COMMITTED


def apply_loan_anchor_true_up(
    *,
    account: Account,
    anchor_balance: Decimal,
    anchor_date: date,
) -> AnchorTrueUpOutcome:
    """Append a user-trueup :class:`LoanAnchorEvent` and commit.

    The loan analogue of :func:`apply_anchor_true_up` (E-18 / Commit
    16, decision D-C).  The loan resolver derives the displayed
    current balance, monthly payment, schedule and payoff date from
    the latest anchor event plus the confirmed payment stream, so a
    new trueup event immediately changes every loan surface
    consistently without mutating any column on
    :class:`LoanParams`.

    The function appends ONE row to :class:`LoanAnchorEvent`.  The
    table is structurally append-only (the model's
    ``before_update`` / ``before_delete`` event listeners refuse any
    ORM-mediated UPDATE or DELETE), so a correction of an earlier
    trueup is expressed as another append, never an edit.  The
    function does NOT mutate :class:`LoanParams.current_principal` --
    that column is non-authoritative seed (E-18) and is never written
    by the trueup flow.

    The ``STALE_CONFLICT`` outcome from
    :func:`apply_anchor_true_up` is unreachable here: a
    :class:`LoanAnchorEvent` is an INSERT-only row with no
    ``version_id`` column, and the resolver is read-only.  Two
    concurrent trueup commits with different ``(anchor_date,
    anchor_balance)`` produce two rows, both legitimate; the resolver
    selects the latest by ``(anchor_date, created_at)`` DESC, so the
    last writer's row wins on display while neither is lost.

    The ``DUPLICATE_SAME_DAY`` outcome mirrors the checking-anchor
    semantics: when a second request submits the same
    ``(account_id, anchor_date, anchor_balance)`` on the same UTC
    calendar day, the partial unique expression index
    ``uq_loan_anchor_events_acct_date_bal_day`` rejects the INSERT,
    we roll back, and return DUPLICATE_SAME_DAY so the caller renders
    idempotent success.  This handles network retries and
    double-clicks on the Save button.

    Args:
        account: An attached :class:`Account` row for the loan.
            Caller is responsible for the ownership check (route uses
            404 for cross-owner access) and for confirming the
            account type carries ``has_amortization=True`` (the
            route's ``_load_loan_account`` enforces this).
        anchor_balance: The validated :class:`Decimal` anchor balance
            to write.  Caller is responsible for constructing this
            from schema-validated form data via ``Decimal(str(...))``
            and for enforcing ``anchor_balance >= 0`` at the schema
            layer (the storage tier's
            ``ck_loan_anchor_events_balance_nonneg`` is the backstop).
        anchor_date: The date the user is asserting the balance for.
            Caller is responsible for enforcing
            ``anchor_date <= today`` and
            ``anchor_date >= params.origination_date`` at the
            schema/route layer; this function trusts the caller and
            persists whatever date it is given.

    Returns:
        AnchorTrueUpOutcome -- ``COMMITTED`` when a new event row was
        written and the commit succeeded; ``DUPLICATE_SAME_DAY`` when
        the loan partial unique index rejected an identical
        same-day INSERT.  ``STALE_CONFLICT`` is never returned by
        this function but is part of the enum's contract so route
        composition is uniform with the checking-anchor path.

    Raises:
        IntegrityError: When the IntegrityError raised at commit time
            is NOT the same-day-uniqueness violation -- a different
            constraint failed and we must not swallow it.  Caller
            propagates (Flask will surface as 500, which is the
            correct disposition for an unexpected DB-level failure).
    """
    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        ),
    ))

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        if not is_unique_violation(exc, LOAN_ANCHOR_EVENT_UNIQUE_INDEX):
            # Some other constraint failed -- do not silently treat
            # as idempotent success; re-raise so the unexpected
            # DB-level failure surfaces (Flask returns 500).
            raise
        logger.info(
            "Duplicate same-day loan anchor event prevented for "
            "account %d on %s (idempotent success)",
            account.id, anchor_date,
        )
        return AnchorTrueUpOutcome.DUPLICATE_SAME_DAY

    return AnchorTrueUpOutcome.COMMITTED
