"""one level relation: the bank's placements join the owner's true-ups

Revision ID: d2e9f4a17c63
Revises: 6c15d2a97b78
Create Date: 2026-09-16 09:00:00.000000

Plan step **balance:X-bj-1**, rulings **R-IS** and **R-JN**, the eight forks
ruled by the developer 2026-09-16.  ``budget.account_anchor_history`` becomes
THE LEVEL RELATION: every observation that an account held a balance at the
close of a day, whoever observed it.

**What moves, and what it is worth.**  The bank's placed figures were two
columns on ``budget.statement_imports`` (``balance_effective_on``, the day the
file's stated balance is the balance FOR; ``balance_evidence_id``, how firmly),
nulled by UPDATE when a later import or a delete undercut them.  Each becomes
a row here naming its import, its amount locked to the file's own claim by
key, and a withdrawal becomes a row in the new ``budget.anchor_releases``.
The owner's 88 production assertions gain ``evidence_id = uncorroborated``
(the enum's own bottom rung: nothing confirms a typed figure) and a NULL
``statement_import_id`` (the owner declared it).  **On the 2026-09-16
production snapshot this INSERTs exactly ONE row** -- import 1, ``$2,229.73``
at 2026-07-17, ``file_chain`` -- and RELEASES none: ``system.audit_log`` holds
zero UPDATEs on ``statement_imports``, so no placement was ever withdrawn and
there is no lost release to reconstruct.  A DEVELOPMENT database that does
hold a released placement (the NULL pair under a stated figure) upgrades it
into "never placed", and the badge then states the never-placed cause for a
figure the app had withdrawn -- the release was an UPDATE that kept nothing,
so nothing here can tell the two apart; ``system.audit_log`` still holds the
old UPDATE for anyone who needs to.  No figure moves: the cash fold
reads the owner's rows alone until the flip
(``balance_predicates.owner_declared_clause``) and the bank walk reads the
same standing bank level it read off the import row.

**The backfill is a FAST DEFAULT, not an UPDATE.**  ``evidence_id`` is added
``NOT NULL DEFAULT <the uncorroborated id, read first>`` and the default
dropped in the next statement: PostgreSQL stores the literal in
``pg_attribute.attmissingval`` and rewrites no row, so no row UPDATE happens,
the append-only trigger sees nothing to refuse, and the audit log gains no
88 rows of noise.  The id is read from ``ref.statement_balance_evidence`` at
migration time and survives in the catalogue only as that missing-value
until a table rewrite; nothing in the schema names it.  The bank rows are
INSERTs, which the append-only refusal admits.

**The three CHECKs that paired and bounded the two columns go with them.**
The pairing is the level row's NOT NULL shape; the claim requirement is the
key ``fk_anchor_history_statement_import_claim`` onto the new superkey
``uq_statement_imports_id_stated_balance`` (a NULL ``stated_balance`` matches
no referencing row); the within-file bound spans two tables now and is
``budget.level_lies_within_file`` (:mod:`app.level_infrastructure`), attached
to BOTH so neither the level nor the file's span can move outside the other.

**Two trigger families change here and the three-caller contract holds**:
:mod:`app.append_only_infrastructure` gains its fourth table and the two
owner arms (a bank level goes with its import, a release with its level),
re-applied by this revision, ``scripts/init_database.py`` and
``scripts/build_test_template.py``; :mod:`app.level_infrastructure` is new,
applied by the same three.  ``budget.anchor_releases`` joins
``AUDITED_TABLES`` and attaches its own audit trigger here, as
``a7c41f9d2b60`` did for ``account_openings``.

**Downgrade** writes each STANDING bank level back onto its import's two
columns (a released level writes back nothing, which is exactly what the old
UPDATE-release produced), deletes the bank rows with the refusal lifted, drops
the release relation and the two columns, restores the three CHECKs, and
re-installs the PREVIOUS append-only function body over its three tables --
the ``b8e3d5a06c94`` precedent, so the database matches the revision the
chain lands on rather than losing the guard.
"""

from alembic import op
import sqlalchemy as sa

