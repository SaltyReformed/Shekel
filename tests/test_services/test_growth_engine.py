"""
Shekel Budget App -- Unit Tests for Compound Growth Engine

Tests the growth projection service including compound growth,
contribution limits, employer contributions, and year boundary resets.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.services.growth_engine import (
    ContributionRecord,
    ProjectedBalance,
    calculate_employer_contribution,
    cap_contribution_at_limit,
    growth_rate_for_days,
    project_balance,
    reverse_project_balance,
    span_return_rate,
    ZERO,
)
from app.services.pay_calendar import PayCalendar, PayCalendarError, PeriodWindow
from app.utils.money import round_money

from tests._test_helpers import (
    biweekly_window,
    derived_window,
    window_head,
)


def _emp_type_id(member):
    """Resolve an EmployerContributionTypeEnum member to its ref-table id.

    The employer-params dict carries the type as a ref id under
    ``type_id`` (#38); ref_cache is initialized for every test by the
    autouse conftest fixtures, so these unit tests build the dict the
    same way ``investment_projection.employer_contribution_params``
    does.
    """
    return ref_cache.employer_contribution_type_id(member)


# ── Fixtures ─────────────────────────────────────────────────────
#
# Every axis here is a real ``PeriodWindow`` DERIVED from paydays (plan step
# C2-e).  These fixtures were hand-built ``FakePeriod`` objects until then --
# an id, a start and an end, supplied independently -- which is precisely the
# shape ledger row **P17** records: anything carrying those three attributes
# satisfied the engine, so a test could hand it a state the pay calendar cannot
# produce (a gap between two periods, an ordinal out of date order, an end
# below its own start) and pin behaviour that no owner can ever reach.


@pytest.fixture
def biweekly_periods():
    """10 biweekly periods starting Jan 2, 2026."""
    return biweekly_window(date(2026, 1, 2), 10)


@pytest.fixture
def cross_year_periods():
    """Periods that cross a year boundary (Dec 2026 to Jan 2027)."""
    return biweekly_window(date(2026, 12, 5), 4)


# ── Tests: calculate_employer_contribution ──────────────────────


class TestEmployerContribution:
    def test_none_type_returns_zero(self):
        params = {"type_id": _emp_type_id(EmployerContributionTypeEnum.NONE), "gross_biweekly": Decimal("2500")}
        assert calculate_employer_contribution(params, Decimal("200")) == ZERO

    def test_flat_percentage(self):
        params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("2500"),
        }
        result = calculate_employer_contribution(params, Decimal("0"))
        assert result == Decimal("125.00")

    def test_match_full(self):
        """Employee contributes >= matchable amount."""
        params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        # Employee contributes $150 (6% of $2500), match 100% up to 6%
        result = calculate_employer_contribution(params, Decimal("150"))
        assert result == Decimal("150.00")

    def test_match_partial(self):
        """Employee contributes less than matchable amount."""
        params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        # Employee contributes only $100 (less than $150 cap)
        result = calculate_employer_contribution(params, Decimal("100"))
        assert result == Decimal("100.00")

    def test_match_zero_employee(self):
        """No employee contribution → no match."""
        params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        result = calculate_employer_contribution(params, Decimal("0"))
        assert result == ZERO

    def test_none_params_returns_zero(self):
        assert calculate_employer_contribution(None, Decimal("200")) == ZERO


class TestEmployerGrossOverride:
    """P1b / fork F3: the optional per-period gross override."""

    def test_gross_override_replaces_constant_flat_base(self):
        """An override drives the flat-percentage employer base for the period.

        The params carry a $2500 constant gross; the override supplies
        $4000, so a 5% flat employer contribution is 5% of the override:
        4000 * 0.05 = 200.00 (not 2500 * 0.05 = 125.00).
        """
        params = {
            "type_id": _emp_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("2500"),
        }
        assert calculate_employer_contribution(
            params, Decimal("0"), Decimal("4000"),
        ) == Decimal("200.00")

    def test_none_override_keeps_constant_base(self):
        """Omitting the override keeps the byte-identical constant-base value.

        2500 * 0.05 = 125.00, unchanged from the pre-P1b path.
        """
        params = {
            "type_id": _emp_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("2500"),
        }
        assert calculate_employer_contribution(
            params, Decimal("0"),
        ) == Decimal("125.00")


class TestSalaryBasisEmployerBase:
    """P1b / fork F3: project_balance grows the employer base per period."""

    def _flat_params(self):
        """A 5%-flat employer-params dict with a $1000 constant base."""
        return {
            "type_id": _emp_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("1000"),
        }

    def test_salary_basis_grows_flat_employer_per_period(self):
        """The per-period salary basis lifts the flat-employer base each year.

        Two single-period years with growth zeroed (to isolate the
        employer contribution).  The basis returns $1000 gross for 2030 and
        $2000 for 2031, so the 5% flat employer contribution is 50.00 then
        100.00 -- the base tracks the projected salary rather than freezing.
        """
        # One period per year at a 365-day cadence, so the two years are
        # ADJACENT rather than a year apart: a projection axis tiles, and two
        # periods with a year of nothing between them is a state no calendar
        # can hold.  Growth is zeroed below, so the span does not enter the
        # figures under test.
        periods = derived_window([date(2030, 1, 1), date(2031, 1, 1)], 365)
        gross_by_year = {2030: Decimal("1000.00"), 2031: Decimal("2000.00")}
        result = project_balance(
            current_balance=Decimal("0"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            employer_params=self._flat_params(),
            salary_basis=lambda period: gross_by_year[period.start_date.year],
        )
        # 1000 * 0.05 = 50.00; 2000 * 0.05 = 100.00.
        assert result[0].employer_contribution == Decimal("50.00")
        assert result[1].employer_contribution == Decimal("100.00")

    def test_no_salary_basis_keeps_constant_employer_base(self):
        """Without a basis every period uses the constant employer gross.

        Same two years; the constant $1000 base yields 50.00 both periods
        (1000 * 0.05) -- the behavior every non-retirement consumer keeps.
        """
        # One period per year at a 365-day cadence, so the two years are
        # ADJACENT rather than a year apart: a projection axis tiles, and two
        # periods with a year of nothing between them is a state no calendar
        # can hold.  Growth is zeroed below, so the span does not enter the
        # figures under test.
        periods = derived_window([date(2030, 1, 1), date(2031, 1, 1)], 365)
        result = project_balance(
            current_balance=Decimal("0"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            employer_params=self._flat_params(),
        )
        assert result[0].employer_contribution == Decimal("50.00")
        assert result[1].employer_contribution == Decimal("50.00")


# ── Tests: cap_contribution_at_limit (HIGH-07) ──────────────────


class TestCapContributionAtLimit:
    """Shared helper that caps a per-period contribution at the remaining
    annual limit.  HIGH-07 / F-043 / F-055: the engine's per-period loop
    and the investment dashboard's per-period employer card both call
    this so the card, chart, and year-end summary agree on one number.
    """

    def test_no_limit_returns_amount(self):
        """``None`` annual limit means brokerage-style uncapped."""
        # 1500 (no cap) -> 1500
        assert (
            cap_contribution_at_limit(Decimal("1500"), None, Decimal("0"))
            == Decimal("1500")
        )

    def test_no_limit_negative_clamped_to_zero(self):
        """Negative contributions always clamp to zero, limit or not."""
        # max(-5, 0) = 0
        assert (
            cap_contribution_at_limit(Decimal("-5"), None, Decimal("0"))
            == ZERO
        )

    def test_well_below_limit_returns_amount(self):
        """Limit non-binding: helper returns the contribution unchanged."""
        # remaining = max(23000 - 5000, 0) = 18000; min(1000, 18000) = 1000
        assert (
            cap_contribution_at_limit(
                Decimal("1000"),
                Decimal("23000"),
                Decimal("5000"),
            )
            == Decimal("1000")
        )

    def test_limit_binding_returns_remaining(self):
        """Limit binds: capped to ``annual_limit - ytd``.

        HIGH-07 / F-043 worked example: annual_limit 23000, ytd 22800,
        remaining = 200; proposed contribution 1500 -> cap to 200.
        """
        # remaining = max(23000 - 22800, 0) = 200; min(1500, 200) = 200
        assert (
            cap_contribution_at_limit(
                Decimal("1500"),
                Decimal("23000"),
                Decimal("22800"),
            )
            == Decimal("200")
        )

    def test_ytd_exceeds_limit_returns_zero(self):
        """Already over the limit: remaining clamped to zero."""
        # remaining = max(23000 - 24000, 0) = 0; min(1500, 0) = 0
        assert (
            cap_contribution_at_limit(
                Decimal("1500"),
                Decimal("23000"),
                Decimal("24000"),
            )
            == ZERO
        )

    def test_zero_limit_returns_zero(self):
        """Stored zero cap means "no contributions allowed this year"
        (E-12: zero is a value, not missing)."""
        # remaining = max(0 - 0, 0) = 0; min(500, 0) = 0
        assert (
            cap_contribution_at_limit(
                Decimal("500"),
                Decimal("0"),
                Decimal("0"),
            )
            == ZERO
        )

    def test_three_surface_equality_at_binding_limit(self, biweekly_periods):
        """HIGH-07: card / chart / year-end employer match agree when the
        annual contribution limit binds.

        Worked example from F-043:
          annual_limit = 23000, ytd_start = 22800, remaining = 200.
          periodic_contribution proposed = 1500 (uncapped employee).
          match 50% up to 6% of biweekly gross 8000:
            matchable_salary = 8000 * 0.06 = 480.

        Pre-fix card (uncapped employee feeds the matcher):
          matched = min(1500, 480) = 480; employer = 480 * 0.5 = 240.
        Engine-internal cap (chart and year-end):
          capped = min(1500, 200) = 200;
          matched = min(200, 480) = 200; employer = 200 * 0.5 = 100.

        Post-fix card calls the same helper as the engine, so all three
        surfaces read 100.
        """
        employer_params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("0.5"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("8000"),
        }
        periodic = Decimal("1500")
        annual_limit = Decimal("23000")
        ytd_start = Decimal("22800")

        # Surface A -- card: helper + sole producer.
        capped = cap_contribution_at_limit(periodic, annual_limit, ytd_start)
        card_employer = calculate_employer_contribution(employer_params, capped)

        # Surface B -- chart's first-period employer line (engine internal cap).
        chart_projection = project_balance(
            current_balance=Decimal("100000"),
            assumed_annual_return=Decimal("0"),
            periods=window_head(biweekly_periods, 1),
            periodic_contribution=periodic,
            employer_params=employer_params,
            annual_contribution_limit=annual_limit,
            ytd_contributions_start=ytd_start,
        )
        chart_employer = chart_projection[0].employer_contribution

        # Surface C -- year-end per-period employer total (single binding
        # period in the year: only the first period contributes since the
        # rest are capped to zero).
        year_periods = biweekly_periods
        year_projection = project_balance(
            current_balance=Decimal("100000"),
            assumed_annual_return=Decimal("0"),
            periods=year_periods,
            periodic_contribution=periodic,
            employer_params=employer_params,
            annual_contribution_limit=annual_limit,
            ytd_contributions_start=ytd_start,
        )
        year_first_employer = year_projection[0].employer_contribution
        # Subsequent periods hit the now-zero remaining limit: capped to 0,
        # match on 0 is 0.
        for pb in year_projection[1:]:
            assert pb.employer_contribution == ZERO

        # All three surfaces agree -- the divergence is closed.
        assert card_employer == Decimal("100.00")
        assert chart_employer == Decimal("100.00")
        assert year_first_employer == Decimal("100.00")
        assert card_employer == chart_employer == year_first_employer

    def test_below_limit_no_regression(self, biweekly_periods):
        """HIGH-07 regression guard: well below limit, the helper does
        not alter the contribution and the employer match is unchanged.

        annual_limit 23000, ytd_start 0, periodic 200, match 100% to 6%
        of gross 2500 (matchable_salary = 150):
          capped = min(200, 23000) = 200;
          matched = min(200, 150) = 150; employer = 150 * 1.0 = 150.
        """
        employer_params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        capped = cap_contribution_at_limit(
            Decimal("200"), Decimal("23000"), Decimal("0"),
        )
        card_employer = calculate_employer_contribution(employer_params, capped)
        chart_projection = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=window_head(biweekly_periods, 1),
            periodic_contribution=Decimal("200"),
            employer_params=employer_params,
            annual_contribution_limit=Decimal("23000"),
            ytd_contributions_start=Decimal("0"),
        )
        assert capped == Decimal("200")
        assert card_employer == Decimal("150.00")
        assert chart_projection[0].employer_contribution == Decimal("150.00")

    def test_engine_loop_uses_helper(self):
        """``project_balance`` per-period cap routes through the helper.

        Single inputs (annual_limit=7000, ytd_start=5000, periodic 1000):
        engine first-period cap must equal the helper's output.
        """
        # remaining = max(7000 - 5000, 0) = 2000; min(1000, 2000) = 1000
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=biweekly_window(date(2026, 6, 4), 1),
            periodic_contribution=Decimal("1000"),
            annual_contribution_limit=Decimal("7000"),
            ytd_contributions_start=Decimal("5000"),
        )
        helper_value = cap_contribution_at_limit(
            Decimal("1000"), Decimal("7000"), Decimal("5000"),
        )
        assert result[0].contribution == helper_value == Decimal("1000")


# ── Tests: project_balance ──────────────────────────────────────


class TestProjectBalance:
    def test_basic_growth_no_contributions(self, biweekly_periods):
        """Balance grows at assumed rate with no contributions.

        biweekly_periods span 14 inclusive calendar days
        (start .. start + 13), so period_days = (end - start).days + 1 = 14.
        period_return = (1.07)^(14/365) - 1
        growth = (10000 * period_return).quantize(0.01) = 25.98
        end_balance = 10000 + 25.98 = 10025.98
        """
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=window_head(biweekly_periods, 1),
        )
        assert len(result) == 1
        assert result[0].contribution == ZERO
        assert result[0].employer_contribution == ZERO
        # (1.07)^(14/365) - 1 ~= 0.0025984; 10000 * 0.0025984 = 25.98
        assert result[0].growth == Decimal("25.98"), (
            f"Period 0 growth: expected 25.98, got {result[0].growth}"
        )
        assert result[0].end_balance == Decimal("10025.98"), (
            f"Period 0 end_balance: expected 10025.98, "
            f"got {result[0].end_balance}"
        )

    def test_growth_compounds_over_periods(self, biweekly_periods):
        """Growth compounds across multiple periods."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods,
        )
        assert len(result) == 10
        # Each period's end_balance should be the next period's start.
        for i in range(1, len(result)):
            assert result[i].start_balance == result[i - 1].end_balance

    def test_with_periodic_contributions(self, biweekly_periods):
        """Contributions added each period.

        Each period spans 14 inclusive days, so period_return =
        (1.07)^(14/365) - 1 ~= 0.0025984.  Growth compounds on the starting
        balance, then the $500 contribution is added.
        P0: 10000 + round(10000*0.0025984)=25.98 + 500 = 10525.98
        P1: 10525.98 + round(10525.98*0.0025984)=27.35 + 500 = 11053.33
        P2: 11053.33 + round(11053.33*0.0025984)=28.72 + 500 = 11582.05
        """
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=window_head(biweekly_periods, 3),
            periodic_contribution=Decimal("500"),
        )
        for pb in result:
            assert pb.contribution == Decimal("500"), (
                f"Expected contribution 500, got {pb.contribution}"
            )
        # Period 0: growth on 10000
        assert result[0].growth == Decimal("25.98"), (
            f"P0 growth: expected 25.98, got {result[0].growth}"
        )
        assert result[0].end_balance == Decimal("10525.98"), (
            f"P0 end: expected 10525.98, got {result[0].end_balance}"
        )
        # Period 1: growth on 10525.98
        assert result[1].growth == Decimal("27.35"), (
            f"P1 growth: expected 27.35, got {result[1].growth}"
        )
        assert result[1].end_balance == Decimal("11053.33"), (
            f"P1 end: expected 11053.33, got {result[1].end_balance}"
        )
        # Period 2: growth on 11053.33
        assert result[2].growth == Decimal("28.72"), (
            f"P2 growth: expected 28.72, got {result[2].growth}"
        )
        assert result[2].end_balance == Decimal("11582.05"), (
            f"P2 end: expected 11582.05, got {result[2].end_balance}"
        )

    def test_contribution_limit_caps_contributions(self, biweekly_periods):
        """Contributions capped at annual limit."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods,
            periodic_contribution=Decimal("5000"),
            annual_contribution_limit=Decimal("7000"),
        )
        total_contributions = sum(pb.contribution for pb in result)
        assert total_contributions == Decimal("7000")

    def test_year_boundary_resets_limit(self, cross_year_periods):
        """Annual limit resets at year boundary."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=cross_year_periods,
            periodic_contribution=Decimal("3000"),
            annual_contribution_limit=Decimal("5000"),
        )
        # First 2 periods in 2026: $3000 + $2000 (capped at $5000)
        # Period 3 in 2027: $3000 (limit reset)
        # Period 4 in 2027: $2000 (capped)
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("10000")

    def test_employer_flat_percentage(self, biweekly_periods):
        """Employer flat percentage added each period."""
        employer_params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("2500"),
        }
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=window_head(biweekly_periods, 1),
            periodic_contribution=Decimal("200"),
            employer_params=employer_params,
        )
        assert result[0].employer_contribution == Decimal("125.00")

    def test_employer_match(self, biweekly_periods):
        """Employer match calculated correctly."""
        employer_params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=window_head(biweekly_periods, 1),
            periodic_contribution=Decimal("150"),
            employer_params=employer_params,
        )
        assert result[0].employer_contribution == Decimal("150.00")

    def test_zero_return_rate(self, biweekly_periods):
        """Only contributions grow the balance."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=window_head(biweekly_periods, 3),
            periodic_contribution=Decimal("500"),
        )
        for pb in result:
            assert pb.growth == ZERO
        assert result[-1].end_balance == Decimal("11500.00")

    def test_zero_contribution(self, biweekly_periods):
        """Only growth, no contributions."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=window_head(biweekly_periods, 3),
            periodic_contribution=ZERO,
        )
        for pb in result:
            assert pb.contribution == ZERO

    def test_empty_periods(self):
        """Empty periods list returns empty result."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=[],
        )
        assert result == []

    def test_starting_ytd_reduces_limit(self, biweekly_periods):
        """Mid-year start with existing contributions."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods,
            periodic_contribution=Decimal("1000"),
            annual_contribution_limit=Decimal("7000"),
            ytd_contributions_start=Decimal("5000"),
        )
        # Only $2000 remaining limit.
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("2000")

    def test_negative_return_rate(self, biweekly_periods):
        """Balance decreases with negative return.

        14 inclusive days: period_return = (0.90)^(14/365) - 1
        growth = (10000 * period_return).quantize(0.01) = -40.33
        end_balance = 10000 - 40.33 = 9959.67
        """
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("-0.10"),
            periods=window_head(biweekly_periods, 1),
        )
        # (0.90)^(14/365) - 1 ~= -0.0040331; 10000 * -0.0040331 = -40.33
        assert result[0].growth == Decimal("-40.33"), (
            f"Expected growth -40.33, got {result[0].growth}"
        )
        assert result[0].end_balance == Decimal("9959.67"), (
            f"Expected end_balance 9959.67, got {result[0].end_balance}"
        )

    def test_employer_does_not_count_against_limit(self, biweekly_periods):
        """Employer contributions don't reduce employee contribution limit."""
        employer_params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("2500"),
        }
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=window_head(biweekly_periods, 2),
            periodic_contribution=Decimal("3000"),
            employer_params=employer_params,
            annual_contribution_limit=Decimal("5000"),
        )
        # Employee: $3000 + $2000 = $5000 (capped)
        # Employer: $125 + $125 = $250 (not capped)
        total_emp = sum(pb.contribution for pb in result)
        total_employer = sum(pb.employer_contribution for pb in result)
        assert total_emp == Decimal("5000")
        assert total_employer == Decimal("250.00")

    def test_no_limit_brokerage(self, biweekly_periods):
        """Brokerage accounts have no contribution limit."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=biweekly_periods,
            periodic_contribution=Decimal("5000"),
            annual_contribution_limit=None,
        )
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("50000")

    def test_contribution_limit_exactly_hit(self, biweekly_periods):
        """Last contribution partially applied to hit limit exactly."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=window_head(biweekly_periods, 3),
            periodic_contribution=Decimal("2500"),
            annual_contribution_limit=Decimal("7000"),
        )
        # Period 1: $2500, Period 2: $2500, Period 3: $2000 (capped)
        assert result[0].contribution == Decimal("2500")
        assert result[1].contribution == Decimal("2500")
        assert result[2].contribution == Decimal("2000")

    def test_ytd_contributions_tracked(self, biweekly_periods):
        """ytd_contributions increments correctly."""
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=window_head(biweekly_periods, 3),
            periodic_contribution=Decimal("500"),
        )
        assert result[0].ytd_contributions == Decimal("500")
        assert result[1].ytd_contributions == Decimal("1000")
        assert result[2].ytd_contributions == Decimal("1500")

    def test_period_days_affect_growth(self):
        """Longer periods produce more growth.

        Hand calculation (Commit 32 / MED-07 pinning of directional check),
        counting the end date INCLUSIVELY (period_days = (end-start).days + 1):
          short: Jan 2 - Jan 8 inclusive = 7 days
            return = (1.07)^(7/365) - 1; growth = 10000 * return
            quantized HALF_UP -> 12.98
          long:  Jan 2 - Jan 29 inclusive = 28 days
            return = (1.07)^(28/365) - 1; growth = 10000 * return
            quantized HALF_UP -> 52.04
        """
        short = derived_window([date(2026, 1, 2)], 7)
        long = derived_window([date(2026, 1, 2)], 28)

        short_result = project_balance(
            Decimal("10000"), Decimal("0.07"), short,
        )
        long_result = project_balance(
            Decimal("10000"), Decimal("0.07"), long,
        )
        assert short_result[0].growth == Decimal("12.98"), (
            f"Expected 12.98, got {short_result[0].growth}"
        )
        assert long_result[0].growth == Decimal("52.04"), (
            f"Expected 52.04, got {long_result[0].growth}"
        )

    def test_same_day_period_is_one_day_of_growth(self):
        """A same-day period (start == end) is exactly ONE day of growth.

        With inclusive day-counting, period_days = (end - start).days + 1,
        so start == end gives (0).days + 1 = 1.  A same-day period therefore
        credits a single day of growth -- it does NOT fall back to the 14-day
        cadence (the fallback now fires only for inverted periods; see
        ``test_inverted_period_falls_back_to_14_days``).

        Hand calculation for balance 10000 at 7%, 1 day:
          return = (1.07)^(1/365) - 1 ~= 0.00018538; growth = 10000 * return
          quantized HALF_UP -> 1.85
        """
        same_day = derived_window([date(2026, 1, 2)], 1)
        real_14_day = biweekly_window(date(2026, 1, 2), 1)

        same_day_growth = project_balance(
            Decimal("10000"), Decimal("0.07"), same_day,
        )[0].growth
        # A real biweekly period runs start .. start + 13 (14 inclusive days).
        real_growth = project_balance(
            Decimal("10000"), Decimal("0.07"), real_14_day,
        )[0].growth

        assert same_day_growth == Decimal("1.85"), (
            f"Expected 1.85, got {same_day_growth}"
        )
        assert real_growth == Decimal("25.98"), (
            f"Expected 25.98, got {real_growth}"
        )
        # One day of growth is strictly less than a full 14-day period.
        assert same_day_growth < real_growth

    def test_an_inverted_span_is_REFUSED_not_priced_at_14_days(self):
        """A span that ends before it starts raises instead of returning a rate.

        **This test pinned the opposite behaviour until plan step C2-e**, and
        the change is the developer's (2026-08-14).  The rate function used to
        clamp a non-positive day count to 14 and hand back the biweekly rate,
        so a caller whose dates were crossed was given a believable number
        rather than an error -- and ``$10,000`` at 7% quietly grew ``$25.98``
        over a span of negative length.  Nothing in ``app/`` can reach it now
        (every span comes from a ``DerivedPeriod``, whose end is either the next
        payday minus a day or ``start + cadence - 1``, or from a caller that
        has already selected strictly forward dates), so the branch was a
        silent substitution guarding a state that cannot occur.

        Both doors refuse: the day count directly, and the date pair through
        it.
        """
        with pytest.raises(ValueError, match="at least one calendar day"):
            span_return_rate(
                Decimal("0.07"), date(2026, 1, 16), date(2026, 1, 15),
            )
        with pytest.raises(ValueError, match="at least one calendar day"):
            growth_rate_for_days(Decimal("0.07"), 0)
        with pytest.raises(ValueError, match="at least one calendar day"):
            growth_rate_for_days(Decimal("0.07"), -1)

    def test_an_inverted_span_is_unconstructible_as_a_period(self):
        """The derivation cannot produce the period that used to reach it.

        The firing control for the test above: a window's periods run
        ``payday .. next payday - 1`` (and the last ``payday + cadence - 1``),
        so no payday set derives a period whose end precedes its own start.
        Two paydays a day apart give the earlier one a ONE-day span, the
        shortest a calendar can hold, and one day is the smallest the rate
        function accepts.
        """
        window = derived_window([date(2026, 1, 15), date(2026, 1, 16)], 14)
        assert window[0].start_date == window[0].end_date == date(2026, 1, 15)
        assert span_return_rate(
            Decimal("0.07"), window[0].start_date, window[0].end_date,
        ) == growth_rate_for_days(Decimal("0.07"), 1)


