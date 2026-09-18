"""A bank line is held by the sightings of the imports that showed it.

Plan step ``bank_import:X-f6b-1``, rulings **R-BI10** and **R-BAL71** (which
amends **R-BAL53** and **R-BAL54**).  MOVES NO MONEY: no balance input is
written or rewritten; every recorded line keeps its identity, its amount and
its day, and every placed level keeps its day and its figure.

What changes:

1. NEW ``budget.statement_line_sightings`` -- one row per import that showed a
   line, carrying what THAT source said about it: wording, merchant word,
   stated transaction day, its own id, running balance, category.  Backfilled
   with one sighting per existing line from the import that recorded it,
   which is the only import that showed it under the old schema.  Audit-
   logged like its siblings.
2. ``budget.bank_statement_lines`` DROPS the six columns that were one
   source's facts (``description``, ``transaction_on``, ``external_id``,
   ``running_balance``, ``source_category``, ``import_id``) with the keys on
   them, keeps its identity, its amount, its day and its merchant KEY, and
   gains a direct key onto ``budget.accounts`` (``AccountScopedMixin``'s,
   under the dialect's default name like every sibling's) -- it reached the
   account through its import while an import owned it.
3. ``budget.statement_imports`` RENAMES ``period_start`` / ``period_end`` to
   ``declared_start`` / ``declared_end`` -- the window the source DECLARES
   (ruling **R-BAL71**); for every existing import the stored line extremes
   ARE what a CSV declares, so the rename moves no value -- and DROPS the
   derived ``line_count`` / ``recorded_count`` with their two CHECKs (ruling
   **R-IY**: a derivable column is deleted, not maintained).
4. ``budget.level_lies_within_file``'s two attachments read the declared
   window; the import-side trigger fires ``BEFORE UPDATE OF declared_start,
   declared_end, stated_balance_on``.  Same rule, same count.
5. NEW ``budget.remove_line_left_unsighted``: ``AFTER DELETE`` on a sighting,
   delete the line if no sighting of it remains -- so an import's deletion
   takes exactly the lines no other import vouches for, and a line cannot
   outlive its last sighting.

The DOWNGRADE is exact for every state this revision can produce: it writes
each line's columns back from the sighting of the EARLIEST import that
showed it -- the "first recorded it" fact the old ``import_id`` meant -- and
each import's counts from its sightings.  One state the OLD schema cannot
hold: an import with no sighting at all, which ``ck_statement_imports_line_
count_positive`` refuses.  No door of this revision's tree writes one (the
CSV adapter refuses an empty file); the feed sync a later leaf adds can.
**The downgrade REFUSES while one exists**, naming the count and the
remedy, rather than deleting it: such an import may hold a placed level,
and the app's own delete door is what withdraws a level with a receipt
(``statement_import.delete_import``).  A migration that destroyed a
placement silently would be moving money on the way down.  A second such
state, also refused up front: one external id held on two lines of one
account by the sightings the downgrade would keep -- two SOURCES sharing an
id string, legal above this revision and refused by the old per-account
unique index below it.

Revision ID: af07125d00f1
Revises: 596408fab6f1
Create Date: 2026-09-18 15:20:00
"""

from alembic import op
import sqlalchemy as sa

from app.level_infrastructure import apply_level_infrastructure
from app.sighting_infrastructure import (
    apply_sighting_infrastructure,
    remove_sighting_infrastructure,
)

# revision identifiers, used by Alembic.
revision = "af07125d00f1"
down_revision = "596408fab6f1"
branch_labels = None
depends_on = None


# The order two acts on one account are told apart in: the instant, then the
# id -- ``StatementImport.act_order``, spelled here in SQL because a
# migration reads the tree as it is and not the model as it will be.
_ACT_ORDER = "i.created_at, i.id"

