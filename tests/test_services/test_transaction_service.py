"""
Shekel Budget App -- Transaction Service Tests

Tests for ``app.services.transaction_service`` -- the cross-cutting
helpers used by both the manual ``mark_done`` route and the
carry-forward envelope branch (Phase 4 of
``docs/carry-forward-aftermath-implementation-plan.md``).

Each test verifies exact Decimal values and explicit arithmetic so a
reviewer can recompute the expected values by hand.  Tests are
deliberately kept small and single-purpose so a regression at any
contract boundary surfaces with a precise failure message.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern, Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import transaction_service


# ── Helpers ──────────────────────────────────────────────────────────


def _make_entry(txn_id, user_id, amount, description, *,
                entry_date=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    The transaction service operates on already-loaded entries, so
    these tests construct entries with raw ORM access to keep the
    setup focused on the helper's contract.
    """
    entry = TransactionEntry(
        transaction_id=txn_id,
        user_id=user_id,
        amount=Decimal(amount),
        description=description,
        entry_date=entry_date or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _make_envelope_template(seed_user, *, txn_type_name="Expense",
                            default_amount="500.00"):
    """Create an envelope-tracked template of the requested type.

    Mirrors the seed_entry_template fixture but parameterizes the
    transaction type so the income-side branch can be exercised.
    """
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    txn_type = (
        db.session.query(TransactionType)
        .filter_by(name=txn_type_name).one()
    )

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=txn_type.id,
        name=f"Tracked {txn_type_name}",
        default_amount=Decimal(default_amount),
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()
    return template


def _make_projected_txn(seed_user, period, *, template,
                        estimated_amount="500.00"):
    """Create a Projected transaction tied to the supplied template."""
    projected_status = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected_status.id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        estimated_amount=Decimal(estimated_amount),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


# ── Happy-Path Tests ─────────────────────────────────────────────────


class TestSettleFromEntriesExpense:
    """Settling expense transactions produces DONE status and entry-sum actual."""

    def test_expense_with_entries_sets_done_and_sum(
        self, app, db, seed_user, seed_periods,
    ):
        """Expense + multiple entries: status=DONE, actual=sum, paid_at set.

        Setup: $150 + $250 = $400 of debit entries against a $500 envelope.
        Expected: status_id == DONE, actual_amount == 400.00, paid_at set.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "150.00", "Kroger")
            _make_entry(txn.id, user_id, "250.00", "Target")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 150 + 250 = 400
            assert txn.actual_amount == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.paid_at is not None

    def test_expense_includes_credit_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """Both debit and credit entries contribute to actual_amount.

        Per ``compute_actual_from_entries`` semantics, credit entries
        count toward total spending for analytics; the credit/checking
        impact split is handled separately by the CC payback workflow.

        Setup: $300 debit + $100 credit = $400.
        Expected: actual_amount == 400.00.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "300.00", "Kroger")
            _make_entry(
                txn.id, user_id, "100.00", "Amazon", is_credit=True,
            )
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 300 + 100 = 400
            assert txn.actual_amount == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_expense_zero_entries_settles_at_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Empty entry list settles at actual=0 (carry-forward rollover case).

        The carry-forward envelope branch invokes the helper on
        envelope rows that may have no entries; the contract is
        ``actual_amount == Decimal("0")``, NOT a fallback to
        ``estimated_amount``.  This frees the full estimated amount
        to roll into the next period's canonical row.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="100.00",
            )

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            assert txn.actual_amount == Decimal("0.00")
            assert txn.estimated_amount == Decimal("100.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.paid_at is not None

    def test_expense_overspend_records_full_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Overspending records the full entry sum without clamping.

        If the wife spends $120 against a $100 envelope, the actual
        reflects the truth ($120), not the cap ($100).  The
        carry-forward helper uses this to compute leftover =
        max(0, estimated - actual) elsewhere; clamping here would
        hide the overspend signal from analytics.

        Setup: $80 + $40 = $120 against a $100 estimate.
        Expected: actual_amount == 120.00, estimated stays 100.00.
        """
        with app.app_context():
            template = _make_envelope_template(
                seed_user, default_amount="100.00",
            )
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="100.00",
            )
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "80.00", "Kroger")
            _make_entry(txn.id, user_id, "40.00", "Target")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 80 + 40 = 120 -- exceeds the 100 estimate, intentionally.
            assert txn.actual_amount == Decimal("120.00")
            assert txn.estimated_amount == Decimal("100.00")


class TestSettleFromEntriesIncome:
    """Settling income transactions produces RECEIVED status.

    Phase 2 of the carry-forward aftermath plan (committed in
    ``feat(template): rename ... reject envelope semantics on income``)
    rejects ``is_envelope=True`` on income templates at the schema
    layer, so this branch is not reachable via the normal template
    create/update flow.  The branch must remain correct because
    direct DB writes can still produce the combination, and the
    helper is the lowest-level mutation point -- its behavior
    documents the contract regardless of how the row got into this
    state.
    """

    def test_income_with_entries_sets_received(
        self, app, db, seed_user, seed_periods,
    ):
        """Income + entries: status=RECEIVED, actual=sum, paid_at set."""
        with app.app_context():
            template = _make_envelope_template(
                seed_user, txn_type_name="Income", default_amount="2500.00",
            )
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="2500.00",
            )
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "1000.00", "Direct deposit 1")
            _make_entry(txn.id, user_id, "1500.00", "Direct deposit 2")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 1000 + 1500 = 2500
            assert txn.actual_amount == Decimal("2500.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.RECEIVED)
            assert txn.paid_at is not None


# ── paid_at Handling ─────────────────────────────────────────────────


class TestSettleFromEntriesPaidAt:
    """The helper accepts an explicit paid_at and falls back to db.func.now()."""

    def test_default_paid_at_is_set(self, app, db, seed_user, seed_periods):
        """Calling without paid_at sets txn.paid_at to a real timestamp.

        The default uses ``db.func.now()`` which becomes a SQL ``NOW()``
        evaluated at flush time; after commit the column holds a
        concrete ``datetime``.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "10.00", "Test")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            assert isinstance(txn.paid_at, datetime)

    def test_explicit_paid_at_is_preserved(
        self, app, db, seed_user, seed_periods,
    ):
        """Caller-supplied paid_at is written verbatim.

        Useful when the caller has a more meaningful timestamp than
        "right now" -- e.g. a back-dated reconciliation or a batch
        operation that should record a single shared timestamp.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "10.00", "Test")
            db.session.flush()

            explicit = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
            transaction_service.settle_from_entries(txn, paid_at=explicit)
            db.session.commit()

            db.session.refresh(txn)
            # PostgreSQL stores TIMESTAMP WITH TIME ZONE -- compare in UTC.
            assert txn.paid_at == explicit


# ── Precondition Tests ───────────────────────────────────────────────


class TestSettleFromEntriesPreconditions:
    """Each documented precondition raises ValidationError on violation.

    The helper is the lowest-level "settle a tracked row" mutation,
    used by both manual mark-done and the carry-forward batch.  A
    permissive helper would silently corrupt state when fed a
    soft-deleted row, a transfer shadow, an immutable status, or a
    non-envelope template; defensive validation keeps the bug
    surface narrow.  Each test below confirms one rule.
    """

    def test_rejects_soft_deleted_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Soft-deleted transactions cannot be resurrected via settle."""
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            txn.is_deleted = True
            db.session.commit()
            txn_id = txn.id

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "soft-deleted" in str(exc_info.value)

            # No state change should have leaked through -- the helper
            # raised before any mutation, so a rollback is just hygiene.
            db.session.rollback()
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.actual_amount is None
            assert reloaded.status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )
            assert reloaded.is_deleted is True

    def test_rejects_template_less_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Transactions without a template are not envelope-tracked.

        Ad-hoc transactions (created without a recurrence template)
        have no envelope semantics; mark_done's manual-actual branch
        handles them.  The helper is for tracked rows only.
        """
        with app.app_context():
            projected_status = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected_status.id,
                name="Ad-hoc expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "envelope-tracked" in str(exc_info.value)

    def test_rejects_non_envelope_template(
        self, app, db, seed_user, seed_periods,
    ):
        """Templates with is_envelope=False are not entry-tracked."""
        with app.app_context():
            every_period = (
                db.session.query(RecurrencePattern)
                .filter_by(name="Every Period").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=every_period.id,
            )
            db.session.add(rule)
            db.session.flush()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                recurrence_rule_id=rule.id,
                transaction_type_id=expense_type.id,
                name="Rent",
                default_amount=Decimal("1200.00"),
                is_envelope=False,
            )
            db.session.add(template)
            db.session.flush()

            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="1200.00",
            )

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "envelope-tracked" in str(exc_info.value)

    def test_rejects_transfer_shadow(self, app, db, seed_user, seed_periods):
        """Transfer shadows must settle through transfer_service.

        The invariants in CLAUDE.md require shadow legs and the
        parent transfer to mutate together; settling one shadow in
        isolation would break the invariant ``shadow amounts and
        statuses always equal the parent transfer's``.

        Uses ``no_autoflush`` because the test stamps a synthetic
        ``transfer_id`` in memory that would violate the FK if
        SQLAlchemy autoflushed before the helper's guard fires.  The
        helper itself only reads the attribute -- the FK never enters
        the picture in production callers.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            db.session.commit()

            with db.session.no_autoflush:
                # The helper inspects ``transfer_id`` directly without
                # a DB query, so an in-memory non-None value is enough
                # to exercise the guard -- avoiding a real
                # transfer/category setup that the rule does not
                # depend on.
                txn.transfer_id = 999_999

                with pytest.raises(ValidationError) as exc_info:
                    transaction_service.settle_from_entries(txn)
                assert "transfer" in str(exc_info.value).lower()

            db.session.rollback()

    def test_rejects_already_settled_status(
        self, app, db, seed_user, seed_periods,
    ):
        """A second call on a Paid transaction raises (no idempotent re-settle).

        The Paid status is immutable per the ``is_immutable`` flag on
        ``ref.statuses``.  Attempting to re-settle would update
        ``paid_at`` and possibly ``actual_amount`` on a finalised
        row, which is meaningless and indicates a caller bug.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "100.00", "Kroger")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            # Second call should refuse.
            db.session.refresh(txn)
            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "immutable" in str(exc_info.value).lower()

    def test_rejects_cancelled_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Cancelled transactions stay cancelled.

        ``CANCELLED`` is immutable so the user's deliberate
        cancellation is preserved.  Settling would silently override
        the cancel decision -- a financial bug masquerading as a
        no-op.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            cancelled_status = (
                db.session.query(Status).filter_by(name="Cancelled").one()
            )
            txn.status_id = cancelled_status.id
            db.session.flush()

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "immutable" in str(exc_info.value).lower()


# ── Caller-Owned Session Lifecycle ───────────────────────────────────


class TestSettleFromEntriesSessionContract:
    """Verify the helper does not flush or commit on its own.

    The carry-forward batch needs the helper to participate in a
    single atomic transaction; if the helper auto-committed, a
    failure later in the batch would leave a half-applied state.
    """

    def test_does_not_commit_on_success(
        self, app, db, seed_user, seed_periods,
    ):
        """Mutations are visible only after the caller commits."""
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "75.00", "Pharmacy")
            # Persist setup so a rollback after the helper call only
            # reverts the helper's own mutations, not the test fixtures.
            db.session.commit()
            txn_id = txn.id

            transaction_service.settle_from_entries(txn)

            # Attribute mutations are visible on the in-memory object.
            assert txn.actual_amount == Decimal("75.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            # A rollback should reverse the helper's writes -- proving
            # the helper itself never committed or flushed.
            db.session.rollback()

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded is not None
            assert reloaded.actual_amount is None
            assert reloaded.status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )
