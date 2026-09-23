"""
Shekel Budget App -- Loan Anchor Service

The loan half of the anchor true-up service: every door that appends a
:class:`LoanAnchorEvent` -- the dashboard's dated balance true-up
(:func:`apply_loan_anchor_true_up`, a ``user_trueup``) and its tracking-start
form (:func:`record_loan_tracking_start`, a ``tracking_start``).  Both return
the :class:`~app.services.anchor_service.AnchorTrueUpOutcome` enum the cash
door's :class:`~app.services.anchor_service.AnchorTrueUpReport` carries, so
the route layer's response composition is uniform across account kinds.

**Split from :mod:`app.services.anchor_service` at plan step
``recurrence:R20``**, the moment that step's third loan door pushed the module
past pylint's 1,000-line ceiling.  The cut is the seam the module's own
docstring had always drawn -- "the checking-anchor path" and "the loan-anchor
path", one table each, one mutation set each -- rather than the line count:
what a CASH assertion decides (an observation day, an amortizing-kind refusal,
the origination stager the account factory shares) is one subject, and what a
LOAN assertion decides (a per-source governing compare, a genesis re-sync in
every scenario) is another.  Nothing here changed in the move; the shared
contract both halves hold -- append-only rows, ruling **R-EQ**'s
"refused only when it changes nothing", the per-owner write lock taken before
the first read -- is stated once, in :mod:`app.services.anchor_service`'s
docstring, and holds here unchanged.

A loan trueup never mutates ``LoanParams``: the balance seam reads the latest
event to derive the displayed current balance, monthly payment, schedule and
payoff date, so a new event immediately changes every loan surface
consistently without writing a column.

The third door is the loan SETUP door's, :func:`stage_loan_tracking_start`: it
stages a ``tracking_start`` row WITHOUT the re-sync and commit, so the balance
the owner states at setup is recorded as the assertion it is inside the
transaction that writes the params (plan step ``recurrence:R20``, ruling
**R-R72** part 3).  All three construct the row in one place,
:func:`_stage_loan_anchor`.

Services boundary: no Flask imports.  The route owns the response rendering;
this module returns an outcome enum the route translates into a flash and a
redirect.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_anchor_event import LoanAnchorEvent
from app.services import loan_posting_service
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.user_write_lock import lock_user_writes

logger = logging.getLogger(__name__)


def _governing_loan_anchor(
    account_id: int, source_id: int, anchor_date: date,
) -> LoanAnchorEvent | None:
    """Return the event of ``source_id`` governing ``anchor_date``.

    The loan twin of :func:`app.services.cash_ledger.governing_anchor_on`, and
    the WRITER's question rather than a reader's (ruling **R-EQ**, plan step
    X-f1c4b): of the rows this door could have written for this date, which one
    stands.

    **The ``anchor_date <=`` bound is the whole point and was missing from the
    first version of this step.**  Ordering by date alone answers "the latest
    event of this source", so a submission for an EARLIER date could never
    compare equal and every double-click on a back-dated correction appended a
    duplicate -- reproduced twice, independently, against this door, which is
    the one that has had a user-supplied date field since Commit 16.  A
    submission for date D can only change what is true at or after D.

    **It is not a second copy of the resolver's latest-anchor rule.**  The
    resolver answers over :func:`app.services.loan_loaders.load_loan_anchor_facts`
    -- every source PLUS the synthesized origination, which has no stored row and
    can never be the thing a submission duplicates.  Sharing a query between the
    two would mean filtering the reader's synthesized fact back out, which is
    more coupling than the four lines it would save.  **Both now break a tie the
    SAME way, and this door is where that rule was already right**: its
    ``(anchor_date, created_at, id)`` DESC was the only TOTAL anchor ordering
    until plan step X-an-b gave the read path the ``id`` term too (finding
    **N-196**).  ``id`` is load-bearing -- ``created_at`` is evaluated at
    TRANSACTION START, so two rows written together share an instant.

    Args:
        account_id: The loan account whose anchors to search.
        source_id: The ``ref.loan_anchor_sources`` id to scope to (see
            :func:`_append_loan_anchor_and_sync` for why the scope is per
            source).
        anchor_date: The date the submission asserts for -- the comparison's
            horizon.

    Returns:
        The governing :class:`LoanAnchorEvent`, or ``None`` when the account has
        no stored anchor of that source at or before *anchor_date* -- in which
        case the submission is necessarily new.
    """
    return (
        db.session.query(LoanAnchorEvent)
        .filter(
            LoanAnchorEvent.account_id == account_id,
            LoanAnchorEvent.source_id == source_id,
            LoanAnchorEvent.anchor_date <= anchor_date,
        )
        .order_by(
            LoanAnchorEvent.anchor_date.desc(),
            LoanAnchorEvent.created_at.desc(),
            LoanAnchorEvent.id.desc(),
        )
        .first()
    )


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

    Stages ONE row on the append-only :class:`LoanAnchorEvent` table (through
    :func:`_stage_loan_anchor`, the one constructor site), then re-syncs the
    loan's genesis postings in EVERY scenario (the anchor is per-account, not
    per-scenario) via
    :func:`app.services.loan_posting_service.sync_loan_postings_all_scenarios` --
    which re-runs the running-balance walk so payments re-split from the new
    anchor.  The just-added event becomes visible to that walk because the sync's
    first query autoflushes it (load-bearing -- must NOT run under
    ``session.no_autoflush``).

    **Whether there is anything to append is decided in the staging core, by
    ruling R-EQ**, and the decision is the checking door's rule on this table:
    take the owner's write lock, read the event that currently GOVERNS, append
    only when the submission differs.  It replaced
    ``loan_posting_service.sync_all_scenarios_or_duplicate`` on this path (that
    helper survives for the ARM rate change, whose table is EDITABLE and whose
    unique key is therefore a real business rule rather than an idempotency
    guess) and with it the partial expression index
    ``uq_loan_anchor_events_acct_date_bal_day``.

    **"Governing" is scoped per SOURCE here and not on the checking side, and
    that difference is the tables', not a drift.**  A ``tracking_start`` and a
    ``user_trueup`` are DISTINCT FACTS even at the same date and balance:
    :func:`app.services.loan_loaders.load_loan_anchor_facts` loads both as
    assertions differing only in ``is_tracking_start``, and
    ``loan_posting_service.loan_balance_anchor_history`` renders that label on
    the loan dashboard's drift card.  A cross-source comparison would answer
    UNCHANGED to a ``tracking_start`` because a same-valued ``user_trueup``
    stands, silently dropping the label the user asked to record.
    ``AccountAnchorHistory`` carries one kind of fact and needs no such split.

    *An earlier version of this paragraph justified the split by claiming a
    ``tracking_start`` is privileged as the loan's OPENING, citing
    ``loan_loaders._opening_anchor_fact``.  That function was DELETED in the
    loan arc and the live loader states the opposite -- "Origination is the
    opening ALWAYS", and a ``tracking_start`` "RESETS the running balance at its
    own date like any true-up".  A neutral review caught it; the split is right
    and the reason was not.*

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
        ``COMMITTED`` when the event was written and committed; ``UNCHANGED``
        when the submission matched the governing event of its own source, in
        which case nothing was written and the session was rolled back.
    """
    if not _stage_loan_anchor(
        account=account, anchor_balance=anchor_balance,
        anchor_date=anchor_date, source=source,
    ):
        # See the cash door: the id is read before the rollback expires it.
        account_id = account.id
        db.session.rollback()
        logger.info(
            "Loan anchor (%s) for account %d on %s asserts the balance that "
            "already stands; nothing written (idempotent success)",
            source.value, account_id, anchor_date,
        )
        return AnchorTrueUpOutcome.UNCHANGED

    loan_posting_service.sync_loan_postings_all_scenarios(account.id)
    db.session.commit()
    return AnchorTrueUpOutcome.COMMITTED


