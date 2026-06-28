"""create journal_entries + account_postings ledger tables + backfill settled transfers

Revision ID: db239773c2fd
Revises: b82538084d24
Create Date: 2026-06-28 16:36:14.205407

Review: solo developer, 2026-06-28 (Build-Order Step 2, Commit 3; two new
audited tables ``budget.journal_entries`` + ``budget.account_postings`` and
the new deferred balanced-journal constraint trigger -- the one genuinely
new DB mechanism in the posting-ledger work)

Build-Order Step 2, Commit 3 (posting ledger + chart of accounts, piloted on
transfers; see
``docs/audits/balance_architecture/implementation_plan_posting_ledger_transfers.md``).

Creates the append-only double-entry ledger -- ``budget.journal_entries``
(event headers) and ``budget.account_postings`` (signed, debit-positive
legs) -- enforces the per-entry sum-to-zero / at-least-two-legs invariant
with a deferred constraint trigger, and backfills one balanced journal entry
per historical settled transfer so the reconciliation oracle (Commit 6) is
production-wide.  Nothing reads postings yet; every balance still flows
through the ``balance_at`` seam over ``budget.transactions``.

Five steps in order:

  1. **Create budget.journal_entries.**  Columns and constraints match the
     SQLAlchemy model in ``app/models/journal_entry.py`` exactly (the DDL was
     produced by ``flask db migrate`` against that model, so a future
     autogenerate run yields an empty diff): NOT NULL ``user_id`` /
     ``scenario_id`` / ``pay_period_id`` (all CASCADE -- a deleted tenancy
     disposes of its entries), ``entry_date``, a RESTRICT ``source_kind_id``
     FK into ``ref.posting_sources``, a NULLABLE SET-NULL ``transfer_id`` FK
     (the immutable posted fact survives a source-transfer delete), and the
     ``(user, scenario, period)`` + partial ``transfer_id`` indexes.

  2. **Create budget.account_postings.**  NOT NULL CASCADE FKs to
     ``journal_entries`` and ``ledger_accounts``, a RESTRICT
     ``posting_kind_id`` FK into ``ref.posting_kinds``, the single SIGNED
     ``amount`` Numeric(12,2) with ``CHECK (amount <> 0)``, and the per-entry
     + per-ledger indexes.

  3. **Attach the audit triggers.**  Manual DROP TRIGGER IF EXISTS + CREATE
     TRIGGER for both new tables, following the ``d3d25212504b`` /
     ``b82538084d24`` precedent (NOT ``apply_audit_infrastructure``).  The
     shared ``system.audit_trigger_func`` already exists from the rebuild
     migration ``a5be2a99ea14`` earlier in the chain; that earlier migration
     re-runs ``apply_audit_infrastructure`` against the CURRENT in-code
     ``AUDITED_TABLES`` -- which now references both new tables -- but its
     CREATE TRIGGER is guarded by an ``IF EXISTS`` check against
     ``pg_class`` and quietly no-ops on a from-scratch replay where these
     tables do not exist yet.  This narrow manual attach is what guarantees
     the triggers land.  ``EXPECTED_TRIGGER_COUNT = len(AUDITED_TABLES)``
     auto-bumps so the entrypoint health check picks up the new total.

  4. **Apply the balanced-journal constraint trigger.**
     ``apply_posting_infrastructure(op.execute)`` creates
     ``budget.assert_journal_entry_balanced`` and the deferred
     ``ck_account_postings_balanced`` constraint trigger (per-entry
     ``SUM(amount) = 0`` and ``COUNT(*) >= 2``, validated at COMMIT).  The
     table created in step 2 must exist first -- it does, in the same
     migration.  See the import note below for why this migration may
     import that one ``app`` module.

  5. **Backfill historical settled transfers.**  For every
     ``budget.transfers`` row whose status ``is_settled = TRUE`` and
     ``is_deleted = FALSE`` and that has no journal entry yet, insert one
     balanced entry plus two legs (see :func:`_backfill_settled_transfers`).
     Idempotent via ``NOT EXISTS`` on a prior entry for that ``transfer_id``.

**Import policy -- why this migration may import ``app``.**  The strict
self-contained policy of the ``d3d25212504b`` loan-anchor migration exists to
avoid a circular bootstrap deadlock with ``ref_cache`` and the application
service layer.  Importing :func:`app.posting_infrastructure.apply_posting_infrastructure`
/ :func:`~app.posting_infrastructure.remove_posting_infrastructure` is safe
and deliberate, exactly as the audit rebuild migration ``a5be2a99ea14``
imports ``apply_audit_infrastructure``: ``app.posting_infrastructure`` has no
``app`` or third-party dependencies beyond ``typing`` (no models, no
``ref_cache``, no services), so it cannot deadlock, and centralising the
trigger SQL there is the DRY single-source it shares with
``scripts/init_database`` and ``scripts/build_test_template``.  Inlining the
SQL here would duplicate it across three callers -- the exact drift
``app.audit_infrastructure`` was created to prevent.  The BACKFILL stays
fully self-contained (raw SQL against the catalog, no model / ``ref_cache``
imports), since it runs below the ref-cache layer; string-name lookups on
``ref.posting_sources`` / ``ref.posting_kinds`` are the documented migration
exception to the IDs-for-logic rule.

**Backfill amount = the shadow's effective amount, not the transfer amount.**
The reconciliation oracle (plan Section 6) reconciles posting sums against
the SETTLED TRANSFER SHADOWS' ``effective_amount``
(``COALESCE(actual_amount, estimated_amount)`` -- the value the balance
calculator uses), not against ``transfers.amount``.  These differ when a
shadow carries an ``actual_amount`` (the grid shadow-edit path forwards one
through ``transfer_service.update_transfer``), so the backfill posts each
leg's ``effective_amount`` read from the respective shadow.  On today's data
the two are identical (no settled transfer has a divergent ``actual_amount``),
but ``effective_amount`` is the correct, future-proof choice and makes the
entry balance by construction; a divergence between the two shadows (a
Transfer-Invariant-3 violation) would unbalance the entry and the deferred
trigger would reject the whole migration -- the right fail-loud signal.

**entry_date is the UTC civil date of the shadow's ``paid_at``.**  ``paid_at``
lives on the shadow transactions (the ``Transfer`` model has no such column),
synced identically to both shadows.  It is converted at UTC
(``(paid_at AT TIME ZONE 'UTC')::date``) for determinism -- a bare
``::date`` cast depends on the session ``TimeZone`` GUC -- and matches the
DB's UTC storage convention.  Historical settled transfers can carry a NULL
``paid_at`` (settled before the ``paid_at`` sync was added), so the entry
date falls back to the pay period's ``start_date``; the column is NOT NULL,
so this fallback is load-bearing, not cosmetic.

**Downgrade.**  Removes the posting infrastructure (the constraint trigger
and its function), then drops both tables; PostgreSQL drops each table's
audit trigger as a dependent object with it.  Reversible: the backfilled
entries are fully reproducible from the settled transfers on the next
upgrade, so dropping them loses nothing.  Per the ``loan_anchor_events``
caveat, any go-forward postings emitted between the upgrade and the
downgrade are NOT preserved across the downgrade (the tables themselves are
dropped); the historical backfill regenerates identically on re-upgrade.
"""
from decimal import Decimal

