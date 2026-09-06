"""statement match members say what the act created

Plan step ``bank_import:X-f6d-4``, developer ruling 2026-08-23.  A matched
GROUP's difference is recorded as an ordinary uncategorized row (ruling
**R-FN**), and unlike a purchase created from a bank line that row means
nothing once the grouping is released -- so the undo has to remove it, and
something has to say which member the act created.

``created_version_id`` says two things in one column: its PRESENCE says this
act brought the subject into existence, and its VALUE says at which revision,
so a release can tell a row nobody has touched from one the owner has since
made their own.  See ``app/models/statement_match.py`` for the argument.

**Nullable with no backfill, and that is correct rather than lazy.**  NULL
already means what every existing member is -- a subject that existed before
its match named it -- so there is no historical row this column could be
computed for and none to guess at.  Measured on a production clone 2026-08-23:
production holds 0 statement matches at all.

**Two ops the autogenerate proposed are NOT here**, and dropping them would
have destroyed records: ``system.pre_origination_purge`` and
``system.loan_due_date_backfill`` are what earlier migrations kept of rows they
removed, they are backed by no model, and every autogenerate since has offered
to drop them.

Revision ID: 6376c2b8e6db
Revises: c7d31f9a45e8
Create Date: 2026-08-23 17:02:02.581544
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '6376c2b8e6db'
down_revision = 'c7d31f9a45e8'
branch_labels = None
depends_on = None

_TABLE = "statement_match_members"
_SCHEMA = "budget"
_IS_AN_APP_ROW = "ck_statement_match_members_created_is_an_app_row"
_POSITIVE = "ck_statement_match_members_created_version_positive"


def upgrade():
    """Apply forward migration."""
    op.add_column(
        _TABLE,
        sa.Column("created_version_id", sa.Integer(), nullable=True),
        schema=_SCHEMA,
    )
    # A CREATED subject is an app row, never a bank line: an act cannot bring
    # a line into existence -- an import does that, and the line is what the
    # act is ABOUT.
    op.create_check_constraint(
        _IS_AN_APP_ROW,
        _TABLE,
        "created_version_id IS NULL OR bank_statement_line_id IS NULL",
        schema=_SCHEMA,
    )
    # ``OptimisticLockMixin`` starts a counter at 1, so a created subject's
    # revision is positive -- the same predicate every ``version_id`` column
    # in this schema already carries.
    op.create_check_constraint(
        _POSITIVE,
        _TABLE,
        "created_version_id IS NULL OR created_version_id > 0",
        schema=_SCHEMA,
    )


def downgrade():
    """Revert migration.

    **Value-lossless only where the column is empty**, which it is for every
    row that existed before this migration: NULL is the state this column adds
    a name for.  A member written AFTER it -- a residual row's -- loses the
    fact that its act created it, and the release door then keeps that row
    instead of removing it.  That is the behaviour this step replaced, so the
    downgrade lands on it rather than on something new.
    """
    op.drop_constraint(_POSITIVE, _TABLE, schema=_SCHEMA, type_="check")
    op.drop_constraint(_IS_AN_APP_ROW, _TABLE, schema=_SCHEMA, type_="check")
    op.drop_column(_TABLE, "created_version_id", schema=_SCHEMA)
