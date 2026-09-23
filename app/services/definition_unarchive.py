"""
Shekel Budget App -- The Rows a Definition's Unarchive Brings Back

ONE statement of which rows unarchiving a recurring definition restores (plan
step ``pay_calendar:C18-a``, rulings **R-PC93** and **R-PC95**, developer
2026-09-23), read by the two doors that restore them --
``routes/templates/crud.unarchive_template`` and
``routes/transfers/lifecycle.unarchive_transfer_template`` -- and by the
doors that refuse to strand a planned row below an account's books
(``app.services.planned_rows_books``).

**Why the refusal reads it.**  Archiving a definition hides its still-Projected
rows, and unarchiving brings them back.  A books move made while the
definition was archived saw no live row to strand, so it went through; the
unarchive then restored rows inside the opening balance, and a recurring
transfer's unarchive -- whose maintain pass retires a row the rule no longer
names -- deleted the current paycheck's row without a word, measured by the
C18-a round-2 review.  R-PC93 treats the rows an unarchive would bring back as
planned rows at every door that moves a definition's books.  That holds only
while the refusal counts EXACTLY the rows the unarchive restores, which is why
both read :class:`UnarchiveScope`: two spellings of one scope are two homes
that have to agree (``CLAUDE.md`` rule 14).

**A row its definition's books already drop STAYS DELETED** (ruling
**R-PC95**, the C18-a round-3 review's H-C).  The archive's hide and the
owner's own delete of one occurrence write the same flag -- a hand delete of
a recurring row is a SOFT delete, whose tombstone keeps the maintain pass from
re-creating the occurrence -- and nothing on the row tells the two apart.  So
an unarchive that restored every soft-deleted row also restored one its owner
deleted while the definition was active, below the books when those had
since moved past it (allowed: a deleted row is not planned), inside the
opening balance: measured through the real routes, on dev and on this step
alike, the row's $10.00 counted a second time.  Now the rows answering an
occurrence the books drop, as they stand when the definition is unarchived,
are left deleted, and the unarchive says so (:func:`stays_deleted_notice`).
Under R-PC93 such a row can only be one its owner deleted by hand -- the
doors that move a definition's books refuse to strand the rows its archive
hid -- and the notice names every one, so none is left without a word.

**The books decide by the WALK, never a stored day** (the adversarial
review's finding H1): :func:`inside_the_books` asks
:func:`~app.services.recurrence.placements_below_the_books` which occurrences
the books drop, and a row is matched to the occurrence it answers by
``occurs_on``, the key the maintain pass matches by -- the same question, of
the same producer, the refusals ask.

**The root is not fixed here.**  An unarchive still restores a row its owner
deleted by hand ABOVE the books, and the conflict chooser revives one (ledger
row **REC-535**): both follow from the shared flag, which ledger row
**REC-536** holds, with the from-scratch fix -- a skipped occurrence stored on
the rule, so no row stands for it -- that deletes this guard.

Services-boundary discipline (``CLAUDE.md`` Architecture): ORM reads in, SQL
criteria and a sentence out; no Flask symbol, no write, no clock.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import false, or_

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import BalanceContext, definition_money_accounts
from app.services.cash_ledger import governing_account_opening
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    ResolvedRecurrence,
    placements_below_the_books,
)
from app.services.recurring_definition import resolved_rule_of
from app.utils.balance_predicates import is_projected_clause


@dataclass(frozen=True)
class UnarchiveScope:
    """What unarchiving one recurring definition does with its hidden rows.

    Read off the definition AS IT STANDS -- its books as they are, before any
    save the caller is grading -- because that is what an unarchive would
    act on.

    Attributes:
        definition: The
            :class:`~app.models.transaction_template.TransactionTemplate` or
            :class:`~app.models.transfer_template.TransferTemplate`.
        inside_the_books: Every occurrence its books drop, mapped to the day
            the books are compared with for its placement
            (:func:`inside_the_books`): the rows answering one stay deleted.
        books_opened_on: The definition's books floor, which the notice
            quotes; ``None`` when it has none, and then nothing stays.
        is_envelope: Whether the compared day is a paycheck's last day
            (ruling **R-PC89**) rather than a due day, which the notice has
            to say to be true.
    """

    definition: TransactionTemplate | TransferTemplate
    inside_the_books: dict
    books_opened_on: date | None
    is_envelope: bool

    def _hidden(self) -> tuple:
        """Return the criteria for the definition's soft-deleted, still-Projected rows."""
        _table_order, model, template_fk = rows_of(self.definition)
        return (
            template_fk == self.definition.id,
            is_projected_clause(model),
            model.is_deleted.is_(True),
        )

    def restores(self) -> tuple:
        """Return the SQL criteria selecting the rows the unarchive restores.

        Returns:
            The criteria, for ``query.filter(*criteria)``: the definition's
            soft-deleted still-Projected rows, less those answering an
            occurrence its books drop.  A row answering NO occurrence
            (``occurs_on`` ``NULL``) is restored: no walk names it, with or
            without the books, so the books cannot hold it back.
        """
        if not self.inside_the_books:
            return self._hidden()
        _table_order, model, _template_fk = rows_of(self.definition)
        return (*self._hidden(), or_(
            model.occurs_on.is_(None),
            model.occurs_on.notin_(list(self.inside_the_books)),
        ))

    def stays_deleted(self) -> tuple:
        """Return the SQL criteria selecting the rows the unarchive leaves deleted.

        Returns:
            The criteria, for ``query.filter(*criteria)``: the definition's
            soft-deleted still-Projected rows answering an occurrence its
            books drop -- the complement of :meth:`restores` among its hidden
            rows.
        """
        if not self.inside_the_books:
            return (*self._hidden(), false())
        _table_order, model, _template_fk = rows_of(self.definition)
        return (
            *self._hidden(),
            model.occurs_on.in_(list(self.inside_the_books)),
        )


