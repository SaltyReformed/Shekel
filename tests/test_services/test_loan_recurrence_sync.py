"""Tests for loan_recurrence_sync (Risk R-4: recurring end_date off the GET path).

``app.services.loan_recurrence_sync`` keeps a loan's recurring-payment
``RecurrenceRule.end_date`` equal to the loan's projected payoff, so the
recurrence engine stops generating shadow transactions past payoff.  It used to
run as a write on the loan-detail GET (Risk R-4); it now runs at every
payoff-affecting mutation.  These tests pin the pure payoff logic
(``projected_payoff_end_date``) with hand-built schedules and the service
(``sync_recurring_payment_end_date``) against a resolvable loan.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import loan_recurrence_sync
from app.services.loan_recurrence_sync import projected_payoff_end_date
from tests._test_helpers import (
    create_loan_account,
    freeze_today,
    make_transfer_template,
)


def _row(remaining_balance: str, payment_date: date) -> SimpleNamespace:
    """Return a schedule-row stub carrying only the fields the payoff logic reads."""
    return SimpleNamespace(
        remaining_balance=Decimal(remaining_balance), payment_date=payment_date,
    )


class TestProjectedPayoffEndDate:
    """The three payoff cases the recurrence end_date derives from."""

    def test_normal_payoff_is_the_last_payment_date(self):
        """A schedule that reaches zero ends recurrence on its last payment date."""
        schedule = [
            _row("500.00", date(2030, 1, 1)),
            _row("0.00", date(2030, 2, 1)),
        ]
        assert projected_payoff_end_date(
            schedule, date(2020, 1, 1),
        ) == date(2030, 2, 1)

    def test_empty_schedule_falls_back_to_origination(self):
        """An already-paid-off loan (empty schedule) ends recurrence at origination.

        A past date halts future generation -- the retired GET writer's fallback.
        """
        assert projected_payoff_end_date([], date(2020, 3, 15)) == date(2020, 3, 15)

    def test_negative_amortization_returns_none(self):
        """A schedule ending with a positive balance leaves recurrence indefinite.

        Paying under the monthly interest never retires the loan, so end_date is
        None (recurrence continues until the user adjusts the payment).
        """
        schedule = [_row("100000.00", date(2030, 2, 1))]
        assert projected_payoff_end_date(schedule, date(2020, 1, 1)) is None


class TestSyncRecurringPaymentEndDate:
    """The relocated end_date write, driven directly against a resolvable loan."""

    @pytest.fixture(autouse=True)
    def _frozen(self, monkeypatch):
        """Freeze today mid-loan so the projected schedule is deterministic."""
        freeze_today(monkeypatch, date(2026, 7, 1))

    def _loan(self, seed_user, db_session):
        """A resolvable 24-month $12,000 loan originated 2025-01-01 (pays off 2027)."""
        return create_loan_account(
            seed_user, db_session, name="Recurring Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2025, 1, 1),
        )

    def test_sets_end_date_to_the_projected_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """A configured loan with a recurring template gets its payoff end_date.

        The 24-month loan originated 2025-01-01 pays off 2027-01-01, so the
        recurring rule's end_date lands there (a future date, a real ``date``).
        """
        with app.app_context():
            loan = self._loan(seed_user, db.session)
            tpl = make_transfer_template(db.session, seed_user, loan)
            db.session.commit()
            rule = tpl.recurrence_rule
            assert rule.end_date is None

            loan_recurrence_sync.sync_recurring_payment_end_date(loan.id)
            db.session.commit()
            db.session.refresh(rule)

            assert rule.end_date == date(2027, 1, 1)
            assert isinstance(rule.end_date, date)

    def test_is_idempotent(self, app, db, seed_user, seed_periods):
        """A second sync at the same payoff writes nothing new."""
        with app.app_context():
            loan = self._loan(seed_user, db.session)
            tpl = make_transfer_template(db.session, seed_user, loan)
            db.session.commit()
            rule = tpl.recurrence_rule
            loan_recurrence_sync.sync_recurring_payment_end_date(loan.id)
            db.session.commit()
            first = rule.end_date

            loan_recurrence_sync.sync_recurring_payment_end_date(loan.id)
            db.session.commit()
            db.session.refresh(rule)
            assert rule.end_date == first

    def test_no_template_is_a_noop(self, app, db, seed_user, seed_periods):
        """A loan with no recurring transfer is a safe no-op (no crash)."""
        with app.app_context():
            loan = self._loan(seed_user, db.session)
            # No template created; the sync must return cleanly.
            loan_recurrence_sync.sync_recurring_payment_end_date(loan.id)
            db.session.commit()

    def test_unconfigured_account_is_a_noop(self, app, db, seed_user):
        """A non-loan account resolves to nothing, so the sync is a no-op."""
        with app.app_context():
            loan_recurrence_sync.sync_recurring_payment_end_date(
                seed_user["account"].id,
            )
            db.session.commit()
