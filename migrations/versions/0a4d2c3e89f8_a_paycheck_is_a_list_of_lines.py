"""a paycheck is base pay plus a list of lines

Revision ID: 0a4d2c3e89f8
Revises: 9c1e4b7a2d3f
Create Date: 2026-09-15 21:30:00.000000

Plan step **salary:R18-a** (the first leaf of R18; ruling **R-SAL38**,
developer 2026-09-15; ledger row **D59**)::

    salary.paycheck_deductions      ->  salary.paycheck_lines
    ref.deduction_timings           ->  ref.paycheck_line_kinds
      'pre_tax' / 'post_tax'        ->  'pre_tax_deduction' / 'post_tax_deduction'
    paycheck_lines.deduction_timing_id       ->  .paycheck_line_kind_id
    recurrence_rules.paycheck_deduction_id   ->  .paycheck_line_id

**A RENAME, and nothing but a rename: no row is created or deleted, no
figure moves, and every paycheck the engine prices reads byte-identical
before and after.**  A paycheck is base pay plus a list of LINES, and a
line's KIND is its position in the waterfall -- the deduction side today, and
from leaf R18-b the earning side too (``taxable_earning`` joining gross,
``after_tax_earning`` joining net).  The table that held only deductions is
renamed for the rows it is about to hold, in its own leaf so the rename is
graded alone (byte-identical over the developer's 63 saved paychecks) before
any semantics change beside it; the two new kinds are seeded by R18-b with the
engine arms that price them, so no tree can hold a kind row nothing prices.

**Every artifact the tables carry moves with them**, because PostgreSQL
renames a table and nothing named after it: the primary keys, every foreign
key (the two ``fk_*`` the C-43 sweep named and the two auto-named ones), the
five CHECKs, the unique constraints, the child-FK index, the ``SERIAL``
sequences, the NOT NULL constraints PostgreSQL 18 catalogues by name, and the
audit trigger -- the precedent is ``44893a9dbcc3``, which finished the
``hysa_params`` rename after the first pass left five artifacts behind and
every write double-logged.  A constraint rename has no ``IF EXISTS`` form, so
each is wrapped in a ``DO`` block that no-ops when the source name is absent:
production was bootstrapped by ``db.create_all()`` and carries the
model-declared names -- the rename tables below were read off the dev clone
of it (2026-09-15, at revision ``b3f7c2a91d4e``: 25 constraints on the lines
table and 4 on the kinds table, two of the 25 being the per-year column's
pair that ``542c61e48ee8`` drops, hence 23 pairs here) -- while a
fresh-from-migrations database's set is graded by
``tests/test_models/test_r18a_paycheck_lines_rename.py``, which holds the
head template's constraint set EQUAL to these pairs' new names; a
hand-touched database might carry either, and the migration succeeds against
any combination.  ``ck_recurrence_rules_one_owner`` follows its column's
rename by itself (a CHECK is stored against the attribute, not its name) and
is not touched.

The ref rows' names widen the column from ``VARCHAR(10)`` to ``VARCHAR(25)``
(``post_tax_deduction`` is 18 characters); the downgrade narrows it back
after restoring the two names, and REFUSES -- by the column's own cast --
any longer name still present, which is R18-b's rows and R18-b's downgrade to
remove.  ``system.audit_log.table_name`` keeps ``paycheck_deductions`` on
every row written before this revision: it is a record of what was written
where, and rewriting it would forge history.
"""
import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision = "0a4d2c3e89f8"
down_revision = "9c1e4b7a2d3f"
branch_labels = None
depends_on = None


