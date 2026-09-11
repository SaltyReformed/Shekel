"""a generated transfer reads its definition, and it stores no figure

Revision ID: b7e4c1f38a20
Revises: a1c7e5d20f43
Create Date: 2026-09-10 03:10:00.000000

Plan step **X-au-f** of ``docs/audits/balance_architecture/README.md`` section 5
-- the DATA half of ruling **R-FI** for the PARENT transfer, and the last
per-kind cutover after ``c9a4e7b21d58`` (transfer shadows), ``d7b2e6c1a483``
(salary) and ``c8f3a5d2e714`` (ordinary recurring rows).  The transfer twin of
that third one, and it follows its shape deliberately.

Review: the leaf boundary is developer-ruled 2026-09-10 -- the producer, this
migration and the writers are ONE act, because splitting them leaves a commit
in which every reader asks the parent while the parent still stores a stale
snapshot.  Destructive in the sense that matters: it empties a populated money
column on 169 rows.  Every one of those figures is reproduced by the downgrade
below on the data measured.

## What it does

Every row in ``budget.transfers`` that carries a template, that the owner has
not overridden, and that still owns its figure stops storing one and DECLARES
the relation that prices it::

    amount_source_id = ref.amount_sources('template')
    amount           = NULL

``ck_transfers_amount_ownership`` is the BICONDITIONAL
``(amount_source_id IS NULL) = (amount IS NOT NULL)``, so the two writes are one
statement and a half-write cannot commit.  After this, a generated transfer's
amount has exactly ONE producer where it had two -- the definition at READ time
and ``transfer_recurrence`` at GENERATION time, with nothing reconciling the
stored copy.  ``CLAUDE.md`` rule 14.

## Which rows, and why exactly those

Measured read-only against production at stamp ``a1c7e5d20f43``, 2026-09-09:

  ====================================================  =======
  measurement                                            value
  ====================================================  =======
  transfers in total                                    175
  rows DECLARED here                                    **169**
  of those, Projected                                   96
  of those, Paid                                        13
  of those, Cancelled                                   9
  of those, soft-deleted Projected                      51
  left OWN because the owner overrode them              6
  ad-hoc rows (no template) in the set                  **0**
  rows differing from what their series answers         **0**
  rows carrying no ``due_date``                         **0**
  rows whose template states no price at all            **0**
  loan payments in the set                              **0**
  ====================================================  =======

**Not one ad-hoc transfer exists**, and the schema is what keeps them out of
this set rather than the measurement: ``ck_transfers_adhoc_owns_amount`` refuses
a declaration on a row carrying no ``transfer_template_id``, because nobody
generated it and no definition states its price.  The predicate selects on that
column, so the CHECK and this migration agree by construction.

**An OVERRIDDEN row keeps its figure**, for the reason ``d7b2e6c1a483`` and
``c8f3a5d2e714`` both state: the flag is the only record a migration can read of
a human having authored a figure, and taking the conservative side of it can
only leave a row storing a figure that was already true.

**A SETTLED transfer is declared like any other.**  Its plan is a derivation
like any other plan, and what it was PLANNED at when the money moved is already
stored once -- on its expense LEG, in ``settled_amount`` on the ``derived``
settlement basis.  The parent carries no such column: a transfer's money moves
on its two legs.

**LOAN PAYMENTS are declared here too, and that is what makes this leaf one
act** (ruling **R-BAL10**).  A derive-mode payment's parent had no producer
until this step -- it reached the series arm and was REFUSED by
``template_amount_service.owns_its_amount`` -- which is finding **N-263**, and
it is the reason the producer and this migration cannot be separate commits:
declaring such a row before its producer exists makes it unpriceable, and
building the producer before the declaration leaves every reader on the stale
snapshot.  Zero such rows exist on production (``budget.loan_payment_settings``
is EMPTY), so this class is graded on a seeded loan and the figure it moves
there is the drift a snapshot had accumulated.

## What it costs: `$0.00`, measured row by row rather than argued

All 169 rows already store exactly what their definition's series answers on
their own due date: 0 differing, `$0.00` net and `$0.00` gross.  So the cutover
deletes a COPY rather than a fact, and no screen and no balance can move by the
deletion itself.  **2 of them answer from a SUPERSEDED version**, which is the
population on which the series' time dimension is observable at all.

## The downgrade, and the exact limit of its losslessness

The same two arms ``c8f3a5d2e714`` uses (ruling **R-JC**), in the same order,
with the record read off the LEG because the parent has no such column:

  * a transfer whose expense leg settled on the ``derived`` basis restores from
    that leg's ``settled_amount``, which IS the plan this migration emptied, so
    the restore is EXACT;
  * every other row restores from its template's ``default_amount``, the
    definition's own stated scalar and the app's own fallback for this quantity.

**Where it is neither exact nor guaranteed**: a transfer whose leg settled on
the ``corrected`` basis, whose plan at settle is recoverable from nothing this
migration can read.  :func:`settled_rows_whose_plan_is_not_recoverable` NAMES
them rather than leaving the operator to discover them, and PRINTS rather than
raising, for the reason its transaction twin gives: only a display caption reads
a settled row's plan, and every money reader answers from the settlement record.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b7e4c1f38a20"
down_revision = "a1c7e5d20f43"
branch_labels = None
depends_on = None

#: The declaration, resolved from the ref table by NAME.  A migration cannot
#: read ``app.ref_cache`` -- importing ``app`` from a migration is what makes a
#: schema change depend on application code that may not exist at the revision
#: being replayed -- so the id is looked up in the same statement that uses it.
_TEMPLATE_ID_SQL = "(SELECT id FROM ref.amount_sources WHERE name = 'template')"

#: A settled leg whose settle RECORDED the app's own resolution: its
#: ``settled_amount`` is the plan this migration emptied, to the cent.
_DERIVED_BASIS_SQL = (
    "(SELECT id FROM ref.settlement_bases WHERE name = 'derived')"
)

#: The EXPENSE transaction type, resolved by NAME like every other ref lookup
#: here.  A migration cannot read ``app.ref_cache``.
_EXPENSE_TYPE_SQL = (
    "(SELECT id FROM ref.transaction_types WHERE name = 'Expense')"
)

#: ONE row per transfer: its EXPENSE leg, and the record that leg carries.
#:
#: **Selected by TRANSACTION TYPE, not by account**, which is how
#: ``transfer_service._validation._get_shadow_transactions`` -- the service's own
#: and only way of naming a leg -- does it.  A first draft matched
#: ``s.account_id = x.from_account_id`` and claimed to be reading "the same leg
#: ``transfer_service._settle`` reads"; that was false, and it was also the one
#: spelling exposed to ledger row **BAL-475**, which records that NOTHING ties a
#: shadow's ``account_id`` to its parent's endpoints.  On the state that row
#: describes, an account-matched join finds NO leg and the downgrade skips the
#: transfer in silence.  Selecting by type, BAL-475 does not bear on this at all.
#:
#: **A plain JOIN rather than a LATERAL subquery**, and that is a correctness fix
#: rather than a preference: PostgreSQL forbids a ``LATERAL`` in an
#: ``UPDATE ... FROM`` from referencing the update's TARGET table, so the first
#: draft of the restore below raised ``InvalidColumnReference`` at run time -- on
#: the downgrade path, which no upgrade exercises and no control drove until this
#: migration got one.
#:
#: **``DISTINCT ON`` because this query leaves an index's coverage, and that is
#: the whole of the reason.** ``uq_transactions_transfer_type_active`` is UNIQUE
#: on ``(transfer_id, transaction_type_id)`` under
#: ``transfer_id IS NOT NULL AND is_deleted = FALSE``, so ONE LIVE expense leg
#: per transfer is enforced structurally and always has been. This query
#: deliberately includes DISCARDED legs -- see below -- which is exactly the
#: predicate the index excludes, so a transfer may present one live leg plus N
#: soft-deleted ones and the pick would otherwise be whichever row the planner
#: returned. The two changes are coupled: dropping the filter is what CREATES
#: the multiplicity, and without that this would be dead weight the next reader
#: correctly deletes, citing the index.
#:
#: The ``ORDER BY`` is explicit and load-bearing for the same reason:
#: ``DISTINCT ON`` without one returns an arbitrary member of each group, which
#: on a migration that empties a money column across 169 rows is not a property
#: worth having. Ordering on ``is_deleted`` first prefers a LIVE leg and falls
#: back to a discarded one rather than skipping the transfer.
#:
#: **It does NOT filter soft-deleted legs out**, and a first draft did. A settled
#: leg's record is history whether or not the row was later discarded -- the same
#: argument ``archive_helpers.transfer_template_has_paid_history`` adopts in this
#: step -- and filtering made a whole class invisible: a soft-deleted SETTLED
#: transfer restored from its template's scalar and was not named by the probe
#: below either. The census above partitions by status and by delete state and
#: has no row for that class, so it establishes that the class is untested rather
#: than that it is empty.
_EXPENSE_LEG_SQL = f"""
    SELECT DISTINCT ON (s.transfer_id)
           s.transfer_id, s.settled_amount, s.settled_basis_id
      FROM budget.transactions AS s
     WHERE s.transaction_type_id = {_EXPENSE_TYPE_SQL}
       AND s.transfer_id IS NOT NULL
     ORDER BY s.transfer_id, s.is_deleted, s.id
