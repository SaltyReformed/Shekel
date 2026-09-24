"""
Shekel Budget App -- No books move or definition edit strands an unpaid recurring row

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
marked paid it becomes a movement, cancelled it holds nothing -- and the doors
refuse the save until they have:

* the opening restatement, moving an account's books LATER (ruling
  **R-PC88**; ``opening_service._reject_books_open_on_or_after_planned_rows``);
* a recurring definition's edit, whatever field it changes (rulings
  **R-PC90** -- an account move -- and **R-PC91**, which widened it to any
  save, an envelope box unticked or a due day cleared included):
  :func:`definition_edit_refusal`, asked by the transaction- and
  transfer-template edit routes AFTER the edit is applied, so it reads the
  state the save would leave.

**A row the books already drop may not become unpaid again** (ruling
**R-PC97**, developer 2026-09-23, the round-4 review's H2).  Another way a
still-Projected row comes to answer an occurrence below the books is the
REVERSE move: a paid, received, credited or cancelled row set back to
Projected, which the database's books boundary cannot see (its movement
trigger watches a row only while it carries a settle day, and a revert
clears it) and which no door above asked about.  Measured: a cancelled row
reverted onto its books day took $10.00 off the forecast (-90.00 ->
-100.00), and such a row is one the maintain pass deletes without a word
once a pass reaches its paycheck (plan step R10-a).  Round 9's door census
found this and the conflict chooser (below) as the doors left for a
recurring row; a census, not an argument, so a new writer is not covered by
it.  :func:`reject_revert_below_the_books` asks the same
walk the doors above ask, and the status seam refuses a revert it answers
(``status_seam.apply_status_change`` for a transaction,
``transfer_service.apply_status_to_all_three`` for a transfer, each row
type's one status door).  **A stopgap by design**: plan step
``recurrence:R22`` designs the model under which no unpaid copy is stored, so
a revert only deletes a record and there is nothing to refuse.

**An ARCHIVED definition's hidden rows count** (ruling **R-PC93**, developer
2026-09-23).  Archiving hides a definition's still-Projected rows and
unarchiving brings them back, so a books move made while it was archived used
to see nothing to strand -- and the unarchive then restored rows inside the
opening, a transfer's deleting the current paycheck's row in its maintain
pass (the round-2 review's H-B).  Both doors now count, for an archived
definition, the rows its unarchive would restore as it STANDS, before the
save being graded (:class:`~app.services.definition_unarchive.UnarchiveScope`,
the scope the unarchive routes restore by), and the refusal names such a row
as the archived definition's with the remedy that reaches it: unarchive it
first.  **The unarchive checks too** (ruling **R-PC95**, which revises
R-PC93's "it needs no check of its own"): a hand delete of a recurring row is
a soft delete indistinguishable from the archive's, so a row its owner deleted
while the definition was active, and which the books have since passed, would
otherwise come back inside the opening -- it stays deleted, and the unarchive
says so.  The scope leaves such a row out, so the refusal never names a row
the unarchive would not restore.  **Two ways back are not covered**: an
unarchive still restores a row deleted by hand ABOVE the books, and the
conflict chooser's "use the template" un-deletes one without asking the walk
(ledger rows **REC-536** and **REC-535**, the recurrence arc's, both closed
by plan step ``recurrence:R22``).  **Neither are rows added by hand** -- a
rule-less definition's rows and link-less ones: no door bounds them by the
books at all (ledger row **PC-519**).

**The WALK decides, never a stored day** (the adversarial review of this
step, finding H1).  :func:`first_row_below_the_books` asks
:func:`~app.services.definition_unarchive.books_reading` which occurrences
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
wording the developer ruled for R-PC91's message.  **The one exception is a
row the walk names NOWHERE** (ruling **R-PC98**, the round-5 review's H2): an
ORPHAN a rule edit left behind -- its start moved later, say -- answers no
occurrence, so nothing re-dates it and its own day is its day
(:func:`~app.services.definition_unarchive.own_books_day`); every door here
judges it by that day, the opening and edit doors through
:func:`first_row_below_the_books` and the revert through
:func:`reject_revert_below_the_books`, as the unarchive does.

**An OVERRIDDEN row is refused over too.**  The pass keeps a row the owner
re-priced as a conflict rather than retiring it, but the conflict chooser's
"use" hands it back to its definition, and the next pass that reaches it then
retires it like any other.  A row that answers NO occurrence (``occurs_on``
``NULL``) is never matched here: no walk names it with or without the books,
and ruling **R-PC96** judges it at the unarchive alone, leaving the books
move allowed (finding REC-516 retains it either way).

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data and ORM
reads in, a row or a sentence out; no Flask symbol, no writes, no clock.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from sqlalchemy import and_, or_

from app.exceptions import ValidationError
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import (
    BalanceContext,
    definition_books,
    money_account_columns,
    resolved_with_books,
)
from app.services.definition_unarchive import (
    BooksReading,
    UnarchiveScope,
    books_named,
    books_reading,
    own_books_day,
    rows_of,
    scope_holding_nothing_back,
    unarchive_scope,
    unarchive_scope_on,
)
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.recurrence import (
    RecurrenceGenerationError,
    RecurrenceResolutionError,
    ResolvedRecurrence,
    recurrence_spec,
)
from app.services.recurring_definition import resolved_rule_of
from app.utils.balance_predicates import is_projected_clause, reverts_to_projected
from app.utils.books_boundary import books_hold

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrandedRow:
    """A still-projected recurring row a save would leave below the books.

    Attributes:
        name: What the refusal calls it: the row's own name for a live row,
            its DEFINITION's name for a hidden one (*is_hidden*), which the
            owner finds on the archived list and nowhere else.
        books_day: The day the books are compared with, for the placement
            the walk gives the row
            (:meth:`~app.services.recurrence.ResolvedRecurrence.books_day`).
        is_envelope: Whether *books_day* is the row's paycheck's last day
            (an envelope) rather than its due day, which is what the refusal
            has to say to be true.
        is_hidden: Whether the row is hidden by its definition's archive --
            one its unarchive would bring back (ruling **R-PC93**) -- which
            the owner cannot see, let alone mark paid, until they unarchive.
    """

    name: str
    books_day: date
    is_envelope: bool
    is_hidden: bool = False

    def described(self) -> str:
        """Return the row as the refusals name it: its name and the day compared.

        Returns:
            ``"Rent" is still projected and due 2026-01-01`` for a bill, or
            ``"Groceries" is still projected in the paycheck ending
            2026-09-23`` for an envelope -- the day the owner has to move
            the row past, in the words ruling **R-PC91** gave it.  A hidden
            row reads ``"Rent" is archived and still holds an unpaid item due
            2026-01-01 that unarchiving would bring back``, R-PC93's words.
        """
        where = (
            f"in the paycheck ending {self.books_day.isoformat()}"
            if self.is_envelope
            else f"due {self.books_day.isoformat()}"
        )
        if self.is_hidden:
            return (
                f'"{self.name}" is archived and still holds an unpaid item '
                f"{where} that unarchiving would bring back"
            )
        if not self.is_envelope:
            where = f"and {where}"
        return f'"{self.name}" is still projected {where}'

    def remedy(self) -> str:
        """Return what the owner does first, the clause every refusal ends on.

        Returns:
            ``Mark it paid or cancel it first`` for a live row; for a hidden
            one, ``Unarchive "Rent", mark that item paid or cancel it`` --
            the one door that reaches a row the owner cannot see (ruling
            **R-PC93**), naming the definition so the sentence cannot be read
            as unarchiving the opening or the row.  Both are true for every
            row these refusals name (developer, 2026-09-23, round 9's M1):
            R-PC88's "or move it later" could never work, since every such
            row was made by a recurring schedule -- its due day is the
            schedule's, which the form refuses to change, and a move to a
            later paycheck keeps it, so the refusal came back unchanged -- and
            R-PC93's "or delete "Rent" for good" is refused for any definition
            with payment history (round 8's M-1).
        """
        if self.is_hidden:
            return f'Unarchive "{self.name}", mark that item paid or cancel it'
        return "Mark it paid or cancel it first"


@dataclass(frozen=True)
class DefinitionWalk:
    """One recurring definition as a save would leave it.

    Attributes:
        resolved: The definition's recurrence, carrying the books floor and
            the envelope flag the save would leave.
        definition: The
            :class:`~app.models.transaction_template.TransactionTemplate` or
            :class:`~app.models.transfer_template.TransferTemplate` itself --
            whose rows are asked after (:func:`_planned_rows`) and whose name
            a hidden row's refusal gives.
        restorable: The rows its unarchive would restore, read off the
            definition as it stands BEFORE the save
            (:class:`~app.services.definition_unarchive.UnarchiveScope`), or
            ``None`` for an active definition, which has none.  No
            default: a walk built without asking would count no hidden row,
            which is the round-2 review's H-B.
    """

    resolved: ResolvedRecurrence
    definition: TransactionTemplate | TransferTemplate
    restorable: UnarchiveScope | None


def first_row_below_the_books(
    calendar: PayCalendar, walks: Iterable[DefinitionWalk],
) -> StrandedRow | None:
    """Return the earliest planned row whose occurrence the books drop.

    **The one question every door refusing to strand a row asks**, over one
    definition (an edit) or every definition moving money in an account (an
    opening restatement): of the occurrences
    :func:`~app.services.recurrence.placements_below_the_books` reports for a
    walk -- those its rule names that its books floor drops -- which does a
    planned row still answer?  A row answers the occurrence in its
    ``occurs_on``, the key the maintain pass matches by, so the rows found
    are exactly the ones the pass would stop naming.  Planned means still
    Projected and either live or, for an archived definition, one its
    unarchive would bring back (:func:`_planned_rows`, rulings **R-PC93**
    and **R-PC95**), in any scenario (one definition's rule walks the same in
    every scenario, and an opening is scenario-free, ruling **R-GX**); a
    settled row is a movement, and the movement rules speak for it.  The
    dropped occurrences are
    :func:`~app.services.definition_unarchive.books_reading`', the one
    reading the unarchive leaves rows deleted by.

    **A planned ORPHAN is judged by its own day** (ruling **R-PC98**, the
    round-5 review's H2): a row whose ``occurs_on`` the walk names nowhere --
    left behind by a rule edit -- answers no occurrence, so no dropped one
    catches it, and it was let through inside the books (measured: a start
    moved later, the books restated onto the second old row's day,
    ``-$20.00`` counted inside the opening).  It is compared on
    :func:`~app.services.definition_unarchive.own_books_day`, as the
    unarchive compares it.

    Args:
        calendar: The owner's pay calendar, which every walk places on.
        walks: The definitions to ask about, each as the save would leave it.

    Returns:
        The stranded row with the EARLIEST compared day across every walk
        (ties to transactions before transfers, then the lower id), named by
        the day its walk compares for its placement -- or its own day, for
        an orphan -- or ``None`` when the save strands none.
    """
    candidates = []
    for walk in walks:
        reading = books_reading(walk.resolved, calendar)
        table_order, model, template_fk = rows_of(walk.definition)
        planned = _planned_rows(model, template_fk, walk)
        candidates.extend(_dropped_inside(walk, reading, planned, table_order))
        candidates.extend(
            _orphans_inside(calendar, walk, reading, planned, table_order),
        )
    if not candidates:
        return None
    first = min(candidates)
    return StrandedRow(
        name=first.definition_name if first.is_hidden else first.name,
        books_day=first.books_day,
        is_envelope=first.is_envelope,
        is_hidden=first.is_hidden,
    )


class _Candidate(NamedTuple):
    """One planned row a save would strand, ranked by field ORDER.

    ``min`` over these picks the earliest compared day, ties to transactions
    before transfers, then the lower id -- the order the refusal names one in.
    """

    books_day: date
    table_order: int
    row_id: int
    name: str
    is_envelope: bool
    is_hidden: bool
    definition_name: str


def _dropped_inside(
    walk: DefinitionWalk, reading: BooksReading, planned: tuple,
    table_order: int,
) -> list:
    """Return the refusal candidates among *walk*'s planned rows whose occurrence the books drop.

    Args:
        walk: The definition as the save would leave it.
        reading: That walk's :class:`~app.services.definition_unarchive.BooksReading`.
        planned: The definition's planned-row criteria (:func:`_planned_rows`).
        table_order: The tie-break order of the definition's row table.

    Returns:
        One :class:`_Candidate`, as :func:`first_row_below_the_books`
        ranks them, per planned row answering a dropped occurrence, named by
        the day the walk compares for its placement.
    """
    if not reading.inside:
        return []
    _table_order, model, _template_fk = rows_of(walk.definition)
    return [
        _Candidate(
            reading.inside[occurs_on], table_order, row_id, name,
            walk.resolved.is_envelope, is_deleted, walk.definition.name,
        )
        for row_id, name, occurs_on, is_deleted in (
            db.session.query(
                model.id, model.name, model.occurs_on, model.is_deleted,
            )
            .filter(*planned, model.occurs_on.in_(list(reading.inside)))
            .all()
        )
    ]


def _orphans_inside(
    calendar: PayCalendar, walk: DefinitionWalk, reading: BooksReading,
    planned: tuple, table_order: int,
) -> list:
    """Return the refusal candidates among *walk*'s planned orphans (ruling R-PC98).

    Args:
        calendar: The owner's pay calendar, which gives a paycheck's end.
        walk: The definition as the save would leave it.
        reading: That walk's :class:`~app.services.definition_unarchive.BooksReading`.
        planned: The definition's planned-row criteria (:func:`_planned_rows`).
        table_order: The tie-break order of the definition's row table.

    Returns:
        One :class:`_Candidate`, as :func:`first_row_below_the_books`
        ranks them, per planned orphan whose own day the books hold; none
        when the definition has no books floor.
    """
    floor = walk.resolved.books_opened_on
    if floor is None:
        return []
    _table_order, model, _template_fk = rows_of(walk.definition)
    candidates = []
    for row in (
        db.session.query(model)
        .filter(*planned, reading.orphan_clause(model))
        .all()
    ):
        day = own_books_day(row, calendar, is_envelope=walk.resolved.is_envelope)
        if not books_hold(floor, day):
            candidates.append(_Candidate(
                day, table_order, row.id, row.name, walk.resolved.is_envelope,
                row.is_deleted, walk.definition.name,
            ))
    return candidates


def _planned_rows(model, template_fk, walk: DefinitionWalk) -> tuple:
    """Return the SQL criteria for a definition's rows a books move must not strand.

    Its live, still-Projected rows; and, while it is ARCHIVED, the rows its
    unarchive would bring back as well (ruling **R-PC93**), read through
    :meth:`~app.services.definition_unarchive.UnarchiveScope.restores` over
    the definition as it stands -- the scope both unarchive routes restore
    by, so this counts exactly what an unarchive could put back below the
    books, and never a row the unarchive would leave deleted (ruling
    **R-PC95**).  An ACTIVE definition's soft-deleted rows are its owner's
    own deletions, which no unarchive of it restores (the unarchive routes
    refuse an active definition) and only the conflict chooser revives
    (ledger row **REC-535**).

    Args:
        model: ``Transaction`` or ``Transfer``.
        template_fk: The column naming the row's definition.
        walk: The definition's :class:`DefinitionWalk`, whose ``restorable``
            is ``None`` for an active definition.

    Returns:
        The criteria, for ``query.filter(*criteria)``.
    """
    live = and_(
        template_fk == walk.definition.id,
        is_projected_clause(model),
        model.is_deleted.is_(False),
    )
    if walk.restorable is None:
        return (live,)
    return (or_(live, and_(*walk.restorable.restores())),)


def restorable_before_the_edit(
    template, ctx: BalanceContext,
) -> UnarchiveScope | None:
    """Return the rows *template*'s unarchive would restore, asked BEFORE its edit.

    Only an ARCHIVED definition has rows an unarchive would bring back
    (ruling **R-PC93**).  Each edit door asks this before it applies a
    single field, and hands the answer to :func:`definition_edit_refusal`
    once the edit is whole: that refusal grades the state the save would
    LEAVE, and the rows an unarchive would restore are a fact about the
    state it would REPLACE.  Read after the edit, the scope would already
    leave out every hidden row the edit moves below the books, and the
    refusal could never fire over one (it would still fire over an archived
    definition's LIVE row, which no scope reads).

    **A stored rule the recurrence package cannot walk counts every hidden
    row** (the scope of an unarchive that holds nothing back), because the
    edit form is also where such a rule is repaired
    (``_recurrence_form_refusals.UNREPAIRED_CADENCE_CANNOT_BE_CLEARED``),
    and a refusal over rows it cannot place is the careful answer where a
    500 would lock the owner out of the repair.

    Args:
        template: The owner-checked transaction or transfer template, no
            field of the edit yet applied.
        ctx: The edit door's PRE-WRITE read pass.  Its resolution memo is
            keyed by the rule's spec and the definition's books, so the
            edited rule resolves afresh afterwards.

    Returns:
        The :class:`~app.services.definition_unarchive.UnarchiveScope`, or
        ``None`` for an active definition.
    """
    if template.is_active:
        return None
    try:
        return unarchive_scope_on(template, ctx)
    except (RecurrenceResolutionError, RecurrenceGenerationError):
        logger.warning(
            "Counting every hidden row of archived %s %d: its stored "
            "recurrence rule cannot be walked.",
            type(template).__name__, template.id, exc_info=True,
        )
        return scope_holding_nothing_back(template)


def first_row_an_opening_strands(
    account_id: int, opened_on: date, calendar: PayCalendar,
) -> StrandedRow | None:
    """Return the earliest row books opening on *opened_on* would strand, or ``None``.

    **Ruling R-PC88's one producer**, read by the restatement door's refusal
    (``opening_service._reject_books_open_on_or_after_planned_rows``) and by
    the books-opening card's date ceiling (``routes/accounts/opening``), so
    the day the card stops at and the row the door names cannot come from two
    answers.  Every RECURRING definition that moves money in the account --
    an archived one included, whose hidden rows its unarchive would bring
    back (ruling **R-PC93**) -- is walked with the books it would have if the
    account opened on *opened_on*: the later of that day and the
    definition's OTHER accounts' governing openings
    (:func:`~app.services.balance_at.definition_books`, the candidate
    standing in for this account's own), composed by the ONE composition
    (:func:`~app.services.balance_at.resolved_with_books`).  Each walk asks
    after the definition's OWN rows, wherever they sit: a row left on an
    account the definition has since moved off (ruling **R-CC36** keeps a
    row outside the maintain window where it was) is bounded by the
    definition's walk, not by the account it is filed under.  An archived
    definition's restorable rows are read off the SAME composition over the
    books as they stand (ruling **R-PC95**): a row those already drop stays
    deleted when it is unarchived, so this move cannot strand it.

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
        RecurrenceGenerationError: Its resolved value names something the
            walk cannot place (:func:`~app.services.recurrence
            .occurrence_walk`).
    """
    # ONE memo for every definition, holding the candidate: the definitions'
    # other accounts are read once each, as they stand, and this one as it
    # would.  A second holds every account as it stands, for the rows an
    # archived definition's unarchive would restore.
    memo = {account_id: opened_on}
    standing_memo: dict = {}
    walks = []
    for definition in _recurring_definitions_moving_money_in(account_id):
        spec = recurrence_spec(definition.recurrence_rule)
        resolved = resolved_with_books(
            spec, calendar, definition_books(definition, memo),
        )
        if resolved is None:
            continue
        restorable = None
        if not definition.is_active:
            restorable = unarchive_scope(
                definition,
                resolved_with_books(
                    spec, calendar, definition_books(definition, standing_memo),
                ),
                calendar,
            )
        walks.append(DefinitionWalk(resolved, definition, restorable))
    return first_row_below_the_books(calendar, walks)


def _recurring_definitions_moving_money_in(account_id: int) -> list:
    """Return every recurring definition moving money in *account_id*.

    A transaction or transfer template naming the account in any of its
    money-account columns (:func:`~app.services.balance_at
    .money_account_columns`, the names the books floor reads off a
    definition), each carrying a recurrence rule, archived or not.

    Args:
        account_id: The account.

    Returns:
        The definitions, transaction templates first, each table by id.
    """
    definitions = []
    for model, rule_fk in (
        (TransactionTemplate, RecurrenceRule.transaction_template_id),
        (TransferTemplate, RecurrenceRule.transfer_template_id),
    ):
        definitions.extend(
            db.session.query(model)
            .join(RecurrenceRule, rule_fk == model.id)
            .filter(or_(*(
                column == account_id
                for column in money_account_columns(model)
            )))
            .order_by(model.id)
            .all()
        )
    return definitions


def reject_revert_below_the_books(row, new_status_id: int) -> None:
    """Refuse setting *row* back to Projected when its books drop its occurrence.

    **Ruling R-PC97's one refusal** (developer 2026-09-23, the C18-a
    round-4 review's H2), asked by each row type's one status door ahead of
    any write -- ``status_seam.apply_status_change`` for a transaction,
    ``transfer_service.apply_status_to_all_three`` for a transfer, before
    either shadow is written -- and acting only on a REVERT: a paid,
    received, credited or cancelled row about to go back to Projected.  A
    row whose occurrence its definition's books drop may not:
    unpaid, it would sit inside the opening balance, and the maintain pass
    deletes a still-Projected row its rule no longer names without a word
    once a pass reaches its paycheck (plan step R10-a).  The question is the
    one the books refusals above ask -- which occurrences the walk drops
    (:func:`~app.services.definition_unarchive.books_reading`), a row
    matched to its occurrence by ``occurs_on`` -- over the definition's walk
    as it stands, composed by the ONE composition the opening door takes
    (:func:`~app.services.balance_at.resolved_with_books` over
    :func:`~app.services.balance_at.definition_books`).  A row whose
    ``occurs_on`` that walk names nowhere -- an orphan a rule edit left
    behind -- is judged by its own day instead (ruling **R-PC98**,
    :func:`~app.services.definition_unarchive.own_books_day`), as every
    other books refusal judges it.

    **Its known cost, stated rather than rounded off**: a settled row's
    LOCKED fields -- its amount, paycheck, category and due date
    (``state_machine.finalised_edit_rejection``) -- are editable only after
    a revert, so a row due on or before its books but settled after them
    can no longer have those corrected until the books are restated to open
    before its due day.  (What a settled row records is corrected in place
    and is untouched: a transaction's actual figure and the account it was
    paid from, ``transaction_service._door._correction_for_status``, and a
    transfer's settle day,
    ``transfer_service._status.apply_settle_day_correction``.)  Production
    held SIX such recurring rows on 2026-09-23, measured by this refusal
    itself over every settled templated row -- transactions 781, 865 and
    1069 (due 2026-03-26, settled 2026-03-27), transfer 322 (due 2026-04-22,
    settled 2026-04-23), transfer 102 (due 2026-03-26, settled 2026-04-06,
    inside its destination's books of 2026-04-05) and the CANCELLED
    transaction 788, which cannot be reactivated: its rule places its
    2026-03-01 occurrence into the paycheck starting 2026-03-26
    (``PERIOD_STARTING_ON_OR_AFTER``), a compared day inside Checking's
    books of that day.  **A stopgap by design**: plan step
    ``recurrence:R22`` designs the model in which no unpaid copy is stored,
    under which a revert deletes a record and this refuses nothing.

    **A schedule the recurrence package cannot walk refuses the revert**:
    with no walk there is no telling whether the books hold the row, and the
    edit form is the door that repairs such a rule.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` whose status is about to
            change, not yet changed.  A transfer's shadow has no definition
            of its own and is never held here: its transfer is, at the
            transfer's own door.
        new_status_id: The ``ref.statuses.id`` it is moving to.

    Raises:
        ValidationError: When the move is a revert to Projected and the
            books drop the row's occurrence, or hold an orphan's own day, or
            its schedule cannot be walked.  Nothing is refused for a row no
            recurring definition names (a rule-less item's row, a link-less
            row: ledger row **PC-519**'s, bounded by no door), for an
            undated row (``occurs_on`` ``NULL``, which ruling **R-PC96**
            judges at the unarchive alone), for an owner with no pay
            periods, or for a row the books do not hold.
    """
    if not reverts_to_projected(row, new_status_id):
        return
    refusal = _revert_refusal(row)
    if refusal is not None:
        raise ValidationError(refusal)


def _revert_refusal(row) -> str | None:
    """Return why reverting *row* to Projected is refused, or ``None``.

    Args:
        row: See :func:`reject_revert_below_the_books`.

    Returns:
        The refusal's sentence, or ``None`` when nothing refuses it.
    """
    definition = row.template
    if definition is None or not definition.recurs:
        return None
    calendar = calendar_for(definition.user_id)
    try:
        resolved = resolved_with_books(
            recurrence_spec(definition.recurrence_rule), calendar,
            definition_books(definition, {}),
        )
        reading = books_reading(resolved, calendar)
    except (RecurrenceResolutionError, RecurrenceGenerationError):
        logger.warning(
            "Refusing to revert %s %d to Projected: its definition's stored "
            "recurrence rule cannot be walked.",
            type(row).__name__, row.id, exc_info=True,
        )
        return (
            f'"{row.name}" cannot be set back to projected while the '
            f'schedule of "{definition.name}" cannot be read: repair its '
            "schedule first."
        )
    if resolved is None:
        # An owner with no pay periods: no walk, so nothing is dropped and
        # nothing is an orphan.
        return None
    if row.occurs_on in reading.inside:
        compared = reading.inside[row.occurs_on]
    elif reading.is_orphan(row.occurs_on):
        # Ruling R-PC98: a row the walk names nowhere, judged by its own day.
        compared = own_books_day(row, calendar, is_envelope=resolved.is_envelope)
        if resolved.books_opened_on is None or books_hold(
            resolved.books_opened_on, compared,
        ):
            return None
    else:
        return None
    day = compared.isoformat()
    where = (
        f"in the paycheck ending {day}" if resolved.is_envelope
        else f"due {day}"
    )
    return (
        f'"{row.name}" is {where}, on or before '
        f"{books_named(definition, resolved.books_opened_on)} books, which "
        f"open {resolved.books_opened_on.isoformat()}, so it cannot be "
        "planned as unpaid."
    )


def definition_edit_refusal(
    template, ctx, restorable: UnarchiveScope | None,
) -> str | None:
    """Return why saving *template*'s edit would strand a row, or ``None``.

    Rulings **R-PC90** and **R-PC91** (developer, 2026-09-22): a recurring
    definition's edit is refused when the state it would SAVE leaves a
    still-projected row of that definition answering an occurrence the books
    drop -- whatever field changed.  An account moved onto one whose books
    open later, an envelope box unticked (its rows then compare on their due
    day, not their paycheck's last day), a due day cleared (the row's cash
    day moves back onto its scheduled day), or a row that already sat below
    its books: one check, on the saved state, so no field has to be
    remembered.  An ARCHIVED definition's edit counts the rows its unarchive
    would bring back too (ruling **R-PC93**) -- as it stood BEFORE the edit
    (*restorable*, :func:`restorable_before_the_edit`), since a row its books
    already dropped stays deleted when it is unarchived (ruling **R-PC95**).

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
        restorable: :func:`restorable_before_the_edit`'s answer, asked
            before the edit was applied; ``None`` for an active definition.

    Returns:
        The refusal's sentence, or ``None`` when the save strands nothing --
        including a definition with no rule, whose rows no walk names, and an
        owner with no pay periods, who has no rows.
    """
    resolved = resolved_rule_of(template, ctx)
    if resolved is None:
        return None
    row = first_row_below_the_books(
        ctx.calendar(), (DefinitionWalk(resolved, template, restorable),),
    )
    if row is None:
        return None
    return (
        f"This change cannot be saved: {row.described()}, and the books it "
        f"would move money in open {resolved.books_opened_on.isoformat()}.  "
        "An opening is the balance at the END of its day, so that unpaid item "
        f"would sit inside it and stop being planned.  {row.remedy()}, then "
        "make the change."
    )


__all__ = [
    "DefinitionWalk",
    "StrandedRow",
    "definition_edit_refusal",
    "first_row_an_opening_strands",
    "first_row_below_the_books",
    "reject_revert_below_the_books",
    "restorable_before_the_edit",
]
