"""The ledger takes shape C: the owner-bucket family, the transit kind, the movement source

Revision ID: c7d1e9a4b2f8
Revises: 9900b309f0b0
Create Date: 2026-09-21 10:30:00.000000

Plan step ``balance:X-bi-6-3``.  Rulings **R-BAL45** (a settled transfer is
TWO movements, each posted as its own journal entry against a
Transfers-in-transit clearing account on its own bank day), **R-BAL98** (WHERE
the re-book runs), **R-BAL99** (how the clearing account is held to one per
owner), **R-BAL101** (one movement writer) and **R-BAL102** (the loan payment
split is a derivation keyed by its period and day, linking no row; it re-ruled
**R-BAL100**).

**What this migration does, and what it deliberately does NOT.**  It is the
step's DATA BOUNDARY: it gives the schema and the reference catalogue what the
new posting writer needs, and it owns the downgrade teardown.  It does NOT
re-book a single posting.  The 19 settled transfers' one-entry-per-transfer
postings are reversed and re-posted per movement by the deploy's existing
first hook, ``scripts/init_database.py::resync_all_cash_postings_after_
migration``, which drives the go-forward ``posting_service.sync_transfer_
postings`` -- ONE producer, the same code every future settle runs, and no
SQL restatement of the sign rule, the contributing gate, the (period,
movement-day) key, the kinds, the link or the description (ruling **R-BAL98**;
the policy migrations ``db239773c2fd`` and ``e2a9f1c7b4d6`` state: this host
runs ``create_app(init_ref_cache=False)`` and imports no service).  Measured
on the 2026-09-20 13:59 production restore before ruling: on every one of the
38 movements the movement's day equals the old entry's day and its figure the
posted figure, so the re-book is byte-identical per (real account, day) and
the transit account nets to zero per transfer.

Four things change here:

1. **The owner-bucket family** (ruling **R-BAL99**).  ``budget.ledger_accounts``
   tells its kinds apart by which link column is set; the Uncategorized bucket
   and the deleted-category orphan have none and were told apart by the boolean
   ``is_fallback`` with its singleton ``uq_ledger_accounts_uncategorized
   (user_id, class_id) WHERE is_fallback``.  A Transfers-in-transit row is a
   third link-less kind, and a partial index cannot name a kind (a CHECK cannot
   subquery the ref table and the project never hardcodes its ids).  Rather
   than a flag per kind, the flag is generalised: ``is_owner_bucket`` marks a
   row that is the owner's ONE bucket for its (class, kind) with no account,
   category or loan behind it, and the singleton becomes
   ``uq_ledger_accounts_owner_bucket (user_id, class_id, kind_id) WHERE
   is_owner_bucket``.  The ``fallback`` guarantee is unchanged (one row of that
   kind per owner per Income / Expense class); orphans stay outside the key; a
   future bucket kind needs an enum member, a ref row and a resolver arm and
   no schema.  The shape CHECK is renamed with the column
   (``ck_ledger_accounts_owner_bucket_shape``); ``ck_ledger_accounts_loan_
   shape`` keeps its name and, being stored parsed, follows the rename by
   itself.  Every existing row keeps its value: the rename is a catalogue
   change, not a data change.
2. **The ``transit`` chart kind** -- the owner's Transfers-in-transit clearing
   account, Asset class.  Minted lazily by the chart resolver
   (``ledger_account_service.get_or_create_transit_ledger_account``) the first
   time an owner's transfer posts, exactly as the fallback is; NOT minted here,
   so an owner with no settled transfer (user 2 on the restore) never carries
   one.
3. **The ``transfer_movement`` posting source** -- ONE side of a settled
   transfer, a shadow's covering movement on its own bank day, linking
   ``journal_entries.transaction_entry_id`` exactly as ``purchase`` does.  The
   ``transfer`` source becomes LEGACY: the first deploy's resync reverses every
   entry that carries it, and the reversed pairs (netting zero at their own
   date) keep it until ``X-bi-6-5`` drops ``journal_entries.transfer_id``.
4. **The loan payment split links no row** (ruling **R-BAL102**).  Every
   ``loan_payment`` journal entry carried the loan-side income shadow's
   ``transaction_id``, the key its reconcile read it back by; the split is a
   derivation of the loan walk and is keyed like the loan's opening and
   true-ups now -- ``(source kind, pay_period_id, entry_date)`` on the loan's
   own chart rows -- so the link is CLEARED here (25 entries on the 2026-09-20
   restore, graded: none left carrying any of the three links).  Nothing is
   re-booked: every one of those entries already sits at its payment's period
   and visible day, which IS the new key, so the deploy's loan hook computes
   zero deltas over them (measured in the rehearsal).  ``transaction_id`` is
   thereby dead on every live entry too -- the ``transaction`` source has been
   at zero since ``balance:X-bi-4a`` -- and ``X-bi-6-5`` drops it with
   ``transfer_id``.

**Inline seed rationale.**  Both reference rows are seeded here (not deferred
to the entrypoint's ``seed_reference_data`` pass) so ``ref_cache.init()``
resolves the new ``LedgerAccountKindEnum.TRANSIT`` / ``PostingSourceEnum.
TRANSFER_MOVEMENT`` members immediately after a bare ``flask db upgrade`` -- a
member with no row is a fatal ``RuntimeError`` at app start.  ``ON CONFLICT
(name) DO NOTHING`` keeps the seed idempotent against a re-run and against the
entrypoint's later idempotent reseed (``app/ref_seeds.py`` carries the identical
names; the dual-seed pattern of ``f5037400dc5e`` / ``e6b4a2d8c713``).

**Downgrade.**  The reverse direction needs no app logic and MUST happen here.
First, by convention rather than by any key (it touches no transit row and no
reference row), delete every ``loan_payment`` journal entry WHOLE (the old
image's loan reconcile reads a payment's split by its shadow's
``transaction_id`` and would post a SECOND split beside an unlinked one,
tripping its own checked-projection assert; deleted, its second deploy hook
re-posts each split keyed by shadow and the assert holds).  Then, in the only
order the RESTRICT foreign keys and the balanced-entry invariant permit: every
``transfer_movement`` journal entry WHOLE (its legs cascade through
``fk_account_postings_journal_entry_id``; deleting the transit chart row first
would instead cascade ONE leg of each entry and leave the real-account leg
standing unbalanced), then the emptied ``transit`` chart rows, then the two
reference rows, then the index / CHECK / column back to their
``45f10b870c8b`` names and key.  The old image's first deploy then re-posts
the old one-entry shape from the transfer rows through ITS
``sync_transfer_postings`` (the legacy entries this release reversed net to
zero, so that reconcile posts each transfer's effect afresh at the income
shadow's day -- byte-identical per (real account, day) by the measurement
above), which is ``e2a9f1c7b4d6``'s downgrade doctrine.  Raw SQL throughout,
so the ORM's append-only guards (ORM-mediated only) do not interfere and the
balanced trigger (INSERT / UPDATE only) does not fire on the deletes.  The
counts are printed as the migration's own measurement in both directions.

**Not audited / self-contained.**  Both ``ref`` tables are read-only seed
catalogues outside ``AUDITED_TABLES``; ``budget.ledger_accounts`` and
``budget.journal_entries`` ARE audited, so the downgrade's deletes are captured
by their triggers.  This migration imports nothing from ``app``.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c7d1e9a4b2f8"
down_revision = "9900b309f0b0"
branch_labels = None
depends_on = None


#: The ``name`` values MUST match ``app/enums.py`` exactly
#: (``LedgerAccountKindEnum.TRANSIT``, ``PostingSourceEnum.TRANSFER_MOVEMENT``)
#: and the lists in ``app/ref_seeds.py``.
_SEED_TRANSIT_KIND_SQL = (
    "INSERT INTO ref.ledger_account_kinds (name) VALUES ('transit') "
    "ON CONFLICT (name) DO NOTHING"
)
_SEED_TRANSFER_MOVEMENT_SOURCE_SQL = (
    "INSERT INTO ref.posting_sources (name) VALUES ('transfer_movement') "
    "ON CONFLICT (name) DO NOTHING"
)

#: The chart's owner buckets after the rename: the fallbacks (the only flagged
#: rows at this revision's upgrade) and, once the writer has run, the transit
#: rows.  Printed as the upgrade's measurement.
_OWNER_BUCKETS_SQL = (
    "SELECT COUNT(*) FROM budget.ledger_accounts WHERE is_owner_bucket"
)

#: The loan payment split's link, cleared (thing 4).  Every link column, not
#: only the one the split carried, so the grade below can read "no link at
#: all" rather than "not that link".
_UNLINK_LOAN_PAYMENT_ENTRIES_SQL = (
    "UPDATE budget.journal_entries "
    "   SET transaction_id = NULL, transfer_id = NULL, "
    "       transaction_entry_id = NULL "
    " WHERE source_kind_id = ("
    "       SELECT id FROM ref.posting_sources WHERE name = 'loan_payment'"
    "       )"
    "   AND (transaction_id IS NOT NULL OR transfer_id IS NOT NULL "
    "        OR transaction_entry_id IS NOT NULL)"
)
_LINKED_LOAN_PAYMENT_ENTRIES_SQL = (
    "SELECT COUNT(*) FROM budget.journal_entries "
    " WHERE source_kind_id = ("
    "       SELECT id FROM ref.posting_sources WHERE name = 'loan_payment'"
    "       )"
    "   AND (transaction_id IS NOT NULL OR transfer_id IS NOT NULL "
    "        OR transaction_entry_id IS NOT NULL)"
)

#: Downgrade, in dependency order.  The entries first and WHOLE (see the
#: module docstring for why the chart row cannot go first).
_DELETE_LOAN_PAYMENT_ENTRIES_SQL = (
    "DELETE FROM budget.journal_entries "
    " WHERE source_kind_id = ("
    "       SELECT id FROM ref.posting_sources WHERE name = 'loan_payment'"
    "       )"
)
_DELETE_TRANSFER_MOVEMENT_ENTRIES_SQL = (
    "DELETE FROM budget.journal_entries "
    " WHERE source_kind_id = ("
    "       SELECT id FROM ref.posting_sources WHERE name = 'transfer_movement'"
    "       )"
)
_DELETE_TRANSIT_CHART_ROWS_SQL = (
    "DELETE FROM budget.ledger_accounts "
    " WHERE kind_id = ("
    "       SELECT id FROM ref.ledger_account_kinds WHERE name = 'transit'"
    "       )"
)
_DROP_TRANSFER_MOVEMENT_SOURCE_SQL = (
    "DELETE FROM ref.posting_sources WHERE name = 'transfer_movement'"
)
_DROP_TRANSIT_KIND_SQL = (
    "DELETE FROM ref.ledger_account_kinds WHERE name = 'transit'"
)


def upgrade():
    """Generalise the bucket flag, re-key its singleton, seed the two ref rows."""
    bind = op.get_bind()
    # The singleton index is DROPPED and CREATED rather than renamed: its key
    # gains ``kind_id``.  The column and the shape CHECK are renamed in place
    # (both keep their values and their stored expressions).
    op.drop_index(
        "uq_ledger_accounts_uncategorized",
        table_name="ledger_accounts", schema="budget",
    )
    op.alter_column(
        "ledger_accounts", "is_fallback",
        new_column_name="is_owner_bucket",
        existing_type=sa.Boolean(), existing_nullable=False,
        existing_server_default=sa.text("false"),
        schema="budget",
    )
    op.execute(
        "ALTER TABLE budget.ledger_accounts "
        "RENAME CONSTRAINT ck_ledger_accounts_fallback_shape "
        "TO ck_ledger_accounts_owner_bucket_shape"
    )
    op.create_index(
        "uq_ledger_accounts_owner_bucket",
        "ledger_accounts",
        ["user_id", "class_id", "kind_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text("is_owner_bucket"),
    )
    op.execute(_SEED_TRANSIT_KIND_SQL)
    op.execute(_SEED_TRANSFER_MOVEMENT_SOURCE_SQL)
    buckets = bind.execute(sa.text(_OWNER_BUCKETS_SQL)).scalar()
    unlinked = bind.execute(sa.text(_UNLINK_LOAN_PAYMENT_ENTRIES_SQL)).rowcount
    still_linked = bind.execute(
        sa.text(_LINKED_LOAN_PAYMENT_ENTRIES_SQL)
    ).scalar()
    if still_linked:
        raise RuntimeError(
            f"X-bi-6-3: {still_linked} loan_payment journal entr(y/ies) still "
            f"carry a source link after the unlink; the split is keyed by "
            f"no row (R-BAL102) and the migration refuses to leave one."
        )
    print(
        "X-bi-6-3: is_fallback -> is_owner_bucket, keyed (user, class, kind); "
        f"{buckets} owner bucket(s) carried over; 'transit' kind and "
        "'transfer_movement' source seeded.  The 19 settled transfers are "
        "re-booked per movement by the deploy's cash resync (R-BAL98).  "
        f"{unlinked} loan_payment split entr(y/ies) unlinked from their "
        "shadow (R-BAL102), 0 left linked; the deploy's loan hook computes "
        "zero deltas over them."
    )


def downgrade():
    """Delete the splits and the transit ledger whole, then restore the flag."""
    bind = op.get_bind()
    splits = bind.execute(sa.text(_DELETE_LOAN_PAYMENT_ENTRIES_SQL)).rowcount
    entries = bind.execute(
        sa.text(_DELETE_TRANSFER_MOVEMENT_ENTRIES_SQL)
    ).rowcount
    chart_rows = bind.execute(sa.text(_DELETE_TRANSIT_CHART_ROWS_SQL)).rowcount
    op.execute(_DROP_TRANSFER_MOVEMENT_SOURCE_SQL)
    op.execute(_DROP_TRANSIT_KIND_SQL)
    op.drop_index(
        "uq_ledger_accounts_owner_bucket",
        table_name="ledger_accounts", schema="budget",
    )
    op.execute(
        "ALTER TABLE budget.ledger_accounts "
        "RENAME CONSTRAINT ck_ledger_accounts_owner_bucket_shape "
        "TO ck_ledger_accounts_fallback_shape"
    )
    op.alter_column(
        "ledger_accounts", "is_owner_bucket",
        new_column_name="is_fallback",
        existing_type=sa.Boolean(), existing_nullable=False,
        existing_server_default=sa.text("false"),
        schema="budget",
    )
    # The ``45f10b870c8b``-era key exactly: ``(user_id, class_id) WHERE
    # is_fallback``.
    op.create_index(
        "uq_ledger_accounts_uncategorized",
        "ledger_accounts",
        ["user_id", "class_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text("is_fallback"),
    )
    print(
        f"X-bi-6-3 downgrade: deleted {splits} loan_payment journal "
        f"entr(y/ies) whole (the old image's loan hook re-posts them keyed by "
        f"shadow), {entries} transfer_movement journal entr(y/ies) whole and "
        f"{chart_rows} transit chart row(s); the old image's cash resync "
        "re-posts the one-entry shape."
    )
