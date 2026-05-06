"""
Shekel Budget App -- Service-tier log_event emission tests.

Audit finding F-080 / commit C-14: every state-changing service
function MUST emit a structured ``log_event`` on its successful path
so the Python tier of the audit story is queryable by event-name.

These tests treat each emission as observable behaviour: they call
the service through its public API and assert that the expected
event lands in the log stream with the expected category, level, and
the structured fields a forensic query would key off.

Why every service in one file: each test sets up the bare minimum
state for ONE call and asserts ONE event.  Combining them keeps the
fixture surface area small (a single ``_LogCapture`` helper, no
ad-hoc per-service conftest), and the failure mode is "this specific
service stopped logging" -- a far more useful signal than a generic
"the registry is incomplete".
"""

import logging
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.extensions import db
from app.enums import StatusEnum
from app.models.category import Category
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.services import (
    carry_forward_service,
    credit_workflow,
    entry_credit_workflow,
    entry_service,
    pay_period_service,
    recurrence_engine,
    transaction_service,
    transfer_recurrence,
    transfer_service,
)
from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.utils.log_events import (
    ACCESS,
    BUSINESS,
    EVT_ACCESS_DENIED_CROSS_USER,
    EVT_CARRY_FORWARD,
    EVT_CREDIT_MARKED,
    EVT_CREDIT_UNMARKED,
    EVT_CROSS_USER_BLOCKED,
    EVT_ENTRIES_CLEARED_ON_ANCHOR_TRUEUP,
    EVT_ENTRY_CLEARED_TOGGLED,
    EVT_ENTRY_CREATED,
    EVT_ENTRY_DELETED,
    EVT_ENTRY_PAYBACK_CREATED,
    EVT_ENTRY_PAYBACK_DELETED,
    EVT_ENTRY_PAYBACK_UPDATED,
    EVT_ENTRY_UPDATED,
    EVT_PAY_PERIODS_GENERATED,
    EVT_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_RECURRENCE_GENERATED,
    EVT_RECURRENCE_REGENERATED,
    EVT_TRANSACTION_SETTLED_FROM_ENTRIES,
    EVT_TRANSFER_CREATED,
    EVT_TRANSFER_HARD_DELETED,
    EVT_TRANSFER_RECURRENCE_GENERATED,
    EVT_TRANSFER_RESTORED,
    EVT_TRANSFER_SOFT_DELETED,
    EVT_TRANSFER_UPDATED,
)


class _LogCapture:
    """Capture log records on a target logger with propagation off.

    The Shekel logging config attaches a JSON handler at the root.
    Without ``propagate = False`` the captured records would also fire
    through the root handler and clutter the test stdout.
    """

    def __init__(self, logger_name: str, level: int = logging.DEBUG):
        self._logger = logging.getLogger(logger_name)
        self._level = level
        self.records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.records.append(record)
        self._prior_level = None
        self._prior_propagate = None

    def __enter__(self):
        self._prior_level = self._logger.level
        self._prior_propagate = self._logger.propagate
        self._logger.addHandler(self._handler)
        self._logger.setLevel(self._level)
        self._logger.propagate = False
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prior_level)
        self._logger.propagate = self._prior_propagate

    def find(self, event_name):
        """Return the first record whose ``event`` field matches *event_name*.

        Returns ``None`` if no match is found, so tests can compose a
        helpful assertion message that prints every observed event.
        """
        for record in self.records:
            if getattr(record, "event", None) == event_name:
                return record
        return None

    def event_summary(self):
        """Return a list of (level, event) tuples for assertion messages."""
        return [
            (r.levelname, getattr(r, "event", None)) for r in self.records
        ]


# ── Pay periods ────────────────────────────────────────────────────


class TestPayPeriodServiceLogging:
    """``pay_period_service.generate_pay_periods`` emits ``pay_periods_generated``."""

    def test_generate_emits_event(self, app, db, seed_user):
        """Generating periods emits one event with user_id and count."""
        with app.app_context(), _LogCapture(
            "app.services.pay_period_service",
        ) as cap:
            created = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2027, 1, 1),
                num_periods=3,
                cadence_days=14,
            )
            assert len(created) == 3

        record = cap.find(EVT_PAY_PERIODS_GENERATED)
        assert record is not None, (
            f"Did not emit {EVT_PAY_PERIODS_GENERATED}; "
            f"observed: {cap.event_summary()}"
        )
        assert record.levelno == logging.INFO
        assert record.category == BUSINESS
        assert record.user_id == seed_user["user"].id
        assert record.count == 3
        assert record.cadence_days == 14
        assert record.start_date == "2027-01-01"


