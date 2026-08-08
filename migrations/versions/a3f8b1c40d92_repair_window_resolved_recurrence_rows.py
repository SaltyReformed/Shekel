"""Delete the duplicate Monthly First rows and correct the one wrong paycheck

The data half of plan step R4b-1, in the same commit as the code that stops
producing it: both recurrence engines resolved each rule against the CALLER's
pay-period window rather than the OWNER's whole schedule, so an extend read
every rule as though the owner's pay history began at the newly created
periods.  See ``app.services.generation_schedule.GenerationSchedule``.

Two shapes of residue, both measured 2026-08-08 against a streamed clone of
``shekel-prod-db`` (61 pay periods, 46 recurrence rules, 1,000 transactions):

1. **Duplicate ``Monthly First`` rows.**  ``_first_of_month_anchor`` asks which
   month a payday falls in.  Given only the new batch, the batch's own first
   payday in an already-covered month qualified that month again, and the
   ``PERIOD_STARTING_ON_OR_AFTER`` placement put a second row in it.  Three
   spurious ``Phone Allowance`` rows on production -- 2028-03-23, 2028-06-15
   and 2028-06-29, $39.54 each, $118.62 -- each ``created_at`` matching a
   separate extend, and growing by roughly $39.54 on every other extend.

2. **One wrong stored paycheck.**  ``recurrence_engine._get_transaction_amount``
   forwarded the same window into ``paycheck_calculator.calculate_paycheck`` as
   its ``all_periods``, which reads it for third-paycheck detection among three
   other judgements.  The extend of 2026-07-16 could not see the other two June
   2028 paychecks, so it did not know 2028-06-29 was the THIRD one and applied
   the deductions a third paycheck skips: **$2,814.45 stored where the whole
   schedule gives $3,316.90**, a $502.45 gap.

   **That figure was never SHOWN, and the UPDATE below is worth making anyway.**
   The balance projection and the grid cell both recompute projected salary
   income at read time (``income_service.live_projected_net`` through
   ``cash_ledger.live_amount_overrides``), which answers $3,316.90 for this row
   on an unmigrated clone; a period-by-period balance diff over both accounts
   and all 61 periods moves by exactly the three deleted income rows above and
   by nothing else.  What the stale column reaches is the grid's inline amount
   editor, which pre-fills from ``Transaction.estimated_amount`` -- and saving
   that form sets ``is_override = True``, the very flag that excludes a row
   from the live recompute.  Correcting the cache now costs no displayed figure
   and removes a trap that one click would have sprung.

**Why the deletion is stated as the defect's SIGNATURE and not as row ids**
(developer ruling, 2026-08-08).  Row ids are stable on production and on the
dev clone, but the test database runs migrations against an EMPTY schema where
those same integers later belong to unrelated fixture rows.  The predicate
below is instead what a correct ``Monthly First`` rule can never produce: its
occurrences are all month firsts and ``period_starting_on_or_after`` always
lands on the earliest-STARTING period of a calendar month, so a row in any
other period of that month was placed by nothing.  On the production clone it
selects exactly the three rows above and no other of the 1,000; on an empty
database it selects none.

**Why only ``Monthly First``.**  It is the one pattern whose anchor derivation
reads the schedule's MONTHS (``_first_of_month_anchor`` scans
``calendar.periods``).  The calendar family anchors on a date bound, which a
narrower window can only move FORWARD -- dropping occurrences, never repeating
one -- and the pay-period family's phase is inert at ``interval_n = 1``, which
all 46 live rules carry.

**The one gap in that argument, named rather than left implicit** (adversarial
review, 2026-08-08): it reasons about DATE bounds and says nothing about
``max_occurrences``, which ``_occurrence._bounded`` applies as a COUNT from the
anchor.  A narrower window moves the anchor forward and therefore restarts the
count, so a bounded rule would emit a fresh full allowance into every extend --
the same defect family, which this predicate would not find.  It is moot here
and that was measured, not assumed: **0 of the 46 live rules carry a
``max_occurrences``** (no form authors one until plan step R7b).  The code fix
closes it; ``TestABoundedRuleDoesNotRestartItsCount`` is its gate.

An independent sweep of every live row against a
whole-schedule recompute confirmed it: apart from these three, every row the
corrected engine does not name is either a user OVERRIDE or a finalised
(immutable) row the user accepted.  Neither is this migration's business.

**What it deliberately does NOT do.**  The paycheck correction names the exact
wrong value, the exact period and the profile's own owner, so the only row it
can reach is a projected, non-overridden salary row of that owner sitting on
2028-06-29 at exactly 2814.45.  It does not recompute every projected salary
row (that would pin an Alembic revision to today's calculator and today's tax
configuration).  A
different environment carrying a different wrong paycheck is corrected by
regenerating its salary profile, which recomputes every projected row against
the owner's whole schedule.  Both statements report their row counts so a
silent no-op is distinguishable from a silent over-reach.

Only rows in a NON-immutable status are touched, and never an override or a
soft-deleted row -- the same partition
``_recurrence_common.partition_regeneration_rows`` applies.  Every posted row
is settled and every settled status is immutable, so no statement here can
reach a row with double-entry postings.

Review: solo developer, 2026-08-08 (plan step R4b-1, defects D22 and D25).
Destructive: it deletes three projected transactions and rewrites one
projected amount.

Revision ID: a3f8b1c40d92
Revises: d4a71f6e30bb
Create Date: 2026-08-08 08:30:00.000000
"""
import logging

