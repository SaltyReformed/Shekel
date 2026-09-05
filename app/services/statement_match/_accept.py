"""The ONE place a match is recorded, and the only place here that MOVES MONEY.

An accepted match asserts that a set of bank lines and a set of the app's own
rows are ONE movement.  Two things follow from that assertion, and this module
now owns the second and ORCHESTRATES the first:

* every member row takes the bank's posted day, which SETTLES a row still
  Projected and CORRECTS one whose recorded day was wrong.  **That moved to**
  :mod:`._moving` **at plan step ``bank_import:X-gj-3a``**, when this module
  reached pylint's 1,000-line ceiling (ruling **balance:R-IR**); this one
  decides WHAT to move and calls one function to move it, and no longer
  touches a settle verb;
* the correspondence itself is recorded, so a re-import does not re-propose it,
  an undo has something to delete, and plan steps ``balance:X-f3a-2`` and
  ``balance:X-f3c`` have the provenance ruling **R-FT** promised them.

**It writes TWO relations, and they are not the same set** (plan step
``bank_import:X-f6f``, ruling **R-GG**): the MEMBERS this act asserts are one
movement, and the rows it brought into EXISTENCE.  A group's residual is both;
so is a purchase recorded from a bank line; the budget line that purchase was
created to sit in is created and never named, because naming an envelope
beside its own purchase counts the same money twice.  :mod:`._release` is what
reads the second relation.

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

**RESOLVING, RECORDING, MOVING and the MONEY GAP are four subjects in four
files.**  :mod:`._resolve` refuses what a submission may not NAME; this module
records the correspondence and sequences the act; :mod:`._moving` puts one
member onto the bank's day and figure through that row's own settle door; and
:mod:`._variance` owns everything about the two sides disagreeing -- measuring
the gap, refusing the gaps that cannot be honestly recorded, and deciding
where a recordable one LANDS (on the member the match attributes it to, or in
the ordinary row a group with no named member mints).  The gap file is plan
step ``bank_import:X-f6d-4``'s and the moving file is
``bank_import:X-gj-3a``'s; **each seam is a subject rather than a line count**,
which the second one has to say twice because a line count is what FORCED it:
every function in ``_variance`` reads the two SUMS and nothing here does, and
every function in ``_moving`` calls a settle verb and nothing here does.

**THREE refusals live in this module.**  Two are about the submission's
SHAPE -- a side with nothing in it, and an envelope named beside a purchase
inside it -- and neither reads a figure.  The third is
:func:`_reject_drifted_under_the_act`, which is about what the act's OWN writes
did to a member's price.  *(The count is stated because this arc has shipped a
taxonomy that did not add up before; a fourth added here is what has to change
this sentence.  It was FOUR until plan step ``bank_import:X-f6f``, and the one
that left is the UNDO's -- :mod:`._release` states its own now, beside the
rest of that subject.  :mod:`._resolve` and :mod:`._variance` each state
theirs.)*

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

**The settle verbs are the app's own, never restated -- and the dispatch that
chooses between them is** :mod:`._moving` **'s now**, not this module's.  An
ordinary row goes through ``transaction_service.apply_requested_status``, the
route layer's one status entry point; a transfer SHADOW through
``transfer_service``, because ``CLAUDE.md`` transfer invariant 4 admits no
direct mutation of one and ``settle_transaction`` refuses it outright; a
purchase through ``entry_service.update_entry``.  A matcher that stamped
``settled_on`` itself would be a fourth settle door, which is exactly what
ruling **R-FA** exists to prevent.  It is restated here rather than deleted
because :func:`record_match` still owns the ORDER those doors run in and the
refusals that must precede them.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_match import (
    StatementMatch,
    StatementMatchCreation,
    StatementMatchMember,
)
from app.models.transaction_entry import TransactionEntry
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_MATCHED,
    log_event,
)

from ._candidates import MatchedSubjects, matched_subjects, repriced
from ._creations import CreatedSubject
from ._moving import move_members
from ._offers import (
    CandidateRow,
    MatchDays,
    RowKind,
)
from ._sides import MatchSides
from ._variance import (
    DifferenceLanding,
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
    scope: ReviewScope,
    content: "MatchContent",
    *,
    applied_by_rule: bool,
) -> StatementMatch:
    """Stage the match act, one member per subject, one creation per new row.

    **The two relations are written together and they are not the same set**
    (plan step ``bank_import:X-f6f``, ruling **R-GG**).  The MEMBERS are what
    this act asserts are one movement, so their amounts have to add up; the
    CREATIONS are what it brought into existence, and the create-a-purchase
    arm's container is one of those without being a member -- naming an
    envelope beside its own purchase counts the same money twice, which
    :func:`_reject_parent_and_its_own_purchase` refuses outright.

    **It takes the SCOPE rather than an owner and an account**, which is the
    correction :func:`record_match` above it already made and this function was
    the last to be owed: both are ``scope``'s fields, and two arguments a
    caller unpacks by hand are two chances to pair one act's owner with another
    act's account.

    Args:
        scope: The pass, which is the one statement of whose account this act
            is on.
        content: What this act is MADE OF, as it will be RECORDED -- the
            members and the creations FINAL, with a group's minted residual
            already among them.  **It is a :func:`~dataclasses.replace`d copy
            rather than the one the caller passed**, because
            :func:`record_match` above decides whether a residual exists and
            this function may not: nothing here changes what the act contains,
            and everything that does has already happened.
        applied_by_rule: Whether a standing rule performed this act rather than
            a person ticking it (ruling **R-GT**).  **Keyword-only and with no
            default**, because it is a boolean argument whose two values are
            *the owner agreed to this* and *the app did it on their behalf*: a
            positional ``False`` at a call site says nothing, and a default
            would let a future door claim consent by omission.  It reaches the
            column as it arrives; nothing here derives it, because which rule
            fired is derivable from the matched line and whether ANY rule fired
            is not (ruling **R-GT**'s own argument against a foreign key).

    Returns:
        The staged, flushed :class:`~app.models.statement_match.StatementMatch`.
    """
    account_id = scope.account_id
    lines, rows, created = content.lines, content.rows, content.created
    match = StatementMatch(
        account_id=account_id, user_id=scope.owner_id,
        applied_by_rule=applied_by_rule,
    )
    db.session.add(match)
    # The members and creations carry the act's id in a composite key, so the
    # act must exist before they are staged.
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
    for subject in created:
        db.session.add(StatementMatchCreation(
            match_id=match.id,
            account_id=account_id,
            transaction_id=(
                subject.row_id if subject.kind is RowKind.TRANSACTION else None
            ),
            transaction_entry_id=(
                subject.row_id if subject.kind is RowKind.PURCHASE else None
            ),
            created_version_id=subject.version_id,
        ))
    db.session.flush()
    return match


@dataclass(frozen=True)
class MatchContent:
    """What ONE act is MADE OF, as the writer takes it.

    A parameter object rather than four more positional arguments (plan step
    ``bank_import:X-f6f``): :func:`record_match` reached six when the created
    rows joined it, and this project's rule for a public function past the
    bound is to name what the arguments are collectively rather than to
    disable the check.  They are collectively one thing -- the two sides of a
    correspondence, plus what the caller made and what the owner agreed to --
    where *scope* is whose pass this is and *matched* is what the ACCOUNT has
    already claimed, neither of which is content.

    Attributes:
        lines: The bank lines this match explains, already scoped by
            :func:`~._resolve.load_lines`.
        rows: The app rows that explain them, already priced -- resolved by
            :func:`~._resolve.resolve_rows` or built by the door that created
            one.
        created: Every app row brought into existence for this act, at the
            revision it left them (ruling **R-GG**).  ``()`` is what the form
            door passes: a submission names rows that already existed.

            **It means two subtly different things either side of**
            :func:`record_match`, and saying so is cheaper than a second type.
            What a CALLER passes is what the caller made.  What
            :func:`_record` receives is that plus a group's minted residual,
            because :func:`record_match` mints one and hands its writer a
            :func:`~dataclasses.replace`d copy -- so the field there is what
            the ACT made.  ``rows`` moves the same way, from what the caller
            resolved to what the act asserts.  Found by adversarial review
            2026-08-26, which is also why :func:`_record`'s own docstring no
            longer claims it receives what the caller passed.
        residual: The difference the owner reviewed and agreed to record, or
            ``None`` -- which is what every caller but the form door passes,
            because a door that BUILT its row built it at the bank's own figure
            and has no difference to explain.
        attributed: Which member the difference BELONGS to, as a
            ``(kind, row_id)``, or ``None`` where nothing says (plan step
            ``bank_import:X-gj-3a``).  It travels beside :attr:`residual`
            because the two are one decision seen from two sides -- *how much*
            and *whose* -- and a door given one without the other would be
            deciding the second itself.

            **The two building doors pass ``None`` and cannot pass anything
            else**: each names exactly one row, where ruling **R-GD(a)**'s
            determinacy answers the question.  It is the SUBJECT key rather
            than the reviewed row :class:`~._submission.MatchSubmission`
            carries, because by this tier the rows have been resolved and
            re-priced and the reviewed state has already done its work.
    """

    lines: "list[BankStatementLine]"
    rows: "list[CandidateRow]"
    created: "tuple[CreatedSubject, ...]" = ()
    residual: "Decimal | None" = None
    attributed: "tuple[RowKind, int] | None" = None


def record_match(
    scope: ReviewScope,
    content: MatchContent,
    matched: MatchedSubjects,
    *,
    applied_by_rule: bool,
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
    (:func:`~._variance.reject_unrecordable`); then the EARLIEST of the bank
    days is checked against the day the account's books open
    (:meth:`~._scope.ReviewScope.reject_line_before_books_open`, plan step
    **balance:X-f3c-2b-2b**), which is the one refusal a settle door cannot
    make for it; and only then does any settle door run.  The order the
    MEMBERS move in is :func:`~._moving.move_members`'.

    **A GROUP's difference is a MEMBER this function mints**, not an exception
    to the balance it checks (plan step ``bank_import:X-f6d-4``, ruling
    **R-FN**).  Where several rows explain one line and fall short of it, the
    shortfall is money the bank moved that no row of the owner's accounts for,
    so it is recorded as an ordinary uncategorized row and joins the match --
    and ``Sigma(lines) == Sigma(members)`` then holds BY CONSTRUCTION rather
    than by a refusal.  It is minted AFTER every refusal has fired, so the
    module's own promise that a refused match leaves the database exactly as it
    was is kept without depending on the batch's savepoint.

    **The minted row does NOT go through** :func:`~._moving._apply_day`, and that is
    about the RECEIPT rather than about the write.  It is born on the bank's
    own day, so passing it through would report it as one more row "marked as
    having happened" -- claiming the bank's evidence was applied to a record
    the owner already had, when this act is the only reason the record exists.
    It settles through the same verb ``_moving._apply_day`` uses
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
        content: What this act is made of (:class:`MatchContent`) -- the two
            sides, what the caller created, and the difference the owner
            agreed to.
        matched: What this account's matches have already claimed, as of this
            act.
        applied_by_rule: Whether a standing rule performed this act rather than
            a person ticking it (ruling **R-GT**).  **Keyword-only, required,
            and with no default anywhere on this path.**  Every act today is a
            tick, so a default of ``False`` would be correct at both of this
            function's callers and would still be the wrong shape: the fact it
            records is who consented, and a door added later that simply did
            not think about consent would then record that the owner had given
            it.  The one value this app cannot afford to infer is the one it
            would infer.  Plan step ``bank_import:X-ge`` is the first writer of
            ``True``; until then it is stated ``False`` at the two call sites
            that exist -- :func:`accept_match` and
            :func:`~._create.create_purchase_from_line` -- which is what those
            acts are.

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
    lines, rows = content.lines, content.rows
    _reject_empty_side(lines, rows)
    _reject_parent_and_its_own_purchase(rows, matched)
    # ONE derivation of what the two halves come to, for the whole act -- the
    # refusal below and the residual it may let through are the same
    # subtraction, and summing money twice on the two sides of a gate is this
    # arc's own root cause 1.
    sides = MatchSides.of(lines, rows)
    # **DERIVED BEFORE THE REFUSALS AND HANDED TO THEM**, which is the whole
    # of why it moved up from below :func:`MatchDays.of` (plan step
    # ``bank_import:X-gj-3a``, second pass).  One of those refusals is about
    # the landing itself -- a member whose own row cannot hold the figure the
    # bank leaves it -- and a refusal derived after the first settle verb has
    # run would be caught, if at all, by an invariant guard reporting a drift
    # that did not happen.  It writes nothing and reads nothing, so deriving
    # it first costs a subtraction.
    #
    # **It is ALSO what makes the two remedies exclusive.**
    # :class:`~._variance.DifferenceLanding` names a member exactly where the
    # difference is attributable to one and names none exactly where it is
    # not, so correcting a row and minting a member for the same gap is
    # unrepresentable rather than merely avoided.  A first version of plan step
    # ``bank_import:X-f6d-4`` gated the mint on the owner's consent alone, and
    # a one-row match carrying one then did BOTH -- the row corrected to the
    # bank's figure and the same difference booked again to Uncategorized.
    #
    # **It was ``bank_cash_for(sides, rows)`` until plan step
    # ``bank_import:X-gj-3a``**, which is the same rule over a wider set: that
    # function answered the bank total for a lone row, and the bank total is
    # what a lone row is left when nothing else is ticked.  What is new is the
    # middle arm -- a match naming SEVERAL rows and one member to carry the
    # gap, which is ruling **R-HT(b)**'s *onto a named row, re-pricing it*.
    landing = DifferenceLanding.of(sides, rows, content.attributed)
    reject_unrecordable(rows, sides, content.residual, landing)

    # THE LATEST bank day for the posting, the EARLIEST stated day for the
    # purchase -- derived ONCE for the whole act, so no two members can be moved
    # onto two answers to the same question.  See :class:`MatchDays` for why the
    # two ends are opposite.
    days = MatchDays.of(lines)

    # **The EARLIEST posting day, against the day this account's books open**
    # (plan step **balance:X-f3c-2b-2b**, finding **N-383**).  Asked here
    # because ``posted_first`` is what it needs and this is the first line at
    # which that exists -- and before ``_moving.move_members``, which is the first
    # thing in this function that writes.
    #
    # **``posted_first`` and never ``posts_on``, and the difference is the
    # whole defect.**  Every member settles on the LATEST of the bank days, so
    # a group holding one pre-opening line and one later line settles after
    # the books open and clears ``reject_movement_before_books_open``
    # untouched -- while the pre-opening line's money is already inside the
    # opening equity, and is now inside a settled row as well.  Measured on a
    # restored production clone 2026-08-31: lines of 2026-03-26 (`-$15.96`)
    # and 2026-08-17 (`-$64.04`) matched to one `$80.00` envelope were
    # ACCEPTED against books opening 2026-03-26 at `$689.16`.  A one-line
    # match refuses today only because ``max`` over one line is that line's
    # own day, which is an accident of the derivation and not a rule.
    scope.reject_line_before_books_open(days.posted_first, "this match")

    # **The residual's PAY PERIOD is resolved here, before any member moves**,
    # because that lookup can refuse: a line posted past the owner's last SAVED
    # pay period reaches this door (the review screen splits off only the
    # lines BEFORE the calendar opens and the ones the books cannot hold,
    # neither of which is a day PAST the horizon), and a refusal raised
    # after the settle verbs
    # had run would leave written work behind -- which this module's own
    # promise says it does not, savepoint or no savepoint.  Found by
    # adversarial financial review 2026-08-23.
    residual_period = (
        scope.period_holding(days.posts_on, "the difference on this match")
        if landing.mints_a_row and sides.difference
        else None
    )

    moved = move_members(scope, rows, landing, days)
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
        scope,
        replace(
            content,
            rows=members,
            created=(
                content.created if minted is None
                else (*content.created, CreatedSubject.of(minted))
            ),
        ),
        applied_by_rule=applied_by_rule,
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
        MatchContent(
            lines=load_lines(
                scope.account_id, submission.line_ids, matched,
                for_write=True,
            ),
            rows=resolve_rows(submission, scope, matched),
            # **Nothing, and that is the form door's whole character**: it
            # names rows the owner already had.  The one row this act can
            # bring into existence is a GROUP's residual, and
            # :func:`record_match` mints that itself.
            residual=submission.accepted_difference,
            # ...and where the owner said that residual BELONGS, which is what
            # decides whether it is minted at all (plan step
            # ``bank_import:X-gj-3a``).  ``resolve_rows`` above has already
            # refused a submission whose attribution is not one of its own
            # rows, so this key names a member of ``rows`` by construction.
            attributed=submission.attributed_subject,
        ),
        matched,
        # A TICK, always: this door exists because a person reviewed a proposal
        # and pressed Apply (ruling **R-FP**, amended by **R-GH** for the
        # CREATE class only).  No rule reaches it.
        applied_by_rule=False,
    )
