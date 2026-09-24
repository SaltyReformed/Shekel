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
are left deleted, and the unarchive says so (:func:`stays_deleted_notice`),
naming every one.  **The scope judges each hidden row as it stands and never
asks how the row got there.**  A hand delete made before a books move is one
way; the C18-a round-4 review measured another -- an unpaid copy revived by a
status revert (refused since ruling **R-PC97**) and then hidden by the
archive -- and it refuted the sentence this paragraph used to end on, that
such a row "can only be one its owner deleted by hand".  So no argument about
which writers can leave one is made here.

**A row a walk names is judged by the WALK, never its stored day** (the
round-1 review's finding H1): :func:`books_reading` asks
:func:`~app.services.recurrence.occurrence_walk` which occurrences the books
drop, and a row is matched to the occurrence it answers by
``occurs_on``, the key the maintain pass matches by -- the same question, of
the same producer, the refusals ask.  A stored day would be wrong for such a
row, because the regeneration re-dates every row its walk still names.

**A row NO walk names is judged by its OWN day** (ruling **R-PC96**, the
round-4 review's H1).  A rule-less definition's rows -- an item that no
longer repeats, a one-time transfer -- and a recurring definition's rows
answering no occurrence (``occurs_on`` ``NULL``, a carried-forward leftover)
have no occurrence to look up, and the scope used to restore every one of
them: measured, an archived $50.00 transfer whose cadence was cleared while
it was archived came back due ON its books after a restatement onto that
day, $50.00 counted a second time.  No regeneration walks such a row, so
nothing re-dates it and its stored day IS its day: the one picker
(:func:`~app.utils.books_boundary.row_books_day`: its due day, or its
paycheck's last day for an envelope) gives the day
:func:`~app.utils.books_boundary.books_hold` compares with the floor, and a
row on or before the books stays deleted like the rest.  **So is an ORPHAN**
(ruling **R-PC98**, the round-5 review's H2): a recurring definition's row
whose ``occurs_on`` its CURRENT walk names nowhere, left behind by a rule
edit -- measured, a start moved later while archived, the books restated
onto the second old row's day, and the unarchive restoring both old rows
inside the opening, ``-$20.00`` counted a second time.  R-PC98 judges an
orphan the same way at every door that bounds a planned row by the books,
not only here (``app.services.planned_rows_books``).

**The root is not fixed here.**  An unarchive still restores a row its owner
deleted by hand ABOVE the books, and the conflict chooser revives one (ledger
row **REC-535**): both follow from the shared flag, which ledger row
**REC-536** holds.  Plan step ``recurrence:R22`` designs the from-scratch
model -- a recurring item's unpaid occurrences computed from its schedule and
never stored, only the owner's acts kept -- under which no hidden copy exists
for an unarchive to bring back.

Services-boundary discipline (``CLAUDE.md`` Architecture): ORM reads in, SQL
criteria and a sentence out; no Flask symbol, no write, no clock.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, false, or_

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import (
    BalanceContext,
    definition_books,
    definition_money_accounts,
)
from app.services.cash_ledger import governing_account_opening
from app.services.pay_calendar import FiledRow, PayCalendar
from app.services.recurrence import ResolvedRecurrence, occurrence_walk
from app.services.recurring_definition import resolved_rule_of
from app.utils.balance_predicates import is_projected_clause
from app.utils.books_boundary import books_hold, row_books_day


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
            (:attr:`BooksReading.inside`): the hidden rows answering one stay
            deleted.
        own_day_inside: Every hidden row NO walk names -- a rule-less or
            undated row (ruling **R-PC96**), or an orphan (ruling
            **R-PC98**) -- whose own compared day is on or before the books,
            by row id, mapped to that day: these stay deleted too.
        books_opened_on: The definition's books floor, which the notice
            quotes; ``None`` when it has none, and then nothing stays.
        is_envelope: Whether the compared day is a paycheck's last day
            (ruling **R-PC89**) rather than a due day, which the notice has
            to say to be true.
    """

    definition: TransactionTemplate | TransferTemplate
    inside_the_books: dict
    own_day_inside: dict
    books_opened_on: date | None
    is_envelope: bool

    def _hidden(self) -> tuple:
        """Return the criteria for the definition's soft-deleted, still-Projected rows."""
        return _hidden_rows(self.definition)

    def restores(self) -> tuple:
        """Return the SQL criteria selecting the rows the unarchive restores.

        Returns:
            The criteria, for ``query.filter(*criteria)``: the definition's
            soft-deleted still-Projected rows, less those answering an
            occurrence its books drop and less those no walk names whose own
            day the books hold (:attr:`own_day_inside`).  A row answering NO
            occurrence (``occurs_on`` ``NULL``) passes the first test, since
            no walk names it with or without the books; the second is the one
            that judges it.
        """
        _table_order, model, _template_fk = rows_of(self.definition)
        criteria = list(self._hidden())
        if self.inside_the_books:
            criteria.append(or_(
                model.occurs_on.is_(None),
                model.occurs_on.notin_(list(self.inside_the_books)),
            ))
        if self.own_day_inside:
            criteria.append(model.id.notin_(list(self.own_day_inside)))
        return tuple(criteria)

    def stays_deleted(self) -> tuple:
        """Return the SQL criteria selecting the rows the unarchive leaves deleted.

        Returns:
            The criteria, for ``query.filter(*criteria)``: the definition's
            soft-deleted still-Projected rows answering an occurrence its
            books drop, or named by no walk and held by the books on their
            own day -- the complement of :meth:`restores` among its hidden
            rows.
        """
        _table_order, model, _template_fk = rows_of(self.definition)
        held = []
        if self.inside_the_books:
            held.append(model.occurs_on.in_(list(self.inside_the_books)))
        if self.own_day_inside:
            held.append(model.id.in_(list(self.own_day_inside)))
        if not held:
            return (*self._hidden(), false())
        return (*self._hidden(), or_(*held))


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


def _hidden_rows(definition) -> tuple:
    """Return the criteria for *definition*'s soft-deleted, still-Projected rows.

    Args:
        definition: A transaction or transfer template.

    Returns:
        The criteria, for ``query.filter(*criteria)``: the rows its archive
        hid, and any its owner deleted by hand (ruling **R-PC95**).
    """
    _table_order, model, template_fk = rows_of(definition)
    return (
        template_fk == definition.id,
        is_projected_clause(model),
        model.is_deleted.is_(True),
    )


@dataclass(frozen=True)
class BooksReading:
    """What one walk of a recurring definition says about its rows and its books.

    Attributes:
        inside: ``{occurrence: compared day}`` for every occurrence the
            definition's books drop (the rows answering one are judged by
            the WALK's day for its placement).
        named: Every occurrence the walk names, kept or dropped -- so a row
            whose ``occurs_on`` is in neither half is an ORPHAN (ruling
            **R-PC98**).
    """

    inside: dict
    named: frozenset

    def is_orphan(self, occurs_on: date | None) -> bool:
        """Return whether a row dated *occurs_on* answers no occurrence the walk names.

        Ruling **R-PC98** (developer 2026-09-23, the C18-a round-5 review's
        H2): a still-Projected row a rule EDIT left behind -- its schedule
        moved, its occurrence no longer produced -- is named by no walk, so
        nothing re-dates it and it is judged by its own day
        (:func:`own_books_day`), as ruling **R-PC96** judges a rule-less or
        undated row at an unarchive.  An UNDATED row (``occurs_on``
        ``NULL``) is not an orphan: R-PC96 judges it at the unarchive alone,
        and the books move stays allowed for it, as that ruling says.

        Args:
            occurs_on: The row's ``occurs_on``.

        Returns:
            ``True`` for a dated row the walk names nowhere.
        """
        return occurs_on is not None and occurs_on not in self.named

    def orphan_clause(self, model):
        """Return :meth:`is_orphan` as a SQL criterion over *model*'s rows.

        The same predicate in the query's dialect, stated beside the Python
        one so the two cannot drift -- the shape ``balance_predicates`` gives
        its pairs.  A door asks it in SQL so the rows it loads are only the
        orphans, where the account edit page's card asks at every render.

        Args:
            model: ``Transaction`` or ``Transfer``.

        Returns:
            The criterion, for ``query.filter``.
        """
        return and_(
            model.occurs_on.isnot(None),
            model.occurs_on.notin_(list(self.named)),
        )


def books_reading(
    resolved: ResolvedRecurrence | None, calendar: PayCalendar,
) -> BooksReading:
    """Return what *resolved*'s walk drops and names, from ONE walk.

    **The one reading of what a definition's books drop and what its rule
    names**, taken by the unarchive here and by the refusals in
    ``app.services.planned_rows_books`` (the opening and edit doors'
    :func:`~app.services.planned_rows_books.first_row_below_the_books` and
    the revert's), so a row the unarchive leaves deleted and a row a refusal
    names come from one walk.  Both halves are
    :func:`~app.services.recurrence.occurrence_walk`'s, so asking for the
    orphans (ruling **R-PC98**) walks nothing twice.

    Args:
        resolved: The definition's recurrence with its books floor attached,
            or ``None`` -- a definition with no rule, or an owner with no pay
            periods -- which drops and names nothing.
        calendar: The owner's pay calendar.

    Returns:
        The :class:`BooksReading`: every dropped occurrence mapped to
        :meth:`~app.services.recurrence.ResolvedRecurrence.books_day` for its
        placement (the due day for a bill, ruling **R-PC86**; the paycheck's
        last day for an envelope, ruling **R-PC89**), and every occurrence
        named.

    Raises:
        RecurrenceGenerationError: See
            :func:`~app.services.recurrence.occurrence_walk`.
    """
    if resolved is None:
        return BooksReading(inside={}, named=frozenset())
    walk = occurrence_walk(resolved, calendar)
    return BooksReading(
        inside={
            placement.occurrence: resolved.books_day(placement.period)
            for placement in walk.below_the_books
        },
        named=frozenset(
            placement.occurrence
            for placement in (*walk.kept, *walk.below_the_books)
        ),
    )


def own_books_day(row, calendar: PayCalendar, *, is_envelope: bool) -> date:
    """Return the day the books are compared with for a row no walk names.

    Rulings **R-PC96** and **R-PC98**: no regeneration re-dates such a row,
    so its stored day IS its day, and the one picker
    (:func:`~app.utils.books_boundary.row_books_day`) chooses between its
    due day and its paycheck's last day (an envelope, ruling **R-PC89**).

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`.
        calendar: The owner's pay calendar, which gives the paycheck's end.
        is_envelope: Whether its definition is an envelope.

    Returns:
        The day :func:`~app.utils.books_boundary.books_hold` is asked of.
    """
    return row_books_day(
        row.due_date, calendar.require_period(FiledRow.for_row(row)).end_date,
        is_envelope=is_envelope,
    )


def unarchive_scope(
    definition, resolved: ResolvedRecurrence | None, calendar: PayCalendar,
) -> UnarchiveScope:
    """Return what unarchiving *definition* would restore and leave deleted.

    A recurring definition's floor and envelope flag are the ones its
    resolution carries -- the composition its walk took.  A rule-less
    definition has no resolution, so they are read off it through the SAME
    producer that composition reads
    (:func:`~app.services.balance_at.definition_books`), for the rows judged
    on their own day (ruling **R-PC96**).

    Args:
        definition: The transaction or transfer template.
        resolved: Its recurrence as it stands, books attached -- the pass's
            (:func:`~app.services.recurring_definition.resolved_rule_of`) at
            an unarchive or an edit, the opening door's composition over the
            governing books at a restatement -- or ``None`` when it has no
            rule or the owner no pay periods (who then has no rows).
        calendar: The owner's pay calendar.

    Returns:
        The :class:`UnarchiveScope`.

    Raises:
        RecurrenceGenerationError: See :func:`books_reading`.
    """
    if resolved is None:
        books = definition_books(definition, {})
        books_opened_on, is_envelope = books.opened_on, books.is_envelope
    else:
        books_opened_on = resolved.books_opened_on
        is_envelope = resolved.is_envelope
    reading = books_reading(resolved, calendar)
    return UnarchiveScope(
        definition=definition,
        inside_the_books=reading.inside,
        own_day_inside=_own_day_inside(
            definition, reading, books_opened_on, is_envelope, calendar,
        ),
        books_opened_on=books_opened_on,
        is_envelope=is_envelope,
    )


def scope_holding_nothing_back(definition) -> UnarchiveScope:
    """Return the scope of an unarchive that would restore every hidden row.

    What an edit door counts for an ARCHIVED definition whose stored rule the
    recurrence package cannot walk
    (``planned_rows_books.restorable_before_the_edit``): with no walk there is
    no telling which rows the books hold, so every hidden row is counted --
    the careful answer at the form that repairs such a rule.

    Args:
        definition: The transaction or transfer template.

    Returns:
        An :class:`UnarchiveScope` holding nothing back.
    """
    return UnarchiveScope(
        definition=definition,
        inside_the_books={},
        own_day_inside={},
        books_opened_on=None,
        is_envelope=False,
    )


def _own_day_inside(
    definition, reading: BooksReading, books_opened_on: date | None,
    is_envelope: bool, calendar: PayCalendar,
) -> dict:
    """Return the hidden rows no walk names that the books hold, by their own day.

    Ruling **R-PC96**: every hidden row of a RULE-LESS definition, and a
    recurring definition's hidden rows answering no occurrence (``occurs_on``
    ``NULL``), are compared on the day :func:`own_books_day` picks off the
    row itself -- its due day, or its paycheck's last day for an envelope --
    because no regeneration re-dates them.  Ruling **R-PC98** adds a
    recurring definition's hidden ORPHANS: rows whose ``occurs_on`` its walk
    names nowhere (:meth:`BooksReading.is_orphan`), left behind by a rule
    edit made while it was archived.

    Args:
        definition: The transaction or transfer template.
        reading: Its walk's :class:`BooksReading` (empty for a rule-less
            definition, whose every row is judged here).
        books_opened_on: Its books floor, or ``None`` (nothing is held).
        is_envelope: Whether it is an envelope (ruling **R-PC89**).
        calendar: The owner's pay calendar, which gives a paycheck's last day.

    Returns:
        ``{row id: compared day}`` for each such row on or before the books.
    """
    if books_opened_on is None:
        return {}
    _table_order, model, _template_fk = rows_of(definition)
    criteria = list(_hidden_rows(definition))
    if definition.recurs:
        criteria.append(or_(
            model.occurs_on.is_(None), reading.orphan_clause(model),
        ))
    held = {}
    for row in db.session.query(model).filter(*criteria).all():
        day = own_books_day(row, calendar, is_envelope=is_envelope)
        if not books_hold(books_opened_on, day):
            held[row.id] = day
    return held


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
        RecurrenceGenerationError: See :func:`books_reading`.
    """
    return unarchive_scope(
        definition, resolved_rule_of(definition, ctx), ctx.calendar(),
    )


def stays_deleted_notice(scope: UnarchiveScope) -> str | None:
    """Return the sentence naming the rows an unarchive leaves deleted, or ``None``.

    Ruling **R-PC95**'s words: ``1 item due 2026-03-01 stays deleted: it
    falls inside Checking's books, which open 2026-03-08.``  An envelope's row
    is named by its paycheck's last day, the day its books compare.  A row a
    walk names takes its occurrence's compared day, a row no walk names its
    own (ruling **R-PC96**), so every row the scope leaves deleted is named.

    Args:
        scope: The unarchive's :class:`UnarchiveScope`, read before its
            restore.

    Returns:
        The sentence, or ``None`` when every hidden row is restored.
    """
    _table_order, model, _template_fk = rows_of(scope.definition)
    compared = sorted(
        scope.own_day_inside[row_id]
        if row_id in scope.own_day_inside
        else scope.inside_the_books[occurs_on]
        for row_id, occurs_on in (
            db.session.query(model.id, model.occurs_on)
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
    books = books_named(scope.definition, scope.books_opened_on)
    if len(compared) == 1:
        return (
            f"1 item {where} stays deleted: it falls inside {books} books, "
            f"which open {scope.books_opened_on.isoformat()}."
        )
    return (
        f"{len(compared)} items {where} stay deleted: they fall inside "
        f"{books} books, which open {scope.books_opened_on.isoformat()}."
    )


def books_named(definition, books_opened_on: date) -> str:
    """Return the accounts whose books set *definition*'s floor, as a sentence names them.

    ``Checking's``, or ``Src's and Dst's`` for a transfer whose two accounts
    open the same day.  **The one statement of which account a books sentence
    names**, read by the unarchive's notice and by the revert refusal
    (``planned_rows_books.reject_revert_below_the_books``, ruling
    **R-PC97**).  The floor carries the latest opening day among the accounts
    the definition moves money in, not which account set it, and naming that
    account is what makes the sentence one the owner can check.  The openings
    are read here and only when a sentence is being written, so a door that
    refuses nothing reads nothing more.

    Args:
        definition: The transaction or transfer template.
        books_opened_on: Its books floor -- a day at least one of its
            accounts' governing openings falls on.

    Returns:
        Each such account's name in the possessive, joined by ``and``.
    """
    return " and ".join(
        f"{db.session.get(Account, account_id).name}'s"
        for account_id in definition_money_accounts(definition)
        if _opened_on(account_id) == books_opened_on
    )


def _opened_on(account_id: int) -> date | None:
    """Return *account_id*'s governing opening day, or ``None`` for no record."""
    opening = governing_account_opening(account_id)
    return None if opening is None else opening.opened_on


__all__ = [
    "BooksReading",
    "UnarchiveScope",
    "books_named",
    "books_reading",
    "own_books_day",
    "rows_of",
    "scope_holding_nothing_back",
    "stays_deleted_notice",
    "unarchive_scope",
    "unarchive_scope_on",
]
