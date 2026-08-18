"""a settled row records what moved

Plan step **X-au-c3** of ``docs/audits/balance_architecture/README.md`` section
5.  A row is a PLAN -- ``estimated_amount`` priced by ``amount_source_id`` --
until its money moves, and a RECORD of what moved once it has.  This migration
gives the record its own columns so the two never share one.

**It replaces the FREEZE that step was specified to build, and the reason is a
root cause the specification did not name.**  A settled row's record of what
moved was OPTIONAL: ``actual_amount`` was populated only when a human typed a
correction, and it was NULL on 124 of the 166 settled rows on the 2026-08-16
production clone.  So every reader -- five Python valuations and three SQL folds,
all of them ``COALESCE(actual_amount, estimated_amount)`` -- fell back to the
row's PLAN.  A plan is a derivation, and a derivation moves: an effective-dated
price series (plan step X-au-a) admits a version dated into the past, so
re-resolving a historical row can answer a different figure and be right, because
the series records what a price WAS while the bank records what it TOOK.  That
is why the plan had to be frozen at settle, why the freeze then had to be undone
on a revert, and why undoing it needed to remember a declaration the freeze had
destroyed.

Making the record MANDATORY removes all of it.  A settled row's balance never
reads its plan, so nothing needs freezing, nothing needs handing back, and the
five remaining per-kind cutovers (plan steps X-au-d..X-au-i) may empty
``estimated_amount`` for a settled row without touching what it booked.

Four things, and **no row's balance moves**:

  1. **ref.settlement_bases**, seeded ``derived`` / ``corrected`` / ``purchases``
     -- HOW a settled row's figure is known.  There is deliberately no
     ``not_settled`` row: not having settled is the ABSENCE of a basis, which is
     what lets the pairing CHECK below be written over two NULL-nesses instead of
     freezing a ref id into the schema (the same shape ``ref.amount_sources``
     uses for its OWN state).
  2. **``actual_amount`` becomes ``settled_amount``**, and
     **``settled_basis_id``** joins it.  The rename is the fix rather than
     tidying (finding **N-241**): that column answered two questions at once --
     its VALUE was the settled figure and its NULL-ness was read by three
     subsystems as *a human entered this* (ruling **R-FH**) -- so a
     machine-derived figure written there manufactured a correction nobody made,
     and a settle with no correction to record recorded nothing at all.
  3. **The backfill**, which is byte-identical BY CONSTRUCTION: every settled row
     takes the exact figure ``COALESCE(actual_amount, estimated_amount)`` already
     answers for it, and every classification is measured rather than assumed
     (the counts are in :func:`upgrade`).
  4. **The constraints.**  ``ck_transactions_settled_amount_needs_basis`` keeps
     a stored figure and its provenance together,
     ``ck_transactions_settle_day_needs_basis`` makes a row that ASSERTS its
     money moved say what moved, and ``ck_transactions_actual_amount`` follows
     its column's name.

     **The day constraint is an IMPLICATION, and a draft of this revision made
     it a BICONDITIONAL, which was the error** (``ck_transactions_settlement_
     recorded``, ``(settled_on IS NULL) = (settled_basis_id IS NULL)``).
     ``settled_on`` and ``reconciled_by_id`` are the ASSERTION that money moved
     on a named day, which a revert withdraws; ``settled_amount`` and
     ``settled_basis_id`` are WHAT MOVED, which is a fact about the row.  The
     ``<-`` direction forced one lifetime on the two, so releasing the assertion
     had to destroy the figure -- and a user following the full-edit popover's
     own instruction ("set Status to Projected to edit the amounts") silently
     lost a number they had read off their bank statement.  That is finding
     **N-241**'s shape, one thing answering two questions, rebuilt one level up
     inside the step that exists to remove it.

     **The ``->`` direction survives that argument untouched, and dropping it
     with the other half was over-correction** (developer, 2026-08-17).  It says
     only that a row claiming a settle DAY must record a settle FIGURE, which is
     exactly the state retention needs to stay legal in the other direction: a
     reverted row has ``settled_on IS NULL`` and a basis, and passes.  What it
     forbids is the row on which this app's two tiers DISAGREE --
     ``row_valuation.settled_figure`` raises for a settled row recording nothing
     while ``posting_reads.settled_figure_clause`` answers ``0``, and the SQL
     side is what writes the ledger.  Measured on the 2026-08-17 production dump
     before it was added: 166 rows carry a settle day, all 166 are in a settled
     status, and no settled row lacks one, so every row the backfill above
     classifies satisfies it by construction.
     What keeps a retained figure out of a balance remains the STATUS, asked by
     ``row_valuation.settled_figure`` -- not either constraint.

**The one rule a CHECK cannot state**, said here so it is not mistaken for one
that is enforced: ``purchases`` is the single basis that stores NO figure -- the
row's own entries state it, and a stored copy would need a reconciler, which is
the shape ruling **R-FI** deletes.  Saying "``purchases`` if and only if
``settled_amount IS NULL``" requires the constraint to name a
``ref.settlement_bases`` id, which is the one thing this project's ref
convention keeps out of a schema.  So that half is a write-door rule with its own
negative control (``tests/test_models/test_settlement_record.py``).

**The five unsettled rows carrying a figure are PROMOTED, not dropped**
(developer, 2026-08-16).  ``actual_amount`` was reachable on an unsettled row
through the full-edit form, and 5 production rows carry one: 1 Projected, 3
Credit, 1 Cancelled.  Under this model that state is illegal -- a figure records
a settle, and no money has moved.  Their figure moves into the PLAN, which is
balance-NEUTRAL: the valuation already preferred ``actual_amount`` over
``estimated_amount`` for exactly these rows, so every balance answered the
promoted number before this migration and answers it after.  What changes is that
the row's budget cell now agrees with the balance instead of showing the figure
the app was not using.  All three template-linked rows already carry
``is_override``, so nothing needs to be flagged to survive a regeneration sweep.

**Not audited.**  ``ref.settlement_bases`` is a read-only seed catalogue,
deliberately excluded from ``app.audit_infrastructure.AUDITED_TABLES`` on the
same criteria that keep ``ref.amount_sources`` and every other ref catalogue out.
No trigger is attached and ``EXPECTED_TRIGGER_COUNT`` is unchanged.
``budget.transactions`` is already audited, so its existing trigger records the
renamed and the new column with no change here.

**Inline seed rationale.**  The three rows are seeded here rather than deferred
to the entrypoint's ``seed_reference_data`` pass, so ``ref_cache.init()``
resolves ``SettlementBasisEnum`` immediately after a bare ``flask db upgrade`` --
an enum member with no matching row is a fatal ``RuntimeError`` at app start.
``ON CONFLICT (name) DO NOTHING`` keeps it idempotent against a re-run and
against the entrypoint's later reseed, which carries the identical rows via
``app/ref_seeds.py``.

**Downgrade restores every row's effective figure exactly**, and it is keyed on
the STATUS as well as the basis.  An UNSETTLED row's record is dropped outright:
a revert keeps what moved under this model, and the code the downgrade returns to
reads ``COALESCE(actual, estimated)`` with no status gate, so a retained figure
left in place would price a Projected row at what it recorded before the revert
-- measured on a clone, ``$254.68`` on a ``$500.00`` plan corrected to
``$245.32``.  For a SETTLED row: the stored figure for ``corrected``, the
re-summed entries for ``purchases`` (which is where that figure came from), and
for ``derived`` a NULL **only where the plan still holds the same number**.  That last guard is a money fix rather than a formality: a
row settled AFTER this upgrade can carry a ``derived`` figure its plan does not
hold, because the transfer settle records one and writes no plan column, and
blanking it deleted ``$99.10`` from a ``$1,599.10`` loan payment on a clone.
Where the two differ the figure is kept in the renamed column, which is what the
old model MEANT by "this row is worth X".  **The promotion of the five unsettled
rows is NOT reversed**, and that is stated rather than hidden: the pre-promotion
split is not recoverable from the post-promotion row.  It is balance-equivalent
either way -- the older code reads ``COALESCE(actual, estimated)``, which
answers the promoted figure -- so the downgrade loses a display detail (the
*"(est: ...)"* caption on those five rows), not money.

Revision ID: e4b8a71c0f36
Revises: d9f5c1a48b73
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e4b8a71c0f36"
# RE-POINTED at the dev merge of 2026-08-18.  It was authored on
# ``d9f5c1a48b73`` and so was ``b2e9a47c3f18`` (plan step R9, which drops
# ``ref.recurrence_patterns``), leaving two Alembic heads sharing one parent.
# Whoever merges second re-points, and that is this branch.  The two touch
# disjoint tables -- a settlement record on ``budget.transactions`` against a
# recurrence lookup in ``ref`` -- so the order between them is arbitrary and
# neither backfill can see the other's rows.
down_revision = "b2e9a47c3f18"
branch_labels = None
depends_on = None


# The rows ``SettlementBasisEnum`` names.  Written as literal SQL rather than
# built from a Python tuple, and that is not style: the cross-migration
# inline-seed guard (``tests/test_models/test_posting_ref_seed_parity.py``)
# scans this chain for each enum value as a SINGLE-QUOTED literal inside an
# ``INSERT INTO`` its own ref table, so a value assembled at run time from a
# double-quoted tuple would be invisible to it and the dual seed would go
# unguarded for this table.
_SEED_SETTLEMENT_BASES_SQL = (
    "INSERT INTO ref.settlement_bases (name) VALUES "
    "('derived'), "
    "('corrected'), "
    "('purchases') "
    "ON CONFLICT (name) DO NOTHING"
)

# The settled statuses, as a subquery rather than an id list: which statuses
# settle is ``ref.statuses.is_settled``, and a migration that hardcoded the ids
# would be the magic number ``balance_predicates.settled_status_ids`` exists to
# avoid one layer up.
_SETTLED = "SELECT id FROM ref.statuses WHERE is_settled"

# Whether a row's purchases are tracked, resolved exactly as
# ``Transaction.tracks_purchases`` resolves it: a template-generated row defers
# to its template's flag, an ad-hoc row uses its own.
_TRACKS_PURCHASES = (
    "COALESCE("
    "(SELECT tt.is_envelope FROM budget.transaction_templates tt "
    "WHERE tt.id = t.template_id), t.is_envelope)"
)

# The sum of a row's purchases -- ALL of them, debit and credit, which is what
# ``settle_from_entries`` booked and what ``row_valuation.purchases_total``
# answers.  ``COALESCE`` to zero so a row with no entries reads 0 rather than
# NULL, which is the "no purchases recorded" answer the empty-envelope close
# already books (ruling **R-FJ**).
_ENTRY_SUM = (
    "COALESCE((SELECT sum(e.amount) FROM budget.transaction_entries e "
    "WHERE e.transaction_id = t.id), 0)"
)


def refuse_settled_rows_without_a_plan(bind) -> None:
    """Refuse the upgrade when a settled row has no figure to record.

    **Module-level so a test can drive it**, which is the pattern this chain's
    two previous amount-model revisions use (``a9d3c15e7f42``,
    ``b3f7c2a9d514``) and for the same reason: a guard nothing exercises is a
    guard nobody has seen work.  Called first by :func:`upgrade`, before any DDL,
    so a refused upgrade leaves the schema untouched.

    The backfill's ``derived`` arm records ``estimated_amount`` -- the figure
    every reader answers for such a row today.  A settled row whose
    ``estimated_amount`` is already NULL has a DERIVED plan, which means a
    per-kind cutover (plan steps X-au-d..X-au-i) ran before this migration; there
    is then no figure here to record, and the producer that would compute one
    lives in ``app/``, which a migration must not import.  Zero such rows exist
    at this revision -- nothing is declared derived yet -- so this is the arm
    that keeps that true rather than assumed.

    Args:
        bind: A SQLAlchemy connection to probe.

    Raises:
        RuntimeError: When a settled row carries neither a settled figure nor a
            plan, naming the first 20 ids and the diagnostic SELECT.
    """
    ids = [
        str(row[0]) for row in bind.execute(
            sa.text(
                "SELECT t.id FROM budget.transactions t "
                f"WHERE t.status_id IN ({_SETTLED}) "
                "AND t.actual_amount IS NULL "
                "AND t.estimated_amount IS NULL "
                "ORDER BY t.id LIMIT 20"
            )
        )
    ]
    if ids:
        raise RuntimeError(
            f"Settled transaction(s) {', '.join(ids)} (first 20) carry neither "
            "an actual_amount nor an estimated_amount, so there is no figure to "
            "record as what moved. Those rows have a DERIVED plan, which means a "
            "per-kind cutover (plan steps X-au-d..X-au-i) ran before this "
            "revision; downgrade that cutover first, which re-materialises each "
            "row's figure from the producer that priced it. There is "
            "deliberately no substitute: writing a number nobody computed into a "
            "settled row is the defect this whole step removes. Diagnostic: "
            "SELECT id, amount_source_id, status_id FROM budget.transactions "
            "WHERE estimated_amount IS NULL AND actual_amount IS NULL;"
        )


def upgrade():
    """Create the basis catalogue, split the record from the plan, and backfill.

    Order is load-bearing: the ref table exists before the FK targets it, the
    backfill runs before the pairing CHECKs so no intermediate state has to
    satisfy them, and the promotion of the unsettled rows runs last so its own
    ``settled_amount IS NOT NULL`` predicate cannot see a row the settled arms
    have just written.

    Measured on the 2026-08-16 production clone (1,012 transactions, 166
    settled), and every row lands in exactly one arm:

      * **29 purchases** -- 28 settled envelopes carrying entries plus the one
        entry-less envelope carry-forward closed at ``$0.00``.  Every one has
        ``actual_amount = sum(entries)``, so dropping the stored copy is provably
        lossless: zero envelope rows on the clone disagree with their own
        purchases.
      * **13 corrected** -- settled rows carrying a figure that is not their
        purchases' sum.
      * **124 derived** -- settled rows with no stored figure at all, which is
        what made every reader fall back to the plan.
      * **5 promoted** -- unsettled rows carrying a figure, whose figure moves
        into the plan.
    """
    bind = op.get_bind()
    refuse_settled_rows_without_a_plan(bind)

    op.create_table(
        "settlement_bases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_SETTLEMENT_BASES_SQL)

    op.alter_column(
        "transactions", "actual_amount",
        new_column_name="settled_amount",
        existing_type=sa.Numeric(precision=12, scale=2),
        schema="budget",
    )
    op.drop_constraint(
        "ck_transactions_actual_amount", "transactions",
        type_="check", schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_settled_amount", "transactions",
        "settled_amount IS NULL OR settled_amount >= 0",
        schema="budget",
    )
    op.add_column(
        "transactions",
        sa.Column("settled_basis_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_foreign_key(
        "fk_transactions_settled_basis_id",
        "transactions", "settlement_bases",
        ["settled_basis_id"], ["id"],
        source_schema="budget", referent_schema="ref",
        ondelete="RESTRICT",
    )

    # (1) PURCHASES -- the row's own entries state the figure, so the stored
    # copy goes.  Gated on the stored figure EQUALLING the entry sum, which is
    # what makes dropping it lossless: a settled envelope whose figure had
    # drifted from its purchases would fall through to the ``corrected`` arm and
    # keep its number rather than silently taking a different one.  (Zero rows on
    # the clone are in that state; the arm exists so the claim is a predicate
    # rather than a measurement that could go stale.)
    op.execute(
        "UPDATE budget.transactions t SET "
        "settled_basis_id = (SELECT id FROM ref.settlement_bases "
        "WHERE name = 'purchases'), settled_amount = NULL "
        f"WHERE t.status_id IN ({_SETTLED}) "
        "AND t.settled_amount IS NOT NULL "
        f"AND {_TRACKS_PURCHASES} IS TRUE "
        f"AND t.settled_amount = {_ENTRY_SUM}"
    )
    # (2) CORRECTED -- a figure that is not the row's purchases is one a human
    # read off a statement, which is exactly what ``actual_amount``'s presence
    # meant on a settled row before this step.
    op.execute(
        "UPDATE budget.transactions t SET "
        "settled_basis_id = (SELECT id FROM ref.settlement_bases "
        "WHERE name = 'corrected') "
        f"WHERE t.status_id IN ({_SETTLED}) "
        "AND t.settled_basis_id IS NULL "
        "AND t.settled_amount IS NOT NULL"
    )
    # (3) DERIVED -- no stored figure, so what every reader answers for this row
    # is its plan.  Recording that is what stops the plan being read for it ever
    # again, and it is the arm that carries 124 of the clone's 166 settled rows.
    op.execute(
        "UPDATE budget.transactions t SET "
        "settled_basis_id = (SELECT id FROM ref.settlement_bases "
        "WHERE name = 'derived'), settled_amount = t.estimated_amount "
        f"WHERE t.status_id IN ({_SETTLED}) "
        "AND t.settled_basis_id IS NULL"
    )
    # (4) The unsettled rows carrying a figure: it moves into the PLAN, and
    # ``amount_source_id`` is cleared with it because the row now states its own
    # price (``ck_transactions_amount_ownership`` is the pairing).  Balance
    # -neutral: the valuation already preferred that figure for these rows.
    op.execute(
        "UPDATE budget.transactions t SET "
        "estimated_amount = t.settled_amount, "
        "amount_source_id = NULL, "
        "settled_amount = NULL "
        f"WHERE t.status_id NOT IN ({_SETTLED}) "
        "AND t.settled_amount IS NOT NULL"
    )

    op.create_check_constraint(
        "ck_transactions_settled_amount_needs_basis", "transactions",
        "settled_amount IS NULL OR settled_basis_id IS NOT NULL",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_settle_day_needs_basis", "transactions",
        "settled_on IS NULL OR settled_basis_id IS NOT NULL",
        schema="budget",
    )


def downgrade():
    """Fold what moved back into ``actual_amount`` and drop the basis.

    Restores every row's effective figure: the stored figure for ``corrected``,
    the re-summed entries for ``purchases`` (which is where that figure came
    from), and for ``derived`` a NULL only where the PLAN still holds the same
    number -- the state that made those rows read their plan.  A ``derived``
    figure the plan does NOT hold is kept, because nothing else would state it;
    the comment at that statement carries the ``$99.10`` measurement that arm was
    written for.

    **The five promoted rows are NOT un-promoted**, and the docstring at the top
    of this file says why that is disclosed rather than attempted: the
    pre-promotion split is not recoverable from the post-promotion row, and the
    older code's ``COALESCE(actual, estimated)`` answers the promoted figure
    either way, so what is lost is a caption and not money.
    """
    op.drop_constraint(
        "ck_transactions_settle_day_needs_basis", "transactions",
        type_="check", schema="budget",
    )
    op.drop_constraint(
        "ck_transactions_settled_amount_needs_basis", "transactions",
        type_="check", schema="budget",
    )

    # (0) THE RETENTION STATE, cleared FIRST because the older readers have no
    # status gate.  A row that settled and was then REVERTED keeps what moved
    # -- that is the whole point of the model this revision installs -- so an
    # unsettled row may legitimately carry a figure and a basis.  The code this
    # downgrade returns to reads ``COALESCE(actual_amount, estimated_amount)``
    # for ANY contributing row, settled or not, so leaving the figure there
    # would price a Projected row at what it recorded before the revert.
    # Measured on a clone: a ``$500.00`` plan corrected to ``$245.32`` and
    # reverted came back valued at ``$245.32``, moving ``$254.68`` and
    # recreating exactly the illegal state :func:`upgrade`'s arm 4 exists to
    # remove.
    #
    # The figure is DROPPED rather than promoted into the plan, and that is the
    # asymmetry with arm 4: those five rows carried a figure the old readers had
    # ALREADY been using, so promoting it was balance-neutral.  Here the old
    # readers were using the PLAN -- ``settled_figure`` answers ``None`` for an
    # unsettled row whatever it retains -- so keeping the plan is what preserves
    # every balance, and the retained figure is a fact the older schema has no
    # way to hold.  Stated rather than hidden: a downgrade loses the memory of a
    # correction on a reverted row, and no balance moves.
    op.execute(
        "UPDATE budget.transactions t SET "
        "settled_amount = NULL, settled_basis_id = NULL "
        f"WHERE t.status_id NOT IN ({_SETTLED})"
    )

    # Re-materialise a purchases-basis figure from the entries that state it,
    # BEFORE the basis column goes -- afterwards there is nothing left to say
    # which rows those were.
    op.execute(
        "UPDATE budget.transactions t "
        f"SET settled_amount = {_ENTRY_SUM} "
        f"WHERE t.status_id IN ({_SETTLED}) "
        "AND t.settled_basis_id = (SELECT id FROM ref.settlement_bases "
        "WHERE name = 'purchases')"
    )
    # A ``derived`` figure goes back to NULL ONLY where the PLAN still holds the
    # same number, which is the state such a row was in before the upgrade: the
    # old readers answered ``COALESCE(actual, estimated)`` and the estimate was
    # the figure.
    #
    # **The ``IS NOT DISTINCT FROM`` guard is a money fix, measured rather than
    # defensive.**  Blanking every ``derived`` row was correct for the rows this
    # migration's own backfill classified -- arm 3 copies ``estimated_amount``,
    # so the two agree by construction -- and wrong for every row settled AFTER
    # the upgrade, which is a state only the running app produces.  The transfer
    # settle books ``Settlement.from_settle(frozen, None)`` on the ``derived``
    # basis and writes no plan column at all, so a loan payment settled at
    # ``$1,599.10`` against a ``$1,500.00`` estimate lost ``$99.10`` from every
    # balance on rollback -- reproduced on a production clone before this guard
    # was written.  The same shape arrives for ordinary rows once the per-kind
    # cutovers (plan steps X-au-d..X-au-i) empty ``estimated_amount``: there the
    # comparison is NULL-vs-figure, which is DISTINCT, so the figure is kept and
    # ``COALESCE`` answers it.  Keeping the figure in the renamed column is what
    # the old model MEANT by "this row is worth X".
    op.execute(
        "UPDATE budget.transactions t SET settled_amount = NULL "
        f"WHERE t.status_id IN ({_SETTLED}) "
        "AND t.settled_basis_id = (SELECT id FROM ref.settlement_bases "
        "WHERE name = 'derived') "
        "AND t.settled_amount IS NOT DISTINCT FROM t.estimated_amount"
    )

    op.drop_constraint(
        "fk_transactions_settled_basis_id", "transactions",
        type_="foreignkey", schema="budget",
    )
    op.drop_column("transactions", "settled_basis_id", schema="budget")
    op.drop_constraint(
        "ck_transactions_settled_amount", "transactions",
        type_="check", schema="budget",
    )
    op.alter_column(
        "transactions", "settled_amount",
        new_column_name="actual_amount",
        existing_type=sa.Numeric(precision=12, scale=2),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_actual_amount", "transactions",
        "actual_amount IS NULL OR actual_amount >= 0",
        schema="budget",
    )
    op.drop_table("settlement_bases", schema="ref")
