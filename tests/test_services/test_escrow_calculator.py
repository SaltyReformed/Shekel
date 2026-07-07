"""
Tests for the escrow calculator service.
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services.escrow_calculator import (
    build_escrow_card,
    build_escrow_display,
    calculate_monthly_escrow,
    calculate_total_payment,
    escrow_monthly_as_of,
    resolve_active_lines,
)


def _comp(name, annual, inflation=None, end_date=None, created_at=None, id=1):
    """Helper to create a mock resolved escrow component (a display/sum row).

    ``calculate_monthly_escrow`` and ``build_escrow_display`` take an already-
    resolved set; ``end_date`` is retained only to feed the "no active filter"
    tests a would-be-removed row and is ignored by both functions.
    """
    return SimpleNamespace(
        id=id,
        name=name,
        annual_amount=Decimal(str(annual)),
        inflation_rate=Decimal(str(inflation)) if inflation else None,
        end_date=end_date,
        created_at=created_at,
    )


class TestCalculateMonthlyEscrow:
    """Tests for monthly escrow calculation."""

    def test_basic_two_components(self):
        """Two components → sum of annual/12."""
        components = [
            _comp("Property Tax", "4800"),
            _comp("Insurance", "2400"),
        ]
        result = calculate_monthly_escrow(components)
        assert result == Decimal("600.00")

    def test_empty_components(self):
        """No components → $0."""
        result = calculate_monthly_escrow([])
        assert result == Decimal("0.00")

    def test_sums_all_given_no_active_filter(self):
        """calculate_monthly_escrow sums EVERY component it is handed.

        Active-state resolution is ``resolve_active_lines``' job (it drops
        removed/absent lines as of a date); this pure summation must NOT
        re-filter, so a would-be-removed component passed in IS summed.
        4800/12 + 1200/12 = 400.00 + 100.00 = 500.00.
        """
        components = [
            _comp("Property Tax", "4800"),
            _comp("Old Insurance", "1200", end_date=date(2026, 1, 1)),
        ]
        result = calculate_monthly_escrow(components)
        assert result == Decimal("500.00")

    def test_with_inflation(self):
        """Inflation applied with month-aware elapsed years (M-05)."""
        components = [
            _comp("Property Tax", "4800", inflation="0.03",
                  created_at=datetime(2024, 1, 1)),
        ]
        # 29 months elapsed (Jan 2024 to Jun 2026) = 29/12 ≈ 2.4167 years
        # 4800 * 1.03^(29/12) / 12 ≈ 429.62
        result = calculate_monthly_escrow(components, as_of_date=date(2026, 6, 1))
        assert result == Decimal("429.62")

    def test_no_inflation_without_date(self):
        """No as_of_date → no inflation applied."""
        components = [
            _comp("Property Tax", "4800", inflation="0.03",
                  created_at=datetime(2024, 1, 1)),
        ]
        result = calculate_monthly_escrow(components)
        assert result == Decimal("400.00")

    def test_zero_annual_amount(self):
        """Component with $0 annual amount produces $0 monthly escrow.

        Edge case: a component might be set to zero during a waiver period.
        Expected: Decimal("0.00").
        """
        components = [_comp("Waived Fee", "0")]
        result = calculate_monthly_escrow(components)
        assert result == Decimal("0.00")

    def test_negative_annual_amount(self):
        """Negative annual amount passes through sign-agnostically.

        Pins the service-layer contract: the calculator divides
        whatever amount it is handed, so a negative component yields a
        negative monthly figure (-1200 / 12 = -100.00).  This is NOT a
        reachable production state: the boundary rejects negative
        amounts twice -- ``EscrowComponentSchema.annual_amount``
        requires ``Range(min=0)`` (validation/loans.py) and the DB
        enforces ``ck_escrow_component_versions_nonneg_annual_amount``
        (``annual_amount >= 0``, models/escrow_line.py).
        Sign-guarding is the boundary's job; the service stays a pure
        function of its inputs.
        """
        components = [_comp("Refund", "-1200")]
        result = calculate_monthly_escrow(components)
        # -1200 / 12 = -100.00
        assert result == Decimal("-100.00")

    def test_multiple_components_sum_equals_individuals(self):
        """Total monthly escrow of N components equals the sum of each computed individually.

        Verifies the aggregation logic is additive -- no rounding drift across components.
        Expected: sum of individual monthly amounts == combined call result.
        """
        comp1 = _comp("Property Tax", "1200")
        comp2 = _comp("Insurance", "2400")
        comp3 = _comp("HOA", "600")

        individual_sum = (
            calculate_monthly_escrow([comp1])
            + calculate_monthly_escrow([comp2])
            + calculate_monthly_escrow([comp3])
        )
        combined = calculate_monthly_escrow([comp1, comp2, comp3])

        # Individual: 100 + 200 + 50 = 350
        assert calculate_monthly_escrow([comp1]) == Decimal("100.00")
        assert calculate_monthly_escrow([comp2]) == Decimal("200.00")
        assert calculate_monthly_escrow([comp3]) == Decimal("50.00")
        assert combined == Decimal("350.00")
        assert combined == individual_sum


class TestCalculateTotalPayment:
    """Tests for total payment (P&I + escrow)."""

    def test_pi_plus_escrow(self):
        """P&I + escrow = total."""
        components = [
            _comp("Property Tax", "4800"),
            _comp("Insurance", "2400"),
        ]
        result = calculate_total_payment(Decimal("1264.14"), components)
        assert result == Decimal("1864.14")

    def test_no_escrow(self):
        """No escrow → total = P&I."""
        result = calculate_total_payment(Decimal("1000.00"), [])
        assert result == Decimal("1000.00")


class TestBuildEscrowDisplay:
    """Tests for the display DTO builder (MED-04 / E-16, C31-3)."""

    def test_c31_3_escrow_per_period_server_decimal(self):
        """C31-3 -- per-component monthly is server-computed in Decimal.

        Arithmetic: 4800 / 12 = 400.00 exact; 2400 / 12 = 200.00 exact.
        Both quantised HALF_UP to two places.  No float cast.
        """
        components = [
            _comp("Property Tax", "4800", id=1),
            _comp("Insurance", "2400", inflation="0.03", id=2),
        ]
        rows = build_escrow_display(components)
        assert len(rows) == 2
        assert rows[0].id == 1
        assert rows[0].name == "Property Tax"
        assert rows[0].annual_amount == Decimal("4800.00")
        assert rows[0].monthly_amount == Decimal("400.00")
        assert rows[0].inflation_rate is None
        assert rows[0].inflation_rate_pct is None
        assert rows[1].id == 2
        assert rows[1].annual_amount == Decimal("2400.00")
        assert rows[1].monthly_amount == Decimal("200.00")
        # 0.03 * 100 = 3.00 (Decimal -- no float drift)
        assert rows[1].inflation_rate == Decimal("0.03")
        assert rows[1].inflation_rate_pct == Decimal("3.00")
        # Type assertions: every monetary/percentage field is Decimal.
        for row in rows:
            assert isinstance(row.annual_amount, Decimal)
            assert isinstance(row.monthly_amount, Decimal)

    def test_no_active_filter_rows_match_badge(self):
        """build_escrow_display builds one row per GIVEN component -- it does not
        filter by active state (the caller supplies the currently-active set,
        the same set the badge is summed over by ``calculate_monthly_escrow``).

        This keeps the rows-sum-to-badge invariant true for ANY input: even with
        a removed (``end_date``-set) component present, the rows AND the badge
        both count it, so they agree, rather than the rows omitting it while the
        badge counts it -- the #17 mismatch a divergent filter would resurface.
        4800/12 + 1200/12 = 400.00 + 100.00 = 500.00.
        """
        components = [
            _comp("Property Tax", "4800", id=1),
            _comp("Removed Insurance", "1200", end_date=date(2026, 1, 1), id=2),
        ]
        rows = build_escrow_display(components)
        badge = calculate_monthly_escrow(components)
        assert [r.id for r in rows] == [1, 2]
        assert sum(r.monthly_amount for r in rows) == badge == Decimal("500.00")

    def test_uneven_division_rounds_half_up(self):
        """1000 / 12 = 83.3333... -> HALF_UP rounds to 83.33.

        Hand calc: 1000 / 12 = 83.333... -> quantize 0.01 HALF_UP -> 83.33
        (the third decimal is a 3, so the cents digit is not bumped).
        """
        components = [_comp("Edge", "1000", id=1)]
        rows = build_escrow_display(components)
        assert rows[0].monthly_amount == Decimal("83.33")

    def test_half_up_rounding_boundary(self):
        """500 / 12 = 41.6666... -> HALF_UP rounds to 41.67.

        Hand calc: 500 / 12 = 41.6666... -> quantize 0.01 HALF_UP -> 41.67
        (the third decimal is a 6, so the cents digit is bumped from 6 to 7).
        For a single component the allocation has nothing to distribute
        differently: the row IS the total, so it equals the badge.
        """
        components = [_comp("Edge", "500", id=1)]
        rows = build_escrow_display(components)
        assert rows[0].monthly_amount == Decimal("41.67")
        assert rows[0].monthly_amount == calculate_monthly_escrow(components)


class TestEscrowDisplayCentAllocation:
    """The deep-hunt #17 fix: rows cent-allocate to the badge total.

    ``build_escrow_display`` used to round EACH row HALF_UP while
    ``calculate_monthly_escrow`` (the badge and the loan-payment money
    figure) sums full-precision monthlies and rounds ONCE -- so two
    $100/yr components rendered rows summing to 16.66 beside a 16.67
    badge on the same escrow tab.  Largest-remainder allocation makes
    the rows sum exactly to the badge while keeping every row within
    one cent of its exact ``annual / 12``; the aggregate rule itself
    (E-26 sum-then-round) is untouched.
    """

    def test_two_equal_components_sum_to_badge(self):
        """Two $100/yr components: rows sum to the 16.67 badge.

        Hand calc: exact each = 100 / 12 = 8.3333...; full-precision
        sum = 16.6666... -> badge round_money -> 16.67.  Floors are
        8.33 + 8.33 = 16.66, so ONE leftover cent goes to the largest
        remainder; remainders tie (both .00333), so input order breaks
        the tie -> rows [8.34, 8.33].  The old per-row HALF_UP gave
        [8.33, 8.33] = 16.66 != 16.67 (the registered defect).
        """
        components = [
            _comp("Property Tax", "100", id=1),
            _comp("Insurance", "100", id=2),
        ]
        rows = build_escrow_display(components)
        badge = calculate_monthly_escrow(components)
        assert badge == Decimal("16.67")
        assert [r.monthly_amount for r in rows] == [
            Decimal("8.34"), Decimal("8.33"),
        ]
        assert sum(r.monthly_amount for r in rows) == badge

    def test_three_components_distribute_two_cents(self):
        """Three $50/yr components: two leftover cents distributed.

        Hand calc: exact each = 50 / 12 = 4.1666...; full-precision sum
        = 12.50 exactly -> badge 12.50.  Floors are 4.16 x 3 = 12.48,
        leaving TWO cents for the two largest remainders; all three tie
        (.00666), so input order gives [4.17, 4.17, 4.16].
        """
        components = [
            _comp("Tax", "50", id=1),
            _comp("Insurance", "50", id=2),
            _comp("HOA", "50", id=3),
        ]
        rows = build_escrow_display(components)
        badge = calculate_monthly_escrow(components)
        assert badge == Decimal("12.50")
        assert [r.monthly_amount for r in rows] == [
            Decimal("4.17"), Decimal("4.17"), Decimal("4.16"),
        ]
        assert sum(r.monthly_amount for r in rows) == badge

    def test_largest_remainder_wins_the_cent(self):
        """The extra cent goes to the row nearest its next cent, not row 1.

        Hand calc: 119/12 = 9.91666... (remainder .00666);
        100/12 = 8.33333... (remainder .00333).  Full-precision sum =
        18.25 exactly -> badge 18.25; floors 9.91 + 8.33 = 18.24 leave
        one cent, which must go to the SECOND-listed-but-larger
        remainder when ordered [smaller, larger] -- proving allocation
        ranks by remainder, not input position.
        """
        components = [
            _comp("Insurance", "100", id=1),   # remainder .00333
            _comp("Property Tax", "119", id=2),  # remainder .00666
        ]
        rows = build_escrow_display(components)
        badge = calculate_monthly_escrow(components)
        assert badge == Decimal("18.25")
        assert [r.monthly_amount for r in rows] == [
            Decimal("8.33"), Decimal("9.92"),
        ]
        assert sum(r.monthly_amount for r in rows) == badge

    def test_every_row_within_one_cent_of_exact(self):
        """Allocated rows never drift more than a cent from annual/12.

        Sweep a mixed set; for each row |allocated - exact| < 0.01 and
        the set sums to the badge.  Pins the allocation's two
        guarantees together.
        """
        components = [
            _comp("Tax", "1000", id=1),
            _comp("Insurance", "500", id=2),
            _comp("HOA", "100", id=3),
            _comp("Flood", "85", id=4),
        ]
        rows = build_escrow_display(components)
        badge = calculate_monthly_escrow(components)
        assert sum(r.monthly_amount for r in rows) == badge
        for row in rows:
            exact = row.annual_amount / Decimal("12")
            assert abs(row.monthly_amount - exact) < Decimal("0.01")


def _ver(effective_date, annual, *, is_removed=False, inflation=None, id=0):
    """Build a mock escrow version (supersession model)."""
    return SimpleNamespace(
        id=id,
        effective_date=effective_date,
        annual_amount=Decimal(str(annual)),
        is_removed=is_removed,
        inflation_rate=Decimal(str(inflation)) if inflation is not None else None,
        created_at=None,
    )


def _line(line_id, name, versions):
    """Build a mock escrow line carrying its versions."""
    return SimpleNamespace(id=line_id, name=name, versions=versions)


class TestEscrowMonthlyAsOf:
    """The date-keyed supersession resolver + sum (the DRY heart)."""

    def test_single_version_resolves_annual_over_twelve(self):
        """One line, one version -> annual / 12 as of any date on/after it.

        1200 / 12 = 100.00 (exact).
        """
        line = _line(1, "Escrow", [_ver(date(2020, 1, 1), "1200")])
        assert escrow_monthly_as_of([line], date(2026, 6, 1)) == Decimal("100.00")

    def test_supersession_greatest_effective_le_date(self):
        """As-of resolves to the greatest effective_date <= D version.

        v1 $1,200/yr from 2020-01-01, v2 $2,400/yr from 2026-03-01.
        As of 2026-02-01 -> v1 1200/12 = 100.00; as of the boundary
        2026-03-01 -> v2 2400/12 = 200.00 (effective_date is inclusive).
        """
        line = _line(1, "Escrow", [
            _ver(date(2020, 1, 1), "1200"),
            _ver(date(2026, 3, 1), "2400"),
        ])
        assert escrow_monthly_as_of([line], date(2026, 2, 1)) == Decimal("100.00")
        assert escrow_monthly_as_of([line], date(2026, 3, 1)) == Decimal("200.00")

    def test_ordering_independent(self):
        """Version order within a line does not change the resolution.

        Same two versions listed newest-first; as of 2026-02-01 still
        resolves to v1 -> 100.00.
        """
        line = _line(1, "Escrow", [
            _ver(date(2026, 3, 1), "2400"),
            _ver(date(2020, 1, 1), "1200"),
        ])
        assert escrow_monthly_as_of([line], date(2026, 2, 1)) == Decimal("100.00")

    def test_tombstone_contributes_zero(self):
        """A line whose in-effect version is a removal tombstone contributes 0.

        v1 $1,200/yr from 2020, tombstone from 2026-01-01.  As of 2026-06-01
        the tombstone is in effect -> 0.00; as of 2025-06-01 -> v1 100.00.
        """
        line = _line(1, "Escrow", [
            _ver(date(2020, 1, 1), "1200"),
            _ver(date(2026, 1, 1), "0", is_removed=True),
        ])
        assert escrow_monthly_as_of([line], date(2026, 6, 1)) == Decimal("0.00")
        assert escrow_monthly_as_of([line], date(2025, 6, 1)) == Decimal("100.00")

    def test_no_version_on_or_before_date_contributes_zero(self):
        """A line whose earliest version starts after D contributes 0 on D.

        Only version effective 2026-03-01; as of 2026-01-01 no version is
        on/before D -> line contributes 0.00.
        """
        line = _line(1, "Escrow", [_ver(date(2026, 3, 1), "2400")])
        assert escrow_monthly_as_of([line], date(2026, 1, 1)) == Decimal("0.00")

    def test_multi_line_sum_then_round(self):
        """Two $100/yr lines sum full-precision then round once -> 16.67.

        100/12 = 8.3333...; full-precision sum 16.6666... -> round_money 16.67
        (preserves calculate_monthly_escrow's E-26 sum-then-round boundary).
        """
        lines = [
            _line(1, "Tax", [_ver(date(2020, 1, 1), "100")]),
            _line(2, "Ins", [_ver(date(2020, 1, 1), "100")]),
        ]
        assert escrow_monthly_as_of(lines, date(2026, 1, 1)) == Decimal("16.67")

    def test_empty_lines(self):
        """No lines -> $0.00."""
        assert escrow_monthly_as_of([], date(2026, 1, 1)) == Decimal("0.00")


class TestResolveActiveLines:
    """The shared resolver feeding both the display and the monthly total."""

    def test_carries_line_identity_and_version_fields(self):
        """A resolved row carries the LINE id/name and the version's amount/rate.

        id/name are the line's (the delete/edit target); annual_amount and
        inflation_rate come from the in-effect version (v2 as of 2026-06-01).
        """
        line = _line(7, "Property Tax", [
            _ver(date(2020, 1, 1), "1200"),
            _ver(date(2026, 3, 1), "2400", inflation="0.03"),
        ])
        rows = resolve_active_lines([line], date(2026, 6, 1))
        assert len(rows) == 1
        assert rows[0].id == line.id == 7
        # The row carries the LINE's display name (compared to the input line,
        # not a literal -- the name is display text, not ref-table key logic).
        assert rows[0].name == line.name
        assert rows[0].annual_amount == Decimal("2400.00")
        assert rows[0].inflation_rate == Decimal("0.03")

    def test_drops_removed_and_absent_lines_preserves_order(self):
        """Tombstoned or not-yet-effective lines drop; survivors keep input order.

        id 1 active, id 2 tombstoned as of D, id 3 not yet effective.  Only id 1
        survives.
        """
        lines = [
            _line(1, "A", [_ver(date(2020, 1, 1), "1200")]),
            _line(2, "B", [_ver(date(2026, 1, 1), "0", is_removed=True)]),
            _line(3, "C", [_ver(date(2027, 1, 1), "2400")]),
        ]
        rows = resolve_active_lines(lines, date(2026, 6, 1))
        assert [r.id for r in rows] == [1]

    def test_monthly_as_of_equals_calculate_over_resolved(self):
        """escrow_monthly_as_of == calculate_monthly_escrow(resolve_active_lines).

        Pins the delegation so the two "today's escrow" paths (LoanContext's
        field and the as-of wrapper) can never diverge.  7200/12 + 2400/12 =
        600.00 + 200.00 = 800.00.
        """
        lines = [
            _line(1, "Tax", [_ver(date(2020, 1, 1), "7200")]),
            _line(2, "Ins", [_ver(date(2020, 1, 1), "2400")]),
        ]
        as_of = date(2026, 1, 1)
        assert escrow_monthly_as_of(lines, as_of) == calculate_monthly_escrow(
            resolve_active_lines(lines, as_of),
        )
        assert escrow_monthly_as_of(lines, as_of) == Decimal("800.00")


class TestBuildEscrowCard:
    """The escrow-card display model: active summaries + version drawers."""

    def test_active_line_one_version_no_boundary(self):
        """One active line, one version, no settled boundary -> current, no scheduled.

        With ``forward_boundary=None`` (no settled payment) the only version is
        editable but NOT deletable (a line's sole version is removed via the line,
        not deleted).  1200/12 = 100.00.
        """
        line = _line(1, "Escrow", [_ver(date(2020, 1, 1), "1200", id=10)])
        cards = build_escrow_card([line], date(2026, 6, 1), None)
        assert len(cards) == 1
        card = cards[0]
        assert card.summary.name == "Escrow"
        assert card.summary.monthly_amount == Decimal("100.00")
        assert card.has_scheduled is False
        assert len(card.versions) == 1
        row = card.versions[0]
        assert row.id == 10
        assert row.status_key == "current"
        assert row.status_label == "Current"
        assert row.is_editable is True   # nothing settled -> not frozen
        assert row.is_deletable is False  # sole version

    def test_scheduled_future_version_flags_and_editability(self):
        """A future version -> has_scheduled, scheduled row editable + deletable.

        Boundary = 2026-01-15 (a settled payment's period start).  The 2026-08-01
        version is strictly after it, so it is editable AND deletable (two
        versions).  The origination version (2018-12-01 <= boundary) is frozen.
        """
        line = _line(1, "Tax", [
            _ver(date(2018, 12, 1), "7403.88", id=1),
            _ver(date(2026, 8, 1), "8003.88", id=2),
        ])
        cards = build_escrow_card([line], date(2026, 6, 1), date(2026, 1, 15))
        card = cards[0]
        assert card.has_scheduled is True
        # Ascending by effective_date: origination first, scheduled second.
        current_row, scheduled_row = card.versions
        assert current_row.status_key == "current"
        assert current_row.is_editable is False   # frozen (<= boundary)
        assert current_row.is_deletable is False
        assert scheduled_row.status_key == "scheduled"
        assert scheduled_row.status_label == "Scheduled"
        assert scheduled_row.is_editable is True
        assert scheduled_row.is_deletable is True
        # 8003.88 / 12 = 666.99 (exact).
        assert scheduled_row.monthly_amount == Decimal("666.99")

    def test_current_row_monthly_matches_summary_allocation(self):
        """The drawer's current row uses the summary's cent-allocated monthly.

        Two $100/yr lines allocate to 8.34 + 8.33 = 16.67 (badge), not 8.33 each
        (the leftover cent goes to the first by the stable largest-remainder rule).
        Each line's current version row must show the SAME monthly the summary
        shows, not a bare round(100/12)=8.33, so the drawer and summary never
        disagree.
        """
        lines = [
            _line(1, "A", [_ver(date(2020, 1, 1), "100", id=1)]),
            _line(2, "B", [_ver(date(2020, 1, 1), "100", id=2)]),
        ]
        cards = build_escrow_card(lines, date(2026, 6, 1), None)
        summary_monthlies = [c.summary.monthly_amount for c in cards]
        assert summary_monthlies == [Decimal("8.34"), Decimal("8.33")]
        assert sum(summary_monthlies) == Decimal("16.67")
        for card in cards:
            assert card.versions[0].monthly_amount == card.summary.monthly_amount

    def test_removed_line_absent_from_card(self):
        """A line whose in-effect version is a tombstone gets no card at all."""
        line = _line(1, "PMI", [
            _ver(date(2020, 1, 1), "1200", id=1),
            _ver(date(2024, 1, 1), "0", is_removed=True, id=2),
        ])
        cards = build_escrow_card([line], date(2026, 6, 1), None)
        assert len(cards) == 0

    def test_upcoming_only_line_still_shown(self):
        """A line whose only version is in the FUTURE is still shown (no vanish).

        A new line added with a future effective date has no in-effect-today
        version, so it must not silently disappear: its summary comes off the
        earliest upcoming version, monthly = 6000/12 = 500.00, has_scheduled True,
        and its sole row is 'Scheduled'.
        """
        line = _line(1, "Future Charge", [_ver(date(2026, 12, 1), "6000", id=1)])
        cards = build_escrow_card([line], date(2026, 6, 1), None)
        assert len(cards) == 1
        card = cards[0]
        assert card.summary.name == "Future Charge"
        assert card.summary.monthly_amount == Decimal("500.00")
        assert card.has_scheduled is True
        assert card.versions[0].status_key == "scheduled"

    def test_future_boundary_freezes_gap_version(self):
        """A version in (today, future boundary] is frozen: not editable, not deletable.

        An early-settled payment puts the boundary in the FUTURE (2026-03-27) while
        today is 2026-03-20.  A version effective 2026-03-25 is 'scheduled' (after
        today) yet underpins that settled split (on/before the boundary), so the
        display must offer neither edit nor delete -- the display side of the guard
        the delete routes enforce server-side.
        """
        line = _line(1, "Tax", [
            _ver(date(2020, 1, 1), "3600", id=1),
            _ver(date(2026, 3, 25), "4800", id=2),
        ])
        cards = build_escrow_card([line], date(2026, 3, 20), date(2026, 3, 27))
        gap_row = cards[0].versions[1]
        assert gap_row.effective_date == date(2026, 3, 25)
        assert gap_row.status_key == "scheduled"
        assert gap_row.is_editable is False
        assert gap_row.is_deletable is False

    def test_line_with_only_future_tombstone_absent(self):
        """A line whose only versions are removed / a future tombstone is omitted."""
        line = _line(1, "Gone", [
            _ver(date(2020, 1, 1), "0", is_removed=True, id=1),
            _ver(date(2027, 1, 1), "0", is_removed=True, id=2),
        ])
        cards = build_escrow_card([line], date(2026, 6, 1), None)
        assert len(cards) == 0

    def test_scheduled_removal_status_and_past_version(self):
        """A future tombstone reads 'Scheduled removal'; a superseded one is 'Past'.

        Line: origination (current), a superseded past version, and a future
        removal tombstone.  as-of 2026-06-01 with no boundary.
        """
        line = _line(1, "Ins", [
            _ver(date(2018, 12, 1), "1200", id=1),
            _ver(date(2020, 1, 1), "1400", id=2),
            _ver(date(2027, 1, 1), "0", is_removed=True, id=3),
        ])
        cards = build_escrow_card([line], date(2026, 6, 1), None)
        card = cards[0]
        past_row, current_row, future_row = card.versions
        assert past_row.status_key == "past"
        assert past_row.status_label == "Past"
        assert current_row.status_key == "current"
        assert future_row.status_key == "scheduled"
        assert future_row.status_label == "Scheduled removal"
        assert future_row.is_removed is True
        assert future_row.is_editable is False   # tombstones are not amount-editable
        assert future_row.is_deletable is True    # but a scheduled removal can be undone
