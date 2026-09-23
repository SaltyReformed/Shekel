"""Debt goals: a zero target, and a card goal's recorded start

Revision ID: 764461215480
Revises: 2eabfa596ee0
Create Date: 2026-09-23 10:00:00.000000

Plan step ``credit_card:CC-5-5d`` (debt goals).  Two changes to
``budget.savings_goals``, one per ruling:

**Ruling R-CC72** -- "A debt goal may target $0.00 ('Van Loan paid off by Dec
2028'); a savings goal still needs a target above $0.  The database check
changes to allow $0 and the app refuses $0 on a savings goal."  The target
CHECK is re-cut from ``target_amount > 0`` (``ck_savings_goals_positive_target``)
to ``target_amount >= 0`` (``ck_savings_goals_nonnegative_target``), renamed
because the old name would state a rule the table no longer holds.  A NULL
target (an income-relative goal, resolved on read) passes both.  Whether a goal
is a debt goal is its account's CATEGORY, a fact two tables away that a CHECK
cannot read, and storing a copy on the goal would be a derivable column
(``CLAUDE.md`` rule 14), so the table holds the bound every goal shares and the
one goal door (``app/services/savings_goal_door.py``) refuses ``$0.00`` on a
savings goal.

**Ruling R-CC91 (refining R-CC71 and R-CC88)** -- "A card or other non-loan debt goal SAVES its start when
created ... Loans keep re-reading their start from the books (R-CC71)."  A new
nullable ``start_owed`` holds what a NON-LOAN debt's /savings tile showed it
owing when the goal was created.  It cannot be re-read: that tile values the
debt at the END of the pay period, so a start re-read later with today's books
takes in every payment or purchase recorded in the rest of that period (a
$300 payment made two days after the goal would never count).  A loan's tile
reads the day, which the books reproduce, so a loan goal stores nothing and
re-reads -- which is also why the column is not derivable where it is set.
NULL for a savings goal and for a loan goal; ``> 0`` when set, because the door
refuses a debt goal on a debt that owes nothing.

Review: Josh, 2026-09-23 -- APPROVED by rulings credit_card:R-CC72 ("The
database check changes to allow $0") and credit_card:R-CC91 ("Needs a saved
start amount (same database change as the $0 target)"): a drop-and-recreate of
one CHECK that only WIDENS what the table accepts, and one added nullable column.

**The upgrade REFUSES rather than guesses** a start it cannot know: an ACTIVE
goal already on a non-loan liability (a card, a custom liability, a loan type
with no terms) has no recorded start and none can be reconstructed; nor could
an active debt goal carrying an income-relative mode or a per-period
contribution ever be saved again.  The upgrade counts them before writing
anything and raises naming each one.  Every such goal predates this step's
door; production held none on 2026-09-23 (one goal, on the Money Market).

**The downgrade REFUSES rather than rewrites** a ``$0.00`` target: restoring
``> 0`` would fail on that row, and changing the target would change what the
goal means.  With none, it restores the old CHECK exactly and drops the column
(a recorded start is lost with it, which a re-upgrade cannot restore -- the
refusal above then names the goals it strands).
"""

from alembic import op
import sqlalchemy as sa

revision = "764461215480"
down_revision = "2eabfa596ee0"
branch_labels = None
depends_on = None

# An ACTIVE goal on a liability that this step's goal door would not admit
# (the door's loan test reads the terms row alone, so for a terms row left on
# a NON-amortizing type the two differ -- this SQL is the stricter):
# on a debt that is not a CONFIGURED loan (an amortizing type with its terms
# row -- the seam's configured-loan rule, spelled in SQL for a migration that
# cannot import the app), whose start cannot now be recorded (R-CC91) -- a loan
# type still without terms included (R-CC93) -- or on any debt with an
# income-relative mode or a per-period contribution (R-CC69's premise, R-CC90),
# which no save could ever clear afterwards.
_UNRECORDED_CARD_GOALS = sa.text(
    "SELECT g.id, g.user_id, g.account_id, g.name "
    "FROM budget.savings_goals g "
    "JOIN budget.accounts a ON a.id = g.account_id "
    "JOIN ref.account_types t ON t.id = a.account_type_id "
    "JOIN ref.account_type_categories c ON c.id = t.category_id "
    "JOIN ref.goal_modes m ON m.id = g.goal_mode_id "
    "WHERE g.is_active AND c.name = 'Liability' AND ("
    "NOT (t.has_amortization AND EXISTS ("
    "SELECT 1 FROM budget.loan_params lp WHERE lp.account_id = a.id)) "
    "OR m.name <> 'Fixed' OR g.contribution_per_period IS NOT NULL) "
    "ORDER BY g.id"
)


def upgrade():
    unrecorded = op.get_bind().execute(_UNRECORDED_CARD_GOALS).fetchall()
    if unrecorded:
        raise RuntimeError(
            f"Cannot add budget.savings_goals.start_owed: {len(unrecorded)} "
            "active goal(s) on a debt that the debt-goal rules would not admit "
            "-- on a card or other non-loan debt (its start must be recorded "
            "when the goal is created and cannot be reconstructed now), or "
            "carrying an income-relative mode or a per-period contribution "
            "(plan step credit_card:CC-5-5d).  Nothing was written.  Delete "
            "them, or take them to the developer -- "
            "(id, user_id, account_id, name): "
            f"{[tuple(row) for row in unrecorded]}."
        )
    op.drop_constraint(
        "ck_savings_goals_positive_target",
        "savings_goals",
        schema="budget",
        type_="check",
    )
    op.create_check_constraint(
        "ck_savings_goals_nonnegative_target",
        "savings_goals",
        "target_amount >= 0",
        schema="budget",
    )
    op.add_column(
        "savings_goals",
        sa.Column("start_owed", sa.Numeric(12, 2), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_savings_goals_positive_start_owed",
        "savings_goals",
        "start_owed IS NULL OR start_owed > 0",
        schema="budget",
    )


def downgrade():
    zero_targets = op.get_bind().execute(sa.text(
        "SELECT id, user_id, account_id, name FROM budget.savings_goals "
        "WHERE target_amount = 0 ORDER BY id"
    )).fetchall()
    if zero_targets:
        raise RuntimeError(
            "Cannot restore ck_savings_goals_positive_target (target_amount > 0): "
            f"{len(zero_targets)} goal(s) target $0.00, which only a debt goal "
            "may do (ruling credit_card:R-CC72).  Re-target or delete them "
            "first -- (id, user_id, account_id, name): "
            f"{[tuple(row) for row in zero_targets]}.  Diagnostic: SELECT id, "
            "user_id, account_id, name FROM budget.savings_goals WHERE "
            "target_amount = 0;"
        )
    op.drop_constraint(
        "ck_savings_goals_positive_start_owed",
        "savings_goals",
        schema="budget",
        type_="check",
    )
    op.drop_column("savings_goals", "start_owed", schema="budget")
    op.drop_constraint(
        "ck_savings_goals_nonnegative_target",
        "savings_goals",
        schema="budget",
        type_="check",
    )
    op.create_check_constraint(
        "ck_savings_goals_positive_target",
        "savings_goals",
        "target_amount > 0",
        schema="budget",
    )
