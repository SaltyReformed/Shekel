"""C-42: salary index restore + FK naming convention sweep

Closes audit findings F-071, F-072, F-079, F-137, F-138, F-139, and
F-140 (commit C-42 of the 2026-04-15 security remediation plan).
Three groups of operations:

  1. **Index restoration (F-071 / F-079).**  Migration
     ``22b3dd9d9ed3_add_salary_schema_tables.py`` dropped three
     child-FK indexes during the salary-schema introduction and never
     restored them (the downgrade body did not recreate them either).
     The live DB therefore sequential-scans
     ``salary.paycheck_deductions``, ``salary.salary_raises``, and
     ``salary.tax_brackets`` whenever the paycheck calculator joins by
     ``salary_profile_id`` or ``bracket_set_id``.  Today's
     single-user workload masks the regression; deduction / raise /
     bracket row counts grow with every year of usage and the query
     plan flips to seq-scan + filter as data accumulates.  Indexes
     recreated:

       * ``idx_deductions_profile`` ON
         ``salary.paycheck_deductions(salary_profile_id)``
       * ``idx_salary_raises_profile`` ON
         ``salary.salary_raises(salary_profile_id)``
       * ``idx_tax_brackets_bracket_set`` ON
         ``salary.tax_brackets(bracket_set_id, sort_order)``

  2. **Missing-FK-index sweep (F-139 / F-140).**  Live-DB
     ``pg_indexes`` shows four additional FK columns the original
     CREATE TABLEs left unindexed despite being the primary join /
     filter columns for their respective routes (loan amortisation,
     retirement dashboard, pension gap analysis, calibration
     overrides).  Same growth-driven performance regression as group
     1.  Indexes created:

       * ``idx_rate_history_account`` ON
         ``budget.rate_history(account_id, effective_date DESC)`` --
         DESC matches the predominant ORDER BY in
         ``app/routes/loan.py`` (``ORDER BY effective_date DESC``).
       * ``idx_pension_profiles_user`` ON
         ``salary.pension_profiles(user_id)``
       * ``idx_pension_profiles_salary_profile`` ON
         ``salary.pension_profiles(salary_profile_id)``
       * ``idx_calibration_deduction_overrides_deduction`` ON
         ``salary.calibration_deduction_overrides(deduction_id)``

  3. **FK naming convention sweep (F-072 / F-137 / F-138).**  Three
     FK constraints carry Alembic's default
     ``<table>_<column>_fkey`` name instead of the project's ``fk_*``
     convention documented in ``docs/coding-standards.md``:

       * ``interest_params_account_id_fkey`` (left in place by the
         44893a9dbcc3 finish-rename migration, which renamed from
         ``hysa_params_account_id_fkey`` to the table-prefixed default
         rather than the project convention) ->
         ``fk_interest_params_account``
       * ``transactions_credit_payback_for_id_fkey`` ->
         ``fk_transactions_credit_payback_for`` (self-reference;
         CASCADE-incompatible SET NULL semantics preserved)
       * ``scenarios_cloned_from_id_fkey`` ->
         ``fk_scenarios_cloned_from`` (self-reference; SET NULL
         semantics preserved)

     ``ALTER TABLE ... RENAME CONSTRAINT`` preserves every other
     property of the constraint (column list, referenced table /
     column, ondelete / onupdate, deferrability) -- the rename is a
     metadata-only catalog update.  No drop/recreate is needed.

All operations are idempotent.  Indexes use ``CREATE INDEX IF NOT
EXISTS`` directly.  FK renames are wrapped in a ``DO $$`` block that
checks ``pg_constraint`` for the legacy name before issuing the
``RENAME`` (PostgreSQL has no ``IF EXISTS`` form for ``ALTER TABLE
RENAME CONSTRAINT``).  This makes the migration safe to replay on
any combination of databases: the production DB that pre-dates the
indexes will receive real DDL; the test-template path that runs the
chain to head will receive a real rename; a database that has
already been forward-migrated will skip every operation as a no-op.

Downgrade reverses each operation symmetrically.  Renames restore
the immediately-prior names -- not the deep-legacy ``hysa_params_*``
names, which the 44893a9dbcc3 downgrade reverses separately.  Index
drops are guarded by ``IF EXISTS`` so the downgrade tolerates a DB
that lost the index out-of-band.

The post-creation shape check on the rate_history index verifies
the DESC ordering survived the CREATE INDEX call -- a future hand-
recreation that forgets the DESC keyword would silently break the
backward-scan optimisation the loan-rate-history route relies on, so
the shape check refuses to accept that state.

Review: solo developer, 2026-05-11 (audit 2026-04-15, C-42 retroactive sweep).
Destructive (audit tags D-13 + D-14): the upgrade renames three FK
constraints, which the destructive-migration policy in
``docs/coding-standards.md`` classifies under "rename" alongside
table / column renames.  Downgrade is symmetric and fully working.

Revision ID: c42b1d9a4e8f
Revises: a80c3447c153
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "c42b1d9a4e8f"
down_revision = "a80c3447c153"
branch_labels = None
depends_on = None


# Index specifications: (name, schema, table, column_expression).
# ``column_expression`` is the raw SQL inside the index's parenthesised
# column list -- one entry per column, joined by ", ".  The DESC on
# ``effective_date`` is preserved verbatim through ``sa.text`` rather
# than being decomposed into a column-list because PostgreSQL stores
# the sort direction in the index opclass and SQLAlchemy's
# ``op.create_index`` accepts ``sa.text`` entries for this case.  The
# shape check below verifies the DESC survived the CREATE.
INDEX_SPECS: tuple[tuple[str, str, str, list], ...] = (
    # F-071 / F-079: three indexes dropped by 22b3dd9d9ed3 and never
    # restored.  The names match the originals (where applicable) so
    # any pg_stat_user_indexes regression notes the operator wrote
    # against the old names continue to work after this migration.
    (
        "idx_deductions_profile",
        "salary",
        "paycheck_deductions",
        ["salary_profile_id"],
    ),
    (
        "idx_salary_raises_profile",
        "salary",
        "salary_raises",
        ["salary_profile_id"],
    ),
    # The original migration named this ``idx_tax_brackets_set``; the
    # remediation plan upgrades the name to ``idx_tax_brackets_bracket_set``
    # to make the FK column unambiguous (the table also carries a
    # ``sort_order`` second column in the index, and "set" alone is
    # ambiguous with the parent table name ``tax_bracket_sets``).
    (
        "idx_tax_brackets_bracket_set",
        "salary",
        "tax_brackets",
        ["bracket_set_id", "sort_order"],
    ),
    # F-139: rate_history is the query target for amortisation
    # projections against variable-rate loans.  The predominant query
    # is ``WHERE account_id = ? ORDER BY effective_date DESC LIMIT 1``
    # (the loan-rate-change route in app/routes/loan.py).  A composite
    # (account_id, effective_date DESC) index lets PostgreSQL satisfy
    # both the WHERE and the ORDER BY from a single forward index
    # scan; a plain (account_id) index would still require a sort
    # step.
    (
        "idx_rate_history_account",
        "budget",
        "rate_history",
        ["account_id", sa.text("effective_date DESC")],
    ),
    # F-140: three FK columns on the salary schema that are joined /
    # filtered by the retirement dashboard and the calibration
    # service but were never indexed.
    (
        "idx_pension_profiles_user",
        "salary",
        "pension_profiles",
        ["user_id"],
    ),
    (
        "idx_pension_profiles_salary_profile",
        "salary",
        "pension_profiles",
        ["salary_profile_id"],
    ),
    (
        "idx_calibration_deduction_overrides_deduction",
        "salary",
        "calibration_deduction_overrides",
        ["deduction_id"],
    ),
)


# FK rename specifications: (schema, table, old_name, new_name).
# Each entry rewrites a single ``pg_constraint`` row's ``conname``
# via ALTER TABLE RENAME CONSTRAINT.  The constraint's column list,
# referenced table, and ondelete behavior are preserved verbatim.
FK_RENAME_SPECS: tuple[tuple[str, str, str, str], ...] = (
    # F-072 / F-138: the 44893a9dbcc3 migration renamed
    # ``hysa_params_account_id_fkey`` to the table-prefixed
    # ``interest_params_account_id_fkey`` but did not advance the name
    # to the project's ``fk_*`` convention.  This migration completes
    # that work.
    (
        "budget",
        "interest_params",
        "interest_params_account_id_fkey",
        "fk_interest_params_account",
    ),
    # F-137: self-referential FK on transactions.credit_payback_for_id
    # (used by the credit-payback workflow).  ondelete=SET NULL is
    # preserved.
    (
        "budget",
        "transactions",
        "transactions_credit_payback_for_id_fkey",
        "fk_transactions_credit_payback_for",
    ),
    # F-137: self-referential FK on scenarios.cloned_from_id (used by
    # the scenario-clone workflow).  ondelete=SET NULL is preserved.
    (
        "budget",
        "scenarios",
        "scenarios_cloned_from_id_fkey",
        "fk_scenarios_cloned_from",
    ),
)


def _index_exists(bind, schema: str, name: str) -> bool:
    """Return True iff ``schema.name`` is an index in ``pg_indexes``.

    Queries ``pg_indexes`` directly so the existence check sees the
    same catalog view as the post-creation shape check below.

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute``.
        schema: Schema the index belongs to.
        name: Index name.

    Returns:
        True if an index with this exact name in this exact schema
        exists, False otherwise.
    """
    return bool(bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_indexes "
            "  WHERE schemaname = :schema AND indexname = :name"
            ")"
        ),
        {"schema": schema, "name": name},
    ).scalar())


def _index_definition(bind, schema: str, name: str) -> str | None:
    """Return the ``pg_indexes.indexdef`` string, or None if absent.

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute``.
        schema: Schema the index belongs to.
        name: Index name.

    Returns:
        The raw ``CREATE INDEX`` string PostgreSQL stores in
        ``pg_indexes.indexdef``, e.g.
        ``"CREATE INDEX idx_rate_history_account ON budget.rate_history
        USING btree (account_id, effective_date DESC)"``.  ``None`` if
        no such index exists.
    """
    return bind.execute(
        sa.text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = :schema AND indexname = :name"
        ),
        {"schema": schema, "name": name},
    ).scalar()


def _constraint_exists(bind, schema: str, name: str) -> bool:
    """Return True iff a constraint named ``name`` exists in ``schema``.

    Joins ``pg_constraint`` against ``pg_namespace`` so the search is
    scoped to the right schema -- two constraints in different
    schemas may legitimately share a name, and a global lookup would
    confuse the rename logic.

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute``.
        schema: Schema the constraint belongs to.
        name: Constraint name.

    Returns:
        True if the constraint exists, False otherwise.
    """
    return bool(bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_constraint cn "
            "  JOIN pg_class c ON c.oid = cn.conrelid "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            "  WHERE cn.conname = :name AND n.nspname = :schema"
            ")"
        ),
        {"schema": schema, "name": name},
    ).scalar())


def _create_index_if_missing(
    bind, name: str, schema: str, table: str, columns: list
) -> bool:
    """Create an index iff PostgreSQL does not already carry it.

    Uses :func:`op.create_index` so Alembic's bookkeeping captures
    the DDL alongside the surrounding operations.  Returns True iff
    the index was created on this call; False indicates the no-op
    path was taken.  The boolean return drives the idempotency
    assertions in the regression tests.

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute`` (used only for the existence check).
        name: Index name.
        schema: Schema the index belongs to.
        table: Table the index belongs to.
        columns: Column list passed to :func:`op.create_index`.  Each
            entry is either a column name string or a ``sa.text``
            expression for cases like ``effective_date DESC``.

    Returns:
        True iff DDL ran on this call.
    """
    if _index_exists(bind, schema, name):
        return False
    op.create_index(
        name, table, columns, unique=False, schema=schema,
    )
    return True


def _rename_constraint_if_legacy(
    bind, schema: str, table: str, old_name: str, new_name: str,
) -> bool:
    """Rename ``schema.table``'s constraint iff the legacy name exists.

    PostgreSQL has no ``IF EXISTS`` form for ``ALTER TABLE RENAME
    CONSTRAINT``, so the rename is preceded by an explicit
    ``pg_constraint`` lookup.  Returns True iff the rename was issued
    on this call; False indicates the new name was already present
    (or, less likely, both names were absent and the constraint had
    been dropped out-of-band; the missing-constraint case raises in
    :func:`_assert_constraint_present_post_rename`).

    The rename preserves the constraint's column list, referenced
    table, ondelete / onupdate behavior, and deferrability -- it is
    a metadata-only update.

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute``.
        schema: Schema the constraint belongs to.
        table: Table the constraint belongs to (used in the ALTER
            TABLE statement).
        old_name: Legacy constraint name to rename from.
        new_name: New constraint name to rename to.

    Returns:
        True iff DDL ran on this call.
    """
    if not _constraint_exists(bind, schema, old_name):
        return False
    # Safe to interpolate: schema, table, old_name, new_name are all
    # module-level literals.  PostgreSQL identifier rules disallow the
    # characters that would compose a SQL injection vector, but we
    # treat this as defence-in-depth.
    op.execute(
        f"ALTER TABLE {schema}.{table} "
        f"RENAME CONSTRAINT {old_name} TO {new_name}"
    )
    return True


def _assert_constraint_present_post_rename(
    bind, schema: str, new_name: str, old_name: str,
) -> None:
    """Verify the constraint exists under the new name and not the old.

    Guards against a partial migration state in which the rename
    skipped because the old name was absent but the new name never
    materialised (e.g., the constraint was dropped manually and
    never recreated).  Without this check the migration would silently
    succeed against a degraded schema in which the FK no longer
    exists at all.

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute``.
        schema: Schema the constraint belongs to.
        new_name: Expected constraint name.
        old_name: Legacy constraint name (must be absent after rename).
    """
    new_present = _constraint_exists(bind, schema, new_name)
    old_present = _constraint_exists(bind, schema, old_name)
    if not new_present:
        raise RuntimeError(
            f"Post-rename check failed: constraint {schema}.{new_name} "
            f"is missing after the rename pass.  Either the constraint "
            f"was dropped out-of-band before this migration ran or the "
            f"rename DDL was silently rolled back.  Inspect the DB "
            f"with `\\d+ {schema}.<table>` and recreate the FK by hand "
            f"before retrying."
        )
    if old_present:
        raise RuntimeError(
            f"Post-rename check failed: constraint {schema}.{old_name} "
            f"is still present after the rename pass.  The migration's "
            f"rename helper believes the rename succeeded but the "
            f"catalog disagrees.  This indicates two FK constraints "
            f"with overlapping column lists -- inspect the table with "
            f"`\\d+ {schema}.<table>` to disambiguate."
        )


def _assert_rate_history_index_has_desc(bind) -> None:
    """Verify ``idx_rate_history_account`` includes the DESC ordering.

    PostgreSQL renders DESC explicitly in ``pg_indexes.indexdef``,
    so the presence of ``DESC`` in the indexdef string is the
    canonical proof the sort direction survived the CREATE INDEX
    call.  A future hand-recreation that forgets the DESC keyword
    would silently produce an ascending index that requires a sort
    step for the loan-rate-history route's ``ORDER BY
    effective_date DESC`` query.

    Raises ``RuntimeError`` with the actual indexdef embedded so the
    operator can compare against the expected shape.
    """
    name = "idx_rate_history_account"
    schema = "budget"
    definition = _index_definition(bind, schema, name)
    if definition is None:
        raise RuntimeError(
            f"Post-creation shape check failed: index "
            f"{schema}.{name} is missing after the create pass."
        )
    # PostgreSQL renders DESC as a per-column attribute in the
    # indexdef string, e.g. "USING btree (account_id, effective_date
    # DESC)".  A naive case-sensitive substring check on " DESC"
    # (with the leading space) avoids matching DESCRIPTION or similar
    # tokens that could appear in another part of the string.
    if " DESC" not in definition:
        raise RuntimeError(
            f"Post-creation shape check failed: index "
            f"{schema}.{name} exists but its definition does not "
            f"include the DESC ordering.\n"
            f"  Actual indexdef: {definition!r}\n"
            f"  Expected substring (must be present): ' DESC'\n"
            f"Drop the index and rerun the migration:\n"
            f"  DROP INDEX {schema}.{name};"
        )


def upgrade():
    """Create missing indexes and rename Alembic-default FK names.

    Three-step pattern:

      1. Idempotency-guarded :func:`_create_index_if_missing` for
         each of seven indexes -- the three F-071 / F-079 indexes
         dropped by 22b3dd9d9ed3 and the four F-139 / F-140 indexes
         that were never declared.  Indexes that already exist
         (forward-migrated DBs replaying the chain) are skipped.
      2. Idempotency-guarded :func:`_rename_constraint_if_legacy` for
         each of three FK renames.  Constraints already under the new
         name are skipped.  Each rename is followed by a post-rename
         shape check that verifies the new name is present and the
         old name is absent.
      3. Targeted post-creation shape check on the rate_history index
         to verify the DESC ordering survived the CREATE INDEX call.
    """
    bind = op.get_bind()

    # Step 1: Index creation.
    for name, schema, table, columns in INDEX_SPECS:
        _create_index_if_missing(bind, name, schema, table, columns)

    # Step 2: FK renames + post-rename shape check.
    for schema, table, old_name, new_name in FK_RENAME_SPECS:
        _rename_constraint_if_legacy(bind, schema, table, old_name, new_name)
        _assert_constraint_present_post_rename(
            bind, schema, new_name, old_name,
        )

    # Step 3: Rate-history index DESC verification.
    _assert_rate_history_index_has_desc(bind)


def downgrade():
    """Reverse the renames and drop the seven indexes.

    The downgrade is symmetric.  FK renames are reversed first
    (restoring the immediately-prior names that 44893a9dbcc3 and the
    original create-table migrations carried); indexes are dropped
    afterwards.  The order does not matter functionally -- the two
    groups are independent -- but reversing renames before dropping
    indexes mirrors the upgrade order in reverse.

    Each operation is guarded so the downgrade tolerates a DB that
    has already lost the index or the new FK name out-of-band:
    ``DROP INDEX IF EXISTS`` for indexes; ``pg_constraint`` existence
    check for FK names.

    Review: solo developer, 2026-05-11 (audit 2026-04-15, C-42 retroactive sweep).
    """
    bind = op.get_bind()

    # Reverse step 2: FK renames.  Use the rename helper with old and
    # new swapped -- the helper checks the source name's existence, so
    # the call is a no-op against a DB that has already been
    # downgraded.
    for schema, table, old_name, new_name in FK_RENAME_SPECS:
        _rename_constraint_if_legacy(
            bind, schema, table,
            old_name=new_name,
            new_name=old_name,
        )

    # Reverse step 1: drop the seven indexes.  ``DROP INDEX IF
    # EXISTS`` is the simplest idempotency form -- op.drop_index
    # raises if the index is absent, whereas the IF EXISTS form
    # tolerates that state.  Indexes belong to schemas; ``DROP INDEX``
    # in PostgreSQL takes a schema-qualified name without the table.
    for name, schema, _table, _columns in INDEX_SPECS:
        op.execute(f"DROP INDEX IF EXISTS {schema}.{name}")
