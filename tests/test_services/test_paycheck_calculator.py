"""
Shekel Budget App — Unit Tests for Paycheck Calculator

Tests the recurring raise compounding logic in
paycheck_calculator._apply_raises().
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.paycheck_calculator import _apply_raises


# ── Test Fixtures ─────────────────────────────────────────────────


class FakeRaiseType:
    def __init__(self, name="merit"):
        self.name = name


class FakeRaise:
    """Minimal stand-in for a SalaryRaise ORM object."""

    def __init__(self, percentage=None, flat_amount=None,
                 effective_month=3, effective_year=2026,
                 is_recurring=False):
        self.percentage = Decimal(str(percentage)) if percentage else None
        self.flat_amount = Decimal(str(flat_amount)) if flat_amount else None
        self.effective_month = effective_month
        self.effective_year = effective_year
        self.is_recurring = is_recurring
        self.raise_type = FakeRaiseType()


class FakePeriod:
    """Minimal stand-in for a PayPeriod ORM object."""

    def __init__(self, start_date):
        self.start_date = start_date
        self.id = 1


class FakeProfile:
    """Minimal stand-in for a SalaryProfile ORM object."""

    def __init__(self, annual_salary, raises=None):
        self.annual_salary = Decimal(str(annual_salary))
        self.raises = raises or []


# ── Tests ─────────────────────────────────────────────────────────


class TestRecurringRaiseCompounding:
    """Verify that recurring raises compound correctly across years."""

    def test_recurring_raise_not_yet_effective(self):
        """Before effective month in effective year, raise should not apply."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2026, 2, 1))
        result = _apply_raises(profile, period)
        assert result == Decimal("100000.00")

    def test_recurring_raise_first_year_at_effective_month(self):
        """In effective year at effective month, raise should apply once."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2026, 3, 1))
        result = _apply_raises(profile, period)
        # 100000 * 1.03 = 103000
        assert result == Decimal("103000.00")

    def test_recurring_raise_first_year_after_effective_month(self):
        """Later in effective year, raise should still apply once."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2026, 6, 1))
        result = _apply_raises(profile, period)
        assert result == Decimal("103000.00")

    def test_recurring_raise_second_year_before_month(self):
        """Next year before effective month: still only 1 application."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2027, 1, 1))
        result = _apply_raises(profile, period)
        # Only 1 full year passed (2027 - 2026 = 1), but month not reached
        assert result == Decimal("103000.00")

    def test_recurring_raise_second_year_after_month(self):
        """Next year after effective month: 2 total applications (compounded)."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2027, 4, 1))
        result = _apply_raises(profile, period)
        # 100000 * 1.03 * 1.03 = 106090
        assert result == Decimal("106090.00")

    def test_recurring_raise_third_year(self):
        """Two years later after effective month: 3 total applications."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2028, 6, 1))
        result = _apply_raises(profile, period)
        # 100000 * 1.03^3 = 109272.70
        expected = (Decimal("100000") * Decimal("1.03") ** 3).quantize(Decimal("0.01"))
        assert result == expected

    def test_one_time_raise_applies_once(self):
        """A non-recurring raise should only apply once."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.05", effective_month=1,
                              effective_year=2026, is_recurring=False)],
        )
        # Check in 2027 — still just one application.
        period = FakePeriod(start_date=date(2027, 6, 1))
        result = _apply_raises(profile, period)
        assert result == Decimal("105000.00")

    def test_recurring_flat_raise(self):
        """Recurring flat raise should add the flat amount each year."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(flat_amount="5000", effective_month=1,
                              effective_year=2026, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2028, 6, 1))
        result = _apply_raises(profile, period)
        # 3 applications: 100000 + 5000 + 5000 + 5000 = 115000
        assert result == Decimal("115000.00")

    def test_recurring_raise_no_effective_year(self):
        """Recurring raise with no effective_year applies once if month reached."""
        profile = FakeProfile(
            annual_salary=100000,
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=None, is_recurring=True)],
        )
        period = FakePeriod(start_date=date(2027, 4, 1))
        result = _apply_raises(profile, period)
        assert result == Decimal("103000.00")