from app.append_only_infrastructure import (
    APPEND_ONLY_TRIGGERS,
    apply_append_only_infrastructure,
    remove_append_only_infrastructure,
)
from app.level_infrastructure import (
    apply_level_infrastructure,
    remove_level_infrastructure,
)

# revision identifiers, used by Alembic.
revision = "d2e9f4a17c63"
down_revision = "6c15d2a97b78"
branch_labels = None
depends_on = None


#: The bank's placements, moved in.  ``recorded_on`` is the day the import
#: was performed in the user's timezone -- the derivation every earlier
#: backfill of that column used -- and ``created_at`` is the import act's own
#: instant, so the level orders among the owner's rows by when it was recorded.
_MOVE_PLACEMENTS_IN_SQL = """
INSERT INTO budget.account_anchor_history (
    account_id, anchor_balance, observed_on, recorded_on, created_at,
    evidence_id, statement_import_id
)
SELECT account_id, stated_balance, balance_effective_on,
       (created_at AT TIME ZONE 'America/New_York')::date, created_at,
       balance_evidence_id, id
FROM budget.statement_imports
WHERE balance_effective_on IS NOT NULL
ORDER BY id
"""

#: Downgrade: a STANDING level writes back onto its import; a released one
#: writes back nothing, which is the state the old release left.
_WRITE_PLACEMENTS_BACK_SQL = """
UPDATE budget.statement_imports AS si
SET balance_effective_on = h.observed_on,
    balance_evidence_id = h.evidence_id
FROM budget.account_anchor_history AS h
WHERE h.statement_import_id = si.id
  AND NOT EXISTS (
      SELECT 1 FROM budget.anchor_releases AS r WHERE r.anchor_id = h.id
  )
"""

#: The three CHECKs ``4c1f8b7e2a90`` installed, verbatim, for the downgrade.
_BALANCE_EVIDENCE_PAIRED = (
    "(balance_effective_on IS NULL) = (balance_evidence_id IS NULL)"
)
_ANCHOR_NEEDS_A_CLAIM = (
    "balance_effective_on IS NULL OR stated_balance IS NOT NULL"
)
_EFFECTIVE_DAY_WITHIN_FILE = (
    "balance_effective_on IS NULL OR ("
    "balance_effective_on >= period_start - 1 "
    "AND balance_effective_on <= period_end "
    "AND balance_effective_on <= stated_balance_on)"
)

#: The tables the PREVIOUS append-only function served, for the downgrade.
_PREVIOUS_APPEND_ONLY_TABLES = (
    "budget.account_anchor_history",
    "budget.account_openings",
    "budget.loan_anchor_events",
)

#: The four tables THIS revision installs the refusal on, named literally
#: rather than read from ``APPEND_ONLY_TABLES`` so a replay from the start of
#: the chain installs what this revision installed even after the constant
#: grows again -- the reason ``f4a7c2d9e51b`` and ``b8e3d5a06c94`` name
#: theirs.
_APPEND_ONLY_TABLES_AT_THIS_REVISION = (
    *_PREVIOUS_APPEND_ONLY_TABLES, "budget.anchor_releases",
)

