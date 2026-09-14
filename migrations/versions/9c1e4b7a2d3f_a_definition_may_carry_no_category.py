"""a definition may carry no category

Revision ID: 9c1e4b7a2d3f
Revises: 542c61e48ee8
Create Date: 2026-09-13 23:10:00.000000

Plan step **balance:X-bi-7b** (leaf 7b-1) of
``docs/audits/balance_architecture/README.md`` section 5, under ruling
**R-BAL24** (developer, 2026-09-12; re-ruled 2026-09-13)::

    budget.transaction_templates.category_id  NOT NULL  ->  NULL

**Schema only.  No row is written, no figure moves, and the upgrade is
total.**  Every plan item has exactly one definition (**R-BAL20**), so the row
a bank line's money requires -- minted by
``statement_match._uncategorized.mint_uncategorized`` and written link-less
until this step -- is born as a rule-less DEFINITION plus its placed row from
here on.  Two of that door's three callers state no category, and that is a
FACT rather than a gap: the app does not know what the money was, and saying
so is what books its counter leg to the per-owner Uncategorized LEDGER ACCOUNT
(ruling **R-FN**, ``posting_service._settled_target``) so it can be
categorised later instead of misfiled now.  A definition for such a row must
therefore be able to say the same thing, which is what the column's NOT NULL
forbade.  Every other producer of a definition -- the template form, the grid's
two create doors, the salary profile's own template (``routes/salary/
profiles.py``, which mints its Salary category) -- states a category and is
unchanged.

**Rejected** (10.6-D of ``docs/design/from_scratch_architecture.md``): a real
Uncategorized CATEGORY for such a row (the opposite of R-FN's mechanism -- the
leg would book through that category's ledger account and the spending
reports would gain a category nobody chose); and bank-minted rows staying
link-less as a third meaning of ``template_id IS NULL`` (the cutover leaf's
``= 1`` CHECK could never bind).

**The downgrade re-adds ``NOT NULL`` and REFUSES while a category-less
definition exists**, naming the count, rather than inventing a category for
them or deleting them -- the shape ``e2d7a94f61c3`` (a rule answering a
deposit) and ``6fc77e86d76f`` (an owner with no era) already take for a state
the old schema cannot hold.  An orderly downgrade
never meets the refusal: the family's cutover leaf (``X-bi-7d``) folds
one-row definitions back onto their rows before this revision is reached,
and until it ships the only writer of a category-less definition is the bank
door above.  Measured on the 2026-09-12 production restore: 40 of 40
definitions carry a category and a rule, so on that data both directions are
no-ops on rows.

``down_revision`` was ``bf50951a3599`` (the head of the tree this leaf was
built on, ``eecef63d`` = X-bi-7a) and was re-pointed at ``3ec5291ca4e2``
(pay_calendar C17-d-2) when ``origin/dev`` was merged into the branch, so the
tree holds one head -- the rule every in-flight migration follows; the
coordinator re-points again if another migration lands before the cut.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9c1e4b7a2d3f"
down_revision = "542c61e48ee8"
branch_labels = None
depends_on = None


_CATEGORY_LESS_DEFINITIONS_SQL = """
    SELECT COUNT(*) FROM budget.transaction_templates WHERE category_id IS NULL
"""


def _refuse_category_less_definitions(bind):
    """Stop before the ``ALTER TABLE`` that would fail on such a row anyway.

    PostgreSQL would refuse ``SET NOT NULL`` itself, but as a bare
    ``NotNullViolation`` that names the column and not why the rows are there;
    this names the count and the repair.
    """
    count = bind.execute(sa.text(_CATEGORY_LESS_DEFINITIONS_SQL)).scalar()
    if count:
        raise RuntimeError(
            f"{count} budget.transaction_templates row(s) carry no category, "
            f"which the schema this downgrade restores cannot hold.  They are "
            f"the definitions bank import minted for money it could not "
            f"categorise (plan step balance:X-bi-7b).  Either run the family's "
            f"cutover downgrade first, which folds each one-row definition back "
            f"onto its row, or categorise them by hand; then re-run."
        )


def upgrade():
    """Let a definition carry no category."""
    op.alter_column(
        "transaction_templates", "category_id",
        existing_type=sa.Integer(), nullable=True, schema="budget",
    )


def downgrade():
    """Restore ``NOT NULL``, refusing while a category-less definition exists."""
    _refuse_category_less_definitions(op.get_bind())
    op.alter_column(
        "transaction_templates", "category_id",
        existing_type=sa.Integer(), nullable=False, schema="budget",
    )