# ── Transfer service ───────────────────────────────────────────────


@pytest.fixture
def _transfer_setup(app, db, seed_user, seed_periods):
    """Build the minimum state needed for a transfer.

    seed_user has a Checking account; the transfer needs a second
    account (Savings) and the Transfers: Outgoing/Incoming categories
    that the route layer normally seeds.
    """
    from app.models.account import Account  # noqa: WPS433
    from app.models.ref import AccountType  # noqa: WPS433

    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    savings = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name="Savings",
        current_anchor_balance=Decimal("0.00"),
    )
    db.session.add(savings)

    db.session.add_all([
        Category(
            user_id=seed_user["user"].id,
            group_name="Transfers",
            item_name="Outgoing",
            sort_order=900,
        ),
        Category(
            user_id=seed_user["user"].id,
            group_name="Transfers",
            item_name="Incoming",
            sort_order=901,
        ),
    ])
    db.session.commit()

    projected = db.session.query(Status).filter_by(name="Projected").one()

    return {
        "user": seed_user["user"],
        "account": seed_user["account"],
        "savings": savings,
        "scenario": seed_user["scenario"],
        "periods": seed_periods,
        "projected": projected,
        "category": seed_user["categories"]["Rent"],
    }


def _make_transfer(td, **overrides):
    """Helper: create a transfer using the standard test data."""
    kwargs = dict(
        user_id=td["user"].id,
        from_account_id=td["account"].id,
        to_account_id=td["savings"].id,
        pay_period_id=td["periods"][0].id,
        scenario_id=td["scenario"].id,
        amount=Decimal("100.00"),
        status_id=td["projected"].id,
        category_id=td["category"].id,
    )
    kwargs.update(overrides)
    return transfer_service.create_transfer(**kwargs)


class TestTransferServiceLogging:
    """transfer_service emits one event per public mutation."""

    def test_create_transfer_emits_event(self, app, db, _transfer_setup):
        """create_transfer emits ``transfer_created`` with shadow ids."""
        td = _transfer_setup
        with app.app_context(), _LogCapture(
            "app.services.transfer_service",
        ) as cap:
            xfer = _make_transfer(td)

        record = cap.find(EVT_TRANSFER_CREATED)
        assert record is not None, (
            f"Missing {EVT_TRANSFER_CREATED}; observed: {cap.event_summary()}"
        )
        assert record.levelno == logging.INFO
        assert record.category == BUSINESS
        assert record.user_id == td["user"].id
        assert record.transfer_id == xfer.id
        assert record.amount == "100.00"
        # Shadow ids must be present so a forensic query can pivot
        # from a transfer event to the resulting transactions.
        assert record.expense_shadow_id is not None
        assert record.income_shadow_id is not None

    def test_update_transfer_emits_event(self, app, db, _transfer_setup):
        """update_transfer emits ``transfer_updated`` with sorted fields_changed."""
        td = _transfer_setup
        with app.app_context():
            xfer = _make_transfer(td)
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    amount=Decimal("123.45"),
                    notes="updated notes",
                )

        record = cap.find(EVT_TRANSFER_UPDATED)
        assert record is not None
        assert record.user_id == td["user"].id
        assert record.transfer_id == xfer.id
        # Sorted alphabetically -- amount, notes.
        assert record.fields_changed == ["amount", "notes"]

    def test_soft_delete_transfer_emits_event(self, app, db, _transfer_setup):
        """delete_transfer(soft=True) emits ``transfer_soft_deleted``."""
        td = _transfer_setup
        with app.app_context():
            xfer = _make_transfer(td)
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_service.delete_transfer(
                    xfer.id, td["user"].id, soft=True,
                )

        record = cap.find(EVT_TRANSFER_SOFT_DELETED)
        assert record is not None
        assert record.user_id == td["user"].id
        assert record.transfer_id == xfer.id
        assert record.shadow_count == 2

    def test_hard_delete_transfer_emits_event(self, app, db, _transfer_setup):
        """delete_transfer(soft=False) emits ``transfer_hard_deleted``."""
        td = _transfer_setup
        with app.app_context():
            xfer = _make_transfer(td)
            xid = xfer.id
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_service.delete_transfer(xid, td["user"].id)

        record = cap.find(EVT_TRANSFER_HARD_DELETED)
        assert record is not None
        assert record.transfer_id == xid
        assert record.orphan_count == 0

    def test_restore_transfer_emits_event(self, app, db, _transfer_setup):
        """restore_transfer emits ``transfer_restored`` after soft-delete."""
        td = _transfer_setup
        with app.app_context():
            xfer = _make_transfer(td)
            transfer_service.delete_transfer(
                xfer.id, td["user"].id, soft=True,
            )
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_service.restore_transfer(xfer.id, td["user"].id)

        record = cap.find(EVT_TRANSFER_RESTORED)
        assert record is not None
        assert record.transfer_id == xfer.id
        assert record.shadow_count == 2


