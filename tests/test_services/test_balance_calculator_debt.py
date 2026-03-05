"""
Tests for the debt-aware balance calculator (calculate_balances_with_amortization).
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.balance_calculator import (
    calculate_balances,
    calculate_balances_with_amortization,
)


def _period(id, index, start, end):
    """Create a mock PayPeriod."""
    return SimpleNamespace(
        id=id, period_index=index, start_date=start, end_date=end,
    )


def _transfer(pay_period_id, from_account_id, to_account_id, amount, status="projected"):
    """Create a mock Transfer."""
    return SimpleNamespace(
        pay_period_id=pay_period_id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        amount=Decimal(str(amount)),
        status=SimpleNamespace(name=status),
    )


def _loan_params(principal="200000", rate="0.06", term=360, orig=date(2025, 1, 1), day=1):
    """Create mock loan params."""
    return SimpleNamespace(
        current_principal=Decimal(principal),
        interest_rate=Decimal(rate),
        term_months=term,
        origination_date=orig,
        payment_day=day,
    )


class TestDebtBalanceCalculator:
    """Tests for calculate_balances_with_amortization."""

    def test_debt_balance_with_payments(self):
        """Transfers reduce balance by principal portion only."""
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ]
        params = _loan_params(principal="100000", rate="0.06", term=360)

        # Transfer of $599.55 (standard payment for $100k at 6%, 30yr)
        transfers = [
            _transfer(2, from_account_id=99, to_account_id=1, amount="599.55"),
        ]

        balances, principal_by_period = calculate_balances_with_amortization(
            anchor_balance=Decimal("100000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            transfers=transfers,
            account_id=1,
            loan_params=params,
        )

        # Balance should be reduced by principal, not full payment.
        assert 1 in balances
        assert 2 in balances
        assert balances[2] < Decimal("100000.00")
        # Principal portion should be positive.
        assert principal_by_period[2] > Decimal("0.00")

    def test_debt_balance_no_payments(self):
        """No transfers → balance unchanged."""
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ]
        params = _loan_params()

        balances, principal_by_period = calculate_balances_with_amortization(
            anchor_balance=Decimal("200000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            transfers=[],
            account_id=1,
            loan_params=params,
        )

        assert balances[1] == Decimal("200000.00")
        assert balances[2] == Decimal("200000.00")
        assert principal_by_period[2] == Decimal("0.00")

    def test_debt_principal_tracking(self):
        """principal_by_period dict has correct amounts."""
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
            _period(3, 2, date(2026, 1, 29), date(2026, 2, 11)),
        ]
        params = _loan_params(principal="100000", rate="0.06", term=360)

        transfers = [
            _transfer(2, from_account_id=99, to_account_id=1, amount="599.55"),
            _transfer(3, from_account_id=99, to_account_id=1, amount="599.55"),
        ]

        balances, principal_by_period = calculate_balances_with_amortization(
            anchor_balance=Decimal("100000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            transfers=transfers,
            account_id=1,
            loan_params=params,
        )

        assert 2 in principal_by_period
        assert 3 in principal_by_period
        assert principal_by_period[2] > Decimal("0.00")
        assert principal_by_period[3] > Decimal("0.00")
        # Balance should decrease over time.
        assert balances[3] < balances[2]

    def test_non_debt_unchanged(self):
        """Without loan_params → identical to base calculate_balances."""
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ]

        base_balances = calculate_balances(
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
        )

        amort_balances, principal_by_period = calculate_balances_with_amortization(
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            loan_params=None,
        )

        assert amort_balances == base_balances
        assert principal_by_period == {}
