"""An assertion is append-only against TRUNCATE and against delete-and-recreate

Revision ID: b8e3d5a06c94
Revises: f4a7c2d9e51b
Create Date: 2026-08-30 20:00:00.000000

Plan step ``balance:X-f3c-2d``, ruling **balance:R-IC**.  The two defects
below were found and closed inside this one step, so neither was ever an open
ledger row.

**What this closes.**  ``f4a7c2d9e51b`` installed ONE ``BEFORE UPDATE OR
DELETE`` row trigger and justified its timing with a single sentence about the
UPDATE arm.  A refutation pass then broke the DELETE arm twice, both reproduced
on a clone of the test template with controls that fired first:

* ``TRUNCATE`` never reaches a row trigger.  With every account still standing,
  ``TRUNCATE budget.account_openings`` took the table from 4 rows to 0 and was
  refused by nothing.  ``system.audit_log`` is written by a row trigger too, so
  it recorded nothing either: the log was byte-identical across the statement.
  That made TRUNCATE the only spelling that destroyed history BOTH unrefused
  and unrecorded.
* Delete-and-recreate defeated the DELETE arm's predicate with two ordinary
  statements in one transaction.  ``DELETE FROM budget.accounts WHERE id=20``
  followed by an ``INSERT`` of the same id committed clean, leaving the account
  standing with its assertions destroyed -- because the predicate asks whether
  the owning account exists at the INSTANT the cascade runs, and at that
  instant it genuinely did not.

**What is installed.**  The same one function, ``budget
.refuse_append_only_change``, now attached as THREE arms per table because they
answer three different questions: ``ck_append_only`` ``BEFORE UPDATE ... FOR
EACH ROW`` (a question about the statement), ``ck_append_only_delete`` as a
``DEFERRABLE INITIALLY DEFERRED`` constraint trigger ``AFTER DELETE`` (a
question about the transaction's END state, which is the fix for
delete-and-recreate), and ``ck_append_only_truncate`` ``BEFORE TRUNCATE ... FOR
EACH STATEMENT`` (refused outright, since a TRUNCATE cannot distinguish
disposal from emptying the table and leaves no audit row behind).  The SQL
lives in :mod:`app.append_only_infrastructure`, shared with
``scripts/init_database.py`` and ``scripts/build_test_template.py``.

**No archive table, because the conservation already exists.**  All three
tables are in ``audit_infrastructure.AUDITED_TABLES`` and the audit trigger
writes ``to_jsonb(OLD)`` on DELETE.  Measured on a legitimate cascade disposal:
the audit row holds every column of every destroyed row, ``anchor_balance`` and
``opening_equity`` included.  Once TRUNCATE is refused, every path that removes
a row from these tables conserves it in full first, so a parallel archive would
duplicate ``system.audit_log`` rather than add a guarantee.

**No data changes and nothing to legalise.**  These refuse STATEMENTS rather
than a STATE, so no row that already exists can be in violation.

**Downgrade restores ``f4a7c2d9e51b``'s exact shape** -- the single combined
``BEFORE UPDATE OR DELETE`` trigger and its function body -- rather than
removing the guard or importing today's definition, which has moved on.  The
old body is inlined here for that reason: a downgrade must reproduce what the
revision it lands on produced, and that is a historical fact rather than a
current one.  Value-lossless in both directions: nothing is written or altered.

**One asymmetry, named rather than left to be discovered.**  A fresh replay of
the chain reaches ``f4a7c2d9e51b`` carrying THIS revision's three arms, because
that revision calls the live :mod:`app.append_only_infrastructure` -- the house
pattern :mod:`app.audit_infrastructure` documents, and what
``scripts/build_test_template.py`` relies on to let the latest in-code
definition win over migration-frozen state.  So downgrading to
``f4a7c2d9e51b`` does not leave the database in the state a fresh upgrade to
``f4a7c2d9e51b`` would.  What a downgrade guarantees is that THIS revision's
change is reverted, and that is what was tested: after it, ``TRUNCATE
budget.account_openings CASCADE`` succeeds again.
"""

from alembic import op

from app.append_only_infrastructure import (
    APPEND_ONLY_TABLES,
    apply_append_only_infrastructure,
    remove_append_only_infrastructure,
)

# revision identifiers, used by Alembic.
revision = "b8e3d5a06c94"
down_revision = "f4a7c2d9e51b"
branch_labels = None
depends_on = None


#: ``f4a7c2d9e51b``'s function body, frozen here because the downgrade must
#: reproduce what THAT revision produced.  Importing the current definition
#: would make the downgrade mean whatever the module means today.
_F4A7C2D9E51B_FUNCTION = """
CREATE OR REPLACE FUNCTION budget.refuse_append_only_change()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            '%.% is append-only; UPDATE rejected for id=%. Record a '
            'correction by inserting a new row.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id;
    END IF;

    IF EXISTS (SELECT 1 FROM budget.accounts WHERE id = OLD.account_id) THEN
        RAISE EXCEPTION
            '%.% is append-only; DELETE rejected for id=%. History goes only '
            'with its account.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id;
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql
"""


def upgrade():
    """Split the one trigger into its three arms on all three tables.

    :func:`app.append_only_infrastructure.apply_append_only_infrastructure`
    drops all three arm names before creating, so this runs cleanly over
    ``f4a7c2d9e51b``'s single combined trigger.
    """
    apply_append_only_infrastructure(op.execute)


def downgrade():
    """Restore ``f4a7c2d9e51b``'s single ``BEFORE UPDATE OR DELETE`` trigger.

    Drops all three arms and the shared function, then re-creates the function
    body and the one trigger that revision installed, so the database matches
    the revision the chain lands on rather than losing the guard entirely.
    """
    remove_append_only_infrastructure(op.execute)
    op.execute(_F4A7C2D9E51B_FUNCTION)
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER ck_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION "
            f"budget.refuse_append_only_change()"
        )
