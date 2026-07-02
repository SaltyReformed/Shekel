"""Loan genesis (opening / true-up) posting data boundary (read switch, Commit 4)

Revision ID: f3d6b1a8c2e4
Revises: d1b22f59ba5b
Create Date: 2026-07-01 20:30:00.000000

Review: solo developer, 2026-07-01 (the loan read switch, Commit 4; a
production-wide DATA boundary migration -- no schema change.  Its downgrade
removes the genesis opening / true-up correction entries and the per-loan
opening-equity ledger accounts so the Commit-1 reference-seed downgrade
(``d1b22f59ba5b``) is clean; its upgrade is an intentional no-op because the
forward population is booked at runtime by the go-forward wiring / the deploy
backfill hook, not by a migration upgrade -- see below.)

The loan read switch (see
``docs/audits/balance_architecture/implementation_plan_loan_read_switch.md``).

Commit 4 wires the genesis loan ledger at every go-forward chokepoint: a loan's
once-per-loan OPENING (``-original_principal`` onto the loan, its positive onto a
per-loan ``equity_opening`` account) and every balance TRUE-UP are posted as
balanced correction entries (source ``loan_opening`` / ``loan_trueup``, leg kind
``opening`` / ``trueup``), alongside the Step-4 payment splits.  Nothing reads
these yet -- every loan balance still flows through the resolver / ``balance_at``
seam (this step is write-only) -- but they exist the moment a loan is configured
or paid.

**Why this migration exists (the reversibility contract).**  The Commit-1 ref
seed (``d1b22f59ba5b``) added the ``loan_opening`` / ``loan_trueup`` posting
sources, the ``opening`` / ``trueup`` posting kinds, and the ``equity_opening``
ledger-account kind, and its downgrade DELETEs those five rows.  All three
columns that reference them --
``journal_entries.source_kind_id`` -> ``ref.posting_sources``,
``account_postings.posting_kind_id`` -> ``ref.posting_kinds``, and
``ledger_accounts.kind_id`` -> ``ref.ledger_account_kinds`` -- are
``ondelete='RESTRICT'``.  So once Commit 4 books any genesis posting, that
Commit-1 downgrade would fail on a RESTRICT violation.  The Commit-1 docstring
anticipates exactly this: the RESTRICT "blocks this DELETE until the higher
revisions are themselves downgraded first."  THIS revision is that higher
revision -- it anchors the runtime genesis data's teardown as the new head, so
booking never begins without a working downgrade.

**Why the upgrade posts nothing here (and where the forward population runs).**
The genesis corrections are produced by the money-critical running-balance walk
(``loan_posting_service.walk_loan_ledger``): an opening / true-up correction is
``owed_before - anchor_balance`` on the reset-aware balance, not a one-line SQL
formula.  Like the Step-4 payment backfill (``e2a9f1c7b4d6``), it cannot be
reproduced in the migration without duplicating that engine, and this migration
host runs ``create_app(init_ref_cache=False)`` (the ``3104f87`` bootstrap fix) so
``ref_cache`` is off during migrations.  So the forward population runs in the
go-forward wiring and the post-migration deploy hook
(``scripts/init_database.py::backfill_loan_payment_postings_after_migration`` ->
``loan_posting_service.backfill_all_loan_postings``), reconcile-to-target and
idempotent.

**Downgrade (the reason this migration exists).**  Deletes every
``source_kind = loan_opening`` / ``loan_trueup`` journal entry FIRST (both these
runtime corrections and any booked after the upgrade; their legs -- on the
loan's linked ledger AND its ``equity_opening`` account -- cascade via
``fk_account_postings_journal_entry_id``), then the per-loan ``equity_opening``
ledger accounts (posting-free once the entries are gone).  Ordering: this runs
BEFORE the Commit-1 downgrade (``d1b22f59ba5b``), which then deletes the five ref
rows cleanly -- in particular the ``equity_opening`` accounts are gone before
Commit-1 deletes the ``equity_opening`` LEDGER KIND they reference under RESTRICT.
It also runs before the Step-4 boundary (``e2a9f1c7b4d6``), whose
``loan_account_id IS NOT NULL`` per-loan account delete then only reaches the
interest / escrow / refund rows -- the ``equity_opening`` per-loan accounts are
already removed here.  The linked cash-mirror ledger (``account_id`` set,
``loan_account_id`` NULL), the Step-2 cash entries, and the Step-4 payment
corrections are untouched.  Raw SQL, so the append-only ORM guards and the
balanced trigger (INSERT / UPDATE only) do not fire.  Reversible: a re-upgrade is
a no-op and the go-forward wiring / deploy hook regenerate every genesis
correction identically from the loan's anchors + settled payments.

**No-op on a fresh database.**  The upgrade does nothing, so a fresh
``flask db upgrade base->head`` (a template rebuild or brand-new deploy) reaches
head with no genesis postings; the downgrade's source / kind names were seeded by
the lower-revision Commit-1 migration (``d1b22f59ba5b``) and so are present
whenever the downgrade runs.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'f3d6b1a8c2e4'
down_revision = 'd1b22f59ba5b'
branch_labels = None
depends_on = None


# Downgrade SQL.  Resolve the genesis source / kind ids by unique name (the
# documented migration exception to IDs-for-logic; the names were seeded by the
# Commit-1 migration d1b22f59ba5b), delete the genesis entries (legs cascade),
# then drop the per-loan opening-equity ledger accounts (posting-free once the
# entries are gone).
_SELECT_GENESIS_SOURCE_IDS_SQL = (
    "SELECT id FROM ref.posting_sources "
    "WHERE name IN ('loan_opening', 'loan_trueup')"
)
_SELECT_EQUITY_OPENING_KIND_ID_SQL = (
    "SELECT id FROM ref.ledger_account_kinds WHERE name = 'equity_opening'"
)
_DELETE_GENESIS_ENTRIES_SQL = (
    "DELETE FROM budget.journal_entries WHERE source_kind_id IN :source_kind_ids"
)
# The per-loan opening-equity accounts carry the ``equity_opening`` kind and
# nothing else does; deleting by kind_id leaves the linked cash-mirror row (kind
# ``linked``) and the interest / escrow / refund rows alone.
_DELETE_EQUITY_OPENING_ACCOUNTS_SQL = (
    "DELETE FROM budget.ledger_accounts WHERE kind_id = :kind_id"
)


def _require_genesis_source_ids(connection):
    """Resolve the ``loan_opening`` / ``loan_trueup`` source ids, failing loud if absent.

    The source names are seeded by the Commit-1 migration (``d1b22f59ba5b``, a
    lower revision), so both are present whenever this downgrade runs.  A missing
    row is a broken bootstrap invariant -- raise rather than delete a partial set
    (which would strand one genesis kind's entries and then fail the Commit-1
    RESTRICT anyway).

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            test session) exposing ``execute``.

    Returns:
        tuple[int, ...] -- the ``ref.posting_sources.id`` values for
        ``loan_opening`` and ``loan_trueup``.

    Raises:
        RuntimeError: If either genesis source row is absent.
    """
    source_ids = tuple(
        row[0]
        for row in connection.execute(sa.text(_SELECT_GENESIS_SOURCE_IDS_SQL))
    )
    if len(source_ids) != 2:
        raise RuntimeError(
            "cannot remove loan genesis postings: expected the 'loan_opening' "
            "and 'loan_trueup' posting sources (seeded by the read-switch "
            f"Commit-1 migration d1b22f59ba5b), found {len(source_ids)}"
        )
    return source_ids


def _require_equity_opening_kind_id(connection):
    """Resolve the ``equity_opening`` ledger-account kind id, failing loud if absent.

    Seeded by the Commit-1 migration (``d1b22f59ba5b``); a missing row is a broken
    bootstrap invariant.  Raised rather than binding NULL into the account delete
    (which would silently match nothing and leave the accounts to block the
    Commit-1 kind delete under RESTRICT).

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            test session) exposing ``execute``.

    Returns:
        int -- the ``ref.ledger_account_kinds.id`` for ``equity_opening``.

    Raises:
        RuntimeError: If the ``equity_opening`` kind row is absent.
    """
    kind_id = connection.execute(
        sa.text(_SELECT_EQUITY_OPENING_KIND_ID_SQL)
    ).scalar()
    if kind_id is None:
        raise RuntimeError(
            "cannot remove loan genesis postings: the 'equity_opening' "
            "ledger-account kind is missing; the read-switch Commit-1 reference "
            "seed (d1b22f59ba5b) must be applied"
        )
    return kind_id


def _remove_loan_genesis_postings(connection):
    """Remove every genesis opening / true-up entry and per-loan opening-equity account.

    The downgrade's reversible removal, factored out so it runs with either an
    Alembic bind (``op.get_bind()``) or a test session.  Deletes the
    ``source_kind = loan_opening`` / ``loan_trueup`` journal entries FIRST (their
    legs on the linked ledger AND the ``equity_opening`` account cascade via
    ``fk_account_postings_journal_entry_id``), then the per-loan ``equity_opening``
    ledger accounts -- posting-free by then, since only genesis legs land on an
    ``equity_opening`` account.  The linked cash-mirror ledger, the Step-2 cash
    entries, and the Step-4 payment corrections / per-loan interest / escrow /
    refund accounts are untouched.

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            test session) exposing ``execute``.
    """
    source_ids = _require_genesis_source_ids(connection)
    equity_opening_kind_id = _require_equity_opening_kind_id(connection)
    connection.execute(
        sa.text(_DELETE_GENESIS_ENTRIES_SQL).bindparams(
            sa.bindparam("source_kind_ids", value=source_ids, expanding=True),
        ),
    )
    connection.execute(
        sa.text(_DELETE_EQUITY_OPENING_ACCOUNTS_SQL),
        {"kind_id": equity_opening_kind_id},
    )


def upgrade():
    """No forward data work -- the genesis postings are booked at runtime.

    Intentional no-op (not a stub): the opening / true-up corrections are produced
    by the money-critical ``walk_loan_ledger`` engine, which needs the
    ``ref_cache`` / service layer the migration host lacks by design, so they are
    booked by the go-forward wiring and the post-migration deploy hook (see the
    module docstring).  This revision exists to anchor the reversible teardown
    (:func:`downgrade`) as the new head -- above the Commit-1 ref seed
    (``d1b22f59ba5b``) whose clean downgrade depends on the genesis postings being
    removed first.
    """


def downgrade():
    """Remove the genesis opening / true-up corrections and per-loan equity accounts.

    Deletes every ``source_kind = loan_opening`` / ``loan_trueup`` journal entry
    (legs cascade) and every per-loan ``equity_opening`` ledger account, leaving
    the linked ledger, the Step-2 cash entries, and the Step-4 payment corrections
    intact -- so the Commit-1 ref-seed downgrade (``d1b22f59ba5b``) that follows
    can delete the ``loan_opening`` / ``loan_trueup`` sources, the ``opening`` /
    ``trueup`` kinds, and the ``equity_opening`` kind cleanly under their RESTRICT
    FKs.  Reversible: the go-forward wiring / deploy hook regenerate every genesis
    correction identically on the next upgrade.  See the module docstring.
    """
    _remove_loan_genesis_postings(op.get_bind())