#: ``(old, new)`` for every constraint on ``salary.paycheck_deductions``, in
#: the order the catalogue listed them on the dev clone (2026-09-15): the PK,
#: the four FKs, the five CHECKs, the unique pair, and the twelve NOT NULLs
#: PostgreSQL 18 names after the table.  The NOT NULL on the renamed kind
#: column takes the column's new name too.
_LINE_CONSTRAINTS = (
    ("paycheck_deductions_pkey", "paycheck_lines_pkey"),
    ("fk_paycheck_deductions_calc_method_id", "fk_paycheck_lines_calc_method_id"),
    ("fk_paycheck_deductions_deduction_timing_id",
     "fk_paycheck_lines_paycheck_line_kind_id"),
    ("paycheck_deductions_salary_profile_id_fkey",
     "paycheck_lines_salary_profile_id_fkey"),
    ("paycheck_deductions_target_account_id_fkey",
     "paycheck_lines_target_account_id_fkey"),
    ("ck_paycheck_deductions_positive_amount", "ck_paycheck_lines_positive_amount"),
    ("ck_paycheck_deductions_positive_cap", "ck_paycheck_lines_positive_cap"),
    ("ck_paycheck_deductions_valid_inflation_month",
     "ck_paycheck_lines_valid_inflation_month"),
    ("ck_paycheck_deductions_valid_inflation_rate",
     "ck_paycheck_lines_valid_inflation_rate"),
    ("ck_paycheck_deductions_version_id_positive",
     "ck_paycheck_lines_version_id_positive"),
    ("uq_paycheck_deductions_profile_name", "uq_paycheck_lines_profile_name"),
    ("paycheck_deductions_amount_not_null", "paycheck_lines_amount_not_null"),
    ("paycheck_deductions_calc_method_id_not_null",
     "paycheck_lines_calc_method_id_not_null"),
    ("paycheck_deductions_created_at_not_null", "paycheck_lines_created_at_not_null"),
    ("paycheck_deductions_deduction_timing_id_not_null",
     "paycheck_lines_paycheck_line_kind_id_not_null"),
    ("paycheck_deductions_id_not_null", "paycheck_lines_id_not_null"),
    ("paycheck_deductions_inflation_enabled_not_null",
     "paycheck_lines_inflation_enabled_not_null"),
    ("paycheck_deductions_is_active_not_null", "paycheck_lines_is_active_not_null"),
    ("paycheck_deductions_name_not_null", "paycheck_lines_name_not_null"),
    ("paycheck_deductions_salary_profile_id_not_null",
     "paycheck_lines_salary_profile_id_not_null"),
    ("paycheck_deductions_sort_order_not_null", "paycheck_lines_sort_order_not_null"),
    ("paycheck_deductions_updated_at_not_null", "paycheck_lines_updated_at_not_null"),
    ("paycheck_deductions_version_id_not_null", "paycheck_lines_version_id_not_null"),
)

#: The same for ``ref.deduction_timings``: the PK, the unique on ``name``
#: (whose index renames with it) and the two NOT NULLs.
_KIND_CONSTRAINTS = (
    ("deduction_timings_pkey", "paycheck_line_kinds_pkey"),
    ("deduction_timings_name_key", "paycheck_line_kinds_name_key"),
    ("deduction_timings_id_not_null", "paycheck_line_kinds_id_not_null"),
    ("deduction_timings_name_not_null", "paycheck_line_kinds_name_not_null"),
)

#: The owning arm on ``budget.recurrence_rules`` (plan step salary:R15-b).
_ARM_CONSTRAINTS = (
    ("fk_recurrence_rules_paycheck_deduction_id", "fk_recurrence_rules_paycheck_line_id"),
)

#: The two ref rows, ``(old name, new name)``.
_KIND_NAMES = (
    ("pre_tax", "pre_tax_deduction"),
    ("post_tax", "post_tax_deduction"),
)


def _rename_constraint(schema: str, table: str, old: str, new: str) -> None:
    """Rename one constraint on ``schema.table``, no-op when *old* is absent.

    PostgreSQL has no ``RENAME CONSTRAINT IF EXISTS``, so the rename is wrapped
    in a procedural block that checks ``pg_constraint`` for the source name
    on THIS table first (``44893a9dbcc3``'s shape).
    """
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS ("
        "  SELECT 1 FROM pg_constraint c "
        "  JOIN pg_class t ON t.oid = c.conrelid "
        "  JOIN pg_namespace n ON n.oid = t.relnamespace "
        f"  WHERE c.conname = '{old}' AND n.nspname = '{schema}' "
        f"    AND t.relname = '{table}'"
        ") THEN "
        f"  ALTER TABLE {schema}.{table} RENAME CONSTRAINT {old} TO {new}; "
        "END IF; END $$"
    )


def _rename_constraints(schema: str, table: str, pairs, *, reverse: bool) -> None:
    """Rename every ``(old, new)`` pair on ``schema.table``, or every ``(new, old)``."""
    for old, new in pairs:
        if reverse:
            _rename_constraint(schema, table, new, old)
        else:
            _rename_constraint(schema, table, old, new)


