"""
Shekel Budget App -- Unit Tests for Calibration Service

Tests derive_effective_rates() and apply_calibration() with known
pay stub values, edge cases, and error conditions.
"""

from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.services.calibration_service import (
    DerivedRates,
    apply_calibration,
    derive_effective_rates,
)


# ── Fake Objects ─────────────────────────────────────────────────


class FakeCalibration:
    """Minimal stand-in for a CalibrationOverride with effective rates."""

    def __init__(self, federal_rate, state_rate, ss_rate, medicare_rate):
        self.effective_federal_rate = Decimal(str(federal_rate))
        self.effective_state_rate = Decimal(str(state_rate))
        self.effective_ss_rate = Decimal(str(ss_rate))
        self.effective_medicare_rate = Decimal(str(medicare_rate))


class FakeFicaConfig:
    """Minimal stand-in for a FicaConfig.

    Carries the statutory SS rate and wage-base cap used by
    capped_social_security (the helper apply_calibration delegates to for
    the SS line, CRIT-03 / F-037).  Defaults match the 2026 seed values
    (auth_service.DEFAULT_FICA[2026]).
    """

    def __init__(self, ss_rate="0.062", ss_wage_base="184500"):
        self.ss_rate = Decimal(str(ss_rate))
        self.ss_wage_base = Decimal(str(ss_wage_base))


# ── derive_effective_rates Tests ─────────────────────────────────


