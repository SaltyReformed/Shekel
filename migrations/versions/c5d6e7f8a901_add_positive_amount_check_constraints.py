"""Add positive amount CHECK constraints and baseline scenario unique index

Revision ID: c5d6e7f8a901
Revises: b4c7d8e9f012
Create Date: 2026-02-25 00:00:00.000000

Review: solo developer, 2026-05-11 (audit 2026-04-15, C-41 / F-069 downgrade hardening)

The downgrade body drops two CHECK constraints and one partial unique
index.  The original drops used ``op.drop_index`` and
``op.drop_constraint`` with no ``IF EXISTS`` form available on those
helpers; replaying the chain against a database that did not carry
the upgrade artefacts therefore raised ``UndefinedObject`` and left
the schema half-reverted.  The C-41 commit (audit 2026-04-15, F-069)
replaced the helper calls with raw ``ALTER TABLE ... DROP CONSTRAINT
IF EXISTS`` and ``DROP INDEX IF EXISTS`` statements so the downgrade
tolerates absent artefacts.

Two databases lack the upgrade artefacts in the wild:

  * The production database, bootstrapped via
    ``scripts/init_database.py`` -> ``db.create_all()`` -> Alembic
    ``stamp head`` per the audit-infrastructure rebuild migration's
    docstring.  This path materialises the schema from model
    declarations rather than running the migration chain, so the
    legacy ``ck_transactions_positive_*`` pair never reached the live
    schema and the partial unique index only reached it after C-41
    (commit ``a80c3447c153``).
  * Any developer DB initialised the same way before the test-template
    rebuild script (``scripts/build_test_template.py``) was introduced.

End-to-end test-template builds (``flask db upgrade`` from empty)
DO carry every upgrade artefact at the moment c5d6e7f8a901 ran;
subsequent migrations (notably
``724d21236759_drop_redundant_transaction_check_.py``) drop the
legacy CHECKs again before head is reached.  An end-to-end downgrade
from head back through c5d6e7f8a901 first re-runs the
``724d21236759`` downgrade (which re-creates the legacy CHECKs) so
the constraints are present when c5d6e7f8a901's downgrade runs and
the bare drops would technically work.  The ``IF EXISTS`` guards
remain correct for that case (a no-op extra guard never hurts) and
are essential for the stamp-bootstrapped DB case.
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'c5d6e7f8a901'
down_revision = 'b4c7d8e9f012'
branch_labels = None
depends_on = None


def upgrade():
    """Add CHECK constraints for non-negative amounts and partial unique index."""

    op.create_check_constraint(
        'ck_transactions_positive_amount',
        'transactions',
        'estimated_amount >= 0',
        schema='budget',
    )

    op.create_check_constraint(
        'ck_transactions_positive_actual',
        'transactions',
        'actual_amount IS NULL OR actual_amount >= 0',
        schema='budget',
    )

    op.create_index(
        'uq_scenarios_one_baseline',
        'scenarios',
        ['user_id'],
        unique=True,
        schema='budget',
        postgresql_where=sa.text('is_baseline = TRUE'),
    )


def downgrade():
    """Remove CHECK constraints and partial unique index, idempotently.

    Each drop is wrapped in ``IF EXISTS`` semantics so the downgrade
    tolerates a database that never carried the upgrade artefacts
    (the stamp + ``db.create_all`` bootstrap path described in the
    module docstring).  ``op.drop_index`` and ``op.drop_constraint``
    have no ``IF EXISTS`` parameter, so the drops use raw
    ``op.execute`` statements directly.  The order matches the
    inverse of the upgrade: drop the partial unique index first
    (depends on no other artefact), then the two CHECK constraints.
    """

    op.execute(
        "DROP INDEX IF EXISTS budget.uq_scenarios_one_baseline"
    )

    op.execute(
        "ALTER TABLE budget.transactions "
        "DROP CONSTRAINT IF EXISTS ck_transactions_positive_actual"
    )

    op.execute(
        "ALTER TABLE budget.transactions "
        "DROP CONSTRAINT IF EXISTS ck_transactions_positive_amount"
    )
