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
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern, TransactionType, Status
from app import ref_cache
from app.enums import RecurrencePatternEnum, StatusEnum
from app.services import pay_period_service, pay_period_write, recurrence_engine
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.recurrence import RecurrenceResolutionError
from app.services.recurrence import placed_periods, rule_occurrences
from app.exceptions import (
    RecurrenceCadenceUnsupported,
    RecurrenceConflict,
    ValidationError,
)
from app.services import account_service
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import open_calendar_hole

# Map human-readable pattern names to RecurrencePatternEnum members for
# use in build_rule and test helpers.  Allows tests to construct a rule
# with a pattern name string and resolve it to the integer ID via ref_cache.
_PATTERN_NAME_TO_ENUM = {e.value: e for e in RecurrencePatternEnum}


# --- Rule / period objects for the pure pattern-matching tests ---------------


#: The owner every rule and calendar the PURE matcher tests build names.  The
#: ``FakePeriod`` schedule carries no ``user_id``, and
#: ``app.services.recurrence.resolve`` REFUSES a rule paired with another
#: user's schedule -- so one constant is what keeps the two halves agreeing.
#: Until plan step R4b-1 no constant was needed: period selection built the
#: calendar from ``rule.user_id`` itself, so the owner check compared a value
#: against itself and could not fail.
_MATCH_USER_ID = 1

#: The cadence the hand-built schedules below are generated at.  Named because
#: :func:`_calendar` has to STATE it: since plan step C2-b2 a calendar derives
#: every period's end from the NEXT payday, and the last one's from the owner's
#: cadence, so the cadence is an input rather than a column to copy.
_CADENCE_DAYS = 14


def build_rule(pattern_name="Every Period", interval_n=1,
               day_of_month=None, month_of_year=None,
               start_date=None, end_date=None,
               due_day_of_month=None):
    """Build a REAL, unsaved ``RecurrenceRule`` for the pure matcher tests.

    ``rule_occurrences`` and ``compute_due_date`` are pure functions of a rule's
    columns, so they need no database -- but they DO need the real column
    surface.  This used to be a hand-rolled ``FakeRule`` stub, which is the
    anti-pattern finding B-17 names: a test double that mirrors the model by
    hand silently drifts from it.  It drifted the moment ``start_date`` was
    added (plan step C9a) -- 17 tests died on ``AttributeError`` rather than on
    a behaviour change, and had the matcher instead read a column the stub
    lacked while the stub still satisfied every assertion, they would have gone
    green on a rule shape that cannot exist.

    An unsaved model instance costs nothing extra (no session, no flush) and
    guarantees the real COLUMN SURFACE: a column the model does not have raises
    ``TypeError`` here, and a column it does have cannot differ in name or type
    from the one production reads.

    It does NOT guarantee real DEFAULTS -- SQLAlchemy applies ``default=`` at
    INSERT, so an unflushed ``RecurrenceRule()`` carries ``None`` for
    ``interval_n`` despite its ``default=1``.  That is why it is passed
    explicitly below, and any future column with a Python-side default the
    resolver branches on must be passed here too, or these tests will exercise
    a rule shape production never sees.

    **``offset_periods`` and ``start_period_id`` LEFT this signature at plan
    step R7b-4**, and not because they stopped having defaults: nothing reads
    either column now.  A rule has ONE opening bound, ``start_date``, and the
    ``Every N Periods`` phase is the ordinal of the paycheck that bound falls
    in.  Keeping them here would let a case state a phase the resolver
    ignores, which is a test that agrees with itself.

    Args:
        pattern_name: Display name of the recurrence pattern, resolved to
            ``pattern_id`` through ``ref_cache`` (needs an app context, as the
            stub's own constructor did).
        interval_n: ``every_n_periods`` interval.
        day_of_month: Scheduling day for monthly / quarterly / annual.
        month_of_year: Month for the annual / semi-annual patterns.
        start_date: The rule's opening validity bound, and the whole of what
            it says about when it begins -- including which paycheck an
            ``Every N Periods`` rule phases on.
        end_date: The rule's closing validity bound.
        due_day_of_month: Real bill due day when it differs from
            ``day_of_month``.

    Returns:
        An unsaved :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    enum_member = _PATTERN_NAME_TO_ENUM.get(pattern_name)
    return RecurrenceRule(
        user_id=_MATCH_USER_ID,
        pattern_id=(
            ref_cache.recurrence_pattern_id(enum_member)
            if enum_member else None
        ),
        interval_n=interval_n,
        day_of_month=day_of_month,
        due_day_of_month=due_day_of_month,
        month_of_year=month_of_year,
        start_date=start_date,
        end_date=end_date,
    )


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
            .filter_by(name="Expense")
            .one()
        )

        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=rule_kwargs.get("interval_n", 1),
            offset_periods=rule_kwargs.get("offset_periods", 0),
            day_of_month=rule_kwargs.get("day_of_month"),
            month_of_year=rule_kwargs.get("month_of_year"),
            # The opening bound, which is also what phases an
            # ``Every N Periods`` rule since plan step R7b-4.
            start_date=rule_kwargs.get("start_date"),
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
                seed_user, "Every Period"
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
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
        """every_n_periods starting in period 1 generates every other period.

        The phase is the ordinal of the paycheck the opening bound falls in
        (plan step R7b-4), so starting on period index 1's own payday phases
        the rule at ``1 % 2 == 1`` -- indices 1, 3, 5, 7, 9.  It stated
        ``offset_periods=1`` directly until that step made the phase a
        derivation; the fired set is the same, reached from the fact a form
        can state.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every N Periods",
                interval_n=2, start_date=seed_periods[1].start_date,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )

            # With 10 periods (indices 0-9), offset=1 matches indices 1,3,5,7,9 → 5.
            assert len(created) == 5
            for txn in created:
                period = db.session.get(
                    __import__("app.models.pay_period", fromlist=["PayPeriod"]).PayPeriod,
                    txn.pay_period_id,
                )
                assert (period.period_index - 1) % 2 == 0

    def test_a_rule_less_template_generates_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A template that does not repeat auto-generates nothing.

        Rule-less is the ONE way a definition says "does not recur" since
        plan step R2e-3; this named the ``Once`` PATTERN before it, and that
        spelling had its own guard in ``resolve_generation_plan``.  Asserting
        the rule-less path is what still has a guard to protect -- the same
        assertion against a ``Once``-by-name rule would now pass through
        the retired matcher's unmodelled-pattern default instead, proving nothing
        about the branch it was written for.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period",
            )
            # Both sides plus the row itself, exactly as
            # ``_recurrence_form_helpers._clear_recurrence_rule`` does: the
            # relationship is ``lazy="joined"`` and already loaded, so nulling
            # only the FK leaves the engine reading the stale object.
            rule = template.recurrence_rule
            template.recurrence_rule = None
            template.recurrence_rule = None
            db.session.flush()
            db.session.delete(rule)
            db.session.flush()
            assert template.recurrence_rule is None

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_skips_existing_entries(self, app, db, seed_user, seed_periods):
        """Does not create duplicates for periods that already have entries."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period",
            )

            # First generation.
            first_run = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first_run) == len(seed_periods)

            # Second generation -- should create nothing new.
            second_run = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_respects_is_override_flag(self, app, db, seed_user, seed_periods):
        """Overridden entries are not replaced during generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period",
            )

            # Generate entries.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
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
                    template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
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
                seed_user, "Every Period",
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            # Mark the first one as done.
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            created[0].status_id = done_status.id
            created[0].actual_amount = Decimal("95.00")
            db.session.flush()

            # Regenerate -- should not delete the done transaction.
            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transaction should still exist unchanged.
            db.session.refresh(created[0])
            assert created[0].actual_amount == Decimal("95.00")


# --- Pure Pattern Matching Tests ---------------------------------------------
#
# These exercise the PUBLIC producer (``rule_occurrences``, and before plan
# step R4b-2 the ``match_periods`` adapter) rather than the five ``_match_*``
# helpers plan step R4a deleted with the reverse matcher.  The behaviour each
# class asserts is unchanged and is what production reads; what changed is that
# the assertion now runs against the function every caller actually calls,
# instead of against a private helper that no longer exists.


def _calendar(periods, cadence_days=_CADENCE_DAYS):
    """Return the owner's :class:`~app.services.pay_calendar.PayCalendar`.

    Period selection takes the OWNER's schedule as one calendar VALUE since
    plan step R4b-1, rather than building one from whatever candidate list it
    was handed.  Named once here so each pattern test states the rule and
    nothing else.

    **It reads the PAYDAY and the id, and nothing else** (plan step C2-b2).
    A period's ordinal and its last covered day are DERIVED from the payday
    set, so passing a row's stored ``end_date`` in would be passing a second
    answer -- the denormalization the pay-calendar arc exists to remove.  Every
    schedule these tests build is contiguous and generated at one cadence, so
    the derived ends reproduce the stated ones exactly.

    Args:
        periods: The schedule as rows carrying ``id`` and ``start_date``, in
            any order -- the derivation sorts by payday.  **The owner's WHOLE
            payday set**: a slice re-indexes from zero, which is plan ledger
            row P26.
        cadence_days: Days between paydays.  Read only for the LAST period's
            end; every other end is dictated by the next payday.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar` for
        :data:`_MATCH_USER_ID`.
    """
    return PayCalendar.from_paydays(
        paydays=[(period.id, period.start_date) for period in periods],
        cadence_days=cadence_days,
        user_id=_MATCH_USER_ID,
    )


def _matched_periods(rule, calendar, effective_from):
    """Return the pay periods *rule* fires in, at or after *effective_from*.

    **The contract the retired ``recurrence_engine.match_periods`` adapter
    had**, expressed over the two PRODUCTION functions that replaced it at plan
    step R4b-2 -- :func:`~app.services.recurrence.rule_occurrences` and
    :func:`~app.services.recurrence.placed_periods`.  It composes them rather
    than reimplementing the filter, deliberately: a helper that re-stated the
    predicate would let the 26 assertions below keep passing while production
    drifted, which is the shape a neutral review flagged in this step's first
    draft.

    Every assertion below is about which PERIODS a rule fires in, which is the
    question the adapter answered, so porting them onto this projection keeps
    each test asserting what it was written to assert.  Occurrence DATES are
    pinned separately, by ``tests/test_services/test_recurrence_occurrence.py``,
    and the projection's own bound semantics by
    :class:`TestThePlacedPeriodsBound`.

    Args:
        rule: The unsaved rule from :func:`build_rule`.
        calendar: The OWNER's whole schedule, from :func:`_calendar`.
        effective_from: Drop periods ENDING before this date; ``None`` applies
            no bound.

    Returns:
        The matched :class:`~app.services.pay_calendar.DerivedPeriod` values,
        ascending by occurrence date, one entry per occurrence.
    """
    return placed_periods(
        rule_occurrences(rule, calendar), ending_on_or_after=effective_from,
    )


def _matches(rule, periods):
    """Return the periods *rule* fires in, over the whole schedule.

    ``effective_from`` is ``None`` -- no lower window bound.  It used to be
    ``periods[0].start_date``, which plan step R4b-1 proved redundant: the
    anchor's own floor is ``PayCalendar.opening_bound()``, so no walk can emit
    an occurrence placed before it.

    Args:
        rule: The unsaved rule from :func:`build_rule`.
        periods: The schedule to match against.

    Returns:
        The matched :class:`~app.services.pay_calendar.DerivedPeriod` values,
        ascending by occurrence date.
    """
    return _matched_periods(rule, _calendar(periods), None)


