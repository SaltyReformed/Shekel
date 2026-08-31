"""
Shekel Budget App -- Analytics Taxes Report Service Tests (T-P3)

Hand-confirmed assertions for ``tax_report_service.compute_tax_report``: the
producer that fuses the T-P1 annual liability and the T-P2
withholding-to-date into the refund, hero chips, hybrid W-2 preview, and
Schedule A check the analytics Taxes tab (T-P4) renders.

Configs are seeded through the canonical
``auth_service._seed_tax_data_for_user`` path so every figure anchors on the
same 2026 DEFAULT_* seeds a registered user receives (single, NC, standard
deduction 16,100; brackets 10/12/22/24/32/35/37; NC flat 3.99%, std ded
12,750; FICA ss_wage_base 184,500).

Where the withholding total is delegated to ``project_salary`` (Pub 15-T
per-period, not hand-summable), the tests pin it against an INDEPENDENT
``project_salary`` oracle over the same periods -- the T-P2 pattern -- and
hand-compute everything the producer itself derives (the annual liability,
the refund identity, the rate chips, the W-2 boxes, the next-payday hint).

Periods are 26 biweekly paydays starting 2026-01-02 (indices 1..26), built
directly (the seed_user bootstrap sits in 2024 and is out of the 2026
year-periods query the producer runs):

    i=0 01-02  i=1 01-16  i=2 01-30 ... i=12 06-19  i=13 07-03 ... i=25 12-18
"""

from datetime import date, timedelta
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.extensions import db as _db
from app.models.pay_period import PayPeriod
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import CalcMethod, DeductionTiming, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from app.services import balance_at, paycheck_calculator
from app.services.balance_at import _kernel as net_worth_kernel
from app.services.auth_service import _seed_tax_data_for_user
from app.services.pay_calendar import calendar_for
from app.services.tax_config_service import load_tax_configs_for_year
from app.services.tax_report_service import (
    TaxReport,
    compute_tax_report,
)
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    SPLIT_LOAN,
    payroll_basis,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
)

ZERO = Decimal("0")


# ── Helpers ───────────────────────────────────────────────────────


def _make_profile(
    seed_user,
    *,
    name="Tax Report Profile",
    filing_status_name="single",
    state_code="NC",
    annual_salary="130000.00",
    additional_income="0.00",
    qualifying_children=0,
    other_dependents=0,
    sort_order=0,
):
    """Build and flush an active salary profile for the seeded user.

    Mirrors the liability/withholding test profile helpers.  Salary
    defaults to 130,000 (5,000.00/period at 26, no residue).  ``name`` is a
    parameter because ``uq_salary_profiles_user_scenario_name`` forbids two
    same-named profiles for one user + scenario.
    """
    filing_status = (
        _db.session.query(FilingStatus).filter_by(name=filing_status_name).one()
    )
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name=name,
        annual_salary=Decimal(annual_salary),
        filing_status_id=filing_status.id,
        state_code=state_code,
        is_active=True,
        additional_income=Decimal(additional_income),
        additional_deductions=Decimal("0.00"),
        qualifying_children=qualifying_children,
        other_dependents=other_dependents,
        sort_order=sort_order,
    )
    _db.session.add(profile)
    _db.session.flush()
    return profile


def _seed_and_profile(seed_user, **kwargs):
    """Seed the DEFAULT_* 2025/2026 tax configs and build a profile."""
    _seed_tax_data_for_user(seed_user["user"].id)
    profile = _make_profile(seed_user, **kwargs)
    _db.session.flush()
    return profile


def _make_full_year_periods(user, count=26, start=date(2026, 1, 2)):
    """Create *count* biweekly 2026 pay periods directly (period_index 1..N).

    Built directly rather than via ``seed_periods`` (10 only) so the full
    26-pay 2026 year exists; the producer's 2026 year-periods query returns
    exactly these (the seed_user bootstrap is a 2024 row).
    """
    periods = []
    for i in range(count):
        start_date = start + timedelta(days=14 * i)
        period = PayPeriod(
            user_id=user.id,
            start_date=start_date,
            end_date=start_date + timedelta(days=13),
            period_index=i + 1,
        )
        _db.session.add(period)
        periods.append(period)
    _db.session.flush()
    return periods


