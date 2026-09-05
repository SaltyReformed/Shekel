"""a raise carries the last year it is believed

Adds ``salary.salary_raises.terminal_year`` -- the last year a raise is
believed to happen, ``NULL`` meaning indefinitely.  Plan step
**salary:S3-b**, ruling **R-SAL11** (developer, 2026-09-05): the horizon a
recurring raise decays over is a fact ON THE RAISE, not a global setting on
the owner.

**A recorded raise is a FACT; a raise marked recurring is partly a FORECAST,
and a forecast decays.**  ``auth.user_settings.merit_raise_horizon_years``
holds that decay today as one number for the whole owner, applied by
``/retirement`` alone and keyed by raise TYPE -- every raise except a
recurring COLA stops at ``the render year + N``.  That rule was measured
against the developer's own raises on 2026-09-05 and rejected on three
counts, which this column answers structurally rather than by a guard:

1. **It slides.**  ``start_year`` is ``as_of.year`` at all three call sites,
   so the cutoff moves forward every January.  On his data -- ``$91,675.00``
   base, 3% recurring COLA from 2026-07, 2.5% recurring merit from 2027-01,
   horizon 5 -- the 2040 salary reads ``$161,595.26`` from a 2026 render,
   ``$182,830.20`` from a 2031 render and ``$201,810.34`` from a 2036 one.
   That last figure is *exactly* what believing the merit raise forever
   gives.  The rule is not a claim that merit raises stop; it is a discount
   on the far tail that evaporates as the tail arrives.  A stored year does
   not move when the clock does.
2. **It is a set defined by SUBTRACTION.**  ``pension_calculator
   ._terminate_after_horizon`` reads "``None`` if recurring and cola else the
   cutoff", so a NON-RECURRING COLA is terminated by a rule whose name
   mentions only merit and custom.  *That function's own docstring claims a
   second member -- a raise whose ``raise_type_id`` is ``None`` -- and an
   adversarial review of this step measured it impossible: the column is
   ``nullable=False``.  The claim is corrected there in this commit, and the
   correction is the argument rather than a footnote to it: a set spelled
   EVERYTHING-EXCEPT names members nobody censused.*  A per-raise year needs
   no type test at all.
3. **A raise dated past the cutoff silently never happens.**  Measured
   2026-09-05: a one-time ``$8,000`` promotion recorded for 2035 under a 2031
   cutoff leaves the 2040 salary at the ``$91,675.00`` base, untouched, and a
   recurring 4% one does the same.  ``ck_salary_raises_terminal_year_not_
   before_effective`` below makes the STORED form of that state
   unrepresentable.  **It does not fix the live defect**, and an adversarial
   review of this step corrected a draft of this sentence that read as though
   it did: ``_terminate_after_horizon`` assigns the cutoff IN MEMORY, onto a
   ``TerminatedRaise`` value no constraint can see, and will go on doing so
   until the cutover deletes it.

**NOTHING IS BACKFILLED, and the absence is the design rather than an
omission.**  ``salary_raises.apply_raises`` reads ``terminal_year`` through
``getattr(raise_obj, "terminal_year", None)`` -- shipped at plan step
**salary:S3-a** for the ``TerminatedRaise`` value the pension projector
builds -- so the moment this column exists on the ORM model, every
``SalaryRaise`` row the paycheck engine walks carries it.  A backfill here
would therefore not be additive: it would change what the engine answers,
inside a migration whose docstring claims to change nothing.  ``NULL`` is
"believed indefinitely", which is precisely what the engine does today with
a row that has no such attribute, so an all-``NULL`` column is the honest
identity.  The values arrive with their reader, at the cutover step that
deletes ``merit_raise_horizon_years`` and ``_terminate_after_horizon``.

**THE THIRD CHECK IS A DEVELOPER RULING OF 2026-09-05, made against both
adversarial reviews of this step**, which independently found the state and
both declined to add the constraint unilaterally (``CLAUDE.md`` rule 8).
``terminal_year IS NULL OR is_recurring``: an end year on a ONE-TIME raise is
storable under the other two and can never mean anything, because
``salary_raises._applications`` gates a one-time raise on ``eff_year <=
terminal_year`` and the ordering CHECK already guarantees that inequality.
The end year is a question about a FORECAST; a one-time raise is a recorded
fact that happens once, so asking it produces a stored value that provably
cannot move a figure.  The objection considered and overruled is that a rule
keyed on a raise's KIND is the shape argument 2 rejects -- overruled because
``is_recurring`` is a two-valued NOT NULL column with no unenumerated
members, where ``raise_type_id == cola_id`` is a ``ref`` lookup whose
membership is exactly what nobody censused.

The other two CHECKs are the table's existing conventions applied to the new
column: the ordering one states the rule this column exists to make
unbreakable, and the window one mirrors
``ck_salary_raises_valid_effective_year``'s own ``2000..2100`` bound so a
terminal year cannot leave the range its sibling is held to.  **The lower
bound is DERIVED rather than restated**, which is a dependency worth naming:
it follows from the ordering CHECK plus that sibling, so dropping the sibling
silently unbounds this column below.

The downgrade drops all three constraints, in reverse creation order, and
then the column.  It is value-lossy in the only way it can be -- a stated
end year has nowhere to live once the column is gone -- and it moves NO
projected figure in either direction, because an absent column and a
``NULL`` one are the same answer to ``getattr``.

Revision ID: c9a4e17b53d8
Revises: b3f7c2a91d4e
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c9a4e17b53d8'
down_revision = 'b3f7c2a91d4e'
branch_labels = None
depends_on = None


#: The three CHECKs, as ``(name, predicate)``, in creation order.  A tuple
#: rather than three inline calls so the suite can assert that
#: :func:`upgrade` creates exactly these and :func:`downgrade` drops exactly
#: these -- a constraint DEFINED here and never created is the silent form of
#: this failure, and the deploy is otherwise the first thing to find out.
_CHECKS = (
    (
        "ck_salary_raises_terminal_year_not_before_effective",
        "terminal_year IS NULL OR terminal_year >= effective_year",
    ),
    (
        "ck_salary_raises_valid_terminal_year",
        "terminal_year IS NULL OR terminal_year <= 2100",
    ),
    (
        "ck_salary_raises_terminal_year_only_on_a_recurring_raise",
        "terminal_year IS NULL OR is_recurring",
    ),
)


def upgrade():
    """Add the nullable end-year column and its three CHECKs."""
    op.add_column(
        "salary_raises",
        sa.Column("terminal_year", sa.Integer(), nullable=True),
        schema="salary",
    )
    for name, predicate in _CHECKS:
        op.create_check_constraint(
            name, "salary_raises", predicate, schema="salary",
        )


def downgrade():
    """Drop the three CHECKs and the end-year column."""
    for name, _ in reversed(_CHECKS):
        op.drop_constraint(
            name, "salary_raises", type_="check", schema="salary",
        )
    op.drop_column("salary_raises", "terminal_year", schema="salary")