_BACKFILL_SIGHTINGS_SQL = """
INSERT INTO budget.statement_line_sightings (
    account_id, line_id, import_id, description, merchant, transaction_on,
    external_id, running_balance, source_category
)
SELECT l.account_id, l.id, l.import_id, l.description, m.name,
       l.transaction_on, l.external_id, l.running_balance, l.source_category
FROM budget.bank_statement_lines AS l
LEFT JOIN budget.merchants AS m ON m.id = l.merchant_id
ORDER BY l.id
"""

# DOWN: each line's per-source columns from the sighting of its EARLIEST
# import.  ``DISTINCT ON`` takes the first row per line in the given order.
_WRITE_LINES_BACK_SQL = f"""
UPDATE budget.bank_statement_lines AS l
SET import_id = first.import_id,
    description = first.description,
    transaction_on = first.transaction_on,
    external_id = first.external_id,
    running_balance = first.running_balance,
    source_category = first.source_category
FROM (
    SELECT DISTINCT ON (s.line_id)
           s.line_id, s.import_id, s.description, s.transaction_on,
           s.external_id, s.running_balance, s.source_category
    FROM budget.statement_line_sightings AS s
    JOIN budget.statement_imports AS i ON i.id = s.import_id
    ORDER BY s.line_id, {_ACT_ORDER}
) AS first
WHERE first.line_id = l.id
"""

# DOWN: each import's counts from its sightings -- lines it sighted, and
# lines it was the first to sight.
_WRITE_COUNTS_BACK_SQL = f"""
UPDATE budget.statement_imports AS target
SET line_count = counted.sighted,
    recorded_count = counted.first
FROM (
    SELECT s.import_id,
           count(*) AS sighted,
           count(*) FILTER (WHERE f.import_id = s.import_id) AS first
    FROM budget.statement_line_sightings AS s
    JOIN (
        SELECT DISTINCT ON (s2.line_id) s2.line_id, s2.import_id
        FROM budget.statement_line_sightings AS s2
        JOIN budget.statement_imports AS i ON i.id = s2.import_id
        ORDER BY s2.line_id, {_ACT_ORDER}
    ) AS f ON f.line_id = s.line_id
    GROUP BY s.import_id
) AS counted
WHERE counted.import_id = target.id
"""

# DOWN: the two states the old schema cannot hold (see the module docstring).
_COUNT_UNSIGHTED_IMPORTS_SQL = """
SELECT count(*) FROM budget.statement_imports AS i
WHERE NOT EXISTS (
    SELECT 1 FROM budget.statement_line_sightings AS s
    WHERE s.import_id = i.id
)
"""

# The old schema held one external id per ACCOUNT across lines
# (``uq_bank_statement_lines_external_id``); the relation holds one per
# SOURCE, so two sources sharing an id string on two lines is legal above this
# revision and unrepresentable below it.  Counted over the sightings the
# downgrade would choose -- the earliest import's per line -- because those
# are the rows that would collide.
_COUNT_COLLIDING_IDS_SQL = f"""
SELECT count(*) FROM (
    SELECT chosen.external_id
    FROM (
        SELECT DISTINCT ON (s.line_id) s.account_id, s.external_id
        FROM budget.statement_line_sightings AS s
        JOIN budget.statement_imports AS i ON i.id = s.import_id
        ORDER BY s.line_id, {_ACT_ORDER}
    ) AS chosen
    WHERE chosen.external_id IS NOT NULL
    GROUP BY chosen.account_id, chosen.external_id
    HAVING count(*) > 1
) AS collisions
"""

# DOWN: the two level-trigger functions as ``d2e9f4a17c63`` installed them,
# reading the columns by their previous names.  ``apply_level_infrastructure``
# installs the CURRENT text, so the previous text is spelled here.
_PREVIOUS_LEVEL_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION budget.refuse_level_outside_its_file()
RETURNS TRIGGER AS $$
DECLARE
    file budget.statement_imports%ROWTYPE;
