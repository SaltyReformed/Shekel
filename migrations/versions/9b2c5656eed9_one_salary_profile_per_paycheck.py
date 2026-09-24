"""one salary profile per paycheck definition

Revision ID: 9b2c5656eed9
Revises: 5641f7729b68
Create Date: 2026-09-23

Plan step **salary:X-av-1**, ruling **R-SAL63** ("One profile per paycheck",
developer 2026-09-23), closing finding **N-294**::

    salary.salary_profiles               + uq_salary_profiles_template_id

**A paycheck definition belongs to at most one salary profile, active or
not.**  Two profiles naming one ``budget.transaction_templates`` row left the
amount model's ``{template_id: profile}`` map
(``income_service.SalaryPricing._profile_by_template``) holding two answers for
one paycheck row, and the row was priced by whichever profile the query
returned last, with nothing on screen saying which.  No door produces the state
-- ``routes/salary/profiles.create_profile`` is the only constructor and mints a
fresh template per profile -- so the rule is structural: the state becomes
unstorable for every writer, a migration or a script included, rather than
refused at one door.  The rule covers the whole table, not active profiles
alone (the partial-index alternative, refused): an archived profile keeps its
definition to itself, so reactivating it can never meet a second profile there.

**NULLs stay distinct** (PostgreSQL's default, stated so nobody adds
``NULLS NOT DISTINCT``): ``template_id``'s key is ``ON DELETE SET NULL``, so
a template's hard delete leaves its profile with no template, and several
profiles may have lost theirs.

**The upgrade REFUSES while existing data holds the state**, naming each shared
template and its profiles, before writing anything.  It does not choose a
winner: which profile a definition belongs to is the owner's knowledge, and a
migration that guessed would re-price a paycheck row silently.  The
developer's production data holds none (one profile, measured on a
2026-09-23 clone).  ``$0.00``: no row changes, and a constraint that holds on
every stored row prices nothing differently.

**The downgrade drops the constraint.**  It removes a rule rather than data, so
nothing is lost; the older schema simply stops refusing the state again.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "9b2c5656eed9"
down_revision = "5641f7729b68"
branch_labels = None
depends_on = None


_SCHEMA = "salary"
_CONSTRAINT = "uq_salary_profiles_template_id"

#: Every template two or more profiles name, with those profiles.
_SHARED_TEMPLATES = sa.text(
    "SELECT template_id, "
    "string_agg(id::text || ' (' || name || ')', ', ' ORDER BY id) "
    "FROM salary.salary_profiles "
    "WHERE template_id IS NOT NULL "
    "GROUP BY template_id HAVING count(*) > 1 "
    "ORDER BY template_id"
)


def upgrade():
    """Refuse on a shared template, else add ``uq_salary_profiles_template_id``.

    Raises:
        RuntimeError: When any template is named by two or more profiles,
            listing each one and its profiles; nothing is written.
    """
    shared = op.get_bind().execute(_SHARED_TEMPLATES).fetchall()
    if shared:
        listing = "; ".join(
            f"template {template_id}: profiles {profiles}"
            for template_id, profiles in shared
        )
        raise RuntimeError(
            "salary profiles share a paycheck definition, which ruling "
            f"R-SAL63 makes unstorable: {listing}.  Decide which profile each "
            "definition belongs to and point the others elsewhere (or at no "
            "template), then upgrade again."
        )
    op.create_unique_constraint(
        _CONSTRAINT, "salary_profiles", ["template_id"], schema=_SCHEMA,
    )


def downgrade():
    """Drop ``uq_salary_profiles_template_id``; no row changes."""
    op.drop_constraint(
        _CONSTRAINT, "salary_profiles", schema=_SCHEMA, type_="unique",
    )
