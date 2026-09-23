"""
Shekel Budget App -- A still-projected recurring row is never stranded below the books

Plan step ``pay_calendar:C18-a``.  A recurring definition's occurrences start
above the books of every account it moves money in (ruling **R-PC85**), and
the walk names no occurrence whose row would land on or before them
(``recurrence._placement._lands_inside_the_books``).  A row the rule has
already generated and the owner has not settled -- an unpaid, still-Projected
bill or envelope -- is therefore STRANDED by any save that moves the books
past it: the rule stops naming its paycheck, and the maintain pass retires a
row its rule no longer names the next time it re-runs that rule over the
paycheck (plan step R10-a), raising the forecast without a word.  The owner
decides instead -- marked paid it becomes a movement, cancelled it holds
nothing, moved later it stays owed -- and three doors refuse the save until
they have:

* the opening restatement, moving an account's books LATER (ruling
  **R-PC88**; ``opening_service._reject_books_open_on_or_after_planned_rows``
  asks :func:`first_stranded_row` over the account's rows);
* a recurring definition's edit, whatever field it changes (rulings
  **R-PC90** -- an account move -- and **R-PC91**, which widened it to any
  save, an envelope box unticked included): :func:`definition_edit_refusal`,
  asked by the transaction- and transfer-template edit routes AFTER the edit
  is applied, so it reads the state the save would leave.

**ONE picker and ONE comparison, the walk's own.**  Which day of a row is
compared is :func:`~app.utils.books_boundary.row_books_day` -- the due day for
a bill (ruling **R-PC86**), the paycheck's last day for an envelope (ruling
**R-PC89**) -- and the comparison is :func:`~app.utils.books_boundary
.books_hold`.  The walk asks both of a PLACEMENT and this asks both of a
STORED row, so each door refuses exactly the saves the walk would strand a
row through: a door choosing its own day would refuse saves that strand
nothing, or pass one that does (an envelope whose due day falls after its
paycheck's end).  The stored row's due day is the day the generator stamped;
a row the owner re-dated is OVERRIDDEN, which the maintain pass keeps as a
conflict rather than retiring, so reading its own day errs toward refusing.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data and ORM
reads in, a row or a sentence out; no Flask symbol, no writes, no clock.
"""

from dataclasses import dataclass
from datetime import date

from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.pay_calendar import FiledRow, PayCalendar
from app.utils.balance_predicates import is_projected_clause
from app.utils.books_boundary import books_hold, row_books_day


@dataclass(frozen=True)
class StrandedRow:
    """A live, still-projected recurring row a save would leave below the books.

    Attributes:
        name: The row's name, for the refusal.
        books_day: The day the books are compared with
            (:func:`~app.utils.books_boundary.row_books_day`).
        is_envelope: Whether *books_day* is the row's paycheck's last day
            (an envelope) rather than its due day, which is what the refusal
            has to say to be true.
    """

    name: str
    books_day: date
    is_envelope: bool

    def described(self) -> str:
        """Return the row as the refusals name it: its name and the day compared.

        Returns:
            ``"Rent" is still projected and due 2026-01-01`` for a bill, or
            ``"Groceries" is still projected in the paycheck ending
            2026-09-23`` for an envelope -- the day the owner has to move
            the row past, in the words ruling **R-PC91** gave it.
        """
        where = (
            f"in the paycheck ending {self.books_day.isoformat()}"
            if self.is_envelope
            else f"and due {self.books_day.isoformat()}"
        )
        return f'"{self.name}" is still projected {where}'


