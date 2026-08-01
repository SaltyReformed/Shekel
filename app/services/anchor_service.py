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
grid and Net Worth Cockpit HTMX anchor-edit endpoint (``true_up``).
Its transactional core:

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

  * **F-103 / C-22 same-day same-balance idempotency.** The unique
    index ``uq_anchor_history_account_period_balance_day`` on
    ``(account_id, pay_period_id, anchor_balance, observed_on)``
    rejects a second history INSERT asserting the same balance for the
    same BUSINESS day -- a network retry, a double-click on Save, or a
    back-and-resubmit.
    We translate that ``IntegrityError`` into ``DUPLICATE_SAME_DAY``
    so the caller renders an idempotent success (the prior request
    committed the same value the current request was trying to
    submit).  Its last column was ``((created_at AT TIME ZONE
    'UTC')::date)`` until ``observed_on`` existed, which keyed the guard
    to a UTC day while ruling R-DH's day is the user's -- so two
    assertions on two different Eastern days sharing one UTC day were
    rejected as duplicates (finding N-133 / F12).  The loan path uses
    the analogous expression index
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
from app.services import (
    account_posting_service,
    entry_service,
    loan_posting_service,
)
from app.utils.dates import display_today
from app.utils.db_errors import is_unique_violation


logger = logging.getLogger(__name__)


# Name of the unique index that backstops the F-103 / C-22 same-day
# same-balance idempotency rule.  It keys ``(account_id, pay_period_id,
# anchor_balance, observed_on)`` -- the BUSINESS day; it was a PARTIAL
# EXPRESSION index on a UTC-day truncation of ``created_at`` until plan step 2
# gave the row a stored day (finding N-133 / F12).  Mirrors the literal in
# ``app/models/account.py:AccountAnchorHistory.__table_args__``, its creating
# migration ``e8b14f3a7c22`` and its re-keying migration ``c4a19e7b2d80``;
# renaming the index requires a coordinated edit across all four sites.
ANCHOR_HISTORY_UNIQUE_INDEX = "uq_anchor_history_account_period_balance_day"


# Name of the partial unique expression index that backstops the
# same-day same-balance idempotency rule on loan anchor events
# (Commit 16, mirrors the checking-anchor index above).  Mirrors the
# literal in ``app/models/loan_anchor_event.py:LoanAnchorEvent.__table_args__``
# and Commit 12's loan_anchor_events migration; renaming the index
# requires a coordinated edit across all three sites.
LOAN_ANCHOR_EVENT_UNIQUE_INDEX = "uq_loan_anchor_events_acct_date_bal_day"


