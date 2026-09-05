"""Which of an account's recorded lines no act has ANSWERED yet.

Plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-HP**: every bank line
ends on exactly one of MATCH, ADD, TRANSFER or SKIP, and the Reconcile inbox is
the lines with none yet.  This module is the one statement of *none yet*.

**It is a module because the question has TWO acts behind it.**  An accepted
match answers a line (MATCH and ADD both leave a
:class:`~app.models.statement_match.StatementMatch`), and a recorded skip
answers one with nothing
(:class:`~app.models.statement_line_skip.StatementLineSkip`).  :func:`undisposed`
is the clause that joins them, and it has exactly two callers -- the review
pass's own list (:func:`undisposed_lines`) and the cheap count the grid's bank
control renders (:func:`awaiting_review_count`).  A count that answered a
different question from the list it links to would be a figure disagreeing with
its caption, so both read that clause and neither spells the predicate itself.

**It was three functions inside** :mod:`._reads` **until this step**, and the
split is ruling **balance:R-IR**'s rule rather than a preference: that module
stood at 989 lines against pylint's 1,000-line ceiling, and the fourth
predicate plus the prose it owes does not fit in eleven lines.  R-IR puts the
split on the session that BREAKS the module, and the boundary it takes is the
one stated above: this is the one statement of *none yet*, and
:mod:`._reads` assembles what the review screen makes of it.  *A first draft
justified the line differently -- ":mod:`._reads` is the review screen and
``awaiting_review_count``'s consumer is the grid" -- which its own contents
refute, since two of the three functions that moved serve the review screen.
Named by adversarial design review 2026-09-02.*  Nothing was reworded on the
way across except where this step made a sentence false, and each of those is
marked.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, plain
data out, no Flask import, no clock read.  It READS and never writes; the SKIP
act's own doors are :mod:`._skipping`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_line_skip import StatementLineSkip
from app.models.statement_match import StatementMatchMember
from app.services.cash_ledger import account_opening_fact

from ._candidates import act_still_names_a_row

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from datetime import date


def _spoken_for(account_id: int):
    """Return the query naming *account_id*'s lines a match already claims.

    One definition of "spoken for", because three questions rest on it: the
    review screen's own list and the grid's count, through :func:`undisposed`,
    and the SKIP door's refusal, through :func:`answered_by_a_match` -- which
    may not record a second answer for a line a match has already answered.  A
    count that answered a different question from the list it links to would be
    a figure disagreeing with its caption.

    **Module-private**: every one of those three reaches it through a public
    function in this file, so widening the name would be a surface nobody
    imports.  Named by adversarial design review 2026-09-02.

    Args:
        account_id: The account.

    Returns:
        The query of claimed ``bank_statement_line_id`` values.
    """
    return (
        db.session.query(StatementMatchMember.bank_statement_line_id)
        .filter(
            StatementMatchMember.account_id == account_id,
            StatementMatchMember.bank_statement_line_id.isnot(None),
            # ...and the act still names an app row, or the membership is not a
            # claim about anything (:func:`~._candidates.act_still_names_a_row`,
            # which carries the argument and the measurement).  The SAME clause
            # ``matched_subjects`` applies, so the screen's list, the grid's
            # count and the offer set cannot disagree about what "explained"
            # means.
            act_still_names_a_row(),
        )
    )


def answered_by_a_match(line_id: int, account_id: int) -> bool:
    """Return whether an accepted match already answers one line.

    Plan step ``bank_import:X-gj-4a``.  **The list predicate asked of ONE
    line**, so the SKIP door and the screen that offered the card cannot come
    to disagree about what "already answered" means -- which would be visible
    to the owner as a control that refuses what the page offered.  Spelled here
    rather than in :mod:`._skipping` so that module never names
    :class:`~app.models.statement_match.StatementMatchMember` and there is one
    place the membership clause lives.

    Args:
        line_id: The bank line.
        account_id: The account the route proved the caller owns.

    Returns:
        Whether a live match claims it.
    """
    return bool(
        db.session.query(
            _spoken_for(account_id)
            .filter(StatementMatchMember.bank_statement_line_id == line_id)
            .exists()
        ).scalar()
    )


def skipped(account_id: int):
    """Return the query naming *account_id*'s lines a recorded skip answers.

    Plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-JG**.  **The
    owner deciding a line is explained by nothing is an ANSWER**, so a skipped
    line leaves the inbox exactly as a matched one does -- which is the whole
    of what the store is for: a skip nothing reads is a line that comes back on
    the next visit.

    **No "still names a row" clause here, and the asymmetry with**
    :func:`_spoken_for` **is the difference between the two acts.**  A match can
    outlive its subject -- destroying the last app row it names leaves it
    claiming a line and asserting nothing -- so that reader has to ask whether
    the claim survives.  A skip names NO app row, so there is nothing for it to
    outlive: it stands until the owner undoes it, or until its own line goes
    (``fk_statement_line_skips_line_account``, ``ON DELETE CASCADE``).

    Args:
        account_id: The account.

    Returns:
        The query of skipped ``bank_statement_line_id`` values.
    """
    return (
        db.session.query(StatementLineSkip.bank_statement_line_id)
        .filter(StatementLineSkip.account_id == account_id)
    )


def skipped_among(
    line_ids: "frozenset[int]", account_id: int,
) -> "frozenset[int]":
    """Return which of *line_ids* a recorded skip already answers.

    Plan step ``bank_import:X-gj-4a``.  **The OTHER direction of ruling
    R-HP's "exactly one of the four"**, and it is asked at the one place the
    match doors resolve a submitted line
    (:func:`~._resolve.load_lines`) rather than in each of them:
    :func:`~._skipping.skip_line` refuses a line a live match answers, and
    without this the same line could be matched AFTER it was skipped and end
    up carrying both answers -- a card on the Explained tab and a card on the
    Skipped tab, for one line, with nothing raising.  *That last clause was
    written at ``X-gj-4a`` and was FALSE until plan step
    ``bank_import:X-gj-4c-2`` gave the Skipped tab the store to read*; it is
    measured now by
    ``test_reconcile.TestADoublyAnsweredLineIsTHECOSTTwoRefusalsBUY``, which
    files the match through its real door and then inserts the skip row
    directly -- one door bypassed, not two, and the resulting state one that
    neither would admit.  The refusal itself never depended on it
    -- ruling **R-HP** is the reason -- but a consequence nobody could observe
    is not a reason a reader can check.

    **Read per ACT and never cached on the pass**, which is
    :class:`~._candidates.MatchedSubjects`' own rule for the same reason: a
    batch's fourth item has to see what its third item wrote, and a value read
    once at the top of the pass could not.

    Args:
        line_ids: The submitted ids.
        account_id: The account the route proved the caller owns.

    Returns:
        The subset a skip answers, empty when none does.
    """
    if not line_ids:
        return frozenset()
    # **NARROWED FROM** :func:`skipped` **rather than re-spelled.**  A clause
    # added to that reader later would reach :func:`undisposed` and silently
    # miss this refusal, which is the drift a module named for one statement
    # of a predicate exists to prevent -- and :func:`answered_by_a_match` one
    # function up already composes on its own base.  Named by adversarial
    # design review 2026-09-02.
    rows = (
        skipped(account_id)
        .filter(StatementLineSkip.bank_statement_line_id.in_(line_ids))
        .all()
    )
    return frozenset(row[0] for row in rows)


def undisposed(account_id: int):
    """Return the clause admitting only lines no act has answered.

    **The one statement of ruling R-HP's "with none yet"**, so the pass and the
    grid's badge cannot come to mean different things by it.  Two ``NOT IN``
    terms rather than one over a union, because the two acts are two facts: a
    reader looking for why a line is absent from the inbox is told which of them
    answered it.  *A first draft also claimed each term "reaches its own table's
    own index", which is unmeasured and half false --
    ``budget.statement_match_members`` carries no index on ``account_id``.
    Named by adversarial design review 2026-09-02.*

    Args:
        account_id: The account.

    Returns:
        A SQLAlchemy clause over :class:`~app.models.statement_import
        .BankStatementLine`, ready to hand to ``filter``.
    """
    return db.and_(
        BankStatementLine.id.notin_(_spoken_for(account_id)),
        BankStatementLine.id.notin_(skipped(account_id)),
    )


def awaiting_review_count(account_id: int, opens: "date | None") -> int:
    """Return how many recorded lines the review has NOT disposed of.

    The grid's bank statement control renders this figure, so it is a COUNT
    in the database rather than ``len`` over hydrated rows: the screen it
    links to is the expensive one (:meth:`~._scope.ReviewScope.build`), and a
    control on the app's hottest render path may not pay for it.

    **It applies exactly the four predicates
    :func:`~._reads.review_set` splits on, and no others**, which is what lets
    the number and the screen agree:

    * no accepted match names the line (:func:`_spoken_for`);
    * no recorded SKIP answers it (:func:`skipped`, plan step
      ``bank_import:X-gj-4a``) -- the owner has said it is explained by
      nothing, and a badge that went on counting it would be asking a question
      they have answered;
    * the line is not BEFORE the owner's first payday
      (:func:`~._gaps._split_at_calendar_open`), because a line the pay calendar
      never covers can never be matched -- 130 of the developer's own 378
      recorded lines -- and counting those would leave the figure
      permanently non-zero and therefore meaningless;
    * the line is not one this ACCOUNT's opening equity already accounts for
      (:func:`~._gaps._split_at_books_open`, plan step **balance:X-f3c-2b-2b**), for
      the same reason one place along: such a line reaches no card, no
      proposal and no door, so counting it would promise work the screen does
      not offer -- 4 more on the developer's own Checking, which opens its
      books on the pay calendar's own first day.

    *It said THREE until plan step ``bank_import:X-gj-4a`` added the skip.*
    The count in that sentence is a claim about this function, so it moves with
    the predicate rather than being left to a reader to re-count.

    It deliberately does NOT run the proposer.  A line a proposal explains is
    still work until the owner ACCEPTS it, so the count is the whole of the
    screen's ``proposals`` plus its ``unmatched`` -- every line the review is
    still asking about.

    Args:
        account_id: The account.
        opens: The first day the owner's pay calendar covers
            (:meth:`~app.services.pay_calendar.PayCalendar.opening_bound`),
            or ``None`` for an owner with no periods at all -- in which case
            nothing is before the calendar, matching
            :func:`~._gaps._split_at_calendar_open`.  **Taken as a parameter rather
            than derived here**: the grid route already holds the pass's
            memoized calendar, and a producer below the route reads the
            pass's derivation instead of building a second one.

            *The second half of that reason has been retired.*  It read "a
            second one that can disagree with it under READ COMMITTED", and
            this function's only caller is ``grid/page._bank_control`` on the
            ``GET /grid`` render, where since plan step balance:X-i3 a second
            derivation here could not disagree with the route's.

            **And ``/grid`` is the ONE route where "the whole request is one
            snapshot" would be false**, which is why that is not the sentence:
            it opens a :func:`~app.db_transaction.write_transaction` block for
            the rolling top-up, so it runs read-only, then writable, then
            read-only again over a NEW snapshot.  What holds is narrower and
            positional -- the block is the first statement of ``index()``, and
            both the route's memoized calendar and this count are read long
            after it, so both fall inside the same later snapshot.  A
            ``write_transaction`` moved between those two reads would make the
            disagreement expressible again.

            What survives unconditionally is the reason that never depended on
            the isolation level: two reads of one fact in one request is this
            project's DRY violation, and the read this replaced was a second
            ``budget.pay_periods`` scan on the app's hottest render path.

            **The BOOKS bound is NOT a parameter beside it, and the asymmetry
            is that same reason applied honestly rather than copied.**  The
            grid route holds a memoized calendar and holds no opening, so
            taking one would make every caller derive a fact only this
            function needs -- and the point of the parameter was to consume a
            derivation the caller already had, never to push work outward.  It
            is one primary-key-ordered read of a single account's opening
            rows per grid render, against the ``budget.pay_periods`` scan
            the parameter removed.  **Not a row count**, which would be an
            undated measurement of one database quoted as a cost.

    Returns:
        The count, ``0`` when the account has nothing recorded.

    Raises:
        RuntimeError: From
            :func:`~app.services.cash_ledger.account_opening_fact`, when the
            account carries no opening row at all -- a broken invariant, and
            the same fail-loud policy :meth:`~._scope.ReviewScope.build`
            inherits.  Answering "then every line is work" would put a badge
            on the grid for lines the review screen cannot show.
    """
    query = db.session.query(db.func.count(BankStatementLine.id)).filter(
        BankStatementLine.account_id == account_id,
        undisposed(account_id),
        # **STRICTLY after**, which is :func:`~app.services.cash_ledger
        # .books_hold` in SQL and is the one place this module states that
        # comparison as a filter rather than asking the predicate.  A column
        # expression cannot call it; the ``>`` is the same test, and the
        # arm that grades them together is what stops the two drifting.
        BankStatementLine.posted_on > account_opening_fact(
            account_id,
        ).opened_on,
    )
    if opens is not None:
        query = query.filter(BankStatementLine.posted_on >= opens)
    return query.scalar() or 0


def undisposed_lines(account_id: int) -> "list[BankStatementLine]":
    """Return the account's recorded lines that no act has answered.

    *It was ``unmatched_lines``, and said "that no match explains", until
    plan step ``bank_import:X-gj-4a``.*  A match is now one of two answers
    rather than the only one (:func:`undisposed`), so the old name claimed
    a narrower set than the body returns -- in the module named for the
    wider one.  Renamed rather than re-documented, because a name that has
    to be corrected in prose is a name the next reader trusts anyway.
    Named by adversarial design review 2026-09-02.

    Args:
        account_id: The account.

    Returns:
        The lines, ascending by posted day then id.
    """
    return (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            undisposed(account_id),
        )
        .order_by(BankStatementLine.posted_on, BankStatementLine.id)
        .all()
    )