def _stage_loan_anchor(
    *,
    account: Account,
    anchor_balance: Decimal,
    anchor_date: date,
    source: LoanAnchorSourceEnum,
) -> bool:
    """Stage one :class:`LoanAnchorEvent` of ``source`` unless it already stands.

    The ONE place a loan anchor row is constructed, and the ONE place ruling
    R-EQ's duplicate rule is applied: take the owner's write lock, read the
    event that currently GOVERNS ``anchor_date`` for this source, and add the
    row only when the submission differs.  It neither re-syncs the posted
    ledger nor commits, because the transaction is its CALLER's:

    * :func:`_append_loan_anchor_and_sync` (the true-up and tracking-start
      doors) re-syncs every scenario and commits, or rolls back when nothing
      was staged;
    * :func:`stage_loan_tracking_start` (the loan SETUP door) leaves the
      staged row in the door's own transaction, beside the params it is an
      assertion about, and the door runs the one re-sync and the one commit
      it already runs.

    Splitting the append from the sync-and-commit is what lets the setup door
    record the assertion in the SAME transaction as the params (plan step
    ``recurrence:R20``): the committing wrapper cannot be called from inside
    a door that still has a refusal ahead of its commit, since a refusal rolls
    the whole write back.  A second constructor site in that door instead
    would have been a write inheriting none of this rule (ruling R-EQ).

    Args:
        account: An attached :class:`Account` row for the loan.  Caller owns
            the ownership check.
        anchor_balance: The validated :class:`Decimal` balance to assert
            (``>= 0`` at the schema layer, backstopped by
            ``ck_loan_anchor_events_balance_nonneg``).
        anchor_date: The date the balance is asserted for.  Caller enforces
            the source-appropriate bounds.
        source: The :class:`~app.enums.LoanAnchorSourceEnum` provenance.

    Returns:
        ``True`` when a row was added to the session; ``False`` when the
        governing event of this source already asserts exactly
        ``(anchor_date, anchor_balance)``, in which case nothing was staged.
    """
    # Ruling R-EQ: the lock precedes the read the decision is made from.  For
    # the two committing doors it is also the transaction's first lock
    # (finding N-193's ordering invariant); the setup door reaches here with
    # its params row already INSERTed, which is the order that door has
    # always had -- until plan step R20 its first taking of this lock was
    # inside the all-scenario sync, after the same insert.  The sync every
    # caller runs takes the same re-entrant lock again, harmlessly.
    lock_user_writes(account.user_id)
    source_id = ref_cache.loan_anchor_source_id(source)
    governing = _governing_loan_anchor(account.id, source_id, anchor_date)
    if governing is not None and (
        (governing.anchor_date, Decimal(str(governing.anchor_balance)))
        == (anchor_date, anchor_balance)
    ):
        return False

    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=source_id,
    ))
    return True


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
    function does NOT mutate :class:`LoanParams`: the balance has no
    column there (the demoted ``current_principal`` seed was dropped at
    plan step ``recurrence:R20``), and the immutable origination fields
    are not its business.

    **There is no stale-form conflict on either path, and since plan step
    X-f1c3c that is stated the same way for both.**  A
    :class:`LoanAnchorEvent` is an INSERT-only row with no ``version_id``
    column, and the resolver is read-only.  Two concurrent trueup commits
    with different ``(anchor_date, anchor_balance)`` produce two rows, both
    legitimate; the resolver selects the latest by ``(anchor_date, created_at,
    event_id)`` DESC (the third term is X-an-b's; without it the last writer
    won only when the two differed in day).  The cash path used to differ -- it
    carried a ``STALE_CONFLICT`` outcome and a 409 -- and ruling R-EN deleted
    that, on the ground that this contract had documented since Commit 16.

    **What that ruling did NOT inherit from here is a concurrency guarantee,
    because there was none to inherit.**  Both paths then re-sync the posted
    ledger, and a re-sync is a read-modify-write with no unique index behind
    it; nothing serialised this one between Commit 16 and X-f1c3c.  It is
    serialised now, by the per-owner lock the reconcile takes for itself
    (:mod:`app.services.user_write_lock`).

    The ``UNCHANGED`` outcome mirrors the checking-anchor semantics: when a
    request submits the ``(anchor_date, anchor_balance)`` the governing
    ``user_trueup`` already asserts, nothing is written and the caller renders
    idempotent success.  This handles network retries and double-clicks on the
    Save button.  **It was a UTC-calendar-day unique index until plan step
    X-f1c4b** (ruling R-EQ), which refused a re-assertion of a balance that had
    since been superseded on the same recording day and reported it as saved.

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
        written and the commit succeeded; ``UNCHANGED`` when the submission
        asserts what the governing ``user_trueup`` already asserts.  Those are
        the only two members the enum has carried since ruling R-EN deleted
        ``STALE_CONFLICT`` (plan step X-f1c3c), so the two anchor paths return
        the same pair.
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
    """Append a ``tracking_start`` :class:`LoanAnchorEvent` and commit.

    The mid-life-import flow: the operator started tracking an
    already-amortizing loan and asserts its real balance as of a date.  It is
    an ordinary ``is_opening=False`` balance ASSERTION that RESETS the genesis
    walk's running balance at its own date
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`); the origination
    fields on :class:`LoanParams` are untouched.  *It is NOT the loan's OPENING,
    and this said it was until plan step X-an-b*, citing
    ``loan_loaders._opening_anchor_fact`` -- deleted by step C1 along with the
    behaviour.  Origination is the opening ALWAYS: opening at a mid-life
    tracking-start read the loan out of existence for its whole pre-tracking
    window (finding B-11).  *Nor need it precede the loan's recorded payments,
    and the route refused one that did not until plan step ``recurrence:R20``*
    (ruling **R-R72** part 3): an assertion after payments is exactly what a
    true-up already is, the two sources differ in label alone, and the walk
    resets on both identically.

    Shares the append + all-scenario re-sync + duplicate rule of
    :func:`apply_loan_anchor_true_up` via :func:`_append_loan_anchor_and_sync`;
    the only difference is the anchor source, which is also the scope the
    duplicate rule compares within.  Like a true-up it never mutates
    :class:`LoanParams`.

    Args:
        account: An attached :class:`Account` row for the loan.  Caller is
            responsible for the ownership check and for confirming the account
            carries ``has_amortization=True``.
        anchor_balance: The validated :class:`Decimal` opening balance
            (``>= 0`` at the schema layer).
        anchor_date: The date the balance is asserted for.  Caller is
            responsible for enforcing ``origination_date <= anchor_date`` and
            ``anchor_date <= today``.

    Returns:
        ``COMMITTED`` on a new committed event; ``UNCHANGED`` when the
        submission asserts what the governing ``tracking_start`` already asserts
        (idempotent success).  The comparison is scoped to this source, so a
        re-submitted tracking-start is recognised even when true-ups have been
        recorded after it -- see :func:`_append_loan_anchor_and_sync`.
    """
    return _append_loan_anchor_and_sync(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
        source=LoanAnchorSourceEnum.TRACKING_START,
    )


