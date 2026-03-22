"""
Shekel Budget App -- Recurrence Engine Tests

Tests the auto-generation of transactions from templates with
recurrence rules (§4.7) and the state machine behavior (§4.8).
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern, TransactionType, Status
from app.services import recurrence_engine
from app.services.recurrence_engine import (
    _match_periods,
    _match_monthly,
    _match_monthly_first,
    _match_quarterly,
    _match_semi_annual,
    _match_annual,
)
from app.exceptions import RecurrenceConflict


# --- Fake Objects for Pure Pattern Matching Tests ----------------------------


class FakePattern:
    def __init__(self, name):
        self.name = name


class FakeRule:
    def __init__(self, pattern_name="every_period", interval_n=1,
                 offset_periods=0, day_of_month=None, month_of_year=None,
                 start_period_id=None, start_period=None, end_date=None):
        self.pattern = FakePattern(pattern_name)
        self.interval_n = interval_n
        self.offset_periods = offset_periods
        self.day_of_month = day_of_month
        self.month_of_year = month_of_year
        self.start_period_id = start_period_id
        self.start_period = start_period
        self.end_date = end_date


class FakePeriod:
    def __init__(self, id, start_date, end_date, period_index):
        self.id = id
        self.start_date = start_date
        self.end_date = end_date
        self.period_index = period_index


# --- Fixture: 26 Biweekly Periods for 2026 ----------------------------------


@pytest.fixture()
def biweekly_periods():
    """26 biweekly FakePeriod objects for 2026, starting Jan 2."""
    periods = []
    start = date(2026, 1, 2)
    for i in range(26):
        s = start + timedelta(days=14 * i)
        e = s + timedelta(days=13)
        periods.append(FakePeriod(id=i + 1, start_date=s, end_date=e,
                                  period_index=i))
    return periods


class TestRecurrenceGeneration:
    """Tests for generate_for_template()."""

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a template + recurrence rule."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="expense")
            .one()
        )

        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=rule_kwargs.get("interval_n", 1),
            offset_periods=rule_kwargs.get("offset_periods", 0),
            day_of_month=rule_kwargs.get("day_of_month"),
            month_of_year=rule_kwargs.get("month_of_year"),
            end_date=rule_kwargs.get("end_date"),
        )
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=expense_type.id,
            name="Test Recurring",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def test_every_period_generates_for_all(self, app, db, seed_user, seed_periods):
        """every_period creates a transaction in every pay period."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            assert len(created) == len(seed_periods)
            for txn in created:
                assert txn.estimated_amount == Decimal("100.00")
                assert txn.name == "Test Recurring"

            # Verify 1:1 mapping between transactions and periods.
            period_ids = {txn.pay_period_id for txn in created}
            expected_ids = {p.id for p in seed_periods}
            assert period_ids == expected_ids, (
                f"Period ID mismatch: "
                f"missing={expected_ids - period_ids}, "
                f"extra={period_ids - expected_ids}"
            )

    def test_every_n_periods_with_offset(self, app, db, seed_user, seed_periods):
        """every_n_periods with n=2, offset=1 generates every other period."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_n_periods",
                interval_n=2, offset_periods=1,
            )
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            # With 10 periods (indices 0-9), offset=1 matches indices 1,3,5,7,9 → 5.
            assert len(created) == 5
            for txn in created:
                period = db.session.get(
                    __import__("app.models.pay_period", fromlist=["PayPeriod"]).PayPeriod,
                    txn.pay_period_id,
                )
                assert (period.period_index - 1) % 2 == 0

    def test_once_pattern_generates_nothing(self, app, db, seed_user, seed_periods):
        """'once' pattern does not auto-generate -- user places it manually."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "once",
            )
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_skips_existing_entries(self, app, db, seed_user, seed_periods):
        """Does not create duplicates for periods that already have entries."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period",
            )

            # First generation.
            first_run = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first_run) == len(seed_periods)

            # Second generation -- should create nothing new.
            second_run = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_respects_is_override_flag(self, app, db, seed_user, seed_periods):
        """Overridden entries are not replaced during generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period",
            )

            # Generate entries.
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            created[0].is_override = True
            created[0].estimated_amount = Decimal("999.99")
            db.session.flush()

            # Regenerate -- the overridden entry should be preserved.
            from app.exceptions import RecurrenceConflict

            try:
                recurrence_engine.regenerate_for_template(
                    template, seed_periods, seed_user["scenario"].id,
                )
            except RecurrenceConflict as conflict:
                assert created[0].id in conflict.overridden

            # The overridden amount should still be there.
            db.session.refresh(created[0])
            assert created[0].estimated_amount == Decimal("999.99")

    def test_never_touches_done_transactions(self, app, db, seed_user, seed_periods):
        """Done/received/credit transactions are immutable to the engine."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period",
            )

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Mark the first one as done.
            done_status = db.session.query(Status).filter_by(name="done").one()
            created[0].status_id = done_status.id
            created[0].actual_amount = Decimal("95.00")
            db.session.flush()

            # Regenerate -- should not delete the done transaction.
            recurrence_engine.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transaction should still exist unchanged.
            db.session.refresh(created[0])
            assert created[0].actual_amount == Decimal("95.00")


# --- Pure Pattern Matching Tests ---------------------------------------------


class TestMatchMonthly:
    """Tests for _match_monthly() -- pure function, no DB."""

    def test_monthly_day_15(self, biweekly_periods):
        """Finds the period containing the 15th of each month."""
        matched = _match_monthly(biweekly_periods, day_of_month=15)

        # 26 biweekly periods span Jan-Dec 2026 → one match per month = 12.
        assert len(matched) == 12

        # Each matched period's range must contain the 15th of some month.
        for period in matched:
            # Check start or end month's 15th falls in range.
            found = False
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    found = True
                    break
            assert found, (
                f"Period {period.period_index} ({period.start_date}-"
                f"{period.end_date}) doesn't contain a 15th"
            )

    def test_monthly_day_31_clamped_in_february(self, biweekly_periods):
        """day_of_month=31 clamps to 28 in Feb 2026 (non-leap year)."""
        matched = _match_monthly(biweekly_periods, day_of_month=31)

        # Find the period matched for February.
        feb_periods = [
            p for p in matched
            if any(
                dt.month == 2 and dt.year == 2026
                for dt in (p.start_date, p.end_date)
            )
            and p.start_date <= date(2026, 2, 28) <= p.end_date
        ]
        assert len(feb_periods) == 1
        feb_period = feb_periods[0]
        # Feb 28 must be within the matched period's range.
        assert feb_period.start_date <= date(2026, 2, 28) <= feb_period.end_date

    def test_monthly_day_30_clamped_in_february(self, biweekly_periods):
        """day_of_month=30 also clamps to 28 in Feb 2026."""
        matched = _match_monthly(biweekly_periods, day_of_month=30)

        feb_periods = [
            p for p in matched
            if any(
                dt.month == 2 and dt.year == 2026
                for dt in (p.start_date, p.end_date)
            )
            and p.start_date <= date(2026, 2, 28) <= p.end_date
        ]
        assert len(feb_periods) == 1
        assert feb_periods[0].start_date <= date(2026, 2, 28) <= feb_periods[0].end_date


class TestMatchMonthlyFirst:
    """Tests for _match_monthly_first() -- pure function, no DB."""

    def test_picks_first_period_starting_in_each_month(self, biweekly_periods):
        """One period per calendar month, the earliest starting in that month."""
        matched = _match_monthly_first(biweekly_periods)

        # 26 biweekly periods starting Jan 2 cover all 12 months of 2026.
        assert len(matched) == 12

        # Each matched period should be the first whose start_date falls in
        # its calendar month.
        seen_months = set()
        for period in matched:
            ym = (period.start_date.year, period.start_date.month)
            assert ym not in seen_months, f"Duplicate month {ym}"
            seen_months.add(ym)

            # Verify it's actually the earliest period starting in that month.
            earlier = [
                p for p in biweekly_periods
                if (p.start_date.year, p.start_date.month) == ym
                and p.period_index < period.period_index
            ]
            assert len(earlier) == 0, (
                f"Period {period.period_index} is not the first in month {ym}"
            )


class TestMatchQuarterly:
    """Tests for _match_quarterly() -- pure function, no DB."""

    def test_quarterly_jan_start(self, biweekly_periods):
        """start_month=1 targets Jan, Apr, Jul, Oct."""
        matched = _match_quarterly(biweekly_periods, start_month=1,
                                   day_of_month=15)

        # 26 biweekly periods cover Jan-Dec 2026 → 4 quarterly months.
        assert len(matched) == 4

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {1, 4, 7, 10}

    def test_quarterly_nov_start_wraps(self, biweekly_periods):
        """start_month=11 wraps: targets Nov, Feb, May, Aug."""
        matched = _match_quarterly(biweekly_periods, start_month=11,
                                   day_of_month=15)

        assert len(matched) == 4

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {2, 5, 8, 11}


class TestMatchSemiAnnual:
    """Tests for _match_semi_annual() -- pure function, no DB."""

    def test_semi_annual_jan_start(self, biweekly_periods):
        """start_month=1 targets Jan and Jul."""
        matched = _match_semi_annual(biweekly_periods, start_month=1,
                                     day_of_month=15)

        assert len(matched) == 2

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {1, 7}

    def test_semi_annual_aug_start_wraps(self, biweekly_periods):
        """start_month=8 wraps: targets Aug and Feb."""
        matched = _match_semi_annual(biweekly_periods, start_month=8,
                                     day_of_month=15)

        assert len(matched) == 2

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {2, 8}


class TestMatchAnnual:
    """Tests for _match_annual() -- pure function, no DB."""

    def test_annual_one_per_year(self, biweekly_periods):
        """One match per calendar year on a specific month/day."""
        matched = _match_annual(biweekly_periods, month=3, day=15)

        # All periods are in 2026, so exactly one match.
        assert len(matched) == 1

        period = matched[0]
        assert period.start_date <= date(2026, 3, 15) <= period.end_date

    def test_annual_feb29_non_leap_year(self, biweekly_periods):
        """Feb 29 target in 2026 (non-leap) clamps to Feb 28."""
        matched = _match_annual(biweekly_periods, month=2, day=29)

        assert len(matched) == 1

        period = matched[0]
        # Clamped to Feb 28 since 2026 is not a leap year.
        assert period.start_date <= date(2026, 2, 28) <= period.end_date


class TestMatchPeriodsEdgeCases:
    """Edge case tests for _match_periods() -- pure function, no DB."""

    def test_effective_from_filters_earlier_periods(self, biweekly_periods):
        """Only periods on/after effective_from are candidates."""
        rule = FakeRule(pattern_name="every_period")
        # Use the 4th period's start_date as effective_from.
        effective_from = biweekly_periods[3].start_date

        matched = _match_periods(rule, "every_period", biweekly_periods,
                                 effective_from)

        assert len(matched) == 26 - 3  # Periods 3-25.
        for period in matched:
            assert period.start_date >= effective_from

    def test_unknown_pattern_returns_empty(self, biweekly_periods):
        """Unrecognized pattern name returns an empty list."""
        rule = FakeRule(pattern_name="bogus_pattern")
        effective_from = biweekly_periods[0].start_date

        matched = _match_periods(rule, "bogus_pattern", biweekly_periods,
                                 effective_from)

        assert matched == []


class TestMatchPeriodsFull:
    """Integration tests for _match_periods() dispatch -- pure, no DB."""

    def test_every_period_returns_all_candidates(self, biweekly_periods):
        """every_period returns all periods after effective_from filtering."""
        rule = FakeRule(pattern_name="every_period")
        effective_from = biweekly_periods[0].start_date

        matched = _match_periods(rule, "every_period", biweekly_periods,
                                 effective_from)

        assert len(matched) == 26

    def test_no_periods_empty_result(self):
        """Empty periods list produces an empty result."""
        rule = FakeRule(pattern_name="every_period")

        matched = _match_periods(rule, "every_period", [],
                                 date(2026, 1, 1))

        assert matched == []


class TestMatchPeriodsEdgeCaseSafety:
    """Safety guard tests for invalid inputs to _match_periods.

    These tests verify the engine's behavior when given values that
    are prevented at the DB level by CHECK constraints but could
    reach the service via FakeRule objects, direct function calls,
    or a bypassed validation layer.
    """

    # -- interval_n edge cases (every_n_periods) --

    def test_every_n_periods_interval_zero_defaults_to_one(
        self, biweekly_periods
    ):
        """interval_n=0 is falsy: 'rule.interval_n or 1' = 1.

        Expected: matches every period (same as interval_n=1).
        DB constraint ck_recurrence_rules_positive_interval
        prevents interval_n <= 0 from being stored.
        This test verifies the service-level fallback behavior.
        """
        # NOTE: If this test hangs, interval_n=0 may cause
        # ZeroDivisionError or infinite loop. The 'or 1' fallback
        # should prevent this.
        rule = FakeRule(
            pattern_name="every_n_periods",
            interval_n=0,
            offset_periods=0,
        )
        matched = _match_periods(
            rule, "every_n_periods", biweekly_periods,
            biweekly_periods[0].start_date,
        )
        # 0 or 1 = 1 in Python (0 is falsy), so n=1 and every
        # period matches.
        # Prevented in production by
        # ck_recurrence_rules_positive_interval.
        assert len(matched) == len(biweekly_periods), (
            f"Expected {len(biweekly_periods)} periods, "
            f"got {len(matched)}. interval_n=0 should default "
            f"to 1 via 'or 1' fallback."
        )

    def test_every_n_periods_interval_none_defaults_to_one(
        self, biweekly_periods
    ):
        """interval_n=None (DB NULL): 'None or 1' = 1.

        Expected: matches every period (same as interval_n=1).
        Different failure mode than interval_n=0 -- None means
        the DB column default (1) was not applied.
        """
        rule = FakeRule(
            pattern_name="every_n_periods",
            interval_n=None,
            offset_periods=0,
        )
        matched = _match_periods(
            rule, "every_n_periods", biweekly_periods,
            biweekly_periods[0].start_date,
        )
        # None or 1 = 1 in Python, so every period matches.
        assert len(matched) == len(biweekly_periods), (
            f"Expected {len(biweekly_periods)} periods, "
            f"got {len(matched)}. interval_n=None should "
            f"default to 1 via 'or 1' fallback."
        )

    # -- day_of_month edge cases (monthly) --

    def test_day_of_month_zero_via_match_periods(
        self, biweekly_periods
    ):
        """day_of_month=0 via _match_periods: '0 or 1' = 1.

        Expected: behaves identically to day_of_month=1.
        DB constraint ck_recurrence_rules_dom prevents
        day_of_month < 1 from being stored.
        """
        rule_zero = FakeRule(
            pattern_name="monthly", day_of_month=0,
        )
        rule_one = FakeRule(
            pattern_name="monthly", day_of_month=1,
        )
        effective = biweekly_periods[0].start_date

        matched_zero = _match_periods(
            rule_zero, "monthly", biweekly_periods, effective,
        )
        matched_one = _match_periods(
            rule_one, "monthly", biweekly_periods, effective,
        )
        # 0 or 1 = 1 in Python (0 is falsy).
        # Prevented in production by ck_recurrence_rules_dom.
        assert len(matched_zero) == len(matched_one), (
            f"day_of_month=0 matched {len(matched_zero)} "
            f"periods, day_of_month=1 matched "
            f"{len(matched_one)}"
        )
        assert (
            [p.id for p in matched_zero]
            == [p.id for p in matched_one]
        ), (
            "day_of_month=0 should produce identical matches "
            "to day_of_month=1 via 'or 1' fallback"
        )

    def test_day_of_month_zero_direct_raises(
        self, biweekly_periods
    ):
        """_match_monthly(periods, 0) bypasses 'or 1' fallback.

        Expected: raises ValueError from date(year, month, 0).
        Two layers of defense: DB constraint
        ck_recurrence_rules_dom prevents storage, _match_periods
        'or 1' prevents the crash. Direct call bypasses both.
        """
        # Prevented in production by ck_recurrence_rules_dom.
        # _match_periods applies 'or 1' for falsy values.
        # Direct call bypasses both -- date(y, m, 0) raises.
        with pytest.raises(ValueError):
            _match_monthly(biweekly_periods, day_of_month=0)

    def test_day_of_month_32_clamped_to_last_day(
        self, biweekly_periods
    ):
        """day_of_month=32 clamped by min(32, last_day).

        Expected: identical to day_of_month=31 since both clamp
        to the last day of each month.
        DB constraint ck_recurrence_rules_dom prevents
        day_of_month > 31 from being stored.
        """
        matched_32 = _match_monthly(
            biweekly_periods, day_of_month=32,
        )
        matched_31 = _match_monthly(
            biweekly_periods, day_of_month=31,
        )
        # min(32, last_day) == min(31, last_day) for all months
        # since max(last_day) is 31.
        # Prevented in production by ck_recurrence_rules_dom.
        assert (
            [p.id for p in matched_32]
            == [p.id for p in matched_31]
        ), (
            "day_of_month=32 should clamp identically to 31 "
            "via min(day_of_month, last_day)"
        )

    def test_day_of_month_none_in_monthly_defaults_to_one(
        self, biweekly_periods
    ):
        """day_of_month=None (DB NULL): 'None or 1' = 1.

        Expected: matches identically to day_of_month=1.
        DB column allows NULL (optional for non-monthly
        patterns), so None is a valid state the fallback handles.
        """
        rule_none = FakeRule(
            pattern_name="monthly", day_of_month=None,
        )
        rule_one = FakeRule(
            pattern_name="monthly", day_of_month=1,
        )
        effective = biweekly_periods[0].start_date

        matched_none = _match_periods(
            rule_none, "monthly", biweekly_periods, effective,
        )
        matched_one = _match_periods(
            rule_one, "monthly", biweekly_periods, effective,
        )
        # None or 1 = 1 in Python.
        assert (
            [p.id for p in matched_none]
            == [p.id for p in matched_one]
        ), (
            "day_of_month=None should produce identical matches "
            "to day_of_month=1 via 'or 1' fallback"
        )

    # -- month_of_year edge cases (quarterly, annual) --

    def test_month_of_year_zero_defaults_to_one(
        self, biweekly_periods
    ):
        """month_of_year=0 via _match_periods: '0 or 1' = 1.

        Expected via _match_periods: targets Jan/Apr/Jul/Oct.
        Expected via direct _match_quarterly: targets
        Dec/Mar/Jun/Sep (different due to modular arithmetic).
        DB constraint ck_recurrence_rules_moy prevents
        month_of_year < 1 from being stored.
        """
        effective = biweekly_periods[0].start_date

        # Path (a): via _match_periods -- 0 or 1 = 1.
        # Targets {1, 4, 7, 10} (Jan/Apr/Jul/Oct).
        # Prevented in production by ck_recurrence_rules_moy.
        rule_zero = FakeRule(
            pattern_name="quarterly",
            month_of_year=0,
            day_of_month=15,
        )
        rule_one = FakeRule(
            pattern_name="quarterly",
            month_of_year=1,
            day_of_month=15,
        )
        matched_zero = _match_periods(
            rule_zero, "quarterly",
            biweekly_periods, effective,
        )
        matched_one = _match_periods(
            rule_one, "quarterly",
            biweekly_periods, effective,
        )
        # 0 or 1 = 1 in Python (0 is falsy).
        assert (
            [p.id for p in matched_zero]
            == [p.id for p in matched_one]
        ), (
            "month_of_year=0 via _match_periods should behave "
            "identically to month_of_year=1"
        )

        # Path (b): direct _match_quarterly(start_month=0).
        # No 'or 1' fallback applies.
        # Formula: ((0-1 + i*3) % 12) + 1 for i=0..3
        #   i=0: ((-1)%12)+1=12  i=1: (2%12)+1=3
        #   i=2: (5%12)+1=6     i=3: (8%12)+1=9
        # Target months = {12, 3, 6, 9} != {1, 4, 7, 10}.
        direct_zero = _match_quarterly(
            biweekly_periods, start_month=0,
            day_of_month=15,
        )
        assert len(direct_zero) == 4, (
            f"Expected 4 quarterly matches for "
            f"start_month=0, got {len(direct_zero)}"
        )
        # Verify which months the direct call targets.
        direct_months = set()
        for period in direct_zero:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    direct_months.add(dt.month)
        assert direct_months == {3, 6, 9, 12}, (
            f"start_month=0 direct should target "
            f"{{3, 6, 9, 12}}, got {direct_months}. "
            f"Discrepancy: _match_periods converts 0->1 "
            f"but direct call uses modular arithmetic."
        )

    def test_month_of_year_13_annual_raises(
        self, biweekly_periods
    ):
        """month_of_year=13 is truthy: 'or 1' does NOT apply.

        Expected: ValueError from calendar.monthrange(year, 13).
        The crash propagates through _match_periods since there
        is no try/except wrapper. Note: quarterly and semi_annual
        safely wrap month=13 via modular arithmetic to
        {1,4,7,10}.
        DB constraint ck_recurrence_rules_moy prevents
        month_of_year > 12 from being stored.
        """
        # Direct call -- crashes on monthrange(year, 13).
        # Prevented in production by ck_recurrence_rules_moy.
        with pytest.raises(ValueError):
            _match_annual(
                biweekly_periods, month=13, day=15,
            )

        # Via _match_periods -- 13 or 1 = 13 (truthy).
        # No fallback; passes 13 to _match_annual.
        rule = FakeRule(
            pattern_name="annual",
            month_of_year=13,
            day_of_month=15,
        )
        with pytest.raises(ValueError):
            _match_periods(
                rule, "annual", biweekly_periods,
                biweekly_periods[0].start_date,
            )


# --- DB Integration Tests ----------------------------------------------------


class TestGenerateForTemplate:
    """DB integration tests for generate_for_template()."""

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a template + recurrence rule."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="expense")
            .one()
        )

        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=rule_kwargs.get("interval_n", 1),
            offset_periods=rule_kwargs.get("offset_periods", 0),
            day_of_month=rule_kwargs.get("day_of_month"),
            month_of_year=rule_kwargs.get("month_of_year"),
            end_date=rule_kwargs.get("end_date"),
        )
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=expense_type.id,
            name="Test Recurring",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()

        db.session.refresh(template)
        return template

    def test_effective_from_skips_earlier_periods(
        self, app, db, seed_user, seed_periods
    ):
        """effective_from = 4th period's start → only generates from period 4."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )
            effective_from = seed_periods[3].start_date
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
                effective_from=effective_from,
            )

            # 10 periods total, skip first 3 → 7 created.
            assert len(created) == 7
            for txn in created:
                period = db.session.get(
                    __import__("app.models.pay_period", fromlist=["PayPeriod"]).PayPeriod,
                    txn.pay_period_id,
                )
                assert period.start_date >= effective_from

    def test_skips_deleted_entries(self, app, db, seed_user, seed_periods):
        """Soft-deleted entries are not duplicated on re-generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            # First generation.
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == 10

            # Soft-delete one entry.
            created[2].is_deleted = True
            db.session.flush()

            # Second generation -- should not duplicate the deleted entry.
            second_run = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_monthly_pattern_generates_correct_count(
        self, app, db, seed_user, seed_periods
    ):
        """Monthly pattern across 10 periods produces one per unique month."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "monthly", day_of_month=15,
            )
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            # 10 biweekly periods starting Jan 2 span ~5 months.
            # Determine expected unique months from periods.
            unique_months = set()
            for p in seed_periods:
                for dt in (p.start_date, p.end_date):
                    target = date(dt.year, dt.month, 15)
                    if p.start_date <= target <= p.end_date:
                        unique_months.add((dt.year, dt.month))
            assert len(created) == len(unique_months)


