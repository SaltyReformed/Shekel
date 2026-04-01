"""
Tests for the debt-aware balance calculator (calculate_balances_with_amortization).
"""

from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from app import ref_cache
from app.enums import StatusEnum
from app.services.balance_calculator import (
    calculate_balances,
    calculate_balances_with_amortization,
)
from app.services.amortization_engine import (
    calculate_monthly_payment,
)


def _period(id, index, start, end):
    """Create a mock PayPeriod."""
    return SimpleNamespace(
        id=id, period_index=index, start_date=start, end_date=end,
    )


def _shadow_income(pay_period_id, amount, transfer_id=1, status="Projected"):
    """Create a mock shadow income transaction (loan payment)."""
    # Resolve the integer status_id from the ref_cache based on status name.
    _status_enum_map = {
        "Projected": StatusEnum.PROJECTED,
        "Paid": StatusEnum.DONE,
        "Received": StatusEnum.RECEIVED,
        "Credit": StatusEnum.CREDIT,
        "Cancelled": StatusEnum.CANCELLED,
        "Settled": StatusEnum.SETTLED,
    }
    enum_member = _status_enum_map[status]
    sid = ref_cache.status_id(enum_member)
    is_settled = status == "Settled"
    excludes = status in ("Cancelled", "Settled", "Credit")
    est = Decimal(str(amount))
    # effective_amount mirrors Transaction.effective_amount:
    # excluded statuses -> 0, otherwise estimated (no actual on fakes).
    eff = Decimal("0") if excludes else est
    return SimpleNamespace(
        pay_period_id=pay_period_id,
        estimated_amount=est,
        effective_amount=eff,
        status=SimpleNamespace(
            name=status,
            is_settled=is_settled,
            is_immutable=False,
            excludes_from_balance=excludes,
        ),
        status_id=sid,
        transaction_type=SimpleNamespace(name="Income"),
        transfer_id=transfer_id,
        is_income=True,
        is_expense=False,
    )