# ── Credit workflow (legacy per-transaction) ───────────────────────


@pytest.fixture
def _projected_expense(app, db, seed_user, seed_periods):
    """Build a Projected expense in the first seeded period."""
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense = db.session.query(
        # The TransactionType import lives in app.models.ref; lookup
        # by name is cheaper than another import.
        Status.__table__.metadata.tables["ref.transaction_types"]
    )  # noqa: pylint=unused-variable
    from app.models.ref import TransactionType  # noqa: WPS433
    expense_type = db.session.query(
        TransactionType,
    ).filter_by(name="Expense").one()

    txn = Transaction(
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Test Expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("50.00"),
    )
    db.session.add(txn)
    db.session.flush()
    db.session.commit()
    return txn


class TestCreditWorkflowLogging:
    """credit_workflow.mark_as_credit / unmark_credit emit one event each."""

    def test_mark_as_credit_emits_event(self, app, db, seed_user, _projected_expense):
        """mark_as_credit emits ``credit_marked`` with payback id."""
        with app.app_context(), _LogCapture(
            "app.services.credit_workflow",
        ) as cap:
            payback = credit_workflow.mark_as_credit(
                _projected_expense.id, seed_user["user"].id,
            )
            assert payback is not None

        record = cap.find(EVT_CREDIT_MARKED)
        assert record is not None
        assert record.category == BUSINESS
        assert record.user_id == seed_user["user"].id
        assert record.transaction_id == _projected_expense.id
        assert record.payback_id == payback.id
        assert record.amount == "50.00"

    def test_unmark_credit_emits_event(self, app, db, seed_user, _projected_expense):
        """unmark_credit emits ``credit_unmarked`` with deleted_payback_id."""
        with app.app_context():
            payback = credit_workflow.mark_as_credit(
                _projected_expense.id, seed_user["user"].id,
            )
            db.session.commit()
            with _LogCapture("app.services.credit_workflow") as cap:
                credit_workflow.unmark_credit(
                    _projected_expense.id, seed_user["user"].id,
                )

        record = cap.find(EVT_CREDIT_UNMARKED)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.transaction_id == _projected_expense.id
        assert record.deleted_payback_id == payback.id


# ── Entry service ──────────────────────────────────────────────────


