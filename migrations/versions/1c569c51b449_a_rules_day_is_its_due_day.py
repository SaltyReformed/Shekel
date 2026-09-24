"""a rule's own day is the day its rows are due

Revision ID: 1c569c51b449
Revises: 2eabfa596ee0
Create Date: 2026-09-23 15:30:00.000000

Plan step **recurrence:R5-a**, ruling **R-R96** (developer, 2026-09-23).
Drops ``budget.recurrence_rules.due_day_of_month`` and its CHECK
``ck_recurrence_rules_due_dom``.

Review: Josh, 2026-09-23 -- APPROVED as built (AskUserQuestion, "Approve as
built"): refuse over a stated due day, drop the column and its CHECK, restore
both empty on downgrade.  The drop itself is his ruling recurrence:R-R96 (a
recurring due day that differs from the payment day lives on the loan's
terms).  Rejected: dropping without the refusal.

**What it was.**  A second day on the rule, "the bill's real due day when it
differs from the day the cadence schedules it on", which
``recurrence.compute_due_date`` preferred over the rule's own day and rolled
into the NEXT month when it was the smaller of the two.  Only the transaction
template form offered it, so a loan payment -- the one place a due day truly
differs from the day money moves -- could never carry one, and a loan whose
contract day differs from its payment's day had that contract day recorded
nowhere (plan ledger row **D4**).

**Why it goes rather than moves.**  The developer ruled where such a day lives:
on the LOAN'S TERMS (``loan_params.payment_day``, plan step R6's build), not on
the payment's rule.  For every other bill the day the rule fires on IS the day
it is due, and plan step R5-a dates each generated row from its OCCURRENCE
(ruling **R-R94**), so a second day on the rule has no reader left.  Measured
on a production clone on 2026-09-23: **no rule carried one** (the count is in
the lane's handoff, not here, under ruling **balance:R-BAL132**).

**The refusal is the grade, not a formality.**  A database restored from an
older dump, or a dev database someone typed into, could carry a stated due
day; dropping it silently would lose a date the owner wrote.  So the upgrade
names every such rule and refuses, and the owner re-states the date where it
now belongs (the rule's own first occurrence, or the loan's terms) before
re-running.  Written as a named function, as ``d9f5c1a48b73``'s refusals are,
so a test can drive it without running DDL.

**The downgrade re-adds the column and its CHECK, empty.**  Nothing is lost by
that: the upgrade refused to run over a stated value, so every row the
downgrade meets had ``NULL`` there, and ``NULL`` is what it restores.

**Re-parented at merge, never at authoring** (coordinator, 2026-09-23): the
chain this lands on is ``2eabfa596ee0`` -> ``credit_card:CC-5-4a-4`` ->
``credit_card:CC-5-5d`` -> ``salary:S11-a`` -> this, and ``down_revision`` is
moved onto whichever of those merged last when this branch takes ``dev``.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1c569c51b449"
down_revision = "2eabfa596ee0"
branch_labels = None
depends_on = None


#: Every rule still stating a due day, with enough of it to find and re-state.
_STATED_DUE_DAYS_SQL = """
SELECT r.id,
       r.due_day_of_month,
       r.starts_on,
       r.transaction_template_id,
       r.transfer_template_id,
       r.paycheck_line_id
FROM budget.recurrence_rules r
WHERE r.due_day_of_month IS NOT NULL
ORDER BY r.id
"""


def refuse_stated_due_days(bind) -> None:
    """Raise when any rule still states a due day.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every rule that carries one, its day, its first
            occurrence and the definition that owns it.
    """
    offenders = bind.execute(sa.text(_STATED_DUE_DAYS_SQL)).all()
    if not offenders:
        return
    raise RuntimeError(
        "recurrence rule(s) still state a due_day_of_month: "
        + "; ".join(
            f"id={row.id} due_day_of_month={row.due_day_of_month} "
            f"starts_on={row.starts_on} "
            f"transaction_template_id={row.transaction_template_id} "
            f"transfer_template_id={row.transfer_template_id} "
            f"paycheck_line_id={row.paycheck_line_id}"
            for row in offenders
        )
        + ".  This revision drops the column (ruling R-R96: a rule's own day "
        "is the day its rows are due; a loan's contract day lives on its "
        "terms), so running over these would lose a date the owner stated.  "
        "Re-state each one where it now belongs -- the rule's first "
        "occurrence, or the loan's payment day -- clear the column, and "
        "re-run."
    )


def upgrade():
    """Refuse a stated due day, then drop the column and its CHECK."""
    refuse_stated_due_days(op.get_bind())
    op.drop_constraint(
        "ck_recurrence_rules_due_dom", "recurrence_rules",
        type_="check", schema="budget",
    )
    op.drop_column("recurrence_rules", "due_day_of_month", schema="budget")


def downgrade():
    """Re-add the column, nullable and empty, with its CHECK."""
    op.add_column(
        "recurrence_rules",
        sa.Column("due_day_of_month", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_due_dom", "recurrence_rules",
        "due_day_of_month IS NULL OR "
        "(due_day_of_month >= 1 AND due_day_of_month <= 31)",
        schema="budget",
    )