def _add_pretax_deduction(profile, amount, name="401k"):
    """Add a flat pre-tax deduction taken every period (26/year)."""
    pre_tax = (
        _db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
    )
    flat = _db.session.query(CalcMethod).filter_by(name="flat").one()
    ded = PaycheckDeduction(
        salary_profile_id=profile.id,
        deduction_timing_id=pre_tax.id,
        calc_method_id=flat.id,
        name=name,
        amount=Decimal(amount),
        deductions_per_year=26,
    )
    _db.session.add(ded)
    _db.session.flush()



def _derived(user_id, year=2026):
    """The year's pay periods AS THE PRODUCER SEES THEM.

    ``_load_year_periods`` reads the owner's DERIVED calendar and filters it to
    the tax year since pay-calendar plan step C2-f2d-3, so the oracle below is
    handed the same shape rather than the ORM rows the fixtures create.
    """
    return tuple(
        period for period in calendar_for(user_id).saved()
        if period.start_date.year == year
    )


def _project_sum(user_id, profile, year, periods):
    """Independent oracle: sum ``project_salary`` over *periods*.

    Same configs SSOT and calibration-aware path as the producer.  For the
    flat, sub-cap, no-deduction scenarios here the full-context and
    subset-restart projections coincide (all figures below the 184,500 SS
    wage base and 200,000 Medicare surtax threshold), so this stays a
    genuinely independent check of the producer's dollar values.

    **Since plan step balance:X-bh-1 there is no "subset restart" to
    coincide with**: the engine reads the year's paydays off the owner's
    CALENDAR, which this oracle is now handed too, so both sides carry the
    same year-to-date context and what stays independent is the period
    SUBSET each is asked to sum.
    """
    configs = load_tax_configs_for_year(user_id, profile, year)
    breakdowns = paycheck_calculator.project_salary(
        payroll_basis(profile, periods), periods, configs,
        calibration=profile.calibration,
    )
    return {
        "gross": sum((b.earnings.gross_biweekly for b in breakdowns), ZERO),
        "federal": sum((b.taxes.federal for b in breakdowns), ZERO),
        "state": sum((b.taxes.state for b in breakdowns), ZERO),
        "ss": sum((b.taxes.social_security for b in breakdowns), ZERO),
        "medicare": sum((b.taxes.medicare for b in breakdowns), ZERO),
    }


# ── Anchor: single profile, fully modeled ─────────────────────────


