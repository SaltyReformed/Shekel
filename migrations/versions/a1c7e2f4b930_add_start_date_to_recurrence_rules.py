"""add start_date to recurrence_rules

The symmetric partner of ``end_date`` (``f8f8173ff361``): together the two
columns are a recurrence rule's VALIDITY WINDOW.

``start_period_id`` already exists but is a WEAK bound -- it only seeds
``effective_from`` when the caller passes none
(``recurrence_engine.resolve_generation_plan``), so
``transfer_recurrence.regenerate_for_template`` and the unarchive path, which
both supply their own ``effective_from``, silently discard it.
``match_periods`` filters on ``start_date`` UNCONDITIONALLY, exactly as it
already does on ``end_date``, so no caller can bypass it.

Written only by ``loan_recurrence_sync.sync_recurring_payment_bounds``, which
sets it to the loan's first contractual installment (plan step C9a): a loan
payment cannot precede the loan.  Every rule a user configures by hand keeps
``start_date IS NULL`` (unbounded), so this revision changes the generated
output of NOTHING that exists today -- the column is added empty and the
backfill below only touches loan-payment rules, whose bound lands far before
any materialized pay period on real data (verified on the dev clone: 2 loan
rules, originated 2018-12-01 and 2023-02-14, so their first installments
2019-01-01 / 2023-03-22 exclude no period).

Additive and non-destructive: adds one nullable column and populates it for
loan-payment rules.  The rows the missing bound already generated are cleaned
up by a SEPARATE revision, so this one is safe to run and revert on its own.

Revision ID: a1c7e2f4b930
Revises: c4e91a7b2d38
Create Date: 2026-07-19 22:40:00.000000
"""
import calendar
import datetime

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'a1c7e2f4b930'
down_revision = 'c4e91a7b2d38'
branch_labels = None
depends_on = None


# Every recurrence rule that drives a payment INTO a CONFIGURED loan, with the
# params its start bound derives from.  The ``loan_params`` join IS the
# predicate: it is the same one the app applies
# (``loan_recurrence_sync.bind_rule_to_loan`` bounds a rule iff
# ``load_loan_params(account_id)`` returns a row), so the migration and the
# service cannot disagree about which rules are loan payments.  Adding a
# ``ref.account_types.has_amortization`` filter would make this revision
# STRICTER than the code that maintains the column afterwards.
_LOAN_PAYMENT_RULES = sa.text("""
    SELECT rr.id AS rule_id, lp.origination_date, lp.payment_day
    FROM budget.recurrence_rules rr
    JOIN budget.transfer_templates tt ON tt.recurrence_rule_id = rr.id
    JOIN budget.loan_params lp ON lp.account_id = tt.to_account_id
""")

_SET_START_DATE = sa.text(
    "UPDATE budget.recurrence_rules SET start_date = :start_date "
    "WHERE id = :rule_id"
)


def _first_installment_date(origination_date, payment_day):
    """Return the ``payment_day`` of the month AFTER ``origination_date``.

    A migration-local copy of
    ``app.services.rate_period_engine.first_installment_date``: a migration must
    not import app code, which evolves independently of the schema this revision
    targets (the same rule ``c4e91a7b2d38`` follows for ``monthly_due_date``).

    This is the loan engine's own first-payment convention -- the replay seeds a
    from-origination projection one month after the origination anchor -- NOT
    "the next ``payment_day``".  A loan originating 2026-04-15 with
    ``payment_day`` 20 first bills 2026-05-20, not 2026-04-20.  ``payment_day``
    is clamped to the target month's length, so 31 resolves to Feb 28/29.

    Args:
        origination_date: The loan's immutable origination date.
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        The first contractual installment's due date.
    """
    month_zero = origination_date.month
    year = origination_date.year + month_zero // 12
    month = month_zero % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(payment_day, last_day))


def upgrade():
    """Add the column and bound every existing loan-payment rule."""
    op.add_column(
        'recurrence_rules',
        sa.Column('start_date', sa.Date(), nullable=True),
        schema='budget',
    )
    bind = op.get_bind()
    for row in bind.execute(_LOAN_PAYMENT_RULES).mappings().all():
        bind.execute(_SET_START_DATE, {
            "rule_id": row["rule_id"],
            "start_date": _first_installment_date(
                row["origination_date"], row["payment_day"],
            ),
        })


def downgrade():
    """Drop the column.

    No value is stranded: every ``start_date`` this revision wrote is DERIVED
    from ``loan_params`` (origination date + payment day), so ``upgrade`` on a
    re-run reproduces each one exactly.  Nothing else writes the column.
    """
    op.drop_column('recurrence_rules', 'start_date', schema='budget')
