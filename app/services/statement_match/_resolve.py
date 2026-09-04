"""What a SUBMISSION names, resolved under the pass's own scope.

:mod:`._accept` writes a match; this decides what a match may be written
ABOUT.  The seam is that module's own, stated there in prose since plan step
``bank_import:X-f6a-3c-2`` -- *resolving and recording are two acts* -- and
made structural here at ``bank_import:X-f6d-3``, when the reconciliation
finding **N-336** asks for took the file past its line cap.  Nothing moved
across it: :func:`resolve_rows` called nothing in the write half and the write
half calls nothing here, so the split is the call graph's own shape.

**SEVEN refusals live here and they share one subject**: whether what a body
sent is what this pass could have offered.  Three are about the LINES -- a
line this account does not hold, one another match has claimed, and one the
owner has already SKIPPED (plan step ``bank_import:X-gj-4a``) -- and four
about the ROWS: a row this pass could not offer or can no longer price, one
subject named twice, a row that has MOVED since the screen described it, and
an ATTRIBUTION naming a row the submission does not carry (plan step
``bank_import:X-gj-3a``).
The refusals in :mod:`._accept` are about the submission's SHAPE instead -- an
empty side, a parent matched beside its own child -- and the ones in
:mod:`._variance` are about the two sides DISAGREEING, which since plan step
``bank_import:X-f6d-4`` includes the figure that is not the row's to state.

*(This module's count is stated because this arc has shipped a taxonomy that
did not add up before; if an eighth refusal is added here, this sentence is
what has to change with it.  It read SIX until plan step
``bank_import:X-gj-4a`` added the skip, which is the count moving with the
predicate rather than a reader being left to re-count.  No count is claimed
for the other module, which owns its own.)*

**The security property is the SCOPE** and it did not change: an id is looked
up in the pass's own offer set (:class:`~._scope.ReviewScope`), never queried
directly, so a row belonging to another user, another account, a
non-contributing row, a card purchase or a row another match has claimed is
not a candidate and cannot be reached by crafting a request.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import.  It READS and never writes.
"""

from __future__ import annotations

from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_import import BankStatementLine

from ._candidates import MatchedSubjects, repriced, unmatched_rows
from ._offers import CandidateRow, RowKind
from ._scope import ReviewScope
from ._submission import MatchSubmission, ReviewedRow
from ._undisposed import skipped_among


