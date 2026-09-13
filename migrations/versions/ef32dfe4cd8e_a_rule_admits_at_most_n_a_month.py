"""a rule admits at most N occurrences a month

Plan step **salary:R15-a** (the first leaf of R15, ruling **R-SAL29**,
developer 2026-09-13; the decomposition is **R-SAL32**): the recurrence
vocabulary gains a per-month CEILING, so that "every paycheck, at most 2 a
month" is a rule the application can state.

**Why the vocabulary needed a third value.**  ``budget.recurrence_rules`` says
when a definition fires with ``every <interval_n> <unit>`` from a first
occurrence, placed on a paycheck by one of two placements -- and no spelling on
those axes says *a month's first two paychecks*, which is how a payroll benefit
is taken on a biweekly payroll (the third paycheck of a three-payday month is a
premium holiday).  Ledger row **D59** measured it on the developer's own data:
his Health Insurance Allowance is paid 24 of 26 and was held as an
every-paycheck rule that an ``end_date`` of 2026-06-30 happened to stop before
2026-07-30, July's third payday, or a ``$100.00`` income row the employer does
not pay would have been generated.  Eleven of his twelve payroll deductions
carry the same cadence, spelled today as ``deductions_per_year = 24`` -- a
biweekly COUNT the paycheck engine reads as a MODE (**F-21**); plan step
**salary:R15-b** re-expresses each of those as a rule with this ceiling and
deletes that column.

**Why a ceiling and not a unit or a placement** (the alternatives, measured
against his paydays before the ruling): a semi-monthly unit firing on the 1st
and the 16th, placed on the first paycheck on or after each, reproduces every
2026 paycheck and then skips the WRONG one in a month whose paydays fall on
the 1st, 15th and 29th -- his July 2027 -- moving ``$517.21`` from the 29th's
paycheck to the 15th's; a set-valued placement would make one occurrence two
rows; re-expressing the eleven lines as every-paycheck rules takes
``$1,034.42`` a year the employer does not.  The ceiling is applied inside the
occurrence walk (``app.services.recurrence._occurrence._ceilinged``), composes
with the interval and the count bound, and for a paycheck cadence counts the
month's paydays ON THE OWNER'S CALENDAR -- the paycheck engine's own
month-ordinal rule, read from the same producer -- so a rule started on a
month's second payday still skips that month's third (the developer's
amendment to R-SAL29, 2026-09-13, after an adversarial review measured the
first build counting the rule's own occurrences and admitting it).

**No figure moves.**  The column lands ``NULL`` on every row -- no ceiling,
which is what every existing rule means -- and every reader treats ``NULL`` as
the rule it already was.  Measured on production 2026-09-13: 44 rules, 0 with
a ceiling to carry.  The first ceilinged rule is one the developer authors on
the template form, or one **salary:R15-b**'s migration writes.

**The CHECK is the floor only.**  ``NULL`` is the one spelling of "no
ceiling"; a zero would be a rule that never fires spelled as a cadence, the
``Once`` shape plan step R2e-3 deleted.  Which UNITS may carry a ceiling -- the
paycheck and week units, whose occurrences can repeat within a month; never a
calendar-month cadence, which fires at most once a month by construction -- is
held at the door and at construction
(``recurrence._resolution._require_month_ceiling_pair``) rather than here,
because ``unit_id`` names a ``ref`` row and a CHECK comparing it to a literal
id would tie the table to a seed.

**The downgrade REFUSES while any row carries a ceiling**, naming each.
Dropping the column silently turns "every paycheck, at most 2 a month" into
"every paycheck", which on the developer's lines is ``$1,034.42`` a year of
deductions the employer does not take, generated as rows or priced into every
projected paycheck -- a money change no rollback should make unasked.  The
literal repair is in the refusal.  On the day this lands the refusal is empty
(no row carries a ceiling), so a same-day ``flask db downgrade`` succeeds; it
first bites once a definition has been authored with one.  **The release that
carries it is a MIGRATION release** in ``deploy/shekel-deploy.sh``'s sense
whatever this function does: that script never downgrades -- after a failed
health check it re-reads the stamp, reverts the image pin only where the
previous image can resolve it, and otherwise names the pre-deploy dump and
stops -- so a rollback across this revision is the dump, read that way by the
operator (ruling **R-R14**).

Revision ID: ef32dfe4cd8e
Revises: 4d7123cd9803
Create Date: 2026-09-13 14:30:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ef32dfe4cd8e"
down_revision = "4d7123cd9803"
branch_labels = None
depends_on = None

#: The rows a downgrade would silently re-price: every rule stating a ceiling.
#: Listed with the owning arc's two arms so the operator can find the
#: definition each belongs to.
_CEILINGED_RULES = sa.text("""
    SELECT id, transaction_template_id, transfer_template_id, max_per_month
      FROM budget.recurrence_rules
     WHERE max_per_month IS NOT NULL
     ORDER BY id
""")


def upgrade():
    """Add the nullable ceiling column and its floor CHECK."""
    op.add_column(
        "recurrence_rules",
        sa.Column("max_per_month", sa.SmallInteger(), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_positive_max_per_month",
        "recurrence_rules",
        "max_per_month IS NULL OR max_per_month > 0",
        schema="budget",
    )


def downgrade():
    """Drop the CHECK and the column, refusing while any rule states a ceiling.

    Refuses rather than destroying what it cannot rebuild -- the standard
    ``e5b2c8a17d34`` and ``c4a19e7b2d80`` set -- and here the loss is money:
    a ceilinged rule with its ceiling dropped fires on paychecks the owner
    said it does not.
    """
    connection = op.get_bind()

    ceilinged = connection.execute(_CEILINGED_RULES).fetchall()
    if ceilinged:
        listed = "; ".join(
            f"rule {row[0]} (transaction_template_id={row[1]}, "
            f"transfer_template_id={row[2]}) at most {row[3]} a month"
            for row in ceilinged
        )
        raise RuntimeError(
            f"Cannot drop budget.recurrence_rules.max_per_month: "
            f"{len(ceilinged)} rule(s) state a per-month ceiling -- {listed}.  "
            "Dropping the column turns each into a rule that fires on every "
            "occurrence its unit names, which generates rows and prices "
            "paychecks the owner said it skips.  To downgrade anyway, first "
            "re-author each definition without its ceiling on the form (or "
            "UPDATE budget.recurrence_rules SET max_per_month = NULL WHERE "
            "id IN (...)), accepting that money change, then re-run."
        )

    op.drop_constraint(
        "ck_recurrence_rules_positive_max_per_month",
        "recurrence_rules",
        schema="budget",
        type_="check",
    )
    op.drop_column("recurrence_rules", "max_per_month", schema="budget")