BEGIN
    -- An owner-declared level names no file and is bounded by nothing here.
    IF NEW.statement_import_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO file FROM budget.statement_imports
    WHERE id = NEW.statement_import_id;
    IF NOT budget.level_lies_within_file(
        NEW.observed_on, file.period_start, file.period_end,
        file.stated_balance_on
    ) THEN
        RAISE EXCEPTION
            'level % for account % is dated % but its statement % covers '
            '%..% and states its balance as of %: a placed day must lie '
            'inside the file (rule budget.level_lies_within_file)',
            NEW.id, NEW.account_id, NEW.observed_on, file.id,
            file.period_start, file.period_end, file.stated_balance_on;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_PREVIOUS_IMPORT_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION budget.refuse_file_span_leaving_its_level()
RETURNS TRIGGER AS $$
DECLARE
    stranded budget.account_anchor_history%ROWTYPE;
BEGIN
    SELECT * INTO stranded FROM budget.account_anchor_history
    WHERE statement_import_id = NEW.id
      AND NOT budget.level_lies_within_file(
          observed_on, NEW.period_start, NEW.period_end,
          NEW.stated_balance_on
      )
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'statement % cannot cover %..% as of %: its level % is placed on '
            '%, which that span would leave outside the file (rule '
            'budget.level_lies_within_file)',
            NEW.id, NEW.period_start, NEW.period_end, NEW.stated_balance_on,
            stranded.id, stranded.observed_on;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_PREVIOUS_IMPORT_TRIGGER = (
    "CREATE TRIGGER ck_file_span_holds_level "
    "BEFORE UPDATE OF period_start, period_end, stated_balance_on "
    "ON budget.statement_imports "
    "FOR EACH ROW EXECUTE FUNCTION "
    "budget.refuse_file_span_leaving_its_level()"
)


def _attach_audit_trigger(table: str) -> None:
    """Attach ``system.audit_trigger_func`` to a table this revision creates.

    The idempotent pair ``app.audit_infrastructure`` writes, spelled here
    because the table did not exist when the audit revision ran and the
    entrypoint's trigger-count assertion expects it at boot.
    """
    op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON budget.{table}")
    op.execute(
        f"CREATE TRIGGER audit_{table} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{table} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def upgrade():
    """Create the sighting relation, move the per-source facts, declare the window."""
    # 1. The relation, and one sighting per existing line from the import
    #    that recorded it.
    op.create_table(
        "statement_line_sightings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("merchant", sa.String(length=100), nullable=True),
        sa.Column("transaction_on", sa.Date(), nullable=True),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("running_balance", sa.Numeric(precision=12, scale=2),
                  nullable=True),
        sa.Column("source_category", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "line_id", "import_id",
            name="uq_statement_line_sightings_line_import",
        ),
        sa.ForeignKeyConstraint(
            ["line_id", "account_id"],
            ["budget.bank_statement_lines.id",
             "budget.bank_statement_lines.account_id"],
            name="fk_statement_line_sightings_line_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_id", "account_id"],
            ["budget.statement_imports.id",
             "budget.statement_imports.account_id"],
            name="fk_statement_line_sightings_import_account",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "running_balance IS NULL OR running_balance < 'NaN'::numeric",
            name="ck_statement_line_sightings_running_balance_real",
        ),
        schema="budget",
    )
    op.create_index(
        "idx_statement_line_sightings_account_external_id",
        "statement_line_sightings", ["account_id", "external_id"],
        schema="budget",
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "idx_statement_line_sightings_import",
        "statement_line_sightings", ["import_id"], schema="budget",
    )
    op.create_index(
        "idx_statement_line_sightings_account_line",
        "statement_line_sightings", ["account_id", "line_id"],
        schema="budget",
    )
    _attach_audit_trigger("statement_line_sightings")
    op.execute(_BACKFILL_SIGHTINGS_SQL)

    # 2. The line keeps what every source agrees on.
    op.drop_constraint(
        "fk_bank_statement_lines_import_account", "bank_statement_lines",
        schema="budget", type_="foreignkey",
    )
    op.drop_index(
        "uq_bank_statement_lines_external_id",
        table_name="bank_statement_lines", schema="budget",
    )
    op.drop_constraint(
        "ck_bank_statement_lines_amount_real_nonzero", "bank_statement_lines",
        schema="budget", type_="check",
    )
    for column in (
        "description", "transaction_on", "external_id", "running_balance",
        "source_category", "import_id",
    ):
        op.drop_column("bank_statement_lines", column, schema="budget")
    op.create_check_constraint(
        "ck_bank_statement_lines_amount_real_nonzero", "bank_statement_lines",
        "amount <> 0 AND amount < 'NaN'::numeric", schema="budget",
    )
    op.create_foreign_key(
        "bank_statement_lines_account_id_fkey", "bank_statement_lines",
        "accounts", ["account_id"], ["id"],
        source_schema="budget", referent_schema="budget", ondelete="CASCADE",
    )

    # 3. The import declares its window and stores no count.
    op.alter_column(
        "statement_imports", "period_start", new_column_name="declared_start",
        schema="budget",
    )
    op.alter_column(
        "statement_imports", "period_end", new_column_name="declared_end",
        schema="budget",
    )
    op.execute(
        "ALTER TABLE budget.statement_imports RENAME CONSTRAINT "
        "ck_statement_imports_period_ordered "
        "TO ck_statement_imports_declared_ordered"
    )
    op.drop_constraint(
        "ck_statement_imports_line_count_positive", "statement_imports",
        schema="budget", type_="check",
    )
    op.drop_constraint(
        "ck_statement_imports_recorded_within_file", "statement_imports",
        schema="budget", type_="check",
    )
    op.drop_column("statement_imports", "line_count", schema="budget")
    op.drop_column("statement_imports", "recorded_count", schema="budget")

    # 4. The within-file bound reads the declared window; 5. a line goes
    #    with its last sighting.
    apply_level_infrastructure(op.execute)
    apply_sighting_infrastructure(op.execute)


