"""
Shekel Budget App -- Tests for the tax law package (plan step salary:X-at-1).

The law is data the release carries (ruling salary:R-SAL74), so what these
grade is its SHAPE: a year that is malformed cannot load.  A transcription slip
in a new year -- a bracket ladder with a gap, a filing status left out, a year
with no source -- must stop the application at import rather than price a
paycheck against it.  The figures themselves are graded against their
published sources when a year is added, not restated here: a test pinning them
would be a second copy of the law.
"""

from decimal import Decimal

import pytest

from app.enums import FilingStatusEnum, TaxTypeEnum
from app.tax_law import (
    LAW,
    FederalRules,
    FicaRules,
    StateYearLaw,
    TaxLaw,
    TaxYearLaw,
    ladder,
)

_RUNGS = (
    ("0.00", "10000.00", "0.1000"),
    ("10000.00", "40000.00", "0.1200"),
    ("40000.00", None, "0.2200"),
)


def _federal():
    """Return one filing status's made-up federal rules."""
    return FederalRules(
        standard_deduction=Decimal("15000.00"),
        child_credit_amount=Decimal("2000.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1500.00"),
        brackets=ladder(*_RUNGS),
    )


def _fica():
    """Return made-up FICA rules."""
    return FicaRules(
        ss_rate=Decimal("0.0620"),
        ss_wage_base=Decimal("150000.00"),
        medicare_rate=Decimal("0.0145"),
        medicare_surtax_rate=Decimal("0.0090"),
        medicare_surtax_threshold=Decimal("200000.00"),
    )


def _year(tax_year, *, federal=None, sources=("A made-up source",)):
    """Return a made-up tax year, every filing status present unless *federal* says otherwise."""
    return TaxYearLaw(
        tax_year=tax_year,
        sources=sources,
        federal=federal if federal is not None else {
            status: _federal() for status in FilingStatusEnum
        },
        fica=_fica(),
        states={},
    )


class TestALadderIsOneContiguousClimbFromZero:
    """``ladder`` derives each rung's order and refuses a ladder with a hole in it."""

    def test_each_rung_is_ordered_by_its_position(self):
        """sort_order is the rung's position, never a hand-written number."""
        rungs = ladder(*_RUNGS)
        assert [r.sort_order for r in rungs] == [0, 1, 2]
        assert rungs[1].min_income == Decimal("10000.00")
        assert rungs[2].max_income is None
        assert rungs[2].rate == Decimal("0.2200")

    def test_a_ladder_not_starting_at_zero_is_refused(self):
        """The bottom rung taxes income from the first dollar."""
        with pytest.raises(ValueError, match="starts at zero"):
            ladder(("100.00", "10000.00", "0.1000"), ("10000.00", None, "0.1200"))

    def test_a_gap_between_rungs_is_refused(self):
        """Income between two rungs would be taxed by neither."""
        with pytest.raises(ValueError, match="not where rung 0 ends"):
            ladder(("0.00", "10000.00", "0.1000"), ("10001.00", None, "0.1200"))

    def test_an_open_rung_below_the_top_is_refused(self):
        """Only the top rung may be open-ended."""
        with pytest.raises(ValueError, match="not where rung 0 ends"):
            ladder(("0.00", None, "0.1000"), ("10000.00", None, "0.1200"))

    def test_a_closed_top_rung_is_refused(self):
        """Income above a closed top rung would be taxed by nothing."""
        with pytest.raises(ValueError, match="open-ended"):
            ladder(("0.00", "10000.00", "0.1000"), ("10000.00", "40000.00", "0.1200"))

    def test_a_rate_above_one_is_refused(self):
        """A rate is a fraction; 12 means a typo for 0.12."""
        with pytest.raises(ValueError, match="not a fraction"):
            ladder(("0.00", "10000.00", "0.1000"), ("10000.00", None, "12"))

    def test_an_empty_ladder_is_refused(self):
        """A ladder with no rungs taxes nothing."""
        with pytest.raises(ValueError, match="starts at zero"):
            ladder()


class TestAYearIsCompleteOrDoesNotLoad:
    """A tax year covers every filing status, cites its sources, and lists states whole."""

    def test_a_complete_year_loads(self):
        """The control: every status, a source, FICA."""
        assert _year(2030).tax_year == 2030

    def test_a_year_missing_a_filing_status_is_refused(self):
        """A missing status would price that filer's federal tax at zero."""
        federal = {status: _federal() for status in FilingStatusEnum}
        del federal[FilingStatusEnum.HEAD_OF_HOUSEHOLD]
        with pytest.raises(ValueError, match="head_of_household"):
            _year(2030, federal=federal)

    def test_a_year_citing_no_source_is_refused(self):
        """Nobody could check a figure with no source (ruling R-SAL74)."""
        with pytest.raises(ValueError, match="cites no source"):
            _year(2030, sources=())

    def test_a_state_missing_a_filing_status_is_refused(self):
        """The state's deductions are per status, so every status is stated."""
        deduction = {status: Decimal("10000.00") for status in FilingStatusEnum}
        del deduction[FilingStatusEnum.MARRIED_SEPARATELY]
        with pytest.raises(ValueError, match="married_separately"):
            StateYearLaw(
                tax_type=TaxTypeEnum.FLAT,
                flat_rate=Decimal("0.0400"),
                standard_deduction=deduction,
                child_deduction_tiers={status: () for status in FilingStatusEnum},
            )

    def test_years_out_of_order_are_refused(self):
        """The law lists each year once, oldest first."""
        with pytest.raises(ValueError, match="distinct and ascending"):
            TaxLaw(years=(_year(2031), _year(2030)))

    def test_a_repeated_year_is_refused(self):
        """Two copies of one year are two answers to one question."""
        with pytest.raises(ValueError, match="distinct and ascending"):
            TaxLaw(years=(_year(2030), _year(2030)))


class TestTheShippedLaw:
    """What the app carries holds the shape the resolver relies on."""

    def test_every_year_carries_every_filing_status_fica_and_a_source(self):
        """The resolver slices federal rules by status and FICA by year alone."""
        assert LAW.years
        for year in LAW.years:
            assert set(year.federal) == set(FilingStatusEnum)
            assert isinstance(year.fica, FicaRules)
            assert year.sources

    def test_every_state_year_states_every_filing_status(self):
        """A state's standard deduction and child tiers are read per status."""
        for year in LAW.years:
            for state in year.states.values():
                assert set(state.standard_deduction) == set(FilingStatusEnum)
                assert set(state.child_deduction_tiers) == set(FilingStatusEnum)
