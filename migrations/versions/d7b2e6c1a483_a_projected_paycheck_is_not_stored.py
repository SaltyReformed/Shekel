"""a paycheck's amount is its salary profile's, and it stores none

Revision ID: d7b2e6c1a483
Revises: b7a41e2c9d63
Create Date: 2026-09-02 21:40:00.000000

Plan step **X-au-d** of ``docs/audits/balance_architecture/README.md`` section 5
-- the DATA half of ruling **R-FI** for salary rows, and the second per-kind
cutover after ``c9a4e7b21d58`` took the transfer shadows.

## What it does

Every row in ``budget.transactions`` whose template is named by an ACTIVE
salary profile, and which the owner has not overridden, stops storing a figure
and DECLARES the relation that prices it::

    amount_source_id = ref.amount_sources('template')
    estimated_amount = NULL

``ck_transactions_amount_ownership`` is the BICONDITIONAL
``(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)``, so the two
writes are one statement and a half-write cannot commit.  After this, a ROW's
amount has exactly ONE producer -- ``income_service.SalaryPricing``, reached
through amount rule 2 -- where it had two: that producer at READ time and
``recurrence_engine._get_transaction_amount`` at GENERATION time, with nothing
reconciling the stored copy (finding **N-224**).  ``CLAUDE.md`` rule 14: a
value is derived through one walk, or stored in one place.  *The PROJECTION is
still spelled twice more, on the salary page and the cockpit, which this step
does not reach -- finding **N-443**.*

The relation is ``template`` and NOT a salary-specific member, which is ruling
**R-FK**: the column names the RELATION that prices the row, and whether that
definition is salary-linked is a property of the DEFINITION read live
(``template_amount_service.is_salary_linked_template``).  So archiving the
profile moves these rows from amount rule 2 to amount rule 3 with no write --
and ``routes/salary/profiles.delete_profile`` opens the template's price series
at its ``default_amount`` in the same unit of work, which is what leaves them
priceable (plan step X-au-a).

## Which rows, and why exactly those

  ==================================================  =======
  measurement (production clone, 2026-09-02,           value
  restored and upgraded to stamp ``b7a41e2c9d63``)
  ==================================================  =======
  salary rows in total                                63
  DECLARED here (``is_override = false``)             **59**
  of those, still Projected                           51
  of those, already settled                           8
  left OWN (``is_override = true``)                   4
  rows already carrying ``amount_source_id``          0
  transfer shadows among them                         0
  ==================================================  =======

**An OVERRIDDEN row keeps its figure**, because that flag is how the app has
always recorded "a human authored this one", and a human's figure is rule 1's
by definition.  The flag decides nothing about pricing AFTER this migration --
``amount_rule`` reads the declaration and finding **N-262** is why -- so a
period move that raises the flag on a derived paycheck leaves it derived, and
only a typed figure (``amount_ownership.state_own_amount`` at the edit doors)
takes ownership.  The flag is used HERE, once, to census the rows a human has
touched at all: it is the only record of that fact this migration can read, and
taking the conservative side of it can only leave a row storing a figure that
was already true.

**A SETTLED row is declared like any other** (developer ruling, 2026-09-02).
Its plan is a derivation like any other plan, and the figure it was planned at
when the money moved is already stored ONCE, in ``settled_amount`` on the
``derived`` settlement basis -- which is what that basis MEANS (plan step
X-au-c3: *"the app's resolution at the moment of settle is a point in time"*).
On all 8 settled rows here the two columns hold the same figure to the cent, so
this deletes a second copy rather than a fact.  The rejected alternative was to
declare only the Projected rows and let the settle FREEZE the plan: that needs
a writer at the settle AND one at the revert, and a paycheck reverted for
editing would otherwise keep a frozen figure that nothing ever recomputes
again -- finding **N-224** reborn on the revert path.

**A transfer shadow cannot be in this set**, and that is the schema's
statement rather than a measurement: ``ck_transactions_one_pricing_link`` makes
``template_id`` exclusive with ``transfer_id``, and this predicate selects on
the first.

## What it costs, measured rather than assumed

**It moves `$0.00` through every balance, and the comparison is stated because
the two sides run different code.**  BEFORE was measured on ``origin/dev``
against the pre-migration clone; AFTER on this branch against the
post-migration one; each side folded its own tree's cash walk
(``tests/manual/verify_balance_baseline.py``).  The two baselines differ in
NOTHING but the deleted ``amount_overrides`` field -- 9 accounts, 441 grid
cells, 6,174 daily points, and zero ADDED lines in the diff.  The 51 Projected
rows do not move because the READ-TIME REPAIR this step deletes already
superseded their column on every surface; the 8 settled rows do not move
because a settled row is worth what it RECORDED
(``row_valuation.settled_figure``), never its plan.

What DOES change is what a screen calls the estimate on the 8 settled rows: the
grid cell's ``(est: ...)`` caption and the full-edit popover's Estimated box
read the row's plan, which is now re-derived rather than frozen.  On the clone
that is 7 rows moving from ``$2,473.38`` to ``$2,483.19``, ``+$68.67`` in
total, and the cause is a ``salary.calibration_overrides`` row created
2026-08-28 -- after those 7 paychecks settled -- which carries no date bound and
so re-prices every past period.

## The downgrade, and the exact limit of its losslessness

It restores each row's figure and clears the declaration, keyed on the
SETTLEMENT rather than on the status alone:

  * a row settled on the ``derived`` basis restores from ``settled_amount``,
    and that is EXACT -- the basis means the settle recorded the app's own
    resolution, which is the plan this migration emptied;
  * every other row restores from its template's ``default_amount``.

**The second arm is not the figure the row held, and that is stated rather than
implied.**  A projected paycheck's figure is a paycheck CALCULATION, and a
migration may not import ``app`` to reproduce one -- importing the application
from a migration is what makes a schema change depend on code that may not
exist at the revision being replayed.  ``default_amount`` is the definition's
own stated scalar and the app's own documented fallback for exactly this
quantity: generation wrote it whenever the paycheck engine could not answer.
The restore is therefore VALUE-lossless only on the first arm.

It is BEHAVIOUR-lossless on the second, and that is the claim worth making
because it is the one that matters: the code this downgrade returns to
recomputes a Projected non-overridden salary row at read time
(``income_service.live_projected_net``, laid over the column by
``cash_ledger.display_amounts_by_id``), so it publishes the recomputation and
never the column, on the grid, in every HTMX fragment, in the balance fold and
in both edit prefills.  The restored scalar is a placeholder the first
``regenerate_for_template`` on that template overwrites exactly.

**Where it is neither**: a row settled on the ``corrected`` or ``purchases``
basis, whose plan at settle is recoverable from nothing this migration can
read.  ZERO such rows exist on production (all four corrected salary
settlements carry ``is_override`` and are therefore never declared), and
:func:`settled_rows_whose_plan_is_not_recoverable` names any that do rather
than leaving the operator to discover it -- it PRINTS rather than raises,
because the loss is a display caption on a settled row and refusing a rollback
over one would be worse than taking it.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d7b2e6c1a483"
down_revision = "b7a41e2c9d63"
branch_labels = None
depends_on = None

#: The declaration, resolved from the ref table by NAME.  A migration cannot
#: read ``app.ref_cache`` -- importing ``app`` from a migration is what makes a
#: schema change depend on application code that may not exist at the revision
#: being replayed -- so the id is looked up in the same statement that uses it.
#: The name is the ref row's natural key (``uq`` on ``ref.amount_sources.name``,
#: seeded by ``b3f7c2a9d514``), never an id literal.
_TEMPLATE_ID_SQL = "(SELECT id FROM ref.amount_sources WHERE name = 'template')"

#: The templates an ACTIVE salary profile prices.  ``is_active`` is the same
#: predicate ``template_amount_service.is_salary_linked_template`` reads, so
#: the population this declares is exactly the population amount rule 2 claims.
#: A template whose profile is ARCHIVED is priced by its own series instead and
#: belongs to plan step X-au-e, not here.
_SALARY_TEMPLATES_SQL = (
    "SELECT template_id FROM salary.salary_profiles "
    "WHERE is_active AND template_id IS NOT NULL"
)

#: Every template a salary profile has EVER named, active or not.  The
#: downgrade uses this wider set so a profile archived after the upgrade cannot
#: strand a declared row with no figure -- which would leave
#: ``b3f7c2a9d514``'s own downgrade guard refusing on rows this one should have
#: restored.
_EVER_SALARY_TEMPLATES_SQL = (
    "SELECT template_id FROM salary.salary_profiles "
    "WHERE template_id IS NOT NULL"
)

#: Amount rule 2 answers for an INCOME row and nothing else
#: (``income_service.salary_net_for``): a salary profile states a NET PAY, so an
#: expense row on a salary-linked definition is claimed by that rule and
#: answered by no producer -- finding **N-253**.  Declaring one would empty a
#: figure nothing can replace, so the predicate matches the population the rule
#: can PRICE rather than the population it CLAIMS.  Zero such rows exist on
#: production; the edit that would create one is refused at the template door
#: (``routes/templates/crud._changes_type_of_a_salary_template``).
_INCOME_ID_SQL = "(SELECT id FROM ref.transaction_types WHERE name = 'Income')"

_DECLARE_SQL = f"""
    UPDATE budget.transactions
       SET amount_source_id = {_TEMPLATE_ID_SQL},
           estimated_amount = NULL
     WHERE template_id IN ({_SALARY_TEMPLATES_SQL})
       AND transaction_type_id = {_INCOME_ID_SQL}
       AND is_override = FALSE
       AND amount_source_id IS NULL
