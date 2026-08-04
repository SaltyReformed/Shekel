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

  1. Append an ``AccountAnchorHistory`` row -- an account, a balance, and
     the day it was true.
  2. Commit.

**Step 3 used to be a bulk-clear of entry flags, and its deletion is ruling
R-DH (d)** (``docs/audits/balance_architecture/anchor_settle_partition.md``,
plan step S1-c).  A true-up flipped ``is_cleared = TRUE`` on every entry dated
on or before the SERVER's today, so whether a purchase counted as already
inside the asserted balance depended on the order two buttons were pressed:
record the purchase then true up and it cleared, true up then record and it
never did.  There is no flag to write now -- reconciliation is derived from the
purchase's own recorded posting day -- so the true-up appends one assertion
and nothing else.  It mutated ``accounts`` too until ruling R-EH deleted the
anchor cache columns (plan step X-f1c3c); the assertion IS the state now.
Confirming which outstanding purchases the
statement showed is a SEPARATE, user-driven step
(``entry_service.record_settled_days``) that the route offers after this commit
lands; keeping it out of this transaction is deliberate, because a same-day
re-assert is swallowed here as idempotent success and any reconciliation riding
along would be silently rolled back with it.

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
loan surface consistently without writing a column.

**Both paths are APPEND-ONLY now, and the checking path's optimistic lock
went with the columns** (ruling R-EN, plan step X-f1c3c).  The C-17
``version_id_col`` on ``Account`` raised ``StaleDataError`` when a concurrent
commit bumped the counter between a route's SELECT and its UPDATE -- but a
true-up no longer UPDATEs ``accounts`` at all, so there is no such flush and no
``STALE_CONFLICT`` outcome on either path.  No ASSERTION is lost by that: two
assertions of different balances are two FACTS, the later-observed one is
current, and neither is overwritten.  This is the contract the loan path has
documented since Commit 16, now shared.

**The posted LEDGER is a separate question, and the shared contract never
answered it.**  Both paths re-sync the ledger after appending, and a re-sync is
a reconcile-to-target: read what is posted, subtract, INSERT the difference.
Two of those interleaved both subtract the same posted state.  The cash side
had been serialised by accident (the deleted ``version_id`` UPDATE autoflushed
and took a row lock before the walk) and the loan side never was at all.  Since
plan step X-f1c3c the reconcile takes a per-owner advisory lock for itself
(:mod:`app.services.user_write_lock`), so the guarantee belongs to the code
that needs it rather than to whichever caller happened to write a row first.

One failure mode is part of the contract:

  * **F-103 / C-22 same-day same-balance idempotency.** The unique
    index ``uq_anchor_history_account_period_balance_day`` on
    ``(account_id, anchor_balance, observed_on)``
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
``app/routes/accounts/crud.py``) deliberately does NOT route through
:func:`apply_anchor_true_up`.  Its mutation set is multi-field and its conflict
UX is flash+redirect rather than a partial swap, so folding it in would require
optional-parameter shapes that re-grow the helper.  It DOES share
:func:`stage_anchor_true_up`, so the two doors cannot drift on what an
assertion is.  Its own C-17 lock survives and is not this ruling's business:
that door writes real ``accounts`` columns (name, type, sort order), so it
still has a row to guard.
"""

from __future__ import annotations

import enum
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.loan_anchor_event import LoanAnchorEvent
from app.services import (
    account_posting_service,
    loan_posting_service,
)
from app.utils.dates import display_today
from app.utils.db_errors import is_unique_violation


logger = logging.getLogger(__name__)


# Name of the unique index that backstops the F-103 / C-22 same-day
# same-balance idempotency rule.  It keys ``(account_id, anchor_balance,
# observed_on)`` -- the BUSINESS day.  ``pay_period_id`` left the key with the
# COLUMN at plan step X-f1c3b (ruling R-EO), which made the guard strictly
# tighter and rejected 0 of the 78 production rows.  It was a PARTIAL
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
        DUPLICATE_SAME_DAY: The F-103 unique index rejected the second
            INSERT for the same ``(account_id, anchor_balance,
            observed_on)`` tuple -- the same BUSINESS day, not the same
            UTC recording day (finding N-133 / F12).  The key lost its
            ``pay_period_id`` column with ruling R-EO (plan step
            X-f1c3b) and this docstring named the pre-re-key four-column
            tuple until X-f1c3c.  The session was
            rolled back.  Route
            treats this as idempotent success (the first request
            committed the same value the second was trying to submit)
            and renders the success partial without re-issuing the
            commit.
    """

    COMMITTED = "committed"
    DUPLICATE_SAME_DAY = "duplicate_same_day"


