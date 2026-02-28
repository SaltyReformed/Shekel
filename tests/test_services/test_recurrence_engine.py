"""
Shekel Budget App — Recurrence Engine Tests

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
                 start_period_id=None, start_period=None):
        self.pattern = FakePattern(pattern_name)
        self.interval_n = interval_n
        self.offset_periods = offset_periods
        self.day_of_month = day_of_month
        self.month_of_year = month_of_year
        self.start_period_id = start_period_id
        self.start_period = start_period


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
        """'once' pattern does not auto-generate — user places it manually."""
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

            # Second generation — should create nothing new.
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

            # Regenerate — the overridden entry should be preserved.
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

            # Regenerate — should not delete the done transaction.
            recurrence_engine.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transaction should still exist unchanged.
            db.session.refresh(created[0])
            assert created[0].actual_amount == Decimal("95.00")


# --- Pure Pattern Matching Tests ---------------------------------------------


class TestMatchMonthly:
    """Tests for _match_monthly() — pure function, no DB."""

    def test_monthly_day_15(self, biweekly_periods):
        """Finds the period containing the 15th of each month."""
        matched = _match_monthly(biweekly_periods, day_of_month=15)

        # 26 biweekly periods span Jan–Dec 2026 → one match per month = 12.
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
                f"Period {period.period_index} ({period.start_date}–"
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
    """Tests for _match_monthly_first() — pure function, no DB."""

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
    """Tests for _match_quarterly() — pure function, no DB."""

    def test_quarterly_jan_start(self, biweekly_periods):
        """start_month=1 targets Jan, Apr, Jul, Oct."""
        matched = _match_quarterly(biweekly_periods, start_month=1,
                                   day_of_month=15)

        # 26 biweekly periods cover Jan–Dec 2026 → 4 quarterly months.
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
    """Tests for _match_semi_annual() — pure function, no DB."""

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
    """Tests for _match_annual() — pure function, no DB."""

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
    """Edge case tests for _match_periods() — pure function, no DB."""

    def test_effective_from_filters_earlier_periods(self, biweekly_periods):
        """Only periods on/after effective_from are candidates."""
        rule = FakeRule(pattern_name="every_period")
        # Use the 4th period's start_date as effective_from.
        effective_from = biweekly_periods[3].start_date

        matched = _match_periods(rule, "every_period", biweekly_periods,
                                 effective_from)

        assert len(matched) == 26 - 3  # Periods 3–25.
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
    """Integration tests for _match_periods() dispatch — pure, no DB."""

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

            # Second generation — should not duplicate the deleted entry.
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

            # Regenerate — should delete old and create new.
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

            # Regenerate — should raise with deleted list.
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