class TestDeriveEffectiveRates:
    """Tests for derive_effective_rates()."""

    def test_basic_rate_derivation(self):
        """Derive rates from a typical pay stub with known values.

        Hand calculation:
          gross = $2,307.69
          taxable = $2,107.69  (gross - $200 pre-tax 401k)
          federal = $153.08  -> rate = 153.08 / 2107.69 = 0.07261
          state = $94.85     -> rate = 94.85 / 2107.69 = 0.04499
          ss = $143.08       -> rate = 143.08 / 2307.69 = 0.06200
          medicare = $33.46  -> rate = 33.46 / 2307.69 = 0.01450
        """
        result = derive_effective_rates(
            actual_gross_pay=Decimal("2307.69"),
            actual_federal_tax=Decimal("153.08"),
            actual_state_tax=Decimal("94.85"),
            actual_social_security=Decimal("143.08"),
            actual_medicare=Decimal("33.46"),
            taxable_income=Decimal("2107.69"),
        )

        assert isinstance(result, DerivedRates)
        # With 10-decimal precision, rates reproduce exact pennies.
        assert result.effective_federal_rate == Decimal("0.0726292766")
        assert result.effective_state_rate == Decimal("0.0450018741")
        assert result.effective_ss_rate == Decimal("0.0620013953")
        assert result.effective_medicare_rate == Decimal("0.0144993478")

    def test_zero_federal_tax_produces_zero_rate(self):
        """A pay stub with $0 federal tax produces a 0.00000 federal rate.

        This is valid -- e.g. a state with no income tax or low enough
        income that withholding rounds to zero.
        """
        result = derive_effective_rates(
            actual_gross_pay=Decimal("2000.00"),
            actual_federal_tax=Decimal("0.00"),
            actual_state_tax=Decimal("90.00"),
            actual_social_security=Decimal("124.00"),
            actual_medicare=Decimal("29.00"),
            taxable_income=Decimal("1800.00"),
        )

        assert result.effective_federal_rate == Decimal("0.0000000000")

    def test_zero_state_tax_produces_zero_rate(self):
        """A state with no income tax produces a zero state rate."""
        result = derive_effective_rates(
            actual_gross_pay=Decimal("2000.00"),
            actual_federal_tax=Decimal("150.00"),
            actual_state_tax=Decimal("0.00"),
            actual_social_security=Decimal("124.00"),
            actual_medicare=Decimal("29.00"),
            taxable_income=Decimal("1800.00"),
        )

        assert result.effective_state_rate == Decimal("0.0000000000")

    def test_zero_gross_pay_raises_error(self):
        """Gross pay of zero is rejected -- cannot derive FICA rates."""
        with pytest.raises(ValidationError, match="greater than zero"):
            derive_effective_rates(
                actual_gross_pay=Decimal("0"),
                actual_federal_tax=Decimal("0"),
                actual_state_tax=Decimal("0"),
                actual_social_security=Decimal("0"),
                actual_medicare=Decimal("0"),
                taxable_income=Decimal("0"),
            )

    def test_negative_gross_pay_raises_error(self):
        """Negative gross pay is rejected."""
        with pytest.raises(ValidationError, match="greater than zero"):
            derive_effective_rates(
                actual_gross_pay=Decimal("-100"),
                actual_federal_tax=Decimal("0"),
                actual_state_tax=Decimal("0"),
                actual_social_security=Decimal("0"),
                actual_medicare=Decimal("0"),
                taxable_income=Decimal("100"),
            )

    def test_zero_taxable_income_raises_error(self):
        """Zero taxable income is rejected -- cannot derive income tax rates."""
        with pytest.raises(ValidationError, match="Taxable income"):
            derive_effective_rates(
                actual_gross_pay=Decimal("2000.00"),
                actual_federal_tax=Decimal("0"),
                actual_state_tax=Decimal("0"),
                actual_social_security=Decimal("124.00"),
                actual_medicare=Decimal("29.00"),
                taxable_income=Decimal("0"),
            )

    def test_negative_taxable_income_raises_error(self):
        """Negative taxable income (misconfigured deductions) is rejected."""
        with pytest.raises(ValidationError, match="Taxable income"):
            derive_effective_rates(
                actual_gross_pay=Decimal("2000.00"),
                actual_federal_tax=Decimal("0"),
                actual_state_tax=Decimal("0"),
                actual_social_security=Decimal("124.00"),
                actual_medicare=Decimal("29.00"),
                taxable_income=Decimal("-500"),
            )

    def test_string_inputs_coerced_to_decimal(self):
        """String inputs are accepted and coerced to Decimal."""
        result = derive_effective_rates(
            actual_gross_pay="2000.00",
            actual_federal_tax="100.00",
            actual_state_tax="50.00",
            actual_social_security="124.00",
            actual_medicare="29.00",
            taxable_income="1800.00",
        )

        assert result.effective_federal_rate == Decimal("0.0555555556")
        assert result.effective_state_rate == Decimal("0.0277777778")

    def test_high_income_rates(self):
        """Higher income levels produce reasonable effective rates.

        $200k salary, $7692.31/period gross, ~$1800 federal (23.4% eff).
        """
        result = derive_effective_rates(
            actual_gross_pay=Decimal("7692.31"),
            actual_federal_tax=Decimal("1800.00"),
            actual_state_tax=Decimal("300.00"),
            actual_social_security=Decimal("476.92"),
            actual_medicare=Decimal("111.54"),
            taxable_income=Decimal("6942.31"),
        )

        # Federal: 1800 / 6942.31 at 10 places
        assert result.effective_federal_rate == Decimal("0.2592796922")
        # SS: 476.92 / 7692.31 at 10 places
        assert result.effective_ss_rate == Decimal("0.0619995814")


# ── apply_calibration Tests ──────────────────────────────────────