def stage_anchor_true_up(
    *,
    account: Account,
    new_balance: Decimal,
    notes: str | None = None,
) -> None:
    """Append a dated balance ASSERTION for ``account`` without committing.

    The flush-only in-memory core of :func:`apply_anchor_true_up`, shared with
    the full-form account edit (``routes/accounts/crud.update_account``) so the
    two write doors cannot drift on what an assertion IS.  It does NOT clear
    past-dated entries, does NOT commit, and does NOT translate the F-103
    outcome -- the caller owns the transaction.

    **What it stages shrank twice, and both shrinks are the same ruling
    applied one table apart.**  It used to re-point ``current_anchor_period_id``
    and write ``current_anchor_balance`` before appending the row; ruling R-EH
    deleted those columns as a denormalized copy of the row itself.  It used to
    file the row against a pay period; ruling R-EO deleted THAT, because a
    balance assertion is a fact about a bank and a schedule operation must not
    be able to destroy it.  What is left is the assertion: an account, a
    balance, and the day it was true.

    The amortizing-kind gate (:class:`AmortizingAccountAnchorError`) lives on
    :func:`apply_anchor_true_up`, deliberately NOT here, so a caller that is
    not asserting a CASH balance (the account-edit door, which refuses the kind
    at its own validator) is not gated twice.

    Args:
        account: An attached :class:`Account` row.  Caller owns the
            ownership check.
        new_balance: The validated :class:`Decimal` balance being asserted.
        notes: Optional free-text note for the history row's ``notes``
            column, so the audit trail names the originating path.  ``None``
            leaves it NULL, matching the true-up route path.
    """
    db.session.add(AccountAnchorHistory(
        account_id=account.id,
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
) -> AnchorTrueUpOutcome:
    """Append a balance assertion for ``account``, re-base its postings, commit.

    Stages the assertion via :func:`stage_anchor_true_up`, re-bases the
    account's posted anchor corrections, and commits.  Returns an
    :class:`AnchorTrueUpOutcome` discriminant the caller translates into its
    rendered response.

    **The C-17 optimistic lock left this path at plan step X-f1c3c** (ruling
    R-EN), and the reason is that the path stopped writing the row the lock
    guarded.  ``version_id`` increments when the ORM UPDATEs ``accounts`` and
    on nothing else; once ruling R-EH deleted the anchor cache columns a
    true-up only INSERTs a history row, so ``StaleDataError`` became
    structurally unreachable here.  **Measured** against the dev database and
    rolled back: adding an ``AccountAnchorHistory`` row and flushing leaves
    ``version_id`` at 33, and the very next line writing
    ``current_anchor_balance`` takes it to 34.

    The step accepted what that means rather than working around it.  **An
    assertion history is append-only, so a second tab overwrites no
    ASSERTION**: two assertions of different balances are two facts, the
    later-observed one is current, and neither is lost -- verbatim the contract
    :func:`apply_loan_anchor_true_up` has documented since Commit 16.  This
    makes the cash half the same shape rather than the exception.  Bumping
    ``version_id`` deliberately to keep the lock alive was rejected: a write to
    ``accounts`` whose only purpose is to keep a lock alive is a mechanism with
    no fact under it.

    **That is a property of ONE table in a transaction that mutates three, and
    a first version of this paragraph stated it as though it covered the whole
    call.**  The ``sync_account_anchor_postings_all_scenarios`` below is a
    RECONCILE-TO-TARGET: it reads what is posted, subtracts that from what the
    assertions say, and INSERTs the difference into ``budget.journal_entries``
    / ``budget.account_postings``.  Nothing about append-only makes a
    read-modify-write safe, and the deleted ``version_id`` UPDATE had been
    serialising it by accident -- it autoflushed and took a row lock before the
    walk.  Measured with the interleave forced at the reconcile's read: two
    concurrent true-ups on an account reconciled at ``$4,000.00`` both answer
    200, both assertions survive, the resolver returns one of them -- and the
    linked ledger settles at ``$1,000.00`` against a resolved ``$2,000.00``,
    with the trial balance still ``$0.00`` because the anchor-equity leg
    mirrors the error.  The serialisation is now EXPLICIT and owned by the
    reconcile rather than by a column that happened to sit in front of it:
    :func:`app.services.user_write_lock.lock_user_writes`, taken inside the
    sync, so every other door into that same window (the settle self-heal, the
    direct anchor edit, the pay-period resync) is covered by the same rule.
    The waiting transaction re-reads under READ COMMITTED -- verified as the
    default on dev, test and production, with no override anywhere -- so it
    sees the winner's postings and reconciles to the true merged target.

    **It touches no entry, and that is ruling R-DH (d).**  It used to bulk-flip
    ``is_cleared`` on every entry dated on or before the server's today, which
    made "is this purchase already inside the balance the user just typed"
    an answer decided by the order two buttons were pressed.  The flag is
    gone; reconciliation is derived from each purchase's own recorded posting
    day.  Which outstanding purchases the statement actually showed is a
    separate step the route offers AFTER this commit succeeds
    (``entry_service.record_settled_days``) -- and it is deliberately not
    folded in here, because the F-103 duplicate below is swallowed as
    idempotent success after a ``rollback()``, so a reconciliation riding in
    this transaction would be silently discarded while the UI reported a save.

    The posting re-sync stays inside the same ``try`` as ``commit()``: its
    first query forces a session autoflush of the pending history row, so the
    F-103 unique violation can surface there rather than at ``commit()``, and
    catching only around the commit would let it propagate as a 500 instead of
    the idempotent ``DUPLICATE_SAME_DAY`` outcome.

    Args:
        account: An attached :class:`Account` row.  Caller is
            responsible for the ownership check (route uses 404 for
            cross-owner access).
        new_balance: The validated :class:`Decimal` balance being asserted.
            Caller is responsible for constructing this from
            schema-validated form data via ``Decimal(str(...))``.

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

    stage_anchor_true_up(account=account, new_balance=new_balance)

    try:
        # Build-Order Step 5: the new assertion re-bases the account's
        # anchor corrections in EVERY scenario (anchor history is
        # per-account) -- the fresh history row autoflushes into the walk's
        # first query, so the reconcile books the true-up delta in the same
        # transaction.  Inside this ``try`` so the F-103 duplicate surfacing
        # at its flushes translates into the outcome enum.  An amortizing loan
        # is a structural no-op (loans true-up through
        # :func:`apply_loan_anchor_true_up`).
        account_posting_service.sync_account_anchor_postings_all_scenarios(
            account.id,
        )
        db.session.commit()
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

    **There is no stale-form conflict on either path, and since plan step
    X-f1c3c that is stated the same way for both.**  A
    :class:`LoanAnchorEvent` is an INSERT-only row with no ``version_id``
    column, and the resolver is read-only.  Two concurrent trueup commits
    with different ``(anchor_date, anchor_balance)`` produce two rows, both
    legitimate; the resolver selects the latest by
    ``(anchor_date, created_at)`` DESC, so the last writer's row wins on
    display while neither is lost.  The cash path used to differ -- it
    carried a ``STALE_CONFLICT`` outcome and a 409 -- and ruling R-EN deleted
    that, on the ground that this contract had documented since Commit 16.

    **What that ruling did NOT inherit from here is a concurrency guarantee,
    because there was none to inherit.**  Both paths then re-sync the posted
    ledger, and a re-sync is a read-modify-write with no unique index behind
    it; nothing serialised this one between Commit 16 and X-f1c3c.  It is
    serialised now, by the per-owner lock the reconcile takes for itself
    (:mod:`app.services.user_write_lock`).

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
        same-day INSERT.  Those are the only two members the enum has
        carried since ruling R-EN deleted ``STALE_CONFLICT`` (plan step
        X-f1c3c), so the two anchor paths now return the same pair.

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
