"""
Shekel Budget App -- Salary Cockpit Producer Tests

Unit tests for :mod:`app.services.salary_cockpit_service`, the pure
producers behind the salary cockpit and projection summary.  The
functions take ``(period, breakdown)`` pairs and return plain Decimal
context, so these tests hand-build breakdowns and hand-compute every
expected figure (no DB, no Flask).

Scenario shared by most tests (7 periods, one merit raise, two third
paychecks) -- salary A = $52,000 for periods 0-3, salary B = $54,000
for periods 4-6:

  idx start        salary  net     kind
  0   2026-05-01   A       1400    regular
  1   2026-05-15   A       1600    third paycheck
  2   2026-06-01   A       1400    regular  (contains today 2026-06-05)
  3   2026-06-15   A       1400    regular
  4   2026-07-01   B       1450    regular  (raise event)
  5   2026-07-15   B       1450    regular
  6   2026-11-01   B       1650    third paycheck
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services import salary_cockpit_service as svc
from app.services.paycheck_calculator import (
    DeductionBreakdown,
    DeductionLine,
    Earnings,
    PaycheckBreakdown,
    PeriodInfo,
    TaxLines,
)

TODAY = date(2026, 6, 5)


@dataclass
class _FakePeriod:
    """Minimal pay-period stand-in (the producers read id/start/end only)."""
    id: int
    start_date: date
    end_date: date


def _pair(
    pid, start, end, annual, gross, net, *,
    taxable="0", is_third=False, raise_event="",
    federal="0", state="0", ss="0", medicare="0",
    pre=(), post=(),
):
    """Build a ``(period, breakdown)`` pair from plain values.

    ``pre`` / ``post`` are iterables of ``(name, amount_str)`` deduction
    lines.  Every monetary value is constructed from a string.
    """
    period = _FakePeriod(id=pid, start_date=start, end_date=end)
    breakdown = PaycheckBreakdown(
        period=PeriodInfo(pid, is_third_paycheck=is_third, raise_event=raise_event),
        earnings=Earnings(
            annual_salary=Decimal(annual),
            gross_biweekly=Decimal(gross),
            taxable_income=Decimal(taxable),
            net_pay=Decimal(net),
        ),
        taxes=TaxLines(
            federal=Decimal(federal), state=Decimal(state),
            social_security=Decimal(ss), medicare=Decimal(medicare),
        ),
        deductions=DeductionBreakdown(
            pre_tax=[DeductionLine(name=n, amount=Decimal(a)) for n, a in pre],
            post_tax=[DeductionLine(name=n, amount=Decimal(a)) for n, a in post],
        ),
    )
    return period, breakdown


def _scenario():
    """Return the shared 7-period scenario as an ordered pairs list."""
    return [
        _pair(10, date(2026, 5, 1), date(2026, 5, 14), "52000", "2000", "1400"),
        _pair(11, date(2026, 5, 15), date(2026, 5, 28), "52000", "2000", "1600",
              is_third=True),
        _pair(12, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
              taxable="1800", state="150", ss="124", medicare="26",
              pre=[("401k", "200")], post=[("Roth", "100")]),
        _pair(13, date(2026, 6, 15), date(2026, 6, 28), "52000", "2000", "1400"),
        _pair(14, date(2026, 7, 1), date(2026, 7, 14), "54000", "2076.92", "1450",
              raise_event="MERIT +2.5000%"),
        _pair(15, date(2026, 7, 15), date(2026, 7, 28), "54000", "2076.92", "1450"),
        _pair(16, date(2026, 11, 1), date(2026, 11, 14), "54000", "2076.92", "1650",
              is_third=True),
    ]


class TestCleanRaiseLabel:
    """clean_raise_label: display cleaning of calculator raise_event strings.

    Input shapes are exactly what _get_raise_event emits (verified against
    app/services/paycheck_calculator.py): "{TYPE} +{pct}%" for percentage
    raises (pct = stored Numeric(5,4) percentage * 100, e.g. "2.5000"),
    "{TYPE} +${amount:,.2f}" for flat raises, multiple events joined with
    ", ", and "RAISE" as the null-relationship type fallback.
    """

    def test_percentage_trailing_zeros_trimmed(self):
        """Fractional percentage: "MERIT +2.5000%" -> "Merit +2.5%"."""
        assert svc.clean_raise_label("MERIT +2.5000%") == "Merit +2.5%"

    def test_percentage_whole_number(self):
        """Whole percentage drops the dot: "COLA +3.0000%" -> "Cola +3%"."""
        assert svc.clean_raise_label("COLA +3.0000%") == "Cola +3%"

    def test_percentage_shorter_precision(self):
        """A 2dp source Decimal ("+3.00%") trims identically to "+3%"."""
        # A hand-built Decimal("0.03") * 100 = Decimal("3.00") emits
        # "COLA +3.00%"; the trim is precision-agnostic.
        assert svc.clean_raise_label("COLA +3.00%") == "Cola +3%"

    def test_flat_amount_kept_to_the_cent(self):
        """Flat raises keep money formatting: "+$2,000.00" is untouched."""
        assert svc.clean_raise_label("MERIT +$2,000.00") == "Merit +$2,000.00"

    def test_multiple_events_each_cleaned(self):
        """Comma-joined events clean independently; the flat-amount
        thousands comma (",0", no space) does not split the label."""
        raw = "COLA +$2,000.00, MERIT +3.0000%"
        assert svc.clean_raise_label(raw) == "Cola +$2,000.00, Merit +3%"

    def test_fallback_type_title_cased(self):
        """The null-raise_type fallback "RAISE" title-cases like any type."""
        assert svc.clean_raise_label("RAISE +10.0000%") == "Raise +10%"

    def test_empty_string_passthrough(self):
        """No raise event (empty string) stays empty."""
        assert svc.clean_raise_label("") == ""


class TestBaseRegularNet:
    """base_regular_net: nearest regular paycheck at the same salary."""

    def test_prefers_previous_regular_same_salary(self):
        """The idx-1 third paycheck resolves to idx-0's regular net (prev)."""
        pairs = _scenario()
        # idx 1 is a third paycheck at salary A; nearest regular same-salary
        # preferring previous is idx 0 => net 1400.00.
        assert svc.base_regular_net(pairs, 1) == Decimal("1400.00")

    def test_falls_back_to_next_when_no_previous(self):
        """A leading third paycheck falls forward to the next regular net."""
        pairs = [
            _pair(1, date(2026, 5, 1), date(2026, 5, 14), "52000", "2000", "1600",
                  is_third=True),
            _pair(2, date(2026, 5, 15), date(2026, 5, 28), "52000", "2000", "1400"),
        ]
        # No earlier period; next regular same salary is idx 1 => 1400.00.
        assert svc.base_regular_net(pairs, 0) == Decimal("1400.00")