class TestTheEngineOverARealAxis:
    """What the engine does when the axis is the owner's own pay calendar.

    These replace ``TestGenerateProjectionPeriods``, which graded a producer
    plan step **C2-e** DELETED: ``growth_engine`` fabricated its own periods,
    numbered from 1 in the same integer namespace as real
    ``budget.pay_periods.id`` (ledger row **P17**) and hardcoded to a 14-day
    cadence no call site overrode (row **P20**).  The axis is now
    ``PayCalendar.axis``, and the properties those tests asserted about the
    fabricated list -- it tiles, its ordinals run in order, it is empty for a
    range it cannot cover -- are that value's, graded in
    ``test_pay_calendar_value.py``.  What belongs HERE is what the ENGINE does
    with such an axis, which is what these grade.
    """

    def test_the_engine_emits_one_row_per_axis_period_carrying_that_period(self):
        """Ledger row **P21**: the row's identity is the period, not a copy of one.

        ``ProjectedBalance`` carried ``period_id: int``, taken off the axis
        period's ``.id`` -- and every period past an owner's saved horizon is a
        PROJECTION whose id is ``None``, so a map keyed on it collapsed the
        whole projected tail onto one entry.  Here every row resolves to a
        DISTINCT period.
        """
        # ONE calendar with two SAVED paydays, asked for an axis that runs
        # well past its horizon -- the production shape, where the tail is
        # projected and carries no id.  Here the axis is 25 periods, of which
        # only the first two are saved.
        calendar = PayCalendar.from_paydays(
            [(11, date(2026, 1, 2)), (12, date(2026, 1, 16))], 14, user_id=1,
            history_opens_on=None,
        )
        whole = calendar.axis(date(2026, 1, 2), date(2026, 12, 4))
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=whole,
            periodic_contribution=Decimal("500"),
        )
        assert len(result) == len(whole) == 25
        # 23 of the 25 periods share ``period_id is None``; the ORDINAL is what
        # stays unique across a mixed saved/projected axis.
        assert [row.period.period_id for row in result][:3] == [11, 12, None]
        assert len({row.period.period_id for row in result}) == 3
        assert len({row.period.period_index for row in result}) == 25
        assert [row.period for row in result] == list(whole)

    def test_the_projection_replays_against_an_independent_loop(self):
        """One calendar year of biweekly periods, recomputed outside the engine.

        Independent computation replicating the growth engine formula.
        For each period (14 INCLUSIVE days,
        ``period_days = (end - start).days + 1``):
          period_return = (1 + 0.07)^(period_days / 365) - 1
          growth = round_money(balance * period_return)
          balance = balance + growth + 500
        Starting from balance = 10,000 over 27 periods.
        """
        periods = biweekly_window(date(2026, 1, 1), 27)
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=periods,
            periodic_contribution=Decimal("500"),
        )
        assert len(result) == len(periods) == 27

        expected_balance = Decimal("10000")
        for period in periods:
            period_days = (period.end_date - period.start_date).days + 1
            rate = (
                (1 + Decimal("0.07"))
                ** (Decimal(str(period_days)) / Decimal("365"))
                - 1
            )
            growth = round_money(expected_balance * rate)
            expected_balance = expected_balance + growth + Decimal("500")

        assert result[-1].end_balance == expected_balance

    def test_an_empty_axis_projects_nothing(self):
        """A window with no periods yields no rows -- an answer, not an error.

        The state a retirement date already in the past produces
        (``BalanceContext.projection_axis`` returns an empty window for it),
        and what the lever page's ``past_horizon`` verdict is built on.
        """
        assert project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=PeriodWindow(periods=()),
            periodic_contribution=Decimal("500"),
        ) == []

    def test_the_owners_cadence_decides_how_many_contributions_a_year(self):
        """Ledger row **P20**, priced at ``$588,959.22`` over twenty years.

        The deleted producer applied ``periodic_contribution`` -- a per-PAYCHECK
        figure -- once per 14-day period whatever the owner's real cadence, so
        a monthly-paid owner was credited ``365/14`` contributions a year
        instead of 12.  With the axis derived from their own paydays the count
        is theirs: at 0% return the year's contributions are exactly
        ``periodic * len(axis)``, and the two cadences differ by more than a
        factor of two.
        """
        monthly = derived_window(
            [date(2026, 1, 1) + timedelta(days=30 * step) for step in range(12)],
            30,
        )
        biweekly = biweekly_window(date(2026, 1, 1), 26)
        monthly_end = project_balance(
            current_balance=ZERO,
            assumed_annual_return=ZERO,
            periods=monthly,
            periodic_contribution=Decimal("1000"),
        )[-1].end_balance
        biweekly_end = project_balance(
            current_balance=ZERO,
            assumed_annual_return=ZERO,
            periods=biweekly,
            periodic_contribution=Decimal("1000"),
        )[-1].end_balance
        assert monthly_end == Decimal("12000.00")
        assert biweekly_end == Decimal("26000.00")

    def test_year_boundaries_are_read_off_the_periods_own_start(self):
        """An axis crossing New Year carries both years, which resets the limit."""
        periods = biweekly_window(date(2026, 12, 20), 4)
        years = {period.start_date.year for period in periods}
        assert years == {2026, 2027}