"""

#: A settled row whose settle RECORDED the app's own resolution: its
#: ``settled_amount`` is the plan this migration emptied, to the cent.
_DERIVED_BASIS_SQL = (
    "(SELECT id FROM ref.settlement_bases WHERE name = 'derived')"
)

_RESTORE_FROM_RECORD_SQL = f"""
    UPDATE budget.transactions
       SET estimated_amount = settled_amount,
           amount_source_id = NULL
     WHERE template_id IN ({_EVER_SALARY_TEMPLATES_SQL})
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
       AND t.template_id IN ({_EVER_SALARY_TEMPLATES_SQL})
       AND t.amount_source_id = {_TEMPLATE_ID_SQL}
"""


def settled_rows_whose_plan_is_not_recoverable(bind) -> list:
    """Return the declared SETTLED rows whose plan at settle is stored nowhere.

    **The only non-DDL logic here, and it is module-level so a test can drive
    it** -- the pattern ``c9a4e7b21d58`` and ``b3f7c2a9d514`` both use, for the
    same reason: a guard nothing exercises is a guard nobody has seen work.

    A settled row records HOW its figure is known
    (:class:`app.enums.SettlementBasisEnum`).  On the ``derived`` basis the
    recorded figure IS the app's own resolution at the moment of settle, which
    is the plan this migration emptied, so the restore is exact.  On
    ``corrected`` the figure is a human's reading of a statement and on
    ``purchases`` it is the row's own entries; neither is the plan, and the
    plan is then recoverable from nothing a migration can read.

    Such a row restores from its template's ``default_amount`` like an
    unsettled one, which is a placeholder rather than the figure it held.  The
    cost is a display caption -- the grid's ``(est: ...)`` and the edit
    popover's Estimated box are the only readers of a settled row's plan, and
    every money reader answers from the settlement record -- so this REPORTS
    rather than refusing: blocking a rollback over a caption would be worse
    than taking it.

    Args:
        bind: A SQLAlchemy connection to probe.

    Returns:
        The affected transaction ids, ascending; empty when every declared
        settled row records the ``derived`` basis.  Zero on production
        (measured 2026-09-02: all four corrected salary settlements carry
        ``is_override`` and are never declared).
    """
    probe = sa.text(
        "SELECT t.id FROM budget.transactions AS t "
        f"WHERE t.template_id IN ({_EVER_SALARY_TEMPLATES_SQL}) "
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
    evaluated.  Reversed, every ``derived``-basis settled row silently comes
    back at the template's ``default_amount`` instead of the figure it
    recorded.  An adversarial review of this step found that ordering stated in
    a comment and graded nowhere;
    ``tests/test_models/test_amount_ownership.py`` now runs both arms in one
    call and asserts the figures rather than the counts.

    Args:
        bind: A SQLAlchemy connection to write through.

    Returns:
        ``(exact, placeholder)`` -- how many rows each arm restored.
    """
    exact = bind.execute(sa.text(_RESTORE_FROM_RECORD_SQL))
    placeholder = bind.execute(sa.text(_RESTORE_FROM_DEFINITION_SQL))
    return exact.rowcount, placeholder.rowcount


def upgrade():
    """Declare every non-overridden salary row derived and empty its figure.

    One statement.  It is idempotent by its ``amount_source_id IS NULL``
    predicate, so a re-run declares nothing twice, and it touches no row whose
    template an active salary profile does not name.
    """
    result = op.get_bind().execute(sa.text(_DECLARE_SQL))
    print(f"X-au-d: {result.rowcount} salary row(s) declared derived")


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
            f"X-au-d: {len(unrecoverable)} settled salary row(s) ({ids}) "
            "record a settlement basis other than 'derived', so the plan they "
            "held at settle is stored nowhere this migration can read. They "
            "restore from their template's default_amount, which is a "
            "placeholder. Only the grid's '(est: ...)' caption and the edit "
            "popover's Estimated box read a settled row's plan; every money "
            "reader answers from the settlement record, so no balance moves."
        )
    exact, placeholder = downgrade_rows(bind)
    print(
        f"X-au-d: {exact} salary row(s) restored exactly from their "
        f"settlement record, {placeholder} from their template's "
        "default_amount"
    )
