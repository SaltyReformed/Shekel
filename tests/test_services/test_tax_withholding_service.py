"""
Shekel Budget App -- YTD Tax Withholding Service Tests (T-P2)

Hand-confirmed assertions for ``tax_withholding_service``: the checkpoint
CRUD (``latest_checkpoint`` / ``save_checkpoint``) and the
withholding-to-date producer (``compute_withholding_to_date``).

The producer's rule is "measured checkpoint + calibrated projection for the
remainder", computed with FULL-YEAR engine context: the projection runs
over the entire year's period list (so cumulative wages drive the SS
wage-base cap and the Medicare surtax threshold, and month grouping drives
monthly-capped deductions) and only the remainder periods' breakdowns are
summed.  It re-implements no tax math -- the modeled remainder is
delegated to ``paycheck_calculator.project_salary`` -- so the arithmetic
these tests pin is:

* the period PARTITION (which periods are measured vs modeled), verified by
  comparing the producer's projected side against an INDEPENDENT
  ``project_salary`` call over the hand-determined remainder subset.  That
  oracle deliberately projects the remainder ALONE (its cumulative context
  restarts at zero), which coincides with the producer's full-context
  figures for these scenarios because they sit far below the 2026 SS wage
  base (184,500) and Medicare surtax threshold (200,000) with no
  deductions -- keeping the oracle independent of the producer's
  internals.  ``TestFullYearCapContext`` pins the case where the two
  DIVERGE (a high earner crossing the SS cap mid-year) with absolute
  hand-computed dollars;
* the SPLIT IDENTITY ``total == measured + projected`` (component-wise);
* the measured side taken VERBATIM from the checkpoint;
* the gross figure, which is exactly computable (salary 130,000 / 26 =
  5,000.00 per period, no rounding residue); and
* the four withholding lines on the CALIBRATION path, where each line is a
  simple ``round(base * rate)`` (federal/state on taxable, medicare on
  gross, SS capped) and is hand-computed in the test docstring.

Pay periods come from ``seed_periods`` (10 biweekly periods, all in 2026,
starting 2026-01-02 with a 14-day cadence):

    P0 01-02  P1 01-16  P2 01-30  P3 02-13  P4 02-27
    P5 03-13  P6 03-27  P7 04-10  P8 04-24  P9 05-08
"""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db as _db
from app.models.calibration_override import CalibrationOverride
from app.models.pay_period import PayPeriod
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from app.services import paycheck_calculator
from app.services.auth_service import _seed_tax_data_for_user
from app.services.tax_config_service import load_tax_configs_for_year
from app.services.tax_withholding_service import (
    CheckpointFigures,
    WithholdingToDate,
    compute_withholding_to_date,
    latest_checkpoint,
    save_checkpoint,
)

ZERO = Decimal("0")


