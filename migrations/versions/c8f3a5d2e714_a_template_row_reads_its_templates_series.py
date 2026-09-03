"""a template row reads its template's series, and it stores no figure

Revision ID: c8f3a5d2e714
Revises: d4a92f6b13c8
Create Date: 2026-09-03 14:40:00.000000

Plan step **X-au-e** of ``docs/audits/balance_architecture/README.md`` section 5
-- the DATA half of ruling **R-FI** for ordinary recurring rows, and the third
per-kind cutover after ``c9a4e7b21d58`` (transfer shadows) and ``d7b2e6c1a483``
(salary).

Review: developer-ruled -- **R-JB** fixes the population for the CLASS (every
non-override row of the kind, settled ones included) and **R-JC** fixes the
downgrade for the CLASS (exact from the settlement record, else the
definition's scalar), both 2026-09-02; the step's build was authorised
2026-09-03.  Destructive in the sense that matters: it empties a populated
money column on 525 rows.  Every one of those figures is reproduced by the
downgrade below, exactly, on the data measured.

## What it does

Every row in ``budget.transactions`` that carries a template, that the owner
has not overridden, and that still owns its figure stops storing one and
DECLARES the relation that prices it::

    amount_source_id = ref.amount_sources('template')
    estimated_amount = NULL

``ck_transactions_amount_ownership`` is the BICONDITIONAL
``(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)``, so the two
writes are one statement and a half-write cannot commit.  After this, an
ordinary recurring row's amount has exactly ONE producer -- its definition's
effective-dated series, read by ``template_amount_service.amount_as_of`` on the
row's OWN due date through amount rule 3 -- where it had two: that series at
READ time and ``recurrence_engine._amounts`` at GENERATION time, with nothing
reconciling the stored copy.  ``CLAUDE.md`` rule 14: a value is derived through
one walk, or stored in one place.

## Which rows, and why exactly those

Measured on a production clone restored 2026-09-03 from ``a4c6f1d92b73`` and
upgraded to ``d4a92f6b13c8``:

  ====================================================  =======
  measurement                                            value
  ====================================================  =======
  rows DECLARED here                                    **525**
  of those, Projected                                   455
  of those, settled (Paid 55 / Received 11)             66
  of those, Cancelled / Credit                          3 / 1
  left OWN because the owner overrode them              40
  excluded because an ACTIVE salary profile prices them 0
  rows with no ``due_date`` (would be unpriceable)      **0**
  rows whose template states no price at all            **0**
  transfer shadows among them                           0
  ====================================================  =======

**An OVERRIDDEN row keeps its figure**, for the reason ``d7b2e6c1a483`` states:
the flag is the only record this migration can read of a human having authored
a figure, and taking the conservative side of it can only leave a row storing a
figure that was already true.

**A SETTLED row is declared like any other** (**R-JB**).  Its plan is a
derivation like any other plan, and what it was PLANNED at when the money moved
is already stored once, in ``settled_amount`` on the ``derived`` settlement
basis.  On all 46 such rows here the two columns hold the same figure to the
cent.

**A row an ACTIVE salary profile prices is EXCLUDED, and that is finding
N-253 rather than tidiness.**  Amount rule 2 claims every row of a
salary-linked definition and ``income_service.salary_net_for`` answers for an
INCOME row only, so declaring an expense row on such a definition would empty a
figure no producer can replace.  ``d7b2e6c1a483`` owns that population and
already declared the Income half of it; what it deliberately left OWN must stay
OWN.  Zero such rows exist.  A template whose profile is ARCHIVED is priced by
its own series and IS declared here, which is the boundary ``d7b2e6c1a483``'s
own predicate comment draws.

**A transfer shadow cannot be in this set**, and that is the schema's statement
rather than a measurement: ``ck_transactions_one_pricing_link`` makes
``template_id`` exclusive with ``transfer_id``, and this predicate selects on
the first.

## What it costs: `$0.00`, measured row by row rather than argued

Every one of the 525 rows already stores exactly what its definition's series
answers on its own due date: 0 differing, `$0.00` net and `$0.00` gross,
computed on the clone above by resolving each row's series the way
``template_amount_service.amount_as_of`` does (newest version at or before the
due date, holding flat before the earliest).  So the cutover deletes a COPY
rather than a fact, and no screen and no balance can move by the deletion
itself.

## The downgrade, and the exact limit of its losslessness

Ruling **R-JC**, the same two arms ``d7b2e6c1a483`` uses, in the same order:

  * a row settled on the ``derived`` basis restores from ``settled_amount``,
    which IS the plan this migration emptied, so the restore is EXACT;
  * every other row restores from its template's ``default_amount``, the
    definition's own stated scalar and the app's own fallback for this
    quantity.

**On the measured data both arms are exact**, and the second one's exactness is
a property of this data rather than a guarantee: only 7 of the 525 rows hold a
figure that differs from their template's current ``default_amount`` at all
(three Geico rows, four Apple Music), and every one of those 7 is on the
``derived`` basis, so the FIRST arm restores them and the placeholder arm never
sees them.  Reverse the two statements and those 7 come back at today's scalar
instead of the figure they recorded -- which is why the order is asserted by a
test rather than stated in a comment.

**Where it is neither exact nor guaranteed**: a row settled on the
``corrected`` or ``purchases`` basis, whose plan at settle is recoverable from
nothing this migration can read.  **20 such rows exist here**, all on the
``purchases`` basis, where ``d7b2e6c1a483`` had none -- so this arm is
exercised in practice rather than theoretically.
:func:`settled_rows_whose_plan_is_not_recoverable` NAMES them rather than
leaving the operator to discover them, and PRINTS rather than raising: only the
grid's ``(est: ...)`` caption and the edit popover's Estimated box read a
settled row's plan, every money reader answering from the settlement record, so
refusing a rollback over a display caption would be worse than taking it.

**The salary rows ``d7b2e6c1a483`` declared are excluded from BOTH arms.**
They are that migration's to restore, and its downgrade runs after this one; a
row on a template a salary profile has EVER named is therefore left declared
here.  The exclusion is the EVER set rather than the ACTIVE one for the reason
that migration gives: a profile archived between the two upgrades would
otherwise strand a row each downgrade thinks the other owns.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c8f3a5d2e714"
down_revision = "d4a92f6b13c8"
branch_labels = None
depends_on = None

#: The declaration, resolved from the ref table by NAME.  A migration cannot
#: read ``app.ref_cache`` -- importing ``app`` from a migration is what makes a
#: schema change depend on application code that may not exist at the revision
#: being replayed -- so the id is looked up in the same statement that uses it.
_TEMPLATE_ID_SQL = "(SELECT id FROM ref.amount_sources WHERE name = 'template')"

#: The templates an ACTIVE salary profile prices, whose rows amount rule 2
#: claims and this migration must not touch (finding **N-253**).  The same
#: predicate ``template_amount_service.is_salary_linked_template`` reads.
_ACTIVE_SALARY_TEMPLATES_SQL = (
    "SELECT template_id FROM salary.salary_profiles "
    "WHERE is_active AND template_id IS NOT NULL"
)

#: Every template a salary profile has EVER named.  The downgrade excludes
#: this wider set because those rows belong to ``d7b2e6c1a483``'s downgrade,
#: which runs after this one.
_EVER_SALARY_TEMPLATES_SQL = (
    "SELECT template_id FROM salary.salary_profiles "
    "WHERE template_id IS NOT NULL"
)

#: A settled row whose settle RECORDED the app's own resolution: its
#: ``settled_amount`` is the plan this migration emptied, to the cent.
_DERIVED_BASIS_SQL = (
    "(SELECT id FROM ref.settlement_bases WHERE name = 'derived')"
)

_DECLARE_SQL = f"""
    UPDATE budget.transactions
       SET amount_source_id = {_TEMPLATE_ID_SQL},
           estimated_amount = NULL
     WHERE template_id IS NOT NULL
       AND template_id NOT IN ({_ACTIVE_SALARY_TEMPLATES_SQL})
       AND is_override = FALSE
       AND amount_source_id IS NULL
