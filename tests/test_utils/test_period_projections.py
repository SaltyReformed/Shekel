"""Tests for ``app.utils.period_projections.project_balance_horizons``.

The pure helper that picks the projected balance at the 3 / 6 / 12-month
horizons (6 / 13 / 26 pay-period offsets) shown on the /savings tile and
the /accounts/<id> detail pages.  It carried no test of its own after the
balance-seam reroute deleted ``TestInvestmentHorizons`` (the only suite
that exercised it -- through the now-removed ``_investment_horizons``
wrapper), so the hand-computed offset values and the
omit-vs-zero-vs-beyond-horizon contract had ZERO coverage.  This file
re-pins that user-facing display directly against the helper.

The helper is pure (no Flask, no SQLAlchemy): it reads only
``period.period_index`` / ``period.id`` and a ``{period_id: balance}``
map, so the tests stub periods with ``SimpleNamespace`` and need no app
context or database.  Every dollar assertion shows its arithmetic;
Decimals are constructed from strings per the testing standards.
"""

from decimal import Decimal
from types import SimpleNamespace

from app.utils.period_projections import (
    HORIZON_OFFSETS,
    project_balance_horizons,
)


def _period(period_index, period_id):
    """Build a minimal period stub the helper can read."""
    return SimpleNamespace(period_index=period_index, id=period_id)


class TestProjectBalanceHorizons:
    """``project_balance_horizons`` horizon selection and omission rules."""

    def test_picks_all_three_horizons_at_their_offsets(self):
        """Each label resolves to the balance at current_index + 6 / 13 / 26.

        Current period is index 4 (so the offsets are NOT measured from 0 --
        a regression guard against indexing off the list position).  The
        3 / 6 / 12-month periods sit at indices 10 / 17 / 30 with id-keyed
        balances $1,100.00 / $1,250.00 / $1,600.00, so the helper must map
        each label to exactly that balance.
        """
        current = _period(4, 100)
        all_periods = [
            current,
            _period(10, 110),  # +6  -> "3 months"
            _period(17, 117),  # +13 -> "6 months"
            _period(30, 130),  # +26 -> "1 year"
        ]
        balance_map = {
            110: Decimal("1100.00"),
            117: Decimal("1250.00"),
            130: Decimal("1600.00"),
        }

        result = project_balance_horizons(current, all_periods, balance_map)

        assert result == {
            "3 months": Decimal("1100.00"),
            "6 months": Decimal("1250.00"),
            "1 year": Decimal("1600.00"),
        }

    def test_horizon_beyond_available_periods_is_omitted_not_zeroed(self):
        """A horizon with no matching period is OMITTED, never reported as $0.

        Only the +6 period exists; the +13 and +26 periods are absent from
        ``all_periods``, so the result carries the "3 months" key alone --
        not a $0.00 entry for the missing horizons (the omit-vs-zero
        contract a chart caller relies on to draw nothing rather than a
        false zero balance).
        """
        current = _period(0, 1)
        all_periods = [current, _period(6, 7)]
        balance_map = {7: Decimal("900.00")}

        result = project_balance_horizons(current, all_periods, balance_map)

        assert result == {"3 months": Decimal("900.00")}
        assert "6 months" not in result
        assert "1 year" not in result

    def test_period_present_but_balance_missing_is_omitted(self):
        """A horizon period that exists but has NO balance entry is omitted.

        The +13 period is in ``all_periods`` but absent from
        ``balance_map`` (e.g. a pre-anchor period the producer dropped), so
        its label must not appear -- the helper requires BOTH a matching
        period and a balance for it.
        """
        current = _period(0, 1)
        all_periods = [current, _period(6, 7), _period(13, 14)]
        balance_map = {7: Decimal("900.00")}  # no entry for period 14

        result = project_balance_horizons(current, all_periods, balance_map)

        assert result == {"3 months": Decimal("900.00")}
        assert "6 months" not in result

    def test_no_current_period_returns_empty(self):
        """A ``None`` current period yields an empty result (no crash).

        The guard for the no-current-period state every dashboard producer
        relies on to render its empty fallback.
        """
        all_periods = [_period(6, 7), _period(13, 14)]
        balance_map = {7: Decimal("900.00"), 14: Decimal("950.00")}

        assert project_balance_horizons(None, all_periods, balance_map) == {}

    def test_empty_periods_returns_empty(self):
        """No periods to search yields an empty result."""
        current = _period(0, 1)

        assert project_balance_horizons(current, [], {}) == {}

    def test_horizon_offsets_are_the_canonical_3_6_12_month_cadence(self):
        """The offsets are the documented 6 / 13 / 26 biweekly-period cadence.

        Pins the constant so a change to the horizon meaning (e.g. a switch
        away from 26 pay periods per year) is a deliberate, tested edit
        rather than a silent shift of every projected-balance display.
        """
        assert HORIZON_OFFSETS == (
            ("3 months", 6),
            ("6 months", 13),
            ("1 year", 26),
        )
