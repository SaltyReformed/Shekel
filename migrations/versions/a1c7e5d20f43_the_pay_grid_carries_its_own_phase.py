"""the pay grid carries its own phase

Adds ``budget.pay_schedule.nominal_anchor`` -- a day the owner's NOMINAL pay
grid passes through.  Plan step **pay_calendar:C14-e-2**, developer direction
**R-PC61**.

**What it is for.**  ``pay_period_admin.extend_pay_periods`` and the rolling
top-up behind it continue an owner's rhythm by stepping one cadence from the
LAST RECORDED payday.  That is exact while every recorded payday sits on the
arithmetic grid, which is true today because every schedule's convention is
``none``.  From plan step ``C14-e-3`` a projected payday is the nominal grid
day DISPLACED onto a business day, and the writer records the displaced day --
so the anchor becomes a CASH day and each batch re-phases the grid by that
displacement, permanently.

**Measured, on production's own rhythm** (cadence 14, opening payday
2026-03-26, the real federal holiday set, the ``C14-e-3`` writer simulated,
2026-09-05).  At a batch of ONE -- the rolling top-up's steady state, since
``pay_period_rolling`` appends exactly the deficit -- the recorded rhythm walks
away from payroll's: **178 of 301 recorded paydays wrong under ``prior`` with a
final drift of -8 days**, 174 of 301 and +6 under ``next``, against **10 of
301** wrong with the convention off.  One Thanksgiving -- the nominal
2030-11-28, payday 122 -- re-phases everything after it.  Anchored on this
column instead, the same simulation records **0 of 301** wrong.  That is ledger
row **PC-497** fault 2, and it is why ruling **R-PC54**'s premise that "the
rhythm keeps its phase from the recorded paydays, whose one bounded gap is an
owner whose FIRST recorded payday was itself shifted" was measured FALSE: the
gap is neither one nor bounded once a writer records a displaced day.  This
column is what makes that premise true again.

**Why it is a FACT and not a derived value stored beside its source.**  Today
it equals ``MIN(budget.pay_periods.start_date)`` for every owner, which is
exactly what the backfill below uses -- and a value equal to one the database
already holds is rule 14's defect.  It stops being equal the moment a
convention displaces: a recorded payday is then the day the BANK moved money,
and the nominal grid is the day payroll INTENDED, which has no representation
in ``budget.pay_periods`` at all.  **R-PC47** says outright that a recorded
payday may fall off the cadence, so the recorded set cannot define the grid and
the grid cannot be read back off the rows.  Two facts, one home each.

**Why NULLABLE.**  The backfill is exact for every owner who holds a payday.
It cannot answer for a ``budget.pay_schedule`` row holding ZERO paydays, which
is a reachable state -- ``pay_period_admin.reset_pay_periods`` passes through
it -- and there is nothing to invent there: an owner with no paydays has stated
no phase.  NULL means exactly that.  No consumer can reach one: the extend door
refuses an owner with no recorded paydays before it reads this column, and
``pay_period_write.record_paydays`` writes the column on every batch that
records a payday.

**Why no CHECK, and no server_default.**  Its bounds are the application
calendar's, which ``ck_pay_schedule_history_opens_range`` already states for the
sibling column -- but this one is not a date the owner types, it is written by
the ONE writer from the batch's own first payday, so a bound here would guard a
value no door can misstate.  A ``server_default`` would be a phase nobody chose,
and a wrong phase generates wrong paydays silently, which is the direction that
must fail loudly.

**Downgrade is value-lossless.**  Dropping the column restores the pre-step
behaviour exactly, because the value it holds is re-derivable from
``MIN(start_date)`` for every schedule this application can currently produce
-- every convention is ``none``, so no recorded payday is displaced.  That
statement expires at ``C14-e-3``: once a convention displaces, a downgrade
loses the phase, and re-running this backfill afterwards would recover the
recorded set's phase rather than payroll's.  Stated here rather than in a
release note, because this file is what a person reads before running it.

Revision ID: a1c7e5d20f43
Revises: c9a4e17b53d8
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1c7e5d20f43"
down_revision = "c9a4e17b53d8"
branch_labels = None
depends_on = None


def upgrade():
    """Add the column and backfill each owner's earliest recorded payday."""
    op.add_column(
        "pay_schedule",
        sa.Column("nominal_anchor", sa.Date(), nullable=True),
        schema="budget",
    )
    # The backfill is a CORRELATED subquery rather than a join-update so that
    # an owner with no pay periods is left NULL rather than dropped from the
    # statement's row set -- the two are the same here, and saying which one
    # was meant is the point.
    op.execute(
        """
        UPDATE budget.pay_schedule AS s
           SET nominal_anchor = (
               SELECT MIN(p.start_date)
                 FROM budget.pay_periods AS p
                WHERE p.user_id = s.user_id
           )
        """
    )


def downgrade():
    """Drop the column.  See this migration's docstring on what that loses."""
    op.drop_column("pay_schedule", "nominal_anchor", schema="budget")
