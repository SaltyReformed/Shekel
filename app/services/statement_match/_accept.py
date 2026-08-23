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

**RESOLVING and RECORDING are two acts, and splitting them is plan step
``bank_import:X-f6a-3c-2``.**  :func:`resolve_rows` turns submitted IDS into
priced rows under the owner's own scope; :func:`record_match` takes rows a
caller already holds and writes the match.  :func:`accept_match` is the form
door and does both.  The split is not tidiness: ``_create`` creates a purchase
and then records a match naming it, and a row an act has just created cannot be
in a scope derived before it -- measured, all 91 of the developer's creatable
lines were refused as "no longer available to match" the first time a whole
statement ran against one shared derivation.  What that door needed was never a
scope proof (it built the row itself) but the recording, so it now calls the
recording.  **There is still exactly ONE function that writes a match**, which
is what rulings **R-FT** and **R-FV** actually ask for.

**Every refusal fires before anything is written.**  The ids are re-derived
under the owner's own scope, the group is checked for balance, and only then
does a settle verb run -- so a refused match leaves the database exactly as it
was without depending on the rollback, the same discipline
``statement_import.record_statement`` states for itself.

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
    transaction_service,
    transfer_service,
)
from app.services.cash_ledger import off_statement_sum
from app.services.settle_day import SettleDay
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_MATCHED,
    EVT_STATEMENT_MATCH_RELEASED,
    log_event,
)
from app.utils.money import round_money

from ._candidates import (
    MatchedSubjects,
    matched_subjects,
    repriced,
    unmatched_rows,
)
from ._offers import (
    CandidateRow,
    MatchDays,
    MatchSubmission,
    RowKind,
    corrected_purchase_day,
)
from ._scope import ReviewScope

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcceptedMatch:  # pylint: disable=too-many-instance-attributes
    """What accepting one match did.

    Pylint: too-many-instance-attributes (8/7) -- **eight because a receipt
    for this act has eight things to say**, not because the value wants
    splitting.  The eighth is ``repriced_count``, and it is the one that made
    the panel say *"Nothing moved."* about a rewritten figure until
    2026-08-22; dropping a count to satisfy a limit is how that sentence came
    to be false in the first place.  ``CandidateRow`` carries the same disable
    for the same reason.

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
    """

    match_id: int
    posts_on: date
    amount: Decimal
    settled_count: int
    corrected_count: int
    line_count: int
    redated_count: int
    repriced_count: int


def load_lines(
    account_id: int, line_ids: "frozenset[int]", matched: MatchedSubjects,
) -> "list[BankStatementLine]":
    """Return the submitted bank lines, refusing any this account cannot match.

    **A line ALREADY in a match is refused here, symmetrically with the row
    side**, and the asymmetry was a real defect rather than an omission.
    ``uq_statement_match_members_line`` refuses the second act either way, so
    nothing could be corrupted -- but it arrives as an ``IntegrityError`` AFTER
    ``_apply_day`` has moved a settle day, which reaches the user as
    "Something went wrong" and logs a full traceback at ERROR for an ordinary
    stale page.  The hand-build form makes it easy to reach: its checkboxes
    render ``review.unmatched``, so one tab submitting a line another tab has
    just matched is two clicks.  Found by adversarial security review
    2026-08-17.

    **PUBLIC within the package since plan step X-f6a-3c-2**, because
    :mod:`._create` needs exactly this refusal for the one line it records and
    had grown its own copy of it.  Two implementations of "is this line on this
    account, and has something already claimed it" is two places for the
    refusal to stop firing.

    Args:
        account_id: The account the match is for.
        line_ids: The submitted ids.
        matched: What this account's matches have already claimed, read by the
            ACT rather than queried here -- so a batch's fourth item sees the
            lines its third item claimed.

    Returns:
        The lines, ascending by posted day then id.

    Raises:
        ValidationError: When an id names no line on this account, or names one
            another match already explains.  A REFUSAL rather than a silent
            skip, unlike the reconcile panel's bulk tick: that door narrows a
            set the user swept, and this one names specific rows on purpose, so
            dropping a member would change what the match MEANS while
            reporting success.
    """
    if line_ids & matched.lines:
        raise ValidationError(
            "A statement line you picked is already matched to something "
            "else.  Undo that match first if it is wrong.  Nothing was "
            "changed."
        )
    lines = (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.id.in_(line_ids),
        )
        .order_by(BankStatementLine.posted_on, BankStatementLine.id)
        .all()
    )
    if len(lines) != len(line_ids):
        raise ValidationError(
            "A statement line you picked is no longer on this account.  "
            "Reload the page and try again -- nothing was changed."
        )
    return lines


def resolve_rows(
    submission: MatchSubmission,
    scope: ReviewScope,
    matched: MatchedSubjects,
) -> "list[CandidateRow]":
    """Return the submitted app rows as priced candidates, refusing the rest.

    **Looked up in the pass's own offer set rather than queried directly**, so
    the set this door may act on is exactly the set the screen may offer.  One
    scope, shared by the reader and the writer, is the security property
    ``reconcile_service`` is built on: an id belonging to another user, another
    account, a non-contributing row, a card purchase or a row already spoken
    for by another match is not a candidate and cannot be matched by crafting a
    request.

    **The scope is a PARAMETER, the claims are re-read per act, and the FIGURE
    is re-derived per act** (plan step X-f6a-3c-2).  This function derived the
    whole account itself until that step, at 3.593 s a call on the developer's
    own data, which is 12.88 minutes to work one statement's 215 acts.  What
    made the derivation shareable is that its parts move at different rates:

    * WHICH rows exist and may be offered cannot change while a pass runs, so
      that is derived once and arrives on *scope*.  It is also the expensive
      half -- an 827-row scan -- and the security-bearing one;
    * WHICH of them are already spoken for changes with every item, so that is
      the *matched* argument, re-read by every act;
    * WHAT one is WORTH can be moved by a SIBLING act, so it is re-derived here
      through :func:`~._candidates.repriced`.

    **That third bullet replaces an argument adversarial financial review
    measured FALSE on 2026-08-19.**  The claim was that only a parent/child
    pairing can move a figure another item names, and that
    :func:`_reject_parent_and_its_own_purchase` refuses it.  But settling a
    matched purchase runs ``entry_service.update_entry``, which re-derives the
    envelope's CC Payback through ``sync_entry_payback`` and WRITES its
    ``estimated_amount`` -- and that payback is a candidate on the same
    account, a SIBLING of the purchase rather than its parent, invisible to
    that guard.  Measured: a `$60.00` payback dropping to `$50.00` mid-pass,
    with the second match accepted against the stale `$60.00` and the ledger
    booking `$50.00` for a `-$60.00` bank line.  Re-pricing is total where an
    enumeration of sibling writers is one writer from being wrong again.

    Args:
        submission: What the owner accepted.
        scope: The pass's derived offer set.
        matched: What this account's matches have already claimed, as of this
            act.

    Returns:
        The candidates the submission names, transactions first, priced as they
        stand NOW.

    Raises:
        ValidationError: When an id names nothing the screen could have
            offered, names a row another match has since claimed, or names one
            that can no longer be priced at all.
    """
    wanted = {
        (RowKind.TRANSACTION, row_id) for row_id in submission.transaction_ids
    } | {
        (RowKind.PURCHASE, row_id) for row_id in submission.entry_ids
    }
    offered = [
        row for row in unmatched_rows(scope.candidates, matched)
        if (row.kind, row.row_id) in wanted
    ]
    found = [
        fresh for fresh in (
            repriced(row, scope.calendar, scope.basis) for row in offered
        )
        if fresh is not None
    ]
    if len(found) != len(wanted):
        raise ValidationError(
            "One of the rows in this match is no longer available to match -- "
            "it may have been deleted, cancelled, or matched to another "
            "statement line.  Reload the page and try again; nothing was "
            "changed."
        )
    return found


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