class TestMatchMonthly:
    """The Monthly pattern, through the public producer."""

    def test_monthly_day_15(self, biweekly_periods):
        """Finds the period containing the 15th of each month."""
        matched = _matches(
            build_rule(pattern_name="Monthly", day_of_month=15),
            biweekly_periods,
        )

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
        matched = _matches(
            build_rule(pattern_name="Monthly", day_of_month=31),
            biweekly_periods,
        )

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
        matched = _matches(
            build_rule(pattern_name="Monthly", day_of_month=30),
            biweekly_periods,
        )

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
    """The Monthly First pattern, through the public producer."""

    def test_picks_first_period_starting_in_each_month(self, biweekly_periods):
        """One period per calendar month, the earliest starting in that month."""
        matched = _matches(
            build_rule(pattern_name="Monthly First"), biweekly_periods,
        )

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
    """The Quarterly pattern, through the public producer."""

    def test_quarterly_jan_start(self, biweekly_periods):
        """start_month=1 targets Jan, Apr, Jul, Oct."""
        matched = _matches(
            build_rule(
                pattern_name="Quarterly", month_of_year=1, day_of_month=15,
            ),
            biweekly_periods,
        )

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
        matched = _matches(
            build_rule(
                pattern_name="Quarterly", month_of_year=11, day_of_month=15,
            ),
            biweekly_periods,
        )

        assert len(matched) == 4

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {2, 5, 8, 11}


class TestMatchSemiAnnual:
    """The Semi-Annual pattern, through the public producer."""

    def test_semi_annual_jan_start(self, biweekly_periods):
        """start_month=1 targets Jan and Jul."""
        matched = _matches(
            build_rule(
                pattern_name="Semi-Annual", month_of_year=1, day_of_month=15,
            ),
            biweekly_periods,
        )

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
        matched = _matches(
            build_rule(
                pattern_name="Semi-Annual", month_of_year=8, day_of_month=15,
            ),
            biweekly_periods,
        )

        assert len(matched) == 2

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {2, 8}


class TestMatchAnnual:
    """The Annual pattern, through the public producer."""

    def test_annual_one_per_year(self, biweekly_periods):
        """One match per calendar year on a specific month/day."""
        matched = _matches(
            build_rule(
                pattern_name="Annual", month_of_year=3, day_of_month=15,
            ),
            biweekly_periods,
        )

        # All periods are in 2026, so exactly one match.
        assert len(matched) == 1

        period = matched[0]
        assert period.start_date <= date(2026, 3, 15) <= period.end_date

    def test_annual_feb29_non_leap_year(self, biweekly_periods):
        """Feb 29 target in 2026 (non-leap) clamps to Feb 28."""
        matched = _matches(
            build_rule(
                pattern_name="Annual", month_of_year=2, day_of_month=29,
            ),
            biweekly_periods,
        )

        assert len(matched) == 1

        period = matched[0]
        # Clamped to Feb 28 since 2026 is not a leap year.
        assert period.start_date <= date(2026, 2, 28) <= period.end_date


class TestMatchPeriodsEdgeCases:
    """Edge case tests for _matched_periods() -- pure function, no DB."""

    def test_effective_from_filters_earlier_periods(self, biweekly_periods):
        """Only periods on/after effective_from are candidates."""
        rule = build_rule(pattern_name="Every Period")
        # Use the 4th period's start_date as effective_from.
        effective_from = biweekly_periods[3].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 26 - 3  # Periods 3-25.
        for period in matched:
            assert period.start_date >= effective_from

    def test_unknown_pattern_is_refused(self, biweekly_periods):
        """An unmodelled pattern id is REFUSED, naming the id.

        It used to log a warning and answer ``[]``, which reads as "this rule
        fires nowhere" -- a rule that generates nothing forever, silently.  A
        pattern id the application does not MODEL is a broken invariant, not a
        rule with no occurrences: ``RecurrencePatternEnum`` is the vocabulary
        (plan step R2e-2), the write doors refuse anything outside it, and
        plan step R2e-3's migration deleted the last rows that carried one.
        Plan step R4a's adapter resolves through
        ``app.services.recurrence.resolve``, which raises rather than
        fabricating a cadence.
        """
        rule = build_rule(pattern_name="bogus_pattern")
        # ``build_rule`` leaves ``pattern_id`` NULL for a name the enum does
        # not carry; an id the ``ref`` table does not carry is the other half
        # of the same state, and both must refuse.
        rule.pattern_id = 99999
        effective_from = biweekly_periods[0].start_date

        with pytest.raises(RecurrenceResolutionError, match="99999"):
            _matched_periods(rule, _calendar(biweekly_periods), effective_from)


class TestTheEveryNPeriodsPhase:
    """Which paychecks an ``Every N Periods`` rule fires on, and from what.

    **ONE input decides it, since plan step R7b-4**: the opening bound.  The
    phase is the ordinal of the paycheck that bound falls in, so a rule cannot
    state its cadence twice and there is no second value for the anchor to
    disagree with.

    What this class covered before is the history worth keeping, because it is
    what the single input replaced.  The reverse matcher read the STORED
    ``offset_periods`` column unconditionally; plan step R4a made the read
    RESOLVE instead, taking the phase from the rule's start PERIOD when the
    calendar contained that period and falling back to the column when it did
    not.  Two facts, two sources, and a fallback between them -- a neutral
    review of R4a found the gap and these tests were what closed it.  R7b-4
    removed the second source rather than the disagreement: the start period
    folded into ``start_date``, and the column became output-only.

    All 46 live rules carry ``interval_n = 1``, where the phase is inert
    (measured 2026-08-14 against a production clone), so the change cost
    ``$0.00`` and is about rules authored from here on.
    """

    def test_the_phase_comes_from_the_paycheck_the_bound_falls_in(
        self, biweekly_periods,
    ):
        """A rule starting in period 4 fires from it, every third after.

        ``4 % 3 == 1``, so the fired set is indices 4, 7, 10, ... -- the
        paycheck the user named and every third one after it.

        The bound named that paycheck by FK until plan step R7b-4 and names it
        by date now; the assertion is unchanged, which is what makes this a
        re-expression rather than a new claim.
        """
        rule = build_rule(
            pattern_name="Every N Periods",
            interval_n=3,
            start_date=biweekly_periods[4].start_date,
        )

        matched = _matched_periods(
            rule, _calendar(biweekly_periods), biweekly_periods[0].start_date,
        )

        assert [p.period_index for p in matched] == [4, 7, 10, 13, 16, 19, 22, 25]

    def test_a_mid_period_bound_phases_on_the_paycheck_that_contains_it(
        self, biweekly_periods,
    ):
        """The bound need not BE a payday for the phase to be exact.

        A date one day after period 4 opens is still inside period 4, so the
        rule phases at ``4 % 3 == 1`` and fires the identical set.  It is the
        paycheck the money comes out of that the cadence counts, not the
        calendar day -- which is why the derivation asks containment rather
        than equality, and why an opening bound the user typed by hand (a loan
        origination, a mid-month start) lands where they meant.
        """
        rule = build_rule(
            pattern_name="Every N Periods",
            interval_n=3,
            start_date=biweekly_periods[4].start_date + timedelta(days=1),
        )

        matched = _matched_periods(
            rule, _calendar(biweekly_periods), biweekly_periods[0].start_date,
        )

        assert [p.period_index for p in matched] == [4, 7, 10, 13, 16, 19, 22, 25]

    def test_a_bound_past_the_horizon_still_derives_a_phase(
        self, biweekly_periods,
    ):
        """Totality: the derivation answers past the materialised schedule.

        ``PayCalendar.span_containing`` PROJECTS the ordinal forward from the
        last saved payday at the owner's own cadence, so a bound the schedule
        has not reached still has an ordinal to take a remainder of.  Nothing
        fires -- there are no materialised periods out there to fire in -- but
        the value stays derivable, which is what plan step R7c's NOT NULL
        columns require.

        **This replaces a case about a DANGLING start-period id.**  That state
        was reachable -- the FK is ``ON DELETE SET NULL``, but a rule read into
        memory outlives the row its id named -- and it was the one branch where
        the phase fell back to the stored column.  Neither the id nor the
        fallback exists now, so the question that survives is what the
        derivation answers where the schedule stops.
        """
        past_horizon = biweekly_periods[-1].end_date + timedelta(days=365)
        rule = build_rule(
            pattern_name="Every N Periods",
            interval_n=3,
            start_date=past_horizon,
        )

        matched = _matched_periods(
            rule, _calendar(biweekly_periods), biweekly_periods[0].start_date,
        )

        assert matched == []


class TestMatchPeriodsFull:
    """Integration tests for _matched_periods() dispatch -- pure, no DB."""

    def test_every_period_returns_all_candidates(self, biweekly_periods):
        """every_period returns all periods after effective_from filtering."""
        rule = build_rule(pattern_name="Every Period")
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 26

    def test_no_periods_empty_result(self):
        """Empty periods list produces an empty result."""
        rule = build_rule(pattern_name="Every Period")

        matched = _matched_periods(rule, _calendar([]),
                                 date(2026, 1, 1))

        assert matched == []