class TestSingleProfileFullyModeled:
    """130,000 single/NC, no checkpoint, no calibration, 26 periods."""

    def test_end_to_end_anchor(self, app, db, seed_user):
        """Refund, chips, W-2, and Schedule A on the locked anchor.

        Withholding is fully modeled (no checkpoint); gross is exact
        (130,000 / 26 = 5,000.00 * 26 = 130,000.00) and the four withholding
        lines match the project_salary oracle.

        Federal liability (2026 single, std ded 16,100):
          taxable = 130,000 - 0 - 16,100 = 113,900.00
          10%: 12,400 * 0.10               =  1,240.00
          12%: (50,400-12,400) * 0.12      =  4,560.00
          22%: (105,700-50,400) * 0.22     = 12,166.00
          24%: (113,900-105,700) * 0.24    =  1,968.00
          liability                        = 19,934.00 (0 credits)
        NC state (flat 3.99%, std ded 12,750):
          taxable = 130,000 - 12,750 = 117,250.00
          raw = 117,250 * 0.0399 = 4,678.2750 -> 4,678.28 (ROUND_HALF_UP)
        Effective rate = (19,934.00 + 4,678.28) / 130,000
                       = 24,612.28 / 130,000 = 0.1893252... -> 0.1893
        Marginal = 24% (113,900 in (105,700, 201,775]).
        next_stub with today 2026-03-01: first payday > 03-01 is 2026-03-13.
        """
        profile = _seed_and_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        periods = _derived(seed_user["user"].id)
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )
        oracle = _project_sum(seed_user["user"].id, profile, 2026, periods)

        assert isinstance(report, TaxReport)
        # Withholding: gross exact, lines == oracle, fully modeled.
        assert report.withholding.total.gross == Decimal("130000.00")
        assert report.withholding.total.federal == oracle["federal"]
        assert report.withholding.total.state == oracle["state"]
        assert report.withholding.measured_through is None
        assert report.withholding.has_checkpoint is False

        # Liability (hand-computed).
        assert report.liability.annual_wage_income == Decimal("130000.00")
        assert report.liability.annual_pretax == ZERO
        assert report.liability.federal.taxable == Decimal("113900.00")
        assert report.liability.federal.liability == Decimal("19934.00")
        assert report.liability.state.liability == Decimal("4678.28")

        # Refund = withheld - liability, federal + state.
        assert report.refund.federal_refund == (
            report.withholding.total.federal - Decimal("19934.00")
        )
        assert report.refund.state_refund == (
            report.withholding.total.state - Decimal("4678.28")
        )
        assert report.refund.total_refund == (
            report.refund.federal_refund + report.refund.state_refund
        )

        # Chips.
        assert report.chips.effective_rate == Decimal("0.1893")
        assert report.chips.marginal_rate == Decimal("0.2400")
        assert report.chips.next_stub == date(2026, 3, 13)

        # W-2 preview (no pre-tax -> box1 == gross; SS uncapped at 130k).
        assert report.w2_preview.wages.box1_wages == Decimal("130000.00")
        assert report.w2_preview.wages.box3_ss_wages == Decimal("130000.00")
        assert report.w2_preview.wages.box5_medicare_wages == Decimal("130000.00")
        assert report.w2_preview.wages.box16_state_wages == Decimal("130000.00")
        assert report.w2_preview.withheld.box2_federal == (
            report.withholding.total.federal
        )
        assert report.w2_preview.withheld.box17_state_withheld == (
            report.withholding.total.state
        )
        assert report.w2_preview.measured_through is None

        # Assumptions (single profile).
        assert report.assumptions.filing.filing_status_name == "single"
        assert report.assumptions.filing.state_code == "NC"
        assert report.assumptions.active_profile_count == 1
        assert report.assumptions.filing_inputs_from is None
        assert report.assumptions.disclosures.calibration_active is False
        assert report.assumptions.disclosures.checkpoint_as_of_date is None
        assert report.assumptions.disclosures.pretax_modeled_for_elapsed is False
        # T-P5: nonrefundable_credit_clamp retired in favour of the honest
        # ACTC disclosures (the refundable credit IS now modeled).
        assert report.assumptions.disclosures.actc_modeled is True
        assert report.assumptions.disclosures.phase_out_not_modeled is True

        # Schedule A (no loans -> mortgage 0; state component == withheld).
        assert report.schedule_a.components.mortgage_interest == ZERO
        assert report.schedule_a.components.state_income_tax == (
            report.withholding.total.state
        )
        assert report.schedule_a.components.property_tax is None
        assert report.schedule_a.property_tax_included is False
        assert report.schedule_a.salt_cap_not_applied is True
        assert report.schedule_a.standard_deduction == Decimal("16100.00")
        assert report.schedule_a.itemized_estimate == (
            report.withholding.total.state
        )
        assert report.schedule_a.margin == (
            report.schedule_a.itemized_estimate - Decimal("16100.00")
        )