def rows_of(definition) -> tuple:
    """Return ``(table_order, model, template_fk)`` for *definition*'s rows.

    Args:
        definition: A transaction or transfer template.

    Returns:
        ``(0, Transaction, Transaction.template_id)`` or ``(1, Transfer,
        Transfer.transfer_template_id)`` -- the order the books refusals break
        ties in, the row model, and the column naming the row's definition.
    """
    if isinstance(definition, TransferTemplate):
        return 1, Transfer, Transfer.transfer_template_id
    return 0, Transaction, Transaction.template_id


def inside_the_books(
    resolved: ResolvedRecurrence | None, calendar: PayCalendar,
) -> dict:
    """Return every occurrence *resolved*'s books drop, with the day compared.

    **The one reading of what a definition's books drop**, taken by the
    unarchive here and by the refusals in ``app.services.planned_rows_books``
    (:func:`~app.services.planned_rows_books.first_row_below_the_books`), so
    a row the unarchive leaves deleted and a row a refusal names come from
    one walk.

    Args:
        resolved: The definition's recurrence with its books floor attached,
            or ``None`` -- a definition with no rule, or an owner with no pay
            periods -- which drops nothing.
        calendar: The owner's pay calendar.

    Returns:
        ``{occurrence: compared day}`` for every occurrence
        :func:`~app.services.recurrence.placements_below_the_books` reports,
        the compared day being
        :meth:`~app.services.recurrence.ResolvedRecurrence.books_day` for its
        placement (the due day for a bill, ruling **R-PC86**; the paycheck's
        last day for an envelope, ruling **R-PC89**).

    Raises:
        RecurrenceGenerationError: See
            :func:`~app.services.recurrence.placements_below_the_books`.
    """
    if resolved is None:
        return {}
    return {
        placement.occurrence: resolved.books_day(placement.period)
        for placement in placements_below_the_books(resolved, calendar)
    }


