"""C-28 ref.account_types user_id + per-user partial uniqueness + audit trigger

Closes F-044 (Medium) of the 2026-04-15 security remediation plan.  Three
DDL changes are bundled into a single migration because they share the
same goal -- converting ``ref.account_types`` from a globally-shared
catalogue into a multi-tenant table with a single seeded namespace --
and any partial application would leave the route-layer ownership check
without the storage tier it depends on:

  1. Add ``user_id INTEGER NULL`` with a foreign key to ``auth.users.id``
     (``ON DELETE RESTRICT``).  Pre-existing seeded rows remain
     ``NULL`` -- the convention is that ``user_id IS NULL`` denotes a
     built-in row managed by ``scripts/seed_ref_tables.py`` and is
     read-only to every owner.  ``RESTRICT`` rather than ``CASCADE``
     so a user delete that still has custom types referenced by
     ``budget.accounts`` rows cannot orphan those FKs by sweeping the
     types out from under them; the application is required to delete
     the dependent accounts first.

  2. Replace the legacy ``UNIQUE(name)`` constraint with two partial
     unique indexes that together encode the new namespace policy:

       * ``uq_account_types_seeded_name`` -- ``UNIQUE (name) WHERE
         user_id IS NULL``.  Preserves the old "exactly one built-in
         per name" invariant and keeps the ``ref_cache`` enum-to-id
         contract intact (each ``AcctTypeEnum`` member resolves to
         a single seeded row).
       * ``uq_account_types_user_name`` -- ``UNIQUE (user_id, name)
         WHERE user_id IS NOT NULL``.  Each owner may have at most
         one custom type per name; two different owners may both
         carry a custom "Crypto" without conflict; an owner may
         shadow a built-in name (her own "HYSA" alongside the
         seeded "HYSA") because the two index predicates are
         disjoint.

  3. Index ``user_id`` so the per-user listing queries (settings page,
     account form dropdown) plan as bitmap scans rather than full
     table reads.

  4. Attach a row-level audit trigger to ``ref.account_types`` so
     mutations through the new owner-scoped routes land in
     ``system.audit_log``.  The audit infrastructure rule in
     ``app/audit_infrastructure.py`` exempted the ``ref`` schema on
     the basis that those tables were read-only seed data; that
     premise no longer holds for ``account_types`` once owners can
     create, rename, and delete their own rows.  The matching entry
     in ``AUDITED_TABLES`` is added in the same commit and the
     entrypoint trigger-count health check picks up the new total
     automatically (``EXPECTED_TRIGGER_COUNT = len(AUDITED_TABLES)``).

Pre-flight semantics
--------------------

The two partial unique indexes encode the same uniqueness contract that
the dropped ``account_types_name_key`` enforced for the seeded subset
plus a stricter rule for the new owner-scoped subset.  Because every
existing row is seeded (``user_id IS NULL``), the seeded-name partial
index inherits the legacy invariant verbatim and there are no
pre-existing rows that could violate the user-name partial index.  No
detection query is needed -- the legacy constraint already guaranteed
the post-migration state for the rows in flight.  Future commits that
backfill ``user_id`` for any historical row would need their own
pre-flight; that is out of scope for C-28 because no such row exists.

Audit reference: F-044 / commit C-28 of the 2026-04-15 security
remediation plan.

Revision ID: d477228fee56
Revises: c5d20b701a4e
Create Date: 2026-05-08 06:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "d477228fee56"
down_revision = "c5d20b701a4e"
branch_labels = None
depends_on = None


# ── Constraint and index names ─────────────────────────────────────
#
# Each literal must stay in sync with the corresponding model
# declaration in ``app/models/ref.py:AccountType.__table_args__`` and
# any application-side ``is_unique_violation`` callers.  Renaming an
# index requires a coordinated edit across the model, this migration,
# and any caller that pattern-matches the index name in error
# handlers.  No such caller exists today -- the route layer surfaces
# the duplicate-name conflict via an explicit pre-flight query --
# but the symmetry is preserved so the contract holds if a future
# commit needs to fall back on IntegrityError discrimination.

LEGACY_NAME_UNIQUE = "account_types_name_key"
SEEDED_NAME_INDEX = "uq_account_types_seeded_name"
USER_NAME_INDEX = "uq_account_types_user_name"
USER_ID_INDEX = "ix_account_types_user_id"
USER_ID_FK = "fk_account_types_user_id_users"

SEEDED_PREDICATE = "user_id IS NULL"
USER_PREDICATE = "user_id IS NOT NULL"

AUDIT_TRIGGER = "audit_account_types"


def upgrade():
    """Add user_id, swap unique semantics, and attach audit trigger.

    Execution order matters: the column must exist before the partial
    indexes that reference it can be created, and the legacy
    ``UNIQUE(name)`` constraint must be dropped before the seeded-name
    partial index goes up (otherwise PostgreSQL reports a redundant
    constraint).  Alembic wraps the whole upgrade in a single
    transaction; a failure in any step rolls back the column add and
    every constraint mutation atomically.
    """
    # ── 1. Add the user_id column ─────────────────────────────────
    # ``ON DELETE RESTRICT`` is the conservative choice: deleting a
    # user that still has custom types in flight refuses, so the
    # ``budget.accounts.account_type_id`` FK can never dangle.  The
    # column is nullable because the seeded built-in rows must
    # continue to belong to no owner.
    op.add_column(
        "account_types",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey(
                "auth.users.id",
                name=USER_ID_FK,
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        schema="ref",
    )

    # ── 2. Drop the legacy global UNIQUE(name) constraint ─────────
    # The auto-generated name from PostgreSQL ("<table>_<column>_key")
    # was created when the column was first declared with
    # ``unique=True`` in the initial schema migration.  After the
    # constraint drops, the next two partial indexes encode the new
    # per-namespace uniqueness rule.
    op.drop_constraint(
        LEGACY_NAME_UNIQUE,
        "account_types",
        schema="ref",
        type_="unique",
    )

    # ── 3. Partial unique index for seeded built-ins ──────────────
    # ``UNIQUE (name) WHERE user_id IS NULL`` -- exactly one seeded
    # row per name.  This inherits the dropped global UNIQUE's
    # invariant for the rows it still needs to apply to (the seed
    # script's idempotent upsert pattern).
    op.create_index(
        SEEDED_NAME_INDEX,
        "account_types",
        ["name"],
        unique=True,
        schema="ref",
        postgresql_where=sa.text(SEEDED_PREDICATE),
    )

    # ── 4. Partial unique index for owner-scoped types ────────────
    # ``UNIQUE (user_id, name) WHERE user_id IS NOT NULL`` -- each
    # owner may carry at most one custom type per name.  The
    # disjoint predicate from the seeded index lets owners shadow
    # built-in names without collision.
    op.create_index(
        USER_NAME_INDEX,
        "account_types",
        ["user_id", "name"],
        unique=True,
        schema="ref",
        postgresql_where=sa.text(USER_PREDICATE),
    )

    # ── 5. Plain index on user_id for per-user listings ───────────
    # Settings/account-form queries filter by ``user_id IS NULL OR
    # user_id = :uid`` and would otherwise scan the entire table on
    # every render; the index brings them down to a small bitmap
    # scan as the table grows with custom types over time.
    op.create_index(
        USER_ID_INDEX,
        "account_types",
        ["user_id"],
        unique=False,
        schema="ref",
    )

    # ── 6. Attach audit trigger ───────────────────────────────────
    # The shared trigger function ``system.audit_trigger_func`` is
    # already in place from the rebuild migration; attaching a
    # row-level trigger is all that is needed to start logging
    # mutations.  The DROP IF EXISTS pair makes the step idempotent
    # against a re-run of this migration.
    op.execute(
        f"DROP TRIGGER IF EXISTS {AUDIT_TRIGGER} ON ref.account_types"
    )
    op.execute(
        f"CREATE TRIGGER {AUDIT_TRIGGER} "
        "AFTER INSERT OR UPDATE OR DELETE ON ref.account_types "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def downgrade():
    """Reverse the upgrade in strict reverse order.

    The downgrade must restore the live database to a state byte-for-byte
    compatible with the c5d20b701a4e revision.  That state has:

        * No ``user_id`` column on ``ref.account_types``.
        * No partial indexes named ``uq_account_types_*``.
        * The legacy ``account_types_name_key`` UNIQUE constraint on
          ``name``.
        * No row-level audit trigger on ``ref.account_types``.

    Reverse order matters: drop the audit trigger before any DDL on the
    table itself (the trigger depends on the table), drop the indexes
    before dropping the column they reference, and recreate the legacy
    UNIQUE constraint last so a partial failure leaves the table in a
    recognisable post-state for forensic review.

    Pre-flight semantics: restoring ``UNIQUE (name)`` requires that
    every row in the table at downgrade time satisfy the constraint.
    Any non-seeded rows present (added through the new owner routes
    after upgrade) potentially share a name with the seeded built-in,
    which the partial index allows but the legacy global UNIQUE would
    not.  The downgrade refuses cleanly when such conflicts are
    present rather than silently dropping rows or letting PostgreSQL
    surface a constraint-build error against a hard-to-interpret row
    list.  Resolving the conflict means deleting one row from each
    group manually after confirming with the user which row to keep.
    """
    bind = op.get_bind()

    # ── 1. Detect any pre-existing duplicate names that the legacy
    #      global UNIQUE(name) would reject ─────────────────────────
    duplicates = bind.execute(
        sa.text(
            "SELECT name, COUNT(*) AS cnt "
            "FROM ref.account_types "
            "GROUP BY name "
            "HAVING COUNT(*) > 1 "
            "ORDER BY name"
        )
    ).fetchall()
    if duplicates:
        details = "; ".join(
            f"name={row[0]!r} count={row[1]}" for row in duplicates
        )
        raise RuntimeError(
            "Refusing to downgrade C-28: the per-owner partial unique "
            "indexes allow multiple rows with the same name (one "
            "seeded plus one or more owner-scoped copies), but the "
            "legacy global UNIQUE(name) constraint that the downgrade "
            "must restore does not.  Resolve the conflicts manually "
            "(typically by deleting all but one row from each name "
            "group, after confirming with the user which row to "
            f"keep) and rerun the downgrade.  Offending names: {details}."
        )

    # ── 2. Drop the audit trigger (idempotent) ────────────────────
    op.execute(
        f"DROP TRIGGER IF EXISTS {AUDIT_TRIGGER} ON ref.account_types"
    )

    # ── 3. Drop the supporting per-user lookup index ──────────────
    op.drop_index(
        USER_ID_INDEX,
        table_name="account_types",
        schema="ref",
    )

    # ── 4. Drop the partial unique indexes ────────────────────────
    # Restating ``postgresql_where`` on the drop is informational only
    # (Alembic looks the index up by name), but it keeps the up/down
    # pair symmetric for any future autogenerate run.
    op.drop_index(
        USER_NAME_INDEX,
        table_name="account_types",
        schema="ref",
        postgresql_where=sa.text(USER_PREDICATE),
    )
    op.drop_index(
        SEEDED_NAME_INDEX,
        table_name="account_types",
        schema="ref",
        postgresql_where=sa.text(SEEDED_PREDICATE),
    )

    # ── 5. Restore the legacy global UNIQUE(name) constraint ──────
    # The pre-flight above guarantees no rows violate it at this point.
    op.create_unique_constraint(
        LEGACY_NAME_UNIQUE,
        "account_types",
        ["name"],
        schema="ref",
    )

    # ── 6. Drop the user_id column ────────────────────────────────
    # The column drop cascades the FK constraint declared at upgrade
    # time, so the explicit ``op.drop_constraint`` call is not needed.
    op.drop_column("account_types", "user_id", schema="ref")