#: ``b8e3d5a06c94``'s function body, verbatim, for the downgrade -- the
#: revision the chain lands on installed exactly this.
_PREVIOUS_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION budget.refuse_append_only_change()
RETURNS TRIGGER AS $$
BEGIN
    -- TRUNCATE first, because it is the only arm with no OLD row to name.
    -- It is refused outright rather than conditionally: a TRUNCATE cannot
    -- distinguish disposing of an account from emptying the table, and it is
    -- invisible to the audit trigger, so permitting it would destroy history
    -- leaving no record anywhere.
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION
            '%.% is append-only; TRUNCATE rejected. Dispose of an account by '
            'deleting the account, which carries its history through the '
            'audit log.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            '%.% is append-only; UPDATE rejected for id=%. Record a '
            'correction by inserting a new row.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id;
    END IF;

    -- DELETE, evaluated at COMMIT because this trigger is DEFERRED.  The
    -- owning account still standing at the END of the transaction means this
    -- is a row being picked off rather than an account being disposed of --
    -- and asking at the end is what distinguishes a genuine disposal from a
    -- delete-and-recreate, which leaves the account standing by the time
    -- anybody looks.
    IF EXISTS (SELECT 1 FROM budget.accounts WHERE id = OLD.account_id) THEN
        RAISE EXCEPTION
            '%.% is append-only; DELETE rejected for id=%. History goes only '
            'with its account.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def _uncorroborated_id() -> int:
    """Return ``ref.statement_balance_evidence``'s ``uncorroborated`` id.

    Read by name because a migration seeds and reads reference rows by their
    name -- the ``4c1f8b7e2a90`` seed that wrote it is the only writer -- and
    the id is used ONCE, as the fast default that is dropped in the next
    statement; nothing in the schema keeps it.
    """
    row = op.get_bind().execute(sa.text(
        "SELECT id FROM ref.statement_balance_evidence "
        "WHERE name = 'uncorroborated'"
    )).first()
    if row is None:
        raise RuntimeError(
            "ref.statement_balance_evidence holds no 'uncorroborated' row; "
            "4c1f8b7e2a90 seeds it and this revision cannot run without it"
        )
    return int(row[0])


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


def _create_previous_append_only_triggers(table: str) -> None:
    """Re-create the three arms ``b8e3d5a06c94`` attached to one table."""
    update_arm, delete_arm, truncate_arm = APPEND_ONLY_TRIGGERS
    op.execute(
        f"CREATE TRIGGER {update_arm} BEFORE UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION budget.refuse_append_only_change()"
    )
    op.execute(
        f"CREATE CONSTRAINT TRIGGER {delete_arm} AFTER DELETE ON {table} "
        "DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION budget.refuse_append_only_change()"
    )
    op.execute(
        f"CREATE TRIGGER {truncate_arm} BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION "
        "budget.refuse_append_only_change()"
    )