class TestActcDrivesFederalRefund:
    """T-P5: a CTC-heavy household's federal refund IS the refundable ACTC."""

    def test_actc_added_to_federal_refund(self, app, db, seed_user):
        """MFJ, 4 children, 78,000 (3,000/period): liability 0, ACTC drives refund.

        Federal (2026 MFJ, std ded 32,200; CTC 2,200; refundable cap 1,700):
          taxable  = 78000 - 32200 = 45,800.00
          brackets = 24800*0.10 + (45800-24800)*0.12 = 2480 + 2520 = 5,000.00
          credits  = 4 * 2200 = 8,800.00  ->  liability max(0, 5000-8800) = 0
          ACTC     = min(unused 3800, cap 6800, earned 11325) = 3,800.00
        So federal_refund = withheld - 0 + 3,800.00 -- the ACTC is the refund
        the old nonrefundable-clamp model (liability 0, refund = withheld)
        would have missed entirely.
        """
        _seed_and_profile(
            seed_user, filing_status_name="married_jointly",
            annual_salary="78000.00", qualifying_children=4,
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.liability.federal.liability == Decimal("0.00")
        assert report.liability.federal.refundable_actc == Decimal("3800.00")
        # The ACTC is added on top of (withheld - liability).
        assert report.refund.federal_refund == (
            report.withholding.total.federal + Decimal("3800.00")
        )


# ── Checkpoint: measured differs from modeled ─────────────────────


class TestCheckpointMovesRefundByDelta:
    """A checkpoint whose measured federal differs moves the refund by it."""

    def test_federal_delta_and_box_tieouts(self, app, db, seed_user):
        """Elapsed-gross-matched checkpoint, +1,000 injected on federal.

        A checkpoint dated 2026-06-30 covers paydays i=0..12 (start
        <= 06-30; 06-19 is i=12).  Its ytd_gross is set to the modeled
        elapsed gross (5,000 * 13 = 65,000) so the ANNUAL wage income --
        and therefore the liability -- is unchanged from the fully-modeled
        case; ytd_state / SS / Medicare are set to their modeled elapsed
        values so only FEDERAL is perturbed, by exactly +1,000.

        Then:
          total.federal = checkpoint.federal + modeled_remainder.federal
                        = (elapsed_modeled.federal + 1,000) + remainder
                        = fully_modeled.federal + 1,000
        and (liability unchanged) refund.federal_refund moves by +1,000.
        Box 2 == total.federal == measured.federal + modeled.federal.
        """
        profile = _seed_and_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        # Fully-modeled baseline (no checkpoint yet).
        base = compute_tax_report(seed_user["user"].id, 2026, date(2026, 8, 1))

        # Independent oracle over the elapsed subset (i=0..12).
        elapsed = [
            p for p in _derived(seed_user["user"].id)
            if p.start_date <= date(2026, 6, 30)
        ]
        assert len(elapsed) == 13  # 06-19 is the last elapsed payday
        elapsed_oracle = _project_sum(
            seed_user["user"].id, profile, 2026, elapsed,
        )
        assert elapsed_oracle["gross"] == Decimal("65000.00")

        cp = YtdTaxCheckpoint(
            salary_profile_id=profile.id,
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("65000.00"),
            ytd_federal=elapsed_oracle["federal"] + Decimal("1000.00"),
            ytd_state=elapsed_oracle["state"],
            ytd_social_security=elapsed_oracle["ss"],
            ytd_medicare=elapsed_oracle["medicare"],
        )
        db.session.add(cp)
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 8, 1),
        )

        # Wage income (and thus liability) unchanged: same 130,000.
        assert report.withholding.total.gross == Decimal("130000.00")
        assert report.liability.federal.liability == (
            base.liability.federal.liability
        )
        # Federal withholding moved by exactly the injected +1,000.
        assert report.withholding.total.federal == (
            base.withholding.total.federal + Decimal("1000.00")
        )
        # Refund moved by exactly the delta (liability held constant).
        assert report.refund.federal_refund == (
            base.refund.federal_refund + Decimal("1000.00")
        )
        # Split identity + measured verbatim + box tie-out.
        assert report.withholding.measured.federal == (
            elapsed_oracle["federal"] + Decimal("1000.00")
        )
        assert report.withholding.total.federal == (
            report.withholding.measured.federal
            + report.withholding.modeled.federal
        )
        assert report.w2_preview.withheld.box2_federal == (
            report.withholding.total.federal
        )
        assert report.w2_preview.measured_through == date(2026, 6, 30)
        assert report.assumptions.disclosures.checkpoint_as_of_date == (
            date(2026, 6, 30)
        )
        assert report.assumptions.disclosures.pretax_modeled_for_elapsed is True


