"""one salary profile per paycheck definition in each scenario

Revision ID: 9b2c5656eed9
Revises: 5641f7729b68
Create Date: 2026-09-23

Plan step **salary:X-av-1**, ruling **R-SAL63** ("One profile per paycheck",
developer 2026-09-23) as scoped by **R-SAL69** ("Per scenario", the same
evening), closing finding **N-294**::

    salary.salary_profiles               + uq_salary_profiles_scenario_template

**Within a scenario, a paycheck definition belongs to at most one salary
profile, active or not.**  The amount model's PRICING lookup finds a row's
profile within the row's scenario
(``income_service.SalaryPricing._profile_by_template``), and two profiles
there naming one ``budget.transaction_templates`` row gave its
``{template_id: profile}`` map two candidates for one key: it kept whichever
the query returned last, and nothing on screen said which profile priced the
paycheck.  No door produces the state -- ``routes/salary/profiles
.create_profile`` is the only constructor and mints a fresh template per
profile -- so the rule is structural: the state becomes unstorable for every
writer, a migration or a script included, rather than refused at one door.
An archived profile keeps its template and its scenario, so reactivating it
can never meet a second profile on that definition.

**Per scenario, not database-wide** (R-SAL69, revising R-SAL63's scope; the
database-wide form was not picked).  A template belongs to no scenario, while
a profile and every generated row do, so the rule lets a what-if scenario give
the same paycheck its own salary, and the pricing lookup never crosses
scenarios.  **Five doors that reach a paycheck through its TEMPLATE still
ignore scenario** and would be wrong in that state: the salary-link predicate
(``template_amount_service.is_salary_linked_template``, read by amount rule
2's classifier), ``salary_profile_service.archive_profile``'s freeze,
``delete_profile``'s template archive, ``update_profile``'s template rename
and restate, and ``salary_regeneration``, which regenerates into the read
pass's BASELINE scenario and restates the shared template's amount.  Every
reader that loads profiles by owner alone (the salary cockpit, the retirement
and investment dashboards, and the savings dashboard's current pay, which sums
them) would also count the what-if profile beside the baseline's.  Ledger row
**SAL-570** records them; none is reachable while no code creates a
non-baseline scenario.

**NULLs stay distinct** (PostgreSQL's default, stated so nobody adds
``NULLS NOT DISTINCT``): ``template_id``'s key is ``ON DELETE SET NULL``, so
a template's hard delete leaves its profile with no template, and several
profiles may have lost theirs.

**The upgrade REFUSES while existing data holds the state**, naming each
scenario, shared template and its profiles, before writing anything.  It does
not choose a winner: which profile a definition belongs to is the owner's
knowledge, and a migration that guessed would re-price a paycheck row
silently.  The developer's production data holds none (one profile, measured
on a 2026-09-23 clone).  ``$0.00``: no row changes, and a constraint that
holds on every stored row prices nothing differently.

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
_CONSTRAINT = "uq_salary_profiles_scenario_template"

#: Every (scenario, template) two or more profiles name, with those profiles.
_SHARED_TEMPLATES = sa.text(
    "SELECT scenario_id, template_id, "
    "string_agg(id::text || ' (' || name || ')', ', ' ORDER BY id) "
    "FROM salary.salary_profiles "
    "WHERE template_id IS NOT NULL "
    "GROUP BY scenario_id, template_id HAVING count(*) > 1 "
    "ORDER BY scenario_id, template_id"
)


def upgrade():
    """Refuse on a shared template, else add ``uq_salary_profiles_scenario_template``.

    Raises:
        RuntimeError: When, in any scenario, a template is named by two or
            more profiles, listing each one and its profiles; nothing is
            written.
    """
    shared = op.get_bind().execute(_SHARED_TEMPLATES).fetchall()
    if shared:
        listing = "; ".join(
            f"scenario {scenario_id}, template {template_id}: "
            f"profiles {profiles}"
            for scenario_id, template_id, profiles in shared
        )
        raise RuntimeError(
            "salary profiles in one scenario share a paycheck definition, "
            f"which rulings R-SAL63 and R-SAL69 make unstorable: {listing}.  "
            "Decide which profile each definition belongs to and point the "
            "others elsewhere (or at no template), then upgrade again."
        )
    op.create_unique_constraint(
        _CONSTRAINT, "salary_profiles", ["scenario_id", "template_id"],
        schema=_SCHEMA,
    )


def downgrade():
    """Drop ``uq_salary_profiles_scenario_template``; no row changes."""
    op.drop_constraint(
        _CONSTRAINT, "salary_profiles", schema=_SCHEMA, type_="unique",
    )
