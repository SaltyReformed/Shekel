"""Apply everything the owner reviewed in ONE pass, and say what each item did.

Plan step ``bank_import:X-f6a-3c-2``, finding **N-306**.  The review screen
offers two acts -- accept a proposed match, record a bank line as a purchase --
and until this step each one was its own request through its own money door.
Measured on the developer's own 2026-08-16 statement against a production
clone: **124 proposals and 91 recordable lines, 215 round trips**, each paying
``candidates_for`` at **3.593 s** before it wrote a row.  **12.88 minutes of
derivation to work one statement**, which is why the corrections do not get
made.  The same 215 acts through this door, end to end and applied for real:
**5.80 s**, against **762.7 s** for a control that re-derives per act -- and
the two produce byte-identical outcomes on all 215 items and identical balances
on six sampled days.

**It is not "accept everything"** (ruling **R-FP**).  Nothing here decides
anything: every item in a batch is one the owner ticked, carrying the same ids
the screen showed, and every one of them goes through the same door, the same
refusals and the same settle verbs a single-item request always did.  What the
batch removes is the round trip, not the review.

**The failure policy is the developer's ruling of 2026-08-19**: a refused item
leaves nothing behind and the rest still land, each refusal quoted with its own
sentence.  It is not a hypothetical bound -- 5 of the developer's own 124
proposals refuse today and will keep refusing, all of the same CLASS: a settled
credit-card payback whose recorded figure has drifted from the card entries it
repays, so any later entry edit on its envelope is refused (finding **N-323**,
two paybacks, `$59.68` of drift).  A batch that failed whole would lose 119
good corrections to it.

**How each item is isolated: a SAVEPOINT.**  ``db.session.begin_nested()``
around each act, released when it lands and rolled back when it refuses.  The
REQUEST is still the transaction and the route still owns the commit, so a
batch that dies part-way commits nothing at all -- the savepoint bounds a
DESIGNED refusal, never a failure.

**A ``PostingError`` is not a refusal and is not caught.**  It means a ledger
invariant is broken, which is a fact about the account rather than about the
item, so it fails the whole request loud (``CLAUDE.md`` rule 4).

**Order: every match, then every create, each in the order it was submitted**
-- which is the order the screen renders them, so the receipt reads down the
page.  It is a real decision rather than an arbitrary one: two ticked items can
collide, because an envelope a match names may also be the destination a
recorded line was aimed at, and the guard against counting one purchase twice
(:func:`~._accept._reject_parent_and_its_own_purchase`) has to refuse one of
them.  Measured on the developer's own statement, 4 envelopes are both named by
a proposal and offered as a destination, and **15 of the 91 recordable lines
aim at one**.  The developer ruled 2026-08-19 that the PROPOSAL wins: it
explains money the records already hold against a line the bank showed, where
the recorded line can be re-aimed at another envelope on the next pass.

**Each item FLUSHES before the next is validated**, and that is what makes one
shared derivation safe rather than merely fast: the guard above reads the
database.

**It is NOT the only way one item can move a figure another item names, and
saying so was measured FALSE on 2026-08-19.**  Settling a matched purchase runs
``entry_service.update_entry``, which re-derives the envelope's CC Payback and
writes its ``estimated_amount`` -- a SIBLING rather than a child, invisible to
that guard.  What actually keeps a pass honest is that
:func:`~._candidates.repriced` re-prices every named row per act, and, since
plan step ``bank_import:X-f6d-3``, that an item whose row has moved since the
screen described it is REFUSED rather than written (finding **N-336**).  This
paragraph asserted the refuted reason until an adversarial review found it
2026-08-23; ``_reject_parent_and_its_own_purchase`` had already been corrected.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db

from ._accept import accept_match
from ._create import MintedEnvelopes, create_purchase_from_line
from ._creations import PurchaseCreation
from ._scope import ReviewScope
from ._submission import MatchSubmission


@dataclass(frozen=True)
class ReviewedBatch:
    """What the owner ticked, in the order the screen showed it.

    Ids and the state each row was REVIEWED in, exactly as
    :class:`~._submission.MatchSubmission` and
    :class:`~._creations.PurchaseCreation` are: every figure and every day this
    door WRITES is re-derived from the rows the ids name, inside the same
    transaction, so a stale page cannot commit a number the database no longer
    holds -- and since plan step ``bank_import:X-f6d-3`` an item whose row has
    MOVED since the screen described it is refused rather than written
    (finding **N-336**), which is the other half of the same sentence.

    **It names no OWNER and no ACCOUNT either**: whose pass this is, is the
    :class:`~._scope.ReviewScope`'s, stated once.  A batch carrying its own
    pair beside a scope carrying another was a second answer nothing
    reconciled.

    Attributes:
        matches: The proposals ticked, plus the hand-built group where one was
            submitted -- they are the same act and reach the same door, so they
            are one list rather than two.
        creations: The bank lines the owner named a destination for.
    """

    matches: "tuple[MatchSubmission, ...]"
    creations: "tuple[PurchaseCreation, ...]"

    @property
    def item_count(self) -> int:
        """Return how many acts this batch asks for."""
        return len(self.matches) + len(self.creations)


@dataclass(frozen=True)
class AppliedItem:
    """One act that landed, as the receipt names it.

    Attributes:
        line_ids: The bank lines the act explains.  **A CORRELATION key, not a
            label**: a ``bank_statement_lines.id`` is opaque to the owner, who
            never sees one anywhere on this screen, so rendering "Line 4711:"
            beside a sentence pointed at nothing.  What identifies the act on
            screen is :attr:`summary`, which names its figure and its day.
            Carried because a caller -- and these tests -- must be able to say
            WHICH submitted item an outcome belongs to.  Named by adversarial
            design review 2026-08-19.
        summary: One sentence saying what it did, written by the door that did
            it.
    """

    line_ids: "tuple[int, ...]"
    summary: str


@dataclass(frozen=True)
class RefusedItem:
    """One act that was refused, as the receipt names it.

    **The sentence is the SERVICE's, verbatim.**  Every refusal in this package
    is written for the person who submitted the form and ends by saying nothing
    was changed; re-wording them here would put a second voice on a money
    screen, and summarising them would lose the figures that make one
    actionable -- the payroll gap names its own difference to the cent.

    Attributes:
        line_ids: The bank lines the refused act named, as a correlation key
            for the reason :class:`AppliedItem` gives.
        reason: The service's own sentence.  It names the act's own figures --
            which is what makes a refusal actionable, and why it is quoted
            rather than summarised.
    """

    line_ids: "tuple[int, ...]"
    reason: str


@dataclass(frozen=True)
class BatchOutcome:  # pylint: disable=too-many-instance-attributes
    """What a whole reviewed pass did.

    Pylint: too-many-instance-attributes (8/7) -- **eight because a pass
    receipt has eight things to say**, and the eighth is
    ``repriced_count``, which is what stopped this panel rendering
    *"Nothing moved."* over a rewritten figure (2026-08-22).  Dropping a
    count to satisfy a limit is how that sentence came to be false.
    :class:`~._accept.AcceptedMatch` carries the same disable for the same
    reason.

    Attributes:
        applied: The acts that landed, in the order they were applied.
        refused: The acts that were refused, in the same order.  **A refusal is
            an ordinary outcome here, not an error**: the ruled policy is that
            one bad item cannot cost the others, so a batch reporting refusals
            has still done everything it could.
        settled_count: How many rows the pass marked as having happened.
        corrected_count: How many settled rows had a day moved onto the bank's.
        repriced_count: How many rows had their FIGURE moved onto the bank's
            (:attr:`~._accept.AcceptedMatch.repriced_count`).  **Without it
            :attr:`moved_nothing` was FALSE rather than merely quiet**: a
            repricing whose row already carried the bank's day reports
            ``unchanged`` on every day count, so a pass that rewrote what a
            payment cost rendered *"Nothing moved."*  Found by adversarial
            design review 2026-08-22.
        redated_count: How many purchases had their PURCHASE day corrected
            (ruling **R-FW**).
        recorded_count: How many bank lines became a purchase the app did not
            have.
        envelopes_created: How many budget lines the pass created to hold one.
    """

    applied: "tuple[AppliedItem, ...]"
    refused: "tuple[RefusedItem, ...]"
    settled_count: int
    corrected_count: int
    redated_count: int
    repriced_count: int
    recorded_count: int
    envelopes_created: int

    @property
    def applied_count(self) -> int:
        """Return how many acts landed."""
        return len(self.applied)

    @property
    def refused_count(self) -> int:
        """Return how many acts were refused."""
        return len(self.refused)

    @property
    def moved_nothing(self) -> bool:
        """Return whether the pass changed no record at all.

        The screen's own question.  An applied item can still move nothing --
        a match that only confirms the day the app already held changes no
        column -- so counting applied items would claim work that did not
        happen, which is the distinction
        :class:`~._accept.AcceptedMatch` draws for a single act.
        """
        return not (
            self.settled_count
            or self.corrected_count
            or self.redated_count
            or self.repriced_count
            or self.recorded_count
        )


def _match_summary(accepted) -> str:
    """Return the sentence describing what one accepted match did.

    **It names the three effects separately**, because they are different acts
    with different consequences: settling a row books money the projection was
    still holding forward, correcting a settle day moves money already booked
    from one day to another, and correcting a PURCHASE day moves no money at
    all but rewrites when the owner says they bought something.  A single
    "2 rows updated" would hide which -- and the third was folded into the
    first until adversarial review 2026-08-18 measured that the case it was
    built for (an unsettled purchase, re-dated by up to 59 days) reported only
    "marked 1 row(s) as having happened".

    Args:
        accepted: The :class:`~._accept.AcceptedMatch`.

    Returns:
        The sentence.
    """
    did = []
    if accepted.settled_count:
        did.append(
            f"marked {accepted.settled_count} row(s) as having happened"
        )
    if accepted.corrected_count:
        did.append(
            f"moved {accepted.corrected_count} row(s) onto the bank's day"
        )
    if accepted.redated_count:
        did.append(
            f"corrected the purchase date on {accepted.redated_count} row(s)"
        )
    # **The AMOUNT, and it is named LAST because it is the one thing here that
    # changes what money was SPENT** rather than when it moved.  Without it a
    # repricing on a row already carrying the bank's day fell through to
    # "confirmed what you already had" -- a sentence that was not merely silent
    # but false.
    if accepted.repriced_count:
        did.append(
            f"corrected the amount on {accepted.repriced_count} row(s)"
        )
    what = " and ".join(did) if did else "confirmed what you already had"
    return (
        f"Matched {accepted.line_count} statement line(s) worth "
        f"{accepted.amount:+,.2f} on {accepted.posts_on}: {what}."
    )


def _created_summary(recorded) -> str:
    """Return the sentence describing what recording one line did.

    **It names the container and whether it was created**, because those are
    different acts: filing a purchase under an envelope the owner already
    budgeted changes what that envelope RECORDS as its cost, while creating one
    adds a budget line to a period that did not have it.

    **It names both days only when they differ.**  A purchase carries the day
    it was MADE beside the day the bank TOOK it (ruling **R-FW**), and on 179
    of the developer's 361 lines the source states no separate made-day at all
    -- so printing "made on" unconditionally would report the clearing day as a
    swipe day on half of every statement, which is the exact substitution R-FW
    rejected.

    Args:
        recorded: The :class:`~._create.CreatedPurchase`.

    Returns:
        The sentence.
    """
    where = (
        f"a new envelope, {recorded.envelope_label}"
        if recorded.envelope_created
        else recorded.envelope_label
    )
    made = (
        f", made {recorded.made_on}" if recorded.made_on != recorded.posts_on
        else ""
    )
    return (
        f"Recorded ${recorded.amount:,.2f} your bank took on "
        f"{recorded.posts_on}{made} as a purchase in {where}."
    )


@dataclass
class _Tally:  # pylint: disable=too-many-instance-attributes
    """The running receipt one pass builds.

    Pylint: too-many-instance-attributes (8/7) -- it accumulates exactly
    the counters :class:`BatchOutcome` publishes, so it carries that
    class's disable for that class's reason.

    Mutable and private, because it IS the loop's accumulator; what leaves this
    module is the frozen :class:`BatchOutcome` built from it.
    """

    applied: list
    refused: list
    settled: int = 0
    corrected: int = 0
    redated: int = 0
    repriced: int = 0
    recorded: int = 0
    envelopes: int = 0


def _run(tally: _Tally, line_ids: "tuple[int, ...]", act) -> object:
    """Run one act inside its own SAVEPOINT and record what happened.

    **The savepoint is what makes the ruled failure policy true rather than
    reassuring.**  A refused act may already have staged rows -- ``_create``
    creates a purchase before the match that names it is validated, and a
    settle verb can refuse mid-way through a group -- so "a refused item leaves
    nothing behind" needs the partial work undone without touching the items
    that landed before it.

    **``ValidationError`` and ``NotFoundError`` are caught, and nothing else.**
    Both are this project's DESIGNED refusals -- a sentence written for the
    person who submitted the form -- and they are SIBLINGS rather than one
    deriving from the other (``app/exceptions.py``), which is why naming only
    the first left a hole.  ``entry_service.update_entry`` and ``create_entry``
    raise ``NotFoundError`` for a row that has gone, so a row hard-deleted
    between this pass's derivation and this item's write took the whole request
    down as a 500 -- where the same event is a designed stale-page refusal
    everywhere else on this screen.  Found by adversarial financial review
    2026-08-19.

    Anything else propagates and fails the whole request, which is the right
    answer for a ``PostingError`` (a broken ledger invariant is a fact about
    the account, not about this item) and for a database error.

    Args:
        tally: The running receipt.
        line_ids: The bank lines this act names, for the receipt.
        act: The service call, taking no arguments.

    Returns:
        Whatever *act* returned, or ``None`` when it was refused.
    """
    savepoint = db.session.begin_nested()
    try:
        result = act()
        # **Inside this item's savepoint, and that is the point.**  Autoflush
        # would otherwise emit THIS item's INSERTs while the NEXT item's first
        # query runs -- inside the next item's savepoint -- so refusing that
        # one would roll back work this one had landed.  An earlier comment
        # here claimed the flush keeps an ``IntegrityError`` inside the
        # savepoint, which it does not: only a designed refusal is caught, so
        # an integrity error fails the whole request either way.  Named by
        # adversarial test-quality review 2026-08-19.
        #
        # It is also what makes the next item's refusals see this one: they
        # read what is already matched, and whether a row names an envelope
        # whose purchase this act just claimed.
        db.session.flush()
    except (ValidationError, NotFoundError) as exc:
        savepoint.rollback()
        tally.refused.append(RefusedItem(line_ids=line_ids, reason=str(exc)))
        return None
    savepoint.commit()
    return result


def apply_reviewed(batch: ReviewedBatch, scope: ReviewScope) -> BatchOutcome:
    """Apply every act the owner ticked, and report each one.

    Does NOT commit -- the route owns the session boundary, so a request that
    fails outside a designed refusal writes nothing at all.

    **It does not LOG the pass either**, and that is the same boundary: an
    event asserting "a reviewed pass was applied" must not sit in the log when
    the transaction that would have applied it failed, so the route emits it
    after its commit -- exactly as ``statements.record_statement``'s own
    business event is emitted by its route rather than by its service.

    Args:
        batch: What the owner ticked.
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).
            **The ROUTE builds it, exactly as only a route builds a
            ``BalanceContext``**, and this door takes it like every door
            beneath it.  A first draft built its own, which is the same shape
            one tier up as the per-act derivation this step exists to remove:
            the route needs that scope too -- to render the refusal a rolled-
            back pass leaves behind -- and a door that derives privately forces
            the caller to derive again.

    Returns:
        The :class:`BatchOutcome`.

    Raises:
        PostingError: From a ledger reconcile, on a broken invariant.  Fails
            the whole request loud rather than being reported as one item's
            refusal.
    """
    tally = _Tally(applied=[], refused=[])
    # **One registry per REQUEST**, which is what makes a sweep mint one
    # envelope per answer per pay period rather than one per line (finding
    # **N-327**).  Built HERE rather than inside the create door for the reason
    # the scope is built by the route: a door that made its own would be a door
    # that converges with nothing, one line at a time, which is the defect.
    minted = MintedEnvelopes.none_yet()

    for submission in batch.matches:
        line_ids = tuple(sorted(submission.line_ids))
        accepted = _run(
            tally, line_ids, lambda s=submission: accept_match(s, scope),
        )
        if accepted is None:
            continue
        tally.settled += accepted.settled_count
        tally.corrected += accepted.corrected_count
        tally.redated += accepted.redated_count
        tally.repriced += accepted.repriced_count
        tally.applied.append(AppliedItem(
            line_ids=line_ids, summary=_match_summary(accepted),
        ))

    for creation in batch.creations:
        line_ids = (creation.line_id,)
        recorded = _run(
            tally, line_ids,
            lambda c=creation: create_purchase_from_line(
                c, scope, minted,
            ),
        )
        if recorded is None:
            continue
        # **AFTER the act returned**, never inside the door that creates: an
        # item refused by ``create_entry`` rolls its whole SAVEPOINT back, and
        # a registry written one line above that refusal hands the NEXT line an
        # id the rollback has already taken.  Measured -- the sweep died on
        # ``NoneType`` -- which is why the remembering lives out here.
        if recorded.envelope_created:
            minted.remember(creation.new_envelope, recorded)
        tally.recorded += 1
        tally.envelopes += 1 if recorded.envelope_created else 0
        tally.applied.append(AppliedItem(
            line_ids=line_ids, summary=_created_summary(recorded),
        ))

    outcome = BatchOutcome(
        applied=tuple(tally.applied),
        refused=tuple(tally.refused),
        settled_count=tally.settled,
        corrected_count=tally.corrected,
        redated_count=tally.redated,
        repriced_count=tally.repriced,
        recorded_count=tally.recorded,
        envelopes_created=tally.envelopes,
    )
    return outcome
