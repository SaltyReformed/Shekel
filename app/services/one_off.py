"""
Shekel Budget App -- A ONE-OFF is a rule-less definition plus its placed rows

**The ONE producer of a one-off** (plan step ``balance:X-bi-7b``, ruling
**R-BAL20**: every plan item has exactly one definition).  A one-off budget
line used to be a bare ``budget.transactions`` row carrying its own name,
category, flags and figure, with ``template_id IS NULL`` as how the code told
it from a generated row.  It is a DEFINITION now -- a
``budget.transaction_templates`` row with no recurrence rule -- holding the
name, category, flags and price, plus the row(s) it PLACES: one for a grid
one-off, one per paycheck for a bank-born envelope once ``X-f6c`` names the
definition from a merchant rule (**R-BAL24**).  The row holds what is the
row's: its paycheck, its status, its due date.

Four writers made a link-less plan item.  THREE call :func:`place_one_off`
since the family's first leaf -- the grid's two create doors
(``routes/transactions/create.py``) and the row a bank line's money requires
(``statement_match._uncategorized.mint_uncategorized``) -- and the fourth, the
envelope a NEW-ENVELOPE merchant answer creates
(``statement_match._container._create_envelope``), still writes a link-less
row until the third leaf (``X-f6c``'s shape) puts it here.  They differ only
in what they put in :class:`OneOffToPlace`.

**What the producer states, and the ruling each clause is.**

* **The price lives on the definition, and a one-off's series holds ONE
  version dated on the row's due date** (**R-BAL21**).  The row states no
  figure: it is priced through amount rule 3
  (``cash_ledger._amount_source``) by its definition's series as of its own
  due date, exactly as ``X-au-f`` ruled for the one-time transfer.  With one
  version the series is flat on every date, so the row's date can move
  freely and prices nothing differently.
* **A one-off is due on its placed paycheck's START unless the owner states
  a date** (**R-BAL22**) -- the rule the transfer twin states for its own row
  (``routes/transfers/_instances._materialize_one_time_transfer``; ``X-ci``
  is where the two spellings meet), balance-neutral by construction because
  ``DerivedPeriod.attribution_day`` already budgets an undated item to its
  paycheck's start.  (The spec also named
  ``carry_forward_service._leftover_due_date``; its rule-less arm was
  deleted at 7a and it reads the cadence now.)
* **The row records its own due date as the occurrence it answers**
  (``occurs_on = due_date``, **R-BAL25**), so no row answers no occurrence
  and ``recurrence:R19-b``'s NOT NULL binds on it.
* **The row carries no flag.**  ``tracks_purchases`` and
  ``visible_to_companion`` read the definition for every template-linked
  row, so the sealed cells on the row are never written here; the cutover
  leaf deletes them.

:func:`place_row_of` is the placing half alone -- a row of an EXISTING
rule-less definition in a paycheck -- split out because a bank-born envelope
definition places one row per paycheck it is filed into (**R-BAL24**), and
because it states the five definition-derived columns through the same
:func:`~app.services.recurrence_engine.unruled_row_fields` the maintain twin
re-declares them with, so a row as born and a row as re-declared cannot
differ.

**The definition carries no scenario.**  Definitions never have; a one-off
created in a non-baseline scenario mints one that carries none and places its
row in the submitted scenario, noted rather than forked while one scenario
exists and the selector is a stub (**R-BAL26**).

Boundary discipline (``CLAUDE.md`` Architecture): plain data in, ORM rows
out, no Flask import, no clock read.  It MUTATES and FLUSHES -- the row's
foreign keys need the definition's id -- and does NOT commit; the caller
owns the unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import template_amount_service
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence_engine import unruled_row_fields


@dataclass(frozen=True)
class OneOffToPlace:  # pylint: disable=too-many-instance-attributes
    """What a one-off's DEFINITION says, before it exists.

    Pylint: ``too-many-instance-attributes`` (8/7) -- the eight ARE one
    fact, and there is one per column of ``budget.transaction_templates`` an
    owner STATES about a plan item (``is_active`` and ``sort_order`` take
    their defaults; a one-off is born live and unordered): what the owner (or
    the bank) said this plan item IS.  What is NOT here is where its row goes -- the paycheck,
    the scenario, the due date -- which :func:`place_one_off` takes as the
    same placing arguments :func:`place_row_of` does, because a one-off is a
    definition PLUS a placed row (**R-BAL20**) and the object mirrors that
    split rather than hiding it.  Decomposing the eight further would split
    one table's row across two values.

    **A parameter object because the producer is PUBLIC and reached from
    more than one module** -- two today, a third at the family's third leaf
    (this project's remedy for a public function over the argument bound): a
    signature that cannot be read at a call site is read wrong at each of
    them.

    Attributes:
        user_id: The OWNER, which is a column on both rows (plan step
            ``pay_calendar:C13-a``).  The caller has proved the account, the
            category, the scenario and the paycheck are all this user's; the
            two composite keys refuse the row otherwise.
        account_id: The account the money moves through.
        transaction_type_id: Expense or income.
        name: What the plan item is called.  ``budget.transaction_templates
            .name`` is ``String(200)`` and NOT NULL, as the row's is; the
            bank door cuts its merchant string to fit before building this.
        amount: What it is planned to cost, which becomes the definition's
            ONE version dated on the row's due date (**R-BAL21**).  ``0.00``
            is legal -- a bank-born envelope budgets nothing, because
            nothing budgeted it.
        category_id: What the money IS, or ``None`` where nothing can say so
            -- the bank door's row for money it could not categorise
            (**R-FN**), which is why ``transaction_templates.category_id``
            is nullable since migration ``9c1e4b7a2d3f``.  Every grid door
            requires one at its schema.
        is_envelope: Whether the definition's rows take purchase entries --
            the DEFINITION's setting, read for every row by
            ``Transaction.tracks_purchases``.
        companion_visible: Whether a companion of the owner may see its rows
            -- likewise the definition's.
    """

    user_id: int
    account_id: int
    transaction_type_id: int
    name: str
    amount: Decimal
    category_id: "int | None"
    is_envelope: bool = False
    companion_visible: bool = False


def due_date_for(stated: "date | None", period: DerivedPeriod) -> date:
    """Return the day a one-off placed in *period* is due (**R-BAL22**).

    The one statement of the rule for a row this module places: the owner's
    stated day, else the paycheck's START.  Balance-neutral by construction
    -- ``DerivedPeriod.attribution_day`` already budgets an undated item to
    its paycheck's start -- and the day amount rule 3 prices the row on.

    Args:
        stated: The day the owner (or the bank) said it falls, or ``None``.
        period: The paycheck the row is placed in.

    Returns:
        The due date.
    """
    return stated if stated is not None else period.start_date


def place_row_of(
    definition: TransactionTemplate,
    period: DerivedPeriod,
    *,
    scenario_id: int,
    due_date: "date | None" = None,
) -> Transaction:
    """Place a row of the rule-less *definition* in *period*.

    The placing half of :func:`place_one_off`, and the whole act for a
    definition that already exists -- a bank-born envelope's row in a later
    paycheck (**R-BAL24**).  The five definition-derived columns come from
    :func:`~app.services.recurrence_engine.unruled_row_fields`, the ONE
    statement the maintain twin re-declares them with; what is stated here
    is only what the row IS: whose, which definition's, which paycheck's,
    which occurrence (its own due date, **R-BAL25**), which scenario, born
    Projected and the owner's to move without a flag
    (``is_override = False``: a rule-less definition runs no pass to keep off
    the row, **R-BAL28**).

    Does NOT commit.  FLUSHES, so the caller reads a row with an id -- the
    settle verb the bank door applies next needs one.

    Args:
        definition: The rule-less
            :class:`~app.models.transaction_template.TransactionTemplate`.
            Flushed, so ``definition.id`` is set.
        period: The paycheck to place the row in, as the owner's calendar
            derived it.
        scenario_id: The scenario the row is placed in.
        due_date: The owner's stated day, or ``None`` for the paycheck's
            start (:func:`due_date_for`).

    Returns:
        The placed, flushed :class:`~app.models.transaction.Transaction`.
    """
    due = due_date_for(due_date, period)
    row = Transaction(
        **unruled_row_fields(definition, due)._asdict(),
        user_id=definition.user_id,
        template_id=definition.id,
        pay_period_id=period.period_id,
        occurs_on=due,
        scenario_id=scenario_id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        is_override=False,
        is_deleted=False,
    )
    db.session.add(row)
    db.session.flush()
    return row


def place_one_off(
    spec: OneOffToPlace,
    period: DerivedPeriod,
    *,
    scenario_id: int,
    due_date: "date | None" = None,
) -> Transaction:
    """Mint a one-off's definition and place its row in *period*.

    Three acts, in this order: the definition is written and flushed (the
    row's key and the series' key both need its id); its series is opened
    with ONE version dated on the row's due date through the series' one
    write door (``template_amount_service.set_amount``, **R-BAL21**) --
    which also sets the scalar the constructor above had to carry because
    the column is NOT NULL; and the row is placed by :func:`place_row_of`,
    with the same placing arguments this function takes.

    Does NOT commit -- the route or the bank door owns the unit of work, so
    a refusal downstream (an ``IntegrityError`` on a foreign key, a settle
    day that has not happened) takes the definition back with the row.

    Args:
        spec: What the one-off's definition says (:class:`OneOffToPlace`).
        period: The paycheck to place its row in, as the owner's calendar
            derived it -- the :class:`~app.services.pay_calendar.DerivedPeriod`
            the caller already resolved, so the paycheck's start is read off
            one derivation rather than re-resolved from an id.
        scenario_id: The scenario the ROW is placed in -- the submitted one
            at the grid, the baseline for a bank-born row.  The definition
            carries none (**R-BAL26**).
        due_date: The day the owner said it falls, or ``None`` for the
            paycheck's start (**R-BAL22**).

    Returns:
        The placed, flushed :class:`~app.models.transaction.Transaction`;
        its ``template`` is the minted definition.
    """
    definition = TransactionTemplate(
        user_id=spec.user_id,
        account_id=spec.account_id,
        category_id=spec.category_id,
        transaction_type_id=spec.transaction_type_id,
        name=spec.name,
        default_amount=spec.amount,
        is_envelope=spec.is_envelope,
        companion_visible=spec.companion_visible,
    )
    db.session.add(definition)
    db.session.flush()
    due = due_date_for(due_date, period)
    template_amount_service.set_amount(definition, spec.amount, effective_on=due)
    return place_row_of(definition, period, scenario_id=scenario_id, due_date=due)


__all__ = [
    "OneOffToPlace",
    "due_date_for",
    "place_one_off",
    "place_row_of",
]