def _refuse_what_the_old_schema_cannot_hold() -> None:
    """Raise while a state exists that the old schema refuses (module docstring).

    Two such states, each named with its count and its remedy, BEFORE any
    DDL runs -- so the downgrade refuses whole rather than raising an
    ``IntegrityError`` from a half-applied step.

    Raises:
        RuntimeError: An import with no sighting, or one external id the
            chosen sightings hold on two lines of one account.
    """
    bind = op.get_bind()
    unsighted = bind.execute(sa.text(_COUNT_UNSIGHTED_IMPORTS_SQL)).scalar()
    if unsighted:
        raise RuntimeError(
            f"{unsighted} statement import(s) hold no sighting -- a sync "
            "over a window with no line -- and the schema this revision "
            "downgrades to refuses a zero-line import "
            "(ck_statement_imports_line_count_positive).  Delete them on the "
            "statements page first, so a level one placed is released "
            "through the app's own door, then downgrade again.  Nothing was "
            "changed."
        )
    colliding = bind.execute(sa.text(_COUNT_COLLIDING_IDS_SQL)).scalar()
    if colliding:
        raise RuntimeError(
            f"{colliding} external id(s) are held on two lines of one "
            "account by the sightings this downgrade would keep -- two "
            "sources sharing an id string -- and the schema this revision "
            "downgrades to holds one id per account "
            "(uq_bank_statement_lines_external_id).  Delete one of the "
            "imports on the statements page first, then downgrade again.  "
            "Nothing was changed."
        )