"""

#: A transfer template that carries loan-payment settings.  Its rows are priced
#: by amount rule 4 -- the LOAN -- rather than by the definition's series, so
#: the series preconditions below do not apply to them (ruling **R-BAL10**).
_LOAN_PAYMENT_TEMPLATES_SQL = (
    "SELECT transfer_template_id FROM budget.loan_payment_settings"
)

#: A loan-payment template in DERIVE mode, whose rows are priced from the loan's
#: own resolution and whose stored figure is a SNAPSHOT that has been free to
#: drift since it was written.  Excluded from the figure-agreement precondition
#: for exactly that reason: a difference there is the staleness this cutover
#: exists to delete, not evidence that the declaration would delete a fact.
_DERIVE_MODE_TEMPLATES_SQL = (
    "SELECT transfer_template_id FROM budget.loan_payment_settings "
    "WHERE derive_from_loan"
)

_DECLARE_SQL = f"""
    UPDATE budget.transfers
       SET amount_source_id = {_TEMPLATE_ID_SQL},
           amount = NULL
     WHERE transfer_template_id IS NOT NULL
       AND is_override = FALSE
       AND amount_source_id IS NULL
"""

_RESTORE_FROM_RECORD_SQL = f"""
    UPDATE budget.transfers AS t
       SET amount = leg.settled_amount,
           amount_source_id = NULL
      FROM ({_EXPENSE_LEG_SQL}) AS leg
     WHERE leg.transfer_id = t.id
       AND t.transfer_template_id IS NOT NULL
       AND t.amount_source_id = {_TEMPLATE_ID_SQL}
       AND leg.settled_basis_id = {_DERIVED_BASIS_SQL}
       AND leg.settled_amount IS NOT NULL