# ── Tests: Contribution-Aware Projection ──────────────────────


class TestContributionAwareProjection:
    """Tests for contribution-list-aware projections.

    Verifies that project_balance() correctly uses per-period contribution
    amounts from ContributionRecord instances, with proper fallback to
    periodic_contribution, annual limit capping, employer match computation,
    YTD tracking, and is_confirmed propagation.
    """

    def test_no_contributions_unchanged(self, biweekly_periods):
        """contributions=None produces identical output to omitting the parameter."""
        kwargs = {
            "current_balance": Decimal("10000"),
            "assumed_annual_return": Decimal("0.07"),
            "periods": window_head(biweekly_periods, 3),
            "periodic_contribution": Decimal("500"),
        }
        baseline = project_balance(**kwargs)
        with_none = project_balance(**kwargs, contributions=None)
        assert baseline == with_none

    def test_empty_contributions_unchanged(self, biweekly_periods):
        """contributions=[] produces identical output to contributions=None."""
        kwargs = {
            "current_balance": Decimal("10000"),
            "assumed_annual_return": Decimal("0.07"),
            "periods": window_head(biweekly_periods, 3),
            "periodic_contribution": Decimal("500"),
        }
        baseline = project_balance(**kwargs, contributions=None)
        with_empty = project_balance(**kwargs, contributions=[])
        assert baseline == with_empty

    def test_contributions_applied_per_period(self, biweekly_periods):
        """Explicit contributions for all periods override periodic_contribution.

        0% return, start=$10,000.
        P0: +$300 = $10,300
        P1: +$500 = $10,800
        P2: +$200 = $11,000
        """
        periods = window_head(biweekly_periods, 3)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("300"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("500"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("200"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("999"),  # Should NOT be used.
            contributions=contributions,
        )
        assert result[0].contribution == Decimal("300")
        assert result[1].contribution == Decimal("500")
        assert result[2].contribution == Decimal("200")
        assert result[0].end_balance == Decimal("10300")
        assert result[1].end_balance == Decimal("10800")
        assert result[2].end_balance == Decimal("11000")

    def test_contributions_partial_with_fallback(self, biweekly_periods):
        """Periods without contributions fall back to periodic_contribution.

        0% return, start=$10,000, periodic=$200.
        P0: +$300 (record) = $10,300
        P1: +$500 (record) = $10,800
        P2: +$100 (record) = $10,900
        P3: +$200 (fallback) = $11,100
        P4: +$200 (fallback) = $11,300
        """
        periods = window_head(biweekly_periods, 5)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("300"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("500"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("100"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("200"),
            contributions=contributions,
        )
        assert result[0].contribution == Decimal("300")
        assert result[1].contribution == Decimal("500")
        assert result[2].contribution == Decimal("100")
        assert result[3].contribution == Decimal("200")
        assert result[4].contribution == Decimal("200")
        assert result[4].end_balance == Decimal("11300")

    def test_zero_contribution_does_not_fallback(self, biweekly_periods):
        """A $0 contribution record means no contribution -- not a fallback.

        0% return, start=$10,000, periodic=$500.
        P0: +$500 (record) = $10,500
        P1: +$0 (explicit zero, NOT fallback) = $10,500
        P2: +$500 (record) = $11,000
        """
        periods = window_head(biweekly_periods, 3)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("500"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("0"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("500"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("500"),
            contributions=contributions,
        )
        assert result[1].contribution == Decimal("0")
        assert result[1].end_balance == Decimal("10500")
        assert result[2].end_balance == Decimal("11000")

    def test_annual_limit_caps_contributions(self, biweekly_periods):
        """Contributions capped at annual limit.

        0% return, start=$10,000, limit=$1,000.
        P0: min($300, $1,000) = $300, remaining=$700
        P1: min($300, $700) = $300, remaining=$400
        P2: min($300, $400) = $300, remaining=$100
        P3: min($300, $100) = $100, remaining=$0
        Total: $1,000.
        """
        periods = window_head(biweekly_periods, 4)
        contributions = [
            ContributionRecord(p.start_date, Decimal("300"), True)
            for p in periods
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
            annual_contribution_limit=Decimal("1000"),
        )
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("1000")
        assert result[0].contribution == Decimal("300")
        assert result[1].contribution == Decimal("300")
        assert result[2].contribution == Decimal("300")
        assert result[3].contribution == Decimal("100")

    def test_year_boundary_resets_with_contributions(self, cross_year_periods):
        """Annual limit resets at year boundary with contribution records.

        0% return, start=$10,000, limit=$5,000.
        P0 (2026): min($3,000, $5,000) = $3,000, remaining=$2,000
        P1 (2026): min($3,000, $2,000) = $2,000, remaining=$0
        Year boundary -- remaining resets to $5,000.
        P2 (2027): min($3,000, $5,000) = $3,000, remaining=$2,000
        P3 (2027): min($3,000, $2,000) = $2,000, remaining=$0
        Total: $10,000.
        """
        contributions = [
            ContributionRecord(p.start_date, Decimal("3000"), True)
            for p in cross_year_periods
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=cross_year_periods,
            contributions=contributions,
            annual_contribution_limit=Decimal("5000"),
        )
        assert result[0].contribution == Decimal("3000")
        assert result[1].contribution == Decimal("2000")
        assert result[2].contribution == Decimal("3000")
        assert result[3].contribution == Decimal("2000")
        total = sum(pb.contribution for pb in result)
        assert total == Decimal("10000")

    def test_employer_match_uses_period_contribution(self, biweekly_periods):
        """Employer match computed from per-period contribution, not static.

        Match: 100% up to 6% of $2,500 gross = $150 matchable.
        Record contribution=$100 -- match=min($100, $150) * 1.0 = $100.
        Static periodic=$150 would give match=$150 -- must NOT be used.
        """
        periods = window_head(biweekly_periods, 1)
        employer_params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("150"),
            employer_params=employer_params,
            contributions=contributions,
        )
        # Match is on $100 (from record), not $150 (static).
        assert result[0].employer_contribution == Decimal("100.00")
        assert result[0].contribution == Decimal("100")

    def test_employer_match_with_varying_contributions(self, biweekly_periods):
        """Employer match varies with different per-period contributions.

        Match: 100% up to 6% of $2,500 = $150 matchable.
        P0: $100 -- match=$100
        P1: $200 -- match=min($200, $150) = $150
        P2: $50 -- match=$50
        """
        periods = window_head(biweekly_periods, 3)
        employer_params = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("1.0"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("2500"),
        }
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("50"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            employer_params=employer_params,
            contributions=contributions,
        )
        assert result[0].employer_contribution == Decimal("100.00")
        assert result[1].employer_contribution == Decimal("150.00")
        assert result[2].employer_contribution == Decimal("50.00")

    def test_is_confirmed_propagated(self, biweekly_periods):
        """is_confirmed flag matches input records; fallback periods are False."""
        periods = window_head(biweekly_periods, 3)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), False),
            # P2 has no record -- fallback.
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("300"),
            contributions=contributions,
        )
        assert result[0].is_confirmed is True
        assert result[1].is_confirmed is False
        assert result[2].is_confirmed is False

    def test_is_confirmed_all_confirmed_same_date(self, biweekly_periods):
        """Multiple confirmed contributions on same date -- is_confirmed=True."""
        periods = window_head(biweekly_periods, 1)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 2), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
        )
        assert result[0].is_confirmed is True
        assert result[0].contribution == Decimal("500")

    def test_is_confirmed_mixed_same_date(self, biweekly_periods):
        """Confirmed + projected on same date -- is_confirmed=False."""
        periods = window_head(biweekly_periods, 1)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 2), Decimal("300"), False),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
        )
        assert result[0].is_confirmed is False
        assert result[0].contribution == Decimal("500")

    def test_multiple_contributions_same_date_summed(self, biweekly_periods):
        """Two contributions on the same period are summed.

        P0: $200 + $300 = $500.
        End: $10,000 + $500 = $10,500.
        """
        periods = window_head(biweekly_periods, 1)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 2), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
        )
        assert result[0].contribution == Decimal("500")
        assert result[0].end_balance == Decimal("10500")

    def test_unsorted_contributions_handled(self, biweekly_periods):
        """Non-chronological contributions produce the same result as sorted."""
        periods = window_head(biweekly_periods, 3)
        sorted_contribs = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        unsorted_contribs = list(reversed(sorted_contribs))

        kwargs = {
            "current_balance": Decimal("10000"),
            "assumed_annual_return": Decimal("0"),
            "periods": periods,
        }
        sorted_result = project_balance(**kwargs, contributions=sorted_contribs)
        unsorted_result = project_balance(
            **kwargs, contributions=unsorted_contribs
        )
        assert sorted_result == unsorted_result

    def test_contribution_record_validation_negative(self):
        """Negative contribution amount raises ValueError."""
        with pytest.raises(ValueError, match="amount must be >= 0"):
            ContributionRecord(date(2026, 1, 2), Decimal("-100"), True)

    def test_contribution_record_validation_types(self):
        """Wrong types raise TypeError for each field."""
        with pytest.raises(TypeError, match="contribution_date must be a date"):
            ContributionRecord("2026-01-02", Decimal("100"), True)
        with pytest.raises(TypeError, match="amount must be a Decimal"):
            ContributionRecord(date(2026, 1, 2), 100.0, True)
        with pytest.raises(TypeError, match="is_confirmed must be a bool"):
            ContributionRecord(date(2026, 1, 2), Decimal("100"), "yes")

    def test_ytd_tracking_uses_actual_amounts(self, biweekly_periods):
        """YTD contributions reflect actual per-period amounts, not static.

        P0: $100 -- ytd=$100
        P1: $200 -- ytd=$300
        P2: $300 -- ytd=$600
        """
        periods = window_head(biweekly_periods, 3)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=Decimal("999"),
            contributions=contributions,
        )
        assert result[0].ytd_contributions == Decimal("100")
        assert result[1].ytd_contributions == Decimal("300")
        assert result[2].ytd_contributions == Decimal("600")

    def test_contribution_limit_remaining_reflects_actuals(self, biweekly_periods):
        """contribution_limit_remaining computed from actual per-period amounts.

        Limit=$1,000.
        P0: $100 -- remaining=$900
        P1: $200 -- remaining=$700
        P2: $300 -- remaining=$400
        """
        periods = window_head(biweekly_periods, 3)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
            annual_contribution_limit=Decimal("1000"),
        )
        assert result[0].contribution_limit_remaining == Decimal("900")
        assert result[1].contribution_limit_remaining == Decimal("700")
        assert result[2].contribution_limit_remaining == Decimal("400")

    def test_zero_return_rate_with_contributions(self, biweekly_periods):
        """0% return: balance grows only by contributions.

        Start=$10,000.
        P0: +$100 = $10,100
        P1: +$200 = $10,300
        P2: +$300 = $10,600
        """
        periods = window_head(biweekly_periods, 3)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            contributions=contributions,
        )
        for pb in result:
            assert pb.growth == ZERO
        assert result[0].end_balance == Decimal("10100")
        assert result[1].end_balance == Decimal("10300")
        assert result[2].end_balance == Decimal("10600")

    def test_no_employer_params_with_contributions(self, biweekly_periods):
        """employer_params=None with contributions: no employer match applied."""
        periods = window_head(biweekly_periods, 3)
        contributions = [
            ContributionRecord(date(2026, 1, 2), Decimal("100"), True),
            ContributionRecord(date(2026, 1, 16), Decimal("200"), True),
            ContributionRecord(date(2026, 1, 30), Decimal("300"), True),
        ]
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0"),
            periods=periods,
            employer_params=None,
            contributions=contributions,
        )
        for pb in result:
            assert pb.employer_contribution == ZERO
        assert result[2].end_balance == Decimal("10600")