def _reject_uncorrectable(
    lines: "list[BankStatementLine]", rows: "list[CandidateRow]",
) -> None:
    """Refuse a match whose difference this door cannot honestly record.

    **This REPLACES a blanket refusal, on ruling R-GD(a).**  Until 2026-08-22
    any match whose two sides did not sum to the same figure was refused and
    the owner sent away to retype the number the statement already carried.
    That refusal was not neutral: a line the screen would not explain is the
    line the merchant policy offers to RECORD, so the cheapest act left was to
    enter the movement a SECOND time -- measured at `$356.61` booked for one
    `$178.29` Geico payment, finding **N-335**.  The bank's figure is the
    record, so where it names ONE row it is simply written to that row.

    What still refuses, and why each is a genuine indeterminacy rather than a
    tolerance:

    * a GROUP whose sides differ.  Three rows summing to one deposit, with
      nothing saying WHICH is wrong -- ruling **R-FV**'s reason, undisturbed by
      R-GD, whose remedy is **R-FN**'s ordinary accepted row rather than a
      figure this door picks for a member;
    * a row whose FIGURE IS NOT ITS OWN TO STATE, which the row now SAYS
      (:attr:`~._offers.CandidateRow.states_own_figure`) rather than this door
      re-deriving.  A difference on one says a PURCHASE is missing or wrong,
      which is a different repair on a different row.  **The census that
      answers it is TWO published predicates and it moved to the candidate
      constructor at plan step X-f6d-1**, because the PROPOSER has to ask the
      same question and is pure: a near miss offered on such a row is an
      Accept button that can never succeed.  Two derivations of one refusal on
      a money gate is this arc's own root cause 1, and the argument for both
      members lives where the fact is now built;
    * a transfer SHADOW.  ``CLAUDE.md`` transfer invariant 3 holds a shadow's
      amount equal to its parent's, so correcting one means correcting the
      TRANSFER, which is not this door;
    * a bank line whose SIGN disagrees with the row's type.  Money leaving an
      account is not the same movement as money entering it, whatever the
      magnitudes do, and this is the one arm the old sum test used to catch by
      accident.

    Args:
        lines: The submitted bank lines.
        rows: The submitted app rows, already priced.

    Raises:
        ValidationError: With the figures in the message, and naming which of
            the four it is, so the sentence says what to do next.
    """
    bank = round_money(sum((line.amount for line in lines), Decimal("0.00")))
    app_side = round_money(
        sum((row.cash_amount for row in rows), Decimal("0.00")),
    )
    if bank == app_side:
        return

    nothing = "  Nothing was changed."
    if len(lines) != 1 or len(rows) != 1:
        raise ValidationError(
            f"These do not add up.  Your bank shows {bank:+,.2f} and the "
            f"{len(rows)} row(s) you picked come to {app_side:+,.2f}, a "
            f"difference of {bank - app_side:+,.2f}.  With more than one row "
            f"on a side nothing says WHICH row the difference belongs to, so "
            f"correct the one you know is wrong and match them again."
            + nothing
        )
    row = rows[0]
    if (bank < 0) != (app_side < 0):
        raise ValidationError(
            f"Your bank shows {bank:+,.2f} and this row is {app_side:+,.2f}.  "
            f"One is money leaving the account and the other is money coming "
            f"in, so they are not the same movement." + nothing
        )
    if row.kind is RowKind.TRANSACTION and row.transfer_id is not None:
        raise ValidationError(
            f"Your bank shows {bank:+,.2f} and this transfer is "
            f"{app_side:+,.2f}.  A transfer's two halves must stay equal, so "
            f"change the transfer itself and then match it." + nothing
        )
    if row.kind is RowKind.TRANSACTION and not row.states_own_figure:
        raise ValidationError(
            f"These do not add up.  Your bank shows {bank:+,.2f} and this row "
            f"is {app_side:+,.2f}.  This row is worth whatever its purchases "
            f"are, so it has no figure of its own to correct -- the difference "
            f"is a purchase that is missing or wrong, and that is what to "
            f"fix." + nothing
        )


def bank_cash_for(
    lines: "list[BankStatementLine]", rows: "list[CandidateRow]",
) -> "Decimal | None":
    """Return the cash the bank states for the ONE row this match names.

    **Defined only where the bank's figure names a single row**, which is the
    whole of ruling **R-GD(a)**'s determinacy: one line against one row is an
    assertion about that row and nothing has to be apportioned.  A GROUP is a
    different question -- three rows summing to one deposit, with nothing
    saying WHICH is the six cents wrong -- and ruling **R-FV** refused to guess
    at it for reasons R-GD did not disturb.  So this answers ``None`` there and
    the residual is **R-FN**'s ordinary accepted row, never a figure this door
    invents for a member.

    Args:
        lines: The bank lines the match explains.
        rows: The app rows it names.

    Returns:
        The single line's signed amount when both sides hold exactly one
        member, else ``None``.
    """
    if len(lines) != 1 or len(rows) != 1:
        return None
    return lines[0].amount


def corrected_figure(
    row: CandidateRow, bank_cash: "Decimal | None",
) -> "Decimal | None":
    """Return the figure *row* should book to move its cash onto the bank's.

    **The bank constrains the CASH LEG, and the stored figure is GROSS**, so
    the two are not the same number on a row carrying entries.  Inverting
    :func:`~app.services.cash_ledger.cash_leg_of` -- *gross, less what never
    reaches this account, signed by the transaction TYPE* -- gives
    ``|bank| + off_statement_sum``, which reuses that rule rather than
    restating it.  The two coincide on every row this arm reaches today (all 8
    of the developer's transaction near misses carry no entries), and the
    inversion is written anyway because a row that HAS entries is expressible
    and would otherwise book its credit purchases twice.

    **A PURCHASE stores its figure directly** -- its cash is the negated stored
    amount (:func:`~._candidates.purchase_candidate`) -- so its correction is
    the bare magnitude.

    Args:
        row: The member the bank's figure is about.
        bank_cash: What the bank states, signed, or ``None`` for a group.

    Returns:
        The figure to submit, or ``None`` when nothing should be submitted --
        a group, an unchanged figure, or a row whose amount is DERIVED from its
        own purchases and which :func:`_reject_uncorrectable` has already let
        through only when the two agree.
    """
    if bank_cash is None or bank_cash == row.cash_amount:
        return None
    if row.kind is RowKind.PURCHASE:
        return round_money(abs(bank_cash))
    txn = db.session.get(Transaction, row.row_id)
    return round_money(abs(bank_cash) + off_statement_sum(txn))


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