def unarchive_scope(
    definition, resolved: ResolvedRecurrence | None, calendar: PayCalendar,
) -> UnarchiveScope:
    """Return what unarchiving *definition* would restore and leave deleted.

    Args:
        definition: The transaction or transfer template.
        resolved: Its recurrence as it stands, books attached -- the pass's
            (:func:`~app.services.recurring_definition.resolved_rule_of`) at
            an unarchive or an edit, the opening door's composition over the
            governing books at a restatement -- or ``None`` when it has no
            rule or the owner no pay periods.
        calendar: The owner's pay calendar.

    Returns:
        The :class:`UnarchiveScope`.

    Raises:
        RecurrenceGenerationError: See :func:`inside_the_books`.
    """
    return UnarchiveScope(
        definition=definition,
        inside_the_books=inside_the_books(resolved, calendar),
        books_opened_on=None if resolved is None else resolved.books_opened_on,
        is_envelope=resolved is not None and resolved.is_envelope,
    )


def unarchive_scope_on(definition, ctx: BalanceContext) -> UnarchiveScope:
    """Return :func:`unarchive_scope` for *definition* as it stands on a read pass.

    The unarchive routes' and the edit doors' reading: the pass's memoised
    resolution of the stored rule, books attached
    (:func:`~app.services.recurring_definition.resolved_rule_of`), walked on
    the pass's calendar.

    Args:
        definition: The owner-checked transaction or transfer template.
        ctx: A PRE-WRITE read pass for its owner.

    Returns:
        The :class:`UnarchiveScope`.

    Raises:
        RecurrenceResolutionError: The stored rule cannot be resolved
            (:func:`~app.services.recurring_definition.resolved_rule_of`).
        RecurrenceGenerationError: See :func:`inside_the_books`.
    """
    return unarchive_scope(
        definition, resolved_rule_of(definition, ctx), ctx.calendar(),
    )


def stays_deleted_notice(scope: UnarchiveScope) -> str | None:
    """Return the sentence naming the rows an unarchive leaves deleted, or ``None``.

    Ruling **R-PC95**'s words: ``1 item due 2026-03-01 stays deleted: it
    falls inside Checking's books, which open 2026-03-08.``  An envelope's row
    is named by its paycheck's last day, the day its books compare.

    **The accounts are read here, and only on this path.**  The floor carries
    the latest opening day among the accounts the definition moves money in,
    not which account set it, and naming that account is what makes the
    sentence one the owner can check; a transfer whose two accounts open the
    same day names both.  Reached only when a row stays deleted, so an
    unarchive that leaves none reads nothing more.

    Args:
        scope: The unarchive's :class:`UnarchiveScope`, read before its
            restore.

    Returns:
        The sentence, or ``None`` when every hidden row is restored.
    """
    _table_order, model, _template_fk = rows_of(scope.definition)
    compared = sorted(
        scope.inside_the_books[occurs_on]
        for (occurs_on,) in (
            db.session.query(model.occurs_on)
            .filter(*scope.stays_deleted())
            .all()
        )
    )
    if not compared:
        return None
    first, last = compared[0].isoformat(), compared[-1].isoformat()
    span = first if first == last else f"{first} to {last}"
    if scope.is_envelope:
        where = f"in the paycheck{'' if len(compared) == 1 else 's'} ending {span}"
    else:
        where = f"due {span}"
    books = " and ".join(
        f"{db.session.get(Account, account_id).name}'s"
        for account_id in definition_money_accounts(scope.definition)
        if _opened_on(account_id) == scope.books_opened_on
    )
    if len(compared) == 1:
        return (
            f"1 item {where} stays deleted: it falls inside {books} books, "
            f"which open {scope.books_opened_on.isoformat()}."
        )
    return (
        f"{len(compared)} items {where} stay deleted: they fall inside "
        f"{books} books, which open {scope.books_opened_on.isoformat()}."
    )


def _opened_on(account_id: int) -> date | None:
    """Return *account_id*'s governing opening day, or ``None`` for no record."""
    opening = governing_account_opening(account_id)
    return None if opening is None else opening.opened_on


__all__ = [
    "UnarchiveScope",
    "inside_the_books",
    "rows_of",
    "stays_deleted_notice",
    "unarchive_scope",
    "unarchive_scope_on",
]
