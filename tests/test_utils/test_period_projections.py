"""Tests for ``app.utils.period_projections`` -- the forward balance horizons.

The pure helpers behind the "3 months" / "6 months" / "1 year" chips on the
/savings tiles and the ``/accounts/<id>/details`` pages: one that turns a span
of MONTHS into a count of the owner's PAYCHECKS, and one that reads the
projected balance that many periods ahead.

The helper carried no test of its own after the balance-seam reroute deleted
``TestInvestmentHorizons`` (the only suite that exercised it -- through the
now-removed ``_investment_horizons`` wrapper), so the offset values and the
omit-vs-zero-vs-beyond-horizon contract had ZERO coverage.  This file re-pins
that user-facing display directly against the helpers.

**The offsets were hardcoded ``(("3 months", 6), ("6 months", 13),
("1 year", 26))`` until recurrence plan step R-F17** -- ``months x 26 / 12``,
true for an owner paid every fourteen days and for nobody else.  They are
derived per owner now (ledger row **F-17**, ruling **R-R31**), so the cases
below are written at the cadences that can tell a derivation from a constant,
plus the biweekly regression pin that shows the cutover moved no figure.

The helpers are pure (no Flask, no SQLAlchemy): they read only
``period.period_index`` / ``period.period_id``, a ``{period_id: balance}`` map
and a :class:`~app.services.pay_calendar.PayCadence`, so the tests build real
:class:`~app.services.pay_calendar.DerivedPeriod` values -- itself pure -- and
need no app context or database.  Every dollar assertion shows its arithmetic;
Decimals are constructed from strings per the testing standards.

Clock discipline (``.claude/rules/testing.md``): nothing here reads a clock.
"""

from decimal import Decimal
from datetime import date, timedelta

import pytest

from app.services.pay_calendar import DerivedPeriod, PayCadence
from app.utils.period_projections import (
    HORIZON_MONTHS,
    ONE_YEAR_MONTHS,
    horizon_offsets,
    project_balance_horizons,
)

#: The biweekly offsets, named once so a reader can see they are the constants
#: plan step R-F17 replaced rather than three numbers picked to make a test
#: pass.
BIWEEKLY = (("3 months", 6), ("6 months", 13), ("1 year", 26))


def _period(period_index, period_id):
    """Build the REAL period value the helper reads.

    It was a ``SimpleNamespace(period_index=..., id=...)`` until pay-calendar
    plan step C2-f2d-3 moved this helper onto the derived calendar.  A
    stand-in with the old ``id`` spelling would have kept passing here while
    both production callers broke, so the frozen production value is built
    instead; the dates are internally consistent and nothing under test reads
    them.
    """
    return DerivedPeriod(
        period_id=period_id,
        period_index=period_index,
        start_date=date(2026, 1, 2) + timedelta(days=14 * period_index),
        end_date=date(2026, 1, 15) + timedelta(days=14 * period_index),
        end_is_projected=False,
    )


