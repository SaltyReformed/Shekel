"""
Shekel Budget App -- Transaction Status Seam Tests

Tests for ``app.services.status_seam.apply_status_change`` -- the single
status-mechanics primitive every non-transfer status change routes through
(Build-Order Step 3, Commit 5).  The seam does the transition check, the
``status_id`` assignment, the ``paid_at`` maintenance, and the ``status``
relationship refresh; it does NOT post to the ledger (that is Commit 6) and does
NOT flush or commit (the caller owns the session boundary).

Each test verifies one contract with explicit values so a regression surfaces
with a precise message.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam


def _make_txn(seed_user, period, *, status):
    """Create and flush an ad-hoc expense in the given status.

    The seam operates on any non-transfer transaction, so an ad-hoc expense
    (no template, no entries) is the minimal fixture.  ``status`` is a
    :class:`StatusEnum` member resolved to its id.
    """
    txn = Transaction(
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=ref_cache.status_id(status),
        name="Seam test expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=Decimal("50.00"),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


class TestApplyStatusChangePaidAt:
    """paid_at is derived from the new status unless explicitly supplied."""

    def test_enter_settled_stamps_paid_at(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected -> Paid with no explicit paid_at stamps a real timestamp."""
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            assert txn.paid_at is None

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert isinstance(txn.paid_at, datetime)

    def test_explicit_paid_at_written_verbatim(
        self, app, db, seed_user, seed_periods,
    ):
        """A caller-supplied paid_at (carry-forward back-dating) is used as-is."""
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            explicit = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE), paid_at=explicit,
            )
            db.session.commit()

            db.session.refresh(txn)
            assert txn.paid_at == explicit

    def test_re_settle_preserves_existing_paid_at(
        self, app, db, seed_user, seed_periods,
    ):
        """An idempotent re-settle (Paid -> Paid) does NOT churn paid_at.

        ``apply_status_change`` only stamps now() when entering a settled status
        with no timestamp yet; a row that already carries one keeps it, so
        editing a Paid row (which re-submits its unchanged status) never rewrites
        the original payment time.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            done_id = ref_cache.status_id(StatusEnum.DONE)
            status_seam.apply_status_change(txn, done_id)
            db.session.commit()
            db.session.refresh(txn)
            first_paid_at = txn.paid_at
            assert first_paid_at is not None

            # Re-settle (identity transition, allowed).
            status_seam.apply_status_change(txn, done_id)
            db.session.commit()
            db.session.refresh(txn)
            # Unchanged -- the seam left the existing stamp untouched.
            assert txn.paid_at == first_paid_at

    def test_leave_settled_clears_paid_at(
        self, app, db, seed_user, seed_periods,
    ):
        """Paid -> Projected (a revert) clears paid_at."""
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            db.session.refresh(txn)
            assert txn.paid_at is not None

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.paid_at is None

    def test_enter_non_settled_leaves_paid_at_none(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected -> Cancelled (non-settled) leaves paid_at None."""
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.commit()
            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert txn.paid_at is None


class TestApplyStatusChangeTransition:
    """The seam enforces the state machine and refreshes the status relationship."""

    def test_illegal_transition_raises(
        self, app, db, seed_user, seed_periods,
    ):
        """An illegal move (Paid -> Cancelled) raises ValidationError, no mutation.

        Done -> Cancelled is not a legal transaction transition (the state
        machine admits done -> {done, projected, settled}); the seam must refuse
        it and leave status_id untouched.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.DONE,
            )
            done_id = ref_cache.status_id(StatusEnum.DONE)

            with pytest.raises(ValidationError):
                status_seam.apply_status_change(
                    txn, ref_cache.status_id(StatusEnum.CANCELLED),
                )
            # status_id is unchanged -- verify_transition runs before the assign.
            assert txn.status_id == done_id

    def test_status_relationship_is_fresh_after_change(
        self, app, db, seed_user, seed_periods,
    ):
        """The eagerly-joined status relationship reflects the NEW status pre-commit.

        Loads the (cached) Projected relationship first, so without the seam's
        ``expire`` the read after the change would be the stale Projected row.
        The seam expires it, so ``txn.status`` reloads to Paid -- proving the
        absorbed expire works.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            # Populate the cached relationship with the pre-change row.
            assert txn.status.id == ref_cache.status_id(StatusEnum.PROJECTED)

            done_id = ref_cache.status_id(StatusEnum.DONE)
            status_seam.apply_status_change(txn, done_id)

            # No commit: the fresh value comes from the seam's expire, not
            # expire_on_commit.
            assert txn.status.id == done_id

    def test_does_not_commit(
        self, app, db, seed_user, seed_periods,
    ):
        """The seam mutates in place but never commits -- a rollback reverts it."""
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            db.session.commit()
            txn_id = txn.id

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

            db.session.rollback()
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert reloaded.paid_at is None