def downgrade():
    """Write each line's facts back from its earliest sighting; drop the rest.

    Refuses while a state the old schema cannot hold exists
    (:func:`_refuse_what_the_old_schema_cannot_hold`), BEFORE anything is
    dropped.
    """
    _refuse_what_the_old_schema_cannot_hold()
    remove_sighting_infrastructure(op.execute)

    # 3. The counts, then the window's previous names.
    op.add_column(
        "statement_imports",
        sa.Column("line_count", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "statement_imports",
        sa.Column("recorded_count", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute(_WRITE_COUNTS_BACK_SQL)
    op.alter_column(
        "statement_imports", "line_count", nullable=False, schema="budget",
    )
    op.alter_column(
        "statement_imports", "recorded_count", nullable=False, schema="budget",
    )
    op.create_check_constraint(
        "ck_statement_imports_line_count_positive", "statement_imports",
        "line_count > 0", schema="budget",
    )
    op.create_check_constraint(
        "ck_statement_imports_recorded_within_file", "statement_imports",
        "recorded_count >= 0 AND recorded_count <= line_count",
        schema="budget",
    )
    op.execute(
        "ALTER TABLE budget.statement_imports RENAME CONSTRAINT "
        "ck_statement_imports_declared_ordered "
        "TO ck_statement_imports_period_ordered"
    )
    op.alter_column(
        "statement_imports", "declared_start", new_column_name="period_start",
        schema="budget",
    )
    op.alter_column(
        "statement_imports", "declared_end", new_column_name="period_end",
        schema="budget",
    )

    # 2. The line's six columns, from its earliest sighting.
    op.drop_constraint(
        "bank_statement_lines_account_id_fkey", "bank_statement_lines",
        schema="budget", type_="foreignkey",
    )
    op.add_column(
        "bank_statement_lines",
        sa.Column("import_id", sa.Integer(), nullable=True), schema="budget",
    )
    op.add_column(
        "bank_statement_lines",
        sa.Column("description", sa.String(length=200), nullable=True),
        schema="budget",
    )
    op.add_column(
        "bank_statement_lines",
        sa.Column("transaction_on", sa.Date(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "bank_statement_lines",
        sa.Column("external_id", sa.String(length=64), nullable=True),
        schema="budget",
    )
    op.add_column(
        "bank_statement_lines",
        sa.Column("running_balance", sa.Numeric(precision=12, scale=2),
                  nullable=True),
        schema="budget",
    )
    op.add_column(
        "bank_statement_lines",
        sa.Column("source_category", sa.String(length=100), nullable=True),
        schema="budget",
    )
    op.execute(_WRITE_LINES_BACK_SQL)
    op.alter_column(
        "bank_statement_lines", "import_id", nullable=False, schema="budget",
    )
    op.alter_column(
        "bank_statement_lines", "description", nullable=False, schema="budget",
    )
    op.drop_constraint(
        "ck_bank_statement_lines_amount_real_nonzero", "bank_statement_lines",
        schema="budget", type_="check",
    )
    op.create_check_constraint(
        "ck_bank_statement_lines_amount_real_nonzero", "bank_statement_lines",
        "amount <> 0 AND amount < 'NaN'::numeric "
        "AND (running_balance IS NULL OR running_balance < 'NaN'::numeric)",
        schema="budget",
    )
    op.create_index(
        "uq_bank_statement_lines_external_id", "bank_statement_lines",
        ["account_id", "external_id"], unique=True, schema="budget",
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "fk_bank_statement_lines_import_account", "bank_statement_lines",
        "statement_imports", ["import_id", "account_id"],
        ["id", "account_id"],
        source_schema="budget", referent_schema="budget", ondelete="CASCADE",
    )

    # 1. The relation goes; its audit trigger goes with the table.
    op.drop_index(
        "idx_statement_line_sightings_account_line",
        table_name="statement_line_sightings", schema="budget",
    )
    op.drop_index(
        "idx_statement_line_sightings_import",
        table_name="statement_line_sightings", schema="budget",
    )
    op.drop_index(
        "idx_statement_line_sightings_account_external_id",
        table_name="statement_line_sightings", schema="budget",
    )
    op.drop_table("statement_line_sightings", schema="budget")

    # 4. The within-file bound as it was: the same rule function, the
    #    previous trigger bodies over the previous column names.
    op.execute(_PREVIOUS_LEVEL_TRIGGER_FUNCTION)
    op.execute(_PREVIOUS_IMPORT_TRIGGER_FUNCTION)
    op.execute(
        "DROP TRIGGER IF EXISTS ck_file_span_holds_level "
        "ON budget.statement_imports"
    )
    op.execute(_PREVIOUS_IMPORT_TRIGGER)