@pytest.fixture
def _envelope_transaction(app, db, seed_user, seed_periods):
    """Build a Projected envelope-tracked expense for entry tests."""
    from app.models.ref import TransactionType  # noqa: WPS433
    expense_type = db.session.query(
        TransactionType,
    ).filter_by(name="Expense").one()
    projected = db.session.query(Status).filter_by(name="Projected").one()

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.EVERY_PERIOD,
        ),
        start_period_id=seed_periods[0].id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        name="Groceries Envelope",
        default_amount=Decimal("400.00"),
        is_envelope=True,
        recurrence_rule_id=rule.id,
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        template_id=template.id,
        name="Groceries Envelope",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("400.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


class TestEntryServiceLogging:
    """entry_service emits one event per mutation."""

    def test_create_entry_emits_event(self, app, db, seed_user, _envelope_transaction):
        """create_entry emits ``entry_created`` and propagates payback signal."""
        with app.app_context(), _LogCapture(
            "app.services.entry_service",
        ) as cap:
            entry = entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                amount=Decimal("12.50"),
                description="Coffee",
                entry_date=date(2026, 1, 15),
                is_credit=False,
            )

        record = cap.find(EVT_ENTRY_CREATED)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.owner_id == seed_user["user"].id
        assert record.transaction_id == _envelope_transaction.id
        assert record.entry_id == entry.id
        assert record.amount == "12.50"
        assert record.is_credit is False

    def test_update_entry_emits_event(self, app, db, seed_user, _envelope_transaction):
        """update_entry emits ``entry_updated`` with sorted fields_changed."""
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                amount=Decimal("12.50"),
                description="Coffee",
                entry_date=date(2026, 1, 15),
            )
            db.session.commit()
            with _LogCapture("app.services.entry_service") as cap:
                entry_service.update_entry(
                    entry.id, seed_user["user"].id,
                    amount=Decimal("15.00"),
                    description="Coffee + tip",
                )

        record = cap.find(EVT_ENTRY_UPDATED)
        assert record is not None
        assert record.entry_id == entry.id
        assert record.fields_changed == ["amount", "description"]

    def test_delete_entry_emits_event(self, app, db, seed_user, _envelope_transaction):
        """delete_entry emits ``entry_deleted``."""
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                amount=Decimal("12.50"),
                description="Coffee",
                entry_date=date(2026, 1, 15),
            )
            db.session.commit()
            entry_id = entry.id
            with _LogCapture("app.services.entry_service") as cap:
                entry_service.delete_entry(entry_id, seed_user["user"].id)

        record = cap.find(EVT_ENTRY_DELETED)
        assert record is not None
        assert record.entry_id == entry_id

    def test_toggle_cleared_emits_event(self, app, db, seed_user, _envelope_transaction):
        """toggle_cleared emits ``entry_cleared_toggled`` carrying the new value."""
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                amount=Decimal("12.50"),
                description="Coffee",
                entry_date=date(2026, 1, 15),
            )
            db.session.commit()
            assert entry.is_cleared is False
            with _LogCapture("app.services.entry_service") as cap:
                updated = entry_service.toggle_cleared(
                    entry.id, seed_user["user"].id,
                )

        record = cap.find(EVT_ENTRY_CLEARED_TOGGLED)
        assert record is not None
        assert record.entry_id == entry.id
        assert record.is_cleared is True
        assert updated.is_cleared is True

    def test_clear_entries_for_anchor_trueup_emits_event(
        self, app, db, seed_user, _envelope_transaction,
    ):
        """clear_entries_for_anchor_true_up emits the bulk event when rows match."""
        with app.app_context():
            entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                amount=Decimal("12.50"),
                description="Coffee",
                # Past-dated so it is eligible for the anchor true-up flip.
                entry_date=date(2026, 1, 1),
            )
            db.session.commit()
            with _LogCapture("app.services.entry_service") as cap:
                count = entry_service.clear_entries_for_anchor_true_up(
                    seed_user["user"].id,
                )

        assert count == 1
        record = cap.find(EVT_ENTRIES_CLEARED_ON_ANCHOR_TRUEUP)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.cleared_count == 1


# ── Entry credit workflow ──────────────────────────────────────────


class TestEntryCreditWorkflowLogging:
    """entry_credit_workflow emits create/update/delete payback events."""

    def test_payback_create_update_delete_cycle(
        self, app, db, seed_user, _envelope_transaction,
    ):
        """A credit entry creates, updates, then deletes the payback."""
        with app.app_context():
            # CREATE: first credit entry triggers ``entry_payback_created``.
            with _LogCapture(
                "app.services.entry_credit_workflow",
            ) as cap:
                entry1 = entry_service.create_entry(
                    transaction_id=_envelope_transaction.id,
                    user_id=seed_user["user"].id,
                    amount=Decimal("25.00"),
                    description="Card 1",
                    entry_date=date(2026, 1, 10),
                    is_credit=True,
                )
            assert cap.find(EVT_ENTRY_PAYBACK_CREATED) is not None

            # UPDATE: a second credit entry bumps the payback amount.
            with _LogCapture(
                "app.services.entry_credit_workflow",
            ) as cap:
                entry_service.create_entry(
                    transaction_id=_envelope_transaction.id,
                    user_id=seed_user["user"].id,
                    amount=Decimal("10.00"),
                    description="Card 2",
                    entry_date=date(2026, 1, 11),
                    is_credit=True,
                )
            update_record = cap.find(EVT_ENTRY_PAYBACK_UPDATED)
            assert update_record is not None
            assert update_record.new_amount == "35.00"
            assert update_record.previous_amount == "25.00"

            # DELETE: removing the credit entries deletes the payback.
            entry_service.delete_entry(
                entry1.id, seed_user["user"].id,
            )
            db.session.commit()
            with _LogCapture(
                "app.services.entry_credit_workflow",
            ) as cap:
                # Toggling the remaining credit entry off (via update)
                # zeroes total_credit and triggers the delete branch.
                entries = entry_service.get_entries_for_transaction(
                    _envelope_transaction.id, seed_user["user"].id,
                )
                assert len(entries) == 1
                entry_service.update_entry(
                    entries[0].id, seed_user["user"].id,
                    is_credit=False,
                )
            assert cap.find(EVT_ENTRY_PAYBACK_DELETED) is not None