# ── Reverse Projection Tests ────────────────────────────────────


class TestReverseProjectBalance:
    """Tests for reverse_project_balance -- backward growth derivation."""

    def test_roundtrip_recovers_starting_balance(self):
        """Forward-project, then reverse-project -- original balance recovered.

        The reverse of the forward formula should recover the starting
        balance within rounding tolerance ($0.01 per period).
        """
        periods = biweekly_window(date(2026, 1, 2), 5)
        start_balance = Decimal("10000.00")
        annual_return = Decimal("0.07")
        contribution = Decimal("200.00")

        # Forward project from the known starting balance.
        forward = project_balance(
            current_balance=start_balance,
            assumed_annual_return=annual_return,
            periods=periods,
            periodic_contribution=contribution,
        )
        end_balance = forward[-1].end_balance

        # Reverse project from the ending balance.
        reversed_proj = reverse_project_balance(
            anchor_balance=end_balance,
            assumed_annual_return=annual_return,
            periods=periods,
            periodic_contribution=contribution,
        )

        # The start_balance of the first reversed period should match
        # the original starting balance within rounding tolerance.
        recovered = reversed_proj[0].start_balance
        tolerance = Decimal("0.01") * len(periods)
        assert abs(recovered - start_balance) <= tolerance, (
            f"Roundtrip failed: start={start_balance}, "
            f"recovered={recovered}, diff={abs(recovered - start_balance)}"
        )

    def test_roundtrip_with_employer_match(self):
        """Forward then reverse with employer match -- balance recovered.

        Employer match complicates the formula; verify the inverse
        still works.
        """
        periods = biweekly_window(date(2026, 1, 2), 3)
        start_balance = Decimal("25000.00")
        annual_return = Decimal("0.105")
        contribution = Decimal("300.00")
        employer = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            "flat_percentage": Decimal("0.05"),
            "gross_biweekly": Decimal("3000.00"),
        }

        forward = project_balance(
            current_balance=start_balance,
            assumed_annual_return=annual_return,
            periods=periods,
            periodic_contribution=contribution,
            employer_params=employer,
        )
        end_balance = forward[-1].end_balance

        reversed_proj = reverse_project_balance(
            anchor_balance=end_balance,
            assumed_annual_return=annual_return,
            periods=periods,
            periodic_contribution=contribution,
            employer_params=employer,
        )

        recovered = reversed_proj[0].start_balance
        tolerance = Decimal("0.01") * len(periods)
        assert abs(recovered - start_balance) <= tolerance

    def test_zero_return_subtracts_contributions(self):
        """With 0% return, reverse should just subtract contributions.

        No growth to reverse -- each period's start is the end minus
        the contribution and employer amounts.
        """
        periods = biweekly_window(date(2026, 1, 2), 2)
        anchor_balance = Decimal("1000.00")
        contribution = Decimal("200.00")

        reversed_proj = reverse_project_balance(
            anchor_balance=anchor_balance,
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=contribution,
        )

        # With 0% return: start = end - contribution
        # Period 2: end=1000, start=1000-200=800
        # Period 1: end=800, start=800-200=600
        assert reversed_proj[0].start_balance == Decimal("600.00")
        assert reversed_proj[0].end_balance == Decimal("800.00")
        assert reversed_proj[1].start_balance == Decimal("800.00")
        assert reversed_proj[1].end_balance == Decimal("1000.00")
        # Growth should be zero.
        for pb in reversed_proj:
            assert pb.growth == ZERO

    def test_returns_forward_chronological_order(self):
        """Result list is in forward chronological order."""
        periods = biweekly_window(date(2026, 1, 2), 3)
        reversed_proj = reverse_project_balance(
            anchor_balance=Decimal("5000.00"),
            assumed_annual_return=Decimal("0.07"),
            periods=periods,
        )
        assert len(reversed_proj) == 3
        assert [row.period for row in reversed_proj] == list(periods)

    @staticmethod
    def _year_2026_periods(count):
        """Build ``count`` consecutive 14-day periods all starting in 2026."""
        return biweekly_window(date(2026, 1, 2), count)

    def test_roundtrip_with_binding_annual_limit(self):
        """DH-#28: reverse caps each period like forward, so a maxed-out
        account's start balance is recovered EXACTLY (not derived too low).

        IRA limit $7,000, periodic $1,000 with a 50% employer match capped
        at 6% of a $3,000 gross.  At 0% return over 8 periods the cap binds
        after period 7:
          periods 1-7: contribution=1000, match=min(1000, 3000*0.06=180)*0.5=90
          period 8:    contribution=0 (limit hit), match=min(0,180)*0.5=0
        Forward end = 25000 + 7*1000 + 7*90 = 25000 + 7000 + 630 = 32630.00.
        Reverse from 32630 with the SAME limit replays that capped schedule
        and recovers 25000.00 exactly.
        """
        periods = self._year_2026_periods(8)
        start_balance = Decimal("25000.00")
        limit = Decimal("7000.00")
        periodic = Decimal("1000.00")
        employer = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("0.5"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("3000.00"),
        }

        forward = project_balance(
            current_balance=start_balance,
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=periodic,
            employer_params=employer,
            annual_contribution_limit=limit,
        )
        assert forward[-1].end_balance == Decimal("32630.00")

        reversed_proj = reverse_project_balance(
            anchor_balance=forward[-1].end_balance,
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=periodic,
            employer_params=employer,
            annual_contribution_limit=limit,
        )

        # Exact recovery -- the cap was replayed, not over-subtracted.
        assert reversed_proj[0].start_balance == start_balance

        # Per-period capped contribution + employer match mirror the forward
        # (period 8 is capped to 0, with 0 employer match on it).
        for rev, fwd in zip(reversed_proj, forward):
            assert rev.contribution == fwd.contribution
            assert rev.employer_contribution == fwd.employer_contribution
        assert reversed_proj[7].contribution == ZERO
        assert reversed_proj[7].employer_contribution == ZERO

        # The reverse rows now carry the real replayed limit state, not the
        # old hardcoded ZERO / None.
        assert reversed_proj[6].ytd_contributions == Decimal("7000.00")
        assert reversed_proj[6].contribution_limit_remaining == ZERO

    def test_binding_limit_roundtrip_at_nonzero_return(self):
        """The capped reverse still inverts the growth divisor at 7%.

        Same binding IRA scenario as the 0% case but at a 7% annual return;
        the forward end balance is recovered within the per-period $0.01
        rounding tolerance (observed drift is 0.00).
        """
        periods = self._year_2026_periods(8)
        start_balance = Decimal("25000.00")
        limit = Decimal("7000.00")
        periodic = Decimal("1000.00")
        employer = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("0.5"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("3000.00"),
        }
        annual_return = Decimal("0.07")

        forward = project_balance(
            current_balance=start_balance,
            assumed_annual_return=annual_return,
            periods=periods,
            periodic_contribution=periodic,
            employer_params=employer,
            annual_contribution_limit=limit,
        )
        reversed_proj = reverse_project_balance(
            anchor_balance=forward[-1].end_balance,
            assumed_annual_return=annual_return,
            periods=periods,
            periodic_contribution=periodic,
            employer_params=employer,
            annual_contribution_limit=limit,
        )

        recovered = reversed_proj[0].start_balance
        tolerance = Decimal("0.01") * len(periods)
        assert abs(recovered - start_balance) <= tolerance

    def test_without_limit_over_subtracts_when_cap_binds(self):
        """Revert-proof: dropping the limit reproduces the DH-#28 bug.

        On the exact binding scenario, calling the reverse WITHOUT the
        annual limit (the old behaviour) subtracts the full $1,000 in the
        post-cap period that actually contributed $0, deriving a start
        balance STRICTLY LOWER than the true one:
          uncapped reverse = 32630 - 8*(1000 + 90) = 32630 - 8720 = 23910.00.
        So a revert that drops the limit threading re-breaks this assertion.
        """
        periods = self._year_2026_periods(8)
        start_balance = Decimal("25000.00")
        limit = Decimal("7000.00")
        periodic = Decimal("1000.00")
        employer = {
            "type_id": _emp_type_id(EmployerContributionTypeEnum.MATCH),
            "match_percentage": Decimal("0.5"),
            "match_cap_percentage": Decimal("0.06"),
            "gross_biweekly": Decimal("3000.00"),
        }

        forward = project_balance(
            current_balance=start_balance,
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=periodic,
            employer_params=employer,
            annual_contribution_limit=limit,
        )
        reversed_no_limit = reverse_project_balance(
            anchor_balance=forward[-1].end_balance,
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=periodic,
            employer_params=employer,
            # annual_contribution_limit omitted -- the pre-fix uncapped path.
        )

        assert reversed_no_limit[0].start_balance == Decimal("23910.00")
        assert reversed_no_limit[0].start_balance < start_balance

    def test_roundtrip_binding_limit_across_year_boundary(self):
        """The reverse replays the year-boundary YTD reset.

        limit $3,000, periodic $1,500, no employer, 0% return, over 5 periods
        that straddle a calendar-year boundary (3 in 2026, 2 in 2027):
          2026: 1500, 1500, then 0 (limit hit at $3,000)
          2027 (YTD resets to 0): 1500, 1500
        Forward end = 20000 + (1500+1500+0+1500+1500) = 26000.00.
        Reverse recovers 20000.00 exactly, proving the reset is replayed.
        """
        periods = biweekly_window(date(2026, 12, 1), 5)
        start_balance = Decimal("20000.00")
        limit = Decimal("3000.00")
        periodic = Decimal("1500.00")

        forward = project_balance(
            current_balance=start_balance,
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=periodic,
            annual_contribution_limit=limit,
        )
        assert forward[-1].end_balance == Decimal("26000.00")
        # Period 3 (2026) capped to 0; period 4 (2027) resets back to 1500.
        assert forward[2].contribution == ZERO
        assert forward[3].contribution == Decimal("1500.00")

        reversed_proj = reverse_project_balance(
            anchor_balance=forward[-1].end_balance,
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=periodic,
            annual_contribution_limit=limit,
        )
        assert reversed_proj[0].start_balance == start_balance
        assert reversed_proj[2].contribution == ZERO
        assert reversed_proj[3].contribution == Decimal("1500.00")

    def test_roundtrip_with_nonzero_ytd_start(self):
        """ytd_contributions_start threads symmetrically into the reverse.

        With $2,000 already contributed this year, limit $7,000, periodic
        $1,000, 0% return, the cap binds after period 5 (not 7):
          periods 1-5: 1000 each (YTD 3000->7000); periods 6-8: 0.
        Forward end = 30000 + 5000 = 35000.00; reverse fed the same
        ytd_contributions_start=2000 recovers 30000.00 exactly.
        """
        periods = self._year_2026_periods(8)
        start_balance = Decimal("30000.00")
        limit = Decimal("7000.00")
        periodic = Decimal("1000.00")
        ytd_start = Decimal("2000.00")

        forward = project_balance(
            current_balance=start_balance,
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=periodic,
            annual_contribution_limit=limit,
            ytd_contributions_start=ytd_start,
        )
        assert forward[-1].end_balance == Decimal("35000.00")

        reversed_proj = reverse_project_balance(
            anchor_balance=forward[-1].end_balance,
            assumed_annual_return=Decimal("0"),
            periods=periods,
            periodic_contribution=periodic,
            annual_contribution_limit=limit,
            ytd_contributions_start=ytd_start,
        )
        assert reversed_proj[0].start_balance == start_balance
        assert reversed_proj[5].contribution == ZERO


