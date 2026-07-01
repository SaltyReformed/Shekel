"""Build-Order Step 4 loan-payment split-posting data boundary (backfill + teardown)

Revision ID: e2a9f1c7b4d6
Revises: d1e7c4a2f9b3
Create Date: 2026-07-01 09:00:00.000000

Review: solo developer, 2026-07-01 (Build-Order Step 4, Commit 6; a
production-wide DATA boundary migration -- no schema change.  Its downgrade
removes the loan-payment split corrections and the per-loan interest / escrow /
refund ledger accounts so the Commit-2 schema downgrade (efca4315bf81) is
clean; its upgrade is an intentional no-op because the forward population
requires the ref-cache / service layer the migration host deliberately lacks,
and runs in the post-migration deploy hook instead -- see below.)

Build-Order Step 4, Commit 6 (post confirmed loan payments with their real
principal / interest / escrow split; see
``docs/audits/balance_architecture/implementation_plan_posting_ledger_loan_payments.md``).

The go-forward wiring (Commit 5) posts a balanced CORRECTION -- moving each
confirmed loan payment's interest / escrow / refund off the loan -- whenever a
payment crosses into a settled status through a service / route chokepoint.
Every payment settled BEFORE that wiring shipped carries no correction, so the
Commit-7 loan reconciliation oracle would be blind to historical loan payments
on real data.  This migration is the chain boundary that owns that historical
backfill's lifecycle.  Nothing reads these postings yet; every loan balance
still flows through the resolver / ``balance_at`` seam (this Build-Order step is
write-only).

**Why the upgrade posts nothing here (and where the forward population runs).**
The backfill posts one correction per confirmed post-anchor settled payment,
computed by the REAL-split walk
(``loan_posting_service.compute_loan_payment_splits``): a running-balance walk
seeded from the loan's latest anchor, accruing interest per rate period and
subtracting the effective-dated configured escrow of each payment's date.  That
is NOT a one-line SQL formula like the Step-2 settled-transfer effect or the
Step-3 ``COALESCE(actual, estimated) - SUM(credit)`` cash effect were, so --
unlike those self-contained raw-SQL backfills (``db239773c2fd`` /
``7d63529e4300``) -- it cannot be reproduced in the migration without
duplicating the money-critical split engine (the exact drift the plan's unified
``app.utils.money.accrue_monthly_interest`` exists to prevent).  The only
correct source of the split is the go-forward service itself, which is built on
``ref_cache`` + the service layer.  This migration host runs
``create_app(init_ref_cache=False)`` (the ``3104f87`` bootstrap fix), so
``ref_cache`` is off during migrations, and the documented self-contained-
backfill policy (``db239773c2fd``) forbids importing the service here.  So the
forward population runs in the deploy's POST-migration app context --
``scripts/init_database.py::backfill_loan_payment_postings_after_migration``,
which initialises ``ref_cache`` against the migrated database, then calls the
idempotent ``loan_posting_service.backfill_all_loan_payment_postings``.  It is
reconcile-to-target, so it is safe on every deploy and never double-posts a
go-forward correction.  ``scripts/build_test_template.py`` and a bare
``flask db upgrade`` do not run that hook, but the test template is loan-free
(the backfill would no-op) and the Commit-6 suite invokes the app-layer backfill
directly.

**Downgrade (the reason this migration exists).**  Removes every
``source_kind = loan_payment`` journal entry (both these historical corrections
and any go-forward corrections emitted after the upgrade; their legs cascade via
``fk_account_postings_journal_entry_id``) and then every per-loan ledger account
(``loan_account_id IS NOT NULL`` -- the interest / escrow / refund rows).  The
loan's LINKED cash-mirror ledger keeps its ``account_id`` (``loan_account_id``
NULL) and is untouched, as are the Step-2 cash entries the corrections layer on.
Entries are deleted before the accounts so the accounts are posting-free when
dropped.  Raw SQL, so the append-only ORM guards (ORM-mediated deletes only) do
not interfere and the balanced trigger (INSERT / UPDATE only) does not fire.
Reversible: a re-upgrade is a no-op and the post-migration deploy hook (or the
oracle) regenerates every correction identically from the settled payments.
Must run BEFORE the Commit-2 schema downgrade (``efca4315bf81``), which drops
``loan_account_id`` / ``kind_id``: once this has run no per-loan row remains and
that drop is clean -- exactly the ordering ``efca4315bf81``'s downgrade
docstring depends on.

**No-op on a fresh database.**  The upgrade does nothing, so a fresh
``flask db upgrade base->head`` (a template rebuild or brand-new deploy) reaches
head with no loan-payment postings; the downgrade's ``loan_payment`` source id
was seeded by the lower-revision Commit-1 migration (``f8e025a8be41``) and so is
present whenever the downgrade runs.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'e2a9f1c7b4d6'
down_revision = 'd1e7c4a2f9b3'
branch_labels = None
depends_on = None


# Downgrade SQL.  Resolve the ``loan_payment`` source id by unique name (the
# documented migration exception to IDs-for-logic; the name was seeded by the
# Commit-1 migration f8e025a8be41), delete its entries (legs cascade), then drop
# the per-loan ledger accounts (posting-free once the entries are gone).
_SELECT_LOAN_PAYMENT_SOURCE_SQL = (
    "SELECT id FROM ref.posting_sources WHERE name = 'loan_payment'"
)
_DELETE_LOAN_PAYMENT_ENTRIES_SQL = (
    "DELETE FROM budget.journal_entries WHERE source_kind_id = :source_kind_id"
)
# Every ``loan_account_id IS NOT NULL`` ledger account is a Step-4 per-loan
# interest / escrow / refund row (the columns-only ``ck_ledger_accounts_loan_shape``
# CHECK guarantees such a row is nothing else); the loan's LINKED cash-mirror row
# carries an ``account_id`` with ``loan_account_id`` NULL and is left alone.
_DELETE_PER_LOAN_LEDGER_ACCOUNTS_SQL = (
    "DELETE FROM budget.ledger_accounts WHERE loan_account_id IS NOT NULL"
)


def _require_loan_payment_source(connection):
    """Resolve the ``loan_payment`` posting-source id, failing loud if absent.

    The source name is seeded by the Commit-1 migration (``f8e025a8be41``, a
    lower revision), so it is present whenever this downgrade runs.  A missing
    row is a broken bootstrap invariant -- raise with the offending lookup rather
    than binding a NULL into the ``source_kind_id`` filter (which would silently
    match nothing and leave the entries in place).

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            session in a test) exposing ``execute``.

    Returns:
        int -- the ``ref.posting_sources.id`` for ``loan_payment``.

    Raises:
        RuntimeError: If the ``loan_payment`` source row is absent.
    """
    source_kind_id = connection.execute(
        sa.text(_SELECT_LOAN_PAYMENT_SOURCE_SQL)
    ).scalar()
    if source_kind_id is None:
        raise RuntimeError(
            "cannot remove loan-payment postings: the 'loan_payment' posting "
            "source is missing; the Step-4 Commit-1 reference seed "
            "(f8e025a8be41) must be applied"
        )
    return source_kind_id


def _remove_loan_payment_postings(connection):
    """Remove every loan-payment correction entry and per-loan ledger account.

    The downgrade's reversible removal, factored out so it runs with either an
    Alembic bind (``op.get_bind()``) or a test session.  Deletes the
    ``source_kind = loan_payment`` journal entries FIRST (their legs cascade via
    ``fk_account_postings_journal_entry_id``), then the ``loan_account_id IS NOT
    NULL`` per-loan ledger accounts -- which are posting-free by then, since only
    loan-payment correction legs ever land on a per-loan account.  The Step-2
    cash entries, the ``transfer`` / ``transaction`` entries, and the linked /
    category / fallback ledger accounts are untouched.

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            test session) exposing ``execute``.
    """
    source_kind_id = _require_loan_payment_source(connection)
    connection.execute(
        sa.text(_DELETE_LOAN_PAYMENT_ENTRIES_SQL),
        {"source_kind_id": source_kind_id},
    )
    connection.execute(sa.text(_DELETE_PER_LOAN_LEDGER_ACCOUNTS_SQL))


def upgrade():
    """No forward data work -- the split backfill runs in the deploy hook.

    Intentional no-op (not a stub): the real-split loan-payment backfill needs
    the ``ref_cache`` / service layer the migration host lacks by design, so it
    runs in ``scripts/init_database.py`` after the chain reaches head (see the
    module docstring).  This revision exists to anchor the reversible teardown
    (:func:`downgrade`) at the correct point in the chain -- above the Commit-2
    schema migration (``efca4315bf81``) whose clean downgrade depends on the
    per-loan rows being removed first.
    """


def downgrade():
    """Remove the Step-4 loan-payment corrections and per-loan ledger accounts.

    Deletes every ``source_kind = loan_payment`` journal entry (legs cascade)
    and every ``loan_account_id IS NOT NULL`` per-loan ledger account, leaving
    the Step-2 cash entries and the linked ledger accounts intact -- so the
    Commit-2 schema downgrade (``efca4315bf81``) that follows can drop
    ``loan_account_id`` / ``kind_id`` cleanly.  Reversible: the post-migration
    deploy hook (or the Commit-7 oracle) regenerates every correction identically
    on the next upgrade.  See the module docstring.
    """
    _remove_loan_payment_postings(op.get_bind())
