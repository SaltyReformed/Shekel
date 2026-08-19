"""drop salary_profiles.pay_periods_per_year; the count derives from the cadence (R-F16)

Plan step **R-F16** closes finding **F-16**: "how often am I paid" had TWO
stored answers and nothing tied them together.

``salary.salary_profiles.pay_periods_per_year`` was a 12 / 24 / 26 / 52
dropdown and was the DIVISOR the paycheck engine turned an annual salary into
one paycheck with.  ``budget.pay_schedule.cadence_days`` is the same fact --
the rhythm the owner's paydays arrive on -- and is what every
monthly-equivalent conversion multiplies that paycheck back up by.  No door
validated one against the other, and a profile's paycheck recurs every pay
period BY DEFINITION (``salary.profiles._paycheck_template``), so the two were
never independently authorable in the first place.  Measured on the
developer's own ``$91,675`` salary, a profile reading 26 beside a 7-day cadence
modelled ``$15,279.20`` of monthly gross against a true ``$7,639.60`` -- the
year's paychecks summing to 200% of salary.

Upgrade:

  1. Report every profile whose stored count disagrees with the count its
     owner's cadence derives, naming both values.  Reported and NOT refused:
     a disagreeing row is an owner whose modelled income was WRONG, and this
     migration is what corrects it -- refusing would leave them broken.  Zero
     such rows on production, which reads 26 beside a 14-day cadence.
  2. Drop ``ck_salary_profiles_positive_periods`` and the column.

Downgrade re-adds the column (NOT NULL, server default 26) and backfills it
from each owner's cadence -- ``round(365.2425 / cadence_days)``, the same
derivation :attr:`app.services.pay_calendar.PayCadence.periods_per_year`
applies -- falling back to the default for an owner with no
``budget.pay_schedule`` row and no pay period to infer one from.

**The documented asymmetry**: for an owner whose stored value AGREED with
their cadence the restore is exact, which is every row on production.  For a
disagreeing owner it restores the DERIVED count rather than the stored one --
the value the application uses either side of this migration, and the one that
made their modelled income correct.  The stored disagreeing value is not
recoverable after the drop, and re-creating it would re-create the finding.

**The rollback path does not run this.**  ``deploy/shekel-deploy.sh`` rolls
back by re-pinning the previous image and REFUSES when the database has
migrated past what that image can resolve (``repin_is_safe``); it never issues
``alembic downgrade``.  This downgrade is the developer's own step-back path,
which is why it restores rather than refuses.

Review: developer-ruled 2026-08-19 -- "derive now, schedule real semi-monthly
later", chosen from four options.  Semi-monthly pay (24) is the 1st and the
15th, which no fixed ``cadence_days`` expresses; the ruling accepts the
NOMINAL 15-day walk here -- which gives the right count and drifting paydays,
exactly as a monthly cadence already does -- and schedules a day-of-month
schedule KIND as its own pay-calendar step.

Revision ID: f2b7c40d918e
Revises: a4c6f1d92b73
Create Date: 2026-08-19 06:00:00.000000
"""
import logging

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'f2b7c40d918e'
down_revision = 'a4c6f1d92b73'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: Days in the mean Gregorian year.  Spelled here rather than imported from
#: :data:`app.services.pay_calendar.DAYS_PER_YEAR` because a migration must
#: keep running after the application constant moves or is renamed -- the
#: standing rule for every literal a migration derives a stored value from.
_DAYS_PER_YEAR = "365.2425"

#: The cadence an owner with no resolvable one falls back to on downgrade --
#: the column's own historical server default, and ``DEFAULT_PAY_CADENCE_DAYS``.
_DEFAULT_PERIODS = 26

#: The one derivation, as SQL: ``round(365.2425 / cadence_days)``.  ``numeric``
#: rounding is half-away-from-zero, which is the ``ROUND_HALF_UP`` PayCadence
#: states; no cadence in 1..365 produces an exact half, so the two agree on
#: every reachable input either way.
_DERIVED_COUNT = f"round({_DAYS_PER_YEAR}::numeric / ps.cadence_days)"


def upgrade():
    """Report any disagreeing profile, then drop the column and its CHECK."""
    bind = op.get_bind()

    disagreeing = bind.execute(sa.text(
        "SELECT sp.id, sp.user_id, sp.pay_periods_per_year, "
        f"       ps.cadence_days, {_DERIVED_COUNT} AS derived "
        "FROM salary.salary_profiles sp "
        "JOIN budget.pay_schedule ps ON ps.user_id = sp.user_id "
        f"WHERE sp.pay_periods_per_year <> {_DERIVED_COUNT} "
        "ORDER BY sp.id"
    )).fetchall()
    for row in disagreeing:
        logger.warning(
            "R-F16: salary profile %s (user %s) stored %s paychecks a year "
            "beside a %s-day cadence, which derives %s.  Its modelled "
            "paycheck was wrong by a factor of %s/%s and this migration "
            "corrects it; the stored value is not carried forward.",
            row.id, row.user_id, row.pay_periods_per_year,
            row.cadence_days, int(row.derived),
            int(row.derived), row.pay_periods_per_year,
        )
    if not disagreeing:
        logger.info(
            "R-F16: every salary profile's stored paycheck count agrees with "
            "its owner's cadence; the drop moves no money."
        )

    op.drop_constraint(
        "ck_salary_profiles_positive_periods", "salary_profiles",
        schema="salary", type_="check",
    )
    op.drop_column("salary_profiles", "pay_periods_per_year", schema="salary")


def downgrade():
    """Re-add the column + its CHECK, backfilled from each owner's cadence."""
    op.add_column(
        "salary_profiles",
        sa.Column(
            "pay_periods_per_year", sa.Integer(), nullable=False,
            server_default=sa.text(str(_DEFAULT_PERIODS)),
        ),
        schema="salary",
    )
    # Restore the DERIVED count, which is the value the application used on
    # either side of this migration.  An owner with no pay_schedule row keeps
    # the server default the ADD COLUMN already wrote.
    op.execute(
        "UPDATE salary.salary_profiles sp "
        f"SET pay_periods_per_year = {_DERIVED_COUNT} "
        "FROM budget.pay_schedule ps "
        "WHERE ps.user_id = sp.user_id "
        f"  AND {_DERIVED_COUNT} > 0"
    )
    op.create_check_constraint(
        "ck_salary_profiles_positive_periods", "salary_profiles",
        "pay_periods_per_year > 0", schema="salary",
    )