class TestHorizonOffsets:
    """The months -> pay-period offsets, per owner (plan step R-F17)."""

    def test_biweekly_is_the_hardcoded_table_it_replaced(self):
        """6 / 13 / 26 under the same three labels -- nothing moved.

        The developer is paid biweekly, so this is the whole of what
        ``/savings`` and every account detail page displayed before the
        derivation landed.  Asserted as one equality against the labelled
        pairs rather than three numbers, because the ORDER is part of the
        contract: the chips render in this sequence.
        """
        assert horizon_offsets(PayCadence(cadence_days=14)) == BIWEEKLY

    @pytest.mark.parametrize("cadence_days, expected, why", [
        (
            7,
            (("3 months", 13), ("6 months", 26), ("1 year", 52)),
            "weekly: the old 26 reached six months under a '1 year' label",
        ),
        (
            30,
            (("3 months", 3), ("6 months", 6), ("1 year", 12)),
            "monthly: the old 26 reached over two years under the same label",
        ),
        (
            15,
            (("3 months", 6), ("6 months", 12), ("1 year", 24)),
            "semi-monthly: 24 paychecks a year, so a year is 24 periods",
        ),
    ])
    def test_the_label_stops_lying_at_other_cadences(
        self, cadence_days, expected, why,
    ):
        """Each cadence the replaced constant got wrong, hand-computed."""
        assert horizon_offsets(
            PayCadence(cadence_days=cadence_days),
        ) == expected, why

    def test_a_horizon_no_paycheck_reaches_is_not_offered(self):
        """An annually paid owner is offered "1 year" and nothing shorter.

        Ruling **R-R31**: the pay period is this application's finest forward
        resolution, so with no paycheck inside three months there is no column
        to value.  The alternative -- publishing the CURRENT period's own end
        balance under a "3 months" label -- is row F-17's defect one step in,
        and the alternative to THAT (a $0.00 row) is the fabricated figure this
        helper's omit contract already refuses.
        """
        assert horizon_offsets(PayCadence(cadence_days=365)) == (
            ("1 year", 1),
        )

    def test_every_legal_cadence_is_offered_at_least_the_year(self):
        """No cadence in 1..365 produces an empty offer set.

        ``paychecks_within(12)`` is ``periods_per_year``, which is at least 1
        across the whole domain -- so no owner sees a chip row vanish entirely,
        and no caller needs a "there are no horizons" branch.
        """
        for cadence_days in range(1, 366):
            offsets = horizon_offsets(PayCadence(cadence_days=cadence_days))

            assert offsets, f"cadence {cadence_days} was offered nothing"
            assert offsets[-1][0] == "1 year"
            assert offsets[-1][1] >= 1

    def test_the_offsets_increase_with_the_span(self):
        """A longer horizon never resolves nearer than a shorter one.

        Swept across the whole cadence domain: the chips are read top to
        bottom as a trajectory, so an out-of-order pair would render a
        six-month balance before a three-month one with nothing on screen
        saying so.
        """
        for cadence_days in range(1, 366):
            offsets = [
                offset for _, offset
                in horizon_offsets(PayCadence(cadence_days=cadence_days))
            ]

            assert offsets == sorted(offsets), f"cadence {cadence_days}"

    def test_one_year_months_is_the_span_the_last_horizon_names(self):
        """``ONE_YEAR_MONTHS`` IS the "1 year" entry of ``HORIZON_MONTHS``.

        The account detail page sums its "Interest, next 12 mo" chip over
        ``paychecks_within(ONE_YEAR_MONTHS)`` periods, and its "1 year" balance
        chip reads the offset this table produces for the same constant, so the
        two chips beside each other cover the same span structurally.  A
        separate ``_ONE_YEAR_PERIODS = 26`` asserted that agreement in a
        comment until plan step R-F17 (ledger row **F-17**).
        """
        assert (ONE_YEAR_MONTHS, "1 year") in HORIZON_MONTHS
        assert ONE_YEAR_MONTHS == 12


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

        result = project_balance_horizons(
            current, all_periods, balance_map, BIWEEKLY,
        )

        assert result == {
            "3 months": Decimal("1100.00"),
            "6 months": Decimal("1250.00"),
            "1 year": Decimal("1600.00"),
        }

    def test_a_weekly_owner_reads_a_different_period_for_one_label(self):
        """The SAME map, the SAME label, a different period -- by cadence.

        The point of plan step R-F17 in one case.  Current period 0; the
        weekly owner's "3 months" is 13 paychecks out and the biweekly owner's
        is 6, so with balances planted at both indices the two owners read
        $2,000.00 and $1,000.00 from one map.  Before the derivation both read
        index 6, and the weekly owner's chip called six weeks a quarter.
        """
        current = _period(0, 1)
        all_periods = [current, _period(6, 7), _period(13, 14)]
        balance_map = {7: Decimal("1000.00"), 14: Decimal("2000.00")}

        biweekly = project_balance_horizons(
            current, all_periods, balance_map,
            horizon_offsets(PayCadence(cadence_days=14)),
        )
        weekly = project_balance_horizons(
            current, all_periods, balance_map,
            horizon_offsets(PayCadence(cadence_days=7)),
        )

        assert biweekly["3 months"] == Decimal("1000.00")
        assert weekly["3 months"] == Decimal("2000.00")

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

        result = project_balance_horizons(
            current, all_periods, balance_map, BIWEEKLY,
        )

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

        result = project_balance_horizons(
            current, all_periods, balance_map, BIWEEKLY,
        )

        assert result == {"3 months": Decimal("900.00")}
        assert "6 months" not in result

    def test_no_current_period_returns_empty(self):
        """A ``None`` current period yields an empty result (no crash).

        The guard for the no-current-period state every dashboard producer
        relies on to render its empty fallback.  Its callers pass an EMPTY
        offset tuple in that state too -- the cadence cannot be read without
        a current period -- so this is asserted with both, and neither may
        raise.
        """
        all_periods = [_period(6, 7), _period(13, 14)]
        balance_map = {7: Decimal("900.00"), 14: Decimal("950.00")}

        assert project_balance_horizons(
            None, all_periods, balance_map, BIWEEKLY,
        ) == {}
        assert project_balance_horizons(
            None, all_periods, balance_map, (),
        ) == {}

    def test_empty_periods_returns_empty(self):
        """No periods to search yields an empty result."""
        current = _period(0, 1)

        assert project_balance_horizons(current, [], {}, BIWEEKLY) == {}

    def test_no_offsets_yields_no_rows_rather_than_a_default_set(self):
        """An empty offer set renders NOTHING, not the biweekly table.

        The state a caller passes when the owner has no current period, and
        the reason the parameter is a plain tuple rather than a nullable: an
        empty one is a total answer, so no consumer needs a second field to
        tell "no horizons" from "the default horizons".  A default applied
        here would silently reinstate the hardcoded 6 / 13 / 26 that plan step
        R-F17 exists to delete.
        """
        current = _period(0, 1)
        all_periods = [current, _period(6, 7), _period(13, 14), _period(26, 27)]
        balance_map = {
            7: Decimal("900.00"),
            14: Decimal("950.00"),
            27: Decimal("990.00"),
        }

        assert project_balance_horizons(
            current, all_periods, balance_map, (),
        ) == {}