# ── Pre-tax deductions ────────────────────────────────────────────


class TestPreTaxDeductions:
    """A 401k pre-tax line reduces box 1 and the liability taxable base."""

    def test_box1_and_taxable_use_modeled_pretax(self, app, db, seed_user):
        """500/period pre-tax (26/year) = 13,000 modeled annual pre-tax.

        gross stays 130,000; box 1 = 130,000 - 13,000 = 117,000.
        Federal taxable = 130,000 - 13,000 - 16,100 = 100,900.00:
          10%: 1,240.00 + 12%: 4,560.00 + 22%: (100,900-50,400)*0.22
             = 11,110.00  -> liability 16,910.00 (0 credits).
        Marginal = 22% (100,900 in (50,400, 105,700]).
        Schedule A state component == the hybrid state withholding.
        """
        profile = _seed_and_profile(seed_user)
        _add_pretax_deduction(profile, "500.0000")
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.liability.annual_pretax == Decimal("13000.00")
        assert report.liability.federal.taxable == Decimal("100900.00")
        assert report.liability.federal.liability == Decimal("16910.00")
        assert report.chips.marginal_rate == Decimal("0.2200")
        # Box 1 / 16 net of the modeled pre-tax; box 5 stays raw gross.
        assert report.w2_preview.wages.box1_wages == Decimal("117000.00")
        assert report.w2_preview.wages.box16_state_wages == Decimal("117000.00")
        assert report.w2_preview.wages.box5_medicare_wages == Decimal("130000.00")
        # Schedule A state component tie-out.
        assert report.schedule_a.components.state_income_tax == (
            report.withholding.total.state
        )


# ── SS wage-base cap on box 3 ─────────────────────────────────────


class TestSocialSecurityWageCap:
    """Box 3 caps the gross at the year's SS wage base."""

    def test_box3_capped_above_wage_base(self, app, db, seed_user):
        """260,000 salary (> 184,500 base) -> box 3 = 184,500; box 5 raw.

        gross = 260,000 / 26 = 10,000.00 * 26 = 260,000.00.
        Box 3 (SS wages) = min(260,000, 184,500) = 184,500.
        Box 5 (Medicare wages) = 260,000 (raw gross, uncapped).
        Box 1 = 260,000 (no pre-tax).
        """
        profile = _seed_and_profile(
            seed_user, name="High Earner", annual_salary="260000.00",
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.withholding.total.gross == Decimal("260000.00")
        assert report.w2_preview.wages.box3_ss_wages == Decimal("184500")
        assert report.w2_preview.wages.box5_medicare_wages == Decimal("260000.00")
        assert report.w2_preview.wages.box1_wages == Decimal("260000.00")
        assert profile.annual_salary == Decimal("260000.00")


# ── Degrade cases ─────────────────────────────────────────────────


class TestDegradeCases:
    """No profile -> None; no periods -> all-modeled zero report."""

    def test_no_active_profile_returns_none(self, app, db, seed_user):
        """A user with a baseline scenario but no active profile -> None."""
        _seed_tax_data_for_user(seed_user["user"].id)
        db.session.commit()
        assert compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        ) is None

    def test_zero_period_year_degrades_to_zero(self, app, db, seed_user):
        """Profile but no 2026 periods -> zeros, no crash, effective None.

        Withholding is all zero (empty periods, no checkpoint); the
        liability is computed on wage 0 (taxable clamps to 0 -> 0), so both
        refunds are 0.  box 1 is 0 -> effective_rate None; no periods ->
        next_stub None.
        """
        _seed_and_profile(seed_user)
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report is not None
        assert report.withholding.total.gross == ZERO
        assert report.liability.annual_wage_income == ZERO
        assert report.liability.federal.taxable == ZERO
        assert report.liability.federal.liability == ZERO
        assert report.liability.state.liability == ZERO
        assert report.refund.federal_refund == ZERO
        assert report.refund.state_refund == ZERO
        assert report.refund.total_refund == ZERO
        assert report.chips.effective_rate is None
        assert report.chips.next_stub is None
        assert report.w2_preview.wages.box1_wages == ZERO
        assert report.w2_preview.wages.box3_ss_wages == ZERO


