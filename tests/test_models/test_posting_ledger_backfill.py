"""Tests for the db239773c2fd historical settled-transfer backfill (Commit 3).

The Commit-3 migration creates the ledger tables and then backfills one
balanced journal entry per historical settled, non-deleted transfer.  The
migration is already at HEAD when these tests run (the template builder
upgraded base->head against an EMPTY ``budget.transfers`` table, so the
in-chain backfill was a no-op).  Each test therefore engineers a transfer
through ``transfer_service`` (the sole writer, so the shadows are real) and
invokes the migration's idempotent
:func:`_backfill_settled_transfers` directly -- the same pattern the
``ledger_accounts`` / ``loan_anchor_events`` backfill tests use.

The asserted invariants:

  * a settled transfer backfills to exactly one balanced entry: a ``-amount``
    leg on the from-account's ledger and a ``+amount`` leg on the
    to-account's ledger, summing to zero (asset->asset AND asset->liability);
  * the leg amount is the shadow's ``effective_amount``
    (``COALESCE(actual_amount, estimated_amount)``), so an ``actual_amount``
    that diverges from the transfer amount is honoured (the value the oracle
    reconciles against);
  * the entry date is the shadow ``paid_at`` (UTC civil date), falling back
    to the pay-period start when ``paid_at`` is NULL;
  * Projected / Cancelled / soft-deleted / zero-effective transfers are
    excluded;
  * the backfill is idempotent.

The executable migration up/down round-trip was verified manually against the
prod-clone dev DB during development (downgrade dropped both tables, the
balanced trigger, and its function; re-upgrade regenerated 13 balanced
entries / 26 legs reconciling to the settled-transfer shadows); the downgrade
is checked at source level here, matching the ``ledger_accounts`` backfill
suite's rationale (a ``DROP TABLE`` needs an ACCESS EXCLUSIVE lock that
conflicts with the session-scoped ``ref_cache`` refresh in an xdist worker).
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; bodies bind fixtures by name.
from __future__ import annotations

import importlib.util
import pathlib
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.services import transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    ledger_accounts_for_account,
)


# ---------------------------------------------------------------------------
# Migration module loader (migrations/versions has no __init__)
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILENAME = (
    "db239773c2fd_create_journal_entries_account_postings_.py"
)


def _load_migration(filename):
    """Load an Alembic migration module by path via importlib."""
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_migration(_MIGRATION_FILENAME)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_backfill():
    """Execute the migration's idempotent backfill on the test session."""
    posted = _MIGRATION._backfill_settled_transfers(_db.session)
    _db.session.commit()
    return posted


def _entry_for_transfer(transfer_id):
    """Return the single journal entry for *transfer_id*, or None."""
    return (
        _db.session.query(JournalEntry)
        .filter_by(transfer_id=transfer_id)
        .one_or_none()
    )


def _legs_by_ledger(entry_id):
    """Return ``{ledger_account_id: amount}`` for an entry's legs."""
    return {
        leg.ledger_account_id: leg.amount
        for leg in _db.session.query(Posting).filter_by(
            journal_entry_id=entry_id,
        ).all()
    }


def _ledger_id(account):
    """Return the linked ledger account id for *account*."""
    return ledger_accounts_for_account(_db.session, account.id)[0].id


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so transfers have a destination.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned :class:`Account` stays bound to the live session the test runs
    in -- the same pattern ``seed_user`` uses.  A nested context here would
    pop on return and detach the object.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Backfill Savings",
    )
    _db.session.commit()
    return acct


# ---------------------------------------------------------------------------
# Backfill: one balanced entry per settled transfer
# ---------------------------------------------------------------------------


