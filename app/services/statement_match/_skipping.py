"""The SKIP act: recording that a bank line is explained by nothing, and undoing it.

Plan step ``bank_import:X-gj-4a``, rulings **bank_import:R-HP** and **R-JG**.
SKIP is the fourth of the four verbs a bank line can end on, and the only one
that names no row of the owner's: *a duplicate your bank later reversed, or a
figure that is not money you spent*.  This module is its two doors.

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

from ._undisposed import answered_by_a_match
from ._vocabulary import account_payment_merchants

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

#: What the owner is told when their bank files this line as paying an account
#: they hold.  Ruling **bank_import:R-JI** (developer, 2026-09-02).
#:
#: **It is a DOOR and not a shut tab**, which is ruling **R-GJ**'s whole
#: lesson one verb over: that ruling cost `$7,412.94` to learn that a warning
#: paragraph over a working control is not a refusal.  ``X-gj-4b`` will render
#: SKIP shut for these lines; a screen-level shut over an open door is the
#: exact shape R-GJ closed, so the refusal lives here.
#:
#: **And it is not only a doctrine point.**  Such a line is a
#: :class:`~._bars.ParkedLine` the Reconcile page counts on its *waiting for
#: the account they paid* chip, whose label carries a COUNT and a MAGNITUDE
#: (:func:`~._reconcile._chips`).  Skipping one drops it out of the pass, so
#: that money figure falls -- a skip moving a rendered amount, which is the
#: one thing this act is supposed never to do.  Named by adversarial review
#: 2026-09-02.
_PAYS_AN_ACCOUNT = (
    "Your bank files that line as a payment to another account you hold, so "
    "it is not explained by nothing -- the money moved between two of your "
    "own accounts.  It waits on the Transfers tab until the app can pair the "
    "two sides."
)


@dataclass(frozen=True)
class SkippedLine:
    """One recorded decision that a bank line is explained by nothing.

    Attributes:
        skip_id: The act, so a later screen can offer to undo it.
        line_id: The bank line it disposes of.  **Carried even though the
            caller supplied it**, for the reason
            :attr:`~._batch.AppliedItem.line_ids` is: a batch reports per-item
            outcomes and pairs each with what was submitted, and an outcome
            that could not say which line it was about would have to be paired
            by position.
        was_already_skipped: Whether this act found the decision already
            recorded and wrote nothing.  **Reported rather than hidden**: a
            door that absorbs a repeat and says nothing is indistinguishable
            from one that wrote, which is the shape this package refuses on
            every receipt it builds.  ``True`` is a stale double-submit, never
            a state the Reconcile page can offer -- a skipped line is not in
            the pass.
    """

    skip_id: int
    line_id: int
    was_already_skipped: bool


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
            skip_id=standing.id, line_id=line_id, was_already_skipped=True,
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
        raise ValidationError(_PAYS_AN_ACCOUNT)

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
        skip_id=skip.id, line_id=line_id, was_already_skipped=False,
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
        The bank line that is unexplained again, so the surface that pressed
        this can name it.

    Raises:
        ValidationError: When *skip_id* names no skip on this owner's account.
            Raised rather than ignored because this door names ONE act on
            purpose -- :func:`~._release.release_match`'s own rule.
    """
    skip = (
        db.session.query(StatementLineSkip)
        .filter(
            StatementLineSkip.id == skip_id,
            StatementLineSkip.account_id == account_id,
            StatementLineSkip.user_id == owner_id,
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
