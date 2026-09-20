"""the record is its movements: the row's figure columns go

Revision ID: 45f10b870c8b
Revises: 22b23085394d
Create Date: 2026-09-19

Plan step **balance:X-bi-4b-2** (the second leaf of ``X-bi-4b``, the
migration and the sweep) of ``docs/audits/balance_architecture/README.md``
section 5, under rulings **R-BAL80** (developer, 2026-09-18: the row's figure
columns go by a migration), **R-BAL82** (developer, 2026-09-19: a ``$0.00``
close is a close with no entries) and **R-BAL83** (developer, 2026-09-19: the
readers first, then the migration)::

    budget.transactions.settled_amount      DROPPED
    budget.transactions.settled_basis_id    DROPPED, with its FK and CHECKs
    ref.settlement_bases                    DROPPED

**What the columns were.**  A settled row's money lived in TWO homes: the
row's own ``settled_amount`` (the figure) and ``settled_basis_id`` (how it is
known: ``derived`` / ``corrected`` / ``purchases``, ``ref.settlement_bases``),
written by the status seam in the same act as, since plan step ``X-bi-3a``,
ONE covering movement in ``budget.transaction_entries`` carrying the same
figure and who wrote it (``figure_source_id``).  Rule 14: the same fact in
two columns is two sources.  ``X-bi-4a`` made the balance and the posted
ledger read movements alone; ``X-bi-4b-1`` (``21709195``) re-pointed every
other reader -- ``row_valuation.settled_figure`` is the sum of the row's
entries, ``status_seam.recorded_settlement`` and ``honoured_correction`` read
the movement's figure and source, the entry doors' "records a fixed figure"
predicates and the statement matcher read ``covering_movements``, the SQL
twin sums the entries -- so on the tree this revision ships with, the two
columns are written by the seam and read by nothing that counts or shows
money.  This revision deletes them, the three CHECKs over them
(``ck_transactions_settled_amount``,
``ck_transactions_settled_amount_needs_basis``,
``ck_transactions_settle_day_needs_a_record``), the foreign key
``fk_transactions_settled_basis_id`` and the catalogue table; the seam's
write, ``Settlement.basis``, ``SettlementBasisEnum`` and the ref-cache
accessor go in the same commit.  The ``>= 0`` guarantee the first CHECK held
moves to ``Settlement.__post_init__``, where a settle door cannot build a
negative record to hand over (the movement's own CHECK is ``amount <> 0``,
because a merchant credit is a negative PURCHASE, ruling
``bank_import:R-II``).

**Fail-closed, before any DDL** (ruling **R-BAL40**'s shape: no judgment).
The columns are the LAST copy of a figure every reader stopped asking at
``X-bi-4b-1``; deleting them is lossless exactly when the copy every reader
now asks -- the movements -- agrees with them.  Three predicates, each
naming its count, the repair and the diagnostic query:

* **a settled row whose stored figure is NOT ZERO and differs from the sum
  of its entries** -- the reader through ``X-bi-4a`` answered the column for
  a ``derived`` / ``corrected`` row and the reader since answers
  ``SUM(entries)``; a disagreement is a figure the balance showed once and
  shows no more.  A stored ``$0.00`` is EXEMPT (**R-BAL82**): a ``$0.00``
  close holds no movement (``ck_transaction_entries_positive_amount`` admits
  no movement of nothing) and ADMITS a later purchase, so a row legitimately
  reads ``(0, corrected)`` beside ``SUM(entries) = 12.00``, and the entries
  are its record.  Repair: revert the row and re-settle it at the real
  figure through the full-edit popover's Actual box.
* **a covering movement whose parent's stored figure differs from it** --
  the same equality asked of the ONE row the movement mirrors, over EVERY
  row that holds one, in or out of the settled band.  Inside the band the
  first predicate already implies it for every state a door writes (a row
  holding a movement holds no purchase: ``entry_service`` refuses the add
  and the seam refuses a stated figure over purchases, ruling **R-BAL78**),
  and a row holding a movement AND purchases fails one or the other.
  Outside the band the first predicate cannot be asked at all: a reverted
  envelope's family legitimately sums past its retained movement (a typed
  ``$120.00`` close, reverted, over one ``$30.00`` purchase: entries
  ``$150.00``, movement ``$120.00``, ruling **R-BAL68**'s worked example),
  so the RETAINED record -- what a re-settle honours, read off the movement
  since ``X-bi-4b-1`` -- is graded against the column here.  No exemption:
  the seam writes the column and the movement from one value, and a
  movement of ``$0.00`` cannot exist, so a ``$0.00`` or NULL figure beside
  a movement is a write around the seam.
* **a row OUT of the settled band on the ``corrected`` basis with a
  non-zero figure and NO covering movement** -- a retained correction that
  only the column carries, which the popover would have prefilled and a
  re-settle honoured through ``X-bi-4a`` and which nothing carries once the
  column goes.  A ``$0.00`` typed figure is EXEMPT (R-BAL82: it is retained
  by nothing across a revert, so there is nothing to lose); a reverted
  ``derived`` record is not honoured by any tree and is not asked.  Repair:
  re-settle the row at that figure through the popover (the seam writes
  the movement, kept across the revert since ``X-bi-3e-2``), then revert.

Soft-deleted rows are included in all three: a movement stands through a
soft delete and a restore need not pass through the seam.

**The downgrade rebuilds the columns from the movements** and re-creates the
catalogue, the FK and the three CHECKs.  A row holding a covering movement
takes the movement's figure and ``derived`` where its source is ``resolved``,
``corrected`` otherwise -- the mapping ``Settlement.basis`` answered on the
tree below; a settled row holding none takes ``purchases`` with a NULL
figure, the shape the seam below wrote for an envelope closed from its
entries and, since R-BAL82, for a ``$0.00`` close (which that tree reads as
``$0.00`` through the ``purchases`` arm's entry sum); every other row takes
NULL / NULL.  Exactly what the seam below would have written for every
state the seam above leaves, so the older image reads the same money.  It
refuses, before the CHECKs are re-created, a row outside the band carrying
a settle day and no movement: ``ck_transactions_settle_day_needs_a_record``
would refuse to store it and no seam writes it.

**Measured on a production restore** (``shekel_xbi4b2``, the 2026-09-19
07:21 EDT dump at stamp ``97f92340fffc``, stepped through ``22b23085394d``
first, which recorded 0 stated balances there): 1,083 rows, 229 settled (151
``derived`` / 22 ``corrected`` / 56 ``purchases``), 173 covering movements,
0 rows on every one of the three predicates, 0 stored ``$0.00`` figures, 0
rows outside the band carrying a record, 0 rows holding a movement beside a
purchase.  The upgrade printed ``229 settled row(s) read their entries``.
``tests/manual/verify_balance_baseline.py`` (9 accounts, 448 grid cells,
6,272 daily points) and ``verify_statement_baseline.py`` (2 users, 143
statements) are byte-identical before and after, as are the posted
ledger's nets per ``(ledger account, entry_date)`` (514 rows) and every
movement row; ``integrity_check`` DC-10 and DC-11 pass.  The downgrade then
rebuilt all 1,083 rows' ``(settled_amount, basis)`` byte-identical to the
pre-upgrade snapshot -- the total rule of the docstring, graded on the
data rather than described -- with the three CHECKs, the FK and the seeded
catalogue back; the second upgrade printed the same count and left every
baseline unmoved.  Production prints its own count.

**Rollback across this release is a dump restore, not a downgrade**
(``deploy/shekel-deploy.sh`` takes a pre-deploy dump); the downgrade exists
for the rehearsal and for a database that has run this revision alone.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "45f10b870c8b"
down_revision = "22b23085394d"
branch_labels = None
depends_on = None


def _basis(name: str) -> str:
    """Return a scalar subquery for one ``ref.settlement_bases`` id by NAME.

    A migration resolves a ref row by its name because the id is assigned by
    the sequence and differs between databases; application code never did
    this (``ref_cache.settlement_basis_id`` was that door, and goes with the
    table).
    """
    return f"(SELECT id FROM ref.settlement_bases WHERE name = '{name}')"


#: The sum of a row's entries -- ALL of them, the covering movement and the
#: purchases, debit and credit -- which is what ``row_valuation.settled_figure``
#: answers for a settled row since ``X-bi-4b-1``.  ``COALESCE`` to zero so a
#: row with no entries reads ``0``, the ``$0.00`` record.
_ENTRY_SUM = (
    "COALESCE((SELECT SUM(e.amount) FROM budget.transaction_entries e "
    "WHERE e.transaction_id = t.id), 0)"
)

#: The row's covering movement, at most one by
#: ``uq_transaction_entries_one_settlement_record``.
_MOVEMENT_EXISTS = (
    "EXISTS (SELECT 1 FROM budget.transaction_entries e "
    "WHERE e.transaction_id = t.id AND e.covers_settlement)"
)

# Predicate 1: the settled band (``ref.statuses.is_settled`` is the semantic
# column; no status NAME is compared), a stored figure that is not zero, and
# the entry sum disagreeing with it.
_FIGURE_DISAGREES_SQL = (
    "SELECT COUNT(*) FROM budget.transactions t "
    "JOIN ref.statuses s ON s.id = t.status_id "
    "WHERE s.is_settled "
    "AND t.settled_amount IS NOT NULL AND t.settled_amount <> 0 "
    f"AND t.settled_amount <> {_ENTRY_SUM}"
)

# Predicate 2: every covering movement, against its parent's stored figure.
# ``IS DISTINCT FROM`` so a NULL figure beside a movement counts.
_MOVEMENT_DISAGREES_SQL = (
    "SELECT COUNT(*) FROM budget.transaction_entries e "
    "JOIN budget.transactions t ON t.id = e.transaction_id "
    "WHERE e.covers_settlement "
    "AND t.settled_amount IS DISTINCT FROM e.amount"
)

# Predicate 3: out of the band, a non-zero ``corrected`` figure, no movement.
_UNCARRIED_CORRECTION_SQL = (
    "SELECT COUNT(*) FROM budget.transactions t "
    "JOIN ref.statuses s ON s.id = t.status_id "
    "WHERE NOT s.is_settled "
    f"AND t.settled_basis_id = {_basis('corrected')} "
    "AND COALESCE(t.settled_amount, 0) <> 0 "
    f"AND NOT {_MOVEMENT_EXISTS}"
)

_SETTLED_ROWS_SQL = (
    "SELECT COUNT(*) FROM budget.transactions t "
    "JOIN ref.statuses s ON s.id = t.status_id WHERE s.is_settled"
)

# The downgrade's one refusal: a row outside the band carrying a settle day
# and no movement would take no basis below and fail the re-created
# ``ck_transactions_settle_day_needs_a_record``.
_DATED_UNSETTLED_UNCOVERED_SQL = (
    "SELECT COUNT(*) FROM budget.transactions t "
    "JOIN ref.statuses s ON s.id = t.status_id "
    "WHERE NOT s.is_settled AND t.settled_on IS NOT NULL "
    f"AND NOT {_MOVEMENT_EXISTS}"
)

_SEED_SETTLEMENT_BASES_SQL = (
    "INSERT INTO ref.settlement_bases (name) VALUES "
    "('derived'), ('corrected'), ('purchases') "
    "ON CONFLICT (name) DO NOTHING"
)

# The downgrade's backfill, arm (a): a row holding a covering movement takes
# its figure and the basis its source maps to.
_RESTORE_FROM_MOVEMENT_SQL = (
    "UPDATE budget.transactions t SET "
    "settled_amount = e.amount, "
    "settled_basis_id = CASE WHEN fs.name = 'resolved' "
    f"THEN {_basis('derived')} ELSE {_basis('corrected')} END "
    "FROM budget.transaction_entries e "
    "JOIN ref.movement_figure_sources fs ON fs.id = e.figure_source_id "
    "WHERE e.transaction_id = t.id AND e.covers_settlement"
)

# Arm (b): a settled row holding no movement records its entries.
_RESTORE_PURCHASES_BASIS_SQL = (
    "UPDATE budget.transactions t SET "
    f"settled_amount = NULL, settled_basis_id = {_basis('purchases')} "
    "FROM ref.statuses s "
    "WHERE s.id = t.status_id AND s.is_settled "
    f"AND NOT {_MOVEMENT_EXISTS}"
)


def _refuse_if_any(bind, sql: str, sentence: str) -> None:
    """Refuse with the count and *sentence* when *sql* counts anything.

    Args:
        bind: A SQLAlchemy connection.
        sql: A ``SELECT COUNT(*)`` statement.
        sentence: What the count means and how it is repaired; the count is
            prefixed and the diagnostic query appended.
    """
    count = bind.execute(sa.text(sql)).scalar()
    if count:
        raise RuntimeError(f"{count} {sentence}  Diagnose with: {sql}")


def refuse_lossy_rows(bind) -> None:
    """Refuse the drop while any row's columns say what its movements do not.

    **Module-level so a test can DRIVE each refusal** (the chain's own
    pattern, ``ad573b07bede.refuse_unanswerable_rows``).  The three
    predicates of the module docstring, in its order; the 2026-09-19
    production restore holds zero of each.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming the count, the repair and the diagnostic query.
    """
    _refuse_if_any(
        bind, _FIGURE_DISAGREES_SQL,
        "settled budget.transactions row(s) store a non-zero figure that "
        "differs from the sum of the row's entries, which is what every "
        "reader has answered since plan step balance:X-bi-4b-1; dropping the "
        "column would lose a figure the balance once showed.  Revert each "
        "and re-settle it at the real figure through the full-edit popover's "
        "Actual box; then re-run.",
    )
    _refuse_if_any(
        bind, _MOVEMENT_DISAGREES_SQL,
        "covering movement(s) disagree with the parent row's stored figure "
        "(NULL or $0.00 beside a movement counts): the status seam writes "
        "both from one value, so each was written around it.  Revert each "
        "row and re-settle it at the real figure through the full-edit "
        "popover's Actual box; then re-run.",
    )
    _refuse_if_any(
        bind, _UNCARRIED_CORRECTION_SQL,
        "budget.transactions row(s) outside the settled band retain a "
        "non-zero corrected figure that no covering movement carries, so "
        "nothing would honour it once the column goes (ruling R-BAL82).  "
        "Re-settle each at that figure through the full-edit popover, then "
        "revert it; then re-run.",
    )


def upgrade():
    """Refuse the lossy, then drop the columns, their CHECKs, FK and table."""
    bind = op.get_bind()
    refuse_lossy_rows(bind)
    settled = bind.execute(sa.text(_SETTLED_ROWS_SQL)).scalar()
    for name in (
        "ck_transactions_settled_amount",
        "ck_transactions_settled_amount_needs_basis",
        "ck_transactions_settle_day_needs_a_record",
    ):
        op.drop_constraint(name, "transactions", type_="check", schema="budget")
    op.drop_constraint(
        "fk_transactions_settled_basis_id", "transactions",
        type_="foreignkey", schema="budget",
    )
    op.drop_column("transactions", "settled_basis_id", schema="budget")
    op.drop_column("transactions", "settled_amount", schema="budget")
    op.drop_table("settlement_bases", schema="ref")
    # The count is the migration's own measurement, printed so the operator
    # can compare it with the clone rehearsal's.
    print(
        "X-bi-4b-2: dropped settled_amount / settled_basis_id and "
        f"ref.settlement_bases; {settled} settled row(s) read their entries."
    )


def downgrade():
    """Re-create the catalogue and the columns, rebuilt from the movements."""
    bind = op.get_bind()
    _refuse_if_any(
        bind, _DATED_UNSETTLED_UNCOVERED_SQL,
        "budget.transactions row(s) outside the settled band carry a settle "
        "day and no covering movement, a state "
        "ck_transactions_settle_day_needs_a_record refuses to store and no "
        "seam writes.  Revert each through its full-edit popover, which "
        "clears the day; then re-run.",
    )
    op.create_table(
        "settlement_bases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_SETTLEMENT_BASES_SQL)
    op.add_column(
        "transactions",
        sa.Column("settled_amount", sa.Numeric(precision=12, scale=2), nullable=True),
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
    op.execute(_RESTORE_FROM_MOVEMENT_SQL)
    op.execute(_RESTORE_PURCHASES_BASIS_SQL)
    op.create_check_constraint(
        "ck_transactions_settled_amount", "transactions",
        "settled_amount IS NULL OR settled_amount >= 0",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_settled_amount_needs_basis", "transactions",
        "settled_amount IS NULL OR settled_basis_id IS NOT NULL",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_settle_day_needs_a_record", "transactions",
        "settled_on IS NULL OR settled_basis_id IS NOT NULL",
        schema="budget",
    )