class TestApplyCalibration:
    """Tests for apply_calibration()."""

    def test_basic_calibration_application(self):
        """Apply known rates to a gross/taxable amount.

        gross = $2,307.69, taxable = $2,107.69
        With 10-decimal rates, the round-trip reproduces exact pennies for
        federal/state/medicare.  SS goes through capped_social_security
        using the statutory IRS rate (0.062) -- well below the wage base
        on this single period, so SS = 2307.69 * 0.062 = 143.0768 -> 143.08.
        """
        cal = FakeCalibration(
            federal_rate="0.0726292766",
            state_rate="0.0450018741",
            ss_rate="0.0620013953",
            medicare_rate="0.0144993478",
        )

        result = apply_calibration(
            gross_biweekly=Decimal("2307.69"),
            taxable_biweekly=Decimal("2107.69"),
            calibration=cal,
            cumulative_wages=Decimal("0"),
            fica_config=FakeFicaConfig(),
        )

        assert result["federal"] == Decimal("153.08")
        assert result["state"] == Decimal("94.85")
        assert result["ss"] == Decimal("143.08")
        assert result["medicare"] == Decimal("33.46")

    def test_zero_rates_produce_zero_taxes_including_ss(self):
        """All-zero calibrated rates zero every tax line, SS included.

        Re-pinned (SS calibration fix, 2026-06-01; supersedes the prior
        CRIT-03 / F-037 re-pin): apply_calibration now passes the
        calibration's effective_ss_rate to capped_social_security as the
        per-period rate (symmetric with effective_medicare_rate), instead
        of forcing the statutory 6.2%.  A zero effective_ss_rate therefore
        yields zero SS -- the CORRECT result for a non-SS-covered employee
        (e.g. some government workers whose pay stub shows $0 Social
        Security).  The statutory cap ceiling still bounds the annual total
        for covered employees; it never manufactures a withholding line the
        employer did not levy.  The prior assertion (SS == 186.00) pinned
        the regression that forced statutory 6.2% on the full gross and so
        overstated SS for every cafeteria-deduction filer.
        Arithmetic: effective_ss_rate 0 * gross 3000.00 = 0.00.
        """
        cal = FakeCalibration(
            federal_rate="0",
            state_rate="0",
            ss_rate="0",
            medicare_rate="0",
        )

        result = apply_calibration(
            gross_biweekly=Decimal("3000.00"),
            taxable_biweekly=Decimal("2500.00"),
            calibration=cal,
            cumulative_wages=Decimal("0"),
            fica_config=FakeFicaConfig(),
        )

        assert result["federal"] == Decimal("0.00")
        assert result["state"] == Decimal("0.00")
        # effective_ss_rate 0 * gross 3000.00 = 0.00 (non-SS-covered employee).
        assert result["ss"] == Decimal("0.00")
        assert result["medicare"] == Decimal("0.00")

    def test_federal_and_state_use_taxable_not_gross(self):
        """Federal/state taxes use taxable income, FICA uses gross.

        With different gross and taxable values, the distinction matters.
        """
        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = apply_calibration(
            gross_biweekly=Decimal("4000.00"),
            taxable_biweekly=Decimal("3000.00"),
            calibration=cal,
            cumulative_wages=Decimal("0"),
            fica_config=FakeFicaConfig(),
        )

        # Federal: 3000 * 0.10 = 300.00 (uses taxable)
        assert result["federal"] == Decimal("300.00")
        # State: 3000 * 0.05 = 150.00 (uses taxable)
        assert result["state"] == Decimal("150.00")
        # SS: 4000 * 0.062 = 248.00 (uses gross, helper rate, under cap)
        assert result["ss"] == Decimal("248.00")
        # Medicare: 4000 * 0.0145 = 58.00 (uses gross)
        assert result["medicare"] == Decimal("58.00")

    def test_rounding_to_two_decimal_places(self):
        """All results are rounded HALF_UP to 2 decimal places."""
        cal = FakeCalibration(
            federal_rate="0.07261",
            state_rate="0.04499",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = apply_calibration(
            gross_biweekly=Decimal("1000.00"),
            taxable_biweekly=Decimal("900.00"),
            calibration=cal,
            cumulative_wages=Decimal("0"),
            fica_config=FakeFicaConfig(),
        )

        # federal: 900 * 0.07261 = 65.349 -> 65.35
        assert result["federal"] == Decimal("65.35")
        # state: 900 * 0.04499 = 40.491 -> 40.49
        assert result["state"] == Decimal("40.49")

    def test_string_inputs_accepted(self):
        """String gross/taxable values are accepted and coerced."""
        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = apply_calibration(
            gross_biweekly="2000.00",
            taxable_biweekly="1800.00",
            calibration=cal,
            cumulative_wages="0",
            fica_config=FakeFicaConfig(),
        )

        assert result["federal"] == Decimal("180.00")
        assert result["ss"] == Decimal("124.00")


class TestRoundTrip:
    """Derive-then-apply must reproduce the original pay stub amounts exactly.

    This is the most important property of the calibration system. If
    deriving rates from actual amounts and then applying those rates to
    the same gross/taxable does not reproduce the original amounts to
    the penny, the calibration is broken and every future paycheck will
    be wrong.

    IMPORTANT: Several test cases are specifically chosen because they
    produce WRONG results at 5-decimal precision but CORRECT results at
    10-decimal precision. These tests are the safety net for the
    precision fix -- if someone reverts RATE_PLACES to 0.00001, these
    tests MUST fail. Cases marked [precision-sensitive] were verified
    to produce different (incorrect) values at 5 decimal places.
    """

    def _assert_round_trip(self, gross, federal, state, ss, medicare, taxable):
        """Helper: derive rates from actuals, apply back, verify penny match.

        All four lines -- federal, state, SS, and medicare -- round-trip
        through their calibrated effective rates (SS calibration fix,
        2026-06-01): apply_calibration now passes effective_ss_rate to
        capped_social_security as the per-period rate, so SS reproduces the
        actual stub value for ANY rate, not only gross * 0.062.  The SS cap
        ceiling (statutory_rate * ss_wage_base) does not bite here because
        cumulative_wages is zero and the grosses are well below the base.
        """
        rates = derive_effective_rates(
            actual_gross_pay=gross,
            actual_federal_tax=federal,
            actual_state_tax=state,
            actual_social_security=ss,
            actual_medicare=medicare,
            taxable_income=taxable,
        )
        cal = FakeCalibration(
            federal_rate=str(rates.effective_federal_rate),
            state_rate=str(rates.effective_state_rate),
            ss_rate=str(rates.effective_ss_rate),
            medicare_rate=str(rates.effective_medicare_rate),
        )
        result = apply_calibration(
            gross_biweekly=gross,
            taxable_biweekly=taxable,
            calibration=cal,
            cumulative_wages=Decimal("0"),
            fica_config=FakeFicaConfig(),
        )

        assert result["federal"] == Decimal(str(federal)).quantize(Decimal("0.01")), (
            f"Federal round-trip failed: expected {federal}, got {result['federal']} "
            f"(rate={rates.effective_federal_rate})"
        )
        assert result["state"] == Decimal(str(state)).quantize(Decimal("0.01")), (
            f"State round-trip failed: expected {state}, got {result['state']} "
            f"(rate={rates.effective_state_rate})"
        )
        assert result["ss"] == Decimal(str(ss)).quantize(Decimal("0.01")), (
            f"SS round-trip failed: expected {ss}, got {result['ss']} "
            f"(rate={rates.effective_ss_rate})"
        )
        assert result["medicare"] == Decimal(str(medicare)).quantize(Decimal("0.01")), (
            f"Medicare round-trip failed: expected {medicare}, got {result['medicare']} "
            f"(rate={rates.effective_medicare_rate})"
        )

    def test_round_trip_three_taxes_precision_sensitive(self):
        """[precision-sensitive] Federal/state/medicare fail at 5-decimal places.

        Verified: at 5-decimal precision --
          federal $150.00 -> $149.99 (WRONG)
          state $80.01 -> $80.00 (WRONG)
          medicare $30.01 -> $30.00 (WRONG)

        SS is excluded from the precision-sensitivity claim: as of CRIT-03
        / F-037 (audit 2026-05-19) the SS line is delegated to
        capped_social_security and uses the statutory IRS rate from
        fica_config rather than the derived effective_ss_rate, so derive
        precision cannot affect it.  Arithmetic for the SS line under the
        new behaviour: 2884.62 * 0.062 = 178.84644 -> $178.85 (re-pinned
        from the prior $150.01 which assumed a derived-rate apply).

        These amounts were found by systematic sweep of realistic
        paycheck values.  Federal, state, and medicare still produce the
        wrong penny at 5-decimal precision; if RATE_PLACES is reverted
        those three assertions fail.
        """
        self._assert_round_trip(
            gross=Decimal("2884.62"),
            federal=Decimal("150.00"),
            state=Decimal("80.01"),
            # Re-pinned (CRIT-03 / F-037): 2884.62 * 0.062 = 178.84644
            # -> 178.85.  Prior value (150.01) pinned the F-037 bug where
            # apply_calibration trusted the calibrated effective_ss_rate.
            ss=Decimal("178.85"),
            medicare=Decimal("30.01"),
            taxable=Decimal("2684.62"),
        )

    def test_round_trip_federal_and_state_precision_sensitive(self):
        """[precision-sensitive] Large deduction gap -- federal/state break at 5 places.

        Verified: at 5-decimal precision --
          federal $250.00 -> $250.01 (WRONG, off by +$0.01)
          state $100.00 -> $100.01 (WRONG, off by +$0.01)
        """
        self._assert_round_trip(
            gross=Decimal("3846.15"),
            federal=Decimal("250.00"),
            state=Decimal("100.00"),
            ss=Decimal("238.46"),
            medicare=Decimal("55.77"),
            taxable=Decimal("3096.15"),
        )

    def test_round_trip_mid_salary_precision_sensitive(self):
        """[precision-sensitive] ~$67k salary -- federal and state break at 5 places.

        Verified: at 5-decimal precision --
          federal $150.00 -> $150.01 (WRONG, off by +$0.01)
          state $70.01 -> $70.00 (WRONG, off by -$0.01)
        """
        self._assert_round_trip(
            gross=Decimal("2576.92"),
            federal=Decimal("150.00"),
            state=Decimal("70.01"),
            ss=Decimal("159.77"),
            medicare=Decimal("37.37"),
            taxable=Decimal("2376.92"),
        )

    def test_round_trip_typical_paycheck(self):
        """$60k salary, $200 pre-tax 401k -- typical mid-range paycheck.

        This case happens to pass at both 5 and 10 decimal places.
        Retained as a basic correctness check.
        """
        self._assert_round_trip(
            gross=Decimal("2307.69"),
            federal=Decimal("153.08"),
            state=Decimal("94.85"),
            ss=Decimal("143.08"),
            medicare=Decimal("33.46"),
            taxable=Decimal("2107.69"),
        )

    def test_round_trip_zero_state_tax(self):
        """No-income-tax state -- state rate is zero, others must still match."""
        self._assert_round_trip(
            gross=Decimal("3461.54"),
            federal=Decimal("412.18"),
            state=Decimal("0.00"),
            ss=Decimal("214.62"),
            medicare=Decimal("50.19"),
            taxable=Decimal("3061.54"),
        )

    def test_round_trip_one_cent_taxes(self):
        """Very small tax amounts -- tests precision at the lowest end."""
        self._assert_round_trip(
            gross=Decimal("500.00"),
            federal=Decimal("0.01"),
            state=Decimal("0.01"),
            ss=Decimal("31.00"),
            medicare=Decimal("7.25"),
            taxable=Decimal("500.00"),
        )

    def test_round_trip_high_income(self):
        """[precision-sensitive] $200k salary -- state tax breaks at 5 places.

        Verified: at 5-decimal precision --
          state $300.00 -> $299.98 (WRONG, off by -$0.02)
        """
        self._assert_round_trip(
            gross=Decimal("7692.31"),
            federal=Decimal("1800.00"),
            state=Decimal("300.00"),
            ss=Decimal("476.92"),
            medicare=Decimal("111.54"),
            taxable=Decimal("6942.31"),
        )

    def test_round_trip_cafeteria_reduced_ss(self):
        """Cafeteria-reduced SS round-trips via effective_ss_rate.

        The developer's real pay stub (2026-06-01): gross $3,526.00 with
        Section 125 cafeteria pre-tax deductions, so the employer assesses
        Social Security on a reduced base and withholds $194.36 -- 5.51% of
        gross, NOT the statutory 6.2% ($218.61).  Before the SS calibration
        fix apply_calibration forced 6.2% and overstated SS by $24.25 per
        paycheck; it now uses effective_ss_rate and reproduces the actual
        $194.36 to the cent, exactly as Medicare already did.  This is the
        precise scenario the regression shipped on.
        """
        self._assert_round_trip(
            gross=Decimal("3526.00"),
            federal=Decimal("0.00"),
            state=Decimal("84.00"),
            ss=Decimal("194.36"),
            medicare=Decimal("45.45"),
            taxable=Decimal("2819.05"),
        )


# ── HIGH-03 / Q-25 / E-20: schema cross-check tolerance ─────────────


class TestDeriveRatesSchemaToleranceInvariant:
    """C19-4 supporting unit test: derive_effective_rates output, when
    multiplied back against the source base, reproduces the original
    actual_* values within a one-cent absolute tolerance.

    This is the load-bearing assumption behind the schema FICA
    cross-check (``CalibrationConfirmSchema.FICA_TOLERANCE``) and the
    route federal/state cross-check.  At 10-decimal-place rate
    precision the round-trip is penny-exact for every test in
    ``TestRoundTrip`` above; this test pins the looser "$0.01"
    invariant as a regression lock so a future reduction in
    ``RATE_PLACES`` (or a different rounding mode) below the tolerance
    floor would fail loud here instead of silently inflating the
    cross-check's false-rejection rate.

    Hand-computed: $312k high-earner pay stub (CRIT-03 worked example,
    audit 2026-05-19) -- gross $12,000, taxable $11,300 (after $700
    pre-tax 401k).  Each derive(actual_x, base) round-trip back through
    multiplication recovers actual_x to the cent.
    """

    def test_derive_round_trip_within_one_cent_tolerance(self):
        """Derived rates * base reproduce actual_* within $0.01."""
        gross = Decimal("12000.00")
        taxable = Decimal("11300.00")
        federal = Decimal("2260.00")
        state = Decimal("452.00")
        ss = Decimal("744.00")
        medicare = Decimal("174.00")

        rates = derive_effective_rates(
            actual_gross_pay=gross,
            actual_federal_tax=federal,
            actual_state_tax=state,
            actual_social_security=ss,
            actual_medicare=medicare,
            taxable_income=taxable,
        )

        one_cent = Decimal("0.01")
        # 2260.00 / 11300.00 = 0.2000000000; 0.2000 * 11300 = 2260.00
        assert abs(rates.effective_federal_rate * taxable - federal) <= one_cent
        # 452.00 / 11300.00 = 0.0400000000; 0.04 * 11300 = 452.00
        assert abs(rates.effective_state_rate * taxable - state) <= one_cent
        # 744.00 / 12000.00 = 0.0620000000; 0.062 * 12000 = 744.00
        assert abs(rates.effective_ss_rate * gross - ss) <= one_cent
        # 174.00 / 12000.00 = 0.0145000000; 0.0145 * 12000 = 174.00
        assert abs(rates.effective_medicare_rate * gross - medicare) <= one_cent

    def test_derive_round_trip_realistic_uneven_division(self):
        """Pay-stub values that don't divide evenly still round-trip within $0.01.

        $75k salary biweekly with $200 pre-tax 401k.  None of the
        derivations land on a clean fraction; the round-trip diff is
        bounded by 10dp precision * base which is sub-cent for every
        realistic biweekly gross.
        """
        gross = Decimal("2884.62")
        taxable = Decimal("2684.62")
        federal = Decimal("213.50")
        state = Decimal("100.25")
        ss = Decimal("178.85")
        medicare = Decimal("41.83")

        rates = derive_effective_rates(
            actual_gross_pay=gross,
            actual_federal_tax=federal,
            actual_state_tax=state,
            actual_social_security=ss,
            actual_medicare=medicare,
            taxable_income=taxable,
        )

        one_cent = Decimal("0.01")
        # 213.50 / 2684.62 ~= 0.0795194407; * 2684.62 = 213.500 -> within 1c
        assert abs(rates.effective_federal_rate * taxable - federal) <= one_cent
        # 100.25 / 2684.62 ~= 0.0373424008; * 2684.62 = 100.250 -> within 1c
        assert abs(rates.effective_state_rate * taxable - state) <= one_cent
        # 178.85 / 2884.62 ~= 0.0620012341; * 2884.62 = 178.850 -> within 1c
        assert abs(rates.effective_ss_rate * gross - ss) <= one_cent
        # 41.83 / 2884.62 ~= 0.0145010435; * 2884.62 = 41.830 -> within 1c
        assert abs(rates.effective_medicare_rate * gross - medicare) <= one_cent


# ── CRIT-03 / F-037: SS wage-base cap on the calibration path ─────


class TestCalibrationSSWageBaseCap:
    """Hand-computed cap tests for the calibration path (CRIT-03 / F-037).

    Each test pins a specific cap-arithmetic invariant that previously
    diverged between the bracket and calibration paths.  The bracket path
    enforces `if cumulative >= ss_wage_base: ss = 0` (tax_calculator.py);
    before this commit the calibration path had no cumulative_wages
    parameter and no ss_wage_base reference, so SS accrued past the cap
    for high earners.  With the shared capped_social_security helper the
    two paths cannot drift again.

    Worked example (03_consistency.md F-037, 2026-05-19 audit):
      annual_salary = $312,000, pay_periods_per_year = 26
      per-period gross = $12,000.00
      2026 fica_config.ss_wage_base = $184,500, ss_rate = 0.062
      Periods 1-15: cumul moves 0 -> $180,000 (15 * $12,000); each period
        SS = $12,000 * 0.062 = $744.00
      Period 16: cumul before = $180,000; cumul + gross = $192,000
        > $184,500.  ss_taxable = $184,500 - $180,000 = $4,500.00
        ss = $4,500 * 0.062 = $279.00
      Periods 17-26: cumul >= $184,500 -> SS = $0.00
      Year SS = 15 * $744.00 + $279.00 + 10 * $0.00 = $11,439.00
      (= $184,500 * 0.062 exactly, the IRS-invariant year total).
    """

    @staticmethod
    def _high_earner_calibration():
        """Calibration with effective_ss_rate = statutory IRS rate.

        Federal/state/medicare are arbitrary non-zero placeholders;
        cap-arithmetic tests assert only the SS line.
        """
        return FakeCalibration(
            federal_rate="0.20000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

    def test_calibration_ss_capped_after_base(self):
        """C18-1: SS = 0 once cumulative reaches ss_wage_base.

        Period 17 of the $312k worked example: cumul = $192,000 >
        $184,500 -> SS = $0.00 (branch 1 of capped_social_security).
        """
        result = apply_calibration(
            gross_biweekly=Decimal("12000.00"),
            taxable_biweekly=Decimal("12000.00"),
            calibration=self._high_earner_calibration(),
            cumulative_wages=Decimal("192000.00"),
            fica_config=FakeFicaConfig(),
        )
        # cumul (192000) >= ss_wage_base (184500) -> SS = 0
        assert result["ss"] == Decimal("0.00"), (
            f"SS at/over cap must be 0.00, got {result['ss']}"
        )

    def test_calibration_ss_uncapped_before_base(self):
        """C18-2: early-year period -- SS = gross * ss_rate (no cap touched).

        Period 5 of the worked example: cumul = $48,000, cumul + gross =
        $60,000 << $184,500.  SS = $12,000 * 0.062 = $744.00 (branch 3).
        """
        result = apply_calibration(
            gross_biweekly=Decimal("12000.00"),
            taxable_biweekly=Decimal("12000.00"),
            calibration=self._high_earner_calibration(),
            cumulative_wages=Decimal("48000.00"),
            fica_config=FakeFicaConfig(),
        )
        # 12000.00 * 0.062 = 744.00 (under cap)
        assert result["ss"] == Decimal("744.00"), (
            f"SS under cap must be 744.00, got {result['ss']}"
        )

    def test_calibration_year_total_matches_bracket_ss(self):
        """C18-3: 26-period year SS sum equals bracket year SS, to the cent.

        $312k worked example.  Year SS sums must match the bracket path
        and the IRS invariant year-total (ss_wage_base * ss_rate =
        $184,500 * 0.062 = $11,439.00).  Pre-fix the calibration path
        accrued SS for all 26 periods (26 * $744.00 = $19,344.00), an
        overstatement of $7,905.00 (audit reconciliation appendix).
        """
        cal = self._high_earner_calibration()
        fica = FakeFicaConfig()
        gross = Decimal("12000.00")
        taxable = Decimal("12000.00")

        cumulative = Decimal("0")
        year_ss = Decimal("0")
        for _ in range(26):
            result = apply_calibration(
                gross_biweekly=gross,
                taxable_biweekly=taxable,
                calibration=cal,
                cumulative_wages=cumulative,
                fica_config=fica,
            )
            year_ss += result["ss"]
            cumulative += gross

        # Year SS == ss_wage_base * ss_rate (the IRS-invariant total):
        # 184500.00 * 0.062 = 11439.00
        assert year_ss == Decimal("11439.00"), (
            f"Year SS must equal ss_wage_base * ss_rate = $11,439.00 "
            f"(was $19,344.00 pre-fix, +$7,905.00 overstatement); "
            f"got {year_ss}"
        )

    def test_calibration_partial_period_at_crossing(self):
        """C18-5: partial SS in the period that straddles the wage base.

        Period 16 of the worked example: cumul = $180,000, gross = $12,000;
        cumul + gross = $192,000 > $184,500 (branch 2).
          ss_taxable = $184,500 - $180,000 = $4,500.00
          ss = $4,500.00 * 0.062 = $279.00
        Pre-fix the calibration path would have charged the full
        $12,000 * 0.062 = $744.00, overstating by $465.00 in this single
        period.
        """
        result = apply_calibration(
            gross_biweekly=Decimal("12000.00"),
            taxable_biweekly=Decimal("12000.00"),
            calibration=self._high_earner_calibration(),
            cumulative_wages=Decimal("180000.00"),
            fica_config=FakeFicaConfig(),
        )
        # ss_taxable = 184500 - 180000 = 4500.00
        # ss = 4500.00 * 0.062 = 279.00
        assert result["ss"] == Decimal("279.00"), (
            f"Partial-crossing SS must be 279.00, got {result['ss']}"
        )

    def test_calibration_low_earner_unaffected(self):
        """C18-6: $60k salary -- calibration path SS matches the historical value.

        Low earner; cumulative never approaches the cap.  Per-period SS =
        $2,307.69 * 0.062 = $143.0768 -> $143.08.  All 26 periods identical
        (no cap activity).  This is the no-regression guard: the cap fix
        must not change SS for the population it never affected.
        """
        cal = self._high_earner_calibration()
        fica = FakeFicaConfig()
        gross = Decimal("2307.69")  # 60000 / 26 quantised
        taxable = gross

        cumulative = Decimal("0")
        per_period_ss = []
        for _ in range(26):
            result = apply_calibration(
                gross_biweekly=gross,
                taxable_biweekly=taxable,
                calibration=cal,
                cumulative_wages=cumulative,
                fica_config=fica,
            )
            per_period_ss.append(result["ss"])
            cumulative += gross

        # 2307.69 * 0.062 = 143.07678 -> 143.08, every period under cap.
        # Cumul max = 26 * 2307.69 = 59999.94, far below 184500.
        for i, ss in enumerate(per_period_ss):
            assert ss == Decimal("143.08"), (
                f"Period {i+1}: low-earner SS must be 143.08, got {ss}"
            )
