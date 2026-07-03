"""Account anchor (opening / true-up) posting data boundary (Build-Order Step 5, C7)

Revision ID: c9f2e6a4b1d8
Revises: b7d9f3a1c5e8
Create Date: 2026-07-03 16:00:00.000000

Review: solo developer, 2026-07-03 (Actuals reporting, Commit C7; a
production-wide DATA boundary migration -- no schema change.  Its downgrade
removes the non-loan account opening / true-up correction entries and the
per-account ``anchor_equity`` ledger accounts so the Commit-C3 index re-key
(``b7d9f3a1c5e8``) and the Commit-C2 reference-seed (``a4c8e2f6b1d3``)
downgrades that follow are clean; its upgrade is an intentional no-op because
the forward population is booked at runtime by the C6 go-forward wiring / the
deploy backfill hook, not by a migration upgrade -- see below.)

Actuals reporting (see
``docs/audits/balance_architecture/implementation_plan_actuals_reporting.md``).

Build-Order Step 5 posts every NON-loan account's anchor assertions into the
append-only double-entry ledger: a once-per-account OPENING (its earliest
``AccountAnchorHistory`` row) and a TRUE-UP per later row, each a balanced
correction (source ``account_opening`` / ``account_trueup``, leg kind
``opening`` / ``trueup``) whose counter-leg lands on a per-account Equity
ledger account (kind ``anchor_equity``).  After this, every non-loan linked
ledger sums to an ABSOLUTE balance and the trial balance closes app-wide.
This is the shipped loan genesis pattern (``f3d6b1a8c2e4``) generalized to
every non-loan account; the two correction families stay on DISJOINT charts
(loans book onto per-loan ``equity_opening`` accounts, non-loan accounts onto
per-account ``anchor_equity`` accounts), so this migration touches only the
account family and leaves loan genesis, the Step-2/3 cash entries, and the
linked cash-mirror ledgers alone.

**Why this migration exists (the reversibility contract).**  The Commit-C2 ref
seed (``a4c8e2f6b1d3``) added the ``account_opening`` / ``account_trueup``
posting sources and the ``anchor_equity`` ledger-account kind, and its
downgrade DELETEs those rows.  Both columns that reference them --
``budget.journal_entries.source_kind_id`` -> ``ref.posting_sources`` and
``budget.ledger_accounts.kind_id`` -> ``ref.ledger_account_kinds`` -- are
``ondelete='RESTRICT'`` (the leg KINDS ``opening`` / ``trueup`` are REUSED from
the loan read switch and are NOT dropped by C2, so no ``posting_kinds`` row is
at issue here).  So once C6 books any account correction, that C2 downgrade
would fail on a RESTRICT violation.  The C2 docstring anticipates exactly this:
the RESTRICT "would correctly block this DELETE until the higher revisions (the
Step-5 data boundary) are themselves downgraded first."  THIS revision is that
higher revision -- it anchors the runtime account-correction data's teardown as
the new head, so booking never begins without a working downgrade.  It also
clears the ``anchor_equity`` twins the intervening Commit-C3 index re-key
(``b7d9f3a1c5e8``) refuses to downgrade past (its guard raises while any twin
exists); removing them here, first in the chain, lets that re-key downgrade
back to the single-column unique cleanly.

**Why the upgrade posts nothing here (and where the forward population runs).**
An account's opening / true-up correction is produced by the money-critical
moment-granular walk (``account_posting_service.walk_account_ledger``): the
delta is the asserted balance minus the ledger replayed to that assertion
instant, not a one-line SQL formula.  Like the loan genesis backfill it cannot
be reproduced in the migration without duplicating that walk, and this
migration host runs ``create_app(init_ref_cache=False)`` (the ``3104f87``
bootstrap fix) so ``ref_cache`` is off during migrations.  So the forward
population runs in the C6 go-forward wiring and the post-migration deploy hook
(``scripts/init_database.py::backfill_all_account_anchor_postings_after_migration``
-> ``account_posting_service.backfill_all_account_anchor_postings``),
reconcile-to-target and idempotent.

**Downgrade (the reason this migration exists).**  Deletes every
``source_kind = account_opening`` / ``account_trueup`` journal entry FIRST (both
these runtime corrections and any booked after the upgrade; their legs -- on the
account's linked ledger AND its ``anchor_equity`` account -- cascade via
``fk_account_postings_journal_entry_id``), then the per-account
``anchor_equity`` ledger accounts (posting-free once the entries are gone).
Ordering: this runs BEFORE the Commit-C3 re-key downgrade (``b7d9f3a1c5e8``),
which then finds no ``anchor_equity`` row and narrows the unique back cleanly,
and BEFORE the Commit-C2 downgrade (``a4c8e2f6b1d3``), which then deletes the
three ref rows cleanly under their RESTRICT FKs.  The linked cash-mirror ledger
(``account_id`` set, ``anchor_equity`` NOT its kind), the Step-2/3 cash entries,
the loan genesis corrections, and every per-loan ledger are untouched.  Raw SQL,
so the append-only ORM guards and the balanced trigger (INSERT / UPDATE only) do
not fire.  Reversible: a re-upgrade is a no-op and the go-forward wiring / deploy
hook regenerate every account correction identically from the account's anchors
+ settled facts.

**No-op on a fresh database.**  The upgrade does nothing, so a fresh
``flask db upgrade base->head`` (a template rebuild or brand-new deploy) reaches
head with no account corrections; the downgrade's source / kind names were
seeded by the lower-revision Commit-C2 migration (``a4c8e2f6b1d3``) and so are
present whenever the downgrade runs.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'c9f2e6a4b1d8'
down_revision = 'b7d9f3a1c5e8'
branch_labels = None
depends_on = None


# Downgrade SQL.  Resolve the account-correction source / kind ids by unique
# name (the documented migration exception to IDs-for-logic; the names were
# seeded by the Commit-C2 migration a4c8e2f6b1d3), delete the account
# corrections (legs cascade), then drop the per-account anchor-equity ledger
# accounts (posting-free once the entries are gone).
_SELECT_ACCOUNT_CORRECTION_SOURCE_IDS_SQL = (
    "SELECT id FROM ref.posting_sources "
    "WHERE name IN ('account_opening', 'account_trueup')"
)
_SELECT_ANCHOR_EQUITY_KIND_ID_SQL = (
    "SELECT id FROM ref.ledger_account_kinds WHERE name = 'anchor_equity'"
)
_DELETE_ACCOUNT_CORRECTION_ENTRIES_SQL = (
    "DELETE FROM budget.journal_entries WHERE source_kind_id IN :source_kind_ids"
)
# The per-account anchor-equity accounts carry the ``anchor_equity`` kind and
# nothing else does; deleting by kind_id leaves the linked cash-mirror row (kind
# ``linked``) and every per-loan account alone.
_DELETE_ANCHOR_EQUITY_ACCOUNTS_SQL = (
    "DELETE FROM budget.ledger_accounts WHERE kind_id = :kind_id"
)


def _require_account_correction_source_ids(connection):
    """Resolve the ``account_opening`` / ``account_trueup`` source ids, failing loud if absent.

    The source names are seeded by the Commit-C2 migration (``a4c8e2f6b1d3``, a
    lower revision), so both are present whenever this downgrade runs.  A missing
    row is a broken bootstrap invariant -- raise rather than delete a partial set
    (which would strand one correction kind's entries and then fail the Commit-C2
    RESTRICT anyway).

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            test session) exposing ``execute``.

    Returns:
        tuple[int, ...] -- the ``ref.posting_sources.id`` values for
        ``account_opening`` and ``account_trueup``.

    Raises:
        RuntimeError: If either account-correction source row is absent.
    """
    source_ids = tuple(
        row[0]
        for row in connection.execute(
            sa.text(_SELECT_ACCOUNT_CORRECTION_SOURCE_IDS_SQL)
        )
    )
    if len(source_ids) != 2:
        raise RuntimeError(
            "cannot remove account anchor postings: expected the "
            "'account_opening' and 'account_trueup' posting sources (seeded by "
            "the actuals-reporting Commit-C2 migration a4c8e2f6b1d3), found "
            f"{len(source_ids)}"
        )
    return source_ids


def _require_anchor_equity_kind_id(connection):
    """Resolve the ``anchor_equity`` ledger-account kind id, failing loud if absent.

    Seeded by the Commit-C2 migration (``a4c8e2f6b1d3``); a missing row is a
    broken bootstrap invariant.  Raised rather than binding NULL into the account
    delete (which would silently match nothing and leave the accounts to block
    the Commit-C3 re-key downgrade and the Commit-C2 kind delete under RESTRICT).

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            test session) exposing ``execute``.

    Returns:
        int -- the ``ref.ledger_account_kinds.id`` for ``anchor_equity``.

    Raises:
        RuntimeError: If the ``anchor_equity`` kind row is absent.
    """
    kind_id = connection.execute(
        sa.text(_SELECT_ANCHOR_EQUITY_KIND_ID_SQL)
    ).scalar()
    if kind_id is None:
        raise RuntimeError(
            "cannot remove account anchor postings: the 'anchor_equity' "
            "ledger-account kind is missing; the actuals-reporting Commit-C2 "
            "reference seed (a4c8e2f6b1d3) must be applied"
        )
    return kind_id


def _remove_account_anchor_postings(connection):
    """Remove every account opening / true-up entry and per-account anchor-equity account.

    The downgrade's reversible removal, factored out so it runs with either an
    Alembic bind (``op.get_bind()``) or a test session.  Deletes the
    ``source_kind = account_opening`` / ``account_trueup`` journal entries FIRST
    (their legs on the linked ledger AND the ``anchor_equity`` account cascade
    via ``fk_account_postings_journal_entry_id``), then the per-account
    ``anchor_equity`` ledger accounts -- posting-free by then, since only account
    corrections land legs on an ``anchor_equity`` account.  The linked
    cash-mirror ledger, the Step-2/3 cash entries, the loan genesis corrections,
    and every per-loan ledger are untouched.

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            test session) exposing ``execute``.
    """
    source_ids = _require_account_correction_source_ids(connection)
    anchor_equity_kind_id = _require_anchor_equity_kind_id(connection)
    connection.execute(
        sa.text(_DELETE_ACCOUNT_CORRECTION_ENTRIES_SQL).bindparams(
            sa.bindparam("source_kind_ids", value=source_ids, expanding=True),
        ),
    )
    connection.execute(
        sa.text(_DELETE_ANCHOR_EQUITY_ACCOUNTS_SQL),
        {"kind_id": anchor_equity_kind_id},
    )


def upgrade():
    """No forward data work -- the account corrections are booked at runtime.

    Intentional no-op (not a stub): the opening / true-up corrections are
    produced by the money-critical ``walk_account_ledger`` engine, which needs
    the ``ref_cache`` / service layer the migration host lacks by design, so they
    are booked by the C6 go-forward wiring and the post-migration deploy hook
    (see the module docstring).  This revision exists to anchor the reversible
    teardown (:func:`downgrade`) as the new head -- above the Commit-C3 re-key
    (``b7d9f3a1c5e8``) and the Commit-C2 ref seed (``a4c8e2f6b1d3``), whose clean
    downgrades depend on the account corrections and their ``anchor_equity`` rows
    being removed first.
    """


def downgrade():
    """Remove the account opening / true-up corrections and per-account equity accounts.

    Deletes every ``source_kind = account_opening`` / ``account_trueup`` journal
    entry (legs cascade) and every per-account ``anchor_equity`` ledger account,
    leaving the linked ledger, the Step-2/3 cash entries, the loan genesis
    corrections, and every per-loan ledger intact -- so the Commit-C3 re-key
    downgrade (``b7d9f3a1c5e8``, which refuses to run while any ``anchor_equity``
    row survives) and then the Commit-C2 ref-seed downgrade (``a4c8e2f6b1d3``,
    which deletes the ``account_opening`` / ``account_trueup`` sources and the
    ``anchor_equity`` kind under their RESTRICT FKs) both run cleanly.
    Reversible: the go-forward wiring / deploy hook regenerate every account
    correction identically on the next upgrade.  See the module docstring.
    """
    _remove_account_anchor_postings(op.get_bind())
