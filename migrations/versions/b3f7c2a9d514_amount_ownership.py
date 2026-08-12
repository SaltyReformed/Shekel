"""the column says who owns a row's amount

Plan step **X-au-c1** of ``docs/audits/balance_architecture/README.md`` section
5 -- the SCHEMA half of ruling **R-FI**: *a row's amount is either its OWN -- a
human authored the figure, or the money moved -- or it is DERIVED, and a derived
amount is not stored at all.*

Review: Josh, 2026-08-12 -- APPROVED: the leaf split, the RELATION-shaped
discriminator (ruling R-FK), and the nullability, which the step's own
specification in section 5 already states.  OFFERED WITH THIS COMMIT and NOT
covered by that approval: the two constraints below that impose a NEW structural
rule on a populated financial table rather than following from the amount model
-- ``ck_transactions_one_pricing_link`` and ``ck_transfers_adhoc_owns_amount``.
Both make an already-documented CONVENTION structural, both were measured against
every production row before being written (0 violations), and each is a two-line
revert if the answer is no.

Four things, and no row's figure moves:

  1. **ref.amount_sources**, seeded ``template`` and ``parent_transfer`` -- WHICH
     RELATION states a row's amount when the row does not state it itself.  There
     is deliberately no ``own`` row: owning an amount is the ABSENCE of a source,
     which is what lets the pairing CHECK below be written over two NULL-nesses
     instead of freezing a ref id into the schema.
  2. **``amount_source_id``** on ``budget.transactions`` and
     ``budget.transfers`` -- nullable, FK ``ON DELETE RESTRICT``.
  3. **``transactions.estimated_amount`` and ``transfers.amount`` become
     NULLABLE.**  Safe only because (4) lands in the same migration: after it, a
     NULL figure is legal exactly when a source says the figure is derived.
  4. **The constraints.**  ``ck_transactions_amount_ownership`` /
     ``ck_transfers_amount_ownership`` pair the source with the presence of a
     figure; ``ck_transactions_one_pricing_link`` and
     ``ck_transfers_adhoc_owns_amount`` each make a documented convention
     structural, and the second of those is what keeps
     ``uq_transfers_adhoc_dedupe`` -- whose key includes ``amount`` -- working
     once that column can be NULL.

**NOTHING IS BACKFILLED, and that is the leaf boundary rather than an
omission.**  Every existing row keeps its figure and takes
``amount_source_id = NULL``, which is the true statement about it *today*: the
app still prices every row by writing its column.  So this migration moves no
money, and the pairing CHECK can be a BICONDITIONAL from its first day rather
than a one-sided rule tightened later.  The per-kind cutovers (plan steps X-au-d
salary, X-au-e template, X-au-f transfer, X-au-g loan payment, X-au-i CC
payback) are what declare a bucket derived and empty its column, one measurable
money movement each.

**Why the exclusivity CHECK is here.**  ``ck_transactions_one_pricing_link``
says a row is priced through at most one relation.  The balance README states
that exclusivity as a CONVENTION with nothing enforcing it, and the amount model
depends on it: a derived row's source names a relation, and a row holding two
links has two candidate answers with only dispatch ORDER to separate them.
Measured before imposing it, on a 2026-08-12 production clone at
``a9d3c15e7f42``: 997 rows, 606 template-linked, 342 transfer shadows, 21 CC
paybacks, 28 with no link, and **0 holding two** of the three.

**Why ``estimated_amount >= 0`` and ``amount > 0`` are left alone.**  A
comparison with NULL is UNKNOWN and a CHECK admits UNKNOWN, so both existing
constraints already tolerate the NULL; rewriting them into an explicit
``IS NULL OR`` form would be a drop-and-recreate of a financial constraint for
zero change in behaviour.  Each column's comment in the model says so, because a
reader meeting ``estimated_amount >= 0`` on a nullable column would otherwise
have to work out whether NULL passes.

**Not audited.**  ``ref.amount_sources`` is a read-only seed catalogue, so it is
deliberately excluded from ``app.audit_infrastructure.AUDITED_TABLES`` -- the
same inclusion criteria that keep ``ref.statuses`` and every other ref catalogue
out (only the multi-tenant ``ref.account_types`` is audited).  No trigger is
attached and ``EXPECTED_TRIGGER_COUNT`` is unchanged.  ``budget.transactions``
and ``budget.transfers`` are already audited, so their existing triggers record
the new column with no change here -- which is what makes a future declaration
change traceable in ``system.audit_log``.

**Inline seed rationale.**  The two rows are seeded in this migration rather than
deferred to the entrypoint's ``seed_reference_data`` pass, so ``ref_cache.init()``
resolves ``AmountSourceEnum`` immediately after a bare ``flask db upgrade`` -- an
enum member with no matching row is a fatal ``RuntimeError`` at app start.
``ON CONFLICT (name) DO NOTHING`` keeps it idempotent against a re-run and
against the entrypoint's later reseed, which carries the identical rows via
``app/ref_seeds.py``.  The duplication is the established project pattern
(``f5037400dc5e``, ``e7a4d95c2b18``): migrations run below the app layer and must
not import ``app`` code.

**No explicit ids**, so the identity sequence stays in step with the table (the
defect migration ``1dc0e7a1b9e4`` left behind).  Nothing depends on a particular
id: every reader resolves these through ``ref_cache``.

**Downgrade** is complete and refuses rather than destroying.  It drops the
constraints and the two columns, then restores NOT NULL -- and if any row's
figure is NULL by then (i.e. a later cutover ran and this migration is being
downgraded out from under it) it raises ``RuntimeError`` naming the offending ids
rather than inventing a figure to satisfy the constraint.  Downgrading past a
cutover means downgrading that cutover first, which is what the chain order
already says.

Revision ID: b3f7c2a9d514
Revises: a9d3c15e7f42
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b3f7c2a9d514"
down_revision = "a9d3c15e7f42"
branch_labels = None
depends_on = None


# The rows ``AmountSourceEnum`` names.  Written as literal SQL rather than
# built from a Python tuple, and that is not style: the cross-migration
# inline-seed guard (``tests/test_models/test_posting_ref_seed_parity.py``)
# scans this chain for each enum value as a SINGLE-QUOTED literal inside an
# ``INSERT INTO`` its own ref table, so a value assembled at run time from a
# double-quoted tuple would be invisible to it and the dual seed would go
# unguarded for this table.
_SEED_AMOUNT_SOURCES_SQL = (
    "INSERT INTO ref.amount_sources (name) VALUES "
    "('template'), "
    "('parent_transfer') "
    "ON CONFLICT (name) DO NOTHING"
)

# ``(table, amount column)`` for the two tables the model covers.  The pairing
# CHECK is the same rule on both, so it is written once and applied twice --
# a second spelling of it is exactly what ruling R-FI is about.
_AMOUNT_COLUMNS = (
    ("transactions", "estimated_amount"),
    ("transfers", "amount"),
)


def upgrade():
    """Create the source catalogue, add the column, and pair the two.

    Order is load-bearing: the ref table exists before the FK targets it, and the
    ownership CHECK is created in the same transaction that drops NOT NULL, so
    there is no window in which a figure may be absent with nothing saying why.
    """
    op.create_table(
        "amount_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_AMOUNT_SOURCES_SQL)

    for table, amount_column in _AMOUNT_COLUMNS:
        op.add_column(
            table,
            sa.Column("amount_source_id", sa.Integer(), nullable=True),
            schema="budget",
        )
        op.create_foreign_key(
            f"fk_{table}_amount_source_id",
            table, "amount_sources",
            ["amount_source_id"], ["id"],
            source_schema="budget", referent_schema="ref",
            ondelete="RESTRICT",
        )
        op.alter_column(
            table, amount_column,
            existing_type=sa.Numeric(precision=12, scale=2),
            nullable=True,
            schema="budget",
        )
        op.create_check_constraint(
            f"ck_{table}_amount_ownership",
            table,
            f"(amount_source_id IS NULL) = ({amount_column} IS NOT NULL)",
            schema="budget",
        )

    # An AD-HOC transfer owns its amount: no definition states a price for one,
    # so a declaration on it names a relation nothing can reach.  It also keeps
    # ``uq_transfers_adhoc_dedupe`` working -- that index includes ``amount`` and
    # PostgreSQL indexes NULLs as DISTINCT, so two ad-hoc transfers with a NULL
    # amount would both insert and the double-submit guard would be off.
    op.create_check_constraint(
        "ck_transfers_adhoc_owns_amount",
        "transfers",
        "amount_source_id IS NULL OR transfer_template_id IS NOT NULL",
        schema="budget",
    )

    # A row is priced through AT MOST ONE relation.  Transactions only: a
    # transfer carries a single pricing link (``transfer_template_id``), so there
    # is nothing to be exclusive about.
    op.create_check_constraint(
        "ck_transactions_one_pricing_link",
        "transactions",
        "(template_id IS NOT NULL)::int "
        "+ (transfer_id IS NOT NULL)::int "
        "+ (credit_payback_for_id IS NOT NULL)::int <= 1",
        schema="budget",
    )


def refuse_rows_without_a_figure(bind) -> None:
    """Refuse the downgrade when any row's amount column is already NULL.

    **The only non-DDL logic in this migration, and it is module-level so a test
    can drive it** -- the pattern the previous revision (``a9d3c15e7f42``) uses
    for its backfill, and the reason is the same: a guard nothing exercises is a
    guard nobody has seen work.  Called first by :func:`downgrade`, before any
    DDL, so a refused downgrade leaves the schema untouched.

    A NULL figure means a per-kind cutover (plan steps X-au-d..X-au-i) has
    already declared those rows derived.  Restoring ``NOT NULL`` would then need
    a figure this migration cannot know: the producer that prices the row lives
    in ``app/``, which a migration must not import, and inventing one would write
    a number nobody computed into a money column.  So it names the rows and
    stops.  Because the pairing CHECK is a BICONDITIONAL, ``amount IS NULL`` is
    exactly ``amount_source_id IS NOT NULL``, so this probe cannot miss a derived
    row.

    Args:
        bind: A SQLAlchemy connection to probe.

    Raises:
        RuntimeError: When any row in either table carries no figure, naming the
            first 20 ids and the diagnostic SELECT.
    """
    for table, amount_column in _AMOUNT_COLUMNS:
        ids = [
            str(row[0]) for row in bind.execute(
                sa.text(
                    f"SELECT id FROM budget.{table} "
                    f"WHERE {amount_column} IS NULL ORDER BY id LIMIT 20"
                )
            )
        ]
        if ids:
            raise RuntimeError(
                f"budget.{table}.{amount_column} is NULL on row(s) "
                f"{', '.join(ids)} (first 20), so this downgrade cannot restore "
                "NOT NULL without inventing a figure. Those rows have a "
                "DERIVED amount, which means a per-kind cutover (plan steps "
                "X-au-d..X-au-i) ran after this migration; downgrade that "
                "revision first, which re-materialises each row's figure from "
                "the producer that priced it. Diagnostic: SELECT id, "
                f"amount_source_id FROM budget.{table} WHERE {amount_column} "
                "IS NULL;"
            )


def downgrade():
    """Drop the pairing and restore NOT NULL, refusing to invent a figure.

    Raises:
        RuntimeError: From :func:`refuse_rows_without_a_figure`, when a later
            cutover has already declared some rows derived.
    """
    refuse_rows_without_a_figure(op.get_bind())

    op.drop_constraint(
        "ck_transactions_one_pricing_link", "transactions",
        type_="check", schema="budget",
    )
    op.drop_constraint(
        "ck_transfers_adhoc_owns_amount", "transfers",
        type_="check", schema="budget",
    )
    for table, amount_column in _AMOUNT_COLUMNS:
        op.drop_constraint(
            f"ck_{table}_amount_ownership", table,
            type_="check", schema="budget",
        )
        op.drop_constraint(
            f"fk_{table}_amount_source_id", table,
            type_="foreignkey", schema="budget",
        )
        op.drop_column(table, "amount_source_id", schema="budget")
        op.alter_column(
            table, amount_column,
            existing_type=sa.Numeric(precision=12, scale=2),
            nullable=False,
            schema="budget",
        )
    op.drop_table("amount_sources", schema="ref")
