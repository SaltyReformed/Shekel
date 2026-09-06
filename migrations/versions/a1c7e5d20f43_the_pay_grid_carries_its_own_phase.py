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
final drift of -8 days** and 174 of 301 at +6 under ``next``.  One
Thanksgiving -- the nominal 2030-11-28, payday 122 -- re-phases everything
after it.  Anchored on this column instead, the same simulation records **0 of
301** wrong.  *A first draft put "against 10 of 301 wrong with the convention
off" in the same sentence, which an adversarial review struck: with the
convention off nothing displaces, so record-anchored and phase-anchored are the
same grid and BOTH are 0 by construction.  The 10 is a different quantity --
nominal paydays landing on a closed day, the ~3% this column exists for -- and
it does not belong in a drift comparison.*  That is ledger
row **PC-497** fault 2, and it is why ruling **R-PC54**'s premise that "the
rhythm keeps its phase from the recorded paydays, whose one bounded gap is an
owner whose FIRST recorded payday was itself shifted" was measured FALSE: the
gap is neither one nor bounded once a writer records a displaced day.  This
column is what makes that premise true again.

**Why it is a FACT and not a derived value stored beside its source.**  Today
it is DERIVABLE from the recorded rows, which is what the backfill below does
-- and a value the database already determines is rule 14's defect.  It stops
being derivable the moment a convention displaces: a recorded payday is then
the day the BANK moved money, and the nominal grid is the day payroll INTENDED,
which has no representation in ``budget.pay_periods`` at all.  **R-PC47** says
outright that a recorded payday may fall off the cadence, so the recorded set
cannot define the grid and the grid cannot be read back off the rows.  Two
facts, one home each.

**The backfill takes ``MAX(start_date)`` and NOT ``MIN``, and an adversarial
review of this step is why.**  Both are days the grid passes through for an
owner whose whole payday set is ONE arithmetic progression.  They part for a
PIECEWISE owner -- one who has used *correct my cadence going forward*, which
``pay_period_write.record_paydays`` deliberately permits and ledger row
**N-492** records -- and there ``MIN`` names the grid of the FIRST era while
the owner is being paid on the LAST.  ``MAX`` is the day the most recent batch
was spaced from, so it describes the rhythm the extend door continues.  *The
first draft used ``MIN`` and called the backfill "exact"; what it was exact
about was the equality it had just written, which proves nothing.  The
predicate that matters is that the anchor describes the grid the owner's LAST
paycheck came from.*

**Why NULLABLE.**  The backfill answers for every owner who holds a payday.
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

**RE-PARENTED onto `d4e8b1c62f07`** (`salary:S3-b`'s terminal-year column) when merging `dev`,
which had branched a second head off the same `c9a4e17b53d8`.  The two touch different tables and
neither reads the other, so the order between them is arbitrary and this one takes the later slot.

**Downgrade is value-lossless WHILE no convention displaces.**  Dropping the
column restores the pre-step behaviour exactly: with every convention at
``none`` no recorded payday is displaced, so ``MAX(start_date)`` re-derives
what was dropped.  **That expires at ``C14-e-3``**: once a convention
displaces, ``MAX`` is a CASH day, so a downgrade loses the phase and
re-running this backfill afterwards recovers the recorded set's phase rather
than payroll's -- the drift this column exists to delete, re-armed.  Stated
here rather than in a release note, because this file is what a person reads
before running it.

Revision ID: a1c7e5d20f43
Revises: d4e8b1c62f07
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1c7e5d20f43"
down_revision = "d4e8b1c62f07"
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
    #
    # MAX and not MIN: for a PIECEWISE owner the two name different grids, and
    # the one the extend door continues is the LATEST.  The docstring carries
    # the argument.
    op.execute(
        """
        UPDATE budget.pay_schedule AS s
           SET nominal_anchor = (
               SELECT MAX(p.start_date)
                 FROM budget.pay_periods AS p
                WHERE p.user_id = s.user_id
           )
        """
    )


def downgrade():
    """Drop the column.  See this migration's docstring on what that loses."""
    op.drop_column("pay_schedule", "nominal_anchor", schema="budget")
