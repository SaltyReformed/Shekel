"""
Tests for calculate_balances_with_interest() in balance_calculator.

Validates that HYSA interest projections layer correctly on top of
the existing balance roll-forward logic.
"""

import calendar
from collections import namedtuple
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.services.balance_calculator import (
    calculate_balances,
    calculate_balances_with_interest,
)

# Lightweight stubs to avoid needing full SQLAlchemy models.
Period = namedtuple(
    "Period", ["id", "period_index", "start_date", "end_date"]
)
HysaParams = namedtuple(
    "HysaParams", ["apy", "compounding_frequency"]
)

# Transfer/Status stubs for tests with account transfers.
TransferStub = namedtuple("TransferStub", [
    "pay_period_id", "from_account_id", "to_account_id",
    "amount", "status", "is_deleted",
])
StatusStub = namedtuple("StatusStub", ["name"])


def _make_periods(count=4):
    """Create a list of 14-day periods starting 2026-01-02."""
    base = date(2026, 1, 2)
    periods = []
    for i in range(count):
        start = base + timedelta(days=i * 14)
        end = base + timedelta(days=(i + 1) * 14)
        periods.append(Period(
            id=i + 1, period_index=i,
            start_date=start, end_date=end,
        ))
    return periods


class TestHysaBalanceWithInterest:
    """Tests for calculate_balances_with_interest()."""

    def test_hysa_balance_includes_interest(self):
        """Exact balance and interest with HYSA daily compounding.

        Parameters: $10,000 anchor, 4.5% APY, daily compounding,
        3 periods (14 days each), no transactions.

        Proves each period's balance and interest exactly match
        hand-computed values from the daily compounding formula.
        """
        periods = _make_periods(3)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
        )

        balances, interest = calculate_balances_with_interest(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            hysa_params=params,
        )

        # Period 1: interest on $10,000 for 14 days at 4.5% daily
        # = Q(10000 * ((1+0.045/365)^14 - 1)) = Q(17.2741..) = 17.27
        # balance = 10000.00 + 17.27 = 10017.27
        assert balances[1] == Decimal("10017.27"), (
            f"Period 1 balance: expected 10017.27, "
            f"got {balances[1]}"
        )
        assert interest[1] == Decimal("17.27"), (
            f"Period 1 interest: expected 17.27, "
            f"got {interest[1]}"
        )
        # Period 2: interest on $10,017.27 for 14 days
        # = Q(10017.27 * ((1+0.045/365)^14-1)) = Q(17.3039..) = 17.30
        # balance = 10000.00 + 17.27 + 17.30 = 10034.57
        assert balances[2] == Decimal("10034.57"), (
            f"Period 2 balance: expected 10034.57, "
            f"got {balances[2]}"
        )
        assert interest[2] == Decimal("17.30"), (
            f"Period 2 interest: expected 17.30, "
            f"got {interest[2]}"
        )
        # Period 3: interest on $10,034.57 for 14 days
        # = Q(10034.57 * ((1+0.045/365)^14-1)) = Q(17.3338..) = 17.33
        # balance = 10000.00 + 51.90 = 10051.90
        assert balances[3] == Decimal("10051.90"), (
            f"Period 3 balance: expected 10051.90, "
            f"got {balances[3]}"
        )
        assert interest[3] == Decimal("17.33"), (
            f"Period 3 interest: expected 17.33, "
            f"got {interest[3]}"
        )

    def test_hysa_interest_compounds_across_periods(self):
        """Interest compounds: each period earns more than prior.

        Parameters: $10,000 anchor, 4.5% APY, daily compounding,
        3 periods (14 days each), no transactions.

        Proves exact interest and balance values, confirming that
        compounding causes each period's interest to increase.
        """
        periods = _make_periods(3)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
        )

        balances, interest = calculate_balances_with_interest(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            hysa_params=params,
        )

        # Period 1: Q(10000.00 * ((1+0.045/365)^14-1)) = 17.27
        assert interest[1] == Decimal("17.27"), (
            f"Period 1 interest: expected 17.27, "
            f"got {interest[1]}"
        )
        assert balances[1] == Decimal("10017.27"), (
            f"Period 1 balance: expected 10017.27, "
            f"got {balances[1]}"
        )
        # Period 2: Q(10017.27 * ((1+0.045/365)^14-1)) = 17.30
        assert interest[2] == Decimal("17.30"), (
            f"Period 2 interest: expected 17.30, "
            f"got {interest[2]}"
        )
        assert balances[2] == Decimal("10034.57"), (
            f"Period 2 balance: expected 10034.57, "
            f"got {balances[2]}"
        )
        # Period 3: Q(10034.57 * ((1+0.045/365)^14-1)) = 17.33
        assert interest[3] == Decimal("17.33"), (
            f"Period 3 interest: expected 17.33, "
            f"got {interest[3]}"
        )
        assert balances[3] == Decimal("10051.90"), (
            f"Period 3 balance: expected 10051.90, "
            f"got {balances[3]}"
        )

    def test_hysa_with_transfers(self):
        """Transfers and interest projection combined correctly.

        Parameters: $10,000 anchor, 4.5% APY, daily compounding,
        2 periods, $500 transfer into account in period 2.

        Proves exact balance and interest when a transfer increases
        the base balance mid-projection.
        """
        periods = _make_periods(2)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
        )

        # Simulate a transfer into the account.
        Transfer = namedtuple("Transfer", [
            "pay_period_id", "from_account_id",
            "to_account_id", "amount",
            "status", "is_deleted",
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

        # Period 1: interest on $10,000 for 14 days at 4.5% daily
        # = Q(10000 * ((1+0.045/365)^14-1)) = 17.27
        # balance = 10000.00 + 17.27 = 10017.27
        assert balances[1] == Decimal("10017.27"), (
            f"Period 1 balance: expected 10017.27, "
            f"got {balances[1]}"
        )
        assert interest[1] == Decimal("17.27"), (
            f"Period 1 interest: expected 17.27, "
            f"got {interest[1]}"
        )
        # Period 2: base=10500 (+$500 xfer), running=10517.27
        # interest = Q(10517.27 * ((1+0.045/365)^14-1)) = 18.17
        # balance = 10517.27 + 18.17 = 10535.44
        assert balances[2] == Decimal("10535.44"), (
            f"Period 2 balance: expected 10535.44, "
            f"got {balances[2]}"
        )
        assert interest[2] == Decimal("18.17"), (
            f"Period 2 interest: expected 18.17, "
            f"got {interest[2]}"
        )

    def test_hysa_zero_apy_no_interest(self):
        """APY=0 → balances identical to non-HYSA."""
        periods = _make_periods(3)
        params = HysaParams(
            apy=Decimal("0.00000"),
            compounding_frequency="daily",
        )

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
        assert not interest

    def test_interest_by_period_dict(self):
        """Interest dict has correct types, quantization, and values.

        Parameters: $10,000 anchor, 4.5% APY, daily compounding,
        3 periods (14 days each), no transactions.

        Proves interest_by_period keys match balances keys, all
        values are Decimal with 2 decimal places, and all values
        match hand-computed exact amounts.
        """
        periods = _make_periods(3)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
        )

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
        for _, amt in interest.items():
            assert isinstance(amt, Decimal)
            assert amt == amt.quantize(Decimal("0.01"))

        # Exact value assertions (same setup as above tests).
        # Period 1: Q(10000 * ((1+0.045/365)^14-1)) = 17.27
        assert interest[1] == Decimal("17.27"), (
            f"Period 1 interest: expected 17.27, "
            f"got {interest[1]}"
        )
        # Period 2: Q(10017.27 * ((1+0.045/365)^14-1)) = 17.30
        assert interest[2] == Decimal("17.30"), (
            f"Period 2 interest: expected 17.30, "
            f"got {interest[2]}"
        )
        # Period 3: Q(10034.57 * ((1+0.045/365)^14-1)) = 17.33
        assert interest[3] == Decimal("17.33"), (
            f"Period 3 interest: expected 17.33, "
            f"got {interest[3]}"
        )
        # Balances = base + cumulative interest
        # Period 1: 10000.00 + 17.27 = 10017.27
        assert balances[1] == Decimal("10017.27"), (
            f"Period 1 balance: expected 10017.27, "
            f"got {balances[1]}"
        )
        # Period 2: 10000.00 + 34.57 = 10034.57
        assert balances[2] == Decimal("10034.57"), (
            f"Period 2 balance: expected 10034.57, "
            f"got {balances[2]}"
        )
        # Period 3: 10000.00 + 51.90 = 10051.90
        assert balances[3] == Decimal("10051.90"), (
            f"Period 3 balance: expected 10051.90, "
            f"got {balances[3]}"
        )

    # --- New tests below ---

    def test_hysa_26_period_compounding_no_drift(self):  # pylint: disable=too-many-locals
        """Verify 26-period daily compounding with no drift.

        Parameters: $10,000 anchor, 4.5% APY, daily compounding,
        26 periods (14 days each), no transactions.

        Proves that the compounding accumulation across a full
        year of biweekly periods matches an independent oracle
        using raw Decimal arithmetic, with no rounding drift.
        """
        periods = _make_periods(26)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
        )

        # --- Independent oracle (no production code) ---
        # Daily rate = APY / 365
        daily_rate = params.apy / Decimal("365")
        base_bal = Decimal("10000.00")
        expected_balances = {}
        expected_interest = {}
        interest_cumulative = Decimal("0.00")

        for period in periods:
            # running = base + prior cumulative interest
            running = base_bal + interest_cumulative
            # daily compounding: balance * ((1+r)^days - 1)
            days = Decimal(str(
                (period.end_date - period.start_date).days
            ))
            interest = (
                running * ((1 + daily_rate) ** days - 1)
            ).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            interest_cumulative += interest
            expected_balances[period.id] = running + interest
            expected_interest[period.id] = interest

        # Compounding verification: period 25 interest should
        # exceed period 0 because the balance has grown.
        assert (
            expected_interest[periods[25].id]
            > expected_interest[periods[0].id]
        ), (
            "Oracle sanity: period 25 interest "
            f"{expected_interest[periods[25].id]} should exceed "
            f"period 0 interest "
            f"{expected_interest[periods[0].id]}"
        )

        # --- Call the service under test ---
        balances, interest_result = (
            calculate_balances_with_interest(
                anchor_balance=base_bal,
                anchor_period_id=1,
                periods=periods,
                transactions=[],
                hysa_params=params,
            )
        )

        # --- Assert every period (oracle vs service) ---
        for i, period in enumerate(periods):
            pid = period.id
            assert balances[pid] == expected_balances[pid], (
                f"Period {i} (id={pid}): "
                f"expected balance "
                f"{expected_balances[pid]}, "
                f"got {balances[pid]}, "
                f"diff={balances[pid] - expected_balances[pid]}"
            )
            assert (
                interest_result[pid] == expected_interest[pid]
            ), (
                f"Period {i} (id={pid}): "
                f"expected interest "
                f"{expected_interest[pid]}, "
                f"got {interest_result[pid]}, "
                f"diff="
                f"{interest_result[pid] - expected_interest[pid]}"
            )

        # Cumulative cross-check: final balance = anchor + sum
        # 10000.00 + 458.93 = 10458.93
        total_i = sum(expected_interest.values())
        assert balances[periods[25].id] == base_bal + total_i, (
            f"Cumulative check: "
            f"expected {base_bal + total_i}, "
            f"got {balances[periods[25].id]}"
        )

    def test_hysa_monthly_compounding_exact(self):
        """Verify monthly compounding produces exact values.

        Parameters: $10,000 anchor, 4.5% APY, monthly compounding,
        3 periods (14 days each) starting 2026-01-02.

        Proves that the monthly formula (balance * apy/12 *
        period_days/days_in_month) produces correct results.
        days_in_month is derived from period_start.month.
        """
        periods = _make_periods(3)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="monthly",
        )

        # --- Independent oracle (no production code) ---
        # Monthly rate = APY / 12 = 0.00375
        monthly_rate = params.apy / Decimal("12")
        base_bal = Decimal("10000.00")
        expected_balances = {}
        expected_interest = {}
        interest_cumulative = Decimal("0.00")

        for period in periods:
            days = Decimal(str(
                (period.end_date - period.start_date).days
            ))
            # days_in_month from period_start.month
            dim = Decimal(str(calendar.monthrange(
                period.start_date.year,
                period.start_date.month,
            )[1]))
            running = base_bal + interest_cumulative
            # monthly: balance * (apy/12) * (days/days_in_month)
            interest = (
                running * monthly_rate * (days / dim)
            ).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            interest_cumulative += interest
            expected_balances[period.id] = running + interest
            expected_interest[period.id] = interest

        # --- Call service under test ---
        balances, interest_result = (
            calculate_balances_with_interest(
                anchor_balance=Decimal("10000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[],
                hysa_params=params,
            )
        )

        # All periods start in January → days_in_month = 31
        # Period 1: Q(10000 * 0.00375 * 14/31) = 16.94
        assert balances[1] == expected_balances[1], (
            f"Period 1 balance: expected "
            f"{expected_balances[1]}, got {balances[1]}"
        )
        assert interest_result[1] == expected_interest[1], (
            f"Period 1 interest: expected "
            f"{expected_interest[1]}, "
            f"got {interest_result[1]}"
        )
        # Period 2: Q(10016.94 * 0.00375 * 14/31) = 16.96
        assert balances[2] == expected_balances[2], (
            f"Period 2 balance: expected "
            f"{expected_balances[2]}, got {balances[2]}"
        )
        assert interest_result[2] == expected_interest[2], (
            f"Period 2 interest: expected "
            f"{expected_interest[2]}, "
            f"got {interest_result[2]}"
        )
        # Period 3: starts Jan 30, crosses into Feb, but
        # days_in_month uses period_start.month = Jan = 31
        # Q(10033.90 * 0.00375 * 14/31) = 16.99
        assert balances[3] == expected_balances[3], (
            f"Period 3 balance: expected "
            f"{expected_balances[3]}, got {balances[3]}"
        )
        assert interest_result[3] == expected_interest[3], (
            f"Period 3 interest: expected "
            f"{expected_interest[3]}, "
            f"got {interest_result[3]}"
        )

    def test_hysa_quarterly_compounding_exact(self):
        """Verify quarterly compounding produces exact values.

        Parameters: $10,000 anchor, 4.5% APY, quarterly
        compounding, 3 periods (14 days each).

        Proves that the quarterly formula (balance * apy/4 *
        period_days/91) produces correct results.
        """
        periods = _make_periods(3)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="quarterly",
        )

        # --- Independent oracle (no production code) ---
        # Quarterly rate = APY / 4 = 0.01125
        quarterly_rate = params.apy / Decimal("4")
        base_bal = Decimal("10000.00")
        expected_balances = {}
        expected_interest = {}
        interest_cumulative = Decimal("0.00")

        for period in periods:
            days = Decimal(str(
                (period.end_date - period.start_date).days
            ))
            running = base_bal + interest_cumulative
            # quarterly: balance * (apy/4) * (days/91)
            interest = (
                running * quarterly_rate
                * (days / Decimal("91"))
            ).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            interest_cumulative += interest
            expected_balances[period.id] = running + interest
            expected_interest[period.id] = interest

        # --- Call service under test ---
        balances, interest_result = (
            calculate_balances_with_interest(
                anchor_balance=Decimal("10000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[],
                hysa_params=params,
            )
        )

        # Period 1: Q(10000 * 0.01125 * 14/91) = 17.31
        assert balances[1] == expected_balances[1], (
            f"Period 1 balance: expected "
            f"{expected_balances[1]}, got {balances[1]}"
        )
        assert interest_result[1] == expected_interest[1], (
            f"Period 1 interest: expected "
            f"{expected_interest[1]}, "
            f"got {interest_result[1]}"
        )
        # Period 2: Q(10017.31 * 0.01125 * 14/91) = 17.34
        assert balances[2] == expected_balances[2], (
            f"Period 2 balance: expected "
            f"{expected_balances[2]}, got {balances[2]}"
        )
        assert interest_result[2] == expected_interest[2], (
            f"Period 2 interest: expected "
            f"{expected_interest[2]}, "
            f"got {interest_result[2]}"
        )
        # Period 3: Q(10034.65 * 0.01125 * 14/91) = 17.37
        assert balances[3] == expected_balances[3], (
            f"Period 3 balance: expected "
            f"{expected_balances[3]}, got {balances[3]}"
        )
        assert interest_result[3] == expected_interest[3], (
            f"Period 3 interest: expected "
            f"{expected_interest[3]}, "
            f"got {interest_result[3]}"
        )

    def test_hysa_invalid_compounding_frequency(self):
        """Unknown compounding frequency produces zero interest.

        Parameters: $10,000 anchor, 4.5% APY,
        compounding_frequency="invalid_string", 3 periods.

        Proves that calculate_interest returns Decimal("0.00")
        for an unknown frequency (else: return ZERO branch),
        so balances equal base balances exactly.
        """
        periods = _make_periods(3)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="invalid_string",
        )

        balances, interest = calculate_balances_with_interest(
            anchor_balance=Decimal("10000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            hysa_params=params,
        )

        for pid in [1, 2, 3]:
            # All interest = 0 (unknown frequency → ZERO),
            # so balances = base = anchor = 10000.00
            assert balances[pid] == Decimal("10000.00"), (
                f"Period {pid} balance: expected 10000.00, "
                f"got {balances[pid]}"
            )
            assert interest[pid] == Decimal("0.00"), (
                f"Period {pid} interest: expected 0.00, "
                f"got {interest[pid]}"
            )

    def test_hysa_high_apy_no_overflow(self):
        """High APY and large balance produce correct results.

        Parameters: $50,000 anchor, 10% APY, daily compounding,
        3 periods (14 days each).

        Proves that large balances and high rates do not cause
        precision loss or overflow in Decimal arithmetic.
        """
        periods = _make_periods(3)
        params = HysaParams(
            apy=Decimal("0.10000"),
            compounding_frequency="daily",
        )

        # --- Independent oracle (no production code) ---
        # Daily rate = 0.10 / 365
        daily_rate = params.apy / Decimal("365")
        base_bal = Decimal("50000.00")
        expected_balances = {}
        expected_interest = {}
        interest_cumulative = Decimal("0.00")

        for period in periods:
            days = Decimal(str(
                (period.end_date - period.start_date).days
            ))
            running = base_bal + interest_cumulative
            # daily: balance * ((1 + apy/365)^days - 1)
            interest = (
                running * ((1 + daily_rate) ** days - 1)
            ).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            interest_cumulative += interest
            expected_balances[period.id] = running + interest
            expected_interest[period.id] = interest

        # --- Call service under test ---
        balances, interest_result = (
            calculate_balances_with_interest(
                anchor_balance=Decimal("50000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[],
                hysa_params=params,
            )
        )

        # Period 1: Q(50000 * ((1+0.10/365)^14-1)) = 192.12
        assert balances[1] == expected_balances[1], (
            f"Period 1 balance: expected "
            f"{expected_balances[1]}, got {balances[1]}"
        )
        assert interest_result[1] == expected_interest[1], (
            f"Period 1 interest: expected "
            f"{expected_interest[1]}, "
            f"got {interest_result[1]}"
        )
        # Period 2: Q(50192.12 * ((1+0.10/365)^14-1)) = 192.86
        assert balances[2] == expected_balances[2], (
            f"Period 2 balance: expected "
            f"{expected_balances[2]}, got {balances[2]}"
        )
        assert interest_result[2] == expected_interest[2], (
            f"Period 2 interest: expected "
            f"{expected_interest[2]}, "
            f"got {interest_result[2]}"
        )
        # Period 3: Q(50384.98 * ((1+0.10/365)^14-1)) = 193.60
        assert balances[3] == expected_balances[3], (
            f"Period 3 balance: expected "
            f"{expected_balances[3]}, got {balances[3]}"
        )
        assert interest_result[3] == expected_interest[3], (
            f"Period 3 interest: expected "
            f"{expected_interest[3]}, "
            f"got {interest_result[3]}"
        )

    def test_hysa_interest_on_zero_balance_with_transfer(self):
        """Zero balance earns no interest until transfer arrives.

        Parameters: $0.00 anchor, 4.5% APY, daily compounding,
        3 periods, $500 transfer into account in period 2.

        Proves the guard clause (balance <= 0 → ZERO) prevents
        interest on zero balances, and interest correctly starts
        accruing once a transfer makes the balance positive.
        """
        periods = _make_periods(3)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
        )

        transfers = [
            TransferStub(
                pay_period_id=2,
                from_account_id=99,
                to_account_id=1,
                amount=Decimal("500.00"),
                status=StatusStub(name="projected"),
                is_deleted=False,
            ),
        ]

        # --- Independent oracle (no production code) ---
        # Base balances (mimicking calculate_balances):
        # P1 (anchor): 0.00, P2: 0+500=500, P3: 500
        base_bals = {
            1: Decimal("0.00"),
            2: Decimal("500.00"),
            3: Decimal("500.00"),
        }
        daily_rate = params.apy / Decimal("365")
        expected_balances = {}
        expected_interest = {}
        interest_cumulative = Decimal("0.00")

        for period in periods:
            days = Decimal(str(
                (period.end_date - period.start_date).days
            ))
            running = base_bals[period.id] + interest_cumulative
            # Guard: balance <= 0 → zero interest
            if running <= 0:
                interest = Decimal("0.00")
            else:
                interest = (
                    running
                    * ((1 + daily_rate) ** days - 1)
                ).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            interest_cumulative += interest
            expected_balances[period.id] = running + interest
            expected_interest[period.id] = interest

        # --- Call service under test ---
        balances, interest_result = (
            calculate_balances_with_interest(
                anchor_balance=Decimal("0.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[],
                transfers=transfers,
                account_id=1,
                hysa_params=params,
            )
        )

        # Period 1: balance=0, guard clause → interest=0.00
        assert balances[1] == expected_balances[1], (
            f"Period 1 balance: expected "
            f"{expected_balances[1]}, got {balances[1]}"
        )
        assert interest_result[1] == expected_interest[1], (
            f"Period 1 interest: expected "
            f"{expected_interest[1]}, "
            f"got {interest_result[1]}"
        )
        # Period 2: $500 transfer, Q(500 * M) = 0.86
        assert balances[2] == expected_balances[2], (
            f"Period 2 balance: expected "
            f"{expected_balances[2]}, got {balances[2]}"
        )
        assert interest_result[2] == expected_interest[2], (
            f"Period 2 interest: expected "
            f"{expected_interest[2]}, "
            f"got {interest_result[2]}"
        )
        # Period 3: Q(500.86 * M) = 0.87
        assert balances[3] == expected_balances[3], (
            f"Period 3 balance: expected "
            f"{expected_balances[3]}, got {balances[3]}"
        )
        assert interest_result[3] == expected_interest[3], (
            f"Period 3 interest: expected "
            f"{expected_interest[3]}, "
            f"got {interest_result[3]}"
        )

    def test_hysa_compounding_with_periodic_deposits(self):  # pylint: disable=too-many-locals
        """Compounding with periodic $500 deposits every other period.

        Parameters: $10,000 anchor, 4.5% APY, daily compounding,
        6 periods, $500 transfers in periods 2, 4, and 6.

        Proves that the interaction between transfer timing and
        compounding accumulation is correct across multiple
        deposit events.
        """
        periods = _make_periods(6)
        params = HysaParams(
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
        )

        transfers = [
            TransferStub(
                pay_period_id=2,
                from_account_id=99,
                to_account_id=1,
                amount=Decimal("500.00"),
                status=StatusStub(name="projected"),
                is_deleted=False,
            ),
            TransferStub(
                pay_period_id=4,
                from_account_id=99,
                to_account_id=1,
                amount=Decimal("500.00"),
                status=StatusStub(name="projected"),
                is_deleted=False,
            ),
            TransferStub(
                pay_period_id=6,
                from_account_id=99,
                to_account_id=1,
                amount=Decimal("500.00"),
                status=StatusStub(name="projected"),
                is_deleted=False,
            ),
        ]

        # --- Independent oracle (no production code) ---
        # Base balances from calculate_balances logic:
        # P1 (anchor): 10000
        # P2: 10000+500=10500  P3: 10500
        # P4: 10500+500=11000  P5: 11000
        # P6: 11000+500=11500
        base_bals = {
            1: Decimal("10000.00"),
            2: Decimal("10500.00"),
            3: Decimal("10500.00"),
            4: Decimal("11000.00"),
            5: Decimal("11000.00"),
            6: Decimal("11500.00"),
        }
        daily_rate = params.apy / Decimal("365")
        expected_balances = {}
        expected_interest = {}
        interest_cumulative = Decimal("0.00")

        for period in periods:
            days = Decimal(str(
                (period.end_date - period.start_date).days
            ))
            running = base_bals[period.id] + interest_cumulative
            # daily: balance * ((1 + apy/365)^days - 1)
            interest = (
                running * ((1 + daily_rate) ** days - 1)
            ).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            interest_cumulative += interest
            expected_balances[period.id] = running + interest
            expected_interest[period.id] = interest

        # --- Call service under test ---
        balances, interest_result = (
            calculate_balances_with_interest(
                anchor_balance=Decimal("10000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[],
                transfers=transfers,
                account_id=1,
                hysa_params=params,
            )
        )

        # P1: base=10000, interest=17.27
        # P2: base=10500, running=10517.27, interest=18.17
        # P3: base=10500, running=10535.44, interest=18.20
        # P4: base=11000, running=11053.64, interest=19.09
        # P5: base=11000, running=11072.73, interest=19.13
        # P6: base=11500, running=11591.86, interest=20.02
        for i, period in enumerate(periods):
            exp_b = expected_balances[period.id]
            exp_i = expected_interest[period.id]
            assert balances[period.id] == exp_b, (
                f"Period {i} (id={period.id}): "
                f"expected balance {exp_b}, "
                f"got {balances[period.id]}"
            )
            assert interest_result[period.id] == exp_i, (
                f"Period {i} (id={period.id}): "
                f"expected interest {exp_i}, "
                f"got {interest_result[period.id]}"
            )
