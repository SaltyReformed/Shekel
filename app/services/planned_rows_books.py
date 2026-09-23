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
(ledger rows **REC-536** and **REC-535**, the recurrence arc's).

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

from sqlalchemy import and_, or_

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
    UnarchiveScope,
    inside_the_books,
    rows_of,
    unarchive_scope,
    unarchive_scope_on,
)
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    RecurrenceGenerationError,
    RecurrenceResolutionError,
    ResolvedRecurrence,
    recurrence_spec,
)
from app.services.recurring_definition import resolved_rule_of
from app.utils.balance_predicates import is_projected_clause


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
            ``Mark it paid, cancel it or move it later first`` for a live row;
            for a hidden one, ``Unarchive it and mark it paid, cancel it or
            move it later`` -- the one door that reaches a row the owner
            cannot see (ruling **R-PC93**).  R-PC93's words ended "or delete
            "Rent" for good", which the permanent delete refuses for any
            definition with payment history, a merchant rule or a row holding
            a movement; the developer dropped the clause (2026-09-23, the
            round-3 review's M-1), so the remedy is true for every definition.
        """
        if self.is_hidden:
            return "Unarchive it and mark it paid, cancel it or move it later"
        return "Mark it paid, cancel it or move it later first"


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
    :func:`~app.services.definition_unarchive.inside_the_books`', the one
    reading the unarchive leaves rows deleted by.

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
        compared = inside_the_books(walk.resolved, calendar)
        if not compared:
            continue
        table_order, model, template_fk = rows_of(walk.definition)
        candidates.extend(
            (
                compared[occurs_on], table_order, row_id, name,
                walk.resolved.is_envelope, is_deleted, walk.definition.name,
            )
            for row_id, name, occurs_on, is_deleted in (
                db.session.query(
                    model.id, model.name, model.occurs_on, model.is_deleted,
                )
                .filter(
                    *_planned_rows(model, template_fk, walk),
                    model.occurs_on.in_(list(compared)),
                )
                .all()
            )
        )
    if not candidates:
        return None
    (
        books_day, _table_order, _row_id, name, is_envelope, is_hidden,
        definition_name,
    ) = min(candidates)
    return StrandedRow(
        name=definition_name if is_hidden else name,
        books_day=books_day,
        is_envelope=is_envelope,
        is_hidden=is_hidden,
    )


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
    leave out every row the edit moves below the books, and the refusal
    could never fire for an archived definition.

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
        return unarchive_scope(template, None, ctx.calendar())


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
            .placements_below_the_books`).
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
    "restorable_before_the_edit",
]