"""

_RESTORE_FROM_RECORD_SQL = f"""
    UPDATE budget.transactions
       SET estimated_amount = settled_amount,
           amount_source_id = NULL
     WHERE template_id IS NOT NULL
       AND template_id NOT IN ({_EVER_SALARY_TEMPLATES_SQL})
       AND amount_source_id = {_TEMPLATE_ID_SQL}
       AND settled_basis_id = {_DERIVED_BASIS_SQL}
       AND settled_amount IS NOT NULL
"""

_RESTORE_FROM_DEFINITION_SQL = f"""
    UPDATE budget.transactions AS t
       SET estimated_amount = tt.default_amount,
           amount_source_id = NULL
      FROM budget.transaction_templates AS tt
     WHERE tt.id = t.template_id
       AND t.template_id NOT IN ({_EVER_SALARY_TEMPLATES_SQL})
       AND t.amount_source_id = {_TEMPLATE_ID_SQL}
"""


def settled_rows_whose_plan_is_not_recoverable(bind) -> list:
    """Return the declared SETTLED rows whose plan at settle is stored nowhere.

    **Module-level so a test can drive it** -- the pattern ``c9a4e7b21d58``,
    ``b3f7c2a9d514`` and ``d7b2e6c1a483`` all use, for the same reason: a guard
    nothing exercises is a guard nobody has seen work.

    A settled row records HOW its figure is known
    (:class:`app.enums.SettlementBasisEnum`).  On the ``derived`` basis the
    recorded figure IS the app's own resolution at the moment of settle, which
    is the plan this migration emptied, so the restore is exact.  On
    ``corrected`` the figure is a human's reading of a statement and on
    ``purchases`` it is the row's own entries; neither is the plan, and the
    plan is then recoverable from nothing a migration can read.

    Such a row restores from its template's ``default_amount`` like an
    unsettled one.  **On the 2026-09-03 production clone that scalar happens to
    equal the figure every one of them held**, so the restore is exact in value
    -- but it is not exact by construction, which is the difference this
    function exists to report.

    Args:
        bind: A SQLAlchemy connection to probe.

    Returns:
        The affected transaction ids, ascending; empty when every declared
        settled row records the ``derived`` basis.  **20 on the 2026-09-03
        production clone**, all on the ``purchases`` basis.
    """
    probe = sa.text(
        "SELECT t.id FROM budget.transactions AS t "
        "WHERE t.template_id IS NOT NULL "
        f"AND t.template_id NOT IN ({_EVER_SALARY_TEMPLATES_SQL}) "
        f"AND t.amount_source_id = {_TEMPLATE_ID_SQL} "
        "AND t.settled_basis_id IS NOT NULL "
        f"AND t.settled_basis_id <> {_DERIVED_BASIS_SQL} "
        "ORDER BY t.id"
    )
    return [row[0] for row in bind.execute(probe)]


def downgrade_rows(bind) -> tuple:
    """Restore every declared row's figure and clear its declaration.

    **Module-level so a test can drive it, and the ORDER of the two statements
    is the reason it is worth driving**: the EXACT restore runs first, so a row
    it covers is no longer declared when the placeholder restore's predicate is
    evaluated.  Reversed, every ``derived``-basis settled row comes back at its
    template's ``default_amount`` instead of the figure it recorded -- which on
    the measured clone is all 7 rows where the two differ at all.

    Args:
        bind: A SQLAlchemy connection to write through.

    Returns:
        ``(exact, placeholder)`` -- how many rows each arm restored.
    """
    exact = bind.execute(sa.text(_RESTORE_FROM_RECORD_SQL))
    placeholder = bind.execute(sa.text(_RESTORE_FROM_DEFINITION_SQL))
    return exact.rowcount, placeholder.rowcount


def upgrade():
    """Declare every non-overridden template row derived and empty its figure.

    One statement.  It is idempotent by its ``amount_source_id IS NULL``
    predicate, so a re-run declares nothing twice, and it touches no row an
    active salary profile prices.
    """
    result = op.get_bind().execute(sa.text(_DECLARE_SQL))
    print(f"X-au-e: {result.rowcount} template row(s) declared derived")


def downgrade():
    """Restore each declared row's figure and clear the declaration.

    Two statements, in this order: the exact restore first, so a row it covers
    is no longer declared when the placeholder restore runs and cannot be
    written twice.
    """
    bind = op.get_bind()
    unrecoverable = settled_rows_whose_plan_is_not_recoverable(bind)
    if unrecoverable:
        ids = ", ".join(str(row_id) for row_id in unrecoverable)
        print(
            f"X-au-e: {len(unrecoverable)} settled template row(s) ({ids}) "
            "record a settlement basis other than 'derived', so the plan they "
            "held at settle is stored nowhere this migration can read. They "
            "restore from their template's default_amount. Only the grid's "
            "'(est: ...)' caption and the edit popover's Estimated box read a "
            "settled row's plan; every money reader answers from the "
            "settlement record, so no balance moves."
        )
    exact, placeholder = downgrade_rows(bind)
    print(
        f"X-au-e: {exact} template row(s) restored exactly from their "
        f"settlement record, {placeholder} from their template's "
        "default_amount"
    )
