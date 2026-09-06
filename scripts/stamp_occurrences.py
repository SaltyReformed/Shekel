"""
Shekel Budget App -- Stamp ``occurs_on`` on rows generated before plan step R17

Fills ``budget.transactions.occurs_on`` and ``budget.transfers.occurs_on`` on
rows the recurrence engines wrote before those columns existed, by asking each
active template's rule which occurrences it names and matching its existing
rows against them.

**Why this is a script and not the migration's own backfill** (developer
ruling **recurrence:R-R46**, 2026-08-27).  The value is only computable by the
occurrence walk: a row's existing ``due_date`` is NOT its occurrence for 30 of
the 780 assignable rows on a production clone, because ``compute_due_date``
dates a day-less cadence from its PERIOD's start -- a ``Monthly First`` rule
such as ``Phone Allowance`` occurs on the 1st and is dated on the payday.  No
migration in this repository imports application code, and
``scripts/build_test_template.py`` rebuilds the test database by replaying the
whole Alembic chain from zero, so an import there would break the SUITE rather
than merely itself the day plan step R5 changes that walk.  Running here
instead keeps the migration frozen and self-contained while preserving what the
"backfills belong in migrations" rule was protecting: ``entrypoint.sh`` runs
this after migrations, so no operator has to remember it.

**It runs ONCE, and the SENTINEL is what makes that true** -- ``entrypoint.sh``
gates it on ``state/.occurrences-stamped`` exactly as it gates ``seed_user.py``.
The pre-flight count below is only a fast path and CANNOT reach zero on its own:
rows this pass deliberately leaves NULL stay NULL, and two live writers keep
minting more of them (see the provenance filter).  A first draft claimed the
count short-circuited "every run after the first"; it returned 8 on the second
boot of the author's own clone, which would have re-run the whole heuristic pass
against evolving financial data at every deploy forever.

**TWO deductions, and no inference.**  Each active template's rows are matched
against the occurrences its rule names, hardest evidence first:

  1. EXACT DUE DATE -- the row carries the due date that occurrence computes.
     This runs FIRST because it identifies a row where the period cannot: a
     move rewrites the paycheck and copies no date, and an ``is_override`` row
     may share a paycheck with that paycheck's own row.
  2. EXACT PERIOD -- the row sits in the occurrence's own pay period.

A row matched by neither is LEFT NULL and REPORTED.  NULL is a real state: it
means no occurrence the rule now names claims this row.

**A third rule was CUT after adversarial review, and the reason is the whole
safety argument of this file.**  It stamped the last row from the last
occurrence when exactly one of each remained, called a deduction on the ground
that "no other pairing exists".  That is invalid: it is a deduction only if
every row answers some occurrence, which this file's own NULL case denies.  Two
INDEPENDENT anomalies of opposite sign -- one extra row, one rowless occurrence
-- were paired.  Both are reachable through live doors: a carry-forward envelope
row is template-linked and answers nothing, and an occurrence goes rowless when
a template is archived across a period or a retired row is hard-deleted.  A
reviewer reproduced a `$12.34` envelope roll-forward being stamped as the car
payment's occurrence nine paychecks away.  Under the predicate leaf that reads
this column, such a row SUPPRESSES generation of the real bill: a payment
silently disappears from the budget, which is worse than the duplicate this
whole step exists to stop, because nothing shows it.  Two rows on the author's
clone matched only by that rule; they are reported here and stamped by hand
after the developer confirms them, which is what "no guessing" costs.

**Measured on a production clone, 2026-08-27.**  Of 788 template-linked rows it
considers 736 -- the other 52 sit on archived templates, which no generate path
walks either -- and stamps 726 of those: 725 by due date, 1 by period.  The 10
left NULL are four settled rows predating their rule's edited start, one whose
rule was deleted, one duplicate this defect created, two on templates with no
cadence at all, and the two the cut rule used to guess.

**Only rows the ENGINE could have written are considered.**  Both engines
always date a generated row (``compute_due_date`` never returns ``None``), so
``due_date IS NULL`` on a template-linked row is proof it came from somewhere
else -- ``carry_forward_service._execute`` rolls an unspent envelope forward as
an ``is_override`` row and writes exactly that.  Filtering in the row query is
what stops all three rules inheriting the problem separately.

**Soft-deleted rows ARE considered, deliberately.**  ``OccurrenceClaims`` counts
a row's claim whatever state it is in, so a soft-deleted row DOES answer its
occurrence.  Excluding them here would let the predicate resurrect a bill the
owner removed.

**It only ever WRITES ``occurs_on``.**  No row is created, deleted, re-dated or
moved between pay periods -- deliberately, because a duplicate this defect
already created is a row the statement-matching surfaces offer, and changing
which rows exist is not this script's business.

**One owner's bad data may not stop the app booting.**  Every raiser reachable
from the walk is a ``ShekelError`` -- ``PayCalendarError`` for an owner with no
resolvable cadence, ``RecurrenceGenerationError`` for a rule whose shift the
walk does not implement until plan step R8, ``RecurrenceResolutionError`` for
the ``WEEK`` unit, ``RecurrenceWindowError`` for a window -- and
``entrypoint.sh`` runs under ``set -eEuo pipefail``, so an escape would kill the
container AFTER its migration had already applied, leaving a digest-pinned
rollback pointing at an older schema.  Each owner and each template is isolated:
the failure is logged and the pass continues, because "this rule cannot be
walked" has the same correct answer as an unmatched row -- leave it NULL.

Usage:
    python scripts/stamp_occurrences.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- the sys.path bootstrap above must run before
# these imports so ``app`` resolves when invoked as
# ``python scripts/stamp_occurrences.py`` (sys.path[0] is scripts/, not the
# repo root, in that mode).
# pylint: disable=wrong-import-position
from app import create_app
from app.exceptions import ShekelError
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.user import User
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence_engine import (
    compute_due_date,
    resolve_generation_plan,
)
from app.utils.log_events import BUSINESS, log_event

logger = logging.getLogger("stamp_occurrences")


def _rows_awaiting_stamp() -> int:
    """Return how many engine-written rows still have no ``occurs_on``.

    A FAST PATH, not a completion test.  It applies the provenance filter so a
    carry-forward row never keeps it non-zero, but it cannot apply the pass's
    scenario, window or active-template filters cheaply, so a permanently
    unmatchable row still counts.  ``entrypoint.sh``'s sentinel is what makes
    this a one-time pass; see the module docstring.

    Returns:
        The number of rows across both tables this pass could consider.
    """
    txns = (
        db.session.query(Transaction)
        .filter(Transaction.template_id.isnot(None),
                Transaction.occurs_on.is_(None),
                Transaction.due_date.isnot(None))
        .count()
    )
    xfers = (
        db.session.query(Transfer)
        .filter(Transfer.transfer_template_id.isnot(None),
                Transfer.occurs_on.is_(None),
                Transfer.due_date.isnot(None))
        .count()
    )
    return txns + xfers


def _match_by_due_date(free_rows, free_occ, rule) -> int:
    """Stamp rows carrying the due date their occurrence computes.

    The FIRST and strongest rule.  It identifies a row the owner MOVED to
    another paycheck: the move rewrites ``pay_period_id`` and copies no date, so
    the row still carries the due date ``compute_due_date`` produced for the
    period it was written in.  That value is a pure function of
    ``(rule, period)``, so recomputing it for the same occurrence reproduces it
    exactly.

    Args:
        free_rows: Unmatched rows, mutated in place.
        free_occ: Unmatched placements, mutated in place.
        rule: The template's RecurrenceRule, to date each occurrence from.

    Returns:
        How many rows this rule stamped.
    """
    stamped = 0
    for placement in list(free_occ):
        key = compute_due_date(rule, placement.period)
        matches = [row for row in free_rows if row.due_date == key]
        # Two rows CAN carry one computed due date -- a moved row and the
        # duplicate this defect wrote into the paycheck it left are exactly
        # that pair, measured on a production clone as ``Christmas`` ids
        # 2211/2770.  Prefer the row sitting in the occurrence's OWN period, so
        # the tie is broken by evidence rather than by id order; either row
        # claims the occurrence, but the one in its own paycheck is the one a
        # reader would name.
        hit = next(
            (row for row in matches
             if row.pay_period_id == placement.period.period_id),
            matches[0] if matches else None,
        )
        if hit is not None:
            hit.occurs_on = placement.occurrence
            free_rows.remove(hit)
            free_occ.remove(placement)
            stamped += 1
    return stamped


def _match_by_period(free_rows, free_occ) -> int:
    """Stamp rows sitting in their own occurrence's pay period.

    The SECOND rule, and weaker than the due date: a paycheck can hold more
    than one row of a template, because an ``is_override`` row sits outside the
    partial unique index and that is exactly what a move produces.  Running it
    first assigned two of ``Kayla's Spending Money``'s rows each other's
    occurrences on a production clone.

    Args:
        free_rows: Unmatched rows, mutated in place.
        free_occ: Unmatched placements, mutated in place.

    Returns:
        How many rows this rule stamped.
    """
    # **Both sides are ORDERED, so the pairing is not the query's accident.**
    # A paycheck can hold two free rows of one template -- a canonical and the
    # ``is_override`` sibling a move produces, measured as one such group on
    # the developer's data -- and a cadence can name that paycheck twice.  With
    # two of each, the row this rule reached was whichever the unordered SELECT
    # returned first, so two rows could take each other's occurrences.  Neither
    # is left unanswered either way, so nothing is suppressed; what a swap
    # corrupts is WHICH installment each row says it is, which for a loan
    # payment is a posting input rather than a label.  Ascending due date
    # against ascending occurrence is the only pairing that is not arbitrary
    # once the due-date rule above has taken every exact match.
    stamped = 0
    free_rows.sort(key=lambda row: (row.due_date, row.id))
    for placement in list(free_occ):
        hit = next((row for row in free_rows
                    if row.pay_period_id == placement.period.period_id), None)
        if hit is not None:
            hit.occurs_on = placement.occurrence
            free_rows.remove(hit)
            free_occ.remove(placement)
            stamped += 1
    return stamped


def _placements_to_offer(plan, rows):
    """Return the occurrences still free, one per period, ascending.

    Two collapses, and both make this pass agree with what the ENGINE stores.

    ``taken`` excludes every occurrence a stamped row already holds, which is
    what makes a re-run idempotent: without it a leftover row STEALS a claimed
    occurrence on the second pass, measured on a production clone as a cancelled
    ``Christmas`` duplicate taking its live sibling's date.

    **Every unclaimed placement is offered, including a second one in a paycheck
    the cadence names twice.**  This collapsed a repeated period to its LAST
    placement until plan step **R17**'s second leaf, justified by
    ``_recurrence_common.occurrence_by_period`` -- the maintain pass's create
    arm took a ``{period: occurrence}`` map, so a surplus placement had nothing
    to claim it.  That function is gone: the create arm now consumes every
    placement, and the collapse had become a defect in two directions.  It
    discarded the EARLIEST placement before :func:`_match_by_due_date` could
    see it, so a row carrying that occurrence's exact due date -- the hardest
    evidence this script has -- was matched by PERIOD to the wrong one instead;
    and the occurrence it dropped was left with no row, while the row it
    mis-stamped went on claiming a date the cadence names once.  A row left
    NULL claims its whole PAYCHECK under ``OccurrenceClaims``, so the
    suppression is of both installments, silently.

    :func:`_match_by_period` needs no collapse to be safe with a repeated
    period: it consumes one free row per placement, so two placements sharing a
    paycheck take two different rows, and a placement with no row left is
    simply not stamped -- which is the correct answer, and leaves the engine to
    generate it.

    Args:
        plan: The template's resolved ``GenerationPlan``.
        rows: Every row of this template in scope, stamped or not.

    Returns:
        The placements to offer, ascending by occurrence.
    """
    taken = {row.occurs_on for row in rows if row.occurs_on is not None}
    return sorted(
        (placement for placement in plan.placements
         if placement.occurrence not in taken),
        key=lambda placement: placement.occurrence,
    )


def _stamp_template(template, schedule, scenario_id, model, fk_col) -> tuple:
    """Assign each of *template*'s unstamped rows the occurrence it answers.

    Applies the two deductions this module's docstring states, in order, and
    leaves a row NULL when neither claims it.

    Args:
        template: The (Transaction|Transfer)Template whose rows to stamp.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id: The scenario whose rows are being stamped.
        model: ``Transaction`` or ``Transfer``.
        fk_col: That model's template foreign-key column.

    Returns:
        ``(by_due, by_period, left_null)`` counts for this template.
    """
    # EVERY row of this template in scope, stamped or not: a previous run's
    # answers are what ``_placements_to_offer`` needs to stay idempotent.  The
    # ``due_date`` clause is the PROVENANCE filter -- see the module docstring.
    rows = (
        db.session.query(model)
        .filter(fk_col == template.id,
                model.scenario_id == scenario_id,
                model.due_date.isnot(None),
                model.pay_period_id.in_(schedule.write_period_ids))
        # Ordered so the pass is DETERMINISTIC: an unordered SELECT let
        # Postgres decide which row a rule saw first, observed shifting a row
        # between two rules across runs on identical data.
        .order_by(model.id)
        .all()
    )
    free_rows = [row for row in rows if row.occurs_on is None]
    if template.recurrence_rule is None:
        # No cadence at all: nothing here names these rows.  Reported like any
        # other unmatched row, so the printed lines and the summary's NULL
        # count reconcile -- a count larger than the lines beneath it reads as
        # rows the pass silently swallowed.
        for row in free_rows:
            print(f"    left NULL: {model.__name__} id={row.id} "
                  f"(period {row.pay_period_id}, due {row.due_date}) -- "
                  f"template {template.id} has no recurrence rule")
        return (0, 0, len(free_rows))

    plan = resolve_generation_plan(
        template, schedule, scenario_id, None,
        block_message="Blocked cross-user occurrence stamp",
    )
    if plan is None:
        # The rule EXISTS, so the only other ``None`` is the cross-user
        # ownership refusal -- a route-layer hole or an IDOR probe, already
        # logged at WARNING by ``check_scenario_ownership``.  Reported apart
        # from "no rule" so a security refusal is never indistinguishable from
        # ordinary data in the summary line.
        print(f"    OWNERSHIP REFUSED: template {template.id} "
              f"({len(free_rows)} row(s) left NULL)")
        return (0, 0, len(free_rows))

    free_occ = _placements_to_offer(plan, rows)
    # Called as separate statements, in order.  The order is load-bearing --
    # due date identifies a row where the period cannot -- and expressing it as
    # the evaluation order of a tuple would let a reformat silently reintroduce
    # the swap.
    by_due = _match_by_due_date(free_rows, free_occ, plan.rule)
    by_period = _match_by_period(free_rows, free_occ)

    for row in free_rows:
        print(f"    left NULL: {model.__name__} id={row.id} "
              f"(period {row.pay_period_id}, due {row.due_date}) -- "
              f"no occurrence this rule names claims it")
    return (by_due, by_period, len(free_rows))


def _stamp_user(user, totals, skipped) -> None:
    """Stamp every unstamped row of *user*'s active templates.

    Args:
        user: The owner whose rows to stamp.
        totals: ``[by_due, by_period, left_null]``, accumulated in place.
        skipped: ``[templates, owners]`` skip counts, accumulated in place.
    """
    ctx = BalanceContext.build(user.id)
    if ctx.scenario is None:
        # No baseline scenario: this owner has no generated rows to stamp, and
        # ``ctx.scenario_id`` would raise.  The answer ``period_population``
        # gives for the same state.
        return
    schedule = GenerationSchedule.for_pass(ctx)
    pairs = (
        (TransactionTemplate, Transaction, Transaction.template_id),
        (TransferTemplate, Transfer, Transfer.transfer_template_id),
    )
    for template_model, row_model, fk_col in pairs:
        # ``is_active=True`` matches every generate path
        # (``period_population.populate_periods_from_active_templates``).  An
        # archived template is never generated into, so its rows cannot be
        # duplicated and leaving them NULL is correct -- and walking its rule
        # would drive the engine over cadences no generate path exercises.
        templates = (
            db.session.query(template_model)
            .filter_by(user_id=user.id, is_active=True)
            .order_by(template_model.id).all()
        )
        for template in templates:
            try:
                counts = _stamp_template(
                    template, schedule, ctx.scenario_id, row_model, fk_col,
                )
            except ShekelError as exc:
                # A rule this application cannot walk.  Leaving its rows NULL
                # is the same answer an unmatched row already gets, so the pass
                # continues rather than taking the container down with it.
                skipped[0] += 1
                print(f"    SKIPPED template {template.id}: {exc}")
                log_event(
                    logger, logging.WARNING, "occurs_on.template_skipped",
                    BUSINESS, "Could not walk a template's cadence to stamp it",
                    user_id=user.id, template_id=template.id,
                    reason=type(exc).__name__,
                )
                continue
            totals[:] = [a + b for a, b in zip(totals, counts)]


def stamp_occurrences() -> None:
    """Stamp every unstamped engine-written row, for every owner."""
    pending = _rows_awaiting_stamp()
    if not pending:
        print("occurs_on: nothing to stamp.")
        return
    print(f"occurs_on: {pending} row(s) to consider.")

    totals, skipped = [0, 0, 0], [0, 0]
    for user in db.session.query(User).order_by(User.id).all():
        try:
            _stamp_user(user, totals, skipped)
        except ShekelError as exc:
            # One owner's unresolvable calendar may not stop the app booting.
            skipped[1] += 1
            print(f"  SKIPPED owner {user.id}: {exc}")
            log_event(
                logger, logging.WARNING, "occurs_on.owner_skipped", BUSINESS,
                "Could not resolve an owner's calendar to stamp their rows",
                user_id=user.id, reason=type(exc).__name__,
            )

    db.session.commit()
    considered = totals[0] + totals[1] + totals[2]
    print(f"occurs_on: stamped {totals[0] + totals[1]} row(s) "
          f"({totals[0]} by due date, {totals[1]} by period); "
          f"{totals[2]} left NULL; "
          f"{skipped[0]} template(s) and {skipped[1]} owner(s) skipped.")
    # The pre-flight counts every engine-written NULL row in the database; this
    # pass considers only an ACTIVE template's rows in the owner's baseline
    # scenario.  Naming the difference keeps the remainder from reading as rows
    # the pass looked at and declined, which is a different fact.
    print(f"occurs_on: {max(0, pending - considered)} row(s) were outside this "
          f"pass's scope (archived template, other scenario, or no baseline).")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        stamp_occurrences()