from alembic import op
import sqlalchemy as sa

from app.posting_infrastructure import (
    apply_posting_infrastructure,
    remove_posting_infrastructure,
)


# Revision identifiers, used by Alembic.
revision = 'db239773c2fd'
down_revision = 'b82538084d24'
branch_labels = None
depends_on = None


# Enumerate every settled, non-deleted transfer that still needs a journal
# entry, resolving in one query everything the per-row inserts below need:
# both legs' ledger accounts (joined on the real account_id), both legs'
# effective amounts (COALESCE(actual, estimated) -- the value the balance
# calculator and the reconciliation oracle use), the entry's civil date
# (the shadow ``paid_at`` at UTC, falling back to the period start when a
# historical settled shadow has no ``paid_at``), and the human description.
#
# The two shadows are identified by ``account_id``: the expense shadow lives
# on the from-account, the income shadow on the to-account, and a transfer's
# from/to accounts are guaranteed distinct (``ck_transfers_different_accounts``),
# so each INNER JOIN matches exactly one non-deleted shadow (one active
# shadow per (transfer, type) is enforced by
# ``uq_transactions_transfer_type_active``).  A transfer missing a shadow
# (a Transfer-Invariant-1 violation) is excluded by the inner joins rather
# than producing a one-legged entry; the Commit-6 oracle is the backstop.
#
# Idempotent via ``NOT EXISTS`` on a prior entry for the transfer.  The
# ``effective <> 0`` filter skips a settled transfer whose actual amount is
# zero (no money moved): a zero leg is forbidden by
# ``ck_account_postings_amount_nonzero`` and contributes nothing to the
# oracle either way, so omitting the entry keeps both sides at zero.
_SETTLED_TRANSFER_BACKFILL_SQL = (
    "SELECT t.id AS transfer_id, "
    "       t.user_id, t.scenario_id, t.pay_period_id, "
    "       ledger_from.id AS from_ledger_id, "
    "       ledger_to.id AS to_ledger_id, "
    "       COALESCE(sf.actual_amount, sf.estimated_amount) AS from_effective, "
    "       COALESCE(st_.actual_amount, st_.estimated_amount) AS to_effective, "
    "       COALESCE((sf.paid_at AT TIME ZONE 'UTC')::date, pp.start_date) "
    "           AS entry_date, "
    "       LEFT('Transfer: ' || af.name || ' to ' || att.name, 200) "
    "           AS description "
    "  FROM budget.transfers t "
    "  JOIN ref.statuses sref ON sref.id = t.status_id "
    "  JOIN budget.pay_periods pp ON pp.id = t.pay_period_id "
    "  JOIN budget.accounts af ON af.id = t.from_account_id "
    "  JOIN budget.accounts att ON att.id = t.to_account_id "
    "  JOIN budget.ledger_accounts ledger_from "
    "    ON ledger_from.account_id = t.from_account_id "
    "  JOIN budget.ledger_accounts ledger_to "
    "    ON ledger_to.account_id = t.to_account_id "
    "  JOIN budget.transactions sf "
    "    ON sf.transfer_id = t.id "
    "   AND sf.account_id = t.from_account_id "
    "   AND sf.is_deleted = FALSE "
    "  JOIN budget.transactions st_ "
    "    ON st_.transfer_id = t.id "
    "   AND st_.account_id = t.to_account_id "
    "   AND st_.is_deleted = FALSE "
    " WHERE sref.is_settled = TRUE "
    "   AND t.is_deleted = FALSE "
    "   AND COALESCE(sf.actual_amount, sf.estimated_amount) <> 0 "
    "   AND NOT EXISTS ( "
    "       SELECT 1 FROM budget.journal_entries je "
    "        WHERE je.transfer_id = t.id "
    "   ) "
    " ORDER BY t.id"
)