def _record(
    owner_id: int,
    account_id: int,
    lines: "list[BankStatementLine]",
    rows: "list[CandidateRow]",
) -> StatementMatch:
    """Stage the match act and one member per subject.

    Args:
        owner_id: The user the act belongs to.
        account_id: The account both sides belong to.
        lines: The bank lines it explains.
        rows: The app rows it names.

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
        ))
    db.session.flush()
    return match


def record_match(
    owner_id: int,
    account_id: int,
    lines: "list[BankStatementLine]",
    rows: "list[CandidateRow]",
    matched: MatchedSubjects,
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
    presence, for the double-count pairing and for balance, and only then does
    any settle door run.

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

    Does NOT commit -- the caller owns the session boundary.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account both sides belong to.
        lines: The bank lines this match explains, already scoped by
            :func:`load_lines`.
        rows: The app rows that explain them, already priced -- resolved by
            :func:`resolve_rows` or built by the door that created one.
        matched: What this account's matches have already claimed, as of this
            act.

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
    _reject_uncorrectable(lines, rows)

    # THE LATEST bank day for the posting, the EARLIEST stated day for the
    # purchase -- derived ONCE for the whole act, so no two members can be moved
    # onto two answers to the same question.  See :class:`MatchDays` for why the
    # two ends are opposite.
    days = MatchDays.of(lines)

    ordered = sorted(
        rows, key=lambda row: (row.kind is not RowKind.PURCHASE, row.row_id),
    )
    # Read BEFORE the writes: once `_apply_day` has moved a purchase onto the
    # bank's day the predicate no longer holds, so counting afterwards would
    # report zero every time.
    redated_count = sum(
        1 for row in ordered if corrected_purchase_day(row, days) is not None
    )
    # ONE derivation of what the bank says a row is worth, for the whole act:
    # ``bank_cash_for`` answers only where the figure names a single row, so a
    # group's members are handed ``None`` and keep their own figures.
    bank_cash = bank_cash_for(lines, rows)
    # Read BEFORE the writes, exactly as ``redated_count`` is and for the same
    # reason: once a settle door has taken the bank's figure the row agrees
    # with it, so counting afterwards would report zero every time.
    figures = [corrected_figure(row, bank_cash) for row in ordered]
    repriced_count = sum(1 for figure in figures if figure is not None)
    outcomes = [
        _apply_day(row, owner_id, days, figure)
        for row, figure in zip(ordered, figures, strict=True)
    ]
    match = _record(owner_id, account_id, lines, rows)

    amount = round_money(sum((line.amount for line in lines), Decimal("0.00")))
    accepted = AcceptedMatch(
        match_id=match.id,
        posts_on=days.posts_on,
        amount=amount,
        settled_count=outcomes.count("settled"),
        corrected_count=outcomes.count("corrected"),
        line_count=len(lines),
        redated_count=redated_count,
        repriced_count=repriced_count,
    )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_MATCHED, BUSINESS,
        "A bank statement's lines were matched to the rows they explain.",
        user_id=owner_id,
        account_id=account_id,
        match_id=accepted.match_id,
        posts_on=days.posts_on.isoformat(),
        happened_on=days.happened_on.isoformat(),
        line_count=accepted.line_count,
        row_count=len(rows),
        settled_count=accepted.settled_count,
        corrected_count=accepted.corrected_count,
        redated_count=accepted.redated_count,
        repriced_count=accepted.repriced_count,
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
        submission: What the owner accepted, ids only.
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
        scope.owner_id,
        scope.account_id,
        load_lines(scope.account_id, submission.line_ids, matched),
        resolve_rows(submission, scope, matched),
        matched,
    )


def release_match(match_id: int, owner_id: int, account_id: int) -> int:
    """Undo one match, leaving the days it wrote alone.

    **Deleting the record does NOT put the days back, and that is the honest
    direction.**  A settle day is what the app knows about when money moved,
    and the bank is still the best evidence it has; reverting one because the
    owner unlinked a record would throw away a correction in order to tidy a
    relation.  What the release restores is the QUESTION -- the bank lines
    become unexplained again and the rows become matchable again -- which is
    the repair door finding **N-302** says a refusal owes.

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
            names ONE act on purpose.
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
    released = len(match.members)
    db.session.delete(match)
    db.session.flush()
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_MATCH_RELEASED, BUSINESS,
        "A statement match was released; its lines are unexplained again.",
        user_id=owner_id, account_id=account_id, match_id=match_id,
        released_count=released,
    )
    return released
