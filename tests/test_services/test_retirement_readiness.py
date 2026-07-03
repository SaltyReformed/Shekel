"""
Shekel Budget App -- Retirement Readiness Producer Tests (P1c)

Covers ``retirement_readiness.compute_readiness_data`` and its pure
helpers: the net-frame funded verdict (ruling 2), the F1 missing-tax-rate
flag, the downsampled flight-path chart series, the countdown facts, and
the per-account contribution facts.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum, RaiseTypeEnum
from app.models.investment_params import InvestmentParams
from app.models.pension_profile import PensionProfile
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.user import UserSettings
from app.services import account_service, retirement_readiness
from app.services.retirement_gap_calculator import RetirementGapAnalysis
from app.services.retirement_readiness import (
    _build_countdown,
    _downsample_indices,
    funded_ratio_state,
)
from app.utils.money import round_money


def _gap_analysis(*, required, after_tax_projected):
    """Build a minimal net-frame gap analysis for the funded_ratio_state unit tests."""
    return RetirementGapAnalysis(
        pre_retirement_net_monthly=Decimal("5000.00"),
        monthly_pension_income=Decimal("2000.00"),
        after_tax_monthly_pension=Decimal("2000.00"),
        monthly_income_gap=Decimal("3000.00"),
        required_retirement_savings=required,
        projected_total_savings=after_tax_projected,
        savings_surplus_or_shortfall=after_tax_projected - required,
        safe_withdrawal_rate=Decimal("0.0400"),
        after_tax_projected_savings=after_tax_projected,
        after_tax_surplus_or_shortfall=after_tax_projected - required,
    )


class TestFundedRatio:
    """The funded-ratio guard (ruling 2 / fork F1's division guard)."""

    def test_positive_requirement_divides(self):
        """funded = after-tax projected / required, quantized to 0.0001.

        100,000 / 200,000 = 0.5000 exactly.
        """
        ratio, no_needed = funded_ratio_state(
            _gap_analysis(
                required=Decimal("200000.00"),
                after_tax_projected=Decimal("100000.00"),
            )
        )
        assert no_needed is False
        assert ratio == Decimal("0.5000")

    def test_zero_requirement_is_no_savings_needed(self):
        """A zero requirement reports the distinct state, not a division.

        required == 0 (pension fully covers the gap) -> funded_ratio None,
        no_savings_needed True (never a divide-by-zero).
        """
        ratio, no_needed = funded_ratio_state(
            _gap_analysis(
                required=Decimal("0"),
                after_tax_projected=Decimal("100000.00"),
            )
        )
        assert no_needed is True
        assert ratio is None


class TestDownsampleIndices:
    """The chart downsampler (<= 48 points, first and last always kept)."""

    def test_short_series_returns_all(self):
        # 3 <= 48 -> every index kept, in order.
        assert _downsample_indices(3) == [0, 1, 2]

    def test_empty_series(self):
        assert _downsample_indices(0) == []

    def test_long_series_is_bounded_and_ends_inclusive(self):
        """520 periods downsample to <= 48 points, first=0 and last=519.

        The indices are strictly increasing (de-duplicated) so no plotted
        point repeats.
        """
        indices = _downsample_indices(520)
        assert len(indices) <= 48
        assert indices[0] == 0            # first always included
        assert indices[-1] == 519         # last always included
        assert indices == sorted(indices)
        assert len(set(indices)) == len(indices)   # no duplicates


class TestBuildCountdown:
    """The countdown facts."""

    def test_no_horizon(self):
        """No retirement date -> zeroed countdown, no date."""
        assert _build_countdown(None, []) == {
            "periods_remaining": 0,
            "years_remaining": Decimal("0.0"),
            "retirement_date": None,
        }

    def test_years_and_periods(self):
        """periods_remaining is the synthetic count; years is days/365.25.

        A retirement date 365 days out gives 365 / 365.25 = 0.9993... ->
        1.0 (one decimal, round-half-up); the synthetic-period count passes
        straight through.
        """
        planned = date.today().replace(year=date.today().year + 1)
        days = (planned - date.today()).days
        # Fabricate a synthetic-period list only for the count.
        fake_periods = [object()] * 7
        result = _build_countdown(planned, fake_periods)
        assert result["periods_remaining"] == 7
        assert result["retirement_date"] == planned
        expected_years = (
            Decimal(days) / Decimal("365.25")
        ).quantize(Decimal("0.1"))
        assert result["years_remaining"] == expected_years


def _build_scenario(db, seed_user, *, tax_rate=None):
    """Seed a salary profile, a pension with a horizon, and a 401(k).

    Returns the created account.  ``account_service.create_account`` anchors
    against the current pay period (the ``seed_periods_today`` fixture makes
    one exist), so the projection has a live current period.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    filing = db.session.query(FilingStatus).first()

    profile = SalaryProfile(
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing.id,
        name="Day Job",
        annual_salary=Decimal("80000.00"),
        pay_periods_per_year=26,
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    db.session.flush()

    db.session.add(PensionProfile(
        user_id=user.id,
        salary_profile_id=profile.id,
        name="State Pension",
        benefit_multiplier=Decimal("0.01850"),
        consecutive_high_years=4,
        hire_date=date(2010, 1, 1),
        planned_retirement_date=date(date.today().year + 20, 6, 1),
        is_active=True,
    ))

    inv_type = db.session.query(AccountType).filter_by(name="401(k)").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=inv_type.id,
            name="401k",
            anchor_balance=Decimal("50000.00"),
        ),
    )
    db.session.flush()
    db.session.add(InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=Decimal("0.07000"),
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.NONE
        ),
    ))

    if tax_rate is not None:
        settings = (
            db.session.query(UserSettings)
            .filter_by(user_id=user.id)
            .one()
        )
        settings.estimated_retirement_tax_rate = tax_rate
    db.session.commit()
    return acct


class TestComputeReadinessData:
    """The full readiness producer against a seeded scenario."""

    def test_tax_rate_missing_net_frame_and_chart(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Unset tax rate -> F1 explicit-0% frame, and the chart invariants.

        With no estimated tax rate: ``tax_rate_missing`` True, the effective
        rate is Decimal("0"), so net pension == gross pension and after-tax
        projected == pre-tax projected (0% tax on the traditional 401(k)).
        The funded ratio matches its own components; the chart series share
        one downsampled axis whose endpoints equal the producer's own
        totals; the countdown reports the pension horizon.
        """
        with app.app_context():
            acct = _build_scenario(db, seed_user)
            data = retirement_readiness.compute_readiness_data(
                seed_user["user"].id
            )

            # F1: missing rate is an explicit 0% with the surfaced flag.
            assert data["tax_rate_missing"] is True
            assert data["estimated_tax_rate"] == Decimal("0")
            # 0% tax -> net == gross and after-tax == pre-tax.
            assert data["pension_net_monthly"] == data["pension_gross_monthly"]
            assert (
                data["projected_savings_after_tax"]
                == data["projected_savings_pretax"]
            )

            # Funded ratio is internally consistent with its components.
            assert data["required_savings"] > Decimal("0")
            assert data["funded_ratio"] == (
                data["projected_savings_after_tax"] / data["required_savings"]
            ).quantize(Decimal("0.0001"))
            assert data["no_savings_needed"] is False
            assert data["surplus_or_shortfall_after_tax"] == (
                data["projected_savings_after_tax"] - data["required_savings"]
            )

            # Chart: both series + dates share one <= 48-point axis, all
            # string Decimals; the endpoints equal the producer's totals.
            chart = data["chart"]
            count = len(chart["your_path"])
            assert count == len(chart["needed_path"]) == len(chart["dates"])
            assert 2 <= count <= 48
            for encoded in chart["your_path"] + chart["needed_path"]:
                Decimal(encoded)  # must parse (string-Decimal encoding)
            # "your path" is AFTER-TAX (ruling 2): it ends at the after-tax
            # projected total -- the funded ratio's numerator -- so the
            # chart endpoint and the hero verdict agree.  With the F1
            # explicit-0% rate, after-tax == pre-tax (x * (1 - 0) = x), so
            # both equalities hold on untaxed data.
            assert Decimal(chart["your_path"][-1]) == (
                data["projected_savings_after_tax"]
            )
            assert Decimal(chart["your_path"][-1]) == (
                data["projected_savings_pretax"]
            )
            # "needed path" ends at the required target (reverse anchor).
            assert Decimal(chart["needed_path"][-1]) == data["required_savings"]

            # Countdown: the pension horizon drives the facts.
            assert data["retirement_date"] == date(date.today().year + 20, 6, 1)
            assert data["periods_remaining"] > 0
            assert data["years_remaining"] > Decimal("0")

            # Per-account facts: one row, none linked (no deduction/transfer).
            assert len(data["account_contributions"]) == 1
            fact = data["account_contributions"][0]
            assert fact["account"].id == acct.id
            assert fact["none_linked"] is True

    def test_tax_rate_applied_reduces_pension_and_traditional(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A stored 20% rate nets the pension and taxes the traditional 401(k).

        pension_net == round(pension_gross * (1 - 0.20)); the traditional
        401(k)'s after-tax projection is strictly below its pre-tax value;
        and the chart's "your path" endpoint equals the AFTER-TAX projected
        total (ruling 2: the chart plots the same frame as the verdict).
        The scenario's only account is a traditional 401(k), so the
        endpoint also equals round(pretax * (1 - 0.20)) exactly.
        """
        with app.app_context():
            _build_scenario(db, seed_user, tax_rate=Decimal("0.2000"))
            data = retirement_readiness.compute_readiness_data(
                seed_user["user"].id
            )

            assert data["tax_rate_missing"] is False
            assert data["estimated_tax_rate"] == Decimal("0.2000")
            # pension net = gross * (1 - 0.20).
            assert data["pension_net_monthly"] == round_money(
                data["pension_gross_monthly"] * Decimal("0.80")
            )
            # Chart endpoint == after-tax projected == funded numerator.
            chart = data["chart"]
            assert Decimal(chart["your_path"][-1]) == (
                data["projected_savings_after_tax"]
            )
            # All-traditional portfolio: after-tax = round(pretax * 0.80).
            assert data["projected_savings_after_tax"] == round_money(
                data["projected_savings_pretax"] * Decimal("0.80")
            )
            # And the endpoint therefore matches the funded ratio numerator:
            # funded = after_tax / required (quantized 0.0001).
            assert data["funded_ratio"] == (
                Decimal(chart["your_path"][-1]) / data["required_savings"]
            ).quantize(Decimal("0.0001"))
            # The 401(k) is pre-tax, so after-tax projected < pre-tax.
            assert (
                data["projected_savings_after_tax"]
                < data["projected_savings_pretax"]
            )


class TestComputeReadinessWhatif:
    """The P3a what-if wrapper: baseline, override state, and deltas."""

    def test_no_overrides_returns_baseline_without_deltas(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No overrides: the displayed state IS the baseline, deltas None."""
        with app.app_context():
            _build_scenario(db, seed_user)
            result = retirement_readiness.compute_readiness_whatif(
                seed_user["user"].id,
            )
            assert result["deltas"] is None
            assert result["readiness"] is result["baseline"]

    def test_swr_override_deltas(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A 2% SWR what-if (vs the stored 4%) doubles the requirement.

        required = round(monthly_gap * 12 / swr) and the monthly gap does
        not depend on the SWR, so the override requirement is exactly
        round(monthly_gap * 12 / 0.02).  Funded drops (same after-tax
        projection over a larger requirement), so the points delta is
        negative and equals (override - baseline) * 100 quantized to 0.1;
        the dollars delta is the surplus difference and is negative (the
        position worsens by the extra requirement).
        """
        with app.app_context():
            _build_scenario(db, seed_user)
            result = retirement_readiness.compute_readiness_whatif(
                seed_user["user"].id, swr_override=Decimal("0.02"),
            )
            baseline = result["baseline"]
            override = result["readiness"]
            deltas = result["deltas"]

            # The gap itself is SWR-independent...
            assert override["monthly_gap_net"] == baseline["monthly_gap_net"]
            # ...so required_override = round(gap * 12 / 0.02) exactly.
            assert override["required_savings"] == round_money(
                baseline["monthly_gap_net"] * 12 / Decimal("0.02")
            )
            assert deltas["funded_ratio_points"] == (
                (override["funded_ratio"] - baseline["funded_ratio"])
                * Decimal("100")
            ).quantize(Decimal("0.1"))
            assert deltas["funded_ratio_points"] < 0
            assert deltas["shortfall_dollars"] == (
                override["surplus_or_shortfall_after_tax"]
                - baseline["surplus_or_shortfall_after_tax"]
            )
            assert deltas["shortfall_dollars"] < 0

    def test_merit_horizon_override_moves_the_target(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Horizon 0 (vs stored 5) freezes a merit raise sooner.

        With a 5% recurring January MERIT raise, the stored horizon 5
        compounds it through cutoff = current year + 5 (6 applications:
        salary x 1.05^6) before freezing; the override horizon 0 freezes
        it after this year's single application (salary x 1.05^1).  A
        smaller final-year salary means a smaller net income target, a
        smaller requirement, and therefore a HIGHER funded ratio -- the
        delta signs pin the override path end to end.
        """
        with app.app_context():
            _build_scenario(db, seed_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
                effective_month=1,
                effective_year=date.today().year,
                percentage=Decimal("0.0500"),
                is_recurring=True,
            ))
            db.session.commit()

            result = retirement_readiness.compute_readiness_whatif(
                seed_user["user"].id, merit_horizon_override=0,
            )
            baseline = result["baseline"]
            override = result["readiness"]
            deltas = result["deltas"]

            assert override["required_savings"] < baseline["required_savings"]
            assert deltas["funded_ratio_points"] > 0
            assert deltas["shortfall_dollars"] > 0
