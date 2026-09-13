"""a row of a definition is dated, on both row tables

Revision ID: 4d7123cd9803
Revises: 6fc77e86d76f
Create Date: 2026-09-11 13:30:00.000000

Plan step **balance:X-bv-2** of ``docs/audits/balance_architecture/README.md``
section 5, closing finding **BAL-463** under ruling **R-BAL17** (developer,
2026-09-11).  One CHECK on each row table::

    budget.transactions:  template_id IS NULL OR due_date IS NOT NULL
    budget.transfers:     transfer_template_id IS NULL OR due_date IS NOT NULL

A row that names a recurring definition carries the day it is due.  **Moves no
money and touches no row**: PostgreSQL validates each predicate over its table
as part of the ``ALTER TABLE`` and the statement either binds or refuses whole.

**Why the row must be dated.**  Amount rule 3 prices a derived row from its
definition's effective-dated series *as of the row's own due date*
(``cash_ledger._definition_cash._stated_amount``), and ruling **D5** forbids
substituting the pay period's bounds -- so a linked row with no date is
unpriceable the moment its figure is handed back to the definition, which is
what ``recurrence_engine.resolve_conflicts``' "use the template's amount"
does.  ``AmountUnresolvable`` has no handler on the grid, the dashboard or
the companion, so one such row was the whole screen.  Plan step **X-bv**
(``66ff070b``) closed the STRAND at its only producer: a carried-forward
leftover now carries ``compute_due_date``'s answer.  These constraints are the
storage tier saying the same thing, so the state is unrepresentable rather
than merely no longer produced -- and the refusal arm ``_stated_amount`` kept
for it is deleted in the same step rather than left as a fence.

**Why TWO terms and not three.**  A first form was staged and withdrawn
(ruling **R-BAL6** records it): ``template_id IS NULL OR amount_source_id IS
NULL OR due_date IS NOT NULL``, forbidding only the *derived* undated row.
That ADMITS the undated leftover and refuses the TRANSITION -- the chooser's
declare, which writes ``amount_source_id`` and nothing else -- so it would
have turned a button press into an ``IntegrityError`` and then REQUIRED a
guard in ``resolve_conflicts`` to avoid it.  The two-term form is invariant
under that declare, because the declare touches neither column it names, and
so it needs no guard: every constructor that sets a template link dates the
row.  On ``transactions`` the two recurrence-engine paths splat
``DerivedRowFields`` (``compute_due_date``'s answer, which is never ``None``)
and the carry-forward leftover is X-bv's; on ``transfers`` the engine splats
``DerivedTransferFields`` and the one-time branch of
``routes/transfers/_instances`` writes the chosen paycheck's start.  No
writer in ``app/`` sets a template link on an existing row of either table.
The doors that could CLEAR the date on a linked row -- the transaction PATCH
(``routes/transactions/_gates._reject_generated_due_date_edit``), the
transfer PATCH and the shadow PATCH (both through
``Transfer.due_date_is_its_definitions``) -- refuse it a step earlier with a
designed 400; those gates also refuse MOVING the date, which no CHECK can
express, so they stay, and these constraints are their backstop for a writer
that is not the application.

**Why both tables in one revision.**  The two tables hold one fact -- what a
row of a definition IS -- and ruling R-BAL17 binds it on both so that a
constraint the transaction side states and the transfer side merely relies on
cannot come to disagree.  A release that carried one and not the other would
leave amount rule 3 with a refusal arm on one table and a guarantee on the
other, which is two spellings of one rule (``CLAUDE.md`` rule 14).

**Nothing is backfilled, and a violating row REFUSES the upgrade rather than
being dated here.**  The date has ONE producer, ``compute_due_date``, and it
is application code (``CLAUDE.md`` rule 14: a migration re-spelling it would
be a second walk).  Measured rather than assumed, on the databases as they
stand::

    SELECT count(*) FROM budget.transactions
     WHERE template_id IS NOT NULL AND due_date IS NULL;
    SELECT count(*) FROM budget.transfers
     WHERE transfer_template_id IS NOT NULL AND due_date IS NULL;

Production 2026-09-12 (``shekel-prod-db``, read-only, stamp ``6fc77e86d76f``):
**0 of 636** linked transactions and **0 of 177** linked transfers, live or
soft-deleted.  Transactions were also 0 of 626 on 2026-09-11 and 0 on
2026-09-06 (X-bv's own measurement); the transfers twin had never been counted
before this step.  Should a violating row exist when this runs, the ``ALTER
TABLE`` fails with ``CheckViolation`` naming the constraint, nothing is
changed, and the SELECT above names the row for the operator to date through
the application.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "4d7123cd9803"
down_revision = "6fc77e86d76f"
branch_labels = None
depends_on = None


def upgrade():
    """Bind both CHECKs; PostgreSQL validates every existing row as part of each."""
    op.create_check_constraint(
        "ck_transactions_template_row_needs_due_date", "transactions",
        "template_id IS NULL OR due_date IS NOT NULL",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transfers_template_row_needs_due_date", "transfers",
        "transfer_template_id IS NULL OR due_date IS NOT NULL",
        schema="budget",
    )


def downgrade():
    """Drop both CHECKs.  Lossless: they store nothing and no row is touched."""
    op.drop_constraint(
        "ck_transfers_template_row_needs_due_date", "transfers",
        type_="check", schema="budget",
    )
    op.drop_constraint(
        "ck_transactions_template_row_needs_due_date", "transactions",
        type_="check", schema="budget",
    )
