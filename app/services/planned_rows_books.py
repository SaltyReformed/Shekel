"""
Shekel Budget App -- A still-projected recurring row is never stranded below the books

Plan step ``pay_calendar:C18-a``.  A recurring definition's occurrences start
above the books of every account it moves money in (ruling **R-PC85**), and
the walk names no occurrence whose row would land on or before them
(``recurrence._placement._lands_inside_the_books``).  A row the rule has
already generated and the owner has not settled -- an unpaid, still-Projected
bill or envelope -- is therefore STRANDED by any save that makes the books
drop the occurrence it answers: the rule stops naming it, and the maintain
pass retires a row its rule no longer names (plan step R10-a) whenever a pass
reaches that row's paycheck -- the save's own regeneration for a paycheck
ending on or after the edit's effective date (today unless the owner types an
earlier one), a later pass for an older one.  A row holding the owner's
records is retained with a notice instead; a record-free one is deleted and
the forecast rises by its amount without a word.  The owner decides instead --
marked paid it becomes a movement, cancelled it holds nothing, moved later it
stays owed -- and the doors refuse the save until they have:

* the opening restatement, moving an account's books LATER (ruling
  **R-PC88**; ``opening_service._reject_books_open_on_or_after_planned_rows``);
* a recurring definition's edit, whatever field it changes (rulings
  **R-PC90** -- an account move -- and **R-PC91**, which widened it to any
  save, an envelope box unticked or a due day cleared included):
  :func:`definition_edit_refusal`, asked by the transaction- and
  transfer-template edit routes AFTER the edit is applied, so it reads the
  state the save would leave.

**The WALK decides, never a stored day** (the adversarial review of this
step, finding H1).  :func:`first_row_below_the_books` asks
:func:`~app.services.recurrence.placements_below_the_books` which occurrences
the books drop from the walk the save would leave, and matches a row to the
occurrence it answers by ``occurs_on`` -- the key the maintain pass itself
matches by -- so a door refuses exactly the rows the pass would stop naming.
Reading a row's STORED due day instead let a save through that strands one:
the regeneration re-dates every still-named row by the NEW rule, so clearing
a bill's due day moves its cash day back onto its scheduled day, inside the
books, while the stored day still read as outside them.  The day a refusal
names is the walk's for the placement
(:meth:`~app.services.recurrence.ResolvedRecurrence.books_day`: the due day
for a bill, ruling **R-PC86**; the paycheck's last day for an envelope,
ruling **R-PC89**) -- the row as the save would leave it, which is the
wording the developer ruled for R-PC91's message.

**An OVERRIDDEN row is refused over too.**  The pass keeps a row the owner
re-priced as a conflict rather than retiring it, but the conflict chooser's
"use" hands it back to its definition, and the next pass that reaches it then
retires it like any other.  A row that answers NO occurrence (``occurs_on``
``NULL``) is never matched: no walk names it with or without the books, so
the books cannot strand it (finding REC-516 retains it either way).

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data and ORM
reads in, a row or a sentence out; no Flask symbol, no writes, no clock.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_

from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import definition_books, resolved_with_books
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    ResolvedRecurrence,
    placements_below_the_books,
    recurrence_spec,
)
from app.services.recurring_definition import resolved_rule_of
from app.utils.balance_predicates import is_projected_clause


@dataclass(frozen=True)
class StrandedRow:
    """A live, still-projected recurring row a save would leave below the books.

    Attributes:
        name: The row's name, for the refusal.
        books_day: The day the books are compared with, for the placement
            the walk gives the row
            (:meth:`~app.services.recurrence.ResolvedRecurrence.books_day`).
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


@dataclass(frozen=True)
class DefinitionWalk:
    """One recurring definition as a save would leave it, and where its rows are.

    Attributes:
        resolved: The definition's recurrence, carrying the books floor and
            the envelope flag the save would leave.
        transactions: SQL criteria selecting the definition's transactions;
            empty asks none.
        transfers: The same for its transfers; empty asks none.
    """

    resolved: ResolvedRecurrence
    transactions: tuple = ()
    transfers: tuple = ()


def first_row_below_the_books(
    calendar: PayCalendar, walks: Iterable[DefinitionWalk],
) -> StrandedRow | None:
    """Return the earliest live, still-projected row whose occurrence the books drop.

    **The one question every door refusing to strand a row asks**, over one
    definition (an edit) or every definition moving money in an account (an
    opening restatement): of the occurrences
    :func:`~app.services.recurrence.placements_below_the_books` reports for a
    walk -- those its rule names that its books floor drops -- which does a
    live row still answer?  A row answers the occurrence in its
    ``occurs_on``, the key the maintain pass matches by, so the rows found
    are exactly the ones the pass would stop naming.  Live means not
    soft-deleted and still Projected, in any scenario (one definition's rule
    walks the same in every scenario, and an opening is scenario-free,
    ruling **R-GX**); a settled row is a movement, and the movement rules
    speak for it.

    Args:
        calendar: The owner's pay calendar, which every walk places on.
        walks: The definitions to ask about, each as the save would leave it.

    Returns:
        The stranded row with the EARLIEST compared day across every walk
        (ties to transactions before transfers, then the lower id), named by
        the day its walk compares for its placement, or ``None`` when the
        save strands none.
    """
    candidates = []
    for walk in walks:
        compared = {
            placement.occurrence: walk.resolved.books_day(placement.period)
            for placement in placements_below_the_books(walk.resolved, calendar)
        }
        if not compared:
            continue
        for table_order, model, criteria in (
            (0, Transaction, walk.transactions), (1, Transfer, walk.transfers),
        ):
            if not criteria:
                continue
            candidates.extend(
                (compared[occurs_on], table_order, row_id, name, walk.resolved.is_envelope)
                for row_id, name, occurs_on in (
                    db.session.query(model.id, model.name, model.occurs_on)
                    .filter(
                        *criteria,
                        model.occurs_on.in_(list(compared)),
                        model.is_deleted.is_(False),
                        is_projected_clause(model),
                    )
                    .all()
                )
            )
    if not candidates:
        return None
    books_day, _table_order, _row_id, name, is_envelope = min(candidates)
    return StrandedRow(name=name, books_day=books_day, is_envelope=is_envelope)