# ── Recurrence engine ──────────────────────────────────────────────


@pytest.fixture
def _recurrence_setup(app, db, seed_user, seed_periods):
    """Build a template + every-period rule to drive the recurrence engine."""
    from app.models.ref import TransactionType  # noqa: WPS433
    expense_type = db.session.query(
        TransactionType,
    ).filter_by(name="Expense").one()

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.EVERY_PERIOD,
        ),
        start_period_id=seed_periods[0].id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        name="Recurring Test",
        default_amount=Decimal("100.00"),
        recurrence_rule_id=rule.id,
        is_active=True,
    )
    db.session.add(template)
    db.session.commit()
    return template


class TestRecurrenceEngineLogging:
    """recurrence_engine emits generate / regenerate / resolve events."""

    def test_generate_emits_event(self, app, db, seed_user, _recurrence_setup, seed_periods):
        """generate_for_template emits ``recurrence_generated`` with count."""
        with app.app_context(), _LogCapture(
            "app.services.recurrence_engine",
        ) as cap:
            created = recurrence_engine.generate_for_template(
                _recurrence_setup, seed_periods[:3],
                seed_user["scenario"].id,
            )

        assert len(created) == 3
        record = cap.find(EVT_RECURRENCE_GENERATED)
        assert record is not None
        assert record.template_id == _recurrence_setup.id
        assert record.count == 3
        assert record.user_id == seed_user["user"].id

    def test_regenerate_emits_event(self, app, db, seed_user, _recurrence_setup, seed_periods):
        """regenerate_for_template emits ``recurrence_regenerated``."""
        with app.app_context():
            recurrence_engine.generate_for_template(
                _recurrence_setup, seed_periods[:3],
                seed_user["scenario"].id,
            )
            db.session.commit()
            with _LogCapture("app.services.recurrence_engine") as cap:
                recurrence_engine.regenerate_for_template(
                    _recurrence_setup, seed_periods[:3],
                    seed_user["scenario"].id,
                )

        record = cap.find(EVT_RECURRENCE_REGENERATED)
        assert record is not None
        assert record.template_id == _recurrence_setup.id

    def test_cross_user_blocked_emits_event(
        self, app, db, seed_user, _recurrence_setup, seed_periods,
        seed_second_user,
    ):
        """A scenario from another user emits ``cross_user_blocked``."""
        with app.app_context(), _LogCapture(
            "app.services.recurrence_engine",
        ) as cap:
            created = recurrence_engine.generate_for_template(
                _recurrence_setup, seed_periods[:1],
                seed_second_user["scenario"].id,
            )

        assert created == []
        record = cap.find(EVT_CROSS_USER_BLOCKED)
        assert record is not None
        assert record.levelno == logging.WARNING
        assert record.template_user_id == seed_user["user"].id
        assert record.scenario_id == seed_second_user["scenario"].id

    def test_resolve_conflicts_keep_emits_event(self, app, db, seed_user):
        """resolve_conflicts(action='keep') emits the resolution event."""
        with app.app_context(), _LogCapture(
            "app.services.recurrence_engine",
        ) as cap:
            recurrence_engine.resolve_conflicts(
                [], "keep", seed_user["user"].id,
            )

        record = cap.find(EVT_RECURRENCE_CONFLICTS_RESOLVED)
        assert record is not None
        assert record.action == "keep"

    def test_resolve_conflicts_cross_user_emits_access_event(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A cross-user transaction id emits ``access_denied_cross_user``.

        The resolve helper silently skips the row but the audit trail
        must record the probe so SOC tooling sees it.
        """
        from app.models.ref import TransactionType  # noqa: WPS433
        expense_type = db.session.query(
            TransactionType,
        ).filter_by(name="Expense").one()
        projected = db.session.query(Status).filter_by(
            name="Projected",
        ).one()

        # Build a transaction owned by the second user.
        from app.services import pay_period_service as pps  # noqa: WPS433
        s2_periods = pps.generate_pay_periods(
            user_id=seed_second_user["user"].id,
            start_date=date(2027, 6, 1),
            num_periods=1,
            cadence_days=14,
        )
        db.session.flush()

        s2_txn = Transaction(
            pay_period_id=s2_periods[0].id,
            scenario_id=seed_second_user["scenario"].id,
            account_id=seed_second_user["account"].id,
            status_id=projected.id,
            name="Other Owner Txn",
            category_id=seed_second_user["categories"]["Rent"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("75.00"),
            is_override=True,
        )
        db.session.add(s2_txn)
        db.session.commit()

        with app.app_context(), _LogCapture(
            "app.services.recurrence_engine",
        ) as cap:
            recurrence_engine.resolve_conflicts(
                [s2_txn.id], "update", seed_user["user"].id,
                new_amount=Decimal("80.00"),
            )

        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None
        assert record.category == ACCESS
        assert record.levelno == logging.WARNING
        assert record.user_id == seed_user["user"].id
        assert record.owner_id == seed_second_user["user"].id


# ── Carry forward ──────────────────────────────────────────────────


class TestCarryForwardLogging:
    """carry_forward_unpaid emits ``carry_forward`` with breakdown counts."""

    def test_carry_forward_emits_event(
        self, app, db, seed_user, seed_periods,
    ):
        """A successful carry-forward emits the structured event."""
        from app.models.ref import TransactionType  # noqa: WPS433
        expense_type = db.session.query(
            TransactionType,
        ).filter_by(name="Expense").one()
        projected = db.session.query(Status).filter_by(name="Projected").one()

        # One ad-hoc projected expense in period 0 -- discrete partition.
        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="Ad-hoc Expense",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("50.00"),
        )
        db.session.add(txn)
        db.session.commit()

        with app.app_context(), _LogCapture(
            "app.services.carry_forward_service",
        ) as cap:
            count = carry_forward_service.carry_forward_unpaid(
                source_period_id=seed_periods[0].id,
                target_period_id=seed_periods[1].id,
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
            )

        assert count == 1
        record = cap.find(EVT_CARRY_FORWARD)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.count == 1
        assert record.from_period_id == seed_periods[0].id
        assert record.to_period_id == seed_periods[1].id
        assert record.discrete_count == 1
        assert record.envelope_count == 0
        assert record.transfer_count == 0


# ── Transfer recurrence ────────────────────────────────────────────


class TestTransferRecurrenceLogging:
    """transfer_recurrence emits the analogous events for transfers."""

    def test_generate_emits_event(self, app, db, _transfer_setup, seed_periods):
        """generate_for_template emits ``transfer_recurrence_generated``."""
        td = _transfer_setup

        # Build a transfer template and rule.
        from app.models.transfer_template import TransferTemplate  # noqa: WPS433

        rule = RecurrenceRule(
            user_id=td["user"].id,
            pattern_id=ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_PERIOD,
            ),
            start_period_id=seed_periods[0].id,
        )
        db.session.add(rule)
        db.session.flush()

        template = TransferTemplate(
            user_id=td["user"].id,
            from_account_id=td["account"].id,
            to_account_id=td["savings"].id,
            category_id=td["category"].id,
            name="Recurring Transfer",
            default_amount=Decimal("75.00"),
            recurrence_rule_id=rule.id,
            is_active=True,
        )
        db.session.add(template)
        db.session.commit()

        with app.app_context(), _LogCapture(
            "app.services.transfer_recurrence",
        ) as cap:
            created = transfer_recurrence.generate_for_template(
                template, seed_periods[:2], td["scenario"].id,
            )

        assert len(created) == 2
        record = cap.find(EVT_TRANSFER_RECURRENCE_GENERATED)
        assert record is not None
        assert record.user_id == td["user"].id
        assert record.template_id == template.id
        assert record.count == 2


# ── Transaction service ────────────────────────────────────────────


class TestTransactionServiceLogging:
    """transaction_service.settle_from_entries emits the settle event."""

    def test_settle_from_entries_emits_event(
        self, app, db, seed_user, _envelope_transaction,
    ):
        """settle_from_entries emits ``transaction_settled_from_entries``."""
        with app.app_context():
            entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                amount=Decimal("33.00"),
                description="Test entry",
                entry_date=date(2026, 1, 10),
            )
            db.session.commit()

            with _LogCapture(
                "app.services.transaction_service",
            ) as cap:
                # Reload to ensure relationships are fresh.
                txn = db.session.get(Transaction, _envelope_transaction.id)
                transaction_service.settle_from_entries(txn)

        record = cap.find(EVT_TRANSACTION_SETTLED_FROM_ENTRIES)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.transaction_id == _envelope_transaction.id
        assert record.actual_amount == "33.00"
        assert record.explicit_paid_at is False