from alembic import op


# Revision identifiers, used by Alembic.
revision = 'a3f8b1c40d92'
down_revision = 'd4a71f6e30bb'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: Rows a correct ``Monthly First`` rule cannot have produced: the definition
#: fires on the 1st of a month and ``period_starting_on_or_after`` resolves
#: that to the earliest-STARTING pay period of the month, so any OTHER period
#: of the same month holds a row nothing placed.  Restricted to mutable,
#: non-overridden, non-deleted rows, which is exactly the set the recurrence
#: engines themselves consider theirs to rewrite.
_MONTH_FIRST_PERIODS = """
    SELECT pp.id
      FROM budget.pay_periods pp
     WHERE pp.start_date = (
               SELECT min(p2.start_date)
                 FROM budget.pay_periods p2
                WHERE p2.user_id = pp.user_id
                  AND date_trunc('month', p2.start_date)
                    = date_trunc('month', pp.start_date)
           )
"""

#: The sibling that makes a row a DUPLICATE rather than merely misplaced.  An
#: adversarial review found the first draft's predicate said nothing about
#: duplication: it destroyed ANY mutable ``Monthly First`` row outside its
#: month's first period, including a SOLITARY one left behind by a pattern
#: change (a template edit defaults its effective date to today, so switching
#: a definition to ``Monthly First`` leaves the previous pattern's past rows in
#: place).  Requiring a live sibling in the same month's month-first period
#: states the actual defect: the month is ALREADY covered.  Measured on the
#: production clone, tightening it selects the identical three rows.
_HAS_MONTH_FIRST_SIBLING = """
    SELECT 1
      FROM {table} sib
      JOIN budget.pay_periods sp ON sp.id = sib.pay_period_id
     WHERE sib.{template_fk} = t.{template_fk}
       AND sib.scenario_id = t.scenario_id
       AND sib.id <> t.id
       AND sib.is_deleted = false
       AND date_trunc('month', sp.start_date)
         = date_trunc('month', pp.start_date)
       AND sib.pay_period_id IN ({month_first})
"""

_DELETE_DUPLICATE_TRANSACTIONS = f"""
    DELETE FROM budget.transactions t
     USING budget.transaction_templates tt,
           budget.recurrence_rules r,
           ref.recurrence_patterns rp,
           ref.statuses s,
           budget.pay_periods pp
     WHERE tt.id = t.template_id
       AND r.id = tt.recurrence_rule_id
       AND rp.id = r.pattern_id
       AND s.id = t.status_id
       AND pp.id = t.pay_period_id
       AND rp.name = 'Monthly First'
       AND s.is_immutable = false
       AND t.is_override = false
       AND t.is_deleted = false
       AND t.pay_period_id NOT IN ({_MONTH_FIRST_PERIODS})
       AND EXISTS ({_HAS_MONTH_FIRST_SIBLING.format(
           table="budget.transactions",
           template_fk="template_id",
           month_first=_MONTH_FIRST_PERIODS,
       )})
"""

#: The transfer engine shares ``resolve_generation_plan`` with the transaction
#: engine, so it carries the identical defect and the identical residue shape.
#: Production has no ``Monthly First`` transfer template, so this selected zero
#: rows there; it is stated anyway because a database that does have one has
#: the same duplicates and no other statement would reach them.  Deleting a
#: transfer disposes both of its shadow transactions through
#: ``transactions.transfer_id``'s ``ON DELETE CASCADE`` -- the same disposal
#: ``transfer_service.delete_transfer`` relies on -- so the shadow pair cannot
#: be left half-deleted.
_DELETE_DUPLICATE_TRANSFERS = f"""
    DELETE FROM budget.transfers t
     USING budget.transfer_templates tt,
           budget.recurrence_rules r,
           ref.recurrence_patterns rp,
           ref.statuses s,
           budget.pay_periods pp
     WHERE tt.id = t.transfer_template_id
       AND r.id = tt.recurrence_rule_id
       AND rp.id = r.pattern_id
       AND s.id = t.status_id
       AND pp.id = t.pay_period_id
       AND rp.name = 'Monthly First'
       AND s.is_immutable = false
       AND t.is_override = false
       AND t.is_deleted = false
       AND t.pay_period_id NOT IN ({_MONTH_FIRST_PERIODS})
       AND EXISTS ({_HAS_MONTH_FIRST_SIBLING.format(
           table="budget.transfers",
           template_fk="transfer_template_id",
           month_first=_MONTH_FIRST_PERIODS,
       )})
"""

#: The one understated paycheck, named by the wrong VALUE and the period it
#: sits in so the statement cannot reach a correct row.  3316.90 is what
#: ``paycheck_calculator.calculate_paycheck`` returns for this profile and
#: period when handed the owner's whole 61-period schedule; 2814.45 is what it
#: returned when handed the two periods that extend created.
_CORRECT_THIRD_PAYCHECK = """
    UPDATE budget.transactions t
       SET estimated_amount = 3316.90
      FROM budget.pay_periods pp,
           salary.salary_profiles sp,
           ref.statuses s
     WHERE pp.id = t.pay_period_id
       AND sp.template_id = t.template_id
       AND sp.user_id = pp.user_id
       AND sp.is_active = true
       AND s.id = t.status_id
       AND s.is_immutable = false
       AND t.is_override = false
       AND t.is_deleted = false
       AND pp.start_date = DATE '2028-06-29'
       AND t.estimated_amount = 2814.45
"""


def upgrade():
    """Remove the window-resolved duplicates and correct the wrong paycheck."""
    bind = op.get_bind()
    deleted_txns = bind.exec_driver_sql(_DELETE_DUPLICATE_TRANSACTIONS).rowcount
    deleted_xfers = bind.exec_driver_sql(_DELETE_DUPLICATE_TRANSFERS).rowcount
    corrected = bind.exec_driver_sql(_CORRECT_THIRD_PAYCHECK).rowcount
    # Reported rather than assumed: a migration that silently matched nothing
    # reads identically to one that silently matched everything.  Production
    # is expected to answer 3 / 0 / 1 and a fresh database 0 / 0 / 0.
    logger.info(
        "R4b-1 repair: deleted %d duplicate Monthly First transaction(s), "
        "%d transfer(s); corrected %d understated paycheck(s).",
        deleted_txns, deleted_xfers, corrected,
    )


def downgrade():
    """Refuse: reverting means re-introducing rows and a figure known wrong.

    Raises:
        NotImplementedError: Always.  The deleted rows were duplicates no
            occurrence named and the corrected amount was measurably wrong, so
            an automatic revert would restore a $118.62 over-budget and a
            $502.45 income understatement.  The literal SQL to undo it by hand
            is in the message; the deleted rows' own values are recoverable
            from ``system.audit_log``.
    """
    raise NotImplementedError(
        "Refusing to revert a3f8b1c40d92: it deleted duplicate Monthly First "
        "rows that no occurrence of their own rule named, and corrected one "
        "projected paycheck from 2814.45 to 3316.90 (the value a whole-"
        "schedule calculation gives).  Reverting re-introduces a $118.62 "
        "over-budget and a $502.45 income understatement.\n\n"
        "To undo by hand:\n"
        "  UPDATE budget.transactions t\n"
        "     SET estimated_amount = 2814.45\n"
        "    FROM budget.pay_periods pp\n"
        "   WHERE pp.id = t.pay_period_id\n"
        "     AND pp.start_date = DATE '2028-06-29'\n"
        "     AND t.estimated_amount = 3316.90;\n\n"
        "The deleted rows carry no derivation to re-run -- restore them from "
        "system.audit_log (action='DELETE', table_name='transactions'), or "
        "let the next schedule extend regenerate what the rule genuinely "
        "names."
    )