class TestMatchPeriodsEdgeCaseSafety:
    """Behaviour of period selection on values the DB CHECKs forbid.

    These values are prevented at the storage tier but could reach the
    service via FakeRule objects, direct calls, or an unflushed in-memory
    rule.  The contract differs by field:

      * ``interval_n`` is NOT NULL with CHECK ``> 0``
        (``ck_recurrence_rules_positive_interval``), so an invalid 0 / None
        is a programming error and is REFUSED, naming the value -- never
        coerced to 1, which would mis-generate on every period instead of
        every N.
      * ``day_of_month`` / ``month_of_year`` are genuinely nullable (NULL for
        the patterns that do not fire on a calendar day), so FALSY values keep
        their ``or 1`` coercion, while a value outside the column's own domain
        is refused (plan step R4a).

    **Plan step R4a moved every one of these refusals to one door.**  The five
    reverse-matching helpers each failed in their own way and in their own
    place -- ``% 0`` raised ``ZeroDivisionError``, ``date(y, m, 0)`` raised
    ``ValueError``, ``monthrange(y, 13)`` raised ``ValueError``, and a day of
    32 was silently clamped -- so the same malformed rule had four different
    dispositions depending on its pattern.  It is now one:
    ``app.services.recurrence.resolve`` raises
    :class:`~app.services.recurrence.RecurrenceResolutionError` naming the
    field and the value, before any walking.
    """

    # -- interval_n edge cases (every_n_periods) --

    def test_every_n_periods_interval_zero_raises(
        self, biweekly_periods
    ):
        """interval_n=0 is refused, naming the value (fail-loud, no coercion).

        ``ck_recurrence_rules_positive_interval`` + NOT NULL make a persisted
        interval_n=0 impossible, so reaching the matcher with 0 means an
        invalid in-memory rule (a programming error).  It used to surface as a
        bare ``ZeroDivisionError`` from the phase modulo; the resolution door
        refuses it first and says which value and which rule, which is what an
        operator reading the traceback needs.
        """
        rule = build_rule(
            pattern_name="Every N Periods",
            interval_n=0,
        )
        with pytest.raises(RecurrenceResolutionError, match="interval_n"):
            _matched_periods(
                rule, _calendar(biweekly_periods),
                biweekly_periods[0].start_date,
            )

    def test_every_n_periods_interval_none_raises(
        self, biweekly_periods
    ):
        """interval_n=None raises TypeError (fail-loud, no coercion).

        Distinct failure mode from 0: None means the column default was
        not applied (an unflushed / invalid in-memory rule).  ``% None``
        raises TypeError rather than the old ``or 1`` silently treating
        the rule as every-period.
        """
        rule = build_rule(
            pattern_name="Every N Periods",
            interval_n=None,
        )
        with pytest.raises(TypeError):
            _matched_periods(
                rule, _calendar(biweekly_periods),
                biweekly_periods[0].start_date,
            )

    # -- day_of_month edge cases (monthly) --

    @pytest.mark.parametrize("day", [0, 32, 99, -5])
    def test_day_of_month_outside_its_column_domain_is_refused(
        self, biweekly_periods, day,
    ):
        """A STATED day outside 1-31 is refused, not coerced or clamped.

        Three dispositions became one at plan step R4a.  ``_match_monthly``
        clamped with ``min(day, last_day)``, so 32 was silently identical to
        31; a NEGATIVE day reached ``date(y, m, -5)`` as a bare
        ``ValueError``; and the retired matcher mapped 0 onto 1 through
        ``rule.day_of_month or 1``, which read an impossible day as an absent
        one.  ``ck_recurrence_rules_dom`` bounds the column to ``NULL OR
        1..31`` and ``_author`` writes the authored value verbatim, so a 0
        reached the flush as an unhandled ``IntegrityError``.  The resolution
        door now mirrors the column exactly: NULL states no day and defaults,
        anything else must be in domain.
        """
        rule = build_rule(pattern_name="Monthly", day_of_month=day)

        with pytest.raises(RecurrenceResolutionError, match="day_of_month"):
            _matched_periods(
                rule, _calendar(biweekly_periods),
                biweekly_periods[0].start_date,
            )

    def test_day_of_month_none_in_monthly_defaults_to_one(
        self, biweekly_periods
    ):
        """day_of_month=NULL states no day, which reads as the 1st.

        The column is nullable -- the pay-period-space patterns fire on no day
        of the month at all -- so NULL is a legal state with a default, unlike
        a stated 0 (refused above, plan step R4a).
        """
        rule_none = build_rule(
            pattern_name="Monthly", day_of_month=None,
        )
        rule_one = build_rule(
            pattern_name="Monthly", day_of_month=1,
        )
        effective = biweekly_periods[0].start_date

        matched_none = _matched_periods(
            rule_none, _calendar(biweekly_periods), effective,
        )
        matched_one = _matched_periods(
            rule_one, _calendar(biweekly_periods), effective,
        )
        assert (
            [p.period_index for p in matched_none]
            == [p.period_index for p in matched_one]
        ), (
            "day_of_month=None should produce identical matches "
            "to day_of_month=1 via 'or 1' fallback"
        )

    # -- month_of_year edge cases (quarterly, annual) --

    def test_a_null_month_of_year_reads_as_january(
        self, biweekly_periods
    ):
        """month_of_year=NULL states no cycle month: targets Jan/Apr/Jul/Oct.

        NULL is the only value that means "this rule states no cycle month",
        and the matcher has always read it as January.  ``0`` used to mean the
        same thing through ``rule.month_of_year or 1``; plan step R4a refuses
        it instead, because ``ck_recurrence_rules_moy`` bounds the column to
        ``NULL OR 1..12`` and 0 is not absence.

        **There used to be a second answer to a 0, and R4a deleted it too.**
        Calling ``_match_quarterly(start_month=0)`` directly bypassed the
        coercion, and its own modular arithmetic -- ``((0 - 1 + 3i) % 12) + 1``
        -- targeted Dec/Mar/Jun/Sep, so one malformed rule fired in a
        different quarter depending on which entry point read it.
        """
        effective = biweekly_periods[0].start_date
        rule_null = build_rule(
            pattern_name="Quarterly",
            month_of_year=None,
            day_of_month=15,
        )
        rule_one = build_rule(
            pattern_name="Quarterly",
            month_of_year=1,
            day_of_month=15,
        )

        matched_null = _matched_periods(
            rule_null,
            _calendar(biweekly_periods), effective,
        )
        matched_one = _matched_periods(
            rule_one,
            _calendar(biweekly_periods), effective,
        )

        assert (
            [p.period_index for p in matched_null]
            == [p.period_index for p in matched_one]
        ), "a NULL month_of_year should behave identically to January"
        # Stated absolutely, not only relatively: two equal-but-wrong answers
        # would satisfy the comparison above.  Every period is in 2026, so the
        # months that fired are the ones whose 15th one of them contains.
        fired_months = {
            month for month in range(1, 13)
            if any(
                period.start_date <= date(2026, month, 15) <= period.end_date
                for period in matched_null
            )
        }
        assert fired_months == {1, 4, 7, 10}

    @pytest.mark.parametrize("month", [0, 13, 99, -1])
    def test_month_of_year_outside_its_column_domain_is_refused(
        self, biweekly_periods, month,
    ):
        """A month outside 1-12 is refused, naming the value (plan step R4a).

        It used to depend on the pattern: ``_match_annual`` passed the value
        to ``calendar.monthrange(year, 13)`` and raised a bare ``ValueError``,
        while ``_match_quarterly`` and ``_match_semi_annual`` wrapped it
        modularly and answered {1, 4, 7, 10} as though the user had said
        January.  The forward engine walks month ORDINALS, where 13 is simply
        January again -- so without this refusal, deleting the old matcher
        would have traded the one loud failure for a silently plausible date.
        ``ck_recurrence_rules_moy`` bounds the column; the door now bounds the
        same thing.
        """
        rule = build_rule(
            pattern_name="Annual",
            month_of_year=month,
            day_of_month=15,
        )

        with pytest.raises(RecurrenceResolutionError, match="month_of_year"):
            _matched_periods(
                rule, _calendar(biweekly_periods),
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
            .filter_by(name="Expense")
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

    def test_a_bill_repeating_inside_one_paycheck_is_refused(
        self, app, db, seed_user, seed_periods
    ):
        """A monthly bill at a 90-day cadence REFUSES, it does not 500.

        **Plan step R4a made this reachable and this is its regression test.**
        The reverse matcher walked PAYCHECKS, so a monthly rule emitted one row
        per paycheck and silently dropped the rest -- defect D3.  Forward
        generation emits all three occurrences that fall inside one 90-day pay
        period, and ``idx_transactions_template_period_scenario`` is UNIQUE
        over ``(template, pay_period, scenario)``, so writing them raises an
        ``IntegrityError`` naming nothing and rolling back whatever transaction
        it was inside -- ``pay_period_admin.extend_pay_periods`` among them,
        which would leave the owner unable to extend their schedule at all.

        The refusal names the definition, the paycheck and how many times it
        falls inside it, and writes NOTHING (developer ruling 2026-08-08).
        Plan step R5 re-keys the index onto the occurrence and the refusal goes
        with the fix.

        Measured against the deleted matcher: ``Monthly First`` returned no
        repeated period at ANY cadence, so this failure mode is new to R4a --
        which is why the guard ships in the same commit as the cutover.
        """
        with app.app_context():
            # A 90-day schedule for this owner alone; ``cadence_days`` is
            # user-selectable 1..365, so this is configuration, not a
            # hypothetical.  Built after the seed periods so the batch opens
            # strictly after the latest existing end_date.
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=seed_periods[-1].end_date + timedelta(days=1),
                num_periods=4,
                cadence_days=90,
            )
            db.session.flush()
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=15,
            )

            # The premise, asserted rather than assumed: without this the test
            # would pass vacuously if the engine ever stopped duplicating, and
            # the guard would be a gate over nothing.
            matched = _matched_periods(
                template.recurrence_rule,
                calendar_for(seed_user["user"].id),
                long_periods[0].start_date,
            )
            assert len(matched) > len(
                {period.period_index for period in matched}
            ), (
                "the engine no longer repeats a period, so this test proves "
                "nothing about the guard"
            )

            with pytest.raises(RecurrenceCadenceUnsupported) as excinfo:
                recurrence_engine.generate_for_template(
                    template, GenerationSchedule.for_periods(template.user_id, long_periods), seed_user["scenario"].id,
                )

            # Named, not generic: the definition, the paycheck, and since plan
            # step R4b-2 every occurrence DATE -- which is what a user needs to
            # decide what to change.  The expected dates are DERIVED from the
            # paycheck's own span rather than typed as literals, so this
            # asserts the fact the refusal is about (a monthly bill is owed on
            # every 15th the paycheck covers) instead of three figures that
            # would still pass if the fixture's start date moved.
            paycheck = long_periods[0]
            expected_dates = tuple(
                day
                for day in (
                    date(year, month, 15)
                    for year in range(
                        paycheck.start_date.year, paycheck.end_date.year + 1,
                    )
                    for month in range(1, 13)
                )
                if paycheck.start_date <= day <= paycheck.end_date
            )
            assert len(expected_dates) == 3, (
                "a 90-day paycheck must cover the 15th of three months, or this "
                "fixture no "
                "longer exercises the repeat"
            )
            assert excinfo.value.template_name == "Test Recurring"
            assert excinfo.value.occurrence_dates == expected_dates
            assert excinfo.value.occurrence_count == 3
            assert excinfo.value.period_start == paycheck.start_date
            # The message carries them too -- the card and the log both read
            # from this exception, so an unnamed date would reach neither.
            for day in expected_dates:
                assert day.isoformat() in str(excinfo.value)
            # And nothing was written -- the refusal runs before the first add.
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == 0

    def test_a_paycheck_that_already_holds_a_row_is_skipped_not_refused(
        self, app, db, seed_user, seed_periods
    ):
        """The refusal runs AFTER the per-period skip, and that is deliberate.

        A paycheck already holding a row for this template is skipped by
        ``should_skip_period``, so no second row is attempted and there is
        nothing to refuse.  Testing before the skip would make an
        already-populated long-cadence schedule permanently unextendable: every
        later extend would refuse over periods it was never going to write.
        """
        with app.app_context():
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=seed_periods[-1].end_date + timedelta(days=1),
                num_periods=4,
                cadence_days=90,
            )
            db.session.flush()
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=15,
            )
            # Occupy every period the rule fires in, exactly as a previous
            # (pre-R4a) generation pass would have left them.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            for period in long_periods:
                db.session.add(Transaction(
                    account_id=template.account_id,
                    template_id=template.id,
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=projected_id,
                    name=template.name,
                    transaction_type_id=template.transaction_type_id,
                    estimated_amount=Decimal("100.00"),
                    is_override=False,
                    is_deleted=False,
                ))
            db.session.flush()

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, long_periods), seed_user["scenario"].id,
            )

            assert created == []

    def test_effective_from_skips_earlier_periods(
        self, app, db, seed_user, seed_periods
    ):
        """effective_from = 4th period's start → only generates from period 4."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period"
            )
            effective_from = seed_periods[3].start_date
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
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
                seed_user, "Every Period"
            )

            # First generation.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == 10

            # Soft-delete one entry.
            created[2].is_deleted = True
            db.session.flush()

            # Second generation -- should not duplicate the deleted entry.
            second_run = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_monthly_pattern_generates_correct_count(
        self, app, db, seed_user, seed_periods
    ):
        """Monthly pattern across 10 periods produces one per unique month."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
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


