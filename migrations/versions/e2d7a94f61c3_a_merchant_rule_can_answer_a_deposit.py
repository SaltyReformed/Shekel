"""A merchant rule can answer a DEPOSIT: merchant_rules.income_category_id

Plan step ``bank_import:X-gj-2a``, ruling **bank_import:R-HT(a)**.  The fifth
answer a standing merchant rule may give: *a deposit from this signature is
income under that category*.

**Three DDL acts, and the second is the one worth reading.**  The column is
additive and nullable, so every stored rule stays exactly the answer it was.
What is NOT additive is ``ck_merchant_rules_one_answer``: the CHECK enumerates
which COMBINATIONS of the answer columns are legal, so a new column that no arm
mentions is a column every existing arm silently permits alongside itself.  A
row carrying both ``template_id`` and ``income_category_id`` would then be two
answers to one question -- and ``RuleAnswer.of`` reads the container arm first,
so such a row would file SPENDING under an answer the owner stated about
deposits.  The constraint is therefore DROPPED and re-created with the new term
on every arm, not just on its own.

**The third is the owner key.**  ``income_category_id`` is reached through a
composite foreign key against ``categories(id, user_id)`` rather than a bare
``category_id`` FK, which is the construction its twin
``fk_merchant_rules_category_owner`` already uses: a single-column key is
satisfied perfectly well by another owner's category, and that is the IDOR
every create door in this project probes for by hand.  Here it is unwritable
instead.  ``ON DELETE CASCADE`` for the reason the twin cascades -- an answer
naming a row that no longer exists is not an answer -- and
``archive_helpers.category_has_usage`` learned this column in the same commit,
so the cascade cannot fire behind a door calling the category unused.

**The downgrade is value-lossless only where no rule uses the new answer.**  It
cannot be otherwise: the column is the whole of answer (5), so a stored income
rule has nowhere to go in the old shape.  It is therefore REFUSED rather than
silently dropped -- a downgrade that deleted money decisions and reported
success is the failure mode this project files findings about.  An operator who
means it deletes the rows first, at which point the downgrade is exact.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e2d7a94f61c3"
down_revision = "c9f4b1e78d02"
branch_labels = None
depends_on = None


#: The CHECK as it stood before this revision: four answers over three columns
#: plus the flag.  Frozen as a literal rather than imported, for the reason the
#: revision above freezes its own predecessor's function body: a constant that
#: moves with the model would let a downgrade restore the NEW rule while
#: claiming to have reverted.
_ONE_ANSWER_BEFORE = (
    "(template_id IS NOT NULL AND envelope_name IS NULL "
    "AND category_id IS NULL AND NOT never_a_purchase) "
    "OR (template_id IS NULL AND envelope_name IS NOT NULL "
    "AND category_id IS NOT NULL AND NOT never_a_purchase) "
    "OR (template_id IS NULL AND envelope_name IS NULL "
    "AND category_id IS NULL)"
)

#: The CHECK this revision installs: five answers, and every pre-existing arm
#: gained an ``income_category_id IS NULL`` term so no row can carry two.
_ONE_ANSWER_AFTER = (
    "(template_id IS NOT NULL AND envelope_name IS NULL "
    "AND category_id IS NULL AND income_category_id IS NULL "
    "AND NOT never_a_purchase) "
    "OR (template_id IS NULL AND envelope_name IS NOT NULL "
    "AND category_id IS NOT NULL AND income_category_id IS NULL "
    "AND NOT never_a_purchase) "
    "OR (template_id IS NULL AND envelope_name IS NULL "
    "AND category_id IS NULL AND income_category_id IS NOT NULL "
    "AND NOT never_a_purchase) "
    "OR (template_id IS NULL AND envelope_name IS NULL "
    "AND category_id IS NULL AND income_category_id IS NULL)"
)


def upgrade():
    """Add the income answer, widen the CHECK, key the category to its owner."""
    op.add_column(
        "merchant_rules",
        sa.Column("income_category_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.drop_constraint(
        "ck_merchant_rules_one_answer", "merchant_rules",
        schema="budget", type_="check",
    )
    op.create_check_constraint(
        "ck_merchant_rules_one_answer", "merchant_rules", _ONE_ANSWER_AFTER,
        schema="budget",
    )
    op.create_foreign_key(
        "fk_merchant_rules_income_category_owner",
        "merchant_rules", "categories",
        ["income_category_id", "user_id"], ["id", "user_id"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
    )


def downgrade():
    """Remove the income answer, refusing while any rule still holds one.

    Raises:
        RuntimeError: When a stored rule carries answer (5).  The column IS
            that answer, so dropping it would delete a money decision the owner
            stated and report success.  Deleting those rows is an act an
            operator performs deliberately, not one a schema migration performs
            on their behalf.
    """
    held = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM budget.merchant_rules "
            "WHERE income_category_id IS NOT NULL"
        )
    ).scalar()
    if held:
        raise RuntimeError(
            f"{held} standing merchant rule(s) answer a deposit with an income "
            f"category, and this revision's column is the whole of that "
            f"answer -- dropping it would delete those decisions silently.  "
            f"Delete or restate them first, then downgrade."
        )
    op.drop_constraint(
        "fk_merchant_rules_income_category_owner", "merchant_rules",
        schema="budget", type_="foreignkey",
    )
    op.drop_constraint(
        "ck_merchant_rules_one_answer", "merchant_rules",
        schema="budget", type_="check",
    )
    op.create_check_constraint(
        "ck_merchant_rules_one_answer", "merchant_rules", _ONE_ANSWER_BEFORE,
        schema="budget",
    )
    op.drop_column(
        "merchant_rules", "income_category_id", schema="budget",
    )