class AmortizingAccountAnchorError(ValueError):
    """Raised when a CASH anchor true-up targets an amortizing loan.

    A loan's balance is never ``accounts.current_anchor_balance`` -- it is
    ledger-derived, and its true-up path is
    :func:`apply_loan_anchor_true_up` (an append-only
    :class:`LoanAnchorEvent` plus a posting re-sync).  Writing the cash
    column instead creates a second, stored, never-reconciled loan balance
    (plan-of-record finding B-15: the real Mortgage's column was set to
    $1.00 with an HTTP 200 while the ledger said $177,277.97, and the grid
    then rendered the $1.00).  The cash entry point refuses the kind so
    that cannot recur; routes translate this into a client error naming
    the loan path (ruling D4, step A1).
    """


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
        DUPLICATE_SAME_DAY: The F-103 unique index rejected the second
            INSERT for the same ``(account, period, balance,
            observed_on)`` tuple -- the same BUSINESS day, not the same
            UTC recording day (finding N-133 / F12); the session was
            rolled back.  Route
            treats this as idempotent success (the first request
            committed the same value the second was trying to submit)
            and renders the success partial without re-issuing the
            commit.
    """

    COMMITTED = "committed"
    STALE_CONFLICT = "stale_conflict"
    DUPLICATE_SAME_DAY = "duplicate_same_day"


def stage_anchor_true_up(
    *,
    account: Account,
    new_balance: Decimal,
    anchor_period: PayPeriod,
    notes: str | None = None,
) -> None:
    """Stage an anchor re-point + history row without committing.

    The flush-only in-memory core of :func:`apply_anchor_true_up`: it
    re-points the account's anchor period, writes the new anchor balance,
    and appends the audit-trail :class:`AccountAnchorHistory` row.  It
    does NOT clear past-dated entries, does NOT commit, and does NOT
    translate the StaleData / F-103 outcomes -- the caller owns the
    transaction and its conflict handling.

    Extracted so the two paths that set an existing account's anchor share
    ONE definition of "re-point the period + write the balance + append
    the history row", and can never drift:

      * :func:`apply_anchor_true_up` wraps this with the checking-account
        entry reconcile, the ``commit()``, and the C-17 / F-103 outcome
        translation its HTMX route callers depend on.
      * ``app.services.pay_period_admin.reset_pay_periods`` re-anchors
        every account onto a freshly rebuilt schedule inside ONE
        transaction (the deferred anchor FK is validated only at the final
        commit), so a per-account commit would fire that check while other
        accounts still dangle.  It calls this flush-only core directly and
        lets its route commit once.

    The amortizing-kind gate (:class:`AmortizingAccountAnchorError`) lives
    on :func:`apply_anchor_true_up`, deliberately NOT here: the reset path
    above stages anchors for EVERY account kind because it preserves each
    account's existing balance across a schedule rebuild rather than
    asserting a new one.  That loan-column preservation is a recorded
    residue of B-15 (plan-of-record ledger), not an endorsement.

    Args:
        account: An attached :class:`Account` row.  Caller owns the
            ownership check.
        new_balance: The validated :class:`Decimal` anchor balance to
            write.
        anchor_period: The :class:`PayPeriod` to anchor against.
        notes: Optional free-text note for the history row's ``notes``
            column (e.g. ``"origination (pay-period reset)"`` so the audit
            trail names the originating path).  ``None`` leaves it NULL,
            matching the true-up route path.
    """
    account.current_anchor_balance = new_balance
    account.current_anchor_period_id = anchor_period.id

    db.session.add(AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=anchor_period.id,
        anchor_balance=new_balance,
        # The civil day this balance is asserted TRUE for (ruling R-DH).  A
        # true-up is the user reading their bank NOW, so it is today in the
        # USER's zone -- not ``date.today()``, which is the server's UTC day
        # and files an 8pm-Eastern true-up under tomorrow.  It is the same day
        # ``cash_anchor_facts`` derived from ``created_at`` before the column
        # existed, so this write moves no figure.  Plan step 2's remaining half
        # (the true-up form's own date field) is what makes it user-supplied,
        # exactly as ``account_service.create_account`` already takes it for an
        # opening; the parameter arrives with that consumer, not before it.
        observed_on=display_today(),
        notes=notes,
    ))


def apply_anchor_true_up(
    *,
    account: Account,
    new_balance: Decimal,
    anchor_period: PayPeriod,
    user_id: int,
) -> AnchorTrueUpOutcome:
    """Apply an anchor balance true-up to ``account`` and commit.

    Stages the in-memory mutation and audit-trail history row via
    :func:`stage_anchor_true_up`, reconciles past-dated entries when the
    account is checking, and commits the transaction.  Returns an
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
        user_id: ``auth.users.id`` of the account owner.  Forwarded
            (with ``account.id``) to
            ``entry_service.clear_entries_for_anchor_true_up`` for the
            per-owner, per-account entry-reconcile filter.

    Returns:
        AnchorTrueUpOutcome -- which response the route should render.

    Raises:
        AmortizingAccountAnchorError: When ``account`` is an amortizing
            loan (``account_type.has_amortization``).  A loan's balance
            is ledger-derived and asserted through
            :func:`apply_loan_anchor_true_up`; the cash column must not
            become a second stored loan balance (B-15 / ruling D4).
            Raised BEFORE anything is staged, so the session is clean.
        IntegrityError: When the IntegrityError raised at commit time
            is NOT the F-103 unique-index violation -- a different
            constraint failed and we must not swallow it.  Caller
            propagates (Flask will surface as 500, which is the
            correct disposition for an unexpected DB-level failure).
    """
    acct_type = account.account_type
    if acct_type is not None and acct_type.has_amortization:
        raise AmortizingAccountAnchorError(
            f"account {account.id} is an amortizing loan; assert its "
            "balance through apply_loan_anchor_true_up, never as a "
            "cash anchor"
        )

    stage_anchor_true_up(
        account=account,
        new_balance=new_balance,
        anchor_period=anchor_period,
    )

    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    try:
        if account.account_type_id == checking_type_id:
            entry_service.clear_entries_for_anchor_true_up(user_id, account.id)
        # Build-Order Step 5: the new assertion re-bases the account's
        # anchor corrections in EVERY scenario (anchor history is
        # per-account) -- the fresh history row autoflushes into the walk's
        # first query, so the reconcile books the true-up delta in the same
        # transaction.  Runs AFTER the entry reconcile (verified
        # side-effect-free for posted amounts: it flips ``is_cleared`` on
        # PROJECTED parents only, and the Step-3 effect formula never reads
        # ``is_cleared``) and inside this ``try`` so a StaleDataError or the
        # F-103 duplicate surfacing at its flushes translates into the same
        # outcome enum.  An amortizing loan is a structural no-op (loans
        # true-up through :func:`apply_loan_anchor_true_up`).
        account_posting_service.sync_account_anchor_postings_all_scenarios(
            account.id,
        )
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


def _append_loan_anchor_and_sync(
    *,
    account: Account,
    anchor_balance: Decimal,
    anchor_date: date,
    source: LoanAnchorSourceEnum,
) -> AnchorTrueUpOutcome:
    """Append one :class:`LoanAnchorEvent` of ``source`` and re-sync the ledger.

    The shared transactional core of :func:`apply_loan_anchor_true_up` (a
    ``user_trueup`` balance assertion) and :func:`record_loan_tracking_start`
    (the ``tracking_start`` opening of a mid-life-imported loan): the two differ
    ONLY in the anchor source, so they must not drift on the append + re-sync +
    idempotency handling.

    Appends ONE row to the append-only :class:`LoanAnchorEvent` table, then
    re-syncs the loan's genesis postings in EVERY scenario (the anchor is
    per-account, not per-scenario) via
    :func:`app.services.loan_posting_service.sync_all_scenarios_or_duplicate` --
    which re-runs the running-balance walk so payments re-split from the new
    anchor.  The just-added event becomes visible to that walk because the sync's
    first query autoflushes it (load-bearing -- must NOT run under
    ``session.no_autoflush``).  A same-day partial-unique rejection
    (``uq_loan_anchor_events_acct_date_bal_day``) surfaced by that flush is
    translated into the idempotent ``DUPLICATE_SAME_DAY`` outcome; a non-anchor
    ``IntegrityError`` propagates (the correct 500 disposition).

    Args:
        account: An attached :class:`Account` row for the loan.  Caller owns the
            ownership check.
        anchor_balance: The validated :class:`Decimal` balance to assert
            (``>= 0`` enforced at the schema layer, backstopped by
            ``ck_loan_anchor_events_balance_nonneg``).
        anchor_date: The date the balance is asserted for.  Caller enforces the
            source-appropriate bounds (see the two public wrappers).
        source: The :class:`~app.enums.LoanAnchorSourceEnum` provenance --
            ``USER_TRUEUP`` or ``TRACKING_START``.

    Returns:
        ``COMMITTED`` when the event was written and committed;
        ``DUPLICATE_SAME_DAY`` when the same-day partial unique rejected an
        identical INSERT.

    Raises:
        IntegrityError: When the surfaced ``IntegrityError`` is NOT the
            same-day-uniqueness violation (a different constraint failed).
    """
    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=ref_cache.loan_anchor_source_id(source),
    ))
    if not loan_posting_service.sync_all_scenarios_or_duplicate(
        account.id, LOAN_ANCHOR_EVENT_UNIQUE_INDEX,
    ):
        logger.info(
            "Duplicate same-day loan anchor (%s) prevented for account %d "
            "on %s (idempotent success)",
            source.value, account.id, anchor_date,
        )
        return AnchorTrueUpOutcome.DUPLICATE_SAME_DAY

    db.session.commit()
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
        IntegrityError: When the IntegrityError surfaced while re-splitting
            and flushing the true-up (via
            :func:`app.services.loan_posting_service.sync_all_scenarios_or_duplicate`)
            is NOT the same-day-uniqueness violation -- a different
            constraint failed and we must not swallow it.  Caller
            propagates (Flask will surface as 500, which is the
            correct disposition for an unexpected DB-level failure).
    """
    return _append_loan_anchor_and_sync(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
        source=LoanAnchorSourceEnum.USER_TRUEUP,
    )