class TestRegenerateForTemplate:
    """DB integration tests for regenerate_for_template()."""

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a template + recurrence rule."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="expense")
            .one()
        )

        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=rule_kwargs.get("interval_n", 1),
            offset_periods=rule_kwargs.get("offset_periods", 0),
            day_of_month=rule_kwargs.get("day_of_month"),
            month_of_year=rule_kwargs.get("month_of_year"),
            end_date=rule_kwargs.get("end_date"),
        )
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=expense_type.id,
            name="Test Recurring",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()

        db.session.refresh(template)
        return template

    def test_regenerate_deletes_unmodified_and_recreates(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with changed amount → old entries deleted, new created."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            # Generate initial entries.
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            old_ids = [txn.id for txn in created]
            assert len(old_ids) == 10

            # Change the template amount.
            template.default_amount = Decimal("200.00")
            db.session.flush()

            # Regenerate -- should delete old and create new.
            new_created = recurrence_engine.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            assert len(new_created) == 10
            for txn in new_created:
                assert txn.estimated_amount == Decimal("200.00")
                assert txn.id not in old_ids

    def test_regenerate_raises_conflict_for_deleted_entries(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with soft-deleted entry raises RecurrenceConflict."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Soft-delete one entry.
            deleted_id = created[0].id
            created[0].is_deleted = True
            db.session.flush()

            # Regenerate -- should raise with deleted list.
            with pytest.raises(RecurrenceConflict) as exc_info:
                recurrence_engine.regenerate_for_template(
                    template, seed_periods, seed_user["scenario"].id,
                )

            assert deleted_id in exc_info.value.deleted


class TestResolveConflicts:
    """DB integration tests for resolve_conflicts()."""

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a template + recurrence rule."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="expense")
            .one()
        )

        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=rule_kwargs.get("interval_n", 1),
            offset_periods=rule_kwargs.get("offset_periods", 0),
            day_of_month=rule_kwargs.get("day_of_month"),
            month_of_year=rule_kwargs.get("month_of_year"),
            end_date=rule_kwargs.get("end_date"),
        )
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=expense_type.id,
            name="Test Recurring",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()

        db.session.refresh(template)
        return template

    def test_resolve_keep_no_changes(self, app, db, seed_user, seed_periods):
        """action='keep' leaves overridden transaction unchanged."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            txn = created[0]
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            # Resolve as 'keep'.
            recurrence_engine.resolve_conflicts([txn.id], action="keep")
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is True
            assert txn.estimated_amount == Decimal("999.99")

    def test_resolve_update_clears_flags_and_applies_amount(
        self, app, db, seed_user, seed_periods
    ):
        """action='update' clears flags and applies new_amount."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            txn = created[0]
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            # Resolve as 'update' with new amount.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="update", new_amount=Decimal("200.00")
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            assert txn.is_deleted is False
            assert txn.estimated_amount == Decimal("200.00")

    def test_resolve_update_none_amount_clears_flags_only(
        self, app, db, seed_user, seed_periods
    ):
        """action='update' with new_amount=None clears flags but keeps amount."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry with a custom amount.
            txn = created[0]
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            # Resolve as 'update' with no new amount.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="update", new_amount=None
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            assert txn.is_deleted is False
            # Amount unchanged since new_amount was None.
            assert txn.estimated_amount == Decimal("999.99")


class TestCrossUserIsolation:
    """IDOR tests for the recurrence engine."""

    def _make_template_with_rule(
        self, seed_user, pattern_name, **rule_kwargs
    ):
        """Helper: create a template + recurrence rule."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="expense")
            .one()
        )

        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=rule_kwargs.get("interval_n", 1),
            offset_periods=rule_kwargs.get("offset_periods", 0),
            day_of_month=rule_kwargs.get("day_of_month"),
            month_of_year=rule_kwargs.get("month_of_year"),
            end_date=rule_kwargs.get("end_date"),
        )
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=expense_type.id,
            name="Test Recurring",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()

        db.session.refresh(template)
        return template

    def test_cross_user_isolation(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """Template owned by user A must not generate into B's scenario.

        generate_for_template validates that the template's user_id
        matches the scenario's user_id. If they differ, zero
        transactions are created (defense-in-depth against IDOR).
        """
        with app.app_context():
            # Template belongs to seed_user (user A).
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            # SECURITY: Attempt to generate into user B's
            # scenario using user A's template. This should
            # be rejected but currently is not.
            created = recurrence_engine.generate_for_template(
                template,
                seed_periods,
                second_user["scenario"].id,
            )

            # Correct behavior: no transactions should be
            # created across user boundaries.
            assert len(created) == 0, (
                f"IDOR: Template (user_id="
                f"{seed_user['user'].id}) generated "
                f"{len(created)} transactions into scenario "
                f"(user_id={second_user['user'].id}). "
                f"generate_for_template needs an ownership "
                f"check."
            )


# --- Negative-Path Tests ---------------------------------------------------


class TestNegativePaths:
    """Negative-path and boundary-condition tests for the recurrence engine.

    Verifies behavior with zero-amount templates, None recurrence rules,
    empty period lists, and immutable status preservation during regeneration.
    """

    def _make_template_with_rule(self, seed_user, pattern_name,
                                  default_amount=Decimal("100.00"),
                                  **rule_kwargs):
        """Helper: create a template + recurrence rule with configurable amount."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="expense")
            .one()
        )

        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=rule_kwargs.get("interval_n", 1),
            offset_periods=rule_kwargs.get("offset_periods", 0),
            day_of_month=rule_kwargs.get("day_of_month"),
            month_of_year=rule_kwargs.get("month_of_year"),
            end_date=rule_kwargs.get("end_date"),
        )
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=expense_type.id,
            name="Test Recurring NP",
            default_amount=default_amount,
        )
        db.session.add(template)
        db.session.flush()
        db.session.refresh(template)
        return template

    def test_template_with_zero_estimated_amount(
        self, app, db, seed_user, seed_periods
    ):
        """Zero-amount template generates transactions with amount=0.00.

        Input: Template with default_amount=0.00, every_period pattern.
        Expected: One transaction per period, each with estimated_amount=0.00.
        The engine does not skip zero-amount templates.
        Why: A template accidentally set to $0 must behave predictably, not crash
        or generate phantom balances.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period", default_amount=Decimal("0.00")
            )
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            # Engine generates for all periods regardless of amount.
            assert len(created) == len(seed_periods)
            for txn in created:
                assert txn.estimated_amount == Decimal("0.00")

    def test_template_with_none_recurrence_rule(
        self, app, db, seed_user, seed_periods
    ):
        """Template with no recurrence rule returns empty list.

        Input: Template with recurrence_rule_id=None.
        Expected: generate_for_template returns [].
        Why: Templates without rules are manually placed; the engine must not
        crash or generate spurious transactions.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense")
                .one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Car Payment"].id,
                recurrence_rule_id=None,
                transaction_type_id=expense_type.id,
                name="No Rule Template",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            db.session.refresh(template)

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            assert created == []

    def test_received_status_in_existing_transaction_is_immutable(
        self, app, db, seed_user, seed_periods
    ):
        """Received transactions must NOT be deleted or recreated on regeneration.

        Input: Generate for all periods, mark one as received, regenerate.
        Expected: The received transaction persists with same ID and status.
        Other periods are regenerated normally.
        Why: The recurrence engine must never overwrite settled financial history.
        A received paycheck deleted by regeneration corrupts balance history.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            # Initial generation.
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Mark the 3rd transaction as received.
            received_status = (
                db.session.query(Status).filter_by(name="received").one()
            )
            target_txn = created[2]
            target_id = target_txn.id
            target_txn.status_id = received_status.id
            db.session.flush()

            # Regenerate -- received transaction must survive.
            recurrence_engine.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Verify the received transaction still exists unchanged.
            preserved = db.session.get(Transaction, target_id)
            assert preserved is not None, (
                f"Received transaction {target_id} was deleted during regeneration"
            )
            assert preserved.status.name == "received"
            assert preserved.id == target_id

    def test_generate_with_empty_periods_list(
        self, app, db, seed_user, seed_periods
    ):
        """Empty periods list returns empty without error.

        Input: Template with valid rule, periods=[].
        Expected: Returns []. No crash.
        Why: Edge case when the user has no pay periods generated yet.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )
            created = recurrence_engine.generate_for_template(
                template, [], seed_user["scenario"].id,
                effective_from=date(2026, 1, 1),
            )

            assert created == []

    def test_cancelled_status_in_existing_is_immutable(
        self, app, db, seed_user, seed_periods
    ):
        """Cancelled transactions must be preserved on regeneration.

        Input: Generate for all periods, cancel one, regenerate.
        Expected: The cancelled transaction persists with same ID and status.
        Why: Cancelled items represent a deliberate user action that must
        not be overwritten by the recurrence engine.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Cancel one transaction.
            cancelled_status = (
                db.session.query(Status).filter_by(name="cancelled").one()
            )
            target_txn = created[4]
            target_id = target_txn.id
            target_txn.status_id = cancelled_status.id
            db.session.flush()

            # Regenerate.
            recurrence_engine.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Cancelled transaction must still exist.
            preserved = db.session.get(Transaction, target_id)
            assert preserved is not None, (
                f"Cancelled transaction {target_id} was deleted "
                f"during regeneration"
            )
            assert preserved.status.name == "cancelled"
            assert preserved.id == target_id

    def test_credit_status_in_existing_is_immutable(
        self, app, db, seed_user, seed_periods
    ):
        """Credit transactions must be preserved on regeneration.

        Input: Generate for all periods, mark one as credit, regenerate.
        Expected: The credit transaction persists with same ID and status.
        Why: Credit items represent payments on a credit card and must not
        be overwritten by the recurrence engine.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Mark one as credit.
            credit_status = (
                db.session.query(Status).filter_by(name="credit").one()
            )
            target_txn = created[6]
            target_id = target_txn.id
            target_txn.status_id = credit_status.id
            db.session.flush()

            # Regenerate.
            recurrence_engine.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Credit transaction must still exist.
            preserved = db.session.get(Transaction, target_id)
            assert preserved is not None, (
                f"Credit transaction {target_id} was deleted "
                f"during regeneration"
            )
            assert preserved.status.name == "credit"
            assert preserved.id == target_id


class TestEndDate:
    """Tests for the optional end_date on recurrence rules."""

    def test_end_date_limits_every_period(self, biweekly_periods):
        """end_date stops generation after that date (every_period)."""
        # End date after the 5th period's start_date (period index 4).
        end = biweekly_periods[4].start_date
        rule = FakeRule(pattern_name="every_period", end_date=end)
        effective_from = biweekly_periods[0].start_date

        matched = _match_periods(rule, "every_period", biweekly_periods,
                                 effective_from)

        assert len(matched) == 5
        for p in matched:
            assert p.start_date <= end

    def test_end_date_none_means_indefinite(self, biweekly_periods):
        """NULL end_date generates for all periods (no change from default)."""
        rule = FakeRule(pattern_name="every_period", end_date=None)
        effective_from = biweekly_periods[0].start_date

        matched = _match_periods(rule, "every_period", biweekly_periods,
                                 effective_from)

        assert len(matched) == 26

    def test_end_date_with_monthly_pattern(self, biweekly_periods):
        """end_date works with monthly pattern -- only months before end."""
        # End in March 2026.
        rule = FakeRule(pattern_name="monthly", day_of_month=15,
                        end_date=date(2026, 3, 31))
        effective_from = biweekly_periods[0].start_date

        matched = _match_periods(rule, "monthly", biweekly_periods,
                                 effective_from)

        # Should get Jan, Feb, Mar only.
        assert len(matched) == 3
        for p in matched:
            assert p.start_date <= date(2026, 3, 31)

    def test_end_date_before_first_period(self, biweekly_periods):
        """end_date before all periods returns empty list."""
        rule = FakeRule(pattern_name="every_period",
                        end_date=date(2025, 12, 31))
        effective_from = biweekly_periods[0].start_date

        matched = _match_periods(rule, "every_period", biweekly_periods,
                                 effective_from)

        assert matched == []

    def test_end_date_with_effective_from_both_filter(self, biweekly_periods):
        """Both effective_from and end_date narrow the window."""
        # effective_from at period 5, end_date at period 10.
        effective_from = biweekly_periods[5].start_date
        end = biweekly_periods[10].start_date
        rule = FakeRule(pattern_name="every_period", end_date=end)

        matched = _match_periods(rule, "every_period", biweekly_periods,
                                 effective_from)

        # Periods 5 through 10 inclusive.
        assert len(matched) == 6
        for p in matched:
            assert p.start_date >= effective_from
            assert p.start_date <= end

    def test_end_date_mid_period_includes_that_period(self, biweekly_periods):
        """A period whose start_date is on the end_date is included."""
        target_period = biweekly_periods[7]
        rule = FakeRule(pattern_name="every_period",
                        end_date=target_period.start_date)
        effective_from = biweekly_periods[0].start_date

        matched = _match_periods(rule, "every_period", biweekly_periods,
                                 effective_from)

        assert target_period in matched

    def test_end_date_with_every_n_periods(self, biweekly_periods):
        """end_date works correctly with every_n_periods pattern."""
        # Every 3 periods, end at period 12.
        end = biweekly_periods[11].start_date
        rule = FakeRule(pattern_name="every_n_periods", interval_n=3,
                        offset_periods=0, end_date=end)
        effective_from = biweekly_periods[0].start_date

        matched = _match_periods(rule, "every_n_periods", biweekly_periods,
                                 effective_from)

        # Periods 0, 3, 6, 9 (index % 3 == 0 and start_date <= end).
        assert len(matched) == 4
        for p in matched:
            assert p.period_index % 3 == 0
            assert p.start_date <= end


class TestEndDateIntegration:
    """Integration tests for end_date with generate_for_template()."""

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a template + recurrence rule."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="expense")
            .one()
        )

        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=rule_kwargs.get("interval_n", 1),
            offset_periods=rule_kwargs.get("offset_periods", 0),
            day_of_month=rule_kwargs.get("day_of_month"),
            month_of_year=rule_kwargs.get("month_of_year"),
            end_date=rule_kwargs.get("end_date"),
        )
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=expense_type.id,
            name="Test Recurring End Date",
            default_amount=Decimal("50.00"),
        )
        db.session.add(template)
        db.session.flush()
        db.session.refresh(template)
        return template

    def test_generate_respects_end_date(self, app, db, seed_user, seed_periods):
        """generate_for_template stops at end_date."""
        with app.app_context():
            # Use the 5th period's start_date as end_date.
            end = seed_periods[4].start_date
            template = self._make_template_with_rule(
                seed_user, "every_period", end_date=end,
            )

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            assert len(created) == 5
            for txn in created:
                period = txn.pay_period
                assert period.start_date <= end

    def test_regenerate_respects_end_date(self, app, db, seed_user, seed_periods):
        """regenerate_for_template respects end_date on re-creation."""
        with app.app_context():
            end = seed_periods[2].start_date
            template = self._make_template_with_rule(
                seed_user, "every_period", end_date=end,
            )

            # Initial generation.
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            assert len(created) == 3

            # Regenerate -- should produce the same count.
            regenerated = recurrence_engine.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            assert len(regenerated) == 3
