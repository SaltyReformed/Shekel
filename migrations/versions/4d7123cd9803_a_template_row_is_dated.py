"""a template row is dated

Revision ID: 4d7123cd9803
Revises: b7e4c1f38a20
Create Date: 2026-09-11 13:30:00.000000

Plan step **balance:X-bv-2** of ``docs/audits/balance_architecture/README.md``
section 5, closing finding **BAL-463** under ruling **R-BAL6** (developer,
2026-09-07).  One CHECK on ``budget.transactions``::

    template_id IS NULL OR due_date IS NOT NULL

A row that names a recurring definition carries the day it is due.  **Moves no
money and touches no row**: PostgreSQL validates the predicate over the table
as part of the ``ALTER TABLE`` and the statement either binds or refuses whole.

**Why the row must be dated.**  Amount rule 3 prices a derived row from its
definition's effective-dated series *as of the row's own due date*
(``_amount_source._stated_amount``), and ruling **D5** forbids substituting
the pay period's bounds -- so a template-linked row with no date is
unpriceable the moment its figure is handed back to the definition, which is
what ``recurrence_engine.resolve_conflicts``' "use the template's amount"
does.  ``AmountUnresolvable`` has no handler on the grid, the dashboard or
the companion, so one such row was the whole screen.  Plan step **X-bv**
(``66ff070b``) closed the STRAND at its only producer: a carried-forward
leftover now carries ``compute_due_date``'s answer.  This constraint is the
storage tier saying the same thing, so the state is unrepresentable rather
than merely no longer produced.

**Why TWO terms and not three.**  A first form was staged and withdrawn
(R-BAL6 records it): ``template_id IS NULL OR amount_source_id IS NULL OR
due_date IS NOT NULL``, forbidding only the *derived* undated row.  That
ADMITS the undated leftover and refuses the TRANSITION -- the chooser's
declare, which writes ``amount_source_id`` and nothing else -- so it would
have turned a button press into an ``IntegrityError`` and then REQUIRED a
guard in ``resolve_conflicts`` to avoid it.  The two-term form is invariant
under that declare, because the declare touches neither column it names, and
so it needs no guard: no writer in ``app/`` sets ``template_id`` on an
existing row (AST census 2026-09-11, ``scratchpad/census.py``: nine
``Transaction(...)`` constructions, zero ``.template_id =`` assignments), and
every constructor that sets it is dated -- the two recurrence-engine paths
splat ``DerivedRowFields`` whose ``due_date`` is ``compute_due_date``'s, and
the carry-forward leftover is X-bv's.  The one door that could CLEAR the date
on a linked row, the transaction PATCH, is refused a step earlier by
``routes/transactions/_gates._reject_generated_due_date_edit`` with a
designed 400; that gate also refuses MOVING the date, which no CHECK can
express, so it stays, and this is its backstop for a writer that is not the
application.

**What this makes structurally unnecessary.**  The ``due_date IS NULL`` arm
of ``c8f3a5d2e714.rows_the_declare_would_strand`` -- the strand guard
**BAL-463** is about -- asks a question the schema now answers, and the next
per-kind cutover inherits nothing it has to remember to copy.  The other two
arms (an EMPTY series, a DISAGREEING figure) are not this constraint's and
stay that migration's.

**Nothing is backfilled, and a violating row REFUSES the upgrade rather than
being dated here.**  The date has ONE producer, ``compute_due_date``, and it
is application code (``CLAUDE.md`` rule 14: a migration re-spelling it would
be a second walk).  Measured rather than assumed, on the databases as they
stand::

    SELECT id, template_id, pay_period_id, is_override, is_deleted
      FROM budget.transactions
     WHERE template_id IS NOT NULL AND due_date IS NULL
     ORDER BY id;

Production 2026-09-11 (``shekel-prod-db``, read-only, stamp
``a1c7e5d20f43``): **0 of 626** template-linked rows, live or soft-deleted.
The developer's dev database the same day: **0 of 624**.  Both were 0 on
2026-09-06 as well (X-bv's own measurement).  Production still runs the
pre-X-bv carry-forward producer until the release carrying both steps
deploys, so the window in which it could write one is real and is the
carry-forward door alone; should it, the ``ALTER TABLE`` fails with
``CheckViolation`` naming this constraint, nothing is changed, and the SELECT
above names the row for the operator to date through the application.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "4d7123cd9803"
down_revision = "b7e4c1f38a20"
branch_labels = None
depends_on = None


def upgrade():
    """Bind the CHECK; PostgreSQL validates every existing row as part of it."""
    op.create_check_constraint(
        "ck_transactions_template_row_needs_due_date", "transactions",
        "template_id IS NULL OR due_date IS NOT NULL",
        schema="budget",
    )


def downgrade():
    """Drop the CHECK.  Lossless: it stores nothing and no row is touched."""
    op.drop_constraint(
        "ck_transactions_template_row_needs_due_date", "transactions",
        type_="check", schema="budget",
    )