def _make_profile(
    seed_user, name="Withholding Test Profile", annual_salary="130000.00",
):
    """Build and flush an active single/NC SalaryProfile.

    Default salary 130,000 (5,000.00/period at 26, no residue); the
    cap-context test passes 260,000 (10,000.00/period) to cross the 2026
    SS wage base mid-year.
    """
    filing_status = (
        _db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name=name,
        annual_salary=Decimal(annual_salary),
        pay_periods_per_year=26,
        filing_status_id=filing_status.id,
        state_code="NC",
        is_active=True,
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
    """Create *count* biweekly pay periods directly (period_index 1..count).

    26 periods from 2026-01-02 span P0 01-02 .. P25 12-18 -- a full 26-pay
    calendar year (the seed_user bootstrap period sits at index 0 in 2024
    and is never passed to the producer).  Built directly rather than via
    ``seed_periods`` (which creates only 10) because the cap-context test
    needs the SS wage-base crossing at paycheck 19.
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


def _add_checkpoint(profile, as_of_date, **figures):
    """Insert a checkpoint directly (independent of ``save_checkpoint``)."""
    defaults = {
        "ytd_gross": Decimal("40000.00"),
        "ytd_federal": Decimal("4000.00"),
        "ytd_state": Decimal("1500.00"),
        "ytd_social_security": Decimal("2480.00"),
        "ytd_medicare": Decimal("580.00"),
    }
    defaults.update(figures)
    cp = YtdTaxCheckpoint(
        salary_profile_id=profile.id, as_of_date=as_of_date, **defaults,
    )
    _db.session.add(cp)
    _db.session.flush()
    return cp


def _expected_projected(user_id, profile, year, periods):
    """Sum ``project_salary`` over *periods* -- the independent oracle.

    Same configs SSOT and calibration-aware path as the producer, but over
    ONLY the passed subset: its cumulative context restarts at zero rather
    than carrying the full-year state the producer uses.  For this file's
    flat-5,000-gross no-deduction scenarios the two are identical (all
    figures sit far below the SS wage base and surtax threshold), so this
    stays a genuinely independent check of the producer's partition and
    dollar values; the divergent above-cap case is pinned with absolute
    hand-computed dollars in ``TestFullYearCapContext`` instead.
    """
    configs = load_tax_configs_for_year(user_id, profile, year)
    breakdowns = paycheck_calculator.project_salary(
        profile, periods, configs, calibration=profile.calibration,
    )
    return {
        "gross": sum((b.earnings.gross_biweekly for b in breakdowns), ZERO),
        "federal": sum((b.taxes.federal for b in breakdowns), ZERO),
        "state": sum((b.taxes.state for b in breakdowns), ZERO),
        "social_security": sum(
            (b.taxes.social_security for b in breakdowns), ZERO,
        ),
        "medicare": sum((b.taxes.medicare for b in breakdowns), ZERO),
    }


def _assert_projected_equals(projected, expected):
    """Assert a WithholdingComponents matches an oracle dict component-wise."""
    assert projected.gross == expected["gross"]
    assert projected.federal == expected["federal"]
    assert projected.state == expected["state"]
    assert projected.social_security == expected["social_security"]
    assert projected.medicare == expected["medicare"]


class TestLatestCheckpoint:
    """latest_checkpoint returns the max in-year as_of_date, or None."""

    def test_returns_max_as_of_date_in_year(self, app, db, seed_user):
        """Two 2026 checkpoints -> the later-dated one is returned."""
        profile = _seed_and_profile(seed_user)
        _add_checkpoint(profile, date(2026, 2, 1), ytd_gross=Decimal("10000.00"))
        march = _add_checkpoint(
            profile, date(2026, 3, 1), ytd_gross=Decimal("20000.00"),
        )
        db.session.commit()

        result = latest_checkpoint(profile.id, 2026)
        assert result is not None
        assert result.id == march.id
        assert result.as_of_date == date(2026, 3, 1)

    def test_none_when_no_checkpoint_in_year(self, app, db, seed_user):
        """A 2025-dated checkpoint is not a candidate for year 2026."""
        profile = _seed_and_profile(seed_user)
        _add_checkpoint(profile, date(2025, 12, 20))
        db.session.commit()
        assert latest_checkpoint(profile.id, 2026) is None

    def test_ignores_other_profiles_checkpoint(self, app, db, seed_user):
        """A different profile's checkpoint is never returned."""
        profile_a = _seed_and_profile(seed_user, name="Profile A")
        profile_b = _make_profile(seed_user, name="Profile B")
        _add_checkpoint(profile_b, date(2026, 4, 1))
        db.session.commit()
        assert latest_checkpoint(profile_a.id, 2026) is None


class TestSaveCheckpoint:
    """save_checkpoint upserts on (profile, as_of_date)."""

    def test_insert_new_date(self, app, db, seed_user):
        """A new date inserts a row with the given figures."""
        profile = _seed_and_profile(seed_user)
        db.session.commit()
        figures = CheckpointFigures(
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("60000.00"),
            ytd_federal=Decimal("6000.00"),
            ytd_state=Decimal("2400.00"),
            ytd_social_security=Decimal("3720.00"),
            ytd_medicare=Decimal("870.00"),
            notes="mid-year stub",
        )
        cp = save_checkpoint(profile.id, figures)
        db.session.commit()

        assert cp.id is not None
        assert cp.ytd_gross == Decimal("60000.00")
        assert cp.notes == "mid-year stub"
        count = (
            db.session.query(YtdTaxCheckpoint)
            .filter_by(salary_profile_id=profile.id)
            .count()
        )
        assert count == 1

    def test_resave_same_date_updates_in_place(self, app, db, seed_user):
        """Re-entering the same date REPLACES the row (no second row)."""
        profile = _seed_and_profile(seed_user)
        db.session.commit()
        base = CheckpointFigures(
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("60000.00"),
            ytd_federal=Decimal("6000.00"),
            ytd_state=Decimal("2400.00"),
            ytd_social_security=Decimal("3720.00"),
            ytd_medicare=Decimal("870.00"),
        )
        first = save_checkpoint(profile.id, base)
        db.session.commit()
        first_id = first.id

        corrected = CheckpointFigures(
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("61000.00"),
            ytd_federal=Decimal("6100.00"),
            ytd_state=Decimal("2450.00"),
            ytd_social_security=Decimal("3782.00"),
            ytd_medicare=Decimal("884.50"),
            notes="corrected",
        )
        second = save_checkpoint(profile.id, corrected)
        db.session.commit()

        assert second.id == first_id  # same row updated
        assert second.ytd_gross == Decimal("61000.00")
        assert second.notes == "corrected"
        count = (
            db.session.query(YtdTaxCheckpoint)
            .filter_by(salary_profile_id=profile.id)
            .count()
        )
        assert count == 1

    def test_new_date_inserts_second_row(self, app, db, seed_user):
        """A different date inserts a second row (history-keeping)."""
        profile = _seed_and_profile(seed_user)
        db.session.commit()
        save_checkpoint(profile.id, CheckpointFigures(
            as_of_date=date(2026, 3, 31),
            ytd_gross=Decimal("30000.00"),
            ytd_federal=Decimal("3000.00"),
            ytd_state=Decimal("1200.00"),
            ytd_social_security=Decimal("1860.00"),
            ytd_medicare=Decimal("435.00"),
        ))
        save_checkpoint(profile.id, CheckpointFigures(
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("60000.00"),
            ytd_federal=Decimal("6000.00"),
            ytd_state=Decimal("2400.00"),
            ytd_social_security=Decimal("3720.00"),
            ytd_medicare=Decimal("870.00"),
        ))
        db.session.commit()
        count = (
            db.session.query(YtdTaxCheckpoint)
            .filter_by(salary_profile_id=profile.id)
            .count()
        )
        assert count == 2


class TestComputeNoCheckpoint:
    """With no checkpoint the whole year is modeled; measured is zero."""

    def test_fully_modeled_sum(self, app, db, seed_user, seed_periods):
        """No checkpoint -> measured zeros, remainder = all 10 periods.

        Gross is exact: 130,000 / 26 = 5,000.00 per period, so
        projected.gross = 5,000.00 * 10 = 50,000.00.  The four withholding
        lines are delegated to project_salary and verified against the
        independent oracle over the same 10 periods.  total == projected
        (measured is zero).
        """
        profile = _seed_and_profile(seed_user)
        db.session.commit()

        result = compute_withholding_to_date(
            seed_user["user"].id, profile, 2026, seed_periods,
        )
        expected = _expected_projected(
            seed_user["user"].id, profile, 2026, seed_periods,
        )

        assert isinstance(result, WithholdingToDate)
        assert result.checkpoint is None
        assert result.measured_through is None
        # Measured side is all zeros.
        assert result.measured.gross == ZERO
        assert result.measured.federal == ZERO
        # Projected == oracle over all 10 periods.
        _assert_projected_equals(result.projected, expected)
        assert result.projected.gross == Decimal("5000.00") * 10
        # total == measured + projected == projected (measured zero).
        assert result.total.gross == result.projected.gross
        assert result.total.federal == result.projected.federal
        assert result.total.state == result.projected.state
        assert result.total.social_security == result.projected.social_security
        assert result.total.medicare == result.projected.medicare


class TestComputeWithCheckpoint:
    """A mid-year checkpoint splits measured (verbatim) from modeled remainder."""

    def test_mid_period_split(self, app, db, seed_user, seed_periods):
        """Checkpoint dated 2026-01-15 (mid-P0, after P0's 01-02 payday).

        P0's payday (01-02) is on/before the stub date, so P0 is MEASURED
        (its paycheck is in the checkpoint, not double-counted).  The
        remainder is P1..P9 (start_date > 2026-01-15), i.e. 9 periods.

        Measured = the checkpoint's five figures verbatim.
        projected.gross = 5,000.00 * 9 = 45,000.00; projected withholding ==
        oracle over P1..P9.  total.<line> = measured + projected.
        """
        profile = _seed_and_profile(seed_user)
        cp = _add_checkpoint(
            profile, date(2026, 1, 15),
            ytd_gross=Decimal("5000.00"),
            ytd_federal=Decimal("500.00"),
            ytd_state=Decimal("200.00"),
            ytd_social_security=Decimal("310.00"),
            ytd_medicare=Decimal("72.50"),
        )
        db.session.commit()

        remainder = seed_periods[1:]  # P1..P9
        result = compute_withholding_to_date(
            seed_user["user"].id, profile, 2026, seed_periods,
        )
        expected = _expected_projected(
            seed_user["user"].id, profile, 2026, remainder,
        )

        assert result.checkpoint is not None
        assert result.checkpoint.id == cp.id
        assert result.measured_through == date(2026, 1, 15)
        # Measured taken verbatim from the checkpoint.
        assert result.measured.gross == Decimal("5000.00")
        assert result.measured.federal == Decimal("500.00")
        assert result.measured.state == Decimal("200.00")
        assert result.measured.social_security == Decimal("310.00")
        assert result.measured.medicare == Decimal("72.50")
        # Projected == oracle over P1..P9 (P0 excluded).
        _assert_projected_equals(result.projected, expected)
        assert result.projected.gross == Decimal("5000.00") * 9
        # Split identity, component-wise.
        assert result.total.gross == Decimal("5000.00") + expected["gross"]
        assert result.total.federal == Decimal("500.00") + expected["federal"]
        assert result.total.state == Decimal("200.00") + expected["state"]
        assert (
            result.total.social_security
            == Decimal("310.00") + expected["social_security"]
        )
        assert result.total.medicare == Decimal("72.50") + expected["medicare"]

    def test_on_payday_boundary(self, app, db, seed_user, seed_periods):
        """Checkpoint dated exactly ON P1's payday (2026-01-16).

        ``start_date > as_of_date`` is STRICT, so P1 (start 01-16) is NOT in
        the remainder -- its paycheck is covered by the stub.  Remainder is
        P2..P9 (8 periods); projected.gross = 5,000.00 * 8 = 40,000.00.
        """
        profile = _seed_and_profile(seed_user)
        _add_checkpoint(profile, date(2026, 1, 16))
        db.session.commit()

        remainder = seed_periods[2:]  # P2..P9
        result = compute_withholding_to_date(
            seed_user["user"].id, profile, 2026, seed_periods,
        )
        expected = _expected_projected(
            seed_user["user"].id, profile, 2026, remainder,
        )

        assert result.measured_through == date(2026, 1, 16)
        _assert_projected_equals(result.projected, expected)
        assert result.projected.gross == Decimal("5000.00") * 8

    def test_different_year_checkpoint_ignored(
        self, app, db, seed_user, seed_periods,
    ):
        """A 2025 checkpoint is ignored for year 2026 -> fully modeled."""
        profile = _seed_and_profile(seed_user)
        _add_checkpoint(profile, date(2025, 12, 20))
        db.session.commit()

        result = compute_withholding_to_date(
            seed_user["user"].id, profile, 2026, seed_periods,
        )
        expected = _expected_projected(
            seed_user["user"].id, profile, 2026, seed_periods,
        )

        assert result.checkpoint is None
        assert result.measured_through is None
        assert result.measured.gross == ZERO
        _assert_projected_equals(result.projected, expected)
        assert result.projected.gross == Decimal("5000.00") * 10


class TestComputeEmptyPeriods:
    """An empty period list yields an all-zero modeled remainder."""

    def test_empty_periods_with_checkpoint(self, app, db, seed_user):
        """No periods -> projected zeros; total == measured (the checkpoint)."""
        profile = _seed_and_profile(seed_user)
        _add_checkpoint(
            profile, date(2026, 6, 30),
            ytd_gross=Decimal("60000.00"),
            ytd_federal=Decimal("6000.00"),
            ytd_state=Decimal("2400.00"),
            ytd_social_security=Decimal("3720.00"),
            ytd_medicare=Decimal("870.00"),
        )
        db.session.commit()

        result = compute_withholding_to_date(
            seed_user["user"].id, profile, 2026, [],
        )
        # Remainder is empty -> projected all zero.
        assert result.projected.gross == ZERO
        assert result.projected.federal == ZERO
        assert result.projected.state == ZERO
        assert result.projected.social_security == ZERO
        assert result.projected.medicare == ZERO
        # total == measured (checkpoint verbatim).
        assert result.total.gross == Decimal("60000.00")
        assert result.total.federal == Decimal("6000.00")
        assert result.total.medicare == Decimal("870.00")

    def test_empty_periods_no_checkpoint(self, app, db, seed_user):
        """No periods AND no checkpoint -> everything zero."""
        profile = _seed_and_profile(seed_user)
        db.session.commit()
        result = compute_withholding_to_date(
            seed_user["user"].id, profile, 2026, [],
        )
        assert result.total.gross == ZERO
        assert result.total.federal == ZERO
        assert result.projected.gross == ZERO
        assert result.measured.gross == ZERO
        assert result.checkpoint is None


class TestCalibrationExactWithholding:
    """The calibration path gives hand-computable per-line withholding."""

    def test_calibrated_remainder_exact(self, app, db, seed_user, seed_periods):
        """Active calibration, no pre-tax deductions -> exact per-line dollars.

        Rates: federal 10%, state 5%, SS 6.2%, Medicare 1.45%.  With no
        pre-tax deductions taxable == gross == 5,000.00, so per period:
          federal  = round(5,000 * 0.10)   = 500.00
          state    = round(5,000 * 0.05)   = 250.00
          medicare = round(5,000 * 0.0145) =  72.50
          SS       = round(5,000 * 0.062)  = 310.00  (cumulative << 176,100
                     wage base over 2 periods, so uncapped)
        Over the 2 modeled periods (no checkpoint):
          gross 10,000.00; federal 1,000.00; state 500.00; medicare 145.00;
          SS 620.00.
        """
        profile = _seed_and_profile(seed_user)
        calibration = CalibrationOverride(
            salary_profile_id=profile.id,
            actual_gross_pay=Decimal("5000.00"),
            actual_federal_tax=Decimal("500.00"),
            actual_state_tax=Decimal("250.00"),
            actual_social_security=Decimal("310.00"),
            actual_medicare=Decimal("72.50"),
            effective_federal_rate=Decimal("0.10"),
            effective_state_rate=Decimal("0.05"),
            effective_ss_rate=Decimal("0.062"),
            effective_medicare_rate=Decimal("0.0145"),
            pay_stub_date=date(2026, 1, 15),
            is_active=True,
        )
        db.session.add(calibration)
        db.session.commit()
        db.session.refresh(profile)

        two_periods = seed_periods[:2]
        result = compute_withholding_to_date(
            seed_user["user"].id, profile, 2026, two_periods,
        )

        assert result.projected.gross == Decimal("10000.00")
        assert result.projected.federal == Decimal("1000.00")
        assert result.projected.state == Decimal("500.00")
        assert result.projected.medicare == Decimal("145.00")
        assert result.projected.social_security == Decimal("620.00")
        # No checkpoint: total == projected.
        assert result.total.federal == Decimal("1000.00")


class TestFullYearCapContext:
    """The remainder is projected with full-year cumulative context.

    Pins the case where full-context and remainder-restart projections
    DIVERGE: a high earner crossing the 2026 SS wage base mid-year.  The
    old remainder-only projection restarted cumulative wages at zero and
    would re-charge full SS (and skip the Medicare surtax) across the
    remainder; these absolute hand-computed dollars fail against that code.
    """

    def test_ss_cap_and_surtax_respected_in_remainder(self, app, db, seed_user):
        """260,000 salary, checkpoint 2026-06-30, 26 periods -> capped SS.

        Setup: annual 260,000 / 26 = 10,000.00 gross per period exactly
        (no residue); no deductions, no calibration; 2026 FICA seeds
        ss_rate 0.0620, ss_wage_base 184,500, medicare_rate 0.0145,
        surtax 0.0090 above cumulative 200,000.

        Periods P0..P25 start 2026-01-02 biweekly; P12 = 06-19,
        P13 = 07-03.  Checkpoint dated 2026-06-30 -> measured covers
        P0..P12 (13 paychecks), remainder = P13..P25 (13 periods).

        Social Security (statutory annual max = 0.0620 x 184,500
        = 11,439.00; cumulative before Pk = 10,000 x k):
          P0..P17  : cumulative <= 170,000 -> full 10,000 x 0.062 = 620.00
          P18 (paycheck 19): cumulative before = 180,000 -> remaining
                     = 11,439.00 - 0.062 x 180,000 = 11,439.00 - 11,160.00
                     = 279.00 -> min(620.00, 279.00) = 279.00
          P19..P25 : cumulative >= 190,000 -> 0.00
        Projected remainder SS = P13..P17 (5 x 620.00 = 3,100.00)
                               + P18 (279.00) + P19..P25 (0)
                               = 3,379.00.
        (Remainder-restart code: 13 x 620.00 = 8,060.00 -- cumulative
        13 x 10,000 = 130,000 never reaches 184,500.)

        Medicare (base 10,000 x 0.0145 = 145.00/period; 0.9% surtax on
        the portion of cumulative gross above 200,000):
          P19 (paycheck 20): before = 190,000, after = 200,000 -- nothing
                     ABOVE the threshold -> 145.00
          P20..P25 : cumulative before >= 200,000 -> full 10,000 surtaxed:
                     145.00 + 10,000 x 0.009 = 145.00 + 90.00 = 235.00
        Projected remainder medicare = P13..P19 (7 x 145.00 = 1,015.00)
                                     + P20..P25 (6 x 235.00 = 1,410.00)
                                     = 2,425.00.
        (Remainder-restart code: 13 x 145.00 = 1,885.00.)

        Gross: 13 x 10,000.00 = 130,000.00.

        The checkpoint's measured figures are set to the modeled elapsed
        values (13 paychecks: SS 8,060.00 = 13 x 620.00 -- all thirteen
        are pre-cap), so total SS = 8,060.00 + 3,379.00 = 11,439.00, the
        statutory annual maximum exactly -- the number the fix exists to
        get right.
        """
        profile = _seed_and_profile(
            seed_user,
            name="High Earner",
            annual_salary="260000.00",
        )
        periods = _make_full_year_periods(seed_user["user"])
        _add_checkpoint(
            profile, date(2026, 6, 30),
            ytd_gross=Decimal("130000.00"),
            ytd_federal=Decimal("26000.00"),
            ytd_state=Decimal("5500.00"),
            ytd_social_security=Decimal("8060.00"),
            ytd_medicare=Decimal("1885.00"),
        )
        db.session.commit()

        result = compute_withholding_to_date(
            seed_user["user"].id, profile, 2026, periods,
        )

        assert result.measured_through == date(2026, 6, 30)
        assert result.projected.gross == Decimal("130000.00")
        # The cap pin: 3,379.00 full-context vs 8,060.00 remainder-restart.
        assert result.projected.social_security == Decimal("3379.00")
        # The surtax pin: 2,425.00 full-context vs 1,885.00 remainder-restart.
        assert result.projected.medicare == Decimal("2425.00")
        # Measured verbatim + split identity -> the statutory annual max.
        assert result.measured.social_security == Decimal("8060.00")
        assert result.total.social_security == Decimal("11439.00")