def _swap_audit_trigger(schema: str, table: str, old: str, new: str) -> None:
    """Replace the ``audit_<old>`` trigger on ``schema.table`` with ``audit_<new>``.

    The trigger stays attached to a renamed table under its old name (the
    ``hysa_params`` lesson: the rebuild migration then added the new-named one
    beside it and every write logged twice), so the old one is dropped and the
    new one created in its place -- the same DROP + CREATE pair
    ``app.audit_infrastructure._trigger_sql_for_table`` emits, spelled here so
    this revision's end state does not depend on the in-code table list.
    """
    op.execute(f"DROP TRIGGER IF EXISTS audit_{old} ON {schema}.{table}")
    op.execute(f"DROP TRIGGER IF EXISTS audit_{new} ON {schema}.{table}")
    op.execute(
        f"CREATE TRIGGER audit_{new} "
        f"AFTER INSERT OR UPDATE OR DELETE ON {schema}.{table} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def upgrade():
    """Rename the two tables, the kind column, the owning arm and every artifact."""
    # 1. The kind vocabulary: ref.deduction_timings -> ref.paycheck_line_kinds,
    #    its column widened for the longer names, and the two rows renamed.
    op.rename_table("deduction_timings", "paycheck_line_kinds", schema="ref")
    _rename_constraints("ref", "paycheck_line_kinds", _KIND_CONSTRAINTS, reverse=False)
    op.execute(
        "ALTER SEQUENCE IF EXISTS ref.deduction_timings_id_seq "
        "RENAME TO paycheck_line_kinds_id_seq"
    )
    op.alter_column(
        "paycheck_line_kinds", "name", schema="ref",
        type_=sa.String(25),
        existing_type=sa.String(10),
        existing_nullable=False,
    )
    for old, new in _KIND_NAMES:
        op.execute(
            f"UPDATE ref.paycheck_line_kinds SET name = '{new}' WHERE name = '{old}'"
        )

    # 2. The lines: salary.paycheck_deductions -> salary.paycheck_lines, the
    #    kind column, every constraint, the index, the sequence, the trigger.
    op.rename_table("paycheck_deductions", "paycheck_lines", schema="salary")
    op.alter_column(
        "paycheck_lines", "deduction_timing_id", schema="salary",
        new_column_name="paycheck_line_kind_id",
    )
    _rename_constraints("salary", "paycheck_lines", _LINE_CONSTRAINTS, reverse=False)
    op.execute(
        "ALTER INDEX IF EXISTS salary.idx_deductions_profile "
        "RENAME TO idx_paycheck_lines_profile"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS salary.paycheck_deductions_id_seq "
        "RENAME TO paycheck_lines_id_seq"
    )
    _swap_audit_trigger("salary", "paycheck_lines", "paycheck_deductions", "paycheck_lines")

    # 3. The owning arm on budget.recurrence_rules (salary:R15-b's column).
    op.alter_column(
        "recurrence_rules", "paycheck_deduction_id", schema="budget",
        new_column_name="paycheck_line_id",
    )
    _rename_constraints("budget", "recurrence_rules", _ARM_CONSTRAINTS, reverse=False)
    op.execute(
        "ALTER INDEX IF EXISTS budget.uq_recurrence_rules_paycheck_deduction_id "
        "RENAME TO uq_recurrence_rules_paycheck_line_id"
    )


def downgrade():
    """Restore every name, newest-first; refuses a kind name the old column cannot hold."""
    # 3. The owning arm.
    op.execute(
        "ALTER INDEX IF EXISTS budget.uq_recurrence_rules_paycheck_line_id "
        "RENAME TO uq_recurrence_rules_paycheck_deduction_id"
    )
    _rename_constraints("budget", "recurrence_rules", _ARM_CONSTRAINTS, reverse=True)
    op.alter_column(
        "recurrence_rules", "paycheck_line_id", schema="budget",
        new_column_name="paycheck_deduction_id",
    )

    # 2. The lines.
    _swap_audit_trigger("salary", "paycheck_lines", "paycheck_lines", "paycheck_deductions")
    op.execute(
        "ALTER SEQUENCE IF EXISTS salary.paycheck_lines_id_seq "
        "RENAME TO paycheck_deductions_id_seq"
    )
    op.execute(
        "ALTER INDEX IF EXISTS salary.idx_paycheck_lines_profile "
        "RENAME TO idx_deductions_profile"
    )
    _rename_constraints("salary", "paycheck_lines", _LINE_CONSTRAINTS, reverse=True)
    op.alter_column(
        "paycheck_lines", "paycheck_line_kind_id", schema="salary",
        new_column_name="deduction_timing_id",
    )
    op.rename_table("paycheck_lines", "paycheck_deductions", schema="salary")

    # 1. The kind vocabulary: the two names back, then the column narrowed --
    #    which is where a name this revision did not write (R18-b's) refuses.
    for old, new in _KIND_NAMES:
        op.execute(
            f"UPDATE ref.paycheck_line_kinds SET name = '{old}' WHERE name = '{new}'"
        )
    op.alter_column(
        "paycheck_line_kinds", "name", schema="ref",
        type_=sa.String(10),
        existing_type=sa.String(25),
        existing_nullable=False,
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS ref.paycheck_line_kinds_id_seq "
        "RENAME TO deduction_timings_id_seq"
    )
    _rename_constraints("ref", "paycheck_line_kinds", _KIND_CONSTRAINTS, reverse=True)
    op.rename_table("paycheck_line_kinds", "deduction_timings", schema="ref")