class TestBackfillPostsBalancedEntry:
    """A settled transfer backfills to exactly one balanced two-leg entry."""

    def test_asset_to_asset_signs_and_balance(self, app, db, seed_user, savings):
        """Checking -> Savings $100 backfills to -100 / +100, summing to zero.

        Arithmetic (plan Section 1): the from leg is -100.00 (a credit: money
        leaving Checking), the to leg is +100.00 (a debit: money entering
        Savings); -100.00 + 100.00 = 0.00.  Both ledgers are Asset class, but
        the posting builder never branches on class -- the sign follows
        from/to direction alone.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            savings_ledger = _ledger_id(savings)

            posted = _run_backfill()
            assert posted == [transfer.id]

            entry = _entry_for_transfer(transfer.id)
            assert entry is not None
            assert entry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER,
            )
            legs = _legs_by_ledger(entry.id)
            assert legs[checking_ledger] == Decimal("-100.00")
            assert legs[savings_ledger] == Decimal("100.00")
            assert sum(legs.values()) == Decimal("0.00")
            # Every leg carries the transfer posting kind.
            kinds = {
                leg.posting_kind_id
                for leg in _db.session.query(Posting).filter_by(
                    journal_entry_id=entry.id,
                ).all()
            }
            assert kinds == {ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)}

    def test_asset_to_liability_signs(self, app, db, seed_user):
        """Checking -> Mortgage $250 backfills to -250 / +250 (pay-down).

        Arithmetic (plan Section 1, second worked example): paying down a
        liability is still from=-amount / to=+amount.  -250.00 on the Asset
        Checking ledger, +250.00 on the Liability Mortgage ledger, summing to
        zero -- the sign rule is class-independent.
        """
        with app.app_context():
            mortgage = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Backfill Mortgage",
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], mortgage,
                seed_user["bootstrap_period"], amount=Decimal("250.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            mortgage_ledger = _ledger_id(mortgage)

            _run_backfill()
            entry = _entry_for_transfer(transfer.id)
            legs = _legs_by_ledger(entry.id)
            assert legs[checking_ledger] == Decimal("-250.00")
            assert legs[mortgage_ledger] == Decimal("250.00")
            assert sum(legs.values()) == Decimal("0.00")

    def test_backfill_uses_effective_amount_not_transfer_amount(
        self, app, db, seed_user, savings,
    ):
        """A settled shadow ``actual_amount`` overrides the transfer amount.

        The transfer's nominal amount is $100, but the settled actual is
        $97.50 (mirrored to both shadows), so the shadow ``effective_amount``
        is $97.50 -- the value the balance calculator and the reconciliation
        oracle use.  The backfill must post -97.50 / +97.50, NOT -100 / +100.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                actual_amount=Decimal("97.50"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            savings_ledger = _ledger_id(savings)

            _run_backfill()
            legs = _legs_by_ledger(_entry_for_transfer(transfer.id).id)
            assert legs[checking_ledger] == Decimal("-97.50")
            assert legs[savings_ledger] == Decimal("97.50")


class TestBackfillEntryDate:
    """``entry_date`` is the shadow paid_at (UTC), else the period start."""

    def test_entry_date_from_paid_at_utc(self, app, db, seed_user, savings):
        """A settled paid_at maps to its UTC civil date.

        Arithmetic: paid_at 2026-05-10 14:30 UTC -> entry_date 2026-05-10
        (``(paid_at AT TIME ZONE 'UTC')::date``).
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                paid_at=datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc),
            )
            _db.session.commit()
            _run_backfill()
            entry = _entry_for_transfer(transfer.id)
            assert entry.entry_date == date(2026, 5, 10)

    def test_entry_date_falls_back_to_period_start_when_paid_at_null(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer with NULL paid_at uses the pay-period start.

        Historical settled transfers can carry a NULL ``paid_at`` (settled
        before the paid_at sync existed); ``entry_date`` is NOT NULL, so the
        backfill falls back to the period's ``start_date``.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                period, amount=Decimal("100.00"), paid_at=None,
            )
            _db.session.commit()
            _run_backfill()
            entry = _entry_for_transfer(transfer.id)
            assert entry.entry_date == period.start_date


class TestBackfillExclusions:
    """Projected / Cancelled / soft-deleted / zero-effective are excluded."""

    def test_projected_transfer_not_backfilled(self, app, db, seed_user, savings):
        """An unsettled (Projected) transfer produces no entry.

        Only confirmed facts post -- a Projected transfer has not happened.
        """
        with app.app_context():
            transfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_user["bootstrap_period"].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("100.00"),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                ),
            )
            _db.session.commit()
            assert _run_backfill() == []
            assert _entry_for_transfer(transfer.id) is None

    def test_cancelled_transfer_not_backfilled(self, app, db, seed_user, savings):
        """A Cancelled transfer (is_settled FALSE) produces no entry."""
        with app.app_context():
            transfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_user["bootstrap_period"].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("100.00"),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                ),
            )
            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
            _db.session.commit()
            assert _run_backfill() == []
            assert _entry_for_transfer(transfer.id) is None

    def test_soft_deleted_settled_transfer_not_backfilled(
        self, app, db, seed_user, savings,
    ):
        """A settled-then-soft-deleted transfer produces no entry.

        Its net posted effect is zero (the balance calculator drops a deleted
        shadow), so the backfill's ``is_deleted = FALSE`` filter excludes it.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            _db.session.commit()
            assert _run_backfill() == []
            assert _entry_for_transfer(transfer.id) is None

    def test_zero_effective_transfer_not_backfilled(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer whose actual amount is zero produces no entry.

        No money moved, so there is nothing to post; a zero leg is forbidden
        by ``ck_account_postings_amount_nonzero`` and contributes nothing to
        the oracle, so the backfill omits the entry entirely.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                actual_amount=Decimal("0.00"),
            )
            _db.session.commit()
            assert _run_backfill() == []
            assert _entry_for_transfer(transfer.id) is None


class TestBackfillIdempotency:
    """Re-running the backfill does not double-post."""

    def test_backfill_is_idempotent(self, app, db, seed_user, savings):
        """Two runs leave exactly one entry and two legs for the transfer.

        The enumeration's ``NOT EXISTS`` guard on a prior entry for the
        transfer makes the second run a no-op.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            first = _run_backfill()
            second = _run_backfill()
            assert first == [transfer.id]
            assert second == []

            entries = (
                _db.session.query(JournalEntry)
                .filter_by(transfer_id=transfer.id)
                .count()
            )
            assert entries == 1
            legs = (
                _db.session.query(Posting)
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(JournalEntry.transfer_id == transfer.id)
                .count()
            )
            assert legs == 2


# ---------------------------------------------------------------------------
# Migration revision pair + downgrade source check
# ---------------------------------------------------------------------------


class TestMigrationRevisionPair:
    """The migration chains off the Commit-2 head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "db239773c2fd"
        assert _MIGRATION.down_revision == "b82538084d24"


class TestDowngradeReversible:
    """downgrade() removes the infrastructure and drops both tables.

    A source-level check (the executable round-trip is out of scope for the
    xdist worker -- see the module docstring) guards against a future edit
    silently re-routing the downgrade past one of the artefacts the upgrade
    materialises.
    """

    def test_downgrade_source_removes_infra_and_drops_tables(self):
        """The downgrade source removes posting infra and drops both tables."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        assert "remove_posting_infrastructure(op.execute)" in source
        assert (
            'drop_table("account_postings"' in source
            or "drop_table('account_postings'" in source
        ), "downgrade() never drops budget.account_postings"
        assert (
            'drop_table("journal_entries"' in source
            or "drop_table('journal_entries'" in source
        ), "downgrade() never drops budget.journal_entries"