# Insert one journal entry header, returning its generated id so the two
# legs can reference it.  ``source_kind_id`` / ``transfer_id`` are bound
# from the resolved ids; the rest come from the enumeration row.
_INSERT_ENTRY_SQL = (
    "INSERT INTO budget.journal_entries "
    "    (user_id, scenario_id, pay_period_id, entry_date, "
    "     source_kind_id, transfer_id, description) "
    "VALUES (:user_id, :scenario_id, :pay_period_id, :entry_date, "
    "        :source_kind_id, :transfer_id, :description) "
    "RETURNING id"
)


# Insert one signed posting leg.  The caller passes the already-signed
# amount (negative for the from leg, positive for the to leg).
_INSERT_POSTING_SQL = (
    "INSERT INTO budget.account_postings "
    "    (journal_entry_id, ledger_account_id, amount, posting_kind_id) "
    "VALUES (:journal_entry_id, :ledger_account_id, :amount, :posting_kind_id)"
)


def _backfill_settled_transfers(connection):
    """Post one balanced journal entry per historical settled transfer.

    For every settled (``status.is_settled``), non-deleted transfer that has
    no journal entry yet (see :data:`_SETTLED_TRANSFER_BACKFILL_SQL`), insert
    one :class:`budget.journal_entries` header plus two
    :class:`budget.account_postings` legs:

    * the **from** leg, on the from-account's ledger account, with the
      NEGATED effective amount of the expense shadow (a credit: money out);
    * the **to** leg, on the to-account's ledger account, with the POSITIVE
      effective amount of the income shadow (a debit: money in).

    Both effective amounts equal the parent transfer's settled amount
    (Transfer Invariant 3), so the entry sums to zero by construction; the
    deferred ``ck_account_postings_balanced`` trigger validates that at the
    migration's COMMIT.  Raw SQL only (no ``app`` models / ``ref_cache``) so
    the backfill is self-contained; the two ``ref`` ids are resolved once by
    string name (the documented migration exception to IDs-for-logic).

    Idempotent: the enumeration's ``NOT EXISTS`` guard skips any transfer
    that already has an entry, so a re-run after a partial failure inserts
    nothing new.

    Args:
        connection: SQLAlchemy bind from ``op.get_bind()``.

    Returns:
        list[int]: the ``transfer_id`` of every transfer a new entry was
        posted for.  Returned for the migration's own logging and for test
        introspection.
    """
    source_kind_id = connection.execute(sa.text(
        "SELECT id FROM ref.posting_sources WHERE name = 'transfer'"
    )).scalar()
    posting_kind_id = connection.execute(sa.text(
        "SELECT id FROM ref.posting_kinds WHERE name = 'transfer'"
    )).scalar()

    rows = connection.execute(
        sa.text(_SETTLED_TRANSFER_BACKFILL_SQL)
    ).fetchall()

    posted = []
    for row in rows:
        entry_id = connection.execute(sa.text(_INSERT_ENTRY_SQL), {
            "user_id": row.user_id,
            "scenario_id": row.scenario_id,
            "pay_period_id": row.pay_period_id,
            "entry_date": row.entry_date,
            "source_kind_id": source_kind_id,
            "transfer_id": row.transfer_id,
            "description": row.description,
        }).scalar()

        # From leg: money leaving the from-account -> a credit -> negative.
        connection.execute(sa.text(_INSERT_POSTING_SQL), {
            "journal_entry_id": entry_id,
            "ledger_account_id": row.from_ledger_id,
            "amount": -Decimal(str(row.from_effective)),
            "posting_kind_id": posting_kind_id,
        })
        # To leg: money entering the to-account -> a debit -> positive.
        connection.execute(sa.text(_INSERT_POSTING_SQL), {
            "journal_entry_id": entry_id,
            "ledger_account_id": row.to_ledger_id,
            "amount": Decimal(str(row.to_effective)),
            "posting_kind_id": posting_kind_id,
        })
        posted.append(row.transfer_id)

    return posted