class TestThePlacedPeriodsBound:
    """``ending_on_or_after`` bounds a period's END, never its START.

    **The predicate three production surfaces share** -- the Recurring
    surface's next-date column, the form's occurrence preview, and this
    module's own ported assertions all reach it through
    :func:`~app.services.recurrence.placed_periods`, and the generation seam
    applies the identical comparison to its ``effective_from``.  Nothing
    asserted it: a neutral review mutated the comparison to ``start_date`` in
    all four places and 3,714 tests stayed green, because the only test that
    passed a bound passed a PERIOD BOUNDARY, where the two readings select
    identically.

    The distinction is real money.  A bound falling INSIDE a period means "from
    here forward", and the period the user is standing in still holds rows they
    are owed -- ``regenerate_for_template`` sweeps and rewrites from exactly
    such a date.  Reading it against the period's START would drop the current
    paycheck from the rewrite while the sweep had already deleted its rows.
    """

    def _monthly_placements(self, periods):
        """Placements of a day-15 monthly rule over *periods*."""
        return rule_occurrences(
            build_rule(pattern_name="Monthly", day_of_month=15),
            _calendar(periods),
        )

    def test_a_bound_inside_a_period_keeps_that_period(self, biweekly_periods):
        """The straddling period survives -- the whole point of ``end_date``.

        Under a ``start_date`` reading it would be dropped, which is what makes
        this case, and not a boundary case, the one worth asserting.
        """
        placements = self._monthly_placements(biweekly_periods)
        placed = [p.period for p in placements if p.period is not None]
        straddled = placed[3]
        # Strictly inside: after the period opens, before it closes.
        bound = straddled.start_date + timedelta(days=1)
        assert bound < straddled.end_date, "the fixture gave no interior day"

        kept = placed_periods(placements, ending_on_or_after=bound)

        assert straddled in kept, (
            "a period the bound falls INSIDE must be kept: the bound means "
            "'from here forward', and this paycheck is still ahead of the user"
        )
        # And it is the FIRST kept period -- everything before it is dropped.
        assert kept[0] == straddled
        assert all(period.end_date >= bound for period in kept)

    def test_a_bound_one_day_past_a_period_drops_it(self, biweekly_periods):
        """The complement, so the bound is shown to bound something.

        Without this the assertion above would pass for a function that
        filtered nothing at all.
        """
        placements = self._monthly_placements(biweekly_periods)
        placed = [p.period for p in placements if p.period is not None]
        straddled = placed[3]
        bound = straddled.end_date + timedelta(days=1)

        kept = placed_periods(placements, ending_on_or_after=bound)

        assert straddled not in kept
        assert len(kept) == len(placed) - 4

    def test_no_bound_keeps_every_placed_period(self, biweekly_periods):
        """``None`` applies no bound -- the default every read surface uses."""
        placements = self._monthly_placements(biweekly_periods)

        assert placed_periods(placements) == [
            p.period for p in placements if p.period is not None
        ]

    def test_the_generation_seam_applies_the_same_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """``effective_from`` bounds the period's END in the seam too.

        The seam keeps its own copy of the comparison -- it walks the pairs to
        build ``PlannedOccurrence`` values and to collect the gaps, so it cannot
        take the shared projection -- which is exactly why the copy needs its
        own assertion rather than inheriting the three above.
        """
        with app.app_context():
            template = self._make_template(seed_user)
            schedule = GenerationSchedule.for_user(template.user_id)
            straddled = seed_periods[3]
            bound = straddled.start_date + timedelta(days=1)
            assert bound < straddled.end_date

            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, bound,
                block_message="test",
            )

            kept = [row.period.id for row in plan.placements]
            assert straddled.id in kept, (
                "the seam dropped the period its bound falls inside"
            )
            assert seed_periods[2].id not in kept

    def _make_template(self, seed_user):
        """A day-15 monthly expense template for this owner."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name="Monthly")
            .one()
        )
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=1,
            offset_periods=0,
            day_of_month=15,
        )
        db.session.add(rule)
        db.session.flush()
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=expense_type.id,
            name="Bounded Bill",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        db.session.refresh(template)
        return template


class TestALegacyScheduleHole:
    """A day no pay period covers: ABSORBED by the paycheck before it.

    **This class asserted the opposite until plan step C2-b2, and the reversal
    IS the step** (plan ledger rows **D7** / **P27**).  Pay periods were not
    contiguous by construction, so a day could belong to no paycheck; the
    engine answered ``PlacementOutcome.SCHEDULE_GAP``, logged
    ``recurrence_occurrence_unplaced`` at WARNING naming the orphaned dates,
    and generated no row for them.  C2-b2 pointed the engine at the DERIVED
    calendar, in which a period runs to the day before the next payday.  A hole
    is then not a state a READER can see: the preceding paycheck covers those
    days.  The outcome enum, ``GenerationPlan.gaps`` and
    ``report_schedule_gaps`` went with the state they described.

    **Two writers had to close before the reader could stop looking, and both
    have**: ``balance:X-ad-a`` deleted the registration bootstrap payday, and
    plan step **C3-b** replaced the batch guard with a writer that materialises
    the derivation.  The first test below is the CONTROL for that closure.  The
    stored rows can still HOLD a hole -- written before C3-b, or written
    directly, which is what ``_test_helpers.open_calendar_hole`` does -- so
    what these tests pin now is what a READER does with one.

    **What replaces the alert is a query**: ``scripts/integrity_check.py``
    **BA-07** reports any owner whose stored ``end_date`` is not the day before
    the next payday, and dies with that column at plan step C4.
    ``tests/test_scripts/test_integrity_check.py`` grades it.

    **The absorption is not free, and the third test says so.**  An absorbed
    hole leaves an OVER-LONG paycheck, and a monthly bill can fall inside one
    more than once -- which ``idx_transactions_template_period_scenario``
    cannot hold, so ``refuse_unstorable_repeats`` refuses the pass.  That is
    the same refusal a 30-day-or-longer cadence already earns; plan step C5b
    lifts it.

    **"Not yet" was never a gap**, and the class still ends with the control
    that says so: under ``PERIOD_STARTING_ON_OR_AFTER`` an occurrence dated
    after the LAST PAYDAY has no paycheck to defer onto even on a perfectly
    contiguous schedule, and that is 43% of biweekly schedule openings.  Since
    C2-b2 it is the ONLY way a placement answers ``None``.
    """

    #: Days after the seed schedule's last covered day that the second batch
    #: opens.  Large enough that a whole calendar month (June 2026) falls in
    #: the hole, so a monthly rule has exactly one occurrence with nowhere to
    #: live and the assertions below are about that one date.
    _GAP_DAYS = 43

    #: The rule's scheduling day, and therefore the day every occurrence and
    #: every generated ``due_date`` falls on.
    _DAY_OF_MONTH = 15

    def _schedule_with_a_gap(self, seed_user, seed_periods):
        """Append a second batch, then re-open the hole the writer absorbs.

        The append is still the real writer's, so the schedule's SHAPE is one
        the app produces; the last line puts back the hole plan step C3-b's
        recompute closes, because that hole is what these tests are about.
        Doing it in this order rather than hand-inserting every row keeps the
        fixture one line away from the production path.

        Returns:
            ``(later_periods, gap_start, gap_end)`` -- the appended batch and
            the inclusive span of days no period covers.
        """
        last_covered = seed_periods[-1].end_date
        later_start = last_covered + timedelta(days=self._GAP_DAYS)
        later = pay_period_write.record_paydays(
            user_id=seed_user["user"].id,
            first_payday=later_start,
            num_periods=6,
            cadence_days=14,
        )
        gap_start, gap_end = open_calendar_hole(
            db.session, seed_periods[-1], last_covered,
        )
        return later, gap_start, gap_end

    def _days_between(self, first, last, day=None):
        """Every *day* of the month in ``first..last``, inclusive, ascending.

        Args:
            first: Inclusive lower bound.
            last: Inclusive upper bound.
            day: Day of the month, defaulting to :data:`_DAY_OF_MONTH`.

        Returns:
            The matching dates, ascending.
        """
        day = self._DAY_OF_MONTH if day is None else day
        return [
            date(year, month, day)
            for year in range(first.year, last.year + 1)
            for month in range(1, 13)
            if first <= date(year, month, day) <= last
        ]

    def test_the_writer_no_longer_leaves_a_gapped_batch(
        self, app, db, seed_user, seed_periods,
    ):
        """The CONTROL: appending a late batch now absorbs the days it skips.

        This test used to assert the opposite -- that the real writer leaves a
        hole -- and said in its own docstring that it would "go red and say so"
        if the writer were ever tightened to refuse gaps (finding **F-10**).
        Plan step **C3-b** tightened it, this went red, and the assertion is
        inverted rather than deleted so the closure has a control of its own.

        The days are checked over the WHOLE span rather than its first day: a
        writer that closed the hole's opening and left its middle uncovered
        would satisfy a single-day check.
        """
        with app.app_context():
            last_covered = seed_periods[-1].end_date
            later_start = last_covered + timedelta(days=self._GAP_DAYS)
            later = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=later_start,
                num_periods=6,
                cadence_days=14,
            )
            assert later[0].start_date == later_start

            # The days the OLD writer would have left behind.
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            day = last_covered + timedelta(days=1)
            while day < later_start:
                assert any(
                    period.start_date <= day <= period.end_date
                    for period in periods
                ), f"{day} is covered by no pay period"
                day += timedelta(days=1)
            # And it is the PRECEDING paycheck that absorbed them, stretched to
            # the day before the new payday rather than left at its old end.
            # Read off the re-queried list: the fixture's own objects were
            # built in an earlier app context and do not see the UPDATE.
            preceding = next(
                p for p in periods if p.start_date == seed_periods[-1].start_date
            )
            assert preceding.end_date == later_start - timedelta(days=1)

    def test_the_calendar_absorbs_the_hole_into_the_preceding_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """The occurrence in the hole is SEATED, not skipped.

        Read off the generation seam's own plan, which is where the fact lives:
        every occurrence the rule names has a period, and the one dated inside
        the hole carries the paycheck that opened before it.  This same fixture
        produced a placement with NO period before plan step C2-b2.

        A day-5 rule rather than the class's day-15 one, deliberately.  The
        absorbing paycheck opens 2026-05-08 and derives an end of 2026-07-02,
        so the 5th falls inside it exactly ONCE (2026-06-05: May's is in the
        previous paycheck and July's in the next).  This test is therefore
        about the SEATING, and the next one -- where two occurrences of the 15th land in
        that one paycheck -- is about the repeat.
        """
        absorbed_day = 5
        with app.app_context():
            _later, gap_start, gap_end = self._schedule_with_a_gap(
                seed_user, seed_periods,
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=absorbed_day,
            )
            schedule = GenerationSchedule.for_user(template.user_id)
            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, None,
                block_message="test",
            )

            absorbing = next(
                period
                for period in pay_period_service.get_all_periods(
                    seed_user["user"].id,
                )
                if period.start_date == seed_periods[-1].start_date
            )
            # The premise, asserted rather than assumed: the fixture really
            # does name an occurrence inside the hole.  Without it the
            # assertions below could pass over an empty list.
            in_hole = [
                row for row in plan.placements
                if gap_start <= row.occurrence <= gap_end
            ]
            assert len(in_hole) == 1, (
                f"the fixture must name exactly one occurrence in the hole "
                f"{gap_start}..{gap_end}, got "
                f"{[row.occurrence for row in plan.placements]}"
            )
            assert in_hole[0].period.id == absorbing.id
            assert absorbing.end_date < in_hole[0].occurrence, (
                "the STORED end must still precede the occurrence -- that is "
                "what makes this an absorption by the derivation rather than a "
                "period that genuinely contains the day"
            )

            # **The COUNT, and it is load-bearing** -- the assertion an
            # adversarial review of plan step C2-b2 caught this test dropping
            # when it replaced the gap-era one.  Without it a pass that seated
            # the absorbed occurrence and dropped the other eight would look
            # identical to a correct one, which is the exact mutant the class
            # was burned by before.  Derived from the calendar the engine
            # itself walks, so a fixture change moves both together.
            named = self._days_between(
                schedule.calendar.opening_bound(),
                schedule.calendar.horizon(),
                day=absorbed_day,
            )
            assert [row.occurrence for row in plan.placements] == named
            # And nothing is left homeless: every occurrence has a paycheck,
            # each inside the DERIVED span of the one it was seated in.
            assert all(row.period is not None for row in plan.placements)
            for row in plan.placements:
                derived = schedule.calendar.period_by_id(row.period.id)
                assert derived.start_date <= row.occurrence <= derived.end_date

    def test_the_regenerate_sweep_and_the_regeneration_share_ONE_period_end(
        self, app, db, seed_user, seed_periods,
    ):
        """The bound both halves of a regenerate read must be the same column.

        ``regenerate_for_template`` DELETES every non-overridden row whose pay
        period ends on or after ``effective_from`` and then regenerates from
        the same bound.  The delete half is SQL over ``pay_periods.end_date``
        -- the STORED column
        (``_recurrence_common.query_rows_from_effective_date``).  Plan step
        C2-b2 gave the recurrence engine a calendar whose ends are DERIVED, and
        an adversarial review caught the regeneration half reading THAT end
        instead: two independently-sourced answers to "when does this paycheck
        end", compared against one date.

        Where they disagree and the bound falls between them the failure is
        silent and asymmetric -- rows deleted and never recreated when the
        derived end is EARLIER (the shrunk-cadence shape, plan ledger row
        **P28**), a stale amount surviving an edit when it is LATER (the
        absorbed hole, row **P27**).  ``resolve_generation_plan`` now resolves
        the ORM row before applying the bound, so both halves read one column.

        This is the LATER case, which is the one this fixture can build: the
        absorbing paycheck's stored end is 2026-05-21 and its derived end
        2026-07-02, so a bound of 2026-06-01 falls between them.
        """
        with app.app_context():
            self._schedule_with_a_gap(seed_user, seed_periods)
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=5,
            )
            absorbing = next(
                period
                for period in pay_period_service.get_all_periods(
                    seed_user["user"].id,
                )
                if period.start_date == seed_periods[-1].start_date
            )
            bound = date(2026, 6, 1)

            # The premise: the bound really does fall between the two ends.
            assert absorbing.end_date < bound
            assert GenerationSchedule.for_user(
                template.user_id,
            ).calendar.period_by_id(absorbing.id).end_date >= bound

            plan = recurrence_engine.resolve_generation_plan(
                template, GenerationSchedule.for_user(template.user_id),
                seed_user["scenario"].id, bound, block_message="test",
            )

            # The DELETE sweep would not collect this period's rows, because
            # its STORED end precedes the bound.  The regeneration must not
            # write into it either.
            assert absorbing.id not in {
                row.period.id for row in plan.placements
            }

    def test_an_absorbed_occurrence_is_DATED_by_its_paycheck_not_its_cadence(
        self, app, db, seed_user, seed_periods,
    ):
        """Plan ledger row **D18**, reached through a door plan step C2-b2 opens.

        **This asserts a DEFECT, deliberately, so the step that fixes it has a
        control.**  ``compute_due_date`` dates a generated row by scanning the
        two ENDPOINT months of the paycheck it landed in, and the occurrence
        date the walk actually found is discarded.  When a paycheck absorbs a
        hole it spans months neither endpoint names, so the row is dated in the
        wrong month entirely.  Measured here: the 2026-06-05 occurrence is
        seated in the 2026-05-08 paycheck and generated with
        ``due_date = 2026-05-05`` -- a month early, and colliding with the date
        the PREVIOUS paycheck's row already carries.

        Before C2-b2 this occurrence produced no row at all (it fell in the
        hole, was logged, and was skipped), so the wrong date is new even
        though the defect is not: a 30-day-or-longer cadence already reaches it
        without any hole.  **Recurrence plan step R5 owns the fix** -- it splits
        a generated row's dates into ``occurs_on`` (the cadence),
        ``pay_period_id`` (the funding) and ``due_on`` (the installment), and
        deletes ``compute_due_date`` -- and this test goes red when it lands,
        which is what it is for.

        Not fixed here: the repair changes every generated row's date and would
        move the frozen 430-shape baseline, which this step must leave
        byte-identical.
        """
        absorbed_day = 5
        with app.app_context():
            _later, gap_start, gap_end = self._schedule_with_a_gap(
                seed_user, seed_periods,
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=absorbed_day,
            )
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(template.user_id),
                seed_user["scenario"].id,
            )

            absorbing = next(
                period
                for period in pay_period_service.get_all_periods(
                    seed_user["user"].id,
                )
                if period.start_date == seed_periods[-1].start_date
            )
            seated = [
                txn for txn in created if txn.pay_period_id == absorbing.id
            ]
            assert len(seated) == 1, (
                "the fixture must seat exactly one absorbed occurrence in the "
                "paycheck that swallowed the hole"
            )
            # The occurrence the cadence named, for the record.
            assert gap_start <= date(2026, 6, absorbed_day) <= gap_end
            # ...and the date the row actually carries, which is not it.
            assert seated[0].due_date == date(2026, 5, absorbed_day)
            # The collision that makes it visible: two rows, two paychecks,
            # one date.
            assert [txn.due_date for txn in created].count(
                date(2026, 5, absorbed_day),
            ) == 2

    def test_an_absorbed_hole_can_make_one_paycheck_owe_a_bill_twice(
        self, app, db, seed_user, seed_periods,
    ):
        """The cost of absorbing, refused rather than written.

        The hole spans a whole calendar month, so the paycheck that absorbs it
        covers the 15th twice.  ``idx_transactions_template_period_scenario`` is
        UNIQUE over ``(template, pay_period, scenario)``, so writing both would
        raise an ``IntegrityError`` naming nothing and roll back whatever
        transaction it was inside.  ``refuse_unstorable_repeats`` refuses
        first, names the definition, the paycheck and both dates, and writes
        NOTHING -- the same refusal a 30-day-or-longer cadence already earns.

        **This is ONE of the ways plan step C2-b2 moves money, not the only
        one**, and an adversarial review of this step refuted the claim that it
        was.  Wherever a stored column disagrees with the payday derivation the
        engine now believes the derivation, and there are THREE such columns --
        the hole this test builds (plan ledger row **P27**), the stored cadence
        against the last stored end (**P28**), and a stored ordinal that is not
        ``0..n-1`` (**P26**).  ``recurrence/_occurrence.py``'s module docstring
        states all three and what each one costs.

        None is reachable through a live door: ``pay_period_write`` writes the
        derivation over the whole payday list on every write and REPAIRS such a
        row, so each means data written before plan step C3-b or edited outside
        that module.  Production carries none of the three (61 contiguous
        periods, 0 index mismatches, 0 end mismatches, measured 2026-08-10).
        Plan step C5b re-keys the index and lifts this particular refusal.
        """
        with app.app_context():
            _later, gap_start, gap_end = self._schedule_with_a_gap(
                seed_user, seed_periods,
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=self._DAY_OF_MONTH,
            )
            schedule = GenerationSchedule.for_user(template.user_id)

            with pytest.raises(RecurrenceCadenceUnsupported) as excinfo:
                recurrence_engine.generate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )

            # The refusal names BOTH dates: the one the hole used to swallow,
            # and the one the absorbing paycheck already owed.
            in_hole = self._days_between(gap_start, gap_end)
            assert len(in_hole) == 1, (
                f"the fixture must put exactly one {self._DAY_OF_MONTH}th in "
                f"the hole {gap_start}..{gap_end}, got {in_hole}"
            )
            assert in_hole[0] in excinfo.value.occurrence_dates
            assert len(excinfo.value.occurrence_dates) == 2
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == 0, "a refused pass must write nothing"

    def test_a_contiguous_schedule_past_its_last_payday_places_nothing_there(
        self, app, db, seed_user, seed_periods,
    ):
        """The control that matters: "not yet" is ordinary, and now unique.

        A ``Monthly First`` rule places on the first paycheck STARTING on or
        after the 1st of each month, so an occurrence after the last payday has
        nothing to defer onto -- and the schedule below is CONTIGUOUS, built by
        the real writer, with its final period straddling a month boundary so
        that case is reached.  Plan step R4b-2's first draft reported it as a
        corrupt schedule; two neutral reviews measured that at 43% of biweekly
        schedule openings.  Since plan step C2-b2 it is the ONLY way a
        placement answers ``None``, and this is the case that goes red if a
        second one is ever reintroduced.

        A ``Monthly`` (CONTAINING_DATE) rule cannot exercise it -- the first
        draft's control used one, which is why the defect survived a green
        suite.
        """
        with app.app_context():
            # One more period, CONTIGUOUS with the seed batch, chosen so the
            # last period spans 2026-05-22..2026-06-04 -- across a month
            # boundary, so the 1st of June falls after the last payday while
            # still inside the schedule's covered span.
            tail = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=seed_periods[-1].end_date + timedelta(days=1),
                num_periods=1,
                cadence_days=14,
            )
            db.session.flush()
            last = tail[-1]
            assert last.start_date.month != last.end_date.month, (
                "the control needs a final period straddling a month boundary"
            )

            template = self._make_template_with_rule(seed_user, "Monthly First")
            schedule = GenerationSchedule.for_user(template.user_id)
            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, None,
                block_message="test",
            )
            placements = rule_occurrences(
                template.recurrence_rule, schedule.calendar,
            )
            # The premise: the rule really does name an occurrence with no
            # paycheck to defer onto.  Without this the assertions below could
            # simply mean nothing was unplaceable.
            unplaceable = [
                placement for placement in placements
                if placement.period is None
            ]
            assert len(unplaceable) == 1, (
                f"the control must exercise an unplaceable occurrence, got "
                f"{[p.occurrence for p in unplaceable]}"
            )
            # Past the last PAYDAY and still inside the covered span -- exactly
            # the shape the deleted SCHEDULE_GAP branch used to misread.
            assert unplaceable[0].occurrence > last.start_date
            assert schedule.calendar.period_containing(
                unplaceable[0].occurrence,
            ) is not None

            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )

            assert created, "the control generated nothing to be a control over"
            # The unplaceable occurrence is simply absent from the plan; it is
            # not reported, and it is not written anywhere.
            assert [row.occurrence for row in plan.placements] == [
                placement.occurrence for placement in placements
                if placement.period is not None
            ]

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a template + recurrence rule."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
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
            name="Gap Bill",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        db.session.refresh(template)
        return template


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
            .filter_by(name="Expense")
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

    def _record_purchase(self, txn, seed_user, amount="12.79"):
        """Record one purchase against *txn*, as the entry door would.

        Written straight onto the model rather than through
        ``entry_service.create_entry`` on purpose: what these tests are about
        is what a REGENERATION does to a purchase that is already there, so
        going through the creation door would only add its own guards to the
        thing under test.
        """
        entry = TransactionEntry(
            transaction_id=txn.id,
            account_id=txn.account_id,
            user_id=seed_user["user"].id,
            amount=Decimal(amount),
            description="Kroger",
            purchased_on=txn.pay_period.start_date,
        )
        db.session.add(entry)
        db.session.flush()
        return entry

    def test_regeneration_keeps_the_purchases_recorded_against_a_row(
        self, app, db, seed_user, seed_periods
    ):
        """A template edit must not destroy what the owner recorded buying.

        **Finding N-292, the defect this step closes** (ruling **R-R19**).
        Measured on a production clone before the fix: editing template 38
        ("Groceries") destroyed txn 2282 and CASCADE took its 3 purchase
        records worth ``$499.82`` with it, with no prompt and an
        ``overridden_conflict_count`` of 0.

        Three assertions, and each fails against the old delete-and-recreate:
        the purchase still exists, it still belongs to the SAME row (the old
        code gave the period a new row with a new id), and the row was re-priced
        rather than rebuilt.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, "Every Period")
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_periods(template.user_id, seed_periods),
                seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on = created[0]
            txn_id = spent_on.id
            entry_id = self._record_purchase(spent_on, seed_user).id

            template.default_amount = Decimal("200.00")
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_periods(template.user_id, seed_periods),
                seed_user["scenario"].id,
            )
            db.session.flush()

            entry = db.session.get(TransactionEntry, entry_id)
            assert entry is not None, "the purchase was destroyed"
            assert entry.transaction_id == txn_id
            assert entry.amount == Decimal("12.79")

            txn = db.session.get(Transaction, txn_id)
            assert txn is not None, "the row the purchase hangs off was destroyed"
            assert txn.estimated_amount == Decimal("200.00")

    def test_a_row_the_rule_no_longer_names_is_retained_when_it_holds_a_purchase(
        self, app, db, seed_user, seed_periods
    ):
        """Clearing a recurrence must not take a part-spent envelope with it.

        The second shape of **N-292**: with no rule left, every existing row is
        an orphan, and the old sweep deleted each one.  A row carrying the
        owner's purchases is now RETAINED instead -- untouched, and named in
        the raise so the route can say so.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, "Every Period")
            schedule = GenerationSchedule.for_periods(
                template.user_id, seed_periods,
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on, untouched = created[0], created[1]
            spent_id, empty_id = spent_on.id, untouched.id
            entry_id = self._record_purchase(spent_on, seed_user).id

            # "Does not repeat" -- the rule is gone, so the rule names nothing.
            template.recurrence_rule = None
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.retained == [spent_id]
            assert db.session.get(TransactionEntry, entry_id) is not None
            assert db.session.get(Transaction, spent_id) is not None
            # The CONTROL: an orphan with nothing on it is still removed, so
            # the retention above is the purchase's doing and not a blanket
            # refusal to delete.
            assert db.session.get(Transaction, empty_id) is None

    def test_an_account_move_retains_a_row_that_holds_a_purchase(
        self, app, db, seed_user, seed_periods
    ):
        """Repointing a template's account must not drag purchases silently.

        The developer's ruling on the third shape: a purchase's account IS its
        parent's (``fk_transaction_entries_parent_account``) and its statement
        link is scoped BY account (``fk_transaction_entries_reconciled_by``), so
        moving the row moves the purchases and invalidates what cleared them.
        The pass leaves such a row exactly as it found it and reports it.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, "Every Period")
            schedule = GenerationSchedule.for_periods(
                template.user_id, seed_periods,
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on, untouched = created[0], created[1]
            spent_id, empty_id = spent_on.id, untouched.id
            original_account_id = spent_on.account_id
            self._record_purchase(spent_on, seed_user)

            moved_to = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Second Checking",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(moved_to)
            db.session.flush()
            template.account_id = moved_to.id
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.retained == [spent_id]
            assert db.session.get(Transaction, spent_id).account_id == (
                original_account_id
            )
            # The CONTROL: a row with no purchases DOES follow the template, so
            # the retention is the purchase's doing and the move still works.
            assert db.session.get(Transaction, empty_id).account_id == moved_to.id

    def test_a_note_alone_retains_an_orphaned_row(
        self, app, db, seed_user, seed_periods
    ):
        """Purchases are not the only thing a template cannot reconstruct.

        ``notes`` is free text the owner typed and no writer derives, so the
        old delete-and-recreate dropped it exactly as it dropped purchases.
        Measured at 0 rows on the production clone -- luck, not safety, which
        is why the predicate covers it rather than waiting for the first note.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, "Every Period")
            schedule = GenerationSchedule.for_periods(
                template.user_id, seed_periods,
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            annotated, blank = created[0], created[1]
            annotated_id, blank_id = annotated.id, blank.id
            annotated.notes = "split with Kayla"
            # The CONTROL for the whitespace arm: a note that is only spaces is
            # not a record worth blocking an edit over.
            blank.notes = "   "
            db.session.flush()

            template.recurrence_rule = None
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.retained == [annotated_id]
            assert db.session.get(Transaction, annotated_id) is not None
            assert db.session.get(Transaction, blank_id) is None

    def test_regenerate_maintains_unmodified_rows_in_place(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with a changed amount re-prices the SAME rows.

        **RE-RULED at plan step R10-a** (ruling **R-R19**, finding **N-292**).
        It asserted the opposite -- ``len(new_created) == 10`` and ``txn.id not
        in old_ids``, that is, that a regeneration DESTROYS every row and
        builds replacements.  That contract is what took the purchases recorded
        against a part-spent envelope with it, since ``transaction_entries``
        CASCADE from their parent, so the developer ruled it out: a row the
        rule still names is now maintained, keeping its id and everything
        hanging off it.

        The assertion is STRONGER than the one it replaces, not weaker.  The
        old one could not tell a re-priced row from a rebuilt one -- both give
        ten rows at $200.00 -- and this pins the identity of all ten.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period"
            )

            # Generate initial entries.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()
            old_ids = sorted(txn.id for txn in created)
            assert len(old_ids) == 10

            # Change the template amount.
            template.default_amount = Decimal("200.00")
            db.session.flush()

            # Regenerate.  Every period the rule names already holds the rule's
            # own row, so there is nothing to CREATE and the return is empty.
            new_created = recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()
            assert new_created == []

            surviving = (
                db.session.query(Transaction)
                .filter(Transaction.template_id == template.id)
                .all()
            )
            assert sorted(txn.id for txn in surviving) == old_ids
            for txn in surviving:
                assert txn.estimated_amount == Decimal("200.00")

    def test_regenerate_raises_conflict_for_deleted_entries(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with soft-deleted entry raises RecurrenceConflict."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period"
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            # Soft-delete one entry.
            deleted_id = created[0].id
            created[0].is_deleted = True
            db.session.flush()

            # Regenerate -- should raise with deleted list.
            with pytest.raises(RecurrenceConflict) as exc_info:
                recurrence_engine.regenerate_for_template(
                    template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
                )

            assert deleted_id in exc_info.value.deleted


class TestResolveConflicts:
    """DB integration tests for resolve_conflicts()."""

    def _make_template_with_rule(
        self, seed_user, pattern_name, category_key=None, **rule_kwargs
    ):
        """Helper: create a template + recurrence rule."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
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

        # Default to "Car Payment" but allow override for fixtures with
        # different category sets (e.g. second_user).
        if category_key is None:
            category_key = "Car Payment"
        category = seed_user["categories"][category_key]

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=category.id,
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
                seed_user, "Every Period"
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            txn = created[0]
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            # Resolve as 'keep'.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="keep", user_id=seed_user["user"].id,
            )
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
                seed_user, "Every Period"
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            txn = created[0]
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            # Resolve as 'update' with new amount.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("200.00"),
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
                seed_user, "Every Period"
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry with a custom amount.
            txn = created[0]
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            # Resolve as 'update' with no new amount.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=None,
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            assert txn.is_deleted is False
            # Amount unchanged since new_amount was None.
            assert txn.estimated_amount == Decimal("999.99")

    def test_cross_user_update_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """update with wrong user_id silently skips the transaction."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period"
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            # Attempt resolve as second_user -- should be blocked.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                user_id=second_user["user"].id,
                new_amount=Decimal("50.00"),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is True
            assert txn.estimated_amount == Decimal("999.99")

    def test_cross_user_keep_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """keep with wrong user_id leaves transaction unchanged."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period"
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            # 'keep' with wrong user -- no-op by design (keep never modifies).
            recurrence_engine.resolve_conflicts(
                [txn.id], action="keep",
                user_id=second_user["user"].id,
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is True
            assert txn.estimated_amount == Decimal("999.99")

    def test_same_user_update_succeeds(
        self, app, db, seed_user, seed_periods
    ):
        """update with correct user_id modifies the transaction."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period"
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("50.00"),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            assert txn.estimated_amount == Decimal("50.00")

    def test_mixed_ownership_list(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """Only owned transactions are modified in a mixed-ownership list."""
        with app.app_context():
            # Create template and transaction for user A.
            template_a = self._make_template_with_rule(
                seed_user, "Every Period"
            )
            created_a = recurrence_engine.generate_for_template(
                template_a, GenerationSchedule.for_periods(template_a.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()
            txn_a = created_a[0]
            txn_a.is_override = True
            txn_a.estimated_amount = Decimal("999.99")

            # Create template and transaction for user B (second_user).
            # second_user needs their own periods and template.
            from app.services import pay_period_service
            periods_b = pay_period_write.record_paydays(
                user_id=second_user["user"].id,
                first_payday=seed_periods[0].start_date,
                num_periods=10, cadence_days=14,
            )
            template_b = self._make_template_with_rule(
                second_user, "Every Period", category_key="Rent",
            )
            created_b = recurrence_engine.generate_for_template(
                template_b, GenerationSchedule.for_periods(template_b.user_id, periods_b), second_user["scenario"].id,
            )
            db.session.flush()
            txn_b = created_b[0]
            txn_b.is_override = True
            txn_b.estimated_amount = Decimal("888.88")
            db.session.flush()

            # Resolve as user A -- only txn_a should be modified.
            recurrence_engine.resolve_conflicts(
                [txn_a.id, txn_b.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("50.00"),
            )
            db.session.flush()

            db.session.refresh(txn_a)
            db.session.refresh(txn_b)
            assert txn_a.is_override is False
            assert txn_a.estimated_amount == Decimal("50.00")
            assert txn_b.is_override is True
            assert txn_b.estimated_amount == Decimal("888.88")


class TestResolveConflictsShadowGuard:
    """C-20 / F-007: ``resolve_conflicts`` must refuse to mutate
    transfer shadow transactions (``transfer_id IS NOT NULL``).

    These tests close CLAUDE.md Transfer invariant 4 (no code path
    directly mutates a shadow -- all transfer mutations route through
    ``transfer_service``).  Pre-C-20, the convention was enforced
    only by reviewer discipline; the per-row loop in
    ``recurrence_engine.resolve_conflicts`` had no defensive check
    and would silently desynchronise a shadow from its transfer
    parent if a shadow ID ever appeared in the conflict list.
    """

    def _create_transfer_with_shadows(self, seed_user, seed_periods):
        """Helper: build a transfer + its two shadow rows.

        Creates a savings ``Account``, the two default
        ``Transfers: Incoming`` / ``Transfers: Outgoing`` categories
        (which the ``seed_user`` fixture does not include), and a
        single transfer in the first period.  Returns the resulting
        transfer plus its two shadows so the test can drive
        ``resolve_conflicts`` against them.
        """
        from app.models.account import Account  # pylint: disable=import-outside-toplevel
        from app.models.category import Category  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel
        from app.models.ref import AccountType  # pylint: disable=import-outside-toplevel

        savings_type = (
            db.session.query(AccountType).filter_by(name="Savings").one()
        )
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                anchor_balance=Decimal("0.00"),
            ),
        )
        db.session.add(savings)

        outgoing = Category(
            user_id=seed_user["user"].id,
            group_name="Transfers",
            item_name="Outgoing",
        )
        incoming = Category(
            user_id=seed_user["user"].id,
            group_name="Transfers",
            item_name="Incoming",
        )
        db.session.add_all([outgoing, incoming])
        db.session.flush()

        projected = (
            db.session.query(Status).filter_by(name="Projected").one()
        )

        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("100.00"),
                status_id=projected.id,
                category_id=outgoing.id,
            ),
        )
        db.session.flush()

        shadows = (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer.id)
            .all()
        )
        assert len(shadows) == 2, (
            "Pre-condition: transfer must have exactly two shadows."
        )
        return xfer, shadows

    def test_update_action_on_shadow_raises_validation_error(
        self, app, db, seed_user, seed_periods,
    ):
        """A shadow ID with action='update' raises ValidationError and
        leaves the shadow unchanged.

        The guard must surface as a hard failure rather than a silent
        skip: silently skipping would let a buggy caller pass shadow
        IDs unnoticed and ship a regression that desyncs the parent
        transfer's amount/status/period from its sibling shadow.
        """
        with app.app_context():
            _, shadows = self._create_transfer_with_shadows(
                seed_user, seed_periods,
            )
            shadow = shadows[0]
            shadow_id = shadow.id
            original_amount = shadow.estimated_amount
            original_override = shadow.is_override
            original_deleted = shadow.is_deleted
            # Commit so the shadow survives the post-raise rollback
            # below.  Without the commit, rollback would undo the
            # transfer/shadow creation along with the failed mutation.
            db.session.commit()

            with pytest.raises(ValidationError, match="transfer shadow"):
                recurrence_engine.resolve_conflicts(
                    [shadow_id],
                    action="update",
                    user_id=seed_user["user"].id,
                    new_amount=Decimal("9999.99"),
                )

            db.session.rollback()
            shadow_after = db.session.get(Transaction, shadow_id)
            assert shadow_after is not None
            assert shadow_after.estimated_amount == original_amount
            assert shadow_after.is_override == original_override
            assert shadow_after.is_deleted == original_deleted

    def test_cross_user_shadow_silently_skipped(
        self, app, db, seed_user, seed_periods, second_user,
    ):
        """Cross-user requests for a shadow ID stay silent.

        Defense-in-depth: the existing cross-user check (silent skip
        + ACCESS event) must take precedence over the new shadow
        guard so an attacker probing for shadow IDs cannot
        distinguish ``shadow + cross-user`` from ``not-found +
        cross-user``.  Both must look identical to the caller.
        """
        with app.app_context():
            _, shadows = self._create_transfer_with_shadows(
                seed_user, seed_periods,
            )
            shadow = shadows[0]
            shadow_id = shadow.id
            original_amount = shadow.estimated_amount

            # Calling as second_user must NOT raise -- cross-user
            # silent skip wins over the shadow guard.
            recurrence_engine.resolve_conflicts(
                [shadow_id],
                action="update",
                user_id=second_user["user"].id,
                new_amount=Decimal("9999.99"),
            )
            db.session.flush()

            shadow_after = db.session.get(Transaction, shadow_id)
            assert shadow_after.estimated_amount == original_amount

    def test_regular_transaction_still_resolves_after_guard(
        self, app, db, seed_user, seed_periods,
    ):
        """Sanity: the C-20 guard does not regress regular-transaction
        resolution.

        With a non-shadow transaction
        (``transfer_id IS NULL``), ``resolve_conflicts`` must keep
        applying its existing semantics: clear flags and apply the
        new amount.  Companion to ``TestResolveConflicts`` -- this
        test exists explicitly under the C-20 class so a future
        refactor of the shadow guard cannot accidentally break the
        baseline path.
        """
        with app.app_context():
            pattern = (
                db.session.query(RecurrencePattern)
                .filter_by(name="Every Period")
                .one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense")
                .one()
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=pattern.id,
            )
            db.session.add(rule)
            db.session.flush()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Car Payment"].id,
                recurrence_rule_id=rule.id,
                transaction_type_id=expense_type.id,
                name="Regular Recurring",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()
            txn = created[0]
            assert txn.transfer_id is None, (
                "Pre-condition: regular transaction must not be a shadow."
            )
            txn.is_override = True
            txn.estimated_amount = Decimal("999.99")
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id],
                action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("50.00"),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            assert txn.is_deleted is False
            assert txn.estimated_amount == Decimal("50.00")


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
            .filter_by(name="Expense")
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
                seed_user, "Every Period"
            )

            # SECURITY: Attempt to generate into user B's
            # scenario using user A's template. This should
            # be rejected but currently is not.
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_periods(template.user_id, seed_periods),
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
            .filter_by(name="Expense")
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
                seed_user, "Every Period", default_amount=Decimal("0.00")
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
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
                .filter_by(name="Expense")
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
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
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
                seed_user, "Every Period"
            )

            # Initial generation.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Mark the 3rd transaction as received.
            received_status = (
                db.session.query(Status).filter_by(name="Received").one()
            )
            target_txn = created[2]
            target_id = target_txn.id
            target_txn.status_id = received_status.id
            db.session.flush()

            # Regenerate -- received transaction must survive.
            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            # Verify the received transaction still exists unchanged.
            preserved = db.session.get(Transaction, target_id)
            assert preserved is not None, (
                f"Received transaction {target_id} was deleted during regeneration"
            )
            assert preserved.status.name == "Received"
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
                seed_user, "Every Period"
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, []), seed_user["scenario"].id,
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
                seed_user, "Every Period"
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Cancel one transaction.
            cancelled_status = (
                db.session.query(Status).filter_by(name="Cancelled").one()
            )
            target_txn = created[4]
            target_id = target_txn.id
            target_txn.status_id = cancelled_status.id
            db.session.flush()

            # Regenerate.
            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            # Cancelled transaction must still exist.
            preserved = db.session.get(Transaction, target_id)
            assert preserved is not None, (
                f"Cancelled transaction {target_id} was deleted "
                f"during regeneration"
            )
            assert preserved.status.name == "Cancelled"
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
                seed_user, "Every Period"
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Mark one as credit.
            credit_status = (
                db.session.query(Status).filter_by(name="Credit").one()
            )
            target_txn = created[6]
            target_id = target_txn.id
            target_txn.status_id = credit_status.id
            db.session.flush()

            # Regenerate.
            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            # Credit transaction must still exist.
            preserved = db.session.get(Transaction, target_id)
            assert preserved is not None, (
                f"Credit transaction {target_id} was deleted "
                f"during regeneration"
            )
            assert preserved.status.name == "Credit"
            assert preserved.id == target_id


class TestPaycheckAmountFallback:
    """Tests for _get_transaction_amount exception narrowing (C-01).

    Verifies that the recurrence engine only catches specific recoverable
    exceptions from the paycheck calculator, and lets unexpected errors
    propagate instead of silently falling back to template.default_amount.
    """

    @staticmethod
    def _fake_tax_configs(*args, **kwargs):
        """Stub the RESOLVING loader to avoid DB hits in unit tests.

        Patched at ``load_tax_configs_for_year`` -- the function
        ``_get_transaction_amount`` actually calls -- rather than at the
        exact-year primitive beneath it.  The resolver reads the profile's
        filing status and state to build its candidate set, which these
        deliberately minimal fakes do not carry; stubbing the entry point
        keeps the test hermetic and on the narrowing behaviour it is about.
        """
        return {
            "bracket_set": "fake", "state_config": "fake",
            "fica_config": "fake",
        }

    def test_returns_default_on_zero_division(
        self, app, db, seed_user, monkeypatch
    ):
        """ZeroDivisionError from paycheck calc falls back to default_amount."""
        monkeypatch.setattr(
            "app.services.paycheck_calculator.calculate_paycheck",
            lambda *a, **kw: (_ for _ in ()).throw(
                ZeroDivisionError("division by zero")),
        )
        monkeypatch.setattr(
            "app.services.tax_config_service.load_tax_configs_for_year",
            self._fake_tax_configs,
        )
        from app.services.recurrence_engine._amounts import _get_transaction_amount

        class FakeTemplate:
            default_amount = Decimal("1500.00")

        class FakeProfile:
            id = 1
            user_id = seed_user["user"].id
            calibration = None

        class FakePeriod:
            start_date = date(2026, 1, 2)

        result = _get_transaction_amount(
            FakeTemplate(), FakeProfile(), FakePeriod(), []
        )
        assert result == Decimal("1500.00")

    def test_returns_default_on_invalid_operation(
        self, app, db, seed_user, monkeypatch
    ):
        """InvalidOperation from bad Decimal data falls back to default_amount."""
        from decimal import InvalidOperation

        def _boom(*args, **kwargs):
            raise InvalidOperation("bad decimal")

        monkeypatch.setattr(
            "app.services.paycheck_calculator.calculate_paycheck", _boom,
        )
        monkeypatch.setattr(
            "app.services.tax_config_service.load_tax_configs_for_year",
            self._fake_tax_configs,
        )
        from app.services.recurrence_engine._amounts import _get_transaction_amount

        class FakeTemplate:
            default_amount = Decimal("2000.00")

        class FakeProfile:
            id = 1
            user_id = seed_user["user"].id
            calibration = None

        class FakePeriod:
            start_date = date(2026, 1, 2)

        result = _get_transaction_amount(
            FakeTemplate(), FakeProfile(), FakePeriod(), []
        )
        assert result == Decimal("2000.00")

    def test_propagates_unexpected_exception(
        self, app, db, seed_user, monkeypatch
    ):
        """Unexpected exceptions (e.g., AttributeError) are NOT caught."""
        def _boom(*args, **kwargs):
            raise AttributeError("profile has no attribute 'raises'")

        monkeypatch.setattr(
            "app.services.paycheck_calculator.calculate_paycheck", _boom,
        )
        monkeypatch.setattr(
            "app.services.tax_config_service.load_tax_configs_for_year",
            self._fake_tax_configs,
        )
        from app.services.recurrence_engine._amounts import _get_transaction_amount

        class FakeTemplate:
            default_amount = Decimal("1500.00")

        class FakeProfile:
            id = 1
            user_id = seed_user["user"].id
            calibration = None

        class FakePeriod:
            start_date = date(2026, 1, 2)

        with pytest.raises(AttributeError, match="raises"):
            _get_transaction_amount(
                FakeTemplate(), FakeProfile(), FakePeriod(), []
            )

    def test_returns_default_amount_when_no_salary_profile(self, app, db):
        """When salary_profile is None, returns template.default_amount directly."""
        from app.services.recurrence_engine._amounts import _get_transaction_amount

        class FakeTemplate:
            default_amount = Decimal("500.00")

        result = _get_transaction_amount(FakeTemplate(), None, None, [])
        assert result == Decimal("500.00")


class TestEndDate:
    """Tests for the optional end_date on recurrence rules."""

    def test_end_date_limits_every_period(self, biweekly_periods):
        """end_date stops generation after that date (every_period)."""
        # End date after the 5th period's start_date (period index 4).
        end = biweekly_periods[4].start_date
        rule = build_rule(pattern_name="Every Period", end_date=end)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 5
        for p in matched:
            assert p.start_date <= end

    def test_end_date_none_means_indefinite(self, biweekly_periods):
        """NULL end_date generates for all periods (no change from default)."""
        rule = build_rule(pattern_name="Every Period", end_date=None)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 26

    def test_end_date_with_monthly_pattern(self, biweekly_periods):
        """end_date works with monthly pattern -- only months before end."""
        # End in March 2026.
        rule = build_rule(pattern_name="Monthly", day_of_month=15,
                        end_date=date(2026, 3, 31))
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        # Should get Jan, Feb, Mar only.
        assert len(matched) == 3
        for p in matched:
            assert p.start_date <= date(2026, 3, 31)

    def test_end_date_before_first_period(self, biweekly_periods):
        """end_date before all periods returns empty list."""
        rule = build_rule(pattern_name="Every Period",
                        end_date=date(2025, 12, 31))
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert matched == []

    def test_end_date_with_effective_from_both_filter(self, biweekly_periods):
        """Both effective_from and end_date narrow the window."""
        # effective_from at period 5, end_date at period 10.
        effective_from = biweekly_periods[5].start_date
        end = biweekly_periods[10].start_date
        rule = build_rule(pattern_name="Every Period", end_date=end)

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        # Periods 5 through 10 inclusive.
        assert len(matched) == 6
        for p in matched:
            assert p.start_date >= effective_from
            assert p.start_date <= end

    def test_end_date_mid_period_includes_that_period(self, biweekly_periods):
        """A period whose start_date is on the end_date is included."""
        target_period = biweekly_periods[7]
        rule = build_rule(pattern_name="Every Period",
                        end_date=target_period.start_date)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        # ``rule_occurrences`` answers in the calendar's own ``DerivedPeriod``
        # values since plan step R4b-1, so identity against the fixture's
        # ``FakePeriod`` no longer holds; the schedule ordinal is the stable
        # identity either way.
        assert target_period.period_index in [p.period_index for p in matched]

    def test_end_date_with_every_n_periods(self, biweekly_periods):
        """end_date works correctly with every_n_periods pattern."""
        # Every 3 periods, end at period 12.
        end = biweekly_periods[11].start_date
        rule = build_rule(pattern_name="Every N Periods", interval_n=3,
                        end_date=end)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
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
            .filter_by(name="Expense")
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
                seed_user, "Every Period", end_date=end,
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )

            assert len(created) == 5
            for txn in created:
                period = txn.pay_period
                assert period.start_date <= end

    def test_regenerate_respects_end_date(self, app, db, seed_user, seed_periods):
        """regenerate_for_template leaves an end-dated rule bounded at 3 rows.

        **RE-RULED at plan step R10-a** (ruling **R-R19**): the assertion was
        ``len(regenerated) == 3``, which read the count of rows the pass
        CREATED as the count of rows the rule names.  Those were the same
        number only while a regeneration rebuilt everything it touched.  The
        step's intent -- an ``end_date`` bounds a regeneration exactly as it
        bounds a generation -- is unchanged and is now asserted against the
        rows themselves, which is what it was always about.
        """
        with app.app_context():
            end = seed_periods[2].start_date
            template = self._make_template_with_rule(
                seed_user, "Every Period", end_date=end,
            )

            # Initial generation.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            assert len(created) == 3

            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )
            db.session.flush()

            surviving = (
                db.session.query(Transaction)
                .filter(Transaction.template_id == template.id)
                .all()
            )
            assert len(surviving) == 3
            for txn in surviving:
                assert txn.pay_period.start_date <= end


# --- Due Date Generation Tests -----------------------------------------------


class TestDueDateGeneration:
    """Tests for due_date computation during transaction generation.

    Verifies that generate_for_template correctly computes due_date on
    each created Transaction by delegating to compute_due_date.  Tests
    cover every recurrence pattern, day-of-month clamping for short
    months, the next-month convention for due_day_of_month, and edge
    cases around leap years and month boundaries.
    """

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Create a TransactionTemplate with the given recurrence pattern."""
        from app.models.recurrence_rule import RecurrenceRule
        from app.models.ref import RecurrencePattern, TransactionType
        from app.models.transaction_template import TransactionTemplate

        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )

        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            **rule_kwargs,
        )
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            name="Test Template",
            default_amount=Decimal("100.00"),
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=expense_type.id,
            account_id=seed_user["account"].id,
            recurrence_rule_id=rule.id,
        )
        db.session.add(template)
        db.session.flush()
        db.session.refresh(template)
        return template

    def _make_custom_period(self, seed_user, start, end, index=0):
        """Create a single PayPeriod with the given date range.

        Args:
            seed_user: The seed_user fixture dict.
            start:     Period start_date.
            end:       Period end_date.
            index:     Relative period index among this test's custom
                       periods (default 0).  Stored as ``index + 1`` to
                       clear ``seed_user``'s bootstrap period (which
                       occupies ``period_index`` 0), satisfying the
                       ``uq_pay_periods_user_index`` constraint.  These
                       tests assert on ``due_date`` (date-derived), never
                       on the absolute index, so the offset is invisible
                       to every assertion.

        Returns:
            The created PayPeriod, flushed with an assigned ID.
        """
        from app.models.pay_period import PayPeriod

        period = PayPeriod(
            user_id=seed_user["user"].id,
            start_date=start,
            end_date=end,
            period_index=index + 1,
        )
        db.session.add(period)
        db.session.flush()
        return period

    # -- Basic pattern tests ---------------------------------------------------

    def test_due_date_monthly_pattern(self, app, db, seed_user, seed_periods):
        """Monthly pattern with day_of_month=15 sets due_date to the 15th.

        seed_periods P0 = Jan 2 - Jan 15, which contains Jan 15.
        Expected: txn.due_date == 2026-01-15.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )

            # Find the transaction assigned to the period containing Jan 15.
            jan_txns = [
                txn for txn in created
                if txn.pay_period.start_date <= date(2026, 1, 15) <= txn.pay_period.end_date
            ]
            assert len(jan_txns) == 1
            assert jan_txns[0].due_date == date(2026, 1, 15)

    def test_due_date_every_period_pattern(
        self, app, db, seed_user, seed_periods
    ):
        """Every Period with no day_of_month sets due_date to period.start_date.

        P0 starts Jan 2, so due_date == 2026-01-02.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Every Period",
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )

            assert len(created) == len(seed_periods)
            # First transaction should have due_date == first period's start.
            first_txn = [
                t for t in created if t.pay_period_id == seed_periods[0].id
            ][0]
            assert first_txn.due_date == date(2026, 1, 2)

            # Verify all transactions use their period's start_date.
            period_start_map = {p.id: p.start_date for p in seed_periods}
            for txn in created:
                assert txn.due_date == period_start_map[txn.pay_period_id]

    # -- Month-end clamping tests ----------------------------------------------

    def test_due_date_feb_clamping(self, app, db, seed_user):
        """day_of_month=30 clamped to Feb 28 in a non-leap year (2026).

        Custom period covers all of February to ensure Feb 28 falls inside.
        min(30, 28) = 28, so due_date == 2026-02-28.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 2, 1), date(2026, 2, 28),
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=30,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, [period]), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 2, 28)

    def test_due_date_feb_leap_year(self, app, db, seed_user):
        """day_of_month=29 in Feb 2028 (leap year) gives Feb 29.

        2028 is a leap year, so min(29, 29) = 29.
        due_date == 2028-02-29.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2028, 2, 1), date(2028, 2, 29),
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=29,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, [period]), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2028, 2, 29)

    def test_due_date_day31_in_30day_month(self, app, db, seed_user):
        """day_of_month=31 clamped to 30 in April (30-day month).

        April has 30 days: min(31, 30) = 30.
        due_date == 2026-04-30.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 4, 1), date(2026, 4, 30),
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=31,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, [period]), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 4, 30)

    # -- due_day_of_month (next-month convention) tests ------------------------

    def test_due_day_next_month_convention(
        self, app, db, seed_user, seed_periods
    ):
        """due_day_of_month=1 < day_of_month=22: due date in the next month.

        P1 = Jan 16 - Jan 29, which contains Jan 22. Since due_dom(1) <
        dom(22), the due date rolls to the next month: Feb 1.
        due_date == 2026-02-01.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Monthly",
                day_of_month=22, due_day_of_month=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )

            # P1 (Jan 16-29) contains Jan 22.
            p1_txns = [
                txn for txn in created
                if txn.pay_period_id == seed_periods[1].id
            ]
            assert len(p1_txns) == 1
            assert p1_txns[0].due_date == date(2026, 2, 1)

    def test_due_day_same_month(self, app, db, seed_user):
        """due_day_of_month=15 > day_of_month=1: due date in the same month.

        Custom period Jan 1-14 contains Jan 1. Since due_dom(15) >=
        dom(1), the due date stays in the same month: Jan 15.
        due_date == 2026-01-15.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 1, 1), date(2026, 1, 14),
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly",
                day_of_month=1, due_day_of_month=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, [period]), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 1, 15)

    def test_due_day_dec_to_jan_rollover(self, app, db, seed_user):
        """due_day_of_month=1 < day_of_month=22 in December rolls to Jan next year.

        Custom period Dec 15-28 contains Dec 22. Since due_dom(1) <
        dom(22), the due date rolls to the next month. December + 1 =
        January of the next year. due_date == 2027-01-01.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 12, 15), date(2026, 12, 28),
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly",
                day_of_month=22, due_day_of_month=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, [period]), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2027, 1, 1)

    def test_due_day_null_uses_day_of_month(
        self, app, db, seed_user, seed_periods
    ):
        """due_day_of_month=None falls back to day_of_month for due_date.

        Same behavior as basic monthly: due_date == Jan 15.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Monthly",
                day_of_month=15, due_day_of_month=None,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )

            jan_txns = [
                txn for txn in created
                if txn.pay_period.start_date <= date(2026, 1, 15) <= txn.pay_period.end_date
            ]
            assert len(jan_txns) == 1
            assert jan_txns[0].due_date == date(2026, 1, 15)

    def test_due_day_equals_day_of_month(
        self, app, db, seed_user, seed_periods
    ):
        """due_day_of_month == day_of_month treated as no override.

        When due_day_of_month equals day_of_month, compute_due_date
        takes the 'due_dom is None or due_dom == dom' branch and uses
        day_of_month directly. due_date == Jan 15.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "Monthly",
                day_of_month=15, due_day_of_month=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )

            jan_txns = [
                txn for txn in created
                if txn.pay_period.start_date <= date(2026, 1, 15) <= txn.pay_period.end_date
            ]
            assert len(jan_txns) == 1
            assert jan_txns[0].due_date == date(2026, 1, 15)

    # -- No recurrence rule ----------------------------------------------------

    def test_due_date_no_recurrence_rule(
        self, app, db, seed_user, seed_periods
    ):
        """Template with no recurrence rule returns empty list.

        A template with recurrence_rule_id=None is manually placed and
        generate_for_template returns [] without computing any due_date.
        """
        with app.app_context():
            from app.models.ref import TransactionType
            from app.models.transaction_template import TransactionTemplate

            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense")
                .one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                name="No Rule Template",
                default_amount=Decimal("100.00"),
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                account_id=seed_user["account"].id,
                recurrence_rule_id=None,
            )
            db.session.add(template)
            db.session.flush()
            db.session.refresh(template)

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
            )

            assert created == []

    # -- Quarterly and annual patterns -----------------------------------------

    def test_due_date_quarterly_pattern(self, app, db, seed_user):
        """Quarterly pattern with month_of_year=1 produces due dates in Jan, Apr, Jul, Oct.

        Creates four custom periods -- one covering the 15th of each
        quarterly month -- and asserts each transaction's due_date.
        """
        with app.app_context():
            # Four periods, each spanning the full target month.
            quarters = [
                (date(2026, 1, 1), date(2026, 1, 31)),   # Jan
                (date(2026, 4, 1), date(2026, 4, 30)),   # Apr
                (date(2026, 7, 1), date(2026, 7, 31)),   # Jul
                (date(2026, 10, 1), date(2026, 10, 31)), # Oct
            ]
            periods = [
                self._make_custom_period(seed_user, s, e, idx)
                for idx, (s, e) in enumerate(quarters)
            ]

            template = self._make_template_with_rule(
                seed_user, "Quarterly",
                month_of_year=1, day_of_month=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, periods), seed_user["scenario"].id,
            )

            assert len(created) == 4
            expected_dates = [
                date(2026, 1, 15),
                date(2026, 4, 15),
                date(2026, 7, 15),
                date(2026, 10, 15),
            ]
            actual_dates = sorted(txn.due_date for txn in created)
            assert actual_dates == expected_dates

    def test_due_date_annual_pattern(self, app, db, seed_user):
        """Annual pattern with month_of_year=10, day_of_month=1 gives Oct 1.

        Custom period covers October 2026.
        due_date == 2026-10-01.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 10, 1), date(2026, 10, 31),
            )
            template = self._make_template_with_rule(
                seed_user, "Annual",
                month_of_year=10, day_of_month=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, [period]), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 10, 1)

    def test_due_date_semi_annual_pattern(self, app, db, seed_user):
        """Semi-Annual with month_of_year=1 produces due dates in Jan and Jul.

        Two custom periods cover Jan and Jul. day_of_month=15.
        """
        with app.app_context():
            periods = [
                self._make_custom_period(
                    seed_user, date(2026, 1, 1), date(2026, 1, 31), 0,
                ),
                self._make_custom_period(
                    seed_user, date(2026, 7, 1), date(2026, 7, 31), 1,
                ),
            ]
            template = self._make_template_with_rule(
                seed_user, "Semi-Annual",
                month_of_year=1, day_of_month=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, periods), seed_user["scenario"].id,
            )

            assert len(created) == 2
            actual_dates = sorted(txn.due_date for txn in created)
            assert actual_dates == [date(2026, 1, 15), date(2026, 7, 15)]

    # -- Period spanning two months --------------------------------------------

    def test_due_date_period_spanning_two_months(self, app, db, seed_user):
        """day_of_month=1, period Jan 17 - Feb 1: due_date is Feb 1, not Jan 1.

        The period spans two months. Jan 1 is before the period start,
        so compute_due_date checks both start_date and end_date months.
        Feb 1 falls within the period [Jan 17, Feb 1], so base_month
        resolves to February. due_date == 2026-02-01.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 1, 17), date(2026, 2, 1),
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly", day_of_month=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, [period]), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 2, 1)

    # -- due_day_of_month clamping tests ---------------------------------------

    def test_due_day_same_month_clamping(self, app, db, seed_user):
        """due_day_of_month=31 in April (30 days) clamped to 30.

        day_of_month=15, due_day_of_month=31. Since 31 >= 15, the due
        date stays in the same month (April). min(31, 30) = 30.
        due_date == 2026-04-30.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 4, 1), date(2026, 4, 30),
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly",
                day_of_month=15, due_day_of_month=31,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, [period]), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 4, 30)

    def test_due_day_next_month_feb_clamping(self, app, db, seed_user):
        """Next-month convention with due_dom clamped in February.

        day_of_month=31 in January, due_day_of_month=30. Since
        due_dom(30) < dom(31), next-month convention applies: the due
        date falls in February. February 2026 has 28 days, so
        min(30, 28) = 28. due_date == 2026-02-28.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 1, 17), date(2026, 1, 31),
            )
            template = self._make_template_with_rule(
                seed_user, "Monthly",
                day_of_month=31, due_day_of_month=30,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, [period]), seed_user["scenario"].id,
            )

            assert len(created) == 1
            # dom=31 in Jan, due_dom=30 < 31 so next month = Feb.
            # Feb 2026 has 28 days: min(30, 28) = 28.
            assert created[0].due_date == date(2026, 2, 28)

    # -- Pure function test for compute_due_date ------------------------------

    def test_compute_due_date_is_pure_function(self, app, db):
        """compute_due_date does not touch the database -- it's a pure function.

        Constructs a FakeRule and FakePeriod to verify that compute_due_date
        can produce correct results without any DB interaction.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import RecurrencePatternEnum, StatusEnum

            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY
            )
            every_period_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_PERIOD
            )

            # Test with day_of_month set (monthly-style).
            rule_monthly = build_rule(
                pattern_name="Monthly", day_of_month=20,
            )
            period = FakePeriod(
                id=1,
                start_date=date(2026, 3, 13),
                end_date=date(2026, 3, 26),
                period_index=5,
            )
            result = recurrence_engine.compute_due_date(
                rule_monthly, period,
            )
            assert result == date(2026, 3, 20)

            # Test with no day_of_month (every-period style).
            rule_every = build_rule(pattern_name="Every Period")
            result = recurrence_engine.compute_due_date(
                rule_every, period,
            )
            assert result == date(2026, 3, 13)