def _loan_params(principal="200000", rate="0.06", term=360, orig=date(2025, 1, 1), day=1,
                  original_principal=None):
    """Create mock loan params.

    For brand-new loans, original_principal defaults to current principal.
    """
    return SimpleNamespace(
        original_principal=Decimal(original_principal or principal),
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
        txns = [
            _shadow_income(2, "599.55"),
        ]

        balances, principal_by_period = calculate_balances_with_amortization(
            anchor_balance=Decimal("100000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
            account_id=1,
            loan_params=params,
        )

        # Period 1 (anchor): no transfers, balance = anchor
        assert balances[1] == Decimal("100000.00"), (
            f"Period 1 balance: expected 100000.00, "
            f"got {balances[1]}"
        )
        assert principal_by_period[1] == Decimal("0.00"), (
            f"Period 1 principal: expected 0.00, "
            f"got {principal_by_period[1]}"
        )
        # interest = 100000.00 * (0.06/12) = 500.00
        # principal = 599.55 - 500.00 = 99.55
        # new balance = 100000.00 - 99.55 = 99900.45
        assert balances[2] == Decimal("99900.45"), (
            f"Period 2 balance: expected 99900.45, "
            f"got {balances[2]}"
        )
        assert principal_by_period[2] == Decimal("99.55"), (
            f"Period 2 principal: expected 99.55, "
            f"got {principal_by_period[2]}"
        )

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

        txns = [
            _shadow_income(2, "599.55", transfer_id=1),
            _shadow_income(3, "599.55", transfer_id=2),
        ]

        balances, principal_by_period = calculate_balances_with_amortization(
            anchor_balance=Decimal("100000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
            account_id=1,
            loan_params=params,
        )

        # Period 1 (anchor): no transfers
        assert balances[1] == Decimal("100000.00"), (
            f"Period 1 balance: expected 100000.00, "
            f"got {balances[1]}"
        )
        assert principal_by_period[1] == Decimal("0.00"), (
            f"Period 1 principal: expected 0.00, "
            f"got {principal_by_period[1]}"
        )
        # Period 2: interest = 100000.00 * 0.005 = 500.00
        # principal = 599.55 - 500.00 = 99.55
        # balance = 100000.00 - 99.55 = 99900.45
        assert balances[2] == Decimal("99900.45"), (
            f"Period 2 balance: expected 99900.45, "
            f"got {balances[2]}"
        )
        assert principal_by_period[2] == Decimal("99.55"), (
            f"Period 2 principal: expected 99.55, "
            f"got {principal_by_period[2]}"
        )
        # Period 3: interest = 99900.45 * 0.005 = 499.50
        # principal = 599.55 - 499.50 = 100.05
        # balance = 99900.45 - 100.05 = 99800.40
        assert balances[3] == Decimal("99800.40"), (
            f"Period 3 balance: expected 99800.40, "
            f"got {balances[3]}"
        )
        assert principal_by_period[3] == Decimal("100.05"), (
            f"Period 3 principal: expected 100.05, "
            f"got {principal_by_period[3]}"
        )

    def test_non_debt_unchanged(self):
        """Without loan_params → identical to base calculate_balances."""
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ]

        base_balances, _ = calculate_balances(
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

    def test_debt_26_period_amortization_accuracy(self):
        """Exact amortization over 26 biweekly periods.

        Loan: $200,000 at 6.5% for 360 months (30yr).
        Proves every period's balance and principal portion
        matches an independent Decimal oracle replicating the
        inline split logic from balance_calculator.py.
        """
        # Build 26 biweekly periods starting 2026-01-02.
        # Listcomp avoids extra loop-scoped locals.
        periods = [
            _period(
                i + 1, i,
                date(2026, 1, 2) + timedelta(days=14 * i),
                date(2026, 1, 2) + timedelta(days=14*i + 13),
            )
            for i in range(26)
        ]

        # Find periods where payment_day=1 falls in range.
        # Expected: 3(Feb),5(Mar),7(Apr),9(May),11(Jun),
        # 13(Jul),16(Aug),18(Sep),20(Oct),22(Nov),24(Dec).
        pay_pids = set()
        for p in periods:
            d = p.start_date
            while d <= p.end_date:
                if d.day == 1:
                    pay_pids.add(p.id)
                    break
                d += timedelta(days=1)

        # Payment amount from amortization formula.
        # Used ONLY to set transfer amounts (test input).
        pmt = calculate_monthly_payment(
            Decimal("200000"), Decimal("0.065"), 360,
        )  # Decimal("1264.14")

        # --- Independent Oracle ---
        # Replicates inline split from balance_calculator.py
        # lines 245-257. Does NOT use generate_schedule.
        m_rate = Decimal("0.065") / 12  # monthly rate
        rp = Decimal("200000.00")  # running principal
        exp_b = {}  # expected balances
        exp_p = {}  # expected principal_by_period

        for p in periods:
            if p.id == 1:
                # Anchor: set running principal
                rp = Decimal("200000.00")

            if p.id in pay_pids and rp > 0:
                # interest = principal * monthly_rate
                interest = (rp * m_rate).quantize(
                    Decimal("0.01"), ROUND_HALF_UP,
                )
                # principal = payment - interest, capped
                princ = pmt - interest
                princ = max(princ, Decimal("0.00"))
                princ = min(princ, rp)
                # Reduce principal
                rp -= princ
                rp = max(rp, Decimal("0.00"))
                exp_p[p.id] = princ
            else:
                # No payment: balance unchanged
                exp_p[p.id] = Decimal("0.00")

            exp_b[p.id] = rp

        # Build shadow income transactions, one per payment period.
        # Each gets a unique transfer_id.
        sorted_pids = sorted(pay_pids)
        txns = [
            _shadow_income(pid, str(pmt), transfer_id=i + 1)
            for i, pid in enumerate(sorted_pids)
        ]

        # Call the service under test.
        balances, pbp = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("200000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=txns,
                account_id=1,
                loan_params=_loan_params(
                    principal="200000",
                    rate="0.065", term=360,
                ),
            )
        )

        # Assert every period individually.
        for i, p in enumerate(periods):
            assert balances[p.id] == exp_b[p.id], (
                f"Period {i} (id={p.id}): "
                f"expected balance {exp_b[p.id]}, "
                f"got {balances[p.id]}, "
                f"diff={balances[p.id] - exp_b[p.id]}"
            )
            assert pbp[p.id] == exp_p[p.id], (
                f"Period {i} (id={p.id}): "
                f"expected principal {exp_p[p.id]}, "
                f"got {pbp[p.id]}, "
                f"diff={pbp[p.id] - exp_p[p.id]}"
            )

        # Cross-check: service final balance =
        # original principal - sum of all service principal paid.
        assert (
            balances[26]
            == Decimal("200000.00") - sum(pbp.values())
        ), (
            f"Cross-check: balances[26]={balances[26]}, "
            f"200000 - sum(pbp)="
            f"{Decimal('200000.00') - sum(pbp.values())}"
        )

        # Hardcoded spot-check (hand-computed, independent
        # of oracle). Period 3 (id=3) is the first payment
        # period (Feb 1). Starting principal = 200000.00.
        # interest = (200000 * 0.065/12).Q = 1083.33
        # principal = 1264.14 - 1083.33 = 180.81
        # balance = 200000.00 - 180.81 = 199819.19
        assert pbp[3] == Decimal("180.81"), (
            f"Period 3 (first payment) principal: "
            f"expected 180.81, got {pbp[3]}"
        )
        assert balances[3] == Decimal("199819.19"), (
            f"Period 3 (first payment) balance: "
            f"expected 199819.19, got {balances[3]}"
        )

    def test_debt_zero_interest_rate(self):
        """Zero-rate loan: entire payment goes to principal.

        Loan: $12,000 at 0% for 12 months.
        Proves interest=0 for every period and principal
        equals the full payment amount.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
            _period(3, 2, date(2026, 1, 29), date(2026, 2, 11)),
            _period(4, 3, date(2026, 2, 12), date(2026, 2, 25)),
            _period(5, 4, date(2026, 2, 26), date(2026, 3, 11)),
        ]
        # Zero-rate: payment = 12000 / 12 = 1000.00
        params = _loan_params(
            principal="12000", rate="0", term=12,
        )

        # $1000 payments in periods 2, 3, 4.
        # Each $1000 is entirely principal (no interest).
        txns = [
            _shadow_income(2, "1000.00", transfer_id=1),
            _shadow_income(3, "1000.00", transfer_id=2),
            _shadow_income(4, "1000.00", transfer_id=3),
        ]

        balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("12000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=txns,
                account_id=1,
                loan_params=params,
            )
        )

        # Period 1 (anchor): no transfer
        assert balances[1] == Decimal("12000.00"), (
            f"Period 1 balance: expected 12000.00, "
            f"got {balances[1]}"
        )
        assert principal_by_period[1] == Decimal("0.00"), (
            f"Period 1 principal: expected 0.00, "
            f"got {principal_by_period[1]}"
        )
        # Period 2: interest=0, principal=1000, bal=11000
        assert balances[2] == Decimal("11000.00"), (
            f"Period 2 balance: expected 11000.00, "
            f"got {balances[2]}"
        )
        assert principal_by_period[2] == Decimal("1000.00"), (
            f"Period 2 principal: expected 1000.00, "
            f"got {principal_by_period[2]}"
        )
        # Period 3: interest=0, principal=1000, bal=10000
        assert balances[3] == Decimal("10000.00"), (
            f"Period 3 balance: expected 10000.00, "
            f"got {balances[3]}"
        )
        assert principal_by_period[3] == Decimal("1000.00"), (
            f"Period 3 principal: expected 1000.00, "
            f"got {principal_by_period[3]}"
        )
        # Period 4: interest=0, principal=1000, bal=9000
        assert balances[4] == Decimal("9000.00"), (
            f"Period 4 balance: expected 9000.00, "
            f"got {balances[4]}"
        )
        assert principal_by_period[4] == Decimal("1000.00"), (
            f"Period 4 principal: expected 1000.00, "
            f"got {principal_by_period[4]}"
        )
        # Period 5: no transfer, balance unchanged
        assert balances[5] == Decimal("9000.00"), (
            f"Period 5 balance: expected 9000.00, "
            f"got {balances[5]}"
        )
        assert principal_by_period[5] == Decimal("0.00"), (
            f"Period 5 principal: expected 0.00, "
            f"got {principal_by_period[5]}"
        )

    def test_debt_zero_principal_paid_off(self):
        """Already paid-off loan ignores transfers.

        Loan: $0 principal at 6% for 360 months.
        Proves the guard 'total_payment_in > 0 and
        running_principal > 0' prevents any balance change
        when principal is already zero.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
            _period(3, 2, date(2026, 1, 29), date(2026, 2, 11)),
        ]
        params = _loan_params(
            principal="0", rate="0.06", term=360,
        )

        # Transfer that should have no effect.
        txns = [
            _shadow_income(2, "599.55"),
        ]

        balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("0.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=txns,
                account_id=1,
                loan_params=params,
            )
        )

        # All balances 0.00 because running_principal = 0
        # and 'running_principal > 0' is False.
        # Period 1 (anchor): rp = 0.00
        assert balances[1] == Decimal("0.00"), (
            f"Period 1 balance: expected 0.00, "
            f"got {balances[1]}"
        )
        assert principal_by_period[1] == Decimal("0.00"), (
            f"Period 1 principal: expected 0.00, "
            f"got {principal_by_period[1]}"
        )
        # Period 2: transfer ignored, rp still 0
        assert balances[2] == Decimal("0.00"), (
            f"Period 2 balance: expected 0.00, "
            f"got {balances[2]}"
        )
        assert principal_by_period[2] == Decimal("0.00"), (
            f"Period 2 principal: expected 0.00, "
            f"got {principal_by_period[2]}"
        )
        # Period 3: no transfer, rp still 0
        assert balances[3] == Decimal("0.00"), (
            f"Period 3 balance: expected 0.00, "
            f"got {balances[3]}"
        )
        assert principal_by_period[3] == Decimal("0.00"), (
            f"Period 3 principal: expected 0.00, "
            f"got {principal_by_period[3]}"
        )

    def test_debt_overpayment_larger_than_remaining(self):
        """Payment exceeding balance is capped at principal.

        Loan: $500 at 6% for 360 months, payment $600.
        Proves min(principal_portion, running_principal)
        caps at $500.00, driving balance to exactly $0.00.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
            _period(3, 2, date(2026, 1, 29), date(2026, 2, 11)),
        ]
        params = _loan_params(
            principal="500", rate="0.06", term=360,
        )

        # $600 deliberately exceeds $500 remaining.
        txns = [
            _shadow_income(2, "600.00"),
        ]

        balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("500.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=txns,
                account_id=1,
                loan_params=params,
            )
        )

        # Period 1 (anchor): balance = 500.00
        assert balances[1] == Decimal("500.00"), (
            f"Period 1 balance: expected 500.00, "
            f"got {balances[1]}"
        )
        assert principal_by_period[1] == Decimal("0.00"), (
            f"Period 1 principal: expected 0.00, "
            f"got {principal_by_period[1]}"
        )
        # Period 2: interest = 500.00 * 0.005 = 2.50
        # uncapped principal = 600.00 - 2.50 = 597.50
        # capped: min(597.50, 500.00) = 500.00
        # balance = 500.00 - 500.00 = 0.00
        assert balances[2] == Decimal("0.00"), (
            f"Period 2 balance: expected 0.00, "
            f"got {balances[2]}"
        )
        assert principal_by_period[2] == Decimal("500.00"), (
            f"Period 2 principal: expected 500.00, "
            f"got {principal_by_period[2]}"
        )
        # Period 3: already paid off, balance stays 0
        assert balances[3] == Decimal("0.00"), (
            f"Period 3 balance: expected 0.00, "
            f"got {balances[3]}"
        )
        assert principal_by_period[3] == Decimal("0.00"), (
            f"Period 3 principal: expected 0.00, "
            f"got {principal_by_period[3]}"
        )

    def test_debt_cancelled_transfer_excluded(self):
        """Cancelled transfers do not reduce balance.

        Loan: $100,000 at 6% for 360 months.
        Proves the status filter 'if status_name in
        ("cancelled",): continue' skips cancelled transfers,
        while projected transfers apply normally.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
            _period(3, 2, date(2026, 1, 29), date(2026, 2, 11)),
        ]
        params = _loan_params(
            principal="100000", rate="0.06", term=360,
        )

        txns = [
            # Cancelled: should be ignored.
            _shadow_income(
                2, "599.55", transfer_id=1, status="Cancelled",
            ),
            # Projected: should apply normally.
            _shadow_income(3, "599.55", transfer_id=2),
        ]

        balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("100000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=txns,
                account_id=1,
                loan_params=params,
            )
        )

        # Period 1 (anchor): balance = 100000.00
        assert balances[1] == Decimal("100000.00"), (
            f"Period 1 balance: expected 100000.00, "
            f"got {balances[1]}"
        )
        assert principal_by_period[1] == Decimal("0.00"), (
            f"Period 1 principal: expected 0.00, "
            f"got {principal_by_period[1]}"
        )
        # Period 2: cancelled transfer, total_payment = 0
        # Balance unchanged at 100000.00
        assert balances[2] == Decimal("100000.00"), (
            f"Period 2 balance: expected 100000.00, "
            f"got {balances[2]}"
        )
        assert principal_by_period[2] == Decimal("0.00"), (
            f"Period 2 principal: expected 0.00, "
            f"got {principal_by_period[2]}"
        )
        # Period 3: interest = 100000 * 0.005 = 500.00
        # principal = 599.55 - 500.00 = 99.55
        # balance = 100000.00 - 99.55 = 99900.45
        assert balances[3] == Decimal("99900.45"), (
            f"Period 3 balance: expected 99900.45, "
            f"got {balances[3]}"
        )
        assert principal_by_period[3] == Decimal("99.55"), (
            f"Period 3 principal: expected 99.55, "
            f"got {principal_by_period[3]}"
        )

    def test_debt_multiple_payments_same_period(self):
        """Multiple transfers in one period are summed.

        Loan: $100,000 at 6% for 360 months.
        Proves the accumulation loop 'total_payment_in +='
        sums both transfers before the interest/principal
        split, yielding a larger principal reduction.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
            _period(3, 2, date(2026, 1, 29), date(2026, 2, 11)),
        ]
        params = _loan_params(
            principal="100000", rate="0.06", term=360,
        )

        # Two transfers in period 2:
        # $599.55 regular + $200.00 extra = $799.55 total
        txns = [
            _shadow_income(2, "599.55", transfer_id=1),  # regular
            _shadow_income(2, "200.00", transfer_id=2),  # extra
        ]

        balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("100000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=txns,
                account_id=1,
                loan_params=params,
            )
        )

        # Period 1 (anchor): balance = 100000.00
        assert balances[1] == Decimal("100000.00"), (
            f"Period 1 balance: expected 100000.00, "
            f"got {balances[1]}"
        )
        assert principal_by_period[1] == Decimal("0.00"), (
            f"Period 1 principal: expected 0.00, "
            f"got {principal_by_period[1]}"
        )
        # Period 2: total_payment = 599.55 + 200.00 = 799.55
        # interest = 100000 * 0.005 = 500.00
        # principal = 799.55 - 500.00 = 299.55
        # balance = 100000.00 - 299.55 = 99700.45
        assert balances[2] == Decimal("99700.45"), (
            f"Period 2 balance: expected 99700.45, "
            f"got {balances[2]}"
        )
        assert principal_by_period[2] == Decimal("299.55"), (
            f"Period 2 principal: expected 299.55, "
            f"got {principal_by_period[2]}"
        )
        # Period 3: no transfer, balance unchanged
        assert balances[3] == Decimal("99700.45"), (
            f"Period 3 balance: expected 99700.45, "
            f"got {balances[3]}"
        )
        assert principal_by_period[3] == Decimal("0.00"), (
            f"Period 3 principal: expected 0.00, "
            f"got {principal_by_period[3]}"
        )


# ── Section 5 Regression Baseline ──────────────────────────────────────


class TestBalanceCalculatorDebtRegression:
    """Regression baseline for Section 5 balance calculator changes.

    Verifies debt balance calculations with shadow transactions,
    multi-period accumulation, status handling, and consistency
    properties before Section 5 modifies the amortization path.
    """

    def test_checking_and_debt_accounts_no_double_counting(self):
        """A transfer between checking and debt must not double-count.

        The checking account decreases by the full transfer amount
        (via shadow expense) and the debt account decreases by only
        the principal portion (via amortization split).  The balance
        calculator must query only budget.transactions, never
        budget.transfers directly.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ]
        transfer_amount = Decimal("599.55")

        # ── Checking side: shadow expense reduces balance ──
        projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
        checking_shadow = SimpleNamespace(
            pay_period_id=2,
            estimated_amount=transfer_amount,
            effective_amount=transfer_amount,
            status=SimpleNamespace(
                name="Projected",
                is_settled=False,
                is_immutable=False,
                excludes_from_balance=False,
            ),
            status_id=projected_id,
            transaction_type=SimpleNamespace(name="Expense"),
            transfer_id=1,
            is_income=False,
            is_expense=True,
        )
        checking_balances, _ = calculate_balances(
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[checking_shadow],
        )
        # Checking should decrease by full transfer amount.
        assert checking_balances[2] == Decimal("5000.00") - transfer_amount

        # ── Debt side: shadow income splits into principal + interest ──
        debt_shadow = _shadow_income(2, str(transfer_amount))
        params = _loan_params(principal="100000", rate="0.06", term=360)

        debt_balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("100000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[debt_shadow],
                account_id=1,
                loan_params=params,
            )
        )
        # interest = 100000 * 0.005 = 500.00
        # principal = 599.55 - 500.00 = 99.55
        assert principal_by_period[2] == Decimal("99.55")
        assert debt_balances[2] == Decimal("100000.00") - Decimal("99.55")

        # ── Cross-check: amounts are consistent ──
        # Checking decreased by 599.55, debt principal decreased by 99.55.
        # These are different because interest is consumed, not transferred.
        checking_decrease = checking_balances[1] - checking_balances[2]
        assert checking_decrease == transfer_amount

    def test_multi_period_cumulative_debt_balance(self):
        """Multiple transfers across different periods produce correct
        cumulative balance.

        Each period's interest is computed on the prior period's ending
        balance, creating a compounding effect that must be exact.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
            _period(3, 2, date(2026, 1, 29), date(2026, 2, 11)),
            _period(4, 3, date(2026, 2, 12), date(2026, 2, 25)),
        ]
        params = _loan_params(principal="100000", rate="0.06", term=360)

        txns = [
            _shadow_income(2, "599.55", transfer_id=1),
            _shadow_income(3, "599.55", transfer_id=2),
            _shadow_income(4, "599.55", transfer_id=3),
        ]

        balances, _principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("100000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=txns,
                account_id=1,
                loan_params=params,
            )
        )

        # Period 2: interest=500.00, principal=99.55, balance=99900.45
        assert balances[2] == Decimal("99900.45")
        # Period 3: interest=99900.45*0.005=499.50 (rounded)
        # principal=599.55-499.50=100.05, balance=99900.45-100.05=99800.40
        assert balances[3] == Decimal("99800.40")
        # Period 4: interest=99800.40*0.005=499.00 (rounded)
        # principal=599.55-499.00=100.55, balance=99800.40-100.55=99699.85
        assert balances[4] == Decimal("99699.85")

        # Balance must monotonically decrease.
        for i in range(2, 5):
            assert balances[i] < balances[i - 1], (
                f"Balance did not decrease from period {i-1} to {i}: "
                f"{balances[i-1]} -> {balances[i]}"
            )

    def test_paid_status_shadow_applies_to_debt(self):
        """Shadow transaction with Paid status still applies to debt balance.

        Paid transactions have excludes_from_balance=False (they represent
        real payments that occurred), so the balance calculator must
        include them in the amortization calculation.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ]
        params = _loan_params(principal="100000", rate="0.06", term=360)

        # Paid status shadow (not excluded from balance).
        paid_shadow = _shadow_income(2, "599.55", status="Paid")
        projected_shadow = _shadow_income(2, "599.55", status="Projected")

        # Both should produce the same balance reduction since neither
        # status excludes from balance.
        paid_balances, _ = calculate_balances_with_amortization(
            anchor_balance=Decimal("100000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[paid_shadow],
            account_id=1,
            loan_params=params,
        )
        projected_balances, _ = calculate_balances_with_amortization(
            anchor_balance=Decimal("100000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[projected_shadow],
            account_id=1,
            loan_params=params,
        )

        # Both should reduce debt by the same principal portion.
        assert paid_balances[2] == projected_balances[2]

    def test_credit_status_shadow_excluded_from_debt(self):
        """Shadow transaction with Credit status is excluded from debt calc.

        Credit status has excludes_from_balance=True.  Even though the
        shadow exists, it should not reduce the debt balance.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ]
        params = _loan_params(principal="100000", rate="0.06", term=360)

        credit_shadow = _shadow_income(2, "599.55", status="Credit")

        balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("100000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[credit_shadow],
                account_id=1,
                loan_params=params,
            )
        )

        # Credit-status payment should be ignored -- balance unchanged.
        assert balances[2] == Decimal("100000.00")
        assert principal_by_period[2] == Decimal("0.00")

    def test_all_monetary_outputs_are_decimal(self):
        """Every balance and principal value must be Decimal, not float.

        Section 5 must preserve Decimal precision throughout the
        balance calculator pipeline.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
            _period(3, 2, date(2026, 1, 29), date(2026, 2, 11)),
        ]
        params = _loan_params(principal="100000", rate="0.06", term=360)
        txns = [_shadow_income(2, "599.55")]

        balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("100000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=txns,
                account_id=1,
                loan_params=params,
            )
        )

        for period_id, balance in balances.items():
            assert isinstance(balance, Decimal), (
                f"Period {period_id}: balance is {type(balance).__name__}, "
                f"expected Decimal"
            )
        for period_id, principal in principal_by_period.items():
            assert isinstance(principal, Decimal), (
                f"Period {period_id}: principal is {type(principal).__name__}, "
                f"expected Decimal"
            )

    def test_interest_only_payment_no_principal_reduction(self):
        """A payment that covers only interest reduces principal by zero.

        If the payment equals exactly the interest for the period,
        no principal reduction occurs and the balance stays the same.
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ]
        params = _loan_params(principal="100000", rate="0.06", term=360)

        # Interest = 100000 * 0.005 = 500.00 exactly.
        interest_only = _shadow_income(2, "500.00")

        balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("100000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[interest_only],
                account_id=1,
                loan_params=params,
            )
        )

        # All payment went to interest -- balance unchanged.
        assert balances[2] == Decimal("100000.00")
        assert principal_by_period[2] == Decimal("0.00")

    def test_partial_payment_below_interest(self):
        """Payment less than monthly interest still reduces by zero principal.

        If payment < interest, the balance calculator does not create
        negative principal (which would increase the balance).
        """
        periods = [
            _period(1, 0, date(2026, 1, 1), date(2026, 1, 14)),
            _period(2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ]
        params = _loan_params(principal="100000", rate="0.06", term=360)

        # $300 < $500 interest
        underpayment = _shadow_income(2, "300.00")

        balances, principal_by_period = (
            calculate_balances_with_amortization(
                anchor_balance=Decimal("100000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[underpayment],
                account_id=1,
                loan_params=params,
            )
        )

        # No negative amortization -- balance must not increase.
        assert balances[2] == Decimal("100000.00")
        assert principal_by_period[2] == Decimal("0.00")