def upgrade():
    """Create the ledger tables + triggers; backfill historical settled transfers.

    Five-step forward migration (see module docstring for the full
    rationale).  Each step is idempotent so the migration can re-run after a
    partial failure without duplicating a table, trigger, or entry.
    """
    # ── Step 1: budget.journal_entries ───────────────────────────────────
    op.create_table(
        'journal_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scenario_id', sa.Integer(), nullable=False),
        sa.Column('pay_period_id', sa.Integer(), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('source_kind_id', sa.Integer(), nullable=False),
        sa.Column('transfer_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['pay_period_id'], ['budget.pay_periods.id'],
            name='fk_journal_entries_pay_period_id', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['scenario_id'], ['budget.scenarios.id'],
            name='fk_journal_entries_scenario_id', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['source_kind_id'], ['ref.posting_sources.id'],
            name='fk_journal_entries_source_kind_id', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['transfer_id'], ['budget.transfers.id'],
            name='fk_journal_entries_transfer_id', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        schema='budget',
    )
    op.create_index(
        'idx_journal_entries_transfer', 'journal_entries', ['transfer_id'],
        unique=False, schema='budget',
        postgresql_where=sa.text('transfer_id IS NOT NULL'),
    )
    op.create_index(
        'idx_journal_entries_user_scenario_period', 'journal_entries',
        ['user_id', 'scenario_id', 'pay_period_id'],
        unique=False, schema='budget',
    )

    # ── Step 2: budget.account_postings ──────────────────────────────────
    op.create_table(
        'account_postings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('journal_entry_id', sa.Integer(), nullable=False),
        sa.Column('ledger_account_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('posting_kind_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.CheckConstraint(
            'amount <> 0', name='ck_account_postings_amount_nonzero',
        ),
        sa.ForeignKeyConstraint(
            ['journal_entry_id'], ['budget.journal_entries.id'],
            name='fk_account_postings_journal_entry_id', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['ledger_account_id'], ['budget.ledger_accounts.id'],
            name='fk_account_postings_ledger_account_id', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['posting_kind_id'], ['ref.posting_kinds.id'],
            name='fk_account_postings_posting_kind_id', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        schema='budget',
    )
    op.create_index(
        'idx_account_postings_entry', 'account_postings',
        ['journal_entry_id'], unique=False, schema='budget',
    )
    op.create_index(
        'idx_account_postings_ledger', 'account_postings',
        ['ledger_account_id'], unique=False, schema='budget',
    )

    # ── Step 3: attach the audit triggers ────────────────────────────────
    # DROP IF EXISTS + CREATE makes each attach idempotent against a re-run.
    # Trigger name ``audit_<table>`` matches the convention the entrypoint
    # trigger-count health check enumerates (``tgname LIKE 'audit_%'``).
    op.execute(
        "DROP TRIGGER IF EXISTS audit_journal_entries "
        "ON budget.journal_entries"
    )
    op.execute(
        "CREATE TRIGGER audit_journal_entries "
        "AFTER INSERT OR UPDATE OR DELETE ON budget.journal_entries "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS audit_account_postings "
        "ON budget.account_postings"
    )
    op.execute(
        "CREATE TRIGGER audit_account_postings "
        "AFTER INSERT OR UPDATE OR DELETE ON budget.account_postings "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )

    # ── Step 4: balanced-journal constraint trigger ──────────────────────
    apply_posting_infrastructure(op.execute)

    # ── Step 5: backfill historical settled transfers ────────────────────
    _backfill_settled_transfers(op.get_bind())


def downgrade():
    """Remove the posting infrastructure and drop both ledger tables.

    Reversible: the backfilled entries are fully reproducible from the
    settled transfers on the next upgrade.  ``remove_posting_infrastructure``
    drops the balanced constraint trigger and its function first; then the
    tables drop (PostgreSQL drops each table's ``audit_<table>`` trigger as a
    dependent object with it).  ``account_postings`` is dropped before
    ``journal_entries`` to satisfy the FK dependency.
    """
    remove_posting_infrastructure(op.execute)

    op.drop_index(
        'idx_account_postings_ledger', table_name='account_postings',
        schema='budget',
    )
    op.drop_index(
        'idx_account_postings_entry', table_name='account_postings',
        schema='budget',
    )
    op.drop_table('account_postings', schema='budget')

    op.drop_index(
        'idx_journal_entries_user_scenario_period',
        table_name='journal_entries', schema='budget',
    )
    op.drop_index(
        'idx_journal_entries_transfer', table_name='journal_entries',
        schema='budget', postgresql_where=sa.text('transfer_id IS NOT NULL'),
    )
    op.drop_table('journal_entries', schema='budget')