"""

_RESTORE_FROM_DEFINITION_SQL = f"""
    UPDATE budget.transfers AS t
       SET amount = tt.default_amount,
           amount_source_id = NULL
      FROM budget.transfer_templates AS tt
     WHERE tt.id = t.transfer_template_id
       AND t.amount_source_id = {_TEMPLATE_ID_SQL}
"""


def settled_rows_whose_plan_is_not_recoverable(bind) -> list:
    """Return the declared SETTLED transfers whose plan at settle is stored nowhere.

    **Module-level so a test can drive it** -- the pattern ``c9a4e7b21d58``,
    ``b3f7c2a9d514``, ``d7b2e6c1a483`` and ``c8f3a5d2e714`` all use, for the
    same reason: a guard nothing exercises is a guard nobody has seen work.

    A settled row records HOW its figure is known
    (:class:`app.enums.SettlementBasisEnum`).  On the ``derived`` basis the
    recorded figure IS the app's own resolution at the moment of settle, which
    is the plan this migration emptied, so the restore is exact.  On
    ``corrected`` the figure is a human's reading of a statement; that is not
    the plan, and the plan is then recoverable from nothing a migration can
    read.  Such a transfer restores from its template's ``default_amount`` like
    an unsettled one.

    **The record is read off the EXPENSE LEG**, because a transfer carries no
    settlement column of its own: its money moves on its two legs and each
    records what it did.  Either leg records the same figure (Transfer Invariant
    3), and naming one is what makes the choice deliberate rather than
    accidental -- selected by TYPE, which is the service's own way of naming a
    leg (``transfer_service._validation._get_shadow_transactions``).
    Soft-deleted
    legs are INCLUDED, for the reason :data:`_EXPENSE_LEG_SQL` states: a class
    the probe cannot see is a class the operator is not warned about.

    Args:
        bind: A SQLAlchemy connection to probe.

    Returns:
        The affected transfer ids, ascending; empty when every declared settled
        transfer records the ``derived`` basis.  **0 on production at stamp
        ``a1c7e5d20f43``**, where all 13 Paid transfers settled on that basis.
    """
    probe = sa.text(f"""
        SELECT t.id
          FROM budget.transfers AS t
          JOIN ({_EXPENSE_LEG_SQL}) AS leg ON leg.transfer_id = t.id
         WHERE t.transfer_template_id IS NOT NULL
           AND t.amount_source_id = {_TEMPLATE_ID_SQL}
           AND leg.settled_basis_id IS NOT NULL
           AND leg.settled_basis_id <> {_DERIVED_BASIS_SQL}
         ORDER BY t.id
    """)
    return [row[0] for row in bind.execute(probe)]


def downgrade_rows(bind) -> tuple:
    """Restore every declared transfer's figure and clear its declaration.

    **Module-level so a test can drive it, and the ORDER of the two statements
    is the reason it is worth driving**: the EXACT restore runs first, so a row
    it covers is no longer declared when the placeholder restore's predicate is
    evaluated.  Reversed, every ``derived``-basis settled transfer comes back at
    its template's ``default_amount`` instead of the figure its leg recorded.

    Args:
        bind: A SQLAlchemy connection to write through.

    Returns:
        ``(exact, placeholder)`` -- how many rows each arm restored.
    """
    exact = bind.execute(sa.text(_RESTORE_FROM_RECORD_SQL))
    placeholder = bind.execute(sa.text(_RESTORE_FROM_DEFINITION_SQL))
    return exact.rowcount, placeholder.rowcount


def rows_the_declare_would_strand(bind) -> list:
    """Return the transfers this migration must NOT declare, and would.

    **The preconditions, asked of the database at the moment the migration runs
    rather than of a clone the week before.**  "0, 0 and `$0.00` differing" was
    measured against production on 2026-09-09; production moves between a
    measurement and a deploy, and each of these, violated by one row, is a
    silently unpriceable transfer or a silently moved figure.

    **The preconditions are PER CLASS, because the three classes have three
    producers** (ruling **R-BAL10**):

    * an ORDINARY generated transfer is priced by its definition's series on its
      own due date (amount rule 3), so it needs a ``due_date``, a non-empty
      series, and a stored figure that AGREES with what that series answers --
      the cutover is meant to delete a copy, and a row where the two differ is
      one where it would delete a fact;
    * a MANUAL loan payment stores the same thing an ordinary row does -- the
      BASE -- so it is graded against the series ALONE, exactly like one.  The
      standing ``extra_principal`` is deliberately NOT baked into
      ``default_amount`` (``routes/loan/payment_transfer``: *"stored on the
      settings row and added live to every payment, in BOTH modes"*), so the
      series is opened at the typed base and the row's copy of it matches.
      **A first draft graded this arm against series + extra and would have
      RAISED on every manual payment carrying one** -- a failed deploy, since
      migrations auto-run in the pipeline -- on rows that are entirely correct.
      An adversarial review found it, and the fixture that "proved" the arm had
      hand-built an ``own(base + extra)`` shape no writer in ``app/`` produces.
      **What declaring such a row DOES move is the PARENT's answer**, from the
      base to the base plus the extra: that is the cash that leaves the bank and
      it is what ruling **R-BAL10** asks for. Its two LEGS already answered it
      (rule 4's old manual arm resolved the parent's figure plus the extra), so
      the fold moves ``$0.00`` and what changes is the parent agreeing with its
      own legs;
    * a DERIVE-mode loan payment is priced by the LOAN, so it needs a resolvable
      loan -- a destination account carrying ``LoanParams`` -- and NOTHING is
      asked about its stored figure.  That figure is a snapshot taken when the
      payment was set up and has been free to drift from the contract ever
      since; the difference IS the staleness this cutover deletes, so requiring
      agreement would refuse exactly the rows the step exists for.

    Module-level so a test can drive it, and modelled on ``a9d3c15e7f42``, which
    raises with a diagnostic rather than leaving half a cutover behind.

    Args:
        bind: A SQLAlchemy connection to probe.

    Returns:
        ``(id, due_date, amount, reason)`` per offending row, ascending by id;
        empty when every row the predicate selects is safe to declare.  Empty on
        production at stamp ``a1c7e5d20f43`` over all 169.
    """
    probe = sa.text(f"""
        SELECT t.id, t.due_date, t.amount,
               CASE
                 WHEN t.transfer_template_id IN ({_DERIVE_MODE_TEMPLATES_SQL})
                   THEN 'the loan that prices it will not resolve: its '
                        || 'destination account carries no LoanParams'
                 WHEN t.due_date IS NULL THEN 'no due_date to resolve on'
                 WHEN NOT EXISTS (
                        SELECT 1 FROM budget.template_amount_versions v
                         WHERE v.transfer_template_id = t.transfer_template_id)
                   THEN 'its template states no price at all'
                 ELSE 'its figure disagrees with what its definition answers'
               END AS reason
          FROM budget.transfers AS t
         WHERE t.transfer_template_id IS NOT NULL
           AND t.is_override = FALSE
           AND t.amount_source_id IS NULL
           AND (
             CASE WHEN t.transfer_template_id IN ({_DERIVE_MODE_TEMPLATES_SQL})
             THEN NOT EXISTS (
                    SELECT 1 FROM budget.loan_params p
                     WHERE p.account_id = t.to_account_id)
             ELSE
               t.due_date IS NULL
               OR NOT EXISTS (
                    SELECT 1 FROM budget.template_amount_versions v
                     WHERE v.transfer_template_id = t.transfer_template_id)
               OR t.amount IS DISTINCT FROM COALESCE(
                    (SELECT v.amount FROM budget.template_amount_versions v
                      WHERE v.transfer_template_id = t.transfer_template_id
                        AND v.effective_date <= t.due_date
                      ORDER BY v.effective_date DESC, v.id DESC LIMIT 1),
                    (SELECT v.amount FROM budget.template_amount_versions v
                      WHERE v.transfer_template_id = t.transfer_template_id
                      ORDER BY v.effective_date ASC, v.id ASC LIMIT 1))
             END
           )
         ORDER BY t.id
    """)
    return [tuple(row) for row in bind.execute(probe)]


def upgrade():
    """Declare every non-overridden generated transfer derived and empty its figure.

    One statement, behind one refusal.  It is idempotent by its
    ``amount_source_id IS NULL`` predicate, so a re-run declares nothing twice.

    Raises:
        RuntimeError: When any row the predicate selects could not be priced
            after the declaration, or stores a figure its definition disagrees
            with (:func:`rows_the_declare_would_strand`).  A refusal leaves
            every figure in place; declaring anyway would empty a column with
            nothing able to answer for it.
    """
    bind = op.get_bind()
    stranded = rows_the_declare_would_strand(bind)
    if stranded:
        detail = "; ".join(
            f"transfer {row_id} (due {due}, ${figure}): {reason}"
            for row_id, due, figure, reason in stranded
        )
        raise RuntimeError(
            f"X-au-f: {len(stranded)} transfer(s) cannot be declared derived "
            f"because nothing would be able to price them, or because the "
            f"figure they store is not what their definition answers. "
            f"NOTHING has been changed. {detail}"
        )
    result = bind.execute(sa.text(_DECLARE_SQL))
    print(f"X-au-f: {result.rowcount} generated transfer(s) declared derived")


def downgrade():
    """Restore each declared transfer's figure and clear the declaration.

    Two statements, in this order: the exact restore first, so a row it covers
    is no longer declared when the placeholder restore runs and cannot be
    written twice.
    """
    bind = op.get_bind()
    unrecoverable = settled_rows_whose_plan_is_not_recoverable(bind)
    if unrecoverable:
        ids = ", ".join(str(row_id) for row_id in unrecoverable)
        print(
            f"X-au-f: {len(unrecoverable)} settled transfer(s) ({ids}) record "
            "a settlement basis other than 'derived' on their expense leg, so "
            "the plan they held at settle is stored nowhere this migration can "
            "read. They restore from their template's default_amount. Every "
            "money reader answers from the settlement record, so no balance "
            "moves."
        )
    exact, placeholder = downgrade_rows(bind)
    print(
        f"X-au-f: {exact} transfer(s) restored exactly from their expense "
        f"leg's settlement record, {placeholder} from their template's "
        "default_amount"
    )