def first_row_an_opening_strands(
    account_id: int, opened_on: date, calendar: PayCalendar,
) -> StrandedRow | None:
    """Return the earliest row books opening on *opened_on* would strand, or ``None``.

    **Ruling R-PC88's one producer**, read by the restatement door's refusal
    (``opening_service._reject_books_open_on_or_after_planned_rows``) and by
    the books-opening card's date ceiling (``routes/accounts/opening``), so
    the day the card stops at and the row the door names cannot come from two
    answers.  Every RECURRING definition that moves money in the account is
    walked with the books it would have if the account opened on *opened_on*
    -- the later of that day and the definition's OTHER accounts' governing
    openings (:func:`~app.services.balance_at.definition_books`, the candidate
    standing in for this account's own), composed by the ONE composition
    (:func:`~app.services.balance_at.resolved_with_books`).  Each walk asks
    after the definition's OWN rows, wherever they sit: a row left on an
    account the definition has since moved off (ruling **R-CC36** keeps a
    row outside the maintain window where it was) is bounded by the
    definition's walk, not by the account it is filed under.

    A rule-less definition is not asked: no walk names its rows, so no books
    can strand them.

    Args:
        account_id: The account whose books would open on *opened_on*.
            Assumed the owner's; the definitions referring to it are too.
        opened_on: The candidate opening day.
        calendar: The owner's pay calendar.

    Returns:
        The earliest stranded row, or ``None``.

    Raises:
        RecurrenceResolutionError: A definition's stored rule cannot be
            resolved (:func:`~app.services.recurrence.resolved_spec`).
    """
    candidate = {account_id: opened_on}
    walks = []
    for definition, criteria in _recurring_definitions_moving_money_in(account_id):
        resolved = resolved_with_books(
            recurrence_spec(definition.recurrence_rule), calendar,
            # A fresh memo holding the candidate: the definition's other
            # accounts are read as they stand, this one as it would.
            definition_books(definition, dict(candidate)),
        )
        if resolved is not None:
            walks.append(DefinitionWalk(resolved, **criteria))
    return first_row_below_the_books(calendar, walks)


def _recurring_definitions_moving_money_in(account_id: int) -> list:
    """Return ``(definition, criteria)`` for every recurring definition touching *account_id*.

    A transaction template whose account it is, and a transfer template it is
    either end of, each carrying a recurrence rule; *criteria* selects that
    definition's own rows for :class:`DefinitionWalk`.

    Args:
        account_id: The account.

    Returns:
        The pairs, transaction templates first, each table by id.
    """
    transaction_definitions = (
        db.session.query(TransactionTemplate)
        .join(
            RecurrenceRule,
            RecurrenceRule.transaction_template_id == TransactionTemplate.id,
        )
        .filter(TransactionTemplate.account_id == account_id)
        .order_by(TransactionTemplate.id)
        .all()
    )
    transfer_definitions = (
        db.session.query(TransferTemplate)
        .join(
            RecurrenceRule,
            RecurrenceRule.transfer_template_id == TransferTemplate.id,
        )
        .filter(or_(
            TransferTemplate.from_account_id == account_id,
            TransferTemplate.to_account_id == account_id,
        ))
        .order_by(TransferTemplate.id)
        .all()
    )
    return [
        (definition, {"transactions": (Transaction.template_id == definition.id,)})
        for definition in transaction_definitions
    ] + [
        (definition, {"transfers": (Transfer.transfer_template_id == definition.id,)})
        for definition in transfer_definitions
    ]


def definition_edit_refusal(template, ctx) -> str | None:
    """Return why saving *template*'s edit would strand a row, or ``None``.

    Rulings **R-PC90** and **R-PC91** (developer, 2026-09-22): a recurring
    definition's edit is refused when the state it would SAVE leaves a live,
    still-projected row of that definition answering an occurrence the books
    drop -- whatever field changed.  An account moved onto one whose books
    open later, an envelope box unticked (its rows then compare on their due
    day, not their paycheck's last day), a due day cleared (the row's cash
    day moves back onto its scheduled day), or a row that already sat below
    its books: one check, on the saved state, so no field has to be
    remembered.

    **Asked after the edit is applied and before regeneration**, so the
    rule, the floor and the envelope flag are the save's own, all through
    the SAME composition the walk takes (the pass's memoised resolution,
    :func:`~app.services.recurring_definition.resolved_rule_of`, reading the
    rule's columns and the definition's accounts and ``is_envelope`` off the
    edited objects).

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
    resolved = resolved_rule_of(template, ctx)
    if resolved is None:
        return None
    if isinstance(template, TransferTemplate):
        scope = {"transfers": (Transfer.transfer_template_id == template.id,)}
    else:
        scope = {"transactions": (Transaction.template_id == template.id,)}
    row = first_row_below_the_books(
        ctx.calendar(), (DefinitionWalk(resolved, **scope),),
    )
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
    "DefinitionWalk",
    "StrandedRow",
    "definition_edit_refusal",
    "first_row_an_opening_strands",
    "first_row_below_the_books",
]
