"""The SKIP act: recording that a bank line is explained by nothing, and undoing it.

Plan steps ``bank_import:X-gj-4a`` and ``X-gj-4c-2``, rulings
**bank_import:R-HP**, **R-JG** and **R-JH**.  SKIP is the fourth of the four
verbs a bank line can end on, and the only one that names no row of the
owner's: *a duplicate your bank later reversed, or a figure that is not money
you spent*.  This module is its two doors and the two readers over what they
have recorded -- :func:`skipped_count` for the tab bar's figure and
:func:`skipped_acts` for its cards, narrowed by one shared clause.

**The reader is HERE and not in a view module of its own**, which is the
placement :mod:`._accepted_view` deliberately does NOT take for the other act.
That module was split out of :mod:`._reads` on SIZE (ruling **balance:R-IR**),
and this one has none of that pressure; what it has instead is a coupling the
split would break.  The ``skip_id`` :func:`skipped_acts` puts on a card is the
one :func:`unskip_line` accepts, and the ORDER a card is offered in is only
honest if the row behind it is one the undo door can still find -- two modules
would be two places for that pair to drift.  :func:`~._undisposed.skipped`
already reads the same table for a different question (*has this line been
answered*), so "one module owns the store" was never the rule here; what this
module owns is the ACT.

**It MOVES NO MONEY and can move none, which is what makes this leaf safe.**
Both doors write exactly one table -- ``budget.statement_line_skips`` -- and
that table holds no figure.  A skip does not change what the bank showed, does
not record a movement in the books, and does not close the difference between
the two: :func:`~app.services.bank_agreement.bank_agreement` goes on reporting
the line's amount as a disagreement, which is right, because the money the bank
showed genuinely is not in the books.  What a skip changes is that the
Reconcile inbox stops asking.  The panel says so where the owner presses it
(plan step ``bank_import:X-gj-4b``).

**One reader is deliberately left alone**, and saying so is the point:
:func:`~app.services.bank_agreement.bank_agreement`'s day drill-down labels
each line matched or not, and a skipped line reads NOT matched there forever.
That is right rather than an oversight -- the drill-down explains a money
DIFFERENCE, a skip closes none, and a line the owner has disposed of is still
money their books do not hold.  Recorded because it is a reader of *has this
line been answered* that this step did not change, and an unrecorded omission
reads as one nobody considered.  Named by adversarial design review
2026-09-02.

**A line carries at most ONE answer, and the two halves of that are enforced at
different tiers because they are different shapes.**  *Skipped at most once* is
one table's own key (``uq_statement_line_skips_line``).  *Skipped OR matched,
never both* spans two tables, so no CHECK can carry it -- the same position
:func:`~._accept.accept_match`'s balance refusal is in -- and :func:`skip_line`
refuses it at the door, reading the very predicate the review pass splits on so
the screen and the door cannot disagree about what "already answered" means.

**A repeat is absorbed and a contradiction is refused**, and the asymmetry is
deliberate.  Pressing skip twice on one line states the same decision twice, so
the second press returns the standing row and writes nothing -- an outcome, not
an error, because there is nothing for the owner to fix.  Skipping a line an
accepted match already explains states a *different* answer for the same line,
which is the state ruling **R-HP** forbids, so it is refused by name.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.  Neither door commits
-- the route owns the unit of work, exactly as
:func:`~._release.release_match` beneath the same screen does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.statement_import import BankStatementLine
from app.models.statement_line_skip import StatementLineSkip
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_LINE_SKIPPED,
    EVT_STATEMENT_LINE_UNSKIPPED,
    log_event,
)

from ._accepted_view import REGISTER_LIMIT
from ._reads import as_bank_line
from ._undisposed import answered_by_a_match
from ._verbs import SKIP_SHUT_PAYS_AN_ACCOUNT
from ._vocabulary import account_payment_merchants

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from ._offers import BankLine

_logger = logging.getLogger(__name__)

#: What the owner is told when the line they named is not one of this account's.
#:
#: **It names no reason**, which is the project's security response rule at the
#: service tier: "not there" and "not yours" answer alike, so a crafted id
#: learns nothing from the difference.  The route above turns this into a 404
#: or a designed refusal depending on which door it came through.
_NO_SUCH_LINE = (
    "That statement line is no longer there.  Reload the page; nothing was "
    "changed."
)

#: What the owner is told when a match already answers the line.
#:
#: **It names the conflict rather than the table**, because the remedy is an
#: act they can perform: undo the match on the Explained tab, and the line
#: comes back to the inbox where it can be skipped.
_ALREADY_MATCHED = (
    "That line is already explained by a match, so it cannot also be skipped. "
    "Undo the match first if you meant to say it explains nothing."
)

#: What the owner is told when the skip they are undoing has gone.
_NO_SUCH_SKIP = (
    "That skip is no longer there.  Reload the page; nothing was changed."
)


@dataclass(frozen=True)
class SkipRequest:
    """One bank line a reviewed pass asks to record as explained by nothing.

    Plan step ``bank_import:X-gj-4b``.  **What the owner SUBMITTED**, where
    :class:`SkippedLine` is what the door DID -- the same seam
    :class:`~._creations.IncomeCreation` and
    :class:`~._creations.RecordedIncome` draw for the act beside it.

    **One field, and the emptiness is the design.**  A skip names no container,
    no row and no figure: ruling **bank_import:R-JG** stores no reason and
    there is none to store, because the decision IS *explained by nothing*.
    Its sibling :class:`~._creations.PurchaseCreation` carries a destination
    because a purchase is filed against something the owner chooses between,
    and this is what that argument looks like when the verb takes no argument
    at all.

    **It lives HERE and not in :mod:`._creations`**, which is the module for
    *what the owner may ask this import to CREATE*.  A skip creates nothing;
    what it produces is a decision, and this module owns the act.

    Attributes:
        line_id: The bank line to dispose of.  Graded before it arrives, by
            :class:`~app.schemas.validation.statements.StatementSkipSchema`,
            through the same ``RowId`` every other id on a reviewed pass is
            graded by -- so a forged or unparseable id is refused at the
            schema and never reaches :func:`skip_line`.
    """

    line_id: int


@dataclass(frozen=True)
class SkippedLine:
    """One recorded decision that a bank line is explained by nothing.

    Attributes:
        skip_id: The act, so a later screen can offer to undo it.
        line: The bank's own record of the movement
            (:class:`~._offers.BankLine`) -- the merchant, the posted day and
            the amount.  **Carried even though the caller supplied its id**,
            for the reason :attr:`~._batch.AppliedItem.line_ids` is: a batch
            reports per-item outcomes and pairs each with what was submitted,
            and an outcome that could not say which line it was about would
            have to be paired by position.
            **It was the bare ``line_id`` until plan step
            ``bank_import:X-gj-4b``**, which put this door in a BATCH: that
            pass writes one receipt sentence per act, every other sentence in
            :mod:`._batch` names its act's figure and its day, and ruling
            **R-GD(a)**'s rule is that a consent naming a count and no figure
            is a consent to an amount nobody stated.  The door is holding the
            row already, so carrying it costs no read; deriving the figure in
            :mod:`._batch` instead would be a second query for a fact this
            function had in hand.
        was_already_skipped: Whether this act found the decision already
            recorded and wrote nothing.  **Reported rather than hidden**: a
            door that absorbs a repeat and says nothing is indistinguishable
            from one that wrote, which is the shape this package refuses on
            every receipt it builds.  ``True`` is a stale double-submit, never
            a state the Reconcile page can offer -- a skipped line is not in
            the pass.
    """

    skip_id: int
    line: "BankLine"
    was_already_skipped: bool

    @property
    def line_id(self) -> int:
        """Return the bank line this act disposes of.

        **A property over :attr:`line` rather than a field beside it**, which
        is ``CLAUDE.md`` rule 14 at its smallest grain: the id is ON the line,
        and a second copy would be a second thing to keep in step for the sake
        of callers that want only the id.

        Returns:
            The ``budget.bank_statement_lines`` row id.
        """
        return self.line.line_id


def _line_on(
    line_id: int, owner_id: int, account_id: int,
) -> "BankStatementLine | None":
    """Return the recorded line LOCKED, or ``None`` when it is not the caller's.

    **The account and the owner are FILTERS and not checks on a fetched row**,
    which is the shape :func:`~._release.release_match` uses for the same
    reason: a query that cannot return another caller's row needs no second
    reader to remember comparing one.  The OWNER term is joined through the
    account rather than assumed from ``@require_owner``: without it,
    ``skip_line(someone_elses_line, attacker, that_account)`` gets past this
    read and is stopped only by ``fk_statement_line_skips_owner`` at flush --
    an ``IntegrityError``, which is a 500 and an aborted transaction rather
    than an answer a screen can render.  Named by adversarial security review
    2026-09-02.

    **IT TAKES A ROW LOCK, and that is what makes ruling R-HP's "exactly one
    verb" hold under concurrency rather than under luck.**  Both halves of the
    exclusivity are app-tier reads -- this door asks whether a match answers
    the line, and :func:`~._resolve.load_lines` asks whether a skip does -- and
    write transactions run at ``READ COMMITTED``
    (:mod:`app.db_transaction`), so two tabs interleave like this without a
    lock: T1 finds no match and inserts a skip, T2 finds no skip and inserts a
    member, both commit, and the line carries BOTH answers with nothing
    raising.  No key can catch it, because the pair spans two tables.  What
    both writers DO share is the bank line row itself, which both of their
    foreign keys reference -- so locking it here and in ``load_lines``, each
    BEFORE it reads the other table, serialises them on the one row they have
    in common.

    ``FOR NO KEY UPDATE`` and not ``FOR KEY SHARE``: the weaker mode is what
    an ordinary foreign-key insert already takes implicitly, and two of those
    are compatible with each other, so it would serialise nothing.  It is also
    not ``FOR UPDATE``, which would block the FK checks of unrelated writers
    against the same line for no benefit.  Named by adversarial security
    review 2026-09-02.

    Args:
        line_id: The bank line.
        owner_id: The user the route proved owns the account.
        account_id: The account the route proved the caller owns.

    Returns:
        The :class:`~app.models.statement_import.BankStatementLine`, locked
        for this transaction, or ``None``.
    """
    return (
        db.session.query(BankStatementLine)
        .join(
            Account,
            db.and_(
                Account.id == BankStatementLine.account_id,
                Account.user_id == owner_id,
            ),
        )
        .filter(
            BankStatementLine.id == line_id,
            BankStatementLine.account_id == account_id,
        )
        .with_for_update(of=BankStatementLine, key_share=False)
        .one_or_none()
    )


def _standing_skip(
    line_id: int, account_id: int,
) -> "StatementLineSkip | None":
    """Return the skip already recorded for this line, or ``None``.

    Args:
        line_id: The bank line.
        account_id: The account the route proved the caller owns.

    Returns:
        The :class:`~app.models.statement_line_skip.StatementLineSkip`, or
        ``None``.  At most one can exist
        (``uq_statement_line_skips_line``), so this is total rather than a
        first-of-many.
    """
    return (
        db.session.query(StatementLineSkip)
        .filter(
            StatementLineSkip.bank_statement_line_id == line_id,
            StatementLineSkip.account_id == account_id,
        )
        .one_or_none()
    )


def skip_line(line_id: int, owner_id: int, account_id: int) -> SkippedLine:
    """Record that one bank line is explained by nothing the owner budgets for.

    Ruling **bank_import:R-JG**.  Does NOT commit -- the route owns the session
    boundary.

    Args:
        line_id: The bank line to dispose of.
        owner_id: The user the route proved owns the account.
        account_id: The account it must belong to.

    Returns:
        The :class:`SkippedLine`.  Its ``was_already_skipped`` is ``True``
        where the decision was already standing and this call wrote nothing.

    Raises:
        ValidationError: On any of three refusals, all of which fire BEFORE
            anything is written.  *line_id* names no line this caller holds --
            the set-operation form of the project's "404 for both not-found
            and not-yours" rule.  An accepted match already answers it, which
            is the two-answers state ruling **R-HP** forbids.  Or the source
            files its merchant as paying an account the owner holds (ruling
            **R-JI**), which is money that moved between two of their own
            accounts rather than a line explained by nothing.
    """
    line = _line_on(line_id, owner_id, account_id)
    if line is None:
        raise ValidationError(_NO_SUCH_LINE)

    standing = _standing_skip(line_id, account_id)
    if standing is not None:
        # **A repeat is the same decision, so it is an outcome and not a
        # refusal.**  Reported as such rather than absorbed silently: see
        # :attr:`SkippedLine.was_already_skipped`.
        return SkippedLine(
            skip_id=standing.id, line=as_bank_line(line),
            was_already_skipped=True,
        )

    # **The pass's own predicate, asked of ONE line**
    # (:func:`~._undisposed.answered_by_a_match`).  A second spelling here
    # would be free to disagree with the list the screen drew the card from,
    # which is how a door comes to refuse what a screen offered -- and this one
    # refuses, so the disagreement would be visible to the owner as a button
    # that does not work.
    if answered_by_a_match(line_id, account_id):
        raise ValidationError(_ALREADY_MATCHED)

    # **The SOURCE's own observation, ruling R-JI, asked through the ONE
    # reader that owns this vocabulary** (:func:`~._vocabulary
    # .account_payment_merchants`).  A line whose merchant a source files as
    # paying an account the owner holds is not "explained by nothing" -- the
    # money moved, and the card arc will pair it -- so recording that decision
    # would store a claim the app already knows to be false.  Asked of the
    # LINE's merchant, and a line naming none contributes nothing, which is
    # that reader's own totality rule rather than a guard restated here.
    if (
        line.merchant_id is not None
        and line.merchant_id in account_payment_merchants(account_id)
    ):
        raise ValidationError(SKIP_SHUT_PAYS_AN_ACCOUNT)

    skip = StatementLineSkip(
        bank_statement_line_id=line_id,
        account_id=account_id,
        user_id=owner_id,
    )
    db.session.add(skip)
    # FLUSHED so the act has an id before it is reported, which is the
    # discipline :func:`~._accept._record` keeps: a receipt naming an act the
    # caller cannot address is a receipt nothing can act on.
    db.session.flush()

    log_event(
        _logger, logging.INFO, EVT_STATEMENT_LINE_SKIPPED, BUSINESS,
        "A bank line was recorded as explained by nothing.",
        user_id=owner_id, account_id=account_id, line_id=line_id,
        skip_id=skip.id,
    )
    return SkippedLine(
        skip_id=skip.id, line=as_bank_line(line), was_already_skipped=False,
    )


def unskip_line(skip_id: int, owner_id: int, account_id: int) -> int:
    """Undo one skip, putting its bank line back among the ones to explain.

    Ruling **bank_import:R-JG**.  Does NOT commit -- the route owns the session
    boundary.

    **It destroys the decision rather than answering it**, which is what makes
    the table a state rather than a log: the forensic record of the skip having
    existed is ``system.audit_log``'s, written by the DELETE trigger every
    ``budget`` table carries, with the whole old row and the acting user id.
    The module docstring of
    :mod:`app.models.statement_line_skip` carries the whole argument.

    **It removes NOTHING else, and that is the difference from**
    :func:`~._release.release_match`.  That door destroys the rows its act
    CREATED, so its control needs a confirmation naming them; a skip created
    nothing, so undoing one takes nothing back and needs none.

    Args:
        skip_id: The act to undo.
        owner_id: The user the route proved owns the account.
        account_id: The account it must belong to.

    Returns:
        The bank line that is unexplained again.  **The reason is the DOOR's
        symmetry and not a surface's need**: every act door here reports what
        it acted on, and the tests grade this one.  *An earlier version said
        "so the surface that pressed this can name it", which no surface does
        -- ``_unskip_report`` deletes the value and the receipt names no row
        id, because none is visible anywhere on the screen.*

    Raises:
        ValidationError: When *skip_id* names no skip on this owner's account.
            Raised rather than ignored because this door names ONE act on
            purpose -- :func:`~._release.release_match`'s own rule.
    """
    # **THE READER'S OWN NARROWING** (:func:`_mine`), not a third spelling of
    # it.  The ids this door accepts are the ids :func:`skipped_acts` put on a
    # card, so the two must agree about whose rows they are -- and two clauses
    # that agree today are still two clauses (``CLAUDE.md`` rule 14).  Named by
    # adversarial review 2026-09-04, which found this door restating the very
    # narrowing ``_mine``'s docstring cites it as sharing.
    skip = (
        db.session.query(StatementLineSkip)
        .filter(
            StatementLineSkip.id == skip_id,
            _mine(owner_id, account_id),
        )
        .one_or_none()
    )
    if skip is None:
        raise ValidationError(_NO_SUCH_SKIP)

    line_id = skip.bank_statement_line_id
    db.session.delete(skip)
    # FLUSHED so the row is really gone before the caller re-derives a pass
    # from this session -- the ordering :func:`~._release.release_match` keeps
    # for the same reason: the screen a door answers with must not be built
    # over the state the door replaced.
    db.session.flush()

    log_event(
        _logger, logging.INFO, EVT_STATEMENT_LINE_UNSKIPPED, BUSINESS,
        "A skip was undone; its bank line is waiting to be explained again.",
        user_id=owner_id, account_id=account_id, line_id=line_id,
        skip_id=skip_id,
    )
    return line_id


@dataclass(frozen=True)
class SkippedAct:
    """One recorded skip, as the Skipped tab renders it.

    Plan step ``bank_import:X-gj-4c-2``.  **Two fields and not a flattened
    copy of the line**, which is :class:`~._cards.ActCard`'s own argument: the
    card prints the bank's facts off :attr:`line` and nothing else about the
    act is rendered, so a second spelling of the merchant or the amount here
    would be a second place for the card to disagree with the row.

    Attributes:
        skip_id: The act, which is what the Undo control submits and what
            :func:`unskip_line` accepts.
        line: The bank's own record of the movement
            (:class:`~._offers.BankLine`) -- the merchant, the posted day, the
            raw description and the amount, exactly as a
            :class:`~._cards.LineCard` shows them, so the two kinds of card
            read as one list.

    **It carries no ``decided_on``**, and the absence is deliberate rather
    than an oversight: ``budget.statement_line_skips`` records ``created_at``,
    the card renders the BANK's day the way every other card on this page
    does, and a field written on every read and printed on none is the shape
    :class:`~._reconcile.ReconcilePage` deleted an ``account_id`` for.  There
    is also no ``was_already_skipped`` twin of :class:`SkippedLine`'s: that is
    a fact about a PRESS, and this is a fact about a standing row.
    """

    skip_id: int
    line: "BankLine"


def _mine(owner_id: int, account_id: int):
    """Return the clause admitting only this owner's skips on this account.

    **ONE narrowing for the count and the list**, which is finding **N-389**'s
    lesson applied before it can happen again: the accepted set had a caption
    derived by one reader and cards by another, they disagreed by one, and the
    remedy was a single clause both compose on
    (:data:`~._release.NAMES_A_BANK_LINE`).

    **It filters the OWNER as well as the account**, which is
    :func:`~._accepted_view.accepted_counts`' own narrowing and
    :func:`unskip_line`'s: the account implies the owner through
    ``fk_statement_line_skips_owner``, and a reader feeding a screen narrows by
    the same columns the write door does.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account.

    Returns:
        The clause, ready to hand to ``filter``.
    """
    return db.and_(
        StatementLineSkip.account_id == account_id,
        StatementLineSkip.user_id == owner_id,
    )


def skipped_count(owner_id: int, account_id: int) -> int:
    """Return how many of this account's lines the owner has skipped.

    Plan step ``bank_import:X-gj-4c-2``.  **The tab bar's figure, on every
    render whichever tab is open**, which is why it is a COUNT in the database
    rather than ``len`` over :func:`skipped_acts`: the Reconcile page builds
    the cards of ONE tab and takes every other tab's count cheaply, which is
    the measurement ruling **R-GX** established for the settled tabs.

    **It cannot disagree with what the tab draws, and that is structural --
    on two legs, and an earlier draft of this paragraph named only one.**

    WITHIN one read: :func:`skipped_acts` narrows on the same clause
    (:func:`_mine`), its joins can neither drop nor duplicate a row, and its
    window ``count`` is evaluated over the filtered set BEFORE ``LIMIT``.
    ACROSS the two reads: a query request runs at ``REPEATABLE READ, READ
    ONLY`` (:mod:`app.db_transaction`, whose own comment says the isolation
    level "is what gives the pass one snapshot"), and the Reconcile page is a
    GET -- so the tab bar's figure and the register's total are two statements
    against ONE snapshot and cannot see different sets however much commits
    under them.

    *This paragraph has now been wrong in BOTH directions, which is why it
    names its legs.*  It first claimed "structural" with only the first leg,
    and an adversarial review called that an overstatement.  The correction
    then said the two reads race under ``READ COMMITTED`` -- false for the
    render it described, because a GET is not a command, and false again in
    saying such a race "needs two concurrent writes" when ONE committed write
    between the statements would do it.  What survives: the only two-snapshot
    path is a POST re-render naming ``tab=skipped``, which no rendered control
    emits (the Skipped tab sits outside the Apply form, **R-HW**) and which
    ``_requested_tab`` would have to be handed by a crafted body.
    :func:`skipped_acts` narrows on the same clause (:func:`_mine`) and adds
    TWO joins, neither of which can drop a row.  The INNER join to the line
    cannot, because ``fk_statement_line_skips_line_account`` guarantees the
    parent row exists -- **the KEY is what guarantees it, not its ``ON DELETE
    CASCADE``**, which decides what happens when the line goes rather than
    whether it is there.  The second is the LEFT OUTER join to
    ``budget.merchants`` that ``lazy="joined"`` adds, which cannot drop a row
    because it is outer and cannot duplicate one because it matches on a
    primary key.  *An earlier draft of this paragraph said "only an INNER
    JOIN" and did not know about the second*, which adversarial review found
    by compiling the statement -- a paragraph whose whole job is to enumerate
    what could drop a row may not omit a join.  So there is no state in which
    the caption counts a skip the tab could not draw FOR WANT OF A JOIN --
    the defect finding **N-389** measured for the accepted set, where the
    loader's own ``NAMES_A_BANK_LINE`` clause is what a key gives this one for
    free.  **What the caption may legitimately exceed is what the BOUND
    renders**, which is a different thing and is said on the page rather than
    hidden: the bar states the whole record and the list says how many it
    withheld (:class:`SkippedRegister`).

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account.

    Returns:
        The count, ``0`` for an account nothing has been skipped on.  A bare
        aggregate always returns one row, so this is total.
    """
    return db.session.query(
        db.func.count(StatementLineSkip.id),
    ).filter(_mine(owner_id, account_id)).scalar()


@dataclass(frozen=True)
class SkippedRegister:
    """The recorded skips a tab renders, and how many it withheld.

    Plan step ``bank_import:X-gj-4c-2``, ruling **bank_import:R-GX** applied to
    a third tab (**R-JW**).  **The count travels with the rows**
    for the reason :class:`~._accepted_view.AcceptedRegister` states: a
    truncated list that does not say it is truncated is a page claiming to be
    the whole record -- and this tab is the only surface a skipped line can be
    found and undone on.

    Attributes:
        shown: The acts to render, newest bank day first.
        withheld_count: How many the bound left out -- ``0`` when the whole
            record is on screen, which is what tells the surface whether to
            offer the link that shows everything.  **No default**, which is
            :attr:`~._cards.CardSection.withheld`'s own discipline: ``0`` is
            the value that reads as safe and it claims *this is all of them*.
    """

    shown: "tuple[SkippedAct, ...]"
    withheld_count: int


def skipped_acts(
    owner_id: int, account_id: int, limit: "int | None" = REGISTER_LIMIT,
) -> SkippedRegister:
    """Return this account's recorded skips, as the Skipped tab lists them.

    Plan step ``bank_import:X-gj-4c-2``, ruling **bank_import:R-JG**.  **The
    tab holds only lines the OWNER skipped** (ruling **R-JH**): a standing
    *never a purchase* answer claims nothing about what a line is, so no such
    line is here -- it is in the inbox, which is what plan step
    ``bank_import:X-gj-4c-1`` put back.

    **ONE query, and the merchant rides along.**
    :attr:`~app.models.statement_import.BankStatementLine.merchant` is
    ``lazy="joined"`` for exactly this reason (finding **N-309**), so listing
    the lines costs no load per card.  The join is spelled here rather than
    through a relationship on
    :class:`~app.models.statement_line_skip.StatementLineSkip`: that model
    deliberately carries none, and adding one for a single reader that a
    two-term join already serves is the abstraction ahead of its caller
    ``CLAUDE.md`` rule 13 forbids.  **Both terms of the composite key are in
    the join**, so the account equality travels with it rather than being a
    filter a later reader could drop (finding **bank_import:N-358**).

    **It BOUNDS what it renders at** :data:`~._accepted_view.REGISTER_LIMIT`
    **and says how many it withheld**, which is ruling **bank_import:R-GX**'s
    shape on a third tab (**R-JW**).

    **It is the PAGE's one bound and not a second constant.**  A first version
    of this step declared a ``SKIPPED_LIMIT`` of its own and defended the
    duplicated literal as letting the two DIVERGE -- and adversarial review
    measured that the page never read it: :func:`~._reconcile.reconcile_page`
    threads ONE ``limit`` from the route into every bounded arm positionally,
    so the default was shadowed on every render and the constant governed
    nothing but its own tests.  A value that cannot change what a reader sees
    is not a knob, and flexibility nobody asked for is what ``CLAUDE.md`` rule
    13 refuses.

    *The bound itself replaced NO bound at all, on a reason review measured
    false*: this step first claimed the settled tabs bound because they VALUE
    every act they render.  :data:`~._accepted_view.REGISTER_LIMIT`'s own
    docstring and R-GX say the opposite -- the fold reads every act either
    way, and *what is bounded is what is RENDERED*.  A reason that is wrong
    about the precedent it cites is worse than none, because the next tab
    copies it.

    Measured 2026-09-04 on the real rendered page: a skip card costs **1,427
    bytes** marginal against about 980 for a settled act (216,637 bytes over
    221), so a skip card is ~1.46x the byte cost of the thing that already had
    a bound.  Unbounded, this owner's 378 recorded lines would render about
    **556 KB** -- 32x the 17,162-byte empty tab, and the same order as the
    578,523-byte review page R-GX split up.  *An earlier draft called 556 KB
    "larger than" that page; it is slightly smaller, and the comparison was
    wrong in either unit convention.*

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account.
        limit: How many acts to render, or ``None`` for the whole record --
            which is what the tab's own *show the other N* link asks for, and
            is :func:`~._accepted_view.accepted_register`'s own spelling of the
            same parameter.

    Returns:
        The :class:`SkippedRegister`.  Its acts are NEWEST bank day first --
        the order the locked direction gives every tab on this page
        (``docs/design/bank_import_audit.md``), ordered in SQL rather than in
        Python because the reader that builds the list is where an order
        belongs.  Ties break on the line id descending, so two lines the bank
        posted on one day keep a STABLE order between renders rather than one
        a reader comparing screenshots would see move -- **which is also what
        makes the bound deterministic**: an unstable sort under a ``LIMIT``
        would render a different 50 on each visit, and the acts it dropped
        would be reachable only by luck.
    """
    # **The WHOLE size and the bounded page in ONE statement.**  A window
    # ``count`` is evaluated over the filtered set BEFORE ``LIMIT`` applies, so
    # the total and the rows come from one snapshot and one predicate.  *An
    # earlier version read the total from a second* :func:`skipped_count`
    # *call and carried a ``max(..., 0)`` clamp* whose only reachable cause was
    # a concurrent insert between the two statements -- a race reporting "this
    # is all of them" over a truncated list, which is the one direction
    # ``withheld_count`` exists to prevent.  Named by adversarial review.
    total_over_all = db.func.count().over().label("total")
    query = (
        db.session.query(
            StatementLineSkip.id, BankStatementLine, total_over_all,
        )
        .join(
            BankStatementLine,
            db.and_(
                BankStatementLine.id
                == StatementLineSkip.bank_statement_line_id,
                BankStatementLine.account_id == StatementLineSkip.account_id,
            ),
        )
        .filter(_mine(owner_id, account_id))
        .order_by(
            BankStatementLine.posted_on.desc(),
            BankStatementLine.id.desc(),
        )
    )
    # **BOUNDED IN SQL, before anything is valued**, which is
    # :func:`~._accepted_view.accepted_register`'s own rule for its parameter:
    # a caller slicing this function's RESULT would have hydrated and mapped
    # every row on the account to drop most of them.
    if limit is not None:
        query = query.limit(limit)
    rows = query.all()
    # **Read off the rows, so shown and withheld are halves of ONE read.**  No
    # clamp: an empty result means an empty SET, because a bound is never zero
    # -- there is no state where nothing was drawn and something was withheld.
    total = rows[0][2] if rows else 0
    return SkippedRegister(
        shown=tuple(
            SkippedAct(skip_id=skip_id, line=as_bank_line(line))
            for skip_id, line, _total in rows
        ),
        withheld_count=total - len(rows),
    )