def load_lines(
    account_id: int, line_ids: "frozenset[int]", matched: MatchedSubjects,
    *, for_write: bool,
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

    **A line the owner has SKIPPED is refused for the same reason one match
    holds** (plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-HP**):
    a bank line ends on exactly ONE of the four verbs, and
    :func:`~._skipping.skip_line` refuses the mirror -- a line a live match
    answers may not also be skipped.  **Without this half the exclusivity is
    one-directional**, and the state it admits is silent: the line carries a
    match AND a skip, so it renders a card on the Explained tab and another on
    the Skipped tab, is absent from the inbox for two independent reasons, and
    nothing raises.  No key can hold it -- the rule spans two tables -- which
    is the position ``accept_match``'s balance refusal is already in.

    *The two-cards half of that sentence was written at ``X-gj-4a`` and was
    FALSE until plan step ``bank_import:X-gj-4c-2``*: the Skipped tab then held
    the lines a standing *never a purchase* answer barred rather than recorded
    skips, so such a line rendered on Explained and nowhere else.  It is true
    now, and it is MEASURED rather than asserted --
    ``test_reconcile.TestADoublyAnsweredLineIsTHECOSTTwoRefusalsBUY`` builds
    the match through the real door at a moment when no skip exists, then
    inserts the SKIP row at the ORM tier -- so only the second answer is
    planted, which is the half this refusal owns.  *An earlier draft said
    "past both doors"; only one is bypassed, and the resulting STATE is the
    thing neither door would allow.*  Recorded
    because a justification written ahead of the surface it describes reads,
    to the next reader, exactly like one that was checked.

    **Asked HERE and not in the three doors**, because this function is
    already the one statement of *is this line on this account, and has
    anything claimed it*: the paragraph above says two implementations of that
    question is two places for it to stop firing, and a third door written next
    year inherits this one by calling it.

    Args:
        account_id: The account the match is for.
        line_ids: The submitted ids.
        matched: What this account's matches have already claimed, read by the
            ACT rather than queried here -- so a batch's fourth item sees the
            lines its third item claimed.
        for_write: Whether the caller is about to WRITE a match, which decides
            whether the lines are read under a row lock.  **Keyword-only and
            with NO DEFAULT**, because the value that reads as safe is the
            wrong one in both directions: defaulting to ``True`` makes a
            PREVIEW fail, and defaulting to ``False`` makes a DOOR race.  The
            three write doors pass ``True``;
            :func:`~._preview.preview_hand_build` passes ``False``, and it is
            the only caller that may -- it exists to run this door's reads and
            refusals WITHOUT its writes.
            **A preview must not lock, and that is measured rather than
            stylistic**: a query request runs inside a
            ``REPEATABLE READ, READ ONLY`` transaction
            (:mod:`app.db_transaction`), where PostgreSQL refuses every row-lock
            strength -- ``FOR NO KEY UPDATE`` included -- at executor start,
            whether or not the query matches a row.  The workbench reaches this
            on an ordinary ``GET .../statements/match?line=N``, which is the
            link ruling **R-HC** puts on every queue row, so a lock taken
            unconditionally here is a 500 on that page.  Named by adversarial
            design review 2026-09-02, which found it in this step's own first
            draft.

    Returns:
        The lines, ascending by posted day then id.

    Raises:
        ValidationError: When an id names no line on this account, names one
            another match already explains, or names one the owner has already
            skipped.  A REFUSAL rather than silently dropping the member,
            unlike the reconcile panel's bulk tick: that door narrows a
            set the user swept, and this one names specific rows on purpose, so
            dropping a member would change what the match MEANS while
            reporting success.  *(It read "rather than a silent skip" until
            plan step ``bank_import:X-gj-4a`` made SKIP a verb of this
            package's own, at which point the sentence read as being about the
            refusal one line above it.)*
    """
    if line_ids & matched.lines:
        raise ValidationError(
            "A statement line you picked is already matched to something "
            "else.  Undo that match first if it is wrong.  Nothing was "
            "changed."
        )
    # **A WRITING CALLER READS THE LINES LOCKED, AND BEFORE THE SKIP TEST
    # BELOW.**  Both halves of ruling R-HP's exclusivity are app-tier reads
    # across two tables -- this asks whether a skip answers the line, and
    # :func:`~._skipping.skip_line` asks whether a match does -- so under
    # ``READ COMMITTED`` two tabs otherwise interleave into a line carrying
    # BOTH answers, which no key can catch.  Locking the bank line, which both
    # writers' foreign keys reference, is what serialises them.
    # ``FOR NO KEY UPDATE`` for the reason :func:`~._skipping._line_on`
    # states: ``FOR KEY SHARE`` is what an ordinary foreign-key insert already
    # takes, and two of those are compatible with each other.
    #
    # **The ORDER BY gives every caller here one lock order, which bounds the
    # deadlock risk WITHIN one call and not across a batch.**
    # :func:`~._batch.apply_reviewed` loops its items and calls a door per
    # item, so a bulk apply takes N separately-ordered reads in SUBMISSION
    # order; two concurrent presses whose items name the same lines in
    # opposite order can still deadlock. That is finding **N-471** and is not
    # narrowed here, because the remedy is an ordering decision in the batch
    # and would change which item's savepoint runs first on a money door.
    # Named by adversarial design review 2026-09-02.
    query = (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.id.in_(line_ids),
        )
        .order_by(BankStatementLine.posted_on, BankStatementLine.id)
    )
    if for_write:
        query = query.with_for_update(of=BankStatementLine, key_share=False)
    lines = query.all()
    if len(lines) != len(line_ids):
        raise ValidationError(
            "A statement line you picked is no longer on this account.  "
            "Reload the page and try again -- nothing was changed."
        )
    if skipped_among(line_ids, account_id):
        raise ValidationError(
            "A statement line you picked is one you have already skipped, so "
            "it is not waiting to be explained.  Undo that skip first if you "
            "meant to explain it.  Nothing was changed."
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
    reviewed = submission.subjects
    if len(reviewed) != len(submission.rows):
        # **A body naming one subject twice, refused BY NAME rather than
        # collapsed** (plan step ``bank_import:X-f6d-3``).  The screen renders
        # exactly one input per row, so this cannot arrive from it; and
        # ``subjects`` is a mapping, so two entries with one subject and
        # different reviewed figures would silently keep whichever the set
        # iterated last -- letting the SENDER choose which state the staleness
        # guard checks against, on the door that re-prices rows.  A first draft
        # left this to the count below and a docstring claimed it was caught
        # there; it was not, because that count is taken over the collapsed
        # mapping and 2 rows over 1 subject compares 1 against 1.
        raise ValidationError(
            "This match names the same row more than once.  Reload the page "
            "and try again; nothing was changed."
        )
    if (
        submission.attributed_to is not None
        and submission.attributed_to not in submission.rows
    ):
        # **The attribution is a POINTER into the rows and this is what makes
        # it one** (plan step ``bank_import:X-gj-3a``).  It is refused HERE,
        # beside the duplicate-subject refusal above, because both are facts
        # about the SUBMISSION as a set of rows rather than about any row's
        # state -- and because refusing it before the offer set is read means
        # a body naming a row it does not carry never reaches the arithmetic
        # that would decide the remedy.
        #
        # **Compared as a WHOLE reviewed value rather than by subject.**  The
        # pane renders the option's value as the row's own token, so the two
        # fields are one string in any browser; a body whose attribution
        # carries a different figure or revision from the row it points at is
        # describing two states of one row, which is finding **N-336**'s shape
        # with the halves inside one submission.
        raise ValidationError(
            "This match says its difference belongs to a row it does not "
            "include.  Reload the page and try again; nothing was changed."
        )
    offered = [
        row for row in unmatched_rows(scope.candidates, matched)
        if (row.kind, row.row_id) in reviewed
    ]
    found = [
        fresh for fresh in (
            repriced(row, scope.calendar, scope.basis) for row in offered
        )
        if fresh is not None
    ]
    if len(found) != len(reviewed):
        raise ValidationError(
            "One of the rows in this match is no longer available to match -- "
            "it may have been deleted, cancelled, or matched to another "
            "statement line.  Reload the page and try again; nothing was "
            "changed."
        )
    _reject_moved_since_review(found, reviewed)
    return found


def _reject_moved_since_review(
    rows: "list[CandidateRow]",
    reviewed: "dict[tuple[RowKind, int], ReviewedRow]",
) -> None:
    """Refuse an item whose row is no longer what the screen described.

    **Finding N-336, and it is the one refusal here that is about the SCREEN
    rather than about the row.**  Every guard beside it asks whether an act is
    legal; this asks whether it is the act the owner reviewed.  Ruling
    **R-FP** -- *a match is a PROPOSAL, never a silent apply* -- is only true
    of the shipped app if something compares the two moments, and until this
    step nothing did: the screen offered *from ``-178.32`` to ``-178.29``*, the
    row was edited to ``500.00`` in another tab, and this door wrote a
    **``$321.71``** correction under that caption.

    **It runs AFTER the re-pricing rather than instead of it.**  The two answer
    different questions and both are needed: :func:`~._candidates.repriced`
    makes the write correct against the database as it stands NOW (finding
    **N-309**), and this makes the write one the owner agreed to.  A door with
    only the first writes a correct number nobody saw; a door with only the
    second writes a reviewed number that is stale.

    **It fails CLOSED, which is what the exact tier used to do by accident.**
    An equal match whose price moved became UNEQUAL and was refused by
    :func:`~._variance.reject_unrecordable`'s predecessor (**R-FV**);
    ``X-f6d-2`` made
    an unequal one-to-one recordable and that accident stopped protecting
    anything.  So this refuses on ANY movement, in either direction, on either
    coordinate -- not only where a correction would be written.  A match whose
    row silently grew a card purchase between render and Apply is exactly as
    unreviewed as one whose figure was retyped.

    Args:
        rows: The submitted rows as they stand now, re-priced.
        reviewed: What the screen showed for each, by subject
            (:attr:`~._submission.MatchSubmission.subjects`).

    Raises:
        ValidationError: Naming the row and both figures, on the first
            disagreement.  ONE sentence rather than a list: the batch quotes it
            per item (**R-FZ(a)**) and a reviewer acts on a stale page by
            reloading it, which fixes every row at once.
    """
    for row in rows:
        moved = reviewed[(row.kind, row.row_id)].disagrees_with(row)
        if moved is not None:
            raise ValidationError(
                f"This match was reviewed against different figures -- {moved}."
                "  Nothing was changed.  Reload the page to review it against "
                "what your records hold now."
            )
