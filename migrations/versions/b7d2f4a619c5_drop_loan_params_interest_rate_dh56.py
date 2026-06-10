"""drop loan_params.interest_rate; seed origination RateHistory rows (DH-56)

DH-#56 retires ``loan_params.interest_rate``.  The loan's base / period-0
rate now lives in its origination :class:`RateHistory` row -- the single
source of truth the resolver derives ``state.current_rate`` from.  The
prior mirror-write (``add_rate_change`` overwrote the column with the
LATEST rate on every change) corrupted the column's period-0 meaning for a
changed ARM; dropping it removes that drift surface entirely.

Upgrade, in order:

  1. Fail loud if any loan has RateHistory rows but none at or before
     ``origination_date`` (the unrecoverable changed-ARM case: the column
     holds the mirror-corrupted LATEST rate, not origination, so it cannot
     seed a faithful origination row -- the operator must enter the true
     origination rate by hand first).
  2. Seed an origination RateHistory row (``effective_date =
     origination_date``, ``interest_rate`` = the column value,
     ``monthly_pi = NULL``) for every loan that has NO RateHistory rows --
     a fixed-rate or never-adjusted loan whose column IS still its
     origination rate.  Loans that already have a covering row (e.g. an
     ARM that recorded its origination rate as its first RateHistory
     entry) are skipped.
  3. Re-verify every loan now has a covering origination row; raise
     otherwise (catches a loan with a NULL column and no history).
  4. Drop the two interest_rate CHECK constraints and the column.

Downgrade re-adds the nullable column + its two CHECKs and backfills it
from the rate effective at origination (the most recent RateHistory row
with ``effective_date <= origination_date``).  This restores the column's
ORIGINATION-rate meaning -- equal to the original value for a
fixed/unchanged loan.  For a changed ARM the original column held the
mirror-corrupted LATEST rate, which is unrecoverable after the drop, so
the downgrade restores the cleaner origination rate instead (documented
asymmetry).  The origination rows the upgrade seeded are NOT deleted on
downgrade (they are valid data; a re-upgrade skips them as already
covered).

Review: developer-selected Option C (DH-#56 full column retirement), 2026-06-09

Revision ID: b7d2f4a619c5
Revises: 4ae84043e9c7
Create Date: 2026-06-09 20:05:00.000000
"""
import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'b7d2f4a619c5'
down_revision = '4ae84043e9c7'
branch_labels = None
depends_on = None


def upgrade():
    """Seed origination RateHistory rows, then drop interest_rate."""
    bind = op.get_bind()

    # 1. Fail loud on the unrecoverable changed-ARM case: RateHistory
    #    rows exist but none covers origination, and the column holds the
    #    mirror-corrupted LATEST rate (not origination), so it cannot
    #    seed a faithful origination row.
    unrecoverable = bind.execute(sa.text(
        "SELECT lp.account_id FROM budget.loan_params lp "
        "WHERE EXISTS (SELECT 1 FROM budget.rate_history rh "
        "              WHERE rh.account_id = lp.account_id) "
        "  AND NOT EXISTS (SELECT 1 FROM budget.rate_history rh "
        "                  WHERE rh.account_id = lp.account_id "
        "                    AND rh.effective_date <= lp.origination_date)"
    )).fetchall()
    if unrecoverable:
        ids = sorted(r[0] for r in unrecoverable)
        raise RuntimeError(
            "DH-#56 migration cannot derive a faithful origination rate "
            f"for loan account(s) {ids}: each has RateHistory rows but "
            "none at or before origination_date, and the retired "
            "interest_rate column held the LATEST (mirror-corrupted) "
            "rate, not origination.  Insert an origination RateHistory "
            "row by hand (effective_date = origination_date, the true "
            "origination rate from the loan documents) for each, then "
            f"re-run.  Diagnostic: SELECT account_id, origination_date "
            f"FROM budget.loan_params WHERE account_id IN {tuple(ids)}."
        )

    # 2. Seed an origination row for every loan with NO RateHistory --
    #    its column value IS the origination rate (no rate change ever
    #    overwrote it).  ``id`` (sequence) and ``created_at`` (DB default
    #    NOW()) fill themselves; ``monthly_pi`` / ``notes`` default NULL.
    op.execute(
        "INSERT INTO budget.rate_history "
        "    (account_id, effective_date, interest_rate) "
        "SELECT lp.account_id, lp.origination_date, lp.interest_rate "
        "FROM budget.loan_params lp "
        "WHERE lp.interest_rate IS NOT NULL "
        "  AND NOT EXISTS (SELECT 1 FROM budget.rate_history rh "
        "                  WHERE rh.account_id = lp.account_id)"
    )

    # 3. Re-verify every loan now has a covering origination row (catches
    #    a loan with a NULL interest_rate column and no RateHistory).
    uncovered = bind.execute(sa.text(
        "SELECT lp.account_id FROM budget.loan_params lp "
        "WHERE NOT EXISTS (SELECT 1 FROM budget.rate_history rh "
        "                  WHERE rh.account_id = lp.account_id "
        "                    AND rh.effective_date <= lp.origination_date)"
    )).fetchall()
    if uncovered:
        ids = sorted(r[0] for r in uncovered)
        raise RuntimeError(
            "DH-#56 migration could not seed an origination RateHistory "
            f"row for loan account(s) {ids} (NULL interest_rate column "
            "and no RateHistory).  Insert an origination row by hand "
            "before re-running."
        )

    # 4. Drop the column's two CHECKs and the column itself.
    op.drop_constraint(
        "ck_loan_params_interest_rate", "loan_params",
        schema="budget", type_="check",
    )
    op.drop_constraint(
        "ck_loan_params_interest_rate_upper", "loan_params",
        schema="budget", type_="check",
    )
    op.drop_column("loan_params", "interest_rate", schema="budget")


def downgrade():
    """Re-add interest_rate (nullable) + CHECKs; backfill from origination."""
    op.add_column(
        "loan_params",
        sa.Column("interest_rate", sa.Numeric(7, 5), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_loan_params_interest_rate", "loan_params",
        "interest_rate >= 0", schema="budget",
    )
    op.create_check_constraint(
        "ck_loan_params_interest_rate_upper", "loan_params",
        "interest_rate IS NULL OR interest_rate <= 1", schema="budget",
    )
    # Restore the column to the rate effective at origination (the most
    # recent RateHistory row with effective_date <= origination_date).
    # Equals the original value for a fixed/unchanged loan; for a changed
    # ARM the original column held the mirror-corrupted LATEST rate, which
    # is unrecoverable -- the origination rate is the cleaner restore.
    op.execute(
        "UPDATE budget.loan_params lp "
        "SET interest_rate = ("
        "    SELECT rh.interest_rate FROM budget.rate_history rh "
        "    WHERE rh.account_id = lp.account_id "
        "      AND rh.effective_date <= lp.origination_date "
        "    ORDER BY rh.effective_date DESC LIMIT 1)"
    )
