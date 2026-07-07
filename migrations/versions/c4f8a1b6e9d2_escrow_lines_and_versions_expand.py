"""create budget.escrow_lines + escrow_component_versions (supersession escrow, expand phase)

Revision ID: c4f8a1b6e9d2
Revises: a1c8e4f2b7d6
Create Date: 2026-07-06 14:00:00.000000

The EXPAND phase (Commit 1) of the escrow config redesign
(``docs/design/escrow_line_identity_refactor.md``).  ADDITIVE ONLY -- it creates
two new tables and backfills them from the legacy
``budget.escrow_components``, which it LEAVES IN PLACE and unchanged.  The
Commit-2 reader cutover repoints every escrow consumer onto these tables and
drops ``escrow_components`` (that later migration is the destructive one and
carries its own ``Review:`` line).  So this migration is not destructive and
behaviour is unchanged on deploy: nothing reads the new tables yet.

## The new model (supersession, no end_date)

- ``budget.escrow_lines`` -- the logical line's stable identity + current display
  name (``id`` PK, ``account_id`` CASCADE FK, ``name``).  Identity is the
  surrogate ``id``, never the mutable ``name``.
- ``budget.escrow_component_versions`` -- one effective-dated version per line
  (``line_id`` CASCADE FK, ``effective_date``, ``annual_amount``,
  ``inflation_rate``, ``is_removed``).  A version is active from its
  ``effective_date`` until the next version of the same line supersedes it --
  NO ``end_date``, so two versions of one line cannot overlap.  ``is_removed`` is
  a tombstone version (the line contributes 0 from that date).

The DDL matches the models in ``app/models/escrow_line.py`` (a future
``flask db migrate --autogenerate`` yields an empty diff); both tables are added
to ``app/audit_infrastructure.py::AUDITED_TABLES`` and get an audit trigger here,
following the ``d3d25212504b`` precedent (a narrow manual DROP+CREATE per new
table, NOT ``apply_audit_infrastructure``, so an earlier fresh-DB replay of the
rebuild migration is not asked to trigger tables that do not exist yet).

## The backfill (byte-identical behaviour on current data)

Maps the legacy ``[effective_date, end_date)`` range rows onto supersession
versions so "escrow as of any date D" is preserved exactly:

  1. **Overlap guard.**  ABORT (with a diagnostic) if any two NON-collapsed
     ``escrow_components`` rows of the same ``(account_id, name)`` line overlap in
     date.  A different-amount overlap (an amount change the temporal migration
     ``d1e7c4a2f9b3`` backfilled to origination) is indistinguishable from two
     real charges and must be resolved by hand -- exactly the manual-review case
     ``f2a7c1e9b4d3`` documented.  Confirmed ZERO such overlaps on the dev prod
     clone, so this is a no-op guard there; it protects any other user's data by
     failing safe rather than silently mangling.  (A zero-length COLLAPSED row --
     ``effective_date = end_date``, the ``f2a7c1e9b4d3`` fix's output -- is not an
     overlap and is simply dropped: it contributed nothing.)
  2. **Lines.**  One ``escrow_lines`` row per ``(account_id, name)`` that has at
     least one non-collapsed row.  A line all of whose rows are collapsed (e.g.
     account 3's old escrow name) yields no line -- matching pre-migration
     behaviour where it contributed nothing.
  3. **Versions.**  Each non-collapsed row becomes a real
     (``is_removed = false``) version at its ``effective_date``.
  4. **Tombstones.**  Each non-collapsed CLOSED row (``end_date`` set) whose
     ``end_date`` is not the ``effective_date`` of an adjacent successor in the
     same line gets a tombstone version (``is_removed = true``) at ``end_date``,
     so the line resolves to 0 after a removal or in a gap -- reproducing the old
     range's "no row active here" semantics.  An amount change (adjacent
     successor at ``end_date``) needs no tombstone: the successor supersedes.

Every step is idempotent (``NOT EXISTS`` guards) so a re-run after a partial
failure inserts nothing new.  **Self-contained:** imports nothing from ``app``;
all reads/writes are raw SQL against the schema, the discipline the
Step-2/3/4 backfills use.  The backfill helpers are module-level so
``tests/test_models/test_escrow_lines_backfill_migration.py`` can exercise the
money-critical derivations directly against engineered rows.

**Downgrade** drops both new tables (their audit triggers cascade with the
table).  Fully reversible with no data loss: ``escrow_components`` was never
touched, so the source of truth is intact; a re-upgrade rebuilds the new tables
from it.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "c4f8a1b6e9d2"
down_revision = "a1c8e4f2b7d6"
branch_labels = None
depends_on = None


# A row is NON-COLLAPSED (a real version) iff its range is open or has positive
# length; a COLLAPSED row (``effective_date = end_date``, the f2a7c1e9b4d3 fix's
# output) is a zero-length "never active" range that contributes nothing and is
# dropped by the backfill.  Reused verbatim in several statements below.
_NON_COLLAPSED = "(ec.end_date IS NULL OR ec.effective_date < ec.end_date)"


# Overlap guard.  Returns one row per pair of NON-collapsed same-line versions
# whose half-open ranges intersect (``a.eff < b.end AND b.eff < a.end`` with an
# open range treated as +infinity).  A non-empty result aborts the migration --
# a different-amount overlap needs manual resolution (see the module docstring).
_OVERLAP_CHECK_SQL = (
    "SELECT a.account_id, a.name, a.id AS id_a, b.id AS id_b "
    "FROM budget.escrow_components a "
    "JOIN budget.escrow_components b "
    "  ON a.account_id = b.account_id AND a.name = b.name AND a.id < b.id "
    "WHERE (a.end_date IS NULL OR a.effective_date < a.end_date) "
    "  AND (b.end_date IS NULL OR b.effective_date < b.end_date) "
    "  AND a.effective_date < COALESCE(b.end_date, 'infinity'::date) "
    "  AND b.effective_date < COALESCE(a.end_date, 'infinity'::date)"
)


# One escrow_lines row per (account_id, name) with >= 1 non-collapsed row.
# ``NOT EXISTS`` on escrow_lines makes it idempotent; the ``GROUP BY`` dedupes
# the group's many versions to a single line row.
_INSERT_LINES_SQL = (
    "INSERT INTO budget.escrow_lines (account_id, name, created_at, updated_at) "
    "SELECT ec.account_id, ec.name, now(), now() "
    "FROM budget.escrow_components ec "
    f"WHERE {_NON_COLLAPSED} "
    "  AND NOT EXISTS ( "
    "    SELECT 1 FROM budget.escrow_lines el "
    "    WHERE el.account_id = ec.account_id AND el.name = ec.name "
    "  ) "
    "GROUP BY ec.account_id, ec.name"
)


# Each non-collapsed row -> one real (is_removed=false) version under its line.
# The overlap guard guarantees no two non-collapsed rows of a line share an
# effective_date, so the (line_id, effective_date) unique cannot collide here.
_INSERT_VERSIONS_SQL = (
    "INSERT INTO budget.escrow_component_versions "
    "  (line_id, effective_date, annual_amount, inflation_rate, is_removed, "
    "   created_at, updated_at) "
    "SELECT el.id, ec.effective_date, ec.annual_amount, ec.inflation_rate, "
    "       false, now(), now() "
    "FROM budget.escrow_components ec "
    "JOIN budget.escrow_lines el "
    "  ON el.account_id = ec.account_id AND el.name = ec.name "
    f"WHERE {_NON_COLLAPSED} "
    "  AND NOT EXISTS ( "
    "    SELECT 1 FROM budget.escrow_component_versions v "
    "    WHERE v.line_id = el.id AND v.effective_date = ec.effective_date "
    "  )"
)


# Each non-collapsed CLOSED row with no adjacent successor (no non-collapsed
# sibling starting exactly at its end_date) -> a tombstone at end_date, so the
# line resolves to 0 after the removal/gap.  ``0.00`` annual is ignored for a
# removed version.  Both ``NOT EXISTS`` guards make it idempotent and prevent a
# collision with an already-present version at (line_id, end_date).
_INSERT_TOMBSTONES_SQL = (
    "INSERT INTO budget.escrow_component_versions "
    "  (line_id, effective_date, annual_amount, inflation_rate, is_removed, "
    "   created_at, updated_at) "
    "SELECT el.id, ec.end_date, 0.00, NULL, true, now(), now() "
    "FROM budget.escrow_components ec "
    "JOIN budget.escrow_lines el "
    "  ON el.account_id = ec.account_id AND el.name = ec.name "
    "WHERE ec.end_date IS NOT NULL AND ec.effective_date < ec.end_date "
    "  AND NOT EXISTS ( "
    "    SELECT 1 FROM budget.escrow_components succ "
    "    WHERE succ.account_id = ec.account_id AND succ.name = ec.name "
    "      AND succ.id <> ec.id "
    "      AND (succ.end_date IS NULL OR succ.effective_date < succ.end_date) "
    "      AND succ.effective_date = ec.end_date "
    "  ) "
    "  AND NOT EXISTS ( "
    "    SELECT 1 FROM budget.escrow_component_versions v "
    "    WHERE v.line_id = el.id AND v.effective_date = ec.end_date "
    "  )"
)


def _assert_no_overlaps(bind) -> None:
    """Abort the backfill if any two non-collapsed same-line versions overlap.

    A different-amount overlap that the temporal migration backfilled to
    origination is indistinguishable from two legitimate charges, so it cannot be
    auto-resolved; the migration fails loud with the offending pairs rather than
    silently collapsing a possibly-real line (the ``f2a7c1e9b4d3`` manual-review
    case).  A no-op on the dev prod clone (zero overlaps confirmed).

    Args:
        bind: A SQLAlchemy connection/bind exposing ``execute``.
    """
    rows = bind.execute(sa.text(_OVERLAP_CHECK_SQL)).fetchall()
    if rows:
        detail = "; ".join(
            f"account {row.account_id} name {row.name!r}: "
            f"escrow_components ids {row.id_a} and {row.id_b}"
            for row in rows
        )
        raise RuntimeError(
            "cannot backfill budget.escrow_lines: overlapping non-collapsed "
            "escrow_components versions within a line (a different-amount "
            "rename/amount-change duplicate the temporal migration created). "
            "Resolve each by hand via the escrow UI or SQL, then re-run. "
            f"Offending pairs: {detail}"
        )


def backfill_escrow_lines(bind) -> None:
    """Populate the new escrow tables from ``budget.escrow_components``.

    Runs the overlap guard, then inserts lines, real versions, and removal
    tombstones (see the module docstring).  Idempotent.  Exposed at module scope
    so the migration test can drive it against engineered rows.

    Args:
        bind: A SQLAlchemy connection/bind exposing ``execute`` (``op.get_bind()``
            in the migration; the test session in tests).
    """
    _assert_no_overlaps(bind)
    bind.execute(sa.text(_INSERT_LINES_SQL))
    bind.execute(sa.text(_INSERT_VERSIONS_SQL))
    bind.execute(sa.text(_INSERT_TOMBSTONES_SQL))


def _attach_audit_trigger(table: str) -> None:
    """Attach the shared audit trigger to a new ``budget`` table (idempotent).

    A narrow manual DROP+CREATE pair (the ``d3d25212504b`` precedent) rather than
    ``apply_audit_infrastructure``, so an earlier fresh-DB replay of the rebuild
    migration is never asked to trigger a table that does not exist yet.

    Args:
        table: The ``budget``-schema table name to attach ``audit_<table>`` to.
    """
    op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON budget.{table}")
    op.execute(
        f"CREATE TRIGGER audit_{table} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{table} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def upgrade():
    """Create the two escrow tables + audit triggers, then backfill from legacy.

    Additive only -- ``budget.escrow_components`` is untouched.  See the module
    docstring for the supersession model and the backfill derivation.
    """
    op.create_table(
        "escrow_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["budget.accounts.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="budget",
    )
    op.create_index(
        "ix_escrow_lines_account_name", "escrow_lines",
        ["account_id", "name"], unique=False, schema="budget",
    )

    op.create_table(
        "escrow_component_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column(
            "annual_amount", sa.Numeric(precision=12, scale=2), nullable=False,
        ),
        sa.Column(
            "inflation_rate", sa.Numeric(precision=5, scale=4), nullable=True,
        ),
        sa.Column(
            "is_removed", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["line_id"], ["budget.escrow_lines.id"], ondelete="CASCADE",
            name="fk_escrow_component_versions_line_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "line_id", "effective_date",
            name="uq_escrow_component_versions_line_effective_date",
        ),
        sa.CheckConstraint(
            "annual_amount >= 0",
            name="ck_escrow_component_versions_nonneg_annual_amount",
        ),
        sa.CheckConstraint(
            "inflation_rate IS NULL OR "
            "(inflation_rate >= 0 AND inflation_rate <= 1)",
            name="ck_escrow_component_versions_valid_inflation_rate",
        ),
        sa.CheckConstraint(
            "NOT is_removed OR annual_amount = 0",
            name="ck_escrow_component_versions_tombstone_zero_amount",
        ),
        schema="budget",
    )

    _attach_audit_trigger("escrow_lines")
    _attach_audit_trigger("escrow_component_versions")

    backfill_escrow_lines(op.get_bind())


def downgrade():
    """Drop both new tables (audit triggers cascade); ``escrow_components`` stays.

    Reversible with no data loss -- the legacy source table was never modified,
    so a re-upgrade rebuilds these tables from it.  Child (``versions``) dropped
    before parent (``lines``) to satisfy the FK.
    """
    op.drop_table("escrow_component_versions", schema="budget")
    op.drop_table("escrow_lines", schema="budget")
