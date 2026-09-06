"""The pay rhythm gets a backward bound: pay_schedule.history_opens_on

Revision ID: a7e3c95d2f18
Revises: b8e3d5a06c94
Create Date: 2026-08-30 21:30:00.000000

Plan step ``balance:X-bh-2``, ruling **balance:R-IA**.  Closes finding
**balance:N-390**.

**What this stores and why it cannot be derived.**  The paycheck engine counts
a payday's position in its calendar month (a 24-per-year deduction skips the
month's third paycheck, a 12-per-year one is taken on its first) and the wages
already paid this calendar year (the FICA Social Security wage base, and a
deduction's ``annual_cap``).  Until this step it counted only paydays the app
had RECORDED, and a record opens later than a year does: measured on the
production owner, whose schedule opens 2026-03-26, the 2026 wage total for
2026-05-21 read ``$14,103.84`` from four recorded paydays against the
``$31,733.64`` of the nine that owner was really paid.

The remedy is to run the payday rhythm BACKWARD from the first recorded payday
at the stored cadence, exactly as it already runs forward past the horizon --
and a backward projection needs a floor, because an owner whose first payday
has not happened yet has no employment history to project.  The app knows the
CADENCE and cannot know when the job began, so the floor is asked and stored.
**``NULL`` means NOT STATED**, and an unstated history projects nothing: that
owner is counted from their recorded paydays, exactly as before this migration.

**Nullable, and no backfill -- which is deliberate and is NOT the conservative
direction.**  There is no derivation to seed this from:
``min(budget.pay_periods.start_date)`` is where the app's RECORD opens, and
writing it here would state as a fact precisely the guess N-390 measured wrong,
freezing today's under-count into the schema instead of removing it.  So every
existing row keeps ``NULL``.

**And nothing an existing row MEANS changes either, which is the ruling's
2026-08-31 amendment.**  A first form of R-IA had ``NULL`` mean "back to
``CALENDAR_DATE_MIN``", so this migration would have silently re-read every
pre-existing row as a claim its owner had never made.  Three adversarial
reviews of the step converged on why that is wrong: the backward rhythm's only
readers ask over one calendar month or one calendar year, so an unbounded reach
was never needed; and the error direction flips the wrong way.  An over-counted
year-to-date exhausts an ``annual_cap`` and retires the FICA wage base early --
understating the deduction and the tax, so OVERSTATING net -- where the
under-count it replaced was bounded by the record's opening.  One review priced
it: a ``$200,000`` salary whose record opens 2026-07-02 withholding
**``$1,437.91`` less Social Security tax** across its 14 recorded 2026
paychecks.  A budgeting app that must guess should guess poor, so an unstated
history counts only the record and this migration is inert in behaviour as well
as in DDL.

**The CHECK.**  ``ck_pay_schedule_history_opens_range`` bounds a stated opening
to the window this application has a calendar for, 2000-01-01..2100-12-31 --
the same pair ``ck_recurrence_rules_starts_on_range`` and
``ck_template_amount_versions_effective_date_range`` bound the other two
user-authored dates to, and for the same measured reason: an HTML date input
accepts a five-digit-year typo.  ``NULL`` satisfies it, as a CHECK is satisfied
by an unknown.

**The two dates are LITERALS here and are NOT read from
``app.utils.dates``, which is the opposite of what the model does.**
A migration is a historical record: the DDL it emitted on production is the DDL
it must emit on a database built from scratch tomorrow, and a constant imported
from live code can move under it -- a fresh install would then get a bound
production never had, silently.  The model builds the same text from
:data:`~app.utils.dates.CALENDAR_DATE_MIN` / ``_MAX`` because
``db.create_all`` (``scripts/init_database.py``) must install the CURRENT
bound, and the two are reconciled by a test rather than by memory:
``tests/test_models/test_pay_schedule.py::TestTheHistoryWindowIsTheApplicationsCalendar::test_the_INSTALLED_constraint_matches_the_model``
reads the constraint out of the catalogue and compares it with the model's.
Changing the window means writing a NEW migration, which is what leaves this
one's history intact.

**Rows REWRITTEN: zero.**  This ADDS a nullable column and a constraint no
existing row can violate (they are all NULL).  What each of those rows MEANS
changes; see above.

**Downgrade** drops the constraint and the column, and is value-lossless only
in the sense that it discards a fact nothing else records: an owner who had
stated when their paychecks began loses that statement and the engine returns
to counting from the first recorded payday.  Nothing else reads the column, so
no other value moves.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7e3c95d2f18"
down_revision = "b8e3d5a06c94"
branch_labels = None
depends_on = None

#: The CHECK's text, as LITERALS -- see the module docstring for why a
#: migration may not read them from live code.  The same two dates the model's
#: ``__table_args__`` builds from
#: :data:`~app.utils.dates.CALENDAR_DATE_MIN` / ``_MAX``.
_HISTORY_RANGE_CHECK = (
    "history_opens_on BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'"
)


def upgrade():
    """Add the nullable column and its calendar-window CHECK."""
    op.add_column(
        "pay_schedule",
        sa.Column("history_opens_on", sa.Date(), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_pay_schedule_history_opens_range",
        "pay_schedule",
        _HISTORY_RANGE_CHECK,
        schema="budget",
    )


def downgrade():
    """Drop the CHECK and the column.

    The constraint goes first: dropping the column would take it with it, but
    naming both makes the reversal readable as the inverse of the upgrade
    rather than as a side effect.
    """
    op.drop_constraint(
        "ck_pay_schedule_history_opens_range",
        "pay_schedule",
        schema="budget",
        type_="check",
    )
    op.drop_column("pay_schedule", "history_opens_on", schema="budget")
