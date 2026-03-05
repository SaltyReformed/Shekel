"""
Tests for calculate_balances_with_interest() in balance_calculator.

Validates that HYSA interest projections layer correctly on top of
the existing balance roll-forward logic.
"""

from collections import namedtuple
from datetime import date, timedelta
from decimal import Decimal

from app.services.balance_calculator import (
    calculate_balances,
    calculate_balances_with_interest,
)

# Lightweight stubs to avoid needing full SQLAlchemy models.
Period = namedtuple("Period", ["id", "period_index", "start_date", "end_date"])
HysaParams = namedtuple("HysaParams", ["apy", "compounding_frequency"])


def _make_periods(count=4):
    """Create a list of 14-day periods starting 2026-01-02."""
    base = date(2026, 1, 2)
    periods = []
    for i in range(count):
        start = base + timedelta(days=i * 14)
        end = base + timedelta(days=(i + 1) * 14)
        periods.append(Period(id=i + 1, period_index=i, start_date=start, end_date=end))
    return periods


class TestHysaBalanceWithInterest:
    """Tests for calculate_balances_with_interest()."""

    def test_hysa_balance_includes_interest(self):
        """Balance roll-forward with HYSA params adds interest each period."""
        periods = _make_periods(3)
        params = HysaParams(apy=Decimal("0.04500"), compounding_frequency="daily")

        balances, interest = calculate_balances_with_interest(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            hysa_params=params,
        )

        # Balance should be higher than the flat anchor.
        assert balances[1] > Decimal("10000.00")
        # Interest should be positive for each period.
        assert interest[1] > Decimal("0.00")

    def test_hysa_interest_compounds_across_periods(self):
        """Interest from period N increases balance for period N+1 interest calc."""
        periods = _make_periods(3)
        params = HysaParams(apy=Decimal("0.04500"), compounding_frequency="daily")

        balances, interest = calculate_balances_with_interest(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            hysa_params=params,
        )

        # Each subsequent period should earn slightly more interest
        # because the balance is higher.
        assert interest[2] > interest[1]
        assert interest[3] > interest[2]

    def test_hysa_with_transfers(self):
        """Transfers + interest projection combined correctly."""
        periods = _make_periods(2)
        params = HysaParams(apy=Decimal("0.04500"), compounding_frequency="daily")

        # Simulate a transfer into the account.
        Transfer = namedtuple("Transfer", [
            "pay_period_id", "from_account_id", "to_account_id", "amount", "status", "is_deleted",
        ])
        Status = namedtuple("Status", ["name"])

        transfers = [
            Transfer(
                pay_period_id=2,
                from_account_id=99,
                to_account_id=1,
                amount=Decimal("500.00"),
                status=Status(name="projected"),
                is_deleted=False,
            ),
        ]

        balances, interest = calculate_balances_with_interest(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            transfers=transfers,
            account_id=1,
            hysa_params=params,
        )

        # Period 2 should include the $500 transfer + interest on the higher balance.
        assert balances[2] > Decimal("10500.00")

    def test_hysa_zero_apy_no_interest(self):
        """APY=0 → balances identical to non-HYSA."""
        periods = _make_periods(3)
        params = HysaParams(apy=Decimal("0.00000"), compounding_frequency="daily")

        base_balances = calculate_balances(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
        )

        hysa_balances, interest = calculate_balances_with_interest(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            hysa_params=params,
        )

        for pid in base_balances:
            assert hysa_balances[pid] == base_balances[pid]
            assert interest[pid] == Decimal("0.00")

    def test_non_hysa_ignores_interest(self):
        """Without hysa_params, behavior is identical to existing."""
        periods = _make_periods(3)

        base_balances = calculate_balances(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
        )

        hysa_balances, interest = calculate_balances_with_interest(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            hysa_params=None,
        )

        for pid in base_balances:
            assert hysa_balances[pid] == base_balances[pid]
        assert interest == {}

    def test_interest_by_period_dict(self):
        """Verify interest_by_period return value has correct per-period amounts."""
        periods = _make_periods(3)
        params = HysaParams(apy=Decimal("0.04500"), compounding_frequency="daily")

        balances, interest = calculate_balances_with_interest(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            hysa_params=params,
        )

        # Should have interest entries for every period in balances.
        assert set(interest.keys()) == set(balances.keys())
        # All interest values should be Decimal with 2 decimal places.
        for pid, amt in interest.items():
            assert isinstance(amt, Decimal)
            assert amt == amt.quantize(Decimal("0.01"))
