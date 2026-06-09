"""Tests for the shared per-deduction annual-cap clamp (deep-hunt #2).

``cap_period_amount`` is the single definition of "how much of a capped
deduction applies this period given the year-to-date total so far," shared
by the net-pay path (``paycheck_calculator._calculate_deductions``) and the
investment-contribution timeline (``investment_projection``).  This file pins
the clamp contract so both callers share one tested rule:

- ``None`` cap is a passthrough (the common, uncapped case).
- A present cap lands the year-to-date total exactly on the cap in the
  binding period and returns ZERO for every period after exhaustion.
- ``cumulative_before`` is the sum of the *raw* prior-period amounts; once it
  reaches or exceeds the cap the clamp returns ZERO.
"""
from decimal import Decimal

from app.utils.deduction_cap import cap_period_amount


class TestCapPeriodAmount:
    """Pin the calendar-year deduction-cap clamp shared across both paths."""

    def test_none_cap_is_passthrough(self):
        """An uncapped deduction returns its raw amount unchanged."""
        # No ceiling: even a large amount with a large prior total passes through.
        assert cap_period_amount(
            Decimal("9999.99"), Decimal("100000.00"), None
        ) == Decimal("9999.99")

    def test_cap_not_binding_returns_raw(self):
        """When the cap has room for the full amount, return it unchanged."""
        # 200 + 600 = 800 < 1000 cap -> full 600 applies.
        assert cap_period_amount(
            Decimal("600.00"), Decimal("200.00"), Decimal("1000.00")
        ) == Decimal("600.00")

    def test_cap_lands_exactly_on_boundary(self):
        """The amount that lands YTD exactly on the cap is allowed in full."""
        # 400 + 600 = 1000 == cap -> full 600 applies (boundary inclusive).
        assert cap_period_amount(
            Decimal("600.00"), Decimal("400.00"), Decimal("1000.00")
        ) == Decimal("600.00")

    def test_cap_binds_partway_clamps_to_remaining(self):
        """The binding period is clamped to the remaining room under the cap."""
        # 600 already taken, 600 proposed, cap 1000 -> only 400 fits.
        assert cap_period_amount(
            Decimal("600.00"), Decimal("600.00"), Decimal("1000.00")
        ) == Decimal("400.00")

    def test_cap_exhausted_returns_zero(self):
        """Once YTD equals the cap, no further amount applies."""
        assert cap_period_amount(
            Decimal("600.00"), Decimal("1000.00"), Decimal("1000.00")
        ) == Decimal("0")

    def test_cap_over_exhausted_returns_zero(self):
        """A YTD past the cap (never reached in practice) still floors at zero."""
        # Negative remaining must not flip into a negative deduction.
        assert cap_period_amount(
            Decimal("600.00"), Decimal("1200.00"), Decimal("1000.00")
        ) == Decimal("0")

    def test_full_year_sequence_clamps_then_zeros(self):
        """A flat $600/period deduction under a $1000 cap: 600, 400, 0, 0."""
        cap = Decimal("1000.00")
        raw = Decimal("600.00")
        cumulative = Decimal("0")
        applied = []
        for _ in range(4):
            amount = cap_period_amount(raw, cumulative, cap)
            applied.append(amount)
            # The running total sums the RAW amounts, per the helper contract.
            cumulative += raw
        assert applied == [
            Decimal("600.00"), Decimal("400.00"), Decimal("0"), Decimal("0"),
        ]
        # The capped amounts sum to exactly the cap, never over.
        assert sum(applied) == cap

    def test_normalizes_non_decimal_inputs(self):
        """Defensive Decimal(str(...)) lets callers pass DB-shaped values."""
        # int/str inputs (column-shaped) normalize without float drift.
        assert cap_period_amount("600", 600, "1000") == Decimal("400")