# ── Multi-profile (single filer, multiple jobs) ───────────────────


class TestMultiProfileSum:
    """Two active profiles sum; filing inputs come from the primary."""

    def test_sum_wages_primary_filing_inputs(self, app, db, seed_user):
        """Primary (single, 2 kids, 130k) + secondary (MFJ, 100k), summed.

        **The report's wages are what the 26 MODELLED PAYCHECKS pay, not the
        contract salaries**, and since plan step balance:X-aw those differ:
          $130,000 / 26 = $5,000.00 exactly  -> 26 x 5,000.00 = 130,000.00
          $100,000 / 26 = $3,846.1538... -> $3,846.15
                                          -> 26 x 3,846.15 =  99,999.90
          wages sum = 229,999.90, ten cents under the $230,000 of salary.
        That ten cents is the cost ruling **balance:R-HW** accepts; MED-05 /
        PA-07 had bought the round figure by giving the year's earliest
        paychecks an extra cent, which is finding N-239.  **Nothing here is
        inconsistent**: ``compute_annual_liability`` is handed
        ``withholding.total.gross``, so the liability is computed on the same
        wages the paychecks pay rather than on a salary nobody is paid.

        The liability uses the SUMMED wages with the PRIMARY's filing status
        (single) and 2 qualifying children:
          taxable = 229,999.90 - 0 - 16,100 = 213,899.90
          10%: 1,240.00 + 12%: 4,560.00 + 22%: 12,166.00
             + 24%: (201,775-105,700)*0.24 = 23,058.00
             + 32%: (213,899.90-201,775)*0.32 = 12,124.90*0.32 = 3,879.968
          before credits = 44,903.968 -> 44,903.97
          credits = 2 * 2,200 = 4,400
          liability = 40,503.97  (CTC $2,200 per OBBBA)
        Credit fully absorbed (44,903.97 > 4,400) so no ACTC.
        Marginal = 32% (213,899.90 in (201,775, 256,225]).
        Filing inputs disclosed as coming from the primary profile.
        """
        _seed_tax_data_for_user(seed_user["user"].id)
        primary = _make_profile(
            seed_user, name="Primary Job", filing_status_name="single",
            annual_salary="130000.00", qualifying_children=2, sort_order=0,
        )
        _make_profile(
            seed_user, name="Second Job",
            filing_status_name="married_jointly",
            annual_salary="100000.00", sort_order=1,
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.withholding.total.gross == Decimal("229999.90")
        assert report.liability.annual_wage_income == Decimal("229999.90")
        assert report.liability.federal.taxable == Decimal("213899.90")
        assert report.liability.federal.qualifying_children == 2
        assert report.liability.federal.liability == Decimal("40503.97")
        assert report.liability.federal.refundable_actc == Decimal("0.00")
        assert report.chips.marginal_rate == Decimal("0.3200")
        # Filing inputs disclosed from the primary.
        assert report.assumptions.filing.filing_status_name == "single"
        assert report.assumptions.active_profile_count == 2
        assert report.assumptions.filing_inputs_from == primary.name


# ── Schedule A mortgage-interest consistency oracle ───────────────


class TestScheduleAMortgageInterest:
    """The Schedule A mortgage term REUSES the year-end hybrid exactly.

    Three tests with deliberately different reach, because no one of them can do
    another's job:

    * :meth:`test_mortgage_interest_matches_year_end_hybrid` proves the WIRING --
      that the Taxes tab spends the seam's figure and ties it into
      ``itemized_estimate``.  Its oracle CALLS ``balance_at.loan_interest_in_year``,
      so it is a consistency check: it holds however wrong that function is.
    * :meth:`test_schedule_a_deducts_the_hand_computed_interest_in_the_year_paid`
      proves the VALUE, against arithmetic done by hand.  It is the only thing
      standing under this number, and it is deliberately on the LIVE path so it
      outlives the year-end summary service's deletion (plan F2 / R-D).
    * :meth:`test_a_car_loans_interest_is_not_home_mortgage_interest` proves the
      DOMAIN -- whose interest may enter the figure at all (N-9).

    All three seed the loan's KIND explicitly.  The wiring test did not, and
    silently asserted a nonzero home-mortgage deduction built from a car loan.
    """

    def test_mortgage_interest_matches_year_end_hybrid(
        self, app, db, seed_user, monkeypatch,
    ):
        """A seeded mortgage gives nonzero 2026 interest == the seam figure.

        Standing up a fresh liability/withholding fixture plus a full loan
        is disproportionate, so the mortgage term is pinned here as a
        CONSISTENCY ORACLE: the producer's
        ``schedule_a.components.mortgage_interest`` must equal
        ``balance_at.loan_interest_in_year`` for the same loan and context the
        orchestrator uses (both resolve the loan at the frozen 2026-06-01 clock),
        and be strictly positive (non-vacuous).  ``itemized_estimate`` then ties
        out to mortgage + hybrid state withholding.

        **This proves the wiring, NOT the number.**  The oracle below is the very
        function under test, so a defect inside it moves both sides together and
        this test stays green -- the shape the plan's Section 7.2 forbids relying
        on alone.  The value is pinned by the hand-computed test beneath.
        """
        freeze_today(monkeypatch, date(2026, 6, 1))
        _seed_and_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        # A MORTGAGE, which is what this test's name and docstring always
        # claimed: the fixture took ``create_loan_with_trueup``'s AUTO_LOAN
        # default, so before ``_load_mortgage_accounts`` existed this asserted
        # a nonzero HOME-MORTGAGE deduction built from a car loan.
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=SPLIT_LOAN.origination_principal,
            anchor_balance=SPLIT_LOAN.anchor_balance,
            anchor_date=SPLIT_LOAN.anchor_date,
            rate=SPLIT_LOAN.rate,
            origination_date=SPLIT_LOAN.origination_date,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 6, 1),
        )

        # Consistency oracle: the same seam producer for the same loan.  NOT an
        # independent check of the number -- see the class docstring.  With one
        # mortgage, _build_schedule_a's sum over mortgage accounts is exactly this.
        bctx = BalanceContext.build(seed_user["user"].id)
        oracle_interest = balance_at.loan_interest_in_year(loan, bctx, 2026)

        assert oracle_interest > ZERO  # non-vacuous
        assert report.schedule_a.components.mortgage_interest == oracle_interest
        assert report.schedule_a.itemized_estimate == (
            oracle_interest + report.withholding.total.state
        )
        assert report.schedule_a.standard_deduction == Decimal("16100.00")
        assert report.schedule_a.margin == (
            report.schedule_a.itemized_estimate - Decimal("16100.00")
        )
        assert report.schedule_a.components.property_tax is None

    def test_schedule_a_deducts_the_hand_computed_interest_in_the_year_paid(
        self, app, db, seed_user, monkeypatch,
    ):
        """THE VALUE: $500.00 of interest, PAID in 2025, deducts in 2025.

        The Taxes tab is the only live consumer of the mortgage-interest hybrid,
        and nothing pinned its NUMBER -- the test above spends the function under
        test as its own oracle.  So this hand-computes the figure end to end, on
        the live ``compute_tax_report`` path.

        The fixture is chosen so the answer is exactly derivable by hand rather
        than by re-running the amortization:

        * ``SPLIT_LOAN`` trues the loan up to $100,000.00 on 2026-01-10, so the
          resolver's schedule runs FORWARD from that anchor and its first row is
          2026-02-01.  **2025 therefore contains no amortization rows at all**
          (asserted below), which makes the year's figure PURE LEDGER -- the
          projected term is structurally zero, not incidentally zero.
        * The one payment splits against the balance in force at its due date:
          interest = 100000.00 * (0.06000 / 12) = **500.00**.
        * It is scheduled for 2026-02-01 but SETTLED 2025-12-20.  Mortgage
          interest deducts in the year PAID, so it belongs to 2025.

        The negative control is the year itself: a payment-DATE basis reports
        0.00 here (the payment's installment is a 2026 row), so the assertion can
        only pass on the paid-date basis the deduction actually requires.

        The loan is a MORTGAGE because :func:`_load_mortgage_accounts` selects
        only mortgages: an AUTO_LOAN here would contribute nothing and this
        oracle would vacuously pin $0.00.  ``create_loan_with_trueup`` defaults
        to AUTO_LOAN, and taking that default is what hid N-9 in the first place.
        """
        freeze_today(monkeypatch, date(2026, 6, 1))
        _seed_and_profile(seed_user)
        # The ORM rows: this case WRITES a transfer into one of them, which
        # needs the row a ``pay_period_id`` points at.
        periods = _make_full_year_periods(seed_user["user"])
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=SPLIT_LOAN.origination_principal,
            anchor_balance=SPLIT_LOAN.anchor_balance,
            anchor_date=SPLIT_LOAN.anchor_date,
            rate=SPLIT_LOAN.rate,
            origination_date=SPLIT_LOAN.origination_date,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        # Due 2026-02-01 (period index 1, payment_day 1); settled 2025-12-20.
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan,
            periods[1], amount=Decimal("1000.00"),
            settled_on=date(2025, 12, 20),
        )
        db.session.commit()

        # The premise, asserted rather than assumed: 2025 carries no schedule
        # rows, so the figure below cannot be coming from the projection.
        bctx = BalanceContext.build(seed_user["user"].id)
        rows_2025 = [
            row
            for row in net_worth_kernel.debt_schedule_rows([loan], bctx)[loan.id]
            if row.payment_date.year == 2025
        ]
        assert rows_2025 == []

        report = compute_tax_report(
            seed_user["user"].id, 2025, date(2026, 6, 1),
        )

        # 100000.00 * 0.06000 / 12 = 500.00, deducted in the year PAID.
        assert report.schedule_a.components.mortgage_interest == Decimal("500.00")
        assert report.schedule_a.itemized_estimate == (
            Decimal("500.00") + report.withholding.total.state
        )

    def test_a_car_loans_interest_is_not_home_mortgage_interest(
        self, app, db, seed_user, monkeypatch,
    ):
        """An AUTO_LOAN contributes NOTHING to the Schedule A mortgage term.

        Personal interest is not deductible.  The pre-fix ``_load_debt_accounts``
        selected on ``has_amortization`` alone -- which is set on AUTO_LOAN,
        STUDENT_LOAN, PERSONAL_LOAN and HELOC just as it is on MORTGAGE -- so
        this exact fixture reported **$5,221.16** of a car loan's interest as
        home mortgage interest (measured 2026-07-16 by restoring the old
        selection).

        The loan here is otherwise identical to the mortgage the sibling tests
        seed -- same principal, rate, dates, and true-up -- so the ONLY thing
        producing the zero is the account's KIND.

        Both assertions discriminate: restore the amortization-only selection and
        the first reads $5,221.16 and the second is off by the same.  ``margin``
        is deliberately NOT asserted -- the standard deduction happens to win
        either way here, so it would pass while the number was wrong, and this
        file has enough of those already.  The harm is not that this fixture
        flips the election; it is that the deduction is overstated by $5,221.16,
        which flips it for anyone near the threshold.
        """
        freeze_today(monkeypatch, date(2026, 6, 1))
        _seed_and_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=SPLIT_LOAN.origination_principal,
            anchor_balance=SPLIT_LOAN.anchor_balance,
            anchor_date=SPLIT_LOAN.anchor_date,
            rate=SPLIT_LOAN.rate,
            origination_date=SPLIT_LOAN.origination_date,
            account_type=AcctTypeEnum.AUTO_LOAN,
        )
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 6, 1),
        )

        assert report.schedule_a.components.mortgage_interest == ZERO
        # Nothing but the state withholding is left to itemize.
        assert report.schedule_a.itemized_estimate == report.withholding.total.state
