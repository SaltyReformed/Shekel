"""The ONE place a match is recorded, and the only place here that MOVES MONEY.

An accepted match asserts that a set of bank lines and a set of the app's own
rows are ONE movement.  Two things follow from that assertion and this module
does both:

* every member row takes the bank's posted day, which SETTLES a row still
  Projected and CORRECTS one whose recorded day was wrong;
* the correspondence itself is recorded, so a re-import does not re-propose it,
  an undo has something to delete, and plan steps ``balance:X-f3a-2`` and
  ``balance:X-f3c`` have the provenance ruling **R-FT** promised them.

**It does NOT write ``reconciled_by_id``, and that is ruling R-FV.**  That
column names an ``account_anchor_history`` row -- a balance the owner asserted
by hand -- and a bank line is not one.  What it records, *which declared
balance already contains this row*, is DERIVABLE from the match once a
statement carries the line, where the match is not derivable from it; so this
door stores the fact and leaves the derivation alone.  The settle doors it
calls RELEASE any prior link as they move the day, which is correct: that link
recorded a statement showing this money on a day the bank has just contradicted.

**RESOLVING and RECORDING are two acts, split in prose at plan step
``bank_import:X-f6a-3c-2`` and in the FILE LAYOUT at ``bank_import:X-f6d-3``.**
:func:`~._resolve.resolve_rows` turns a submission into priced rows under the
owner's own scope and lives in :mod:`._resolve`; :func:`record_match` takes
rows a caller already holds and writes the match.  :func:`accept_match` is the
form door and does both.  Nothing crossed the seam when it became two files --
the resolution half called nothing here and this half calls nothing there --
so what moved is the boundary the paragraph already claimed.

**The split is not tidiness**: ``_create`` creates a purchase
and then records a match naming it, and a row an act has just created cannot be
in a scope derived before it -- measured, all 91 of the developer's creatable
lines were refused as "no longer available to match" the first time a whole
statement ran against one shared derivation.  What that door needed was never a
scope proof (it built the row itself) but the recording, so it now calls the
recording.  **There is still exactly ONE function that writes a match**, which
is what rulings **R-FT** and **R-FV** actually ask for.

**RESOLVING, RECORDING and the MONEY GAP are three subjects in three files.**
:mod:`._resolve` refuses what a submission may not NAME; this module records
the correspondence and moves the days; and :mod:`._variance` owns everything
about the two sides disagreeing -- measuring the gap, refusing the gaps that
cannot be honestly recorded, and the two ways of recording the ones that can
(write the bank's figure to the one row it names, or mint the member a group
was missing).  The third file is plan step ``bank_import:X-f6d-4``'s, and the
seam is a subject rather than a line count: every function there reads the two
SUMS, and nothing here does.

**FOUR refusals live in this module.**  Two are about the submission's SHAPE
-- a side with nothing in it, and an envelope named beside a purchase inside
it -- and neither reads a figure.  The third is
:func:`_reject_drifted_under_the_act`, which is about what the act's OWN
writes did to a member's price.  The fourth belongs to :func:`release_match`:
an id naming no act of this owner's, and a created row the owner has edited
since.  *(The count is stated because this arc has shipped a taxonomy that did
not add up before; a fifth added here is what has to change this sentence.
:mod:`._resolve` and :mod:`._variance` each state their own.)*

**Every refusal fires before anything is written.**  The ids are re-derived
under the owner's own scope (:mod:`._resolve`) and reconciled with the state
the screen showed, the two sides are checked against each other
(:mod:`._variance`), and only then does a settle verb run -- so a refused match
leaves the database exactly as it was without depending on the rollback, the
same discipline ``statement_import.record_statement`` states for itself.

**What is ALREADY CLAIMED is read by the ACT, never by the scope.**  Every
refusal here that asks "is this already matched" takes a
:class:`~._candidates.MatchedSubjects` its caller read for this act alone, so a
batch applying 215 items cannot hand its fourth item a row its third has just
claimed.  One read serves the line refusal, the row refusal and the
parent/child guard, where there were two queries answering the same question.

**This door applies no DATE bound, and that asymmetry is deliberate.**  The
proposer refuses to OFFER a pairing outside the row's own window
(``_pairing.within_window``, ruling **R-FY**); the hand-build form on the
review screen exists precisely so an owner may assert a grouping the proposer
would not guess, so refusing one here on a date would refuse the act ruling
**R-FP** reserves to them.  What this door does enforce is the SCOPE: every id
is resolved against the pass's own offer set (:class:`~._scope.ReviewScope`), so
no request can reach a row the screen could not have shown.  Stated because an
adversarial review read the missing bound as an oversight 2026-08-19, which is
what an unstated deliberate asymmetry looks like.

**The settle verbs are the app's own, never restated.**  An ordinary row goes
through ``transaction_service.apply_requested_status``, the route layer's one
status entry point; a transfer SHADOW through ``transfer_service``, because
``CLAUDE.md`` transfer invariant 4 admits no direct mutation of one and
``settle_transaction`` refuses it outright; a purchase through
``entry_service.update_entry``.  A matcher that stamped ``settled_on`` itself
would be a fourth settle door, which is exactly what ruling **R-FA** exists to
prevent.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.enums import SettledDayBasisEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    entry_service,
    posting_service,
    transaction_service,
    transfer_service,
)
from app.services.settle_day import SettleDay
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_MATCHED,
    EVT_STATEMENT_MATCH_RELEASED,
    log_event,
)

from ._candidates import MatchedSubjects, matched_subjects, repriced
from ._offers import (
    CandidateRow,
    MatchDays,
    RowKind,
    corrected_purchase_day,
)
from ._sides import MatchSides
from ._variance import (
    bank_cash_for,
    corrected_figure,
    mint,
    reject_unrecordable,
)
from ._resolve import load_lines, resolve_rows
from ._scope import ReviewScope
from ._submission import MatchSubmission

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcceptedMatch:  # pylint: disable=too-many-instance-attributes
    """What accepting one match did.

    Pylint: too-many-instance-attributes (9/7) -- **nine because a receipt
    for this act has nine things to say**, not because the value wants
    splitting.  The eighth is ``repriced_count``, and it is the one that made
    the panel say *"Nothing moved."* about a rewritten figure until
    2026-08-22; dropping a count to satisfy a limit is how that sentence came
    to be false in the first place.  The ninth is ``residual``, which is the
    only field here naming money the app did not have at all, and the same
    argument covers it: a receipt silent about a row this act CREATED would be
    false in the same way.  ``CandidateRow`` carries the same disable for the
    same reason.

    Attributes:
        match_id: The ``budget.statement_matches`` row recording the act.
        posts_on: The day every member row now records the money as having
            moved.
        amount: The signed figure both sides agreed on.
        settled_count: How many member rows were still Projected and are now
            settled -- the bank's evidence that money moved, applied.
        corrected_count: How many were already settled and had a day moved --
            the posting day, the PURCHASE day (ruling **R-FW**), or both.  The
            two counts partition the rows that changed; a settled row already
            carrying the bank's own posting day and needing no purchase-day
            correction is in neither, and reporting it as "corrected" would
            claim work that did not happen.
        line_count: How many bank lines the act explains.
        repriced_count: How many member rows had their FIGURE moved onto the
            bank's (ruling **R-GD(a)**, plan step ``bank_import:X-f6d-1``).
            **It cuts ACROSS the day partition exactly as ``redated_count``
            does, and for a sharper reason**: a repricing whose row already
            carried the bank's day reports ``unchanged`` on every day count, so
            without this the receipt said *"confirmed what you already had"*
            about a request that had just rewritten what a payment cost.  The
            day counts are silent there and the SENTENCE was false, which is
            worse than the silence ``redated_count`` was added to fix.  Found
            by adversarial design review 2026-08-22.
        redated_count: How many member PURCHASES had their purchase day moved
            onto the bank's (ruling **R-FW**).  **It cuts ACROSS the partition
            above rather than joining it**, and it has to: a purchase that was
            still Projected is reported as ``settled``, so without its own
            count the step's own motivating case -- six purchases typed in one
            bookkeeping session, none of them settled -- would re-date silently
            and the receipt would say only that a row was marked as having
            happened.  The two largest moves measured on the developer's own
            data, 40 and 59 days, are exactly that case.  Found by two
            independent adversarial reviews 2026-08-18.
        residual: The signed difference this act recorded as an ordinary
            uncategorized row, or ``None`` where the two sides already agreed
            (plan step ``bank_import:X-f6d-4``, ruling **R-FN**).  **A FIGURE
            rather than a count**, and the counts beside it are why: a match
            records at most one residual, so a count could only ever be 0 or
            1, and what the owner needs told is HOW MUCH money this act put
            into the Uncategorized bucket.  It is also the only field here
            naming money the app did not hold at all -- every other one
            re-dates or re-prices a row that already existed.
    """

    match_id: int
    posts_on: date
    amount: Decimal
    settled_count: int
    corrected_count: int
    line_count: int
    redated_count: int
    repriced_count: int
    residual: "Decimal | None"


def _reject_empty_side(
    lines: "list[BankStatementLine]", rows: "list[CandidateRow]",
) -> None:
    """Refuse a match missing either half.

    A match is a claim that two things are the SAME movement, so one thing is
    not a match: a bank line with no app row is what plan step
    ``bank_import:X-f6a-3b`` turns into a purchase, and an app row with no bank
    line is a row the statement did not show.

    Args:
        lines: The submitted bank lines.
        rows: The submitted app rows.

    Raises:
        ValidationError: When either side is empty.
    """
    if not lines or not rows:
        raise ValidationError(
            "A match needs at least one statement line and at least one row "
            "from your budget.  Nothing was changed."
        )


def _reject_parent_and_its_own_purchase(
    rows: "list[CandidateRow]", matched: MatchedSubjects,
) -> None:
    """Refuse an envelope and a purchase under it -- in this match OR another.

    **It would count the same money twice**, and no schema can see it.  An
    envelope's cash leg SUBTRACTS the purchases that have posted and INCLUDES
    the ones that have not (ruling **R-FM**), so naming both sums that purchase
    in two terms.

    **Both directions are checked ACROSS matches, and the cross-match half is
    the one that actually moves money.**  Within one match the two sides are
    priced together and refuse together.  Across two, each balances on its own
    and the second one FALSIFIES the first: measured on a production clone,
    envelope 2280 prices at `-265.69` (its four unposted purchases included)
    and its purchase 78 at `-18.64`; matching 2280 first and 78 second stamps
    78's posting day, which drops 2280's leg to `-247.05` -- so two matched
    line-sets worth `-284.33` are backed by `-265.69` of ledger and the
    projected balance reads `$18.64` HIGH.  The screen's hand-build form lists
    an envelope and its purchases side by side, so it is two clicks.  Found by
    adversarial financial review 2026-08-17.

    **The cross-match half takes the claims its ACT read** (plan step
    X-f6a-3c-2).  It ran its own query over ``statement_match_members`` until
    this step, which was a second answer to the question
    :func:`~._candidates.matched_subjects` already answers -- and the same read
    now also decides which rows and lines are still available, so all three
    refusals see one state.

    **It is NOT what keeps a batch's prices honest**, and a first draft of this
    step said it was.  That argument -- *the only way one item can move a row
    another item names is by adding to or posting a purchase under it* -- was
    measured false by adversarial financial review 2026-08-19 on a SIBLING
    write (``sync_entry_payback``); the answer is that every act re-prices the
    rows it names (:func:`~._candidates.repriced`), and this guard is left to
    do the one job it can actually do.

    Args:
        rows: The submitted app rows, already priced.
        matched: What this account's matches have already claimed, as of this
            act.

    Raises:
        ValidationError: When a submitted purchase's parent is submitted or
            already matched, or a submitted envelope holds a purchase that is.
    """
    transaction_ids = {
        row.row_id for row in rows if row.kind is RowKind.TRANSACTION
    }
    entry_ids = {row.row_id for row in rows if row.kind is RowKind.PURCHASE}
    if not transaction_ids and not entry_ids:
        return
    clash = db.session.query(TransactionEntry.id).filter(
        db.or_(
            db.and_(
                TransactionEntry.id.in_(entry_ids or {0}),
                TransactionEntry.transaction_id.in_(
                    (transaction_ids | matched.transactions) or {0},
                ),
            ),
            db.and_(
                TransactionEntry.id.in_(matched.entries or {0}),
                TransactionEntry.transaction_id.in_(transaction_ids or {0}),
            ),
        )
    ).first()
    if clash is not None:
        raise ValidationError(
            "This match would count the same money twice: it names an "
            "envelope and a purchase inside it -- here, or through a match "
            "you have already accepted.  The envelope's figure already covers "
            "its own purchases.  Match the envelope OR its purchases, not "
            "both.  Nothing was changed."
        )


def _apply_day(
    row: CandidateRow, owner_id: int, days: "MatchDays",
    figure: "Decimal | None" = None,
) -> str:
    """Move one member row onto the bank's days AND figure through its own door.

    The dispatch, and every arm is an existing verb rather than a column write:

    * a PURCHASE stamps its posting day through ``entry_service.update_entry``,
      which refuses a future day and releases the row's clearing link -- and
      takes the bank's own transaction day in the SAME call where the app's
      recorded purchase day is refuted (ruling **R-FW**, see
      :func:`~._offers.corrected_purchase_day`);
    * a transfer SHADOW goes through ``transfer_service`` -- ``settle_transfer``
      when it is still Projected, ``update_transfer`` when only the day moves,
      because a settled transfer is an idempotent no-op for the first;
    * every other transaction goes through
      ``transaction_service.apply_requested_status``, with the row's OWN status
      when it is already settled (an edit that changes only the day is an
      identity transition) and its type's settled status when it is not.

    Args:
        row: The member being moved.
        owner_id: The user the route proved owns the account.
        days: The days the bank states for this match.
        figure: What the bank says this row is worth
            (:func:`corrected_figure`), or ``None`` where the bank's figure
            names no single row or already agrees.  **It rides the SAME call
            as the day** for the reason the purchase's two dates already do:
            each settle door validates the state it is asked to produce, so
            submitting the figure separately would offer it an intermediate
            row the door would rightly refuse.

    Returns:
        ``"settled"`` when the row entered the settled band, ``"corrected"``
        when it was already settled on a different day, ``"unchanged"`` when it
        already carried the bank's own day.  **``"unchanged"`` is about the DAY
        and not about whether anything was written** (plan step X-az): a row
        already carrying the bank's day on a weaker basis has that basis raised
        to ``observed``, which moves no day and so is not a correction.

    Raises:
        ValidationError: From a settle door -- a future day, a posting day
            before its purchase, an illegal transition.  Surfaced to the owner.
        PostingError: From a ledger reconcile.  Fails loud.
    """
    # **"unchanged" requires the row to be SETTLED as well as correctly
    # dated.**  Deciding on the day alone would let a Projected row carrying
    # the bank's own day be recorded as matched and left Projected -- the bank
    # line would read explained while the money was never booked.  The status
    # seam should make that state unreachable (it refuses a day on a
    # non-settled status and clears the column on the way out), but no CHECK
    # pairs the two columns, so the arm does not rest on that discipline.
    posts_on = days.posts_on
    purchase_day = corrected_purchase_day(row, days)
    outcome = (
        "unchanged" if row.is_settled and row.settled_on == posts_on
        and purchase_day is None
        else "corrected" if row.is_settled
        else "settled"
    )
    # **An "unchanged" row is still written when the bank CONFIRMS a day the app
    # only had a BOUND for** (plan step **X-az**, finding **N-332**).  The
    # reconcile panel records the day a BALANCE was asserted for -- the money
    # moved on or BEFORE it -- and a bank line posted on exactly that day turns
    # the bound into an observation.  Nothing else in the app can make that
    # write: no settle door fires when the day does not move, so before this
    # step such a row kept reporting itself a bound forever.  The DAY is
    # unchanged, so the outcome the caller counts stays ``"unchanged"`` and
    # neither the settled nor the corrected tally moves; what changes is the
    # stored answer to "how is this day known".
    #
    # It writes through the row's own settle door rather than assigning the
    # column, exactly as the other arms do, so the basis keeps the single writer
    # ``settled_on`` has.  Each door compares the resulting DAY with the stored
    # one to decide whether to release the clearing link, and the day is equal
    # here -- so a confirmation strengthens the observation the link records
    # instead of dropping it.
    if (
        outcome == "unchanged"
        and row.settle_day_basis is SettledDayBasisEnum.OBSERVED
        and figure is None
    ):
        return outcome

    settle_day = SettleDay(day=posts_on, basis=SettledDayBasisEnum.OBSERVED)

    if row.kind is RowKind.PURCHASE:
        # ONE call, both days, because ``update_entry`` checks the RESULTING
        # pair: submitting them separately would offer the door an intermediate
        # state where the posting day precedes the purchase day, and it would
        # rightly refuse the very correction that fixes it.
        moves = {"settle_day": settle_day}
        if purchase_day is not None:
            moves["purchased_on"] = purchase_day
        if figure is not None:
            moves["amount"] = figure
        entry_service.update_entry(row.row_id, owner_id, **moves)
        return outcome

    if row.transfer_id is not None:
        if row.is_settled:
            transfer_service.update_transfer(
                row.transfer_id, owner_id, settle_day=settle_day,
            )
        else:
            transfer_service.settle_transfer(
                row.transfer_id, owner_id, settle_day=settle_day,
            )
        return outcome

    txn = db.session.get(Transaction, row.row_id)
    target_status_id = (
        txn.status_id if row.is_settled
        else transaction_service.settled_status_id(txn)
    )
    transaction_service.apply_requested_status(
        txn, target_status_id, settle_day=settle_day, submitted=figure,
    )
    return outcome


@dataclass(frozen=True)
class _Moved:
    """What applying a match's days and figures to its member rows did.

    Three counts derived in one pass over the members, because each of them
    has to be read BEFORE the writes that make it false and reading them apart
    would be three passes over one question.

    Attributes:
        outcomes: One of ``"settled"`` / ``"corrected"`` / ``"unchanged"`` per
            member, in the order they were moved -- see :func:`_apply_day`.
        redated_count: How many member purchases had their PURCHASE day
            corrected (ruling **R-FW**).
        repriced_count: How many members took the bank's own figure (ruling
            **R-GD(a)**).
    """

    outcomes: "list[str]"
    redated_count: int
    repriced_count: int


def _move_members(
    scope: ReviewScope,
    rows: "list[CandidateRow]",
    bank_cash: "Decimal | None",
    days: MatchDays,
) -> _Moved:
    """Move every member row onto the bank's days and figure, and count it.

    **The purchases move first**, for the reason
    ``reconcile_service.record_reconciliation`` states for its own order: a
    purchase's posting day changes what its parent envelope's cash leg is worth
    (ruling **R-FM**), so settling a parent first and stamping its purchase
    afterwards would book the parent at a figure the purchase then moves.
    :func:`_reject_parent_and_its_own_purchase` makes that pairing unreachable
    in ONE match and across matches alike, so no submission this door accepts
    can actually hit the interaction today.  **The order is kept anyway and the
    reason is stated rather than invented**: a first draft justified it by "the
    parent is in a different match accepted in the same request", which cannot
    happen -- one POST accepts exactly one match.  What the order really buys
    is that the rule survives the guard: if a later step widens what a match
    may name, the sequence is already the safe one rather than something that
    has to be rediscovered.

    Args:
        scope: The pass, for the owner every settle door is scoped by.
        rows: The submitted app rows, already priced.
        bank_cash: What the bank says the ONE row this match names is worth,
            or ``None`` where the difference names no single row
            (:func:`~._variance.bank_cash_for`).  Taken rather than derived,
            because the caller decides the two remedies by the same answer.
        days: The days the match writes.

    Returns:
        Its :class:`_Moved`.

    Raises:
        ValidationError: From a settle door -- a future day, a posting day
            before its purchase, an illegal transition.
        PostingError: From a ledger reconcile.  Fails loud.
    """
    ordered = sorted(
        rows, key=lambda row: (row.kind is not RowKind.PURCHASE, row.row_id),
    )
    # Read BEFORE the writes: once `_apply_day` has moved a purchase onto the
    # bank's day the predicate no longer holds, so counting afterwards would
    # report zero every time.
    redated_count = sum(
        1 for row in ordered if corrected_purchase_day(row, days) is not None
    )
    # Read BEFORE the writes, exactly as ``redated_count`` is and for the same
    # reason: once a settle door has taken the bank's figure the row agrees
    # with it, so counting afterwards would report zero every time.
    figures = [corrected_figure(row, bank_cash) for row in ordered]
    return _Moved(
        outcomes=[
            _apply_day(row, scope.owner_id, days, figure)
            for row, figure in zip(ordered, figures, strict=True)
        ],
        redated_count=redated_count,
        repriced_count=sum(1 for figure in figures if figure is not None),
    )


def _reject_drifted_under_the_act(
    scope: ReviewScope,
    lines: "list[BankStatementLine]",
    members: "list[CandidateRow]",
    sides: MatchSides,
) -> None:
    """Refuse a match whose own writes moved a member's price out from under it.

    **This is what makes "the identity holds BY CONSTRUCTION" a fact rather
    than an argument** (plan step ``bank_import:X-f6d-4``).  The two sides are
    measured before any settle verb runs -- they have to be, because the
    refusals are about what the owner submitted -- and the difference recorded
    for a group is that measurement.  If applying the act then moves a member's
    figure, the recorded difference no longer closes the gap and the match is
    a set of rows that does not add up to the lines it explains.

    **A settle verb CAN move a sibling, and this package has measured it.**
    ``entry_service.update_entry`` re-derives the envelope's CC Payback through
    ``sync_entry_payback`` and WRITES its ``estimated_amount``; that payback is
    a candidate on the same account and a SIBLING of the purchase rather than
    its parent, so :func:`_reject_parent_and_its_own_purchase` cannot see it.
    ``_scope`` and ``_resolve`` both record that measurement (2026-08-19) as
    the reason a price is re-read per ACT; this is the same answer applied
    WITHIN one.

    **Total rather than enumerated, deliberately.**  The alternative is a list
    of "which writes can move which rows", and ``_resolve`` has already stated
    why that is the wrong shape: *enumerating sibling writes is a guard the
    next unenumerated writer reopens*.  Re-reading every member costs one pass
    over the one to four rows an act names.

    **It is a designed refusal rather than a loud failure**, because it is
    reachable from an ordinary submission: the hand-build form offers a debit
    purchase and its envelope's payback side by side.  A refusal rolls the item
    back and leaves nothing behind, which is the honest outcome for an act this
    door cannot honour.

    Args:
        scope: The pass, for the calendar and basis the re-pricing needs.
        lines: The bank lines the match explains.
        members: Every row the match will record, INCLUDING one this act
            minted -- the minted row is exactly what is supposed to close the
            gap, so a check that left it out would grade the wrong set.
        sides: What the two halves came to before the act ran.

    Raises:
        ValidationError: When the members no longer come to the lines' total.
    """
    fresh = [
        repriced(member, scope.calendar, scope.basis) for member in members
    ]
    if any(member is None for member in fresh):
        raise ValidationError(
            "One of the rows in this match stopped being priceable while it "
            "was being applied.  Reload the page and try again; nothing was "
            "changed."
        )
    after = MatchSides.of(lines, fresh)
    if after.difference:
        raise ValidationError(
            f"Applying this match moved one of its own rows: it was reviewed "
            f"against {sides.app:+,.2f} and settling it left "
            f"{after.app:+,.2f}, which no longer explains the "
            f"{after.bank:+,.2f} your bank shows.  Reload the page and match "
            f"the rows one at a time; nothing was changed."
        )


def _record(
    owner_id: int,
    account_id: int,
    lines: "list[BankStatementLine]",
    rows: "list[CandidateRow]",
    created: "CandidateRow | None" = None,
) -> StatementMatch:
    """Stage the match act and one member per subject.

    Args:
        owner_id: The user the act belongs to.
        account_id: The account both sides belong to.
        lines: The bank lines it explains.
        rows: The app rows it names.
        created: The one member THIS ACT brought into existence, or ``None``
            where every subject already existed.  Its member records the
            subject's revision as it stands now
            (``statement_match_members.created_version_id``), which is what
            lets :func:`release_match` remove a row nobody has touched and
            refuse one the owner has since made their own.

    Returns:
        The staged, flushed :class:`~app.models.statement_match.StatementMatch`.
    """
    match = StatementMatch(account_id=account_id, user_id=owner_id)
    db.session.add(match)
    # The members carry the act's id in a composite key, so the act must exist
    # before they are staged.
    db.session.flush()
    for line in lines:
        db.session.add(StatementMatchMember(
            match_id=match.id, account_id=account_id,
            bank_statement_line_id=line.id,
        ))
    for row in rows:
        db.session.add(StatementMatchMember(
            match_id=match.id,
            account_id=account_id,
            transaction_id=(
                row.row_id if row.kind is RowKind.TRANSACTION else None
            ),
            transaction_entry_id=(
                row.row_id if row.kind is RowKind.PURCHASE else None
            ),
            # Compared by IDENTITY rather than by id, because a created
            # subject is the same VALUE the caller minted and no id
            # comparison could tell it from a submitted row of the same kind
            # that happened to share one.
            created_version_id=(
                row.version_id if created is not None and row is created
                else None
            ),
        ))
    db.session.flush()
    return match


def record_match(
    scope: ReviewScope,
    lines: "list[BankStatementLine]",
    rows: "list[CandidateRow]",
    matched: MatchedSubjects,
    residual: "Decimal | None" = None,
) -> AcceptedMatch:
    """Record that these bank lines ARE these app rows, and move the day.

    **The ONE function that writes a match**, and the half of the old
    ``accept_match`` that does not care where its rows came from (plan step
    X-f6a-3c-2).  Its two callers hold their rows for different reasons:
    :func:`accept_match` resolved them from submitted ids under the pass's
    scope, and :func:`~._create.create_purchase_from_line` just created the one
    it names.  Both reach the same guards, the same day derivation, the same
    settle verbs and the same record, which is what rulings **R-FT** and
    **R-FV** ask for.

    The order its refusals have to happen in: both sides are checked for
    presence, for the double-count pairing and for what their difference means
    (:func:`~._variance.reject_unrecordable`), and only then does any settle
    door run.  The order the MEMBERS move in is :func:`_move_members`'.

    **A GROUP's difference is a MEMBER this function mints**, not an exception
    to the balance it checks (plan step ``bank_import:X-f6d-4``, ruling
    **R-FN**).  Where several rows explain one line and fall short of it, the
    shortfall is money the bank moved that no row of the owner's accounts for,
    so it is recorded as an ordinary uncategorized row and joins the match --
    and ``Sigma(lines) == Sigma(members)`` then holds BY CONSTRUCTION rather
    than by a refusal.  It is minted AFTER every refusal has fired, so the
    module's own promise that a refused match leaves the database exactly as it
    was is kept without depending on the batch's savepoint.

    **The minted row does NOT go through** :func:`_apply_day`, and that is
    about the RECEIPT rather than about the write.  It is born on the bank's
    own day, so passing it through would report it as one more row "marked as
    having happened" -- claiming the bank's evidence was applied to a record
    the owner already had, when this act is the only reason the record exists.
    It settles through the same verb ``_apply_day`` uses
    (:func:`~._variance.mint`), so there is still no fourth settle door, and it
    is reported as its own figure on :attr:`AcceptedMatch.residual`.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        scope: The pass, which is the ONE statement of whose account this act
            is on and which carries the pay calendar a minted residual is
            placed by.  **It was ``owner_id`` and ``account_id`` until plan
            step X-f6d-4**: both callers already held a scope and passed its
            two fields, so the pair was a second spelling of it that a caller
            could get out of step with the rows it priced.
        lines: The bank lines this match explains, already scoped by
            :func:`load_lines`.
        rows: The app rows that explain them, already priced -- resolved by
            :func:`resolve_rows` or built by the door that created one.
        matched: What this account's matches have already claimed, as of this
            act.
        residual: The difference the owner reviewed and agreed to record, or
            ``None`` -- which is what every caller but the form door passes,
            because a door that BUILT its row built it at the bank's own
            figure and has no difference to explain.

    Returns:
        The :class:`AcceptedMatch`.

    Raises:
        ValidationError: On any of this function's refusals or a settle door's.
            A 400: every one of them is reachable by an ordinary owner working
            from a stale page.
        PostingError: From a ledger reconcile, on a broken invariant.  Fails
            loud rather than rendering as a designed refusal, and in a batch it
            fails the WHOLE request rather than one item (:mod:`._batch`).
    """
    _reject_empty_side(lines, rows)
    _reject_parent_and_its_own_purchase(rows, matched)
    # ONE derivation of what the two halves come to, for the whole act -- the
    # refusal below and the residual it may let through are the same
    # subtraction, and summing money twice on the two sides of a gate is this
    # arc's own root cause 1.
    sides = MatchSides.of(lines, rows)
    reject_unrecordable(rows, sides, residual)

    # THE LATEST bank day for the posting, the EARLIEST stated day for the
    # purchase -- derived ONCE for the whole act, so no two members can be moved
    # onto two answers to the same question.  See :class:`MatchDays` for why the
    # two ends are opposite.
    days = MatchDays.of(lines)

    # ONE derivation of what the bank says a single named row is worth, for
    # the whole act -- and **it is what makes the two remedies exclusive**.
    # ``bank_cash_for`` answers a figure exactly where the difference is
    # attributable to one row and ``None`` exactly where it is not, so
    # correcting a row and minting a member for the same gap is unrepresentable
    # rather than merely avoided.  A first version of this step gated the mint
    # on the owner's consent alone, and a one-row match carrying one then did
    # BOTH -- the row corrected to the bank's figure and the same difference
    # booked again to Uncategorized.
    bank_cash = bank_cash_for(sides, rows)

    # **The residual's PAY PERIOD is resolved here, before any member moves**,
    # because that lookup can refuse: a line posted past the owner's last SAVED
    # pay period reaches this door (the review screen splits off only the lines
    # BEFORE the calendar opens), and a refusal raised after the settle verbs
    # had run would leave written work behind -- which this module's own
    # promise says it does not, savepoint or no savepoint.  Found by
    # adversarial financial review 2026-08-23.
    residual_period = (
        scope.period_holding(days.posts_on, "the difference on this match")
        if bank_cash is None and sides.difference
        else None
    )

    moved = _move_members(scope, rows, bank_cash, days)
    # **AFTER the member rows have moved, and that ordering is deliberate.**
    # A settle verb can still refuse -- a future day, a posting day before its
    # purchase -- and a row minted before one did would be a record of money
    # nobody accepted, left behind by an act that never happened.
    #
    # **What a settle verb writes can move a member's price, and a first
    # version of this comment claimed it could not.**  That claim is the one
    # ``_scope`` and ``_resolve`` both record as MEASURED FALSE on 2026-08-19:
    # settling a matched purchase runs ``entry_service.update_entry``, which
    # re-derives a SIBLING CC Payback's ``estimated_amount`` -- a row
    # ``_reject_parent_and_its_own_purchase`` cannot see.  So the sides are
    # re-derived below rather than argued about.  Found by two independent
    # adversarial reviews 2026-08-23.
    minted = (
        mint(sides.difference, residual_period, scope, lines, days)
        if residual_period is not None
        else None
    )
    members = rows if minted is None else [*rows, minted]
    _reject_drifted_under_the_act(scope, lines, members, sides)
    match = _record(
        scope.owner_id, scope.account_id, lines, members, created=minted,
    )

    accepted = AcceptedMatch(
        match_id=match.id,
        posts_on=days.posts_on,
        amount=sides.bank,
        settled_count=moved.outcomes.count("settled"),
        corrected_count=moved.outcomes.count("corrected"),
        line_count=len(lines),
        redated_count=moved.redated_count,
        repriced_count=moved.repriced_count,
        residual=None if minted is None else sides.difference,
    )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_MATCHED, BUSINESS,
        "A bank statement's lines were matched to the rows they explain.",
        user_id=scope.owner_id,
        account_id=scope.account_id,
        match_id=accepted.match_id,
        posts_on=days.posts_on.isoformat(),
        happened_on=days.happened_on.isoformat(),
        line_count=accepted.line_count,
        row_count=len(members),
        settled_count=accepted.settled_count,
        corrected_count=accepted.corrected_count,
        redated_count=accepted.redated_count,
        repriced_count=accepted.repriced_count,
        residual=None if minted is None else str(sides.difference),
    )
    return accepted


def accept_match(
    submission: MatchSubmission, scope: ReviewScope,
) -> AcceptedMatch:
    """Record that the SUBMITTED bank lines ARE the SUBMITTED app rows.

    The form door: it turns ids into scoped, priced subjects and hands them to
    :func:`record_match`.  Nothing a request posts can reach a row the review
    screen could not have shown, because both sides come out of the same
    *scope* the screen was rendered from and the same claims read for this act.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        submission: What the owner accepted -- the ids, the state each row was
            REVIEWED in, and the difference the screen showed for a group they
            built by hand (:attr:`~._submission.MatchSubmission.accepted_difference`).
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).
            **Required rather than defaulted**: a door that could build its own
            is a door a batch will accidentally call 215 times, which is the
            12.88 minutes plan step X-f6a-3c-2 exists to remove.  **It is also
            the ONE statement of whose account this is** -- the submission
            names only WHAT, never WHOSE, so no id here can be scoped by one
            account and written against another.

    Returns:
        The :class:`AcceptedMatch`.

    Raises:
        ValidationError: On any refusal of this door's or a settle door's.
        PostingError: From a ledger reconcile, on a broken invariant.
    """
    matched = matched_subjects(scope.account_id)
    return record_match(
        scope,
        load_lines(scope.account_id, submission.line_ids, matched),
        resolve_rows(submission, scope, matched),
        matched,
        submission.accepted_difference,
    )


def _created_rows(match: StatementMatch) -> "list[Transaction]":
    """Return the rows THIS ACT created, refusing if the owner has touched one.

    Plan step ``bank_import:X-f6d-4``, developer ruling 2026-08-23.  A group's
    difference is recorded as a row (**R-FN**), and that row means nothing
    once the grouping is released: the bank lines go back to unexplained, and
    re-accepting the same group records the difference a SECOND time.
    Reproduced by adversarial security review in two ordinary clicks -- two
    `$0.05` rows for one `$0.05` difference, the balance reading high and
    nothing naming it.

    **A row the owner has since made their own is NOT this act's to remove.**
    ``created_version_id`` is the subject's revision at the moment it was
    created, so a counter that has moved says somebody edited it -- gave it a
    category, corrected its figure, moved its day.  Deleting that would throw
    away their record in order to tidy a relation, which is the direction
    :func:`release_match` already refuses to go for a settle day.

    **The revision is the whole predicate, and that is why it is a version
    rather than a list of columns.**  "Still has no category and still holds
    the figure we recorded and still has no purchases" is three guesses about
    which edits matter; a counter that moves on every ORM update is the fact
    itself.  It also covers what nothing else would: a row nothing edited
    cannot have grown a CC payback either, because ``mark_as_credit`` writes
    the source row's own status.

    Args:
        match: The act being released, with its members loaded.

    Returns:
        The transactions to remove, in id order.

    Raises:
        ValidationError: When a created row has moved since, naming it.
    """
    created = sorted(
        (
            (member, db.session.get(Transaction, member.transaction_id))
            for member in match.members
            if member.created_version_id is not None
        ),
        key=lambda pair: pair[0].transaction_id,
    )
    for member, row in created:
        if row is not None and row.version_id != member.created_version_id:
            raise ValidationError(
                f'Undoing this match would remove "{row.name}", which it '
                f"created -- but you have edited that row since, so it is "
                f"your record now.  Delete it yourself if you want it gone, "
                f"then undo the match.  Nothing was changed."
            )
    # A subject the database has already taken cascades its member away, so a
    # ``None`` here means the row went between the read and now -- nothing to
    # remove and nothing to refuse.
    return [row for _, row in created if row is not None]


def release_match(match_id: int, owner_id: int, account_id: int) -> int:
    """Undo one match: restore the question, and remove what the act CREATED.

    **Deleting the record does NOT put the days back, and that is the honest
    direction.**  A settle day is what the app knows about when money moved,
    and the bank is still the best evidence it has; reverting one because the
    owner unlinked a record would throw away a correction in order to tidy a
    relation.  What the release restores is the QUESTION -- the bank lines
    become unexplained again and the rows become matchable again -- which is
    the repair door finding **N-302** says a refusal owes.

    **A row this act CREATED is the exception, and it is the same argument
    rather than a departure from it** (plan step ``bank_import:X-f6d-4``,
    developer ruling 2026-08-23).  A settle day is a fact about money that
    moved and survives the unlinking; a group's recorded DIFFERENCE is a fact
    about the grouping, and once the grouping is released it states nothing.
    Keeping it is not conservative, it double-counts: the bank line goes back
    to unexplained and re-accepting the same group records the difference
    again.  Reproduced in two ordinary clicks by adversarial security review.
    :func:`_created_rows` is what decides, and it refuses rather than deletes
    where the owner has edited the row since.

    **The create-a-purchase arm creates one too and is NOT removed here.**
    Whether releasing that match should take the purchase -- and the envelope
    it may have minted, and any purchases added to it since -- is plan step
    ``X-f6f``'s question, which exists to give that arm the inverse it never
    had.  It sets no ``created_version_id``, so nothing here reaches it.

    Does NOT commit -- the route owns the session boundary.

    Args:
        match_id: The act to release.
        owner_id: The user the route proved owns the account.
        account_id: The account it must belong to.

    Returns:
        How many member rows were deleted.

    Raises:
        ValidationError: When *match_id* names no act on this owner's account
            -- the set-operation form of the project's "404 for both not-found
            and not-yours" rule, raised rather than ignored because this door
            names ONE act on purpose -- or when a row this act created has
            been edited since (:func:`_created_rows`).
        PostingError: From reversing a created row's postings, on a broken
            ledger invariant.
    """
    match = (
        db.session.query(StatementMatch)
        .filter(
            StatementMatch.id == match_id,
            StatementMatch.account_id == account_id,
            StatementMatch.user_id == owner_id,
        )
        .one_or_none()
    )
    if match is None:
        raise ValidationError(
            "That match is no longer there.  Reload the page; nothing was "
            "changed."
        )
    # BEFORE anything is deleted, so a refusal leaves the act standing.
    created = _created_rows(match)
    released = len(match.members)
    db.session.delete(match)
    for row in created:
        # The ROUTE's own delete sequence, as a service: reverse the postings
        # while ``journal_entries.transaction_id`` still links them, then
        # remove the row.  A residual is always ad-hoc (it names no template),
        # so the hard delete is the arm ``delete_transaction`` takes for one.
        posting_service.reverse_postings_before_delete(row)
        db.session.delete(row)
    db.session.flush()
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_MATCH_RELEASED, BUSINESS,
        "A statement match was released; its lines are unexplained again.",
        user_id=owner_id, account_id=account_id, match_id=match_id,
        released_count=released,
        removed_count=len(created),
    )
    return released