def upgrade():
    """Widen the level relation, move the placements in, add the releases."""
    # 1. The owner's rows: evidence by fast default, no row UPDATE.
    op.execute(
        "ALTER TABLE budget.account_anchor_history "
        f"ADD COLUMN evidence_id INTEGER NOT NULL DEFAULT {_uncorroborated_id()}"
    )
    op.execute(
        "ALTER TABLE budget.account_anchor_history "
        "ALTER COLUMN evidence_id DROP DEFAULT"
    )
    op.create_foreign_key(
        "fk_account_anchor_history_evidence",
        "account_anchor_history", "statement_balance_evidence",
        ["evidence_id"], ["id"],
        source_schema="budget", referent_schema="ref",
        ondelete="RESTRICT",
    )
    op.add_column(
        "account_anchor_history",
        sa.Column("statement_import_id", sa.Integer(), nullable=True),
        schema="budget",
    )

    # 2. The superkey a bank level's amount keys onto.
    op.create_unique_constraint(
        "uq_statement_imports_id_stated_balance",
        "statement_imports", ["id", "stated_balance"], schema="budget",
    )

    # 3. The bank's placements move in -- INSERTs, which the append-only
    #    refusal admits -- BEFORE the keys, so the rows are graded by them.
    op.execute(_MOVE_PLACEMENTS_IN_SQL)

    op.create_foreign_key(
        "fk_anchor_history_statement_import_account",
        "account_anchor_history", "statement_imports",
        ["statement_import_id", "account_id"], ["id", "account_id"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_anchor_history_statement_import_claim",
        "account_anchor_history", "statement_imports",
        ["statement_import_id", "anchor_balance"], ["id", "stated_balance"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_anchor_history_statement_import",
        "account_anchor_history", ["statement_import_id"], schema="budget",
    )

    # 4. The two columns and their three CHECKs leave the import row.
    for name in (
        "ck_statement_imports_effective_day_within_file",
        "ck_statement_imports_anchor_needs_a_claim",
        "ck_statement_imports_balance_evidence_paired",
    ):
        op.drop_constraint(
            name, "statement_imports", schema="budget", type_="check",
        )
    op.drop_constraint(
        "fk_statement_imports_balance_evidence", "statement_imports",
        schema="budget", type_="foreignkey",
    )
    op.drop_column("statement_imports", "balance_evidence_id", schema="budget")
    op.drop_column("statement_imports", "balance_effective_on", schema="budget")

    # 5. The withdrawal relation.  The column-subset SET NULL is what lets the
    #    cause key be composite (a bare composite SET NULL would null
    #    account_id and fail on NOT NULL); Alembic renders it verbatim.
    op.create_table(
        "anchor_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("anchor_id", sa.Integer(), nullable=False),
        sa.Column("released_by_import_id", sa.Integer(), nullable=True),
        sa.Column("lines_changed_from", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["budget.accounts.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint("anchor_id", name="uq_anchor_releases_anchor"),
        sa.ForeignKeyConstraint(
            ["account_id", "anchor_id"],
            ["budget.account_anchor_history.account_id",
             "budget.account_anchor_history.id"],
            name="fk_anchor_releases_anchor_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["released_by_import_id", "account_id"],
            ["budget.statement_imports.id",
             "budget.statement_imports.account_id"],
            name="fk_anchor_releases_import_account",
            ondelete="SET NULL (released_by_import_id)",
        ),
        schema="budget",
    )
    op.create_index(
        "idx_anchor_releases_import", "anchor_releases",
        ["released_by_import_id"], schema="budget",
    )
    _attach_audit_trigger("anchor_releases")

    # 6. The two trigger families: the append-only function's new body and
    #    fourth table, and the within-file bound on both tables.
    apply_append_only_infrastructure(
        op.execute, tables=_APPEND_ONLY_TABLES_AT_THIS_REVISION,
    )
    apply_level_infrastructure(op.execute)


def downgrade():
    """Write standing placements back onto the import row; drop the rest."""
    remove_level_infrastructure(op.execute)
    # Lifted for the DELETE below: the previous body refuses a level delete
    # while its account stands, and every bank level's account stands.
    remove_append_only_infrastructure(
        op.execute, tables=_APPEND_ONLY_TABLES_AT_THIS_REVISION,
    )

    op.add_column(
        "statement_imports",
        sa.Column("balance_effective_on", sa.Date(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "statement_imports",
        sa.Column("balance_evidence_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute(_WRITE_PLACEMENTS_BACK_SQL)
    op.create_foreign_key(
        "fk_statement_imports_balance_evidence",
        "statement_imports", "statement_balance_evidence",
        ["balance_evidence_id"], ["id"],
        source_schema="budget", referent_schema="ref",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_statement_imports_balance_evidence_paired",
        "statement_imports", _BALANCE_EVIDENCE_PAIRED, schema="budget",
    )
    op.create_check_constraint(
        "ck_statement_imports_anchor_needs_a_claim",
        "statement_imports", _ANCHOR_NEEDS_A_CLAIM, schema="budget",
    )
    op.create_check_constraint(
        "ck_statement_imports_effective_day_within_file",
        "statement_imports", _EFFECTIVE_DAY_WITHIN_FILE, schema="budget",
    )

    op.drop_index(
        "idx_anchor_releases_import", table_name="anchor_releases",
        schema="budget",
    )
    op.drop_table("anchor_releases", schema="budget")

    # The bank rows go; the audit log keeps each as to_jsonb(OLD).
    op.execute(
        "DELETE FROM budget.account_anchor_history "
        "WHERE statement_import_id IS NOT NULL"
    )
    for name in (
        "uq_anchor_history_statement_import",
        "fk_anchor_history_statement_import_claim",
        "fk_anchor_history_statement_import_account",
        "fk_account_anchor_history_evidence",
    ):
        op.drop_constraint(name, "account_anchor_history", schema="budget")
    op.drop_column(
        "account_anchor_history", "statement_import_id", schema="budget",
    )
    op.drop_column("account_anchor_history", "evidence_id", schema="budget")
    op.drop_constraint(
        "uq_statement_imports_id_stated_balance", "statement_imports",
        schema="budget", type_="unique",
    )

    op.execute(_PREVIOUS_APPEND_ONLY_FUNCTION)
    for table in _PREVIOUS_APPEND_ONLY_TABLES:
        _create_previous_append_only_triggers(table)
