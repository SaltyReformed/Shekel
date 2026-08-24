"""what a match act created is its own relation

Plan step ``bank_import:X-f6f``, developer ruling **R-GG** (2026-08-24).  What
a match act NAMES and what it MAKES are two relations, and they were one
column.

``statement_match_members.created_version_id`` could only mark a subject the
act also names, because it lives on the membership row.  That was enough while
the only created subject was a matched GROUP's residual (plan step
``bank_import:X-f6d-4``), which is both.  The create-a-purchase arm makes two
things -- the purchase it names, and often the budget line that HOLDS that
purchase -- and a container may not be a member: naming an envelope beside its
own purchase counts the same money twice (ruling **R-FM**), so
``_reject_parent_and_its_own_purchase`` refuses it outright.  The one subject
the undo most needed to reach therefore had nowhere to be recorded, which is
findings **N-333** and **N-340**.

``budget.statement_match_creations`` is that relation: one row per app row an
act brought into existence, carrying the subject's ``version_id`` as the act
left it.  ``created_version_id`` is NOT NULL here -- a row IS a creation, so
there is no "already existed" state for a NULL to mean -- and there is no
bank-line arm at all, which makes the CHECK the old column needed
(``..._created_is_an_app_row``) unspellable rather than enforced.

**The data migration is EMPTY and that is measured, not assumed.**  The old
column shipped 2026-08-23 and only ``_accept.record_match``'s residual arm ever
wrote it.  Measured on the dev database 2026-08-24: 230 matches, 465 members,
**0** carrying ``created_version_id``.  Production holds 0 statement imports and
therefore 0 matches.  The INSERT ... SELECT below is written anyway, because a
migration that assumes its own table is empty is a migration that silently
drops rows on the one database nobody measured.

Review: Josh, 2026-08-24 -- the column drop is destructive and the shape was
chosen by the developer over keeping a second column on the member row.

Revision ID: d1a4f7c9e620
Revises: 4c1f8b7e2a90
Create Date: 2026-08-24 10:35:00.000000
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'd1a4f7c9e620'
down_revision = '4c1f8b7e2a90'
branch_labels = None
depends_on = None

_SCHEMA = "budget"
_CREATIONS = "statement_match_creations"
_MEMBERS = "statement_match_members"
_IS_AN_APP_ROW = "ck_statement_match_members_created_is_an_app_row"
_MEMBER_POSITIVE = "ck_statement_match_members_created_version_positive"


def upgrade():
    """Apply forward migration."""
    op.create_table(
        _CREATIONS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("transaction_entry_id", sa.Integer(), nullable=True),
        sa.Column("created_version_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # Exactly one subject.  Summing the NULL tests is the spelling
        # ``ck_statement_match_members_one_subject`` uses.
        sa.CheckConstraint(
            "(transaction_id IS NOT NULL)::int "
            "+ (transaction_entry_id IS NOT NULL)::int = 1",
            name="ck_statement_match_creations_one_subject",
        ),
        # ``OptimisticLockMixin`` starts a counter at 1, so a created subject's
        # revision is positive -- the predicate every ``version_id`` column in
        # this schema already carries.
        sa.CheckConstraint(
            "created_version_id > 0",
            name="ck_statement_match_creations_version_positive",
        ),
        # This creation's account IS its act's, and IS its subject's.  The
        # composite keys are what make a creation naming another account's row
        # unwritable rather than merely unwritten.
        sa.ForeignKeyConstraint(
            ["match_id", "account_id"],
            ["budget.statement_matches.id",
             "budget.statement_matches.account_id"],
            name="fk_statement_match_creations_match_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id", "account_id"],
            ["budget.transactions.id", "budget.transactions.account_id"],
            name="fk_statement_match_creations_transaction_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_entry_id", "account_id"],
            ["budget.transaction_entries.id",
             "budget.transaction_entries.account_id"],
            name="fk_statement_match_creations_entry_account",
            ondelete="CASCADE",
        ),
        schema=_SCHEMA,
    )
    # One subject, at most one act that made it.  Partial, because one of the
    # two columns is NULL on every row and a NULL is not a claim.
    op.create_index(
        "uq_statement_match_creations_transaction", _CREATIONS,
        ["transaction_id"], unique=True, schema=_SCHEMA,
        postgresql_where=sa.text("transaction_id IS NOT NULL"),
    )
    op.create_index(
        "uq_statement_match_creations_entry", _CREATIONS,
        ["transaction_entry_id"], unique=True, schema=_SCHEMA,
        postgresql_where=sa.text("transaction_entry_id IS NOT NULL"),
    )
    op.create_index(
        "idx_statement_match_creations_match", _CREATIONS, ["match_id"],
        schema=_SCHEMA,
    )
    # Every new table in auth / budget / salary carries the audit trigger, and
    # ``app.audit_infrastructure.AUDITED_TABLES`` now names this one, so the
    # entrypoint's ``EXPECTED_TRIGGER_COUNT = len(AUDITED_TABLES)`` health
    # check auto-bumps with it.  Trigger name ``audit_<table>`` matches the
    # convention that check enumerates (``tgname LIKE 'audit_%'``); the shared
    # ``system.audit_trigger_func`` already exists from the rebuild migration,
    # and DROP IF EXISTS first makes a re-run idempotent.  Spelled inline
    # rather than through the infrastructure module's own generator, which is
    # private -- the same choice migration ``3f408018a71c`` made for the three
    # tables this one joins.
    op.execute(
        f"DROP TRIGGER IF EXISTS audit_{_CREATIONS} ON {_SCHEMA}.{_CREATIONS}"
    )
    op.execute(
        f"CREATE TRIGGER audit_{_CREATIONS} "
        f"AFTER INSERT OR UPDATE OR DELETE ON {_SCHEMA}.{_CREATIONS} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )

    # Carry across what the old column recorded.  Measured empty on both live
    # databases; written because a migration that assumes its own table is
    # empty drops rows on the one nobody measured.
    op.execute(
        f"INSERT INTO {_SCHEMA}.{_CREATIONS} "
        "(match_id, account_id, transaction_id, transaction_entry_id, "
        " created_version_id) "
        "SELECT match_id, account_id, transaction_id, transaction_entry_id, "
        "       created_version_id "
        f"FROM {_SCHEMA}.{_MEMBERS} "
        "WHERE created_version_id IS NOT NULL"
    )

    op.drop_constraint(
        _MEMBER_POSITIVE, _MEMBERS, schema=_SCHEMA, type_="check",
    )
    op.drop_constraint(
        _IS_AN_APP_ROW, _MEMBERS, schema=_SCHEMA, type_="check",
    )
    op.drop_column(_MEMBERS, "created_version_id", schema=_SCHEMA)


def downgrade():
    """Revert migration.

    **Value-lossless for a TRANSACTION creation the act also NAMES -- a
    group's residual, which is the only thing the restored column ever carried
    -- and lossy by construction for a CONTAINER and for a PURCHASE**, which is the whole reason this table
    exists: the shape being restored records the fact on a membership row, and
    a container has no membership row to hold it.  The rows that cannot travel
    are dropped rather than guessed at, and what that lands on is the
    behaviour before this step -- the release keeps a budget line the act
    created instead of removing it.  That is the state ``X-f6f`` replaced, so
    the downgrade returns to it rather than to something new.

    The copy back is keyed on ``(match_id, subject)`` rather than on any id of
    this table's own, because the member row is the one that has to carry it.
    """
    op.add_column(
        _MEMBERS,
        sa.Column("created_version_id", sa.Integer(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        _IS_AN_APP_ROW,
        _MEMBERS,
        "created_version_id IS NULL OR bank_statement_line_id IS NULL",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        _MEMBER_POSITIVE,
        _MEMBERS,
        "created_version_id IS NULL OR created_version_id > 0",
        schema=_SCHEMA,
    )
    # ``IS NOT DISTINCT FROM`` rather than ``=`` on the two subject columns:
    # exactly one of them is NULL on every row of both tables, and ``NULL =
    # NULL`` is unknown, so an equality join would match nothing at all.
    #
    # **Only a TRANSACTION creation travels, and that is the shape the restored
    # code can read.**  Before this migration the column was written by one arm
    # -- a group's residual, always a transaction member -- and the code it
    # returns to reads every member carrying it with ``db.session.get(
    # Transaction, member.transaction_id)``.  Copying a PURCHASE creation onto
    # its entry member would hand that read a ``None`` id: SQLAlchemy answers
    # ``None`` with a warning and the row is silently skipped, and two in one
    # act raise ``TypeError`` in the sort beside it.  The restored CHECK permits
    # the shape, so the filter is what forbids it.  Found by adversarial
    # financial review 2026-08-24.
    op.execute(
        f"UPDATE {_SCHEMA}.{_MEMBERS} AS m "
        "SET created_version_id = c.created_version_id "
        f"FROM {_SCHEMA}.{_CREATIONS} AS c "
        "WHERE c.match_id = m.match_id "
        "  AND c.transaction_id IS NOT NULL "
        "  AND c.transaction_id = m.transaction_id"
    )
    op.drop_index(
        "idx_statement_match_creations_match", table_name=_CREATIONS,
        schema=_SCHEMA,
    )
    op.drop_index(
        "uq_statement_match_creations_entry", table_name=_CREATIONS,
        schema=_SCHEMA,
    )
    op.drop_index(
        "uq_statement_match_creations_transaction", table_name=_CREATIONS,
        schema=_SCHEMA,
    )
    op.drop_table(_CREATIONS, schema=_SCHEMA)