# ── Inclusive day-count regression (D1, developer-approved 2026-07-02) ──


class TestInclusiveDayCountRegression:
    """Pin the corrected inclusive-end day-count convention directly.

    Pay periods carry an INCLUSIVE end_date, so a standard 14-calendar-day
    period runs ``start`` .. ``start + 13`` and the span is
    ``(end - start).days + 1`` (14), not ``(end - start).days`` (13).  The
    prior exclusive count dropped one calendar day per period; because
    consecutive periods tile the calendar with no gaps, that lost 1 day in
    14 (~26 days a year) of compounding -- a configured 10.5% return behaved
    as only ~9.69% effective.  These tests lock the fix so a revert to the
    exclusive count re-breaks them.
    """

    def test_fourteen_day_period_counts_days_inclusively(self):
        """A 14-inclusive-day period compounds (1 + r)^(14/365) - 1 exactly.

        period runs Jan 1 .. Jan 14 (start + 13) = 14 inclusive calendar
        days, so span_return_rate must use 14/365 -- NOT the old 13/365.
        The rate is a pure (unrounded) Decimal, so this is an exact equality
        with zero tolerance.
        """
        r = Decimal("0.105")
        period = biweekly_window(date(2027, 1, 1), 1)[0]
        # 14 inclusive days: (Jan 14 - Jan 1).days + 1 == 13 + 1 == 14.
        assert (period.end_date - period.start_date).days + 1 == 14

        expected = (Decimal("1") + r) ** (Decimal("14") / Decimal("365")) - Decimal("1")
        actual = span_return_rate(r, period.start_date, period.end_date)
        assert actual == expected
        # The date door and the day-count door are the same formula.
        assert actual == growth_rate_for_days(r, 14)

        # The corrected rate is strictly larger than the old 13/365 rate the
        # exclusive count produced, so a revert cannot pass silently.
        old_buggy = (Decimal("1") + r) ** (Decimal("13") / Decimal("365")) - Decimal("1")
        assert actual != old_buggy
        assert actual > old_buggy

    def test_same_day_period_counts_one_day(self):
        """A same-day period (start == end) compounds exactly 1/365.

        (end - start).days + 1 == 0 + 1 == 1, so a same-day period is one
        day of growth (it does not fall back to the 14-day cadence).
        """
        r = Decimal("0.105")
        same_day = derived_window([date(2027, 6, 1)], 1)[0]
        expected = (Decimal("1") + r) ** (Decimal("1") / Decimal("365")) - Decimal("1")
        assert span_return_rate(
            r, same_day.start_date, same_day.end_date,
        ) == expected

    def test_consecutive_periods_over_one_year_compound_to_annual_rate(self):
        """Gap-free periods tiling exactly one year compound to (1 + r).

        Twenty-six 14-inclusive-day periods (364 days) plus a final one-day
        period tile 2027 (a non-leap year, 365 days) with no gap or overlap.
        The inclusive spans therefore sum to exactly 365, so the product of
        the per-period factors (1 + r)^(d_i / 365) is
        (1 + r)^(sum d_i / 365) = (1 + r)^(365/365) = (1 + r).

        Under the old exclusive count the spans would have summed to only
        365 - 27 = 338 (one day lost per period), compounding to
        (1 + r)^(338/365) < (1 + r).  The product carries a sub-1e-26 Decimal
        rounding residual, so the equality is asserted after quantizing well
        above that residual (not a loosened financial assertion -- the
        identity is exact in real arithmetic).
        """
        r = Decimal("0.105")
        year_start = date(2027, 1, 1)
        year_end = date(2027, 12, 31)  # 2027 is not a leap year: 365 days.

        # Twenty-six paydays every 14 days, then one more on the year's last
        # day -- so the derivation gives period 26 its 14-day span and the
        # final period the single day left over, with no gap between them.
        paydays = [year_start + timedelta(days=14 * step) for step in range(26)]
        paydays.append(year_end)
        periods = derived_window(paydays, 1)

        # Inclusive spans tile the year with no gap: they sum to 365, and
        # each period starts the day after the previous one ends.
        total_days = sum(
            (p.end_date - p.start_date).days + 1 for p in periods
        )
        assert total_days == 365
        ordered = list(periods)
        for prev, nxt in zip(ordered, ordered[1:]):
            assert nxt.start_date == prev.end_date + timedelta(days=1)

        product = Decimal("1")
        for period in periods:
            product *= Decimal("1") + span_return_rate(
                r, period.start_date, period.end_date,
            )

        quantum = Decimal("1E-12")
        assert product.quantize(quantum) == (Decimal("1") + r).quantize(quantum)

    def test_canonical_520_period_reproduction_corrected(self):
        """The documented 520-period reproduction ends at the corrected value.

        A biweekly axis opening 2026-07-02 and running to 2046-06-01 is 520
        periods.  Projecting 27332.33 at 10.5% with no contributions:

          corrected: end = 200237.80
            200237.80 / 27332.33 = 7.3260 = (1.105)^(520*14/365)
              (14 inclusive days per period)
          old (buggy): end = 173688.32
            173688.32 / 27332.33 = 6.3547 = (1.105)^(520*13/365)
              (13 exclusive days per period)

        The engine quantizes growth to cents each period, so 200237.80 sits 6
        cents under the single-shot theoretical 27332.33*(1.105)^(520*14/365)
        = 200237.86 (accumulated per-period rounding over 520 periods).
        """
        periods = biweekly_window(date(2026, 7, 2), 520)
        assert periods[-1].start_date <= date(2046, 6, 1)
        assert len(periods) == 520

        result = project_balance(
            current_balance=Decimal("27332.33"),
            assumed_annual_return=Decimal("0.105"),
            periods=periods,
            periodic_contribution=Decimal("0"),
        )
        assert result[-1].end_balance == Decimal("200237.80")

        # The corrected endpoint is well above the old exclusive-count value.
        assert result[-1].end_balance > Decimal("173688.32")