class TestNextEvents:
    """next_raise_after / next_third_after strictly after today."""

    def test_next_raise_after_today(self):
        """First raise event after 2026-06-05 is the 2026-07-01 merit step.

        The raw calculator label "MERIT +2.5000%" is display-cleaned:
        "MERIT" title-cases to "Merit"; "2.5000" trims trailing zeros to
        "2.5" -> "Merit +2.5%".
        """
        result = svc.next_raise_after(_scenario(), TODAY)
        assert result == {
            "label": "Merit +2.5%",
            "period_start": date(2026, 7, 1),
        }

    def test_next_third_after_today_delta(self):
        """Next third paycheck (2026-11-01) delta = 1650 - base 1450 = 200."""
        result = svc.next_third_after(_scenario(), TODAY)
        # base regular net at salary B preferring previous is idx 5 (1450);
        # delta = 1650.00 - 1450.00 = 200.00.
        assert result == {
            "period_start": date(2026, 11, 1),
            "net": Decimal("1650.00"),
            "delta": Decimal("200.00"),
        }

    def test_next_raise_none_when_no_future_raise(self):
        """No raise after today returns None."""
        pairs = [_pair(1, date(2026, 1, 1), date(2026, 1, 14), "52000", "2000", "1400")]
        assert svc.next_raise_after(pairs, TODAY) is None

    def test_next_raise_skips_run_started_on_or_before_today(self):
        """Live-data shape: today 07/03, July COLA run 07/02-07/30 -> Jan Merit.

        The calculator badges EVERY period of a raise month, so 07/16 and
        07/30 carry the same "COLA +3.0000%" as 07/02.  That run STARTED
        07/02 (on/before today 07/03), so the raise already landed and the
        whole run is skipped -- its 07/16 / 07/30 tails must NOT surface as
        "the next raise".  The honest next raise is the Jan 2027 Merit
        run's first period.
        """
        live_today = date(2026, 7, 3)
        pairs = [
            _pair(1, date(2026, 7, 2), date(2026, 7, 15), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(2, date(2026, 7, 16), date(2026, 7, 29), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(3, date(2026, 7, 30), date(2026, 8, 12), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(4, date(2026, 8, 13), date(2026, 8, 26), "94425.25", "3631.74", "2650"),
            _pair(5, date(2027, 1, 7), date(2027, 1, 20), "96786", "3722.54", "2710",
                  raise_event="MERIT +2.5000%"),
        ]
        result = svc.next_raise_after(pairs, live_today)
        assert result == {
            "label": "Merit +2.5%",
            "period_start": date(2027, 1, 7),
        }

    def test_next_raise_future_run_returns_its_start(self):
        """A future run returns its FIRST period even with badged tails."""
        pairs = [
            _pair(1, date(2026, 8, 1), date(2026, 8, 14), "52000", "2000", "1400",
                  raise_event="COLA +3.0000%"),
            _pair(2, date(2026, 8, 15), date(2026, 8, 28), "52000", "2000", "1400",
                  raise_event="COLA +3.0000%"),
        ]
        # Both periods start after TODAY (2026-06-05); only the run START
        # (08/01) is a candidate, never the 08/15 tail.
        result = svc.next_raise_after(pairs, TODAY)
        assert result == {
            "label": "Cola +3%",
            "period_start": date(2026, 8, 1),
        }


class TestYearlyNetTotals:
    """yearly_net_totals sums net per calendar year, ordered."""

    def test_single_year_total(self):
        """All 7 nets fall in 2026: 1400+1600+1400+1400+1450+1450+1650 = 10350."""
        result = svc.yearly_net_totals(_scenario())
        assert result == [(2026, Decimal("10350.00"))]

    def test_multi_year_ordered(self):
        """Two years sum independently and sort ascending by year."""
        pairs = [
            _pair(1, date(2026, 12, 20), date(2027, 1, 2), "52000", "2000", "1400"),
            _pair(2, date(2027, 1, 3), date(2027, 1, 16), "52000", "2000", "1450"),
        ]
        # 2026: 1400.00; 2027: 1450.00 (period 1 is keyed by its 2026 start).
        assert svc.yearly_net_totals(pairs) == [
            (2026, Decimal("1400.00")),
            (2027, Decimal("1450.00")),
        ]


class TestBuildChips:
    """build_chips: focused-period hero figures + next-event chips."""

    def test_chips_from_focused_period(self):
        """Chips read gross/annual/take-home off the focused breakdown."""
        pairs = _scenario()
        focused = pairs[2][1]  # idx 2, salary A, gross 2000, net 1400
        chips = svc.build_chips(pairs, focused, TODAY)
        assert chips["gross"] == Decimal("2000")
        assert chips["annual_salary"] == Decimal("52000")
        # take-home = net/gross*100 = 1400/2000*100 = 70.
        assert chips["take_home_rate_pct"] == Decimal("70")
        # Label display-cleaned: "MERIT +2.5000%" -> "Merit +2.5%".
        assert chips["next_raise"] == {
            "label": "Merit +2.5%",
            "period_start": date(2026, 7, 1),
        }
        # third-paycheck chip carries only period_start + delta (200.00).
        assert chips["third_paycheck"] == {
            "period_start": date(2026, 11, 1),
            "delta": Decimal("200.00"),
        }


class TestBuildComposition:
    """build_composition: segment totals + percentages of gross."""

    def test_percentages_and_zero_federal_flag(self):
        """gross 2000 -> net 70%, pre-tax 10%, taxes 15%, post-tax 5%."""
        breakdown = _scenario()[2][1]  # gross 2000, net 1400, pre 200, tax 300, post 100
        comp = svc.build_composition(breakdown, calibration_active=True)
        assert comp["gross"] == Decimal("2000")
        assert comp["taxable"] == Decimal("1800")
        assert comp["net"] == Decimal("1400")
        assert comp["pre_tax_total"] == Decimal("200")
        assert comp["taxes_total"] == Decimal("300")  # 0 + 150 + 124 + 26
        assert comp["post_tax_total"] == Decimal("100")
        # of gross 2000: 1400->70.0, 200->10.0, 300->15.0, 100->5.0 (sum 100.0).
        assert comp["pct_net"] == Decimal("70.0")
        assert comp["pct_pre_tax"] == Decimal("10.0")
        assert comp["pct_taxes"] == Decimal("15.0")
        assert comp["pct_post_tax"] == Decimal("5.0")
        # federal line is 0 and calibration is active -> honest-zero flag set.
        assert comp["federal_zero_calibrated"] is True

    def test_zero_federal_flag_off_without_calibration(self):
        """Zero federal without an active calibration does NOT set the flag."""
        breakdown = _scenario()[2][1]
        comp = svc.build_composition(breakdown, calibration_active=False)
        assert comp["federal_zero_calibrated"] is False

    def test_zero_gross_percentages_are_zero(self):
        """A zero-gross period yields 0% segments (no division by zero)."""
        breakdown = _pair(1, date(2026, 1, 1), date(2026, 1, 14), "0", "0", "0")[1]
        comp = svc.build_composition(breakdown, calibration_active=False)
        assert comp["pct_net"] == Decimal("0")


class TestBuildDeductionRows:
    """build_deduction_rows: proportional bars scaled to the largest line."""

    def test_rows_scaled_to_largest(self):
        """401k 200 (largest) -> 100.0; Roth 100 -> 50.0."""
        breakdown = _scenario()[2][1]
        rows = svc.build_deduction_rows(breakdown)
        assert rows == [
            {"name": "401k", "amount": Decimal("200"), "timing": "pre_tax",
             "bar_pct": Decimal("100.0")},
            {"name": "Roth", "amount": Decimal("100"), "timing": "post_tax",
             "bar_pct": Decimal("50.0")},
        ]

    def test_empty_when_no_deductions(self):
        """A period with no deduction lines returns an empty list."""
        assert svc.build_deduction_rows(_scenario()[0][1]) == []

    def test_rows_sorted_desc_within_timing_groups(self):
        """Each timing group sorts amount-descending; pre-tax group first.

        Live-shape input order (calculator order): FSA 50, Vision 10,
        Dental 30, Health 200 (pre-tax); Roth 100, Life 150 (post-tax).
        Expected: Health 200, FSA 50, Dental 30, Vision 10, then Life 150,
        Roth 100.  bar_pct scales to the overall max 200:
          Health 200/200*100 = 100.0   FSA    50/200*100 = 25.0
          Dental  30/200*100 =  15.0   Vision 10/200*100 =  5.0
          Life   150/200*100 =  75.0   Roth  100/200*100 = 50.0
        """
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            pre=[("FSA", "50"), ("Vision", "10"), ("Dental", "30"), ("Health", "200")],
            post=[("Roth", "100"), ("Life", "150")],
        )[1]
        rows = svc.build_deduction_rows(breakdown)
        assert [(r["name"], r["amount"], r["timing"], r["bar_pct"]) for r in rows] == [
            ("Health", Decimal("200"), "pre_tax", Decimal("100.0")),
            ("FSA", Decimal("50"), "pre_tax", Decimal("25.0")),
            ("Dental", Decimal("30"), "pre_tax", Decimal("15.0")),
            ("Vision", Decimal("10"), "pre_tax", Decimal("5.0")),
            ("Life", Decimal("150"), "post_tax", Decimal("75.0")),
            ("Roth", Decimal("100"), "post_tax", Decimal("50.0")),
        ]

    def test_equal_amounts_keep_stable_calculator_order(self):
        """Ties within a group keep the calculator's original order."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            pre=[("Alpha", "25"), ("Beta", "25"), ("Gamma", "50")],
        )[1]
        rows = svc.build_deduction_rows(breakdown)
        # Gamma (50) leads; Alpha and Beta tie at 25 and keep their
        # calculator order (sorted() is stable).
        assert [r["name"] for r in rows] == ["Gamma", "Alpha", "Beta"]


class TestBuildChartSeries:
    """build_chart_series: staircase line with the third-paycheck base rule."""

    def test_line_carries_base_thirds_and_raises(self):
        """Third periods carry the base net on the line; spikes go to thirds."""
        series = svc.build_chart_series(_scenario(), TODAY)
        # Window: anchor idx 2 (contains today), lookback 6 -> start 0,
        # horizon +18mo covers all 7 periods.
        starts = [pt["start"] for pt in series["periods"]]
        assert starts == [
            date(2026, 5, 1), date(2026, 5, 15), date(2026, 6, 1),
            date(2026, 6, 15), date(2026, 7, 1), date(2026, 7, 15),
            date(2026, 11, 1),
        ]
        # idx 1 (third) carries base regular net 1400.00, NOT its 1600 spike;
        # idx 6 (third) carries base 1450.00, NOT 1650.
        assert series["periods"][1]["net"] == Decimal("1400.00")
        assert series["periods"][6]["net"] == Decimal("1450.00")
        # The actual third-paycheck nets are the point events.
        assert series["thirds"] == [
            {"start": date(2026, 5, 15), "net": Decimal("1600")},
            {"start": date(2026, 11, 1), "net": Decimal("1650")},
        ]
        # The one raise event, display-cleaned ("MERIT +2.5000%" ->
        # "Merit +2.5%").
        assert series["raises"] == [
            {"start": date(2026, 7, 1), "label": "Merit +2.5%"},
        ]
        assert series["today"] == TODAY

    def test_lookback_clamps_and_horizon_bounds(self):
        """Periods past the 18-month horizon are excluded."""
        pairs = _scenario() + [
            _pair(99, date(2028, 6, 1), date(2028, 6, 14), "54000", "2076.92", "1450"),
        ]
        series = svc.build_chart_series(pairs, TODAY)
        # 2028-06-01 is beyond 2026-06-05 + 18 months (2027-12-05) -> excluded.
        assert all(pt["start"].year < 2028 for pt in series["periods"])

    def test_raise_run_collapsed_to_one_entry(self):
        """Three consecutive periods with one COLA emit ONE raises[] entry.

        The live 07/02-07/30 shape: the calculator badges all three July
        periods with "COLA +3.0000%"; the chart must paint one label at
        the run start (07/02), not three stacked ones.
        """
        pairs = [
            _pair(1, date(2026, 7, 2), date(2026, 7, 15), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(2, date(2026, 7, 16), date(2026, 7, 29), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(3, date(2026, 7, 30), date(2026, 8, 12), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(4, date(2026, 8, 13), date(2026, 8, 26), "94425.25", "3631.74", "2650"),
        ]
        series = svc.build_chart_series(pairs, date(2026, 7, 3))
        assert series["raises"] == [
            {"start": date(2026, 7, 2), "label": "Cola +3%"},
        ]

    def test_adjacent_different_raises_are_two_runs(self):
        """A label change starts a new run: back-to-back COLA then Merit."""
        pairs = [
            _pair(1, date(2026, 7, 2), date(2026, 7, 15), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(2, date(2026, 7, 16), date(2026, 7, 29), "96786", "3722.54", "2710",
                  raise_event="MERIT +2.5000%"),
        ]
        series = svc.build_chart_series(pairs, date(2026, 7, 3))
        assert series["raises"] == [
            {"start": date(2026, 7, 2), "label": "Cola +3%"},
            {"start": date(2026, 7, 16), "label": "Merit +2.5%"},
        ]


class TestBuildSalaryPath:
    """build_salary_path: forward-only annual-salary staircase + end label."""

    def test_forward_only_points_and_end_label(self):
        """Forward window from the anchor; end label formats the last point."""
        path = svc.build_salary_path(_scenario(), TODAY)
        # lookback 0 -> starts at anchor idx 2 (contains today).
        starts = [pt["start"] for pt in path["points"]]
        assert starts == [
            date(2026, 6, 1), date(2026, 6, 15), date(2026, 7, 1),
            date(2026, 7, 15), date(2026, 11, 1),
        ]
        assert path["points"][0]["annual"] == Decimal("52000")
        assert path["points"][-1]["annual"] == Decimal("54000")
        # end label = last point: $54,000 at Nov 2026.
        assert path["end_label"] == "$54,000 Nov 2026"

    def test_empty_window_empty_label(self):
        """No pairs -> no points and an empty end label."""
        path = svc.build_salary_path([], TODAY)
        assert path == {"points": [], "end_label": ""}