def record_loan_tracking_start(
    *,
    account: Account,
    anchor_balance: Decimal,
    anchor_date: date,
) -> AnchorTrueUpOutcome:
    """Append a ``tracking_start`` opening :class:`LoanAnchorEvent` and commit.

    The mid-life-import opening flow: the operator started tracking an
    already-amortizing loan and asserts its real balance as of a date at/before
    the first recorded payment.  Recorded through this chokepoint, the
    ``tracking_start`` event becomes the loan's confirmed-ledger OPENING
    (:func:`app.services.loan_loaders._opening_anchor_fact` synthesizes the
    ``is_opening`` anchor from it in place of the origination), so the genesis
    ledger opens at the recent known balance -- no fictional
    origination-to-tracking-start plateau, and every recorded payment accrues
    interest on the correct balance.  The origination fields on
    :class:`LoanParams` are untouched; they still drive the amortization
    schedule / projection.

    Shares the append + all-scenario re-sync + same-day idempotency of
    :func:`apply_loan_anchor_true_up` via :func:`_append_loan_anchor_and_sync`;
    the only difference is the anchor source.  Like a true-up it never mutates
    :class:`LoanParams`.

    Args:
        account: An attached :class:`Account` row for the loan.  Caller is
            responsible for the ownership check and for confirming the account
            carries ``has_amortization=True``.
        anchor_balance: The validated :class:`Decimal` opening balance
            (``>= 0`` at the schema layer).
        anchor_date: The date the balance is asserted for.  Caller is
            responsible for enforcing ``origination_date <= anchor_date``,
            ``anchor_date <= today``, and that it is at/before the earliest
            recorded payment so no payment is left pre-opening.

    Returns:
        ``COMMITTED`` on a new committed event; ``DUPLICATE_SAME_DAY`` on a
        same-day identical INSERT (idempotent success).

    Raises:
        IntegrityError: When a surfaced ``IntegrityError`` is NOT the
            same-day-uniqueness violation (a different constraint failed).
    """
    return _append_loan_anchor_and_sync(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
        source=LoanAnchorSourceEnum.TRACKING_START,
    )