def first_stranded_row(
    floor: date,
    calendar: PayCalendar,
    *,
    transactions: tuple = (),
    transfers: tuple = (),
) -> StrandedRow | None:
    """Return the earliest live, still-projected recurring row books at *floor* would strand.

    The rows asked about are RECURRING and LIVE: a transaction, or a
    transfer, whose definition carries a recurrence rule, not soft-deleted,
    still in the Projected status, in any scenario (an opening is
    scenario-free, ruling **R-GX**).  "Template-linked" alone would be wrong
    by a whole class: every hand-entered one-off mints a RULE-LESS definition
    since plan step ``balance:X-bi-7c`` (a one-time transfer has one too), and
    no walk names -- so nothing retires -- a rule-less definition's row.  A
    settled row is a movement, and the movement rules speak for it.

    **Every candidate is read, not only the earliest by due day**, because
    the day compared is not one column: an envelope's is its paycheck's last
    day, which ``budget.pay_periods`` does not store (the calendar derives
    it).  Five scalar columns per row, on two doors that are not hot paths:
    an opening restatement asks about one account's projected rows, a
    definition's edit about its own.

    Args:
        floor: The day the books would open (the candidate opening, or the
            latest opening across a definition's accounts).
        calendar: The owner's pay calendar, which derives each row's
            paycheck -- the same derivation the walk's placements carry.
        transactions: SQL criteria selecting the transactions to ask about,
            beside the recurring-and-live ones this adds; empty asks none.
        transfers: The same for transfers; empty asks none.

    Returns:
        The stranded row with the EARLIEST compared day (ties to the lower
        id, transactions before transfers), or ``None`` when *floor* strands
        none.

    Raises:
        RuntimeError: A row names a period *calendar* does not hold
            (:meth:`~app.services.pay_calendar.PayCalendar.require_period`)
            -- a contradiction, since both keys are NOT NULL.
    """
    candidates = []
    if transactions:
        candidates.extend(
            (row_books_day(due_on, _period_end(calendar, Transaction, row_id, period_id),
                           is_envelope=envelope), 0, row_id, name, envelope)
            for row_id, name, due_on, period_id, envelope in (
                db.session.query(
                    Transaction.id, Transaction.name, Transaction.due_date,
                    Transaction.pay_period_id, TransactionTemplate.is_envelope,
                )
                .join(
                    TransactionTemplate,
                    TransactionTemplate.id == Transaction.template_id,
                )
                .join(
                    RecurrenceRule,
                    RecurrenceRule.transaction_template_id
                    == TransactionTemplate.id,
                )
                .filter(
                    *transactions,
                    Transaction.is_deleted.is_(False),
                    is_projected_clause(Transaction),
                )
                .all()
            )
        )
    if transfers:
        # A transfer is never an envelope, so its compared day is its due
        # day; asked through the picker all the same, so the answer has one
        # spelling for both tables.
        candidates.extend(
            (row_books_day(due_on, _period_end(calendar, Transfer, row_id, period_id),
                           is_envelope=False), 1, row_id, name, False)
            for row_id, name, due_on, period_id in (
                db.session.query(
                    Transfer.id, Transfer.name, Transfer.due_date,
                    Transfer.pay_period_id,
                )
                .join(
                    RecurrenceRule,
                    RecurrenceRule.transfer_template_id
                    == Transfer.transfer_template_id,
                )
                .filter(
                    *transfers,
                    Transfer.is_deleted.is_(False),
                    is_projected_clause(Transfer),
                )
                .all()
            )
        )
    stranded = [
        candidate for candidate in candidates
        if not books_hold(floor, candidate[0])
    ]
    if not stranded:
        return None
    books_day, _table, _row_id, name, envelope = min(stranded)
    return StrandedRow(name=name, books_day=books_day, is_envelope=envelope)


def _period_end(calendar: PayCalendar, model, row_id: int, period_id: int) -> date:
    """Return the last day of the paycheck a stored row is filed in.

    Through :meth:`~app.services.pay_calendar.PayCalendar.require_period`, the
    twin for a row already FILED: its key is NOT NULL, so a period the
    calendar lacks is a contradiction, not "not found".

    Args:
        calendar: The owner's pay calendar.
        model: The row's mapped class, for the refusal's table name.
        row_id: The row's id.
        period_id: The ``budget.pay_periods.id`` it is filed in.

    Returns:
        The period's derived ``end_date``.
    """
    return calendar.require_period(FiledRow(
        table=model.__table__.fullname, row_id=row_id, period_id=period_id,
    )).end_date


def definition_edit_refusal(template, ctx) -> str | None:
    """Return why saving *template*'s edit would strand a row, or ``None``.

    Rulings **R-PC90** and **R-PC91** (developer, 2026-09-22): a recurring
    definition's edit is refused when the state it would SAVE leaves a live,
    still-projected row of that definition on or before its books --
    whatever field changed.  An account moved onto one whose books open
    later, an envelope box unticked (its rows then compare on their due day,
    not their paycheck's last day), or a row that already sat below its
    books: one check, on the saved state, so no field has to be remembered.

    **Asked after the edit is applied and before regeneration**, so the
    floor and the flag are the save's own: the floor through the SAME
    composition the walk takes (:meth:`~app.services.balance_at.BalanceContext
    .resolved_recurrence_of`, reading the definition's accounts off the edited
    object), and the envelope flag through the query below, which autoflushes
    the edited definition before it reads the column.

    Args:
        template: The edited
            :class:`~app.models.transaction_template.TransactionTemplate` or
            :class:`~app.models.transfer_template.TransferTemplate`, its new
            field values and rule applied, not yet committed.
        ctx: The route's read pass
            (:class:`~app.services.balance_at.BalanceContext`) for the owner.

    Returns:
        The refusal's sentence, or ``None`` when the save strands nothing --
        including a definition with no rule, whose rows no walk names, and an
        owner with no pay periods, who has no rows.
    """
    rule = template.recurrence_rule
    if rule is None:
        return None
    resolved = ctx.resolved_recurrence_of(rule)
    if resolved is None or resolved.books_opened_on is None:
        return None
    if isinstance(template, TransferTemplate):
        scope = {"transfers": (Transfer.transfer_template_id == template.id,)}
    else:
        scope = {"transactions": (Transaction.template_id == template.id,)}
    row = first_stranded_row(resolved.books_opened_on, ctx.calendar(), **scope)
    if row is None:
        return None
    return (
        f"This change cannot be saved: {row.described()}, and the books it "
        f"would move money in open {resolved.books_opened_on.isoformat()}.  "
        "An opening is the balance at the END of its day, so that unpaid item "
        "would sit inside it and stop being planned.  Mark it paid, cancel it "
        "or move it later first, then make the change."
    )


__all__ = [
    "StrandedRow",
    "definition_edit_refusal",
    "first_stranded_row",
]
