"""a transfer shadow's amount is its parent's, and it stores none

Revision ID: c9a4e7b21d58
Revises: e2d7a94f61c3
Create Date: 2026-09-01 22:10:00.000000

Plan step **X-au-g-2c-2** of ``docs/audits/balance_architecture/README.md``
section 5 -- the DATA half of ruling **R-FI** for transfer shadows, and the
first cutover to write a declaration at all.  Ruling **R-IN** (developer,
2026-09-01) widened it from loan-payment shadows to EVERY transfer shadow: a
loan payment's shadow IS a transfer shadow, and the relation both declare is
the same one (``parent_transfer``), so the narrow version needed four
conditionals and a writer on the loan-settings routes that the wider one does
not need at all.

## What it does

Every row in ``budget.transactions`` carrying a ``transfer_id`` stops storing
a figure and DECLARES the relation that prices it:

    amount_source_id = ref.amount_sources('parent_transfer')
    estimated_amount = NULL

``ck_transactions_amount_ownership`` is the BICONDITIONAL
``(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)``, so the two
writes are one statement and a half-write cannot commit.  After this, Transfer
Invariant 3 -- *shadow amounts always equal the parent transfer's* -- is
STRUCTURAL: the shadow reads its parent through
``cash_ledger.resolve_transfer_amount`` rather than holding a copy, and the two
hand-written repairs that kept the copy true are deleted in the same commit
(``transfer_service._update``'s propagation and ``_restore``'s drift
corrector).

## What it costs, measured rather than assumed

Taken by ``psql`` against the production container on **2026-09-01**, at stamp
``a4c6f1d92b73``:

  ==================================================  =======
  measurement                                         value
  ==================================================  =======
  transfer shadows (``transfer_id IS NOT NULL``)      350
  parent transfers                                    175
  shadows whose figure differs from their parent's    **0**
  rows already carrying ``amount_source_id``          0
  rows in ``budget.loan_payment_settings``            0
  ==================================================  =======

So this **moves `$0.00`**: every shadow resolves to exactly the figure its own
column held, because rule 5 answers ``resolve_transfer_amount(parent)`` and the
parent owns that figure.  The empty ``loan_payment_settings`` is why rule 4 --
the loan arm, where a shadow's derivation is NOT its parent's amount -- prices
nothing on production and is graded on a seeded loan instead.

## The downgrade, and the limit of its losslessness

It restores ``estimated_amount`` from the parent transfer's ``amount`` and
clears the declaration.  ``transactions.transfer_id`` is ``ON DELETE CASCADE``,
so a declared row always has a live parent and the join is total.

**It is value-lossless ON THE LIVE DATA rather than value-lossless**, and the
difference is the whole reason to say it: the round trip restores the parent's
figure, which is what the column held for all 350 rows *as measured on the date
above*.  A shadow that had come to differ from its parent -- the state the
deleted drift corrector existed to repair -- would have that difference
overwritten.  :func:`refuse_a_shadow_whose_parent_states_no_figure` refuses the
other direction of the same hazard: once plan step **X-au-f** empties
``transfers.amount`` for a generated transfer, there is no figure to restore
and this downgrade must stop rather than write a NULL the ownership CHECK
refuses.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c9a4e7b21d58"
down_revision = "e2d7a94f61c3"
branch_labels = None
depends_on = None

#: The declaration, resolved from the ref table by NAME.  A migration cannot
#: read ``app.ref_cache`` -- importing ``app`` from a migration is what makes a
#: schema change depend on application code that may not exist at the revision
#: being replayed -- so the id is looked up in the same statement that uses it.
#: The name is the ref row's natural key (``uq`` on ``ref.amount_sources.name``,
#: seeded by ``b3f7c2a9d514``), never an id literal.
_PARENT_TRANSFER_ID_SQL = (
    "(SELECT id FROM ref.amount_sources WHERE name = 'parent_transfer')"
)

_DECLARE_SQL = f"""
    UPDATE budget.transactions
       SET amount_source_id = {_PARENT_TRANSFER_ID_SQL},
           estimated_amount = NULL
     WHERE transfer_id IS NOT NULL
       AND amount_source_id IS NULL
"""

_UNDECLARE_SQL = f"""
    UPDATE budget.transactions AS t
       SET estimated_amount = x.amount,
           amount_source_id = NULL
      FROM budget.transfers AS x
     WHERE x.id = t.transfer_id
       AND t.amount_source_id = {_PARENT_TRANSFER_ID_SQL}
"""


def refuse_a_shadow_whose_parent_states_no_figure(bind) -> None:
    """Refuse the downgrade when a declared shadow's parent holds no figure.

    **The only non-DDL logic here, and it is module-level so a test can drive
    it** -- the pattern ``a9d3c15e7f42`` and ``b3f7c2a9d514`` both use, for the
    same reason: a guard nothing exercises is a guard nobody has seen work.
    Called first by :func:`downgrade`, before anything is written, so a refused
    downgrade leaves every row untouched.

    A parent transfer with ``amount IS NULL`` is one plan step **X-au-f** has
    declared derived.  Its shadows have no figure to be restored TO: writing
    the NULL would violate ``ck_transactions_amount_ownership`` in the same
    statement that cleared the declaration, and computing one would need the
    definition's price series -- a producer that lives in ``app/``, which a
    migration must not import.  So it names the rows and stops.

    Args:
        bind: A SQLAlchemy connection to probe.

    Raises:
        RuntimeError: When any declared shadow's parent carries no amount,
            naming the first 20 shadow ids and the diagnostic SELECT.
    """
    probe = sa.text(
        "SELECT t.id FROM budget.transactions AS t "
        "JOIN budget.transfers AS x ON x.id = t.transfer_id "
        f"WHERE t.amount_source_id = {_PARENT_TRANSFER_ID_SQL} "
        "AND x.amount IS NULL ORDER BY t.id LIMIT 20"
    )
    ids = [str(row[0]) for row in bind.execute(probe)]
    if ids:
        raise RuntimeError(
            "Cannot downgrade c9a4e7b21d58: "
            f"{len(ids)} or more transfer shadows (ids {', '.join(ids)}) "
            "declare parent_transfer while their parent transfer states no "
            "amount, so there is no figure to restore. A later cutover "
            "(plan step X-au-f) has declared those transfers derived; "
            "downgrade it first. Diagnostic: SELECT t.id, t.transfer_id FROM "
            "budget.transactions t JOIN budget.transfers x ON x.id = "
            "t.transfer_id WHERE t.amount_source_id IS NOT NULL AND "
            "x.amount IS NULL;"
        )


def upgrade():
    """Declare every transfer shadow derived and empty its figure.

    One statement.  It is idempotent by its ``amount_source_id IS NULL``
    predicate, so a re-run declares nothing twice, and it touches no row that
    is not a transfer shadow.
    """
    op.execute(sa.text(_DECLARE_SQL))


def downgrade():
    """Restore each shadow's figure from its parent and clear the declaration.

    Raises:
        RuntimeError: From
            :func:`refuse_a_shadow_whose_parent_states_no_figure`, when a later
            cutover has left a parent with no figure to restore.
    """
    refuse_a_shadow_whose_parent_states_no_figure(op.get_bind())
    op.execute(sa.text(_UNDECLARE_SQL))