def stage_loan_tracking_start(
    *,
    account: Account,
    anchor_balance: Decimal,
    anchor_date: date,
) -> bool:
    """Stage a ``tracking_start`` :class:`LoanAnchorEvent` in the caller's transaction.

    The loan SETUP door's entry (plan step ``recurrence:R20``, ruling
    **R-R72** part 3, finding **REC-519**): the balance the owner states at
    setup IS a dated assertion, and the door records it as one beside the
    :class:`LoanParams` row it is an assertion about -- same transaction, one
    ledger re-sync, one commit, and a refusal later in the door rolls both
    back together.  Until R20 the form required that balance and stored it in
    ``LoanParams.current_principal``, which nothing read: a loan configured
    mid-life then had only its synthesized origination assertion, and under
    ruling R-R71 every unrecorded month since origination read as unpaid.

    Stages and returns; it does NOT re-sync the posted ledger and does NOT
    commit.  The door runs ``sync_loan_postings_all_scenarios`` after this
    exactly as it did before, so the genesis walk folds the assertion the
    first time it runs, and commits once its own remaining refusals have
    passed.  Shares :func:`_stage_loan_anchor` with the two committing doors,
    ruling R-EQ's duplicate rule included; at setup no ``tracking_start`` can
    already stand (anchor rows are written only for a configured loan, and
    the account's history is cascade-deleted with it), so the rule is
    structurally idle here and the return is documented rather than acted on.

    Args:
        account: An attached :class:`Account` row for the loan being
            configured.  Caller owns the ownership check.
        anchor_balance: The validated :class:`Decimal` balance stated
            (``>= 0`` at the schema layer).
        anchor_date: The date the balance is stated for.  Caller enforces
            ``origination_date < anchor_date <= today``: a loan originating on
            or after the stated date asserts nothing, its origination IS the
            assertion, and the caller does not reach this function.

    Returns:
        ``True`` when the row was staged; ``False`` when the governing
        ``tracking_start`` already asserts this ``(date, balance)``.
    """
    return _stage_loan_anchor(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
        source=LoanAnchorSourceEnum.TRACKING_START,
    )
