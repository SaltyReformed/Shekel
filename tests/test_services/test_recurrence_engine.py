"""
Shekel Budget App -- Recurrence Engine Tests

Tests the auto-generation of transactions from templates with
recurrence rules (§4.7) and the state machine behavior (§4.8).
"""


import pytest
from datetime import date, timedelta
from dataclasses import replace
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.services._recurrence_common import (
    TemplateRowSelector,
    rows_this_pass_may_maintain,
)
from app.models.transaction_entry import TransactionEntry
from app.models.scenario import Scenario
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import TransactionType, Status
from app import ref_cache
from app.enums import (
    AmountSourceEnum,
    BusinessDayShiftEnum,
    RecurrenceUnitEnum,
    SettlementBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.services import (
    entry_service,
    pay_period_write,
    recurrence_engine,
    status_seam,
)
from app.services.pay_calendar import (
    DerivedPeriod,
    PayCalendar,
    calendar_for,
)
from app.services.recurrence import (
    RecurrenceResolutionError,
    fires_on_day_of_month,
    reauthor_rule,
    recurrence_spec,
)
from app.services.recurrence import placed_periods, rule_occurrences
from app.services.recurrence._months import clamped_day, month_ordinal
from app.exceptions import (
    RecurrenceConflict,
    ValidationError,
)
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence_engine._amounts import DerivedRowFields
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    MONTHLY_FIRST,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)
from tests._test_helpers import (
    all_periods,
    an_entered_day,
    derived_span,
    last_covered_day,
    make_cadence_rule,
    make_every_period_rule,
    rebuild_calendar_from_spans,
    resolved_amount,
    settlement_basis_id,
    settlement_if_settling,
    state_template_price,
)
from app.services.settle_day import record_settle_day
from app.models.amount_ownership import AmountOwnership
from app.services.amount_ownership import state_own_amount


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

#: The scheduling day ``TestTheGenerationSeam``'s bounded bill fires on.
_MONTHLY_DAY = 15

#: The day every hand-built schedule in this file opens on, and therefore the
#: default first occurrence a rule states.
#:
#: **A rule STATES its first occurrence since plan step R7c-b** (ruling R-R16),
#: so each case below names the date its cadence fires on rather than a day of
#: the month the resolver then had to find the first instance of.  The dates
#: are the ones the old derivation answered for those same cases -- a monthly
#: day-15 rule against a schedule opening 2026-01-02 anchored 2026-01-15 -- so
#: every hand-computed assertion in this file still measures what it measured.
_SCHEDULE_OPENS = date(2026, 1, 2)

_CADENCES_OWN_INTERVAL = object()


def build_rule(cadence=EVERY_PERIOD,
               interval_n=_CADENCES_OWN_INTERVAL,
               starts_on=_SCHEDULE_OPENS, nominal_day=None,
               end_date=None, due_day_of_month=None):
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
    either column now.  A rule has ONE opening bound, ``starts_on`` -- the
    paragraph said ``start_date`` until plan step R9, four steps after R7c-c
    dropped that column -- and the every-N-paychecks phase is the ordinal of
    the paycheck that bound falls in.  Keeping them here would let a case
    state a phase the resolver ignores, which is a test that agrees with
    itself.

    Args:
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to build.  Resolved to ``unit_id`` and ``placement_id`` through
            ``ref_cache``, so this needs an app context as the stub's own
            constructor did.
        interval_n: The cadence interval to store.  Defaults to the one
            *cadence* itself names -- 3 for the quarterly constant, 6 for
            the semi-annual one, 1 elsewhere -- because plan step R7c-c
            re-pointed the column onto the two-axis interval, so a
            builder writing a bare ``1`` would state MONTHLY for a case
            that asked for quarterly.  A caller states one to vary the
            rhythm, including the non-positive values the walk's
            refusals are handed.
        starts_on: The rule's first occurrence, and the whole of what it says
            about when it begins -- including which paycheck an every-N-
            paychecks rule phases on.
        nominal_day: The day the rule MEANS when *starts_on*'s month clamped
            it.
        end_date: The rule's closing validity bound.
        due_day_of_month: Real bill due day when it differs from the
            scheduling day.

    Returns:
        An unsaved :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    # **The cadence is STATED, never decoded.**  An authored rule gets its
    # cadence from the write door; a TRANSIENT one built for a pure test never
    # passes through that door, and forcing a database and a calendar into a
    # pure test would be a worse test rather than a stricter one.
    #
    # It took a closed-set NAME through a lookup table until plan step R9,
    # with a ``cadence is None`` fallback for a name the table did not hold.
    # No caller ever passed one -- the unreadable-cadence cases plant an
    # unmodelled ``unit_id`` on the row after it is built, which is the only
    # way to reach that state now that the write door and the picker both
    # refuse the cadence -- so the fallback went with the table.
    #
    # **The caller's interval lands on the column verbatim**, including the
    # non-positive values ``test_every_n_periods_interval_zero_raises`` and its
    # ``None`` twin hand the WALK.  That is the rule shape those tests need,
    # and the refusal they grade belongs to the walk rather than to this
    # fixture.
    #
    # ``None`` for the one constant that fixes no interval of its own, which
    # is the shape a caller varies.
    #
    # **The unsaved OWNER is plan step R-F6's**, and it is what makes
    # ``rule.user_id`` answerable: a recurrence rule belongs to exactly one
    # definition, so its owner is where the owner is read from -- there is no
    # ``user_id`` column any more.  The template is transient like the rule;
    # neither is added to a session, so these stay pure tests.
    own_interval = 1 if cadence.interval_n is None else cadence.interval_n
    rule = RecurrenceRule(
        interval_n=(
            own_interval if interval_n is _CADENCES_OWN_INTERVAL
            else interval_n
        ),
        unit_id=ref_cache.recurrence_unit_id(cadence.unit),
        placement_id=ref_cache.period_placement_id(cadence.placement),
        shift_id=ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        starts_on=starts_on,
        nominal_day=nominal_day,
        due_day_of_month=due_day_of_month,
        end_date=end_date,
    )
    TransactionTemplate(user_id=_MATCH_USER_ID).recurrence_rule = rule
    return rule


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

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        # The PRICE, through the app's one write door.  Both create doors state
        # one right after the flush, and since plan step balance:X-au-e a
        # generated row stores no figure and is priced by this series on its
        # own due date -- a template without one generates rows
        # ``_stated_amount`` refuses.
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def test_every_period_generates_for_all(self, app, db, seed_user, seed_periods):
        """every_period creates a transaction in every pay period."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == len(seed_periods)
            for txn in created:
                assert resolved_amount(txn) == Decimal("100.00")
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
                seed_user, EVERY_N_PERIODS,
                interval_n=2, starts_on=seed_periods[1].start_date,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            # With 10 periods (indices 0-9), offset=1 matches indices 1,3,5,7,9 → 5.
            assert len(created) == 5
            for txn in created:
                period = db.session.get(
                    __import__("app.models.pay_period", fromlist=["PayPeriod"]).PayPeriod,
                    txn.pay_period_id,
                )
                assert (derived_span(period).period_index - 1) % 2 == 0

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
                seed_user, EVERY_PERIOD,
            )
            # ONE statement, exactly as
            # ``_recurrence_form_helpers._clear_recurrence_rule`` does since
            # plan step R-F6: dis-associating the rule is what deletes it,
            # because the relationship carries ``delete-orphan`` and the rule
            # holds the owning FK.  It was three statements -- null both sides,
            # then delete the row -- while the FK sat on the template, and an
            # explicit delete after this one now reports
            # ``expected to delete 1 row(s); 0 were matched``, because the
            # dis-association already removed it.
            template.recurrence_rule = None
            db.session.flush()
            assert template.recurrence_rule is None

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_skips_existing_entries(self, app, db, seed_user, seed_periods):
        """Does not create duplicates for periods that already have entries."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD,
            )

            # First generation.
            first_run = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first_run) == len(seed_periods)

            # Second generation -- should create nothing new.
            second_run = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_respects_is_override_flag(self, app, db, seed_user, seed_periods):
        """Overridden entries are not replaced during generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD,
            )

            # Generate entries.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            created[0].is_override = True
            state_own_amount(created[0], Decimal("999.99"))
            db.session.flush()

            # Regenerate -- the overridden entry should be preserved.
            from app.exceptions import RecurrenceConflict

            try:
                recurrence_engine.regenerate_for_template(
                    template, GenerationSchedule.for_period_ids(
                        BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                    ), seed_user["scenario"].id,
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
                seed_user, EVERY_PERIOD,
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Mark the first one as done, through the real seam: it writes the
            # whole settlement record in one act (plan step X-au-c3), and a
            # bare status assign leaves a state the record's own CHECKs refuse.
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            status_seam.apply_status_change(
                created[0], done_status.id,
                settlement=settlement_if_settling(created[0], done_status.id),
            )
            db.session.flush()

            # Regenerate -- should not delete the done transaction.
            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transaction should still exist unchanged: the settle
            # recorded the row's own plan on the ``derived`` basis, and the
            # regenerate touched neither.
            db.session.refresh(created[0])
            assert created[0].settled_amount == Decimal("100.00")
            assert created[0].settled_basis_id is not None


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
        history_opens_on=None,
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
            build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 15)),
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
        """fires_on_day=31 clamps to 28 in Feb 2026 (non-leap year)."""
        matched = _matches(
            build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 31)),
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
        """fires_on_day=30 also clamps to 28 in Feb 2026."""
        matched = _matches(
            build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 30)),
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
            build_rule(cadence=MONTHLY_FIRST), biweekly_periods,
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
                cadence=QUARTERLY, starts_on=date(2026, 1, 15),
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
                cadence=QUARTERLY, starts_on=date(2026, 2, 15),
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
                cadence=SEMI_ANNUAL, starts_on=date(2026, 1, 15),
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
                cadence=SEMI_ANNUAL, starts_on=date(2026, 2, 15),
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
                cadence=ANNUAL, starts_on=date(2026, 3, 15),
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
                cadence=ANNUAL, starts_on=date(2026, 2, 28), nominal_day=29,
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
        rule = build_rule(cadence=EVERY_PERIOD)
        # Use the 4th period's start_date as effective_from.
        effective_from = biweekly_periods[3].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 26 - 3  # Periods 3-25.
        for period in matched:
            assert period.start_date >= effective_from

    def test_an_unmodelled_unit_is_refused(self, biweekly_periods):
        """An unmodelled cadence id is REFUSED, naming the id.

        It used to log a warning and answer ``[]``, which reads as "this rule
        fires nowhere" -- a rule that generates nothing forever, silently.  An
        id the application does not MODEL is a broken invariant, not a rule
        with no occurrences: the enums are the vocabulary (plan step R2e-2) and
        the write doors refuse anything outside them.  ``resolve`` raises
        rather than fabricating a cadence.

        **The unreadable COLUMN moved at plan step R7c-c.**  This planted a
        ``pattern_id`` no member named; that column is dropped, and the state
        it leaves is a ``unit_id`` naming a ``ref.recurrence_units`` row the
        enums do not model -- the same broken invariant through the column
        that replaced it.
        """
        rule = build_rule(cadence=MONTHLY)
        rule.unit_id = 99999
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
            cadence=EVERY_N_PERIODS,
            interval_n=3,
            starts_on=biweekly_periods[4].start_date,
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
            cadence=EVERY_N_PERIODS,
            interval_n=3,
            starts_on=biweekly_periods[4].start_date + timedelta(days=1),
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
            cadence=EVERY_N_PERIODS,
            interval_n=3,
            starts_on=past_horizon,
        )

        matched = _matched_periods(
            rule, _calendar(biweekly_periods), biweekly_periods[0].start_date,
        )

        assert matched == []


class TestMatchPeriodsFull:
    """Integration tests for _matched_periods() dispatch -- pure, no DB."""

    def test_every_period_returns_all_candidates(self, biweekly_periods):
        """every_period returns all periods after effective_from filtering."""
        rule = build_rule(cadence=EVERY_PERIOD)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 26

    def test_no_periods_empty_result(self):
        """Empty periods list produces an empty result."""
        rule = build_rule(cadence=EVERY_PERIOD)

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
      * ``day_of_month`` and ``month_of_year`` USED to be here too, with
        their own domains and their own NULL defaults.  Plan step R7c-b made
        both ENCODED columns rather than authored ones -- a rule states its
        first occurrence and the door derives them from it -- so there is no
        caller-supplied value left to refuse.  The refusal of that shape that
        survives is ``due_day_of_month``'s, in
        ``test_recurrence_resolution.TestRefusals``.

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
            cadence=EVERY_N_PERIODS,
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
            cadence=EVERY_N_PERIODS,
            interval_n=None,
        )
        with pytest.raises(TypeError):
            _matched_periods(
                rule, _calendar(biweekly_periods),
                biweekly_periods[0].start_date,
            )


# --- DB Integration Tests ----------------------------------------------------


class TestGenerateForTemplate:
    """DB integration tests for generate_for_template()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def test_a_bill_repeating_inside_one_paycheck_generates_each_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """A monthly bill at a 90-day cadence writes ALL THREE rows.

        **Plan step R4a made the repeat reachable and plan step R17 made it
        storable, and this is the regression test for both.**  The reverse
        matcher walked PAYCHECKS, so a monthly rule emitted one row per
        paycheck and silently dropped the rest -- defect D3.  Forward
        generation emits every occurrence, and while the unique index was keyed
        ``(template, pay_period, scenario)`` the pass could not store them:
        generation REFUSED with ``RecurrenceCadenceUnsupported`` rather than
        under-budget the paycheck (plan ledger row D19, developer ruling
        2026-08-08).

        R17 re-keyed the index onto ``(template, scenario, occurs_on)``,
        because a generated row's identity is the OCCURRENCE it answers and
        never the paycheck its money lands in.  Three occurrences inside one
        90-day paycheck are three distinct keys, so all three now store and the
        refusal was deleted with the exception and its error handler
        (developer ruling 2026-08-28).

        What this asserts is the money: a monthly bill owed three times inside
        one long paycheck is BUDGETED three times.  The old behaviour left the
        owner unable to generate at all; the behaviour before that silently
        under-budgeted by two installments.
        """
        with app.app_context():
            # A 90-day schedule for this owner alone; ``cadence_days`` is
            # user-selectable 1..365, so this is configuration, not a
            # hypothetical.  Built after the seed periods so the batch opens
            # strictly after the latest existing end_date.
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=4,
                cadence_days=90,
            )
            db.session.flush()
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )

            # The premise, asserted rather than assumed: without this the test
            # would pass vacuously if the engine ever stopped repeating a
            # paycheck, and would then prove nothing about the re-key.
            matched = _matched_periods(
                template.recurrence_rule,
                calendar_for(seed_user["user"].id),
                long_periods[0].start_date,
            )
            assert len(matched) > len(
                {period.period_index for period in matched}
            ), (
                "the engine no longer repeats a period, so this test proves "
                "nothing about the re-keyed index"
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id),
                    {p.id for p in long_periods},
                ), seed_user["scenario"].id,
            )
            # The flush is the assertion that matters: under the paycheck-keyed
            # index these three rows were an IntegrityError.
            db.session.flush()

            # The expected dates are DERIVED from the paycheck's own span
            # rather than typed as literals, so this asserts the fact the
            # cadence is about (a monthly bill is owed on every 15th the
            # paycheck covers) instead of three figures that would still pass
            # if the fixture's start date moved.
            paycheck = long_periods[0]
            expected_dates = tuple(
                day
                for day in (
                    date(year, month, 15)
                    for year in range(
                        paycheck.start_date.year, last_covered_day(paycheck).year + 1,
                    )
                    for month in range(1, 13)
                )
                if paycheck.start_date <= day <= last_covered_day(paycheck)
            )
            assert len(expected_dates) == 3, (
                "a 90-day paycheck must cover the 15th of three months, or this "
                "fixture no longer exercises the repeat"
            )

            # Every occurrence answered, each by its own row, all three inside
            # the ONE paycheck.
            in_paycheck = [
                row for row in created if row.pay_period_id == paycheck.id
            ]
            assert sorted(
                row.occurs_on for row in in_paycheck
            ) == list(expected_dates)
            assert db.session.query(Transaction).filter_by(
                template_id=template.id, pay_period_id=paycheck.id,
            ).count() == 3

    def test_a_repeat_is_not_written_twice_by_a_second_pass(
        self, app, db, seed_user, seed_periods
    ):
        """The second pass over a repeated paycheck adds NOTHING.

        The companion to the test above, and the one that would catch an
        occurrence-keyed predicate that had quietly gone back to asking about
        the period: with three rows already answering three occurrences inside
        one paycheck, a re-run must find every one of them answered.  A
        predicate keyed on the paycheck would also pass this; a predicate that
        matched rows by POSITION, or one that compared an occurrence against
        the wrong row's date, would not.
        """
        with app.app_context():
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=4,
                cadence_days=90,
            )
            db.session.flush()
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            window = {p.id for p in long_periods}
            first = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), window,
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first) > 0

            second = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), window,
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert second == []
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == len(first)

    def test_a_populated_long_cadence_schedule_stays_extendable(
        self, app, db, seed_user, seed_periods
    ):
        """An already-populated repeat paycheck adds nothing on a later pass.

        This asserted an ORDERING while the repeat was refused: the refusal ran
        after the per-period skip, so an already-populated long-cadence
        schedule stayed extendable instead of refusing over paychecks the pass
        was never going to write.  Plan step **R17** deleted the refusal
        (the re-keyed index stores the repeat), so what is left to grade is the
        property that ordering existed to protect -- a second pass over a
        populated repeat writes nothing and raises nothing.
        """
        with app.app_context():
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=4,
                cadence_days=90,
            )
            db.session.flush()
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            # Occupy every period the rule fires in, exactly as a previous
            # (pre-R4a) generation pass would have left them.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            for period in long_periods:
                db.session.add(Transaction(
                    account_id=template.account_id,
                    template_id=template.id,
                    user_id=period.user_id,
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=projected_id,
                    name=template.name,
                    transaction_type_id=template.transaction_type_id,
                    amount_ownership=AmountOwnership.own(Decimal("100.00")),
                    is_override=False,
                    is_deleted=False,
                ))
            db.session.flush()

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in long_periods},
                ), seed_user["scenario"].id,
            )

            assert created == []

    def test_effective_from_skips_earlier_periods(
        self, app, db, seed_user, seed_periods
    ):
        """effective_from = 4th period's start → only generates from period 4."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            effective_from = seed_periods[3].start_date
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
                seed_user, EVERY_PERIOD
            )

            # First generation.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == 10

            # Soft-delete one entry.
            created[2].is_deleted = True
            db.session.flush()

            # Second generation -- should not duplicate the deleted entry.
            second_run = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_monthly_pattern_generates_correct_count(
        self, app, db, seed_user, seed_periods
    ):
        """Monthly pattern across 10 periods produces one per unique month."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            # 10 biweekly periods starting Jan 2 span ~5 months.
            # Determine expected unique months from periods.
            unique_months = set()
            for p in seed_periods:
                for dt in (p.start_date, last_covered_day(p)):
                    target = date(dt.year, dt.month, 15)
                    if p.start_date <= target <= last_covered_day(p):
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
            build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 15)),
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
            schedule = GenerationSchedule.for_pass(BalanceContext.build(template.user_id))
            straddled = seed_periods[3]
            bound = straddled.start_date + timedelta(days=1)
            assert bound < last_covered_day(straddled)

            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, bound,
                block_message="test",
            )

            kept = [row.period.period_id for row in plan.placements]
            assert straddled.id in kept, (
                "the seam dropped the period its bound falls inside"
            )
            assert seed_periods[2].id not in kept

    def _make_template(self, seed_user):
        """A day-15 monthly expense template for this owner.

        The first occurrence is the first 15th the schedule reaches, which is
        what the pre-R7c-b derivation answered for a day-15 rule stating no
        opening bound -- so this template fires in the same periods it always
        did, and the assertions above still measure ``effective_from``.
        """
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        opening = calendar_for(seed_user["user"].id).opening_bound()
        ordinal = month_ordinal(opening)
        first_fifteenth = clamped_day(
            ordinal if opening.day <= _MONTHLY_DAY else ordinal + 1,
            _MONTHLY_DAY,
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            transaction_type_id=expense_type.id,
            name="Bounded Bill",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, MONTHLY, starts_on=first_fifteenth,
        )
        db.session.refresh(template)
        return template


def _paycheck_covering(seed_user, day):
    """Return the ``budget.pay_periods.id`` of the paycheck covering *day*.

    ``txn.pay_period.start_date <= day <= txn.pay_period.end_date`` until plan
    step ``pay_calendar:C4-c`` dropped ``end_date``.  Containment is the
    calendar's question -- :meth:`DerivedPeriod.covers` is the one rule
    (**R-PC31**) -- and reaching it through ``period_containing`` is what keeps
    a test from open-coding a fourth copy of that comparison.

    Args:
        seed_user: The seeded owner fixture.
        day: The civil day to place.

    Returns:
        The covering period's id.
    """
    covering = calendar_for(seed_user["user"].id).period_containing(day)
    assert covering is not None, f"no paycheck covers {day}"
    return covering.period_id


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
    the derivation.  The first test below is the CONTROL for that closure.

    **And since plan step ``pay_calendar:C4-c`` the stored rows cannot hold a
    hole either.**  ``end_date`` is dropped, so a period's last covered day is
    the day before the next payday and nothing else; the only shape left is a
    payday JUMP, which is what the fixture below builds and which the writer
    accepts.  ``scripts/integrity_check.py`` **BA-07** reported the stored
    state while it was expressible and went with the column.

    **The absorption is not free, and the third test says so.**  An absorbed
    hole leaves an OVER-LONG paycheck, and a monthly bill can fall inside one
    more than once.  The paycheck-keyed index could not hold that pair, so the
    pass was REFUSED; since plan step **R17** re-keyed onto the occurrence both
    installments are written, and the third test grades the rows rather than
    the refusal.

    **"Not yet" was never a gap**, and the class still ends with the control
    that says so: under ``PERIOD_STARTING_ON_OR_AFTER`` an occurrence dated
    after the LAST PAYDAY has no paycheck to defer onto even on a perfectly
    contiguous schedule, and that is 43% of biweekly schedule openings.  Since
    C2-b2 it is the ONLY way a placement answers ``None``.
    """

    #: Days after the seed schedule's last covered day that the second batch
    #: opens.  Large enough that a whole calendar month (June 2026) falls in
    #: the jump, so a monthly rule has exactly one occurrence the preceding
    #: paycheck has to absorb and the assertions below are about that one date.
    _GAP_DAYS = 43

    #: The rule's scheduling day, and therefore the day every occurrence and
    #: every generated ``due_date`` falls on.
    _DAY_OF_MONTH = 15

    def _schedule_with_a_payday_jump(self, seed_user, seed_periods):
        """Append a second batch 43 days after the schedule's horizon.

        The whole fixture is the real writer's, which is the point: the shape
        these tests are about is one the app produces.

        *It re-opened a stored hole on the last line until plan step
        ``pay_calendar:C4-c``.*  While ``end_date`` was a column, the writer's
        own recompute closed the hole this appended, so the fixture had to put
        it back to reach the state a pre-C3-b row could hold.  The column is
        gone: the days between the horizon and the new payday belong to the
        preceding paycheck, and there is no second value that could say
        otherwise.

        Returns:
            ``(later_periods, jump_start, jump_end)`` -- the appended batch and
            the inclusive span of days the preceding paycheck absorbs.
        """
        last_covered = self._horizon(seed_user)
        later_start = last_covered + timedelta(days=self._GAP_DAYS)
        later = pay_period_write.record_paydays(
            user_id=seed_user["user"].id,
            first_payday=later_start,
            num_periods=6,
            cadence_days=14,
        )
        return (
            later,
            last_covered + timedelta(days=1),
            later_start - timedelta(days=1),
        )

    @staticmethod
    def _horizon(seed_user):
        """Return the owner's last covered day, DERIVED.

        ``seed_periods[-1].end_date`` until plan step ``pay_calendar:C4-c``
        dropped the column; the horizon is the last payday plus the cadence,
        and the calendar is what answers it.
        """
        return calendar_for(seed_user["user"].id).horizon()

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
            last_covered = self._horizon(seed_user)
            later_start = last_covered + timedelta(days=self._GAP_DAYS)
            later = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=later_start,
                num_periods=6,
                cadence_days=14,
            )
            assert later[0].start_date == later_start

            # The days the OLD writer would have left behind.
            calendar = calendar_for(seed_user["user"].id)
            day = last_covered + timedelta(days=1)
            while day < later_start:
                assert calendar.period_containing(day) is not None, (
                    f"{day} is covered by no pay period"
                )
                day += timedelta(days=1)
            # And it is the PRECEDING paycheck that absorbed them, running to
            # the day before the new payday rather than stopping at its old
            # horizon.
            preceding = calendar.period_containing(seed_periods[-1].start_date)
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
            _later, gap_start, gap_end = self._schedule_with_a_payday_jump(
                seed_user, seed_periods,
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=absorbed_day,
            )
            schedule = GenerationSchedule.for_pass(BalanceContext.build(template.user_id))
            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, None,
                block_message="test",
            )

            absorbing = next(
                period
                for period in all_periods(
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
            assert in_hole[0].period.period_id == absorbing.id
            # What makes this an ABSORPTION rather than an ordinary
            # containment: the paycheck spans far more than one cadence,
            # because it ran on to the day before the next payday.
            assert (
                in_hole[0].period.end_date - in_hole[0].period.start_date
            ).days + 1 > 14

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
                derived = row.period
                assert derived.start_date <= row.occurrence <= derived.end_date

    def test_the_regenerate_sweep_and_the_regeneration_share_ONE_period_end(
        self, app, db, seed_user, seed_periods,
    ):
        """The bound both halves of a regenerate read must be the same end.

        ``regenerate_for_template`` decides which existing rows it may act on
        and which periods the rule names, and both decisions are bounded by
        ``effective_from``.  For that to be ONE pass rather than two, both must
        read the same answer to "when does this paycheck end".

        **They read the DERIVED end since pay-calendar plan step C2-f3c, and
        this test was INVERTED there.**  The row half used to be SQL over
        ``pay_periods.end_date`` -- the STORED column -- while plan step C2-b2
        had already given the rule half a calendar whose ends are derived; the
        fix at the time was to make the RULE half read the stored column too,
        by resolving the ORM row before applying the bound, and this test
        asserted the absorbing paycheck was in NEITHER half.  C2-f3c removed
        the stored column from the seam entirely: the row select is now a
        period-ID set filtered by the derived end
        (``_recurrence_common.rows_this_pass_may_maintain``), so the absorbing
        paycheck is in BOTH halves.

        **And that is the correct answer, not merely a consistent one.** The
        absorbing paycheck's span runs to 2026-07-02, so it is the paycheck the
        owner's money on 2026-06-01 actually lives in.  A regeneration
        effective from that date must maintain it.  The old behaviour skipped
        it because a stored column said the paycheck had ended six weeks
        earlier -- a column plan step ``pay_calendar:C4-c`` DROPPED, and which
        no writer had produced since plan step C3-b.

        The bound falls deep INSIDE the absorbing paycheck rather than near a
        boundary, which is what makes the two memberships below a real
        question: the paycheck opens 2026-05-08 and runs to 2026-07-02, and
        the bound is 2026-06-01.
        """
        with app.app_context():
            self._schedule_with_a_payday_jump(seed_user, seed_periods)
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            scenario_id = seed_user["scenario"].id
            schedule = GenerationSchedule.for_pass(
                BalanceContext.build(template.user_id),
            )
            absorbing = next(
                period
                for period in all_periods(seed_user["user"].id)
                if period.start_date == seed_periods[-1].start_date
            )
            bound = date(2026, 6, 1)

            # The premise: the bound really does fall inside the absorbing
            # paycheck, and past where an un-absorbed one would have ended.
            absorbing_span = schedule.calendar.period_by_id(absorbing.id)
            assert absorbing_span.start_date < bound <= absorbing_span.end_date
            assert bound > absorbing_span.start_date + timedelta(days=13)

            # Give the paycheck a row, so the sweep has something to collect.
            recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()

            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, scenario_id, bound, block_message="test",
            )
            swept = rows_this_pass_may_maintain(
                TemplateRowSelector(
                    Transaction, Transaction.template_id, template,
                    scenario_id,
                ),
                schedule, bound,
            )

            # BOTH halves reach it, because both read the derived end.
            assert absorbing.id in {
                row.period.period_id for row in plan.placements
            }, "the rule half skipped the paycheck its own calendar says is live"
            assert absorbing.id in {row.pay_period_id for row in swept}, (
                "the row half skipped the paycheck the rule half named -- the "
                "two are reading different definitions of this period's end"
            )

            # **And the two halves apply the SAME comparison, not merely the
            # same column** (adversarial review of plan step C2-f3c).  The
            # memberships above are satisfied by a sweep using ``>`` where the
            # rule uses ``>=``; the difference is invisible unless a bound
            # falls exactly ON a derived end, which no other case in the suite
            # arranges -- every ``effective_from`` elsewhere is a period START
            # or a round date, and a 14-day schedule never makes those equal.
            # A row the rule NAMES but the fetch does not return is a
            # duplicate create, or an ``IntegrityError`` on the generation
            # index.
            on_the_end = schedule.calendar.period_by_id(absorbing.id).end_date
            named_there = {
                row.period.period_id
                for row in recurrence_engine.resolve_generation_plan(
                    template, schedule, scenario_id, on_the_end,
                    block_message="test",
                ).placements
            }
            swept_there = {
                row.pay_period_id for row in rows_this_pass_may_maintain(
                    TemplateRowSelector(
                        Transaction, Transaction.template_id, template,
                        scenario_id,
                    ),
                    schedule, on_the_end,
                )
            }
            assert absorbing.id in named_there, (
                "the premise: a bound ON the derived end must still NAME the "
                "period, or this case grades an empty set"
            )
            assert named_there <= swept_there, (
                f"the rule half names {sorted(named_there - swept_there)} that "
                f"the row half does not offer, on a bound that falls exactly "
                f"on a derived period end -- the two are not one comparison"
            )

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
            _later, gap_start, gap_end = self._schedule_with_a_payday_jump(
                seed_user, seed_periods,
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=absorbed_day,
            )
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(template.user_id)),
                seed_user["scenario"].id,
            )

            absorbing = next(
                period
                for period in all_periods(
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
        """The cost of absorbing, now WRITTEN rather than refused.

        The hole spans a whole calendar month, so the paycheck that absorbs it
        covers the 15th twice.  While the unique index was keyed
        ``(template, pay_period, scenario)`` both rows could not be stored, so
        ``refuse_unstorable_repeats`` refused the whole pass and wrote nothing.
        Plan step **R17** re-keyed it onto ``(template, scenario, occurs_on)``
        -- two occurrences are two keys -- so the absorbing paycheck now
        carries both installments and the money is budgeted rather than
        withheld (developer ruling 2026-08-28).

        **This is ONE of the ways plan step C2-b2 moves money, not the only
        one**, and an adversarial review of that step refuted the claim that it
        was.  Wherever a stored column disagrees with the payday derivation the
        engine believes the derivation, and there are THREE such columns --
        the hole this test builds (plan ledger row **P27**), the stored cadence
        against the last stored end (**P28**), and a stored ordinal that is not
        ``0..n-1`` (**P26**).  ``recurrence/_occurrence.py``'s module docstring
        states all three and what each one costs.

        None is reachable through a live door: ``pay_period_write`` writes the
        derivation over the whole payday list on every write and REPAIRS such a
        row, so each means data written before plan step C3-b or edited outside
        that module.  Production carries none of the three (61 contiguous
        periods, 0 index mismatches, 0 end mismatches, measured 2026-08-10).
        """
        with app.app_context():
            _later, gap_start, gap_end = self._schedule_with_a_payday_jump(
                seed_user, seed_periods,
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=self._DAY_OF_MONTH,
            )
            schedule = GenerationSchedule.for_pass(BalanceContext.build(template.user_id))

            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            # The date the hole used to swallow is now ANSWERED by a row of its
            # own, rather than costing the owner the whole pass.
            in_hole = self._days_between(gap_start, gap_end)
            assert len(in_hole) == 1, (
                f"the fixture must put exactly one {self._DAY_OF_MONTH}th in "
                f"the hole {gap_start}..{gap_end}, got {in_hole}"
            )
            assert in_hole[0] in {row.occurs_on for row in created}

            # And the absorbing paycheck holds BOTH -- two rows, two
            # occurrences, one period, which the old index could not store.
            absorbing = [
                row for row in created if row.occurs_on == in_hole[0]
            ][0].pay_period_id
            both = [
                row for row in created if row.pay_period_id == absorbing
            ]
            assert len(both) == 2
            assert len({row.occurs_on for row in both}) == 2

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
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=1,
                cadence_days=14,
            )
            db.session.flush()
            last = tail[-1]
            assert last.start_date.month != last_covered_day(last).month, (
                "the control needs a final period straddling a month boundary"
            )

            template = self._make_template_with_rule(seed_user, MONTHLY_FIRST)
            schedule = GenerationSchedule.for_pass(BalanceContext.build(template.user_id))
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

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Gap Bill',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template


class TestRegenerateForTemplate:
    """DB integration tests for regenerate_for_template()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def _make_envelope_template(self, seed_user):
        """A template whose rows TRACK PURCHASES, as production requires.

        ``_make_template_with_rule`` leaves ``is_envelope`` False, so
        ``Transaction.tracks_purchases`` is False and
        ``entry_service.create_entry`` REFUSES a purchase against its rows.  An
        adversarial review of plan step R10-a caught the retention tests
        building a parent production cannot produce; every test that records a
        purchase starts here instead.
        """
        template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
        template.is_envelope = True
        db.session.flush()
        return template

    def _record_purchase(
        self, txn, seed_user, amount="12.79", settled_on=None,
    ):
        """Record one purchase against *txn* through the REAL entry doors.

        Through ``entry_service`` rather than onto the model, because the door
        is where the behaviour under test actually comes from: it runs the
        entry-capability guard, and its closing ``_resync_after_entry_change``
        is what stamps the parent's ``actual_amount`` and books the purchase's
        ledger legs.  A hand-built ``TransactionEntry`` skips all three, so a
        suite written that way can assert nothing about the ledger -- which is
        exactly what an adversarial review of plan step R10-a found.

        Args:
            txn: The parent transaction.
            seed_user: The seeded user fixture.
            amount: The purchase amount.
            settled_on: The day the bank took the money.  Passed through the
                UPDATE door, which is the only one that accepts it (a purchase
                is recorded before it is observed to have posted).  A purchase
                carrying one is a cash movement of its own -- ruling R-FM -- so
                this is what gives the row ledger legs.
        """
        entry = entry_service.create_entry(
            txn.id,
            seed_user["user"].id,
            entry_service.EntryDetails(
                amount=Decimal(amount),
                description="Kroger",
                purchased_on=txn.pay_period.start_date,
            ),
        )
        if settled_on is not None:
            entry_service.update_entry(
                entry.id, seed_user["user"].id, settle_day=None if settled_on is None else an_entered_day(settled_on),
            )
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
            template = self._make_envelope_template(seed_user)
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on = created[0]
            txn_id = spent_on.id
            entry_id = self._record_purchase(spent_on, seed_user).id

            # Through the app's one write door: since plan step
            # balance:X-au-e the SERIES is what prices the rows, so moving
            # the scalar alone states a price no row can read.
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            entry = db.session.get(TransactionEntry, entry_id)
            assert entry is not None, "the purchase was destroyed"
            assert entry.transaction_id == txn_id
            assert entry.amount == Decimal("12.79")

            txn = db.session.get(Transaction, txn_id)
            assert txn is not None, "the row the purchase hangs off was destroyed"
            assert resolved_amount(txn) == Decimal("200.00")

    def test_a_row_the_rule_STOPPED_naming_is_retired_though_the_rule_remains(
        self, app, db, seed_user, seed_periods
    ):
        """The RETIRE branch fires for a LIVE rule, not only a cleared one.

        **The firing control for the sweep's domain** (pay-calendar plan step
        C2-f3c).  A maintain pass decides what to do with each row it can see,
        and it can only RETIRE a row whose period the rule no longer names --
        so the set of rows it looks at has to be strictly WIDER than the set of
        periods the rule names.  Narrowing the row select to the plan's own
        periods would make the branch unreachable while every other assertion
        in this class still passed, which is why the domain is the pass's WRITE
        WINDOW (``_recurrence_common.rows_this_pass_may_maintain``) and not the
        plan.

        The neighbouring case covers the CLEARED rule, where the plan is
        ``None`` and every row is an orphan.  This one keeps a live rule and
        narrows what it names -- an every-paycheck definition edited down to
        the first paycheck of each month -- which is the shape a template edit
        actually produces and the one a plan-shaped domain would silently stop
        handling.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_pass(
                BalanceContext.build(template.user_id),
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            assert len(created) == 10, "the premise: a row in every paycheck"
            by_period = {row.pay_period_id: row.id for row in created}

            # The definition narrows: the first paycheck of each month only.
            # The old cadence is DELETED before the new one is authored -- a
            # rule carries its definition's FK since recurrence plan step
            # **R-F6**, and one partial unique index per kind holds a template
            # to one rule, so replacing means replacing.
            db.session.delete(template.recurrence_rule)
            db.session.flush()
            make_cadence_rule(template, MONTHLY_FIRST)
            db.session.refresh(template)

            recurrence_engine.regenerate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()

            named = {
                row.period.period_id
                for row in recurrence_engine.resolve_generation_plan(
                    template, schedule, scenario_id, None,
                    block_message="test",
                ).placements
            }
            # The premise, asserted rather than assumed: the edit really did
            # drop paychecks, so there is something for the branch to retire.
            assert named, "the narrowed rule must still name some paychecks"
            assert set(by_period) - named, (
                "the narrowed rule must stop naming some paycheck, or this "
                "case grades an empty set"
            )

            # **Every original row is retired, and that is plan step R17's
            # semantics rather than a regression.**  The old cadence answered
            # each paycheck's own PAYDAY (the ``PERIOD`` unit's occurrence);
            # the narrowed one answers the 1st of each month.  Those are
            # different occurrences, so no original row is still NAMED and the
            # pass replaces rather than maintains -- the developer's ruling of
            # 2026-08-28, which chose replace-and-ask over re-pointing a row at
            # whatever occurrence happened to be left in its paycheck.
            for period_id, row_id in by_period.items():
                assert db.session.get(Transaction, row_id) is None, (
                    f"period {period_id}'s original row answers an occurrence "
                    f"the narrowed rule no longer names, so it must retire"
                )

            # **The firing control this case exists for, unchanged**: a row in
            # a paycheck the rule NO LONGER names is gone.  That is only
            # reachable because the sweep's domain is the pass's WRITE WINDOW
            # (``_recurrence_common.rows_this_pass_may_maintain``) -- a
            # plan-shaped domain would never have seen those rows, and every
            # other assertion in this class would still have passed.
            surviving = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id, is_deleted=False)
                .all()
            )
            assert surviving, "the narrowed rule must still write its own rows"
            assert {row.pay_period_id for row in surviving} <= named, (
                "a row survived in a paycheck the rule no longer names -- the "
                "sweep never saw it"
            )

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
            template = self._make_envelope_template(seed_user)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
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
            template = self._make_envelope_template(seed_user)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
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
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
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

    def test_every_derived_column_is_maintained_not_just_the_amount(
        self, app, db, seed_user, seed_periods
    ):
        """A maintained row takes ALL SIX derived columns from its template.

        **The keystone claim of plan step R10-a**, and it was untested until an
        adversarial review measured it: skipping ``name``, ``category_id``,
        ``transaction_type_id`` and ``due_date`` in the update loop -- four of
        the six ``DerivedRowFields`` at once -- passed the entire 9,477-test
        suite.  Only ``account_id`` and ``estimated_amount`` were pinned.

        ``transaction_type_id`` is the one that moves money: it carries the
        row's SIGN, so an Expense-to-Income template edit that failed to reach
        a maintained row would leave that row subtracting what it should add.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            new_category = seed_user["categories"]["Groceries"]
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()
            assert created, "no rows to maintain -- the fixture proves nothing"
            row_ids = [txn.id for txn in created]
            assert all(txn.due_date.day == 5 for txn in created)

            template.name = "Renamed Bill"
            template.category_id = new_category.id
            template.transaction_type_id = income_type.id
            # The DUE DATE moves and the OCCURRENCE does not, which is what
            # keeps these rows maintained rather than retired.  Since plan step
            # **R17** a row is named by the occurrence it answers, so moving
            # ``starts_on`` from the 5th to the 7th would move every occurrence
            # and retire every row -- correct behaviour (developer ruling
            # 2026-08-28) but a different test.  ``due_day_of_month`` is the
            # knob that separates the two: the walk reads the scheduling day
            # and never this, while ``compute_due_date`` prefers it.  Chosen
            # ABOVE the scheduling day so it stays in the same calendar month
            # (below it means "the month after", by that function's contract).
            #
            # RE-AUTHORED rather than assigned, because plan step R7c-b made
            # the day a property of the first OCCURRENCE and the write door
            # derives the storage encoding from the spec.
            rule = template.recurrence_rule
            reauthor_rule(
                rule,
                replace(recurrence_spec(rule), due_day_of_month=7),
                calendar_for(template.user_id),
            )
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            maintained = (
                db.session.query(Transaction)
                .filter(Transaction.id.in_(row_ids))
                .all()
            )
            assert len(maintained) == len(row_ids), "a row was destroyed"
            for txn in maintained:
                assert txn.name == "Renamed Bill"
                assert txn.category_id == new_category.id
                assert txn.transaction_type_id == income_type.id
                assert txn.due_date.day == 7

    def test_maintaining_a_row_reconciles_the_ledger_its_purchase_posted(
        self, app, db, seed_user, seed_periods
    ):
        """A purchase's counter leg follows its parent's category.

        **The ledger half of plan step R10-a, which had NO coverage**: an
        adversarial review replaced the whole ``sync_transaction_postings``
        loop with ``pass`` and the full suite still passed.  The cause was the
        fixture -- every test purchase was built without a ``settled_on``, so
        ``purchase_posts()`` returned False and no test in the suite ever
        booked a ledger leg at all.

        A purchase carrying a recorded bank posting day IS a cash movement
        (ruling R-FM), debited against its envelope's category.  Move the
        template's category and the counter leg must move with it, while the
        cash leg stays where the money actually left.
        """
        with app.app_context():
            template = self._make_envelope_template(seed_user)
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on = created[0]
            self._record_purchase(
                spent_on, seed_user, amount="120.00",
                settled_on=spent_on.pay_period.start_date,
            )
            db.session.flush()

            def legs():
                """Every posting on this row's family, by ledger account."""
                rows = (
                    db.session.query(Posting.ledger_account_id, Posting.amount)
                    .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                    .filter(JournalEntry.transaction_entry_id.isnot(None))
                    .all()
                )
                totals = {}
                for ledger_account_id, amount in rows:
                    totals[ledger_account_id] = (
                        totals.get(ledger_account_id, Decimal("0.00")) + amount
                    )
                return {k: v for k, v in totals.items() if v != Decimal("0.00")}

            before = legs()
            assert before, (
                "the purchase booked no ledger legs -- the fixture cannot see "
                "the behaviour this test exists for"
            )
            assert sum(before.values()) == Decimal("0.00")

            template.category_id = seed_user["categories"]["Groceries"].id
            db.session.flush()
            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            after = legs()
            # Still balanced, and the counter leg actually MOVED: the set of
            # ledger accounts holding a non-zero net is different, while the
            # cash side is untouched.
            assert sum(after.values()) == Decimal("0.00")
            assert after != before, (
                "the category move did not reach the purchase's counter leg"
            )
            moved_off = set(before) - set(after)
            moved_onto = set(after) - set(before)
            assert moved_off and moved_onto, (
                f"expected the counter leg to move accounts, got "
                f"{before} -> {after}"
            )

    def test_a_cross_user_scenario_is_refused_and_retires_nothing(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """The ownership guard is load-bearing, and now it is tested.

        Removing the early return at the top of ``regenerate_for_template``
        passed the full 9,477-test suite -- an adversarial review's measurement
        of plan step R10-a.  The guard matters MORE under this step than
        before, by its own docstring's argument: it is what makes the plan's
        ``None`` mean "the rule was cleared" and nothing else.  Without it a
        foreign-scenario call resolves no plan, which this code would read as
        "retire everything".
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            row_ids = sorted(txn.id for txn in created)
            assert row_ids

            # User B's scenario, with user A's template.
            result = recurrence_engine.regenerate_for_template(
                template, schedule, second_user["scenario"].id,
            )
            db.session.flush()

            assert result == []
            surviving = sorted(
                txn.id for txn in db.session.query(Transaction)
                .filter(Transaction.template_id == template.id).all()
            )
            assert surviving == row_ids, (
                "a cross-user call retired this template's rows"
            )

    def test_no_second_row_is_created_beside_an_overridden_or_deleted_one(
        self, app, db, seed_user, seed_periods
    ):
        """The claim predicate is the maintain path's whole creation rule.

        ``OccurrenceClaims`` counts EVERY row's claim, whatever state it is in,
        and this had no engine-level test: mutating it to ignore soft-deleted
        rows left this file green.  The database will NOT backstop a mistake
        here -- both generation indexes are PARTIAL
        (``WHERE is_deleted = FALSE AND is_override = FALSE``), so a duplicate
        beside a soft-deleted or overridden row inserts silently and the owner
        sees the same bill twice.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            softdeleted, overridden = created[0], created[1]
            softdeleted.is_deleted = True
            overridden.is_override = True
            db.session.flush()
            period_ids = (softdeleted.pay_period_id, overridden.pay_period_id)

            with pytest.raises(RecurrenceConflict):
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            for period_id in period_ids:
                rows = (
                    db.session.query(Transaction)
                    .filter(
                        Transaction.template_id == template.id,
                        Transaction.pay_period_id == period_id,
                        Transaction.scenario_id == seed_user["scenario"].id,
                    )
                    .all()
                )
                assert len(rows) == 1, (
                    f"period {period_id} holds {len(rows)} rows -- a second "
                    "was created beside the owner's"
                )

    def test_a_settlement_record_alone_retains_an_orphaned_row(
        self, app, db, seed_user, seed_periods
    ):
        """The third arm of the records predicate, which was untested.

        Neutering it passed the full suite.

        **The arm reads the settlement RECORD since plan step X-au-c3**, where
        it read ``actual_amount is not None``.  That column meant "a human typed
        a figure" only because it carried both the settled figure and the fact
        that a human had supplied it; a row that has SETTLED records what moved
        whoever said so, and that is the fact worth holding a row for.  The
        fixture settles the row rather than writing a figure onto a projected
        one, which the record's CHECKs now refuse.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            priced, blank = created[0], created[1]
            priced_id, blank_id = priced.id, blank.id
            # The RETAINED state, which is what a REVERT leaves behind: the
            # record kept, the ASSERTION released (``settled_on`` cleared, and
            # ``reconciled_by_id`` with it).
            # ``status_seam.apply_status_change`` writes exactly this shape on
            # the way OUT of the settled band.
            #
            # **That is a LIVE path, not a structural backstop.**  A settled
            # status is immutable to this sweep, so before plan step X-au-c3 --
            # when leaving the band destroyed the record along with the
            # assertion -- no row this pass could see ever carried one and the
            # arm was unreachable.  Retention put a real row in front of it: the
            # owner settled this row, read a figure off a statement, and set it
            # back to Projected in order to edit it.  Retiring it now would
            # delete that figure, which is what retention exists to keep.
            #
            # The columns satisfy ``ck_transactions_settle_day_needs_a_record``: a
            # record without a day is precisely what that implication admits.
            record_settle_day(priced, None)
            priced.settled_amount = Decimal("41.10")
            priced.settled_basis_id = settlement_basis_id(SettlementBasisEnum.CORRECTED)
            db.session.flush()

            template.recurrence_rule = None
            template.recurrence_rule = None
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.retained == [priced_id]
            assert db.session.get(Transaction, priced_id) is not None
            assert db.session.get(Transaction, blank_id) is None

    def test_the_three_conflict_lists_are_reported_together(
        self, app, db, seed_user, seed_periods
    ):
        """One pass can retain, override AND soft-delete at once.

        Each retention test asserts its own list and nothing about the other
        two, so nothing pinned the shape the route actually branches on -- and
        that is the shape that decides whether the chooser renders beside the
        retained notice.  It is the combination the H1 defect lived in.
        """
        with app.app_context():
            template = self._make_envelope_template(seed_user)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on, overridden, softdeleted = created[0], created[1], created[2]
            spent_id = spent_on.id
            overridden_id, softdeleted_id = overridden.id, softdeleted.id
            self._record_purchase(spent_on, seed_user)
            overridden.is_override = True
            softdeleted.is_deleted = True
            db.session.flush()

            template.recurrence_rule = None
            template.recurrence_rule = None
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.retained == [spent_id]
            assert raised.value.overridden == [overridden_id]
            assert raised.value.deleted == [softdeleted_id]

    def test_a_rule_row_survives_beside_a_carried_forward_override(
        self, app, db, seed_user, seed_periods
    ):
        """A period holding BOTH rows keeps both, and that is a change.

        **The one case where maintaining is not equivalent to the old
        delete-and-recreate**, found by adversarial review: the old sweep
        deleted the rule's own row, then the skip predicate saw the override
        sibling and declined to recreate it, so an unrelated edit silently
        removed a period's own bill.  Carry-forward produces exactly this shape
        -- ``carry_forward_service`` moves an unpaid row into the target period
        with ``is_override = True`` precisely so it sits BESIDE the
        rule-generated one (both generation indexes exclude override rows, so
        the pair is permitted).

        Measured at 0 live instances on a production clone, so nothing moved
        when this shipped; the new answer is also the correct one, since the
        period genuinely owes both. Pinned here because nothing else pins it.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            rule_row = created[0]
            rule_row_id = rule_row.id
            period_id = rule_row.pay_period_id

            # The shape carry-forward leaves behind: an override sibling in the
            # same period, beside the rule's own row.
            carried = Transaction(
                account_id=rule_row.account_id,
                template_id=template.id,
                user_id=rule_row.user_id,
                pay_period_id=period_id,
                scenario_id=seed_user["scenario"].id,
                status_id=rule_row.status_id,
                name=template.name,
                category_id=template.category_id,
                transaction_type_id=template.transaction_type_id,
                amount_ownership=AmountOwnership.own(Decimal("55.00")),
                is_override=True,
                is_deleted=False,
            )
            db.session.add(carried)
            db.session.flush()
            carried_id = carried.id

            # Through the app's one write door: since plan step
            # balance:X-au-e the SERIES is what prices the rows, so moving
            # the scalar alone states a price no row can read.
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.overridden == [carried_id]
            survivor = db.session.get(Transaction, rule_row_id)
            assert survivor is not None, (
                "the rule's own row was destroyed beside its override sibling"
            )
            assert resolved_amount(survivor) == Decimal("200.00")
            assert db.session.get(
                Transaction, carried_id,
            ).estimated_amount == Decimal("55.00")

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
                seed_user, EVERY_PERIOD
            )

            # Generate initial entries.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            old_ids = sorted(txn.id for txn in created)
            assert len(old_ids) == 10

            # Change the template amount.
            # Through the app's one write door: since plan step
            # balance:X-au-e the SERIES is what prices the rows, so moving
            # the scalar alone states a price no row can read.
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            # Regenerate.  Every period the rule names already holds the rule's
            # own row, so there is nothing to CREATE and the return is empty.
            new_created = recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
                assert resolved_amount(txn) == Decimal("200.00")

    def test_regenerate_raises_conflict_for_deleted_entries(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with soft-deleted entry raises RecurrenceConflict."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Soft-delete one entry.
            deleted_id = created[0].id
            created[0].is_deleted = True
            db.session.flush()

            # Regenerate -- should raise with deleted list.
            with pytest.raises(RecurrenceConflict) as exc_info:
                recurrence_engine.regenerate_for_template(
                    template, GenerationSchedule.for_period_ids(
                        BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                    ), seed_user["scenario"].id,
                )

            assert deleted_id in exc_info.value.deleted


class TestResolveConflicts:
    """DB integration tests for resolve_conflicts()."""

    def _make_template_with_rule(
        self, seed_user, cadence, category_key=None, **rule_kwargs,
    ):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"][category_key or "Car Payment"].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def test_resolve_keep_no_changes(self, app, db, seed_user, seed_periods):
        """action='keep' leaves overridden transaction unchanged."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            # Resolve as 'keep'.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="keep", user_id=seed_user["user"].id,
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is True
            assert txn.estimated_amount == Decimal("999.99")

    def test_resolve_update_hands_the_row_back_to_its_definition(
        self, app, db, seed_user, seed_periods
    ):
        """action='update' clears the flags and writes a DECLARATION, no figure.

        Plan step balance:X-au-e, ruling **R-JD**.  "Use the template's amount"
        used to write the template's CURRENT ``default_amount`` onto the row;
        there is no figure to write now, and the act is to stop overriding so
        the definition's own series prices the row again.

        The assertion is on all THREE columns the act touches, because two of
        them are what makes it a hand-back rather than a re-price: the flag
        clears, the figure goes to ``None``, and ``amount_source_id`` names the
        template.  Asserting the resolved money alone would pass on a row that
        still owned the very same number.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                user_id=seed_user["user"].id,
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            assert txn.is_deleted is False
            # The hand-back itself: no figure, and the relation that prices it.
            assert txn.estimated_amount is None
            assert txn.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            # And what it is now WORTH is the definition's stated price, not
            # the $999.99 the owner had typed.
            assert resolved_amount(txn) == Decimal("100.00")

    def test_a_row_whose_TEMPLATE_IS_GONE_is_skipped_not_declared(
        self, app, db, seed_user, seed_periods
    ):
        """A row cannot be handed back to a definition it no longer names.

        ``fk_transactions_template`` is ON DELETE SET NULL, so a row can
        outlive its template. Declaring such a row would write exactly the
        state ledger row **N-440** describes -- ``amount_source_id = template``
        with no template to read -- which ``_rule_within_definition`` answers
        TEMPLATE for and ``_stated_amount`` then refuses, in a money path.

        **Unreachable from the route** (the hard delete that orphans a row also
        404s the Apply POST), so this drives the service entry directly: the
        guard is defence in depth for a published function, and a guard nothing
        exercises is a guard nobody has seen work.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            # The orphan state the FK produces.
            txn.template_id = None
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                user_id=seed_user["user"].id,
            )
            db.session.flush()
            db.session.refresh(txn)

            # Untouched: still the owner's figure, still flagged, never
            # declared -- the one answer that stays true.
            assert txn.amount_source_id is None
            assert txn.estimated_amount == Decimal("999.99")
            assert txn.is_override is True

    def test_resolve_update_prices_a_past_row_from_ITS_date_not_todays(
        self, app, db, seed_user, seed_periods
    ):
        """A handed-back row takes the price in force on its OWN due date.

        **Finding N-244 as a regression test.**  The old "use" arm wrote the
        template's CURRENT ``default_amount`` onto whatever row it was given,
        so resolving an override on a row whose due date preceded a price rise
        back-dated today's figure onto it -- and cleared ``is_override``, the
        one flag that would have marked the row as touched.  A $100 history
        with one row resolved to $120 then read as THREE price changes where
        one had occurred.

        Nothing writes a figure now, so the row resolves through the series on
        its own due date and the rise that came after it cannot reach back.
        The test states the rise AFTER the row's due date and asserts the row
        is worth the OLD price; on the deleted arm it would be worth the new
        one.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            assert txn.due_date is not None
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            # The series has to ANCHOR before the row, or the rise below
            # becomes the earliest version and ``amount_as_of`` holds IT flat
            # backwards -- which is the shape that made a first draft of this
            # test read $120.00 and call the app wrong.
            state_template_price(
                template, Decimal("100.00"),
                effective_on=txn.due_date - timedelta(days=1),
            )
            # The price rises the day AFTER this row's due date.
            state_template_price(
                template, Decimal("120.00"),
                effective_on=txn.due_date + timedelta(days=1),
            )
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                user_id=seed_user["user"].id,
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.estimated_amount is None
            # The price in force on the row's OWN due date, never the newest.
            assert resolved_amount(txn) == Decimal("100.00")

    def test_cross_user_update_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """update with wrong user_id silently skips the transaction."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            # Attempt resolve as second_user -- should be blocked.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                user_id=second_user["user"].id,
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
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
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
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                user_id=seed_user["user"].id,
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            # Handed back to its definition rather than given a figure
            # (plan step balance:X-au-e, ruling R-JD): no stored amount,
            # and what it is worth is the DEFINITION's price, never the figure
            # a caller used to pass in.
            assert txn.estimated_amount is None
            assert resolved_amount(txn) == Decimal("100.00")

    def test_mixed_ownership_list(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """Only owned transactions are modified in a mixed-ownership list."""
        with app.app_context():
            # Create template and transaction for user A.
            template_a = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created_a = recurrence_engine.generate_for_template(
                template_a, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template_a.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            txn_a = created_a[0]
            txn_a.is_override = True
            state_own_amount(txn_a, Decimal("999.99"))

            # Create template and transaction for user B (second_user).
            # second_user needs their own periods and template.
            from app.services import pay_period_service
            periods_b = pay_period_write.record_paydays(
                user_id=second_user["user"].id,
                first_payday=seed_periods[0].start_date,
                num_periods=10, cadence_days=14,
            )
            template_b = self._make_template_with_rule(
                second_user, EVERY_PERIOD, category_key="Rent",
            )
            created_b = recurrence_engine.generate_for_template(
                template_b, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template_b.user_id), {p.id for p in periods_b},
                ), second_user["scenario"].id,
            )
            db.session.flush()
            txn_b = created_b[0]
            txn_b.is_override = True
            state_own_amount(txn_b, Decimal("888.88"))
            db.session.flush()

            # Resolve as user A -- only txn_a should be modified.
            recurrence_engine.resolve_conflicts(
                [txn_a.id, txn_b.id], action="update",
                user_id=seed_user["user"].id,
            )
            db.session.flush()

            db.session.refresh(txn_a)
            db.session.refresh(txn_b)
            assert txn_a.is_override is False
            # Handed back to its definition rather than given a figure
            # (plan step balance:X-au-e, ruling R-JD): no stored amount,
            # and what it is worth is the DEFINITION's price, never the figure
            # a caller used to pass in.
            assert txn_a.estimated_amount is None
            assert resolved_amount(txn_a) == Decimal("100.00")
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
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense")
                .one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                name="Regular Recurring",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            # The definition first, then the cadence onto it (plan step R-F6).
            rule = make_every_period_rule(db.session, template)

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            txn = created[0]
            assert txn.transfer_id is None, (
                "Pre-condition: regular transaction must not be a shadow."
            )
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id],
                action="update",
                user_id=seed_user["user"].id,
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            assert txn.is_deleted is False
            # Handed back to its definition rather than given a figure
            # (plan step balance:X-au-e, ruling R-JD): no stored amount,
            # and what it is worth is the DEFINITION's price, never the figure
            # a caller used to pass in.
            assert txn.estimated_amount is None
            assert resolved_amount(txn) == Decimal("100.00")


class TestCrossUserIsolation:
    """IDOR tests for the recurrence engine."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
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
                seed_user, EVERY_PERIOD
            )

            # SECURITY: Attempt to generate into user B's
            # scenario using user A's template. This should
            # be rejected but currently is not.
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
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

    def _make_template_with_rule(
        self, seed_user, cadence,
        default_amount=Decimal("100.00"), **rule_kwargs,
    ):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring NP',
            default_amount=default_amount,
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
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
                seed_user, EVERY_PERIOD, default_amount=Decimal("0.00")
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            # Engine generates for all periods regardless of amount.
            assert len(created) == len(seed_periods)
            for txn in created:
                assert resolved_amount(txn) == Decimal("0.00")

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
                transaction_type_id=expense_type.id,
                name="No Rule Template",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            db.session.refresh(template)

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
                seed_user, EVERY_PERIOD
            )

            # Initial generation.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), set(),
                ), seed_user["scenario"].id,
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
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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


class TestWhatAGeneratedRowsAmountOWNERSHIPIs:
    """Plan step **balance:X-au-e**: generation states ONE ownership, always.

    **This class was ``TestPaycheckAmountFallback`` and its subject is
    deleted.**  It graded ``_get_transaction_amount``'s exception narrowing --
    that a ``ZeroDivisionError`` or an ``InvalidOperation`` out of the paycheck
    calculator fell back to ``template.default_amount`` while an
    ``AttributeError`` propagated (C-01).  Generation runs no pricing at all
    now, so there is no call to raise and no fallback to narrow.

    **Its second subject is deleted too, and this is that rewrite.**  X-au-d
    replaced the fallback with a FORK -- ``_generated_amount_ownership`` asking
    ``template_amount_service.owns_its_amount``, ``own`` over the scalar for a
    definition that states its price and ``derived`` for one whose price is
    computed -- and X-au-e deletes the fork rather than one of its arms.  There
    is no producer left to unit-test, so both cases below grade the public act
    instead: what ``generate_for_template`` actually WRITES.

    **Both cases are kept even though they now assert the same shape**, and
    that is the point rather than a redundancy: a producer that declared only
    the salary rows -- which is exactly what shipped at X-au-d -- passes the
    first and fails the second.  Deleting the second because it stopped
    distinguishing anything would delete the only case that says the cutover
    happened.
    """

    def _generated_row(self, db, seed_user, seed_periods, template):
        """Generate from *template* and return the first row it wrote."""
        make_cadence_rule(template, EVERY_PERIOD)
        db.session.refresh(template)
        created = recurrence_engine.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            ), seed_user["scenario"].id,
        )
        db.session.flush()
        assert created, "the fixture generated no rows to assert about"
        return created[0]

    def test_a_salary_linked_definition_gives_its_rows_a_DECLARATION(
        self, app, db, seed_user, seed_periods,
    ):
        """A definition an ACTIVE profile names prices its rows by computing.

        So the row names the definition and holds no figure: the state that
        makes a stale paycheck unrepresentable rather than merely unlikely.
        Shipped at X-au-d and unchanged by X-au-e.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the salary models are not
            # this module's subject and importing them at the top would put the
            # paycheck stack on every recurrence test's load path.
            from app.models.ref import FilingStatus  # pylint: disable=import-outside-toplevel
            # Pylint: ``import-outside-toplevel`` -- see above.
            from app.models.salary_profile import SalaryProfile  # pylint: disable=import-outside-toplevel

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=next(iter(seed_user["categories"].values())).id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                name="Paycheck",
                default_amount=Decimal("1500.00"),
            )
            db.session.add(template)
            db.session.flush()
            db.session.add(SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                template_id=template.id,
                name="X-au-d Salary",
                annual_salary=Decimal("104000.00"),
                state_code="NC",
                is_active=True,
            ))
            db.session.flush()

            row = self._generated_row(db, seed_user, seed_periods, template)

            assert row.estimated_amount is None
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )

    def test_a_definition_that_STATES_its_price_ALSO_gives_a_DECLARATION(
        self, app, db, seed_user, seed_periods,
    ):
        """The partner case, and the one plan step X-au-e inverted.

        It asserted ``ownership.figure == Decimal("500.00")`` and
        ``source_id is None`` -- generation handing an ordinary definition's
        rows that definition's scalar to OWN.  That is the copy the cutover
        deleted: the row declares the template and the template's own
        effective-dated series prices it on the row's due date, which is what
        the last assertion here reads.
        """
        with app.app_context():
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=next(iter(seed_user["categories"].values())).id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                name="Rent",
                default_amount=Decimal("500.00"),
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)

            row = self._generated_row(db, seed_user, seed_periods, template)

            assert row.estimated_amount is None
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            # The figure is not lost, it has one home: the definition's series.
            assert resolved_amount(row) == Decimal("500.00")


class TestEndDate:
    """Tests for the optional end_date on recurrence rules."""

    def test_end_date_limits_every_period(self, biweekly_periods):
        """end_date stops generation after that date (every_period)."""
        # End date after the 5th period's start_date (period index 4).
        end = biweekly_periods[4].start_date
        rule = build_rule(cadence=EVERY_PERIOD, end_date=end)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 5
        for p in matched:
            assert p.start_date <= end

    def test_end_date_none_means_indefinite(self, biweekly_periods):
        """NULL end_date generates for all periods (no change from default)."""
        rule = build_rule(cadence=EVERY_PERIOD, end_date=None)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 26

    def test_end_date_with_monthly_pattern(self, biweekly_periods):
        """end_date works with monthly pattern -- only months before end."""
        # End in March 2026.
        rule = build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 15),
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
        rule = build_rule(cadence=EVERY_PERIOD,
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
        rule = build_rule(cadence=EVERY_PERIOD, end_date=end)

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
        rule = build_rule(cadence=EVERY_PERIOD,
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
        rule = build_rule(cadence=EVERY_N_PERIODS, interval_n=3,
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

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring End Date',
            default_amount=Decimal("50.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def test_generate_respects_end_date(self, app, db, seed_user, seed_periods):
        """generate_for_template stops at end_date."""
        with app.app_context():
            # Use the 5th period's start_date as end_date.
            end = seed_periods[4].start_date
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD, end_date=end,
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
                seed_user, EVERY_PERIOD, end_date=end,
            )

            # Initial generation.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(created) == 3

            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Rent'].id,
            transaction_type_id=expense_type.id,
            name='Test Template',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def _make_custom_periods(self, seed_user, *spans):
        """Rebuild the owner's calendar so its periods OPEN on these spans.

        **These built ``PayPeriod(...)`` rows by hand until plan step
        ``pay_calendar:C4-b-1``, and every case in this class passed BECAUSE
        of that.**  A hand-built row sets ``end_date`` itself, and the owner
        had no ``budget.pay_schedule`` row, so
        ``pay_schedule_service.resolve_schedule`` inferred the cadence from
        that same typed end -- the derivation then handed back the span the
        author had written, and the case was measuring its own input.  Give
        the owner the stored cadence a real one has and a typed 28-day
        February derives 14 days and generates nothing.

        ``rebuild_calendar_from_spans`` builds them through the reset door and
        the writer instead.  What that changes for a caller: an INTERIOR
        span's end runs to the day before the next span opens, so a gapped
        request gets wider interior periods than it asked for (a gap is not
        expressible in a derived calendar).  Every case here asserts on the
        occurrence's own DAY, which lands in the same period either way, and
        the LAST span -- the one end the derivation projects from the cadence
        -- is exact.

        **What this does NOT remove, and an adversarial review of the step is
        why it is written down**: the owner's cadence is still the last span's
        length, so the date a case types still decides it.  What is gone is the
        CIRCULARITY -- the cadence is now a stored fact the writer persisted,
        not a value inferred back out of the ``end_date`` the same case typed,
        and a 28-day payer is an owner production can have.  The coupling
        survives one level up and is a caller's choice rather than a loop.

        Args:
            seed_user: The seed_user fixture dict.
            *spans: ``(start, end)`` pairs, ascending, at least one.

        Returns:
            The owner's periods, payday ascending -- one per span.
        """
        return rebuild_calendar_from_spans(seed_user["user"].id, list(spans))

    def _make_custom_period(self, seed_user, start, end):
        """Rebuild the owner's calendar as the ONE period *start*..*end*.

        :meth:`_make_custom_periods` for the single-span cases, where the
        stated end is exact: it is the last span, so the owner's cadence is
        its length and the derivation projects precisely it.

        Args:
            seed_user: The seed_user fixture dict.
            start: The period's start_date, which is the owner's only payday.
            end: The period's end_date.

        Returns:
            The created PayPeriod.
        """
        return self._make_custom_periods(seed_user, (start, end))[0]

    # -- Basic pattern tests ---------------------------------------------------

    def test_due_date_monthly_pattern(self, app, db, seed_user, seed_periods):
        """Monthly pattern with fires_on_day=15 sets due_date to the 15th.

        seed_periods P0 = Jan 2 - Jan 15, which contains Jan 15.
        Expected: txn.due_date == 2026-01-15.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            # Find the transaction assigned to the period containing Jan 15.
            jan_txns = [
                txn for txn in created
                if txn.pay_period_id == _paycheck_covering(
                    seed_user, date(2026, 1, 15),
                )
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
                seed_user, EVERY_PERIOD,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
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
        """fires_on_day=30 clamped to Feb 28 in a non-leap year (2026).

        Custom period covers all of February to ensure Feb 28 falls inside.
        min(30, 28) = 28, so due_date == 2026-02-28.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 2, 1), date(2026, 2, 28),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=30,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 2, 28)

    def test_due_date_feb_leap_year(self, app, db, seed_user):
        """fires_on_day=29 in Feb 2028 (leap year) gives Feb 29.

        2028 is a leap year, so min(29, 29) = 29.
        due_date == 2028-02-29.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2028, 2, 1), date(2028, 2, 29),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=29,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2028, 2, 29)

    def test_due_date_day31_in_30day_month(self, app, db, seed_user):
        """fires_on_day=31 clamped to 30 in April (30-day month).

        April has 30 days: min(31, 30) = 30.
        due_date == 2026-04-30.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 4, 1), date(2026, 4, 30),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=31,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 4, 30)

    # -- due_day_of_month (next-month convention) tests ------------------------

    def test_due_day_next_month_convention(
        self, app, db, seed_user, seed_periods
    ):
        """due_day_of_month=1 < fires_on_day=22: due date in the next month.

        P1 = Jan 16 - Jan 29, which contains Jan 22. Since due_dom(1) <
        dom(22), the due date rolls to the next month: Feb 1.
        due_date == 2026-02-01.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY,
                fires_on_day=22, due_day_of_month=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            # P1 (Jan 16-29) contains Jan 22.
            p1_txns = [
                txn for txn in created
                if txn.pay_period_id == seed_periods[1].id
            ]
            assert len(p1_txns) == 1
            assert p1_txns[0].due_date == date(2026, 2, 1)

    def test_due_day_same_month(self, app, db, seed_user):
        """due_day_of_month=15 > fires_on_day=1: due date in the same month.

        Custom period Jan 1-14 contains Jan 1. Since due_dom(15) >=
        dom(1), the due date stays in the same month: Jan 15.
        due_date == 2026-01-15.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 1, 1), date(2026, 1, 14),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY,
                fires_on_day=1, due_day_of_month=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 1, 15)

    def test_due_day_dec_to_jan_rollover(self, app, db, seed_user):
        """due_day_of_month=1 < fires_on_day=22 in December rolls to Jan next year.

        Custom period Dec 15-28 contains Dec 22. Since due_dom(1) <
        dom(22), the due date rolls to the next month. December + 1 =
        January of the next year. due_date == 2027-01-01.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 12, 15), date(2026, 12, 28),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY,
                fires_on_day=22, due_day_of_month=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
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
                seed_user, MONTHLY,
                fires_on_day=15, due_day_of_month=None,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            jan_txns = [
                txn for txn in created
                if txn.pay_period_id == _paycheck_covering(
                    seed_user, date(2026, 1, 15),
                )
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
                seed_user, MONTHLY,
                fires_on_day=15, due_day_of_month=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            jan_txns = [
                txn for txn in created
                if txn.pay_period_id == _paycheck_covering(
                    seed_user, date(2026, 1, 15),
                )
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
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            db.session.refresh(template)

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert created == []

    # -- Quarterly and annual patterns -----------------------------------------

    def test_due_date_quarterly_pattern(self, app, db, seed_user):
        """Quarterly pattern with fires_in_month=1 produces due dates in Jan, Apr, Jul, Oct.

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
            periods = self._make_custom_periods(seed_user, *quarters)

            template = self._make_template_with_rule(
                seed_user, QUARTERLY,
                fires_in_month=1, fires_on_day=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), seed_user["scenario"].id,
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
        """Annual pattern with fires_in_month=10, fires_on_day=1 gives Oct 1.

        Custom period covers October 2026.
        due_date == 2026-10-01.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 10, 1), date(2026, 10, 31),
            )
            template = self._make_template_with_rule(
                seed_user, ANNUAL,
                fires_in_month=10, fires_on_day=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 10, 1)

    def test_due_date_semi_annual_pattern(self, app, db, seed_user):
        """Semi-Annual with fires_in_month=1 produces due dates in Jan and Jul.

        Two custom periods cover Jan and Jul. fires_on_day=15.
        """
        with app.app_context():
            periods = self._make_custom_periods(
                seed_user,
                (date(2026, 1, 1), date(2026, 1, 31)),
                (date(2026, 7, 1), date(2026, 7, 31)),
            )
            template = self._make_template_with_rule(
                seed_user, SEMI_ANNUAL,
                fires_in_month=1, fires_on_day=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 2
            actual_dates = sorted(txn.due_date for txn in created)
            assert actual_dates == [date(2026, 1, 15), date(2026, 7, 15)]

    # -- Period spanning two months --------------------------------------------

    def test_due_date_period_spanning_two_months(self, app, db, seed_user):
        """fires_on_day=1, period Jan 17 - Feb 1: due_date is Feb 1, not Jan 1.

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
                seed_user, MONTHLY, fires_on_day=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 2, 1)

    # -- due_day_of_month clamping tests ---------------------------------------

    def test_due_day_same_month_clamping(self, app, db, seed_user):
        """due_day_of_month=31 in April (30 days) clamped to 30.

        fires_on_day=15, due_day_of_month=31. Since 31 >= 15, the due
        date stays in the same month (April). min(31, 30) = 30.
        due_date == 2026-04-30.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 4, 1), date(2026, 4, 30),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY,
                fires_on_day=15, due_day_of_month=31,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 4, 30)

    def test_due_day_next_month_feb_clamping(self, app, db, seed_user):
        """Next-month convention with due_dom clamped in February.

        fires_on_day=31 in January, due_day_of_month=30. Since
        due_dom(30) < dom(31), next-month convention applies: the due
        date falls in February. February 2026 has 28 days, so
        min(30, 28) = 28. due_date == 2026-02-28.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 1, 17), date(2026, 1, 31),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY,
                fires_on_day=31, due_day_of_month=30,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            # dom=31 in Jan, due_dom=30 < 31 so next month = Feb.
            # Feb 2026 has 28 days: min(30, 28) = 28.
            assert created[0].due_date == date(2026, 2, 28)

    # -- Pure function test for compute_due_date ------------------------------

    def test_compute_due_date_is_pure_function(self, app, db):
        """compute_due_date does not touch the database -- it's a pure function.

        Constructs a rule and a REAL :class:`DerivedPeriod` to verify that
        compute_due_date can produce correct results without any DB
        interaction.

        **The period is the real value and not ``FakePeriod``** since
        pay-calendar plan step C4-a-3, which measured the double out of
        contract: this function's documented parameter is a ``DerivedPeriod``,
        it now asks that value's own ``covers``, and the duck-typed stand-in
        answered ``AttributeError``.  A ``DerivedPeriod`` is a frozen
        dataclass over five plain fields, so building one costs the purity
        claim nothing -- the fake was never buying anything the real type
        does not give.
        """
        with app.app_context():
            from app import ref_cache
            # Test with a day-of-month cadence (monthly-style).
            rule_monthly = build_rule(
                cadence=MONTHLY, starts_on=date(2026, 1, 20),
            )
            period = DerivedPeriod(
                period_id=1,
                period_index=5,
                start_date=date(2026, 3, 13),
                end_date=date(2026, 3, 26),
                end_is_projected=False,
            )
            result = recurrence_engine.compute_due_date(
                rule_monthly, period,
            )
            assert result == date(2026, 3, 20)

            # Test with a cadence that names no day (every-period style).
            rule_every = build_rule(cadence=EVERY_PERIOD)
            result = recurrence_engine.compute_due_date(
                rule_every, period,
            )
            assert result == date(2026, 3, 13)

    def test_this_function_is_why_the_WEEK_unit_is_unofferable(self, app, db):
        """The tie between the offer set's rule and this function's two sources.

        **``_frequency.has_row_date_coordinate`` exists for THIS function**, and
        until this case nothing but a docstring said so -- which an adversarial
        review of plan step R8-a named as the coupling's weak point: if a later
        step gave ``compute_due_date`` a third date source, that predicate
        would go on withholding the ``WEEK`` unit with every gate green.

        The two sources are all there are, and the assertions below are them:
        the rule's scheduling DAY OF THE MONTH, and -- when it has none -- the
        funding paycheck's own ``start_date``.  For the ``PERIOD`` unit the
        second IS the occurrence, so a row dated from it carries the date the
        cadence named.  For the ``WEEK`` unit it is NOT: a weekly occurrence is
        a calendar date strictly inside its paycheck, so dating from the
        paycheck discards the authored weekday for the life of the rule.

        That is why the unit is withheld rather than refused at the door, and
        it is why plan step **R5** -- which gives a generated row its own
        ``occurs_on`` -- is what deletes both the predicate and this case.
        """
        with app.app_context():
            period = DerivedPeriod(
                period_id=1,
                period_index=5,
                start_date=date(2026, 3, 13),
                end_date=date(2026, 3, 26),
                end_is_projected=False,
            )
            starts_on = date(2026, 1, 20)

            # A hand-edited or restored row naming the WEEK unit: the one way
            # to reach this state, since the write door and the picker both
            # refuse the cadence.
            weekly = build_rule(
                cadence=MONTHLY, starts_on=starts_on,
            )
            weekly.unit_id = ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.WEEK,
            )

            with pytest.raises(
                RecurrenceResolutionError, match="generated row",
            ):
                recurrence_engine.compute_due_date(weekly, period)

            # And what the refusal is standing in front of: the paycheck's own
            # start, which is not any date a weekly cadence from ``starts_on``
            # fires on.  Both facts asserted, because the refusal is only worth
            # having while the fallback would be WRONG.
            weekly_occurrences = {
                starts_on + timedelta(days=7 * step) for step in range(60)
            }
            assert period.start_date not in weekly_occurrences
            assert weekly_occurrences & {
                date(2026, 3, 17), date(2026, 3, 24),
            } == {date(2026, 3, 17), date(2026, 3, 24)}


class TestARowRecordsItsOccurrence:
    """``occurs_on`` -- WHICH occurrence a generated row answers.

    Plan step **R17**, the first leaf of **R5**, closing ledger row **D57**:
    both engines decided "has this already been created" by asking whether a
    PAY PERIOD held a row, so a row the owner moved to a neighbouring paycheck
    emptied the period its occurrence named and the next whole-schedule pass
    wrote a second one -- 8 rows / $1,482.93 from ONE pass on a production
    clone, seven already ``Paid``.

    The FIRST leaf made the row state its occurrence; the SECOND moved every
    predicate onto it, re-keyed both unique indexes and deleted the D19
    refusal.  So these controls now pin both halves: that the value is written,
    is the CADENCE's date and not the row's ``due_date``, and survives a
    maintain pass and a move -- AND that the generation decision itself reads
    it, which is what closes D57.
    """

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Build a template with an AUTHORED rule.

        The same door ``TestRecurrenceGeneration`` uses; duplicated here rather
        than shared because the two classes seed different owners and pylint's
        duplicate-code checker does not read test bodies.
        """
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Occurrence Recorder',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        make_cadence_rule(template, cadence, **rule_kwargs)
        db.session.flush()
        db.session.refresh(template)
        return template

    def test_generate_writes_the_date_the_cadence_names(
        self, app, db, seed_user, seed_periods
    ):
        """Every created row carries its own occurrence, not its period's date.

        The set equality is the load-bearing half: it fails both if a row is
        written without an occurrence and if two rows are given the same one.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, None,
                block_message="test",
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert created, "the control needs the engine to create something"
            assert all(row.occurs_on is not None for row in created)
            assert (
                sorted(row.occurs_on for row in created)
                == sorted(p.occurrence for p in plan.placements)
            )

    def test_the_occurrence_is_not_the_due_date(
        self, app, db, seed_user, seed_periods
    ):
        """A ``Monthly First`` row occurs on the 1st and is DATED on the payday.

        This is the case that refutes reading ``occurs_on`` off ``due_date``:
        ``compute_due_date`` dates a day-less cadence from its period's start,
        so the two columns disagree by design.  Measured on production at 30 of
        780 rows, 27 of them one ``Phone Allowance`` rule.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, MONTHLY_FIRST)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert created, "the control needs a Monthly First row"
            assert all(row.occurs_on.day == 1 for row in created), (
                "a Monthly First occurrence is the 1st of its month"
            )
            assert any(row.occurs_on != row.due_date for row in created), (
                "the control is measuring nothing if the two columns agree"
            )

    def test_occurs_on_is_not_a_derived_field(self):
        """The maintain pass's ``setattr`` loop must not be able to reach it.

        ``_apply_maintain_work`` assigns every member of
        :class:`DerivedRowFields` onto a maintained row.  ``occurs_on`` says
        WHICH occurrence the row answers -- what the row IS, not what its
        template currently says -- so adding it to that tuple would let a
        regeneration rewrite a row's identity.

        **Asserted structurally rather than by mutation, and plan step R17's
        second leaf is why.**  The behavioural version set the column to a
        sentinel date and ran a maintain pass; since a row is now NAMED by its
        occurrence, a sentinel makes the row an orphan and the pass correctly
        RETIRES it, so that control could no longer distinguish "the loop did
        not write it" from "the row is gone".  The membership test asks the
        question directly and cannot be confounded that way.
        """
        assert "occurs_on" not in DerivedRowFields._fields

    def test_a_maintained_row_keeps_its_id_and_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """A row the rule still names survives a template edit intact.

        The behavioural half of the control above: an edit that changes every
        derived column while leaving the CADENCE alone must maintain the row in
        place -- same id, same ``occurs_on`` -- rather than replace it.  The id
        is the assertion that matters, because a replaced row loses the
        purchases, notes and envelope state plan step R10-a exists to keep.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            before = {row.id: row.occurs_on for row in created}
            assert before, "no rows to maintain -- the fixture proves nothing"

            # Every derived column moves; the cadence does not.
            template.name = "Renamed"
            template.default_amount = template.default_amount + 1
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            after = {
                row.id: row.occurs_on
                for row in db.session.query(Transaction)
                .filter_by(template_id=template.id, is_deleted=False).all()
            }
            assert after == before, (
                "a template edit that left the cadence alone replaced rows "
                "instead of maintaining them"
            )

    def test_a_row_whose_occurrence_the_rule_dropped_is_retired(
        self, app, db, seed_user, seed_periods
    ):
        """The developer's ruling of 2026-08-28, as a control.

        When a rule EDIT moves the occurrence set out from under an existing
        row, that row is NOT re-pointed at whatever occurrence is left in its
        paycheck -- it is retired, and held back as a conflict instead when it
        carries the owner's own records (the neighbouring retain cases).

        Re-pointing was the alternative, and it is the same invalid inference
        an adversarial review cut from ``scripts/stamp_occurrences.py``: it is
        a deduction only if every row answers some occurrence, which a NULL
        ``occurs_on`` denies.  A wrongly adopted row then SUPPRESSES generation
        of the real bill, which is a payment vanishing with nothing on screen
        to show it.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert created, "no rows -- the fixture proves nothing"
            original_ids = [row.id for row in created]
            original_occurrences = {row.occurs_on for row in created}

            # Move the cadence's DAY: every occurrence moves with it.
            rule = template.recurrence_rule
            reauthor_rule(
                rule,
                replace(
                    recurrence_spec(rule),
                    starts_on=rule.starts_on.replace(day=19),
                ),
                calendar_for(template.user_id),
            )
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            # The premise: the edit really did move the occurrence set.
            surviving = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id, is_deleted=False).all()
            )
            assert {row.occurs_on for row in surviving} != original_occurrences

            # No original row was re-pointed -- each was retired and replaced.
            for row_id in original_ids:
                assert db.session.get(Transaction, row_id) is None

    def test_a_moved_row_is_not_regenerated_in_the_period_it_left(
        self, app, db, seed_user, seed_periods
    ):
        """**Ledger row D57 itself, as a regression control.**

        The defect the whole of plan step R17 exists to close, and until this
        test nothing in the suite failed if the leaf were reverted wholesale:
        an adversarial review restored the pre-R17 pay-period question in
        ``OccurrenceClaims`` and ``_claims_against`` and ran the ten most
        relevant files -- **537 passed**.  Every other second-pass case
        re-runs over an UNMOVED schedule, where "this period holds a row" and
        "this occurrence is answered" are the same set, so none of them can
        tell the two keyings apart.

        Measured on a production clone before the fix: one whole-schedule pass
        wrote **8 rows / $1,482.93**, six of them duplicating a due date a
        ``Paid`` row already covered.

        **The ``is_override = True`` half is load-bearing and asserted
        separately below.**  ``_claims_against`` deliberately does not filter on
        it -- a moved row is the owner's, and it still answers its occurrence.
        An override-filtered fetch would reintroduce D57 exactly, and no other
        test in the suite would notice.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            first = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first) >= 2, "the fixture needs a neighbour to move into"

            # What the PATCH door does to a template-linked row whose period
            # moves: the period changes, the row becomes the owner's, and
            # ``occurs_on`` is untouched.
            moved, neighbour = first[0], first[1]
            vacated = moved.pay_period_id
            answered = moved.occurs_on
            moved.pay_period_id = neighbour.pay_period_id
            moved.is_override = True
            db.session.flush()

            second = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert second == [], (
                f"the pass re-filled period {vacated}, which the moved row "
                f"left -- that is D57"
            )
            assert db.session.query(Transaction).filter_by(
                template_id=template.id, occurs_on=answered,
            ).count() == 1, (
                "the occurrence the moved row answers is now answered twice"
            )
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == len(first)

    def test_an_overridden_row_still_answers_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """The half of the case above that a narrower fetch would drop.

        Stated on its own because it is a one-word change away from being
        wrong: adding ``is_override = FALSE`` to ``_claims_against``'s filter
        would look like tightening the query to "the rule's own rows" and would
        restore D57 in full, since every moved row is an override row.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            row = created[0]
            row.is_override = True
            db.session.flush()

            assert recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            ) == [], "an overridden row stopped claiming its occurrence"

    def test_a_maintained_row_is_derived_from_its_OWN_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """A row selected by occurrence must be derived by occurrence.

        **The crash an adversarial review of this leaf found.**
        ``classify_maintain_work`` routes a row to ``update`` when its
        ``occurs_on`` is named; ``_apply_maintain_work`` then read
        ``derived[row.pay_period_id]``, and ``derived`` holds only the periods
        the rule NAMES.  A row whose occurrence is named while its period is
        not raised ``KeyError`` -- a 500 -- and where the landing period
        happened to be named it silently re-derived the row's amount and due
        date from the WRONG paycheck.

        The door is live and needs no corrupt data: move a row, edit the
        template so the chooser reports it, then choose "use the template's
        amount" -- ``resolve_conflicts`` clears ``is_override`` without
        relocating the row or touching ``occurs_on``.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert created, "no rows -- the fixture proves nothing"

            named_periods = {
                row.period.period_id
                for row in recurrence_engine.resolve_generation_plan(
                    template, schedule, seed_user["scenario"].id, None,
                    block_message="test",
                ).placements
            }
            unnamed = [
                period.id for period in seed_periods
                if period.id not in named_periods
            ]
            assert unnamed, (
                "a monthly rule must leave some biweekly paycheck unnamed, or "
                "this case cannot be built"
            )

            # The state the conflict chooser's "use the template's amount"
            # leaves behind: moved, and no longer the owner's.
            row = created[0]
            answered, was_due = row.occurs_on, row.due_date
            row.pay_period_id = unnamed[0]
            row.is_override = False
            db.session.flush()

            # No KeyError, and the row keeps its OWN occurrence's figures.
            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            db.session.refresh(row)
            assert row.occurs_on == answered
            assert row.due_date == was_due, (
                "the row was re-derived from the paycheck it was moved into "
                "rather than from the occurrence it answers"
            )

    def test_a_rule_edit_that_moves_the_occurrence_inside_one_paycheck_retires(
        self, app, db, seed_user, seed_periods
    ):
        """The ruling of 2026-08-28, on the fixture that can actually see it.

        A neighbouring case moves the rule's day far enough to move the
        PAY PERIOD too, so a period-keyed classifier and an occurrence-keyed
        one agree and the case discriminates nothing -- an adversarial review
        measured exactly that by reverting ``classify_maintain_work`` to the
        period question and watching the whole class stay green.

        Day 5 to day 7 is the fixture that bites: both fall inside the same
        biweekly paycheck, so the PERIOD is unchanged and only the OCCURRENCE
        moves.  The ruling says such a row is retired and replaced, never
        re-pointed at whatever occurrence is left in its paycheck.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert created, "no rows -- the fixture proves nothing"
            before = {row.id: (row.pay_period_id, row.occurs_on)
                      for row in created}

            rule = template.recurrence_rule
            reauthor_rule(
                rule,
                replace(
                    recurrence_spec(rule),
                    starts_on=rule.starts_on.replace(day=7),
                ),
                calendar_for(template.user_id),
            )
            db.session.flush()

            after_periods = {
                row.period.period_id
                for row in recurrence_engine.resolve_generation_plan(
                    template, schedule, seed_user["scenario"].id, None,
                    block_message="test",
                ).placements
            }
            # The premise that makes this case discriminating: the PERIODS did
            # not move, so a period-keyed classifier would still call every
            # original row named.
            assert after_periods == {p for p, _ in before.values()}, (
                "the edit moved the pay periods too, so this fixture cannot "
                "tell a period-keyed classifier from an occurrence-keyed one"
            )

            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            for row_id in before:
                assert db.session.get(Transaction, row_id) is None, (
                    "a row whose occurrence the rule dropped was re-pointed "
                    "instead of retired"
                )

    def test_a_row_moved_OUT_OF_THE_WINDOW_still_answers_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """**D57 on the REGENERATE path**, which the generate fix did not close.

        An adversarial review of this leaf measured it at the service seam.
        ``rows_this_pass_may_maintain`` builds its domain from a PERIOD set --
        the pass's write window, bounded by ``effective_from`` -- while the
        plan bounds PLACEMENTS on the placed period's end.  So a row the owner
        moved to an earlier paycheck drops out of that domain while its
        occurrence stays named, and the create arm answers the occurrence a
        second time.  ``rows_claiming`` is period-unscoped precisely because a
        moved row is not where its occurrence is, and the maintain path was
        still deciding CREATE from the window-scoped read.

        Both variants are bad: while the moved row is still ``is_override`` it
        sits outside both partial indexes and the duplicate is SILENT -- D57
        exactly -- and once the conflict chooser has cleared that flag the
        second write is an unhandled ``IntegrityError`` that rolls the whole
        regeneration back.

        The live door is the salary one: ``routes/salary/_helpers`` regenerates
        with ``effective_from=date.today()`` on every profile save, so an owner
        who moved a paycheck row back one period reaches this by saving a
        salary profile.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            schedule = GenerationSchedule.for_pass(
                BalanceContext.build(template.user_id),
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            assert len(created) >= 2, "the fixture needs two named paychecks"

            named = sorted(
                {row.period.period_id: row.period
                 for row in recurrence_engine.resolve_generation_plan(
                     template, schedule, scenario_id, None,
                     block_message="test",
                 ).placements}.values(),
                key=lambda period: period.start_date,
            )
            unnamed = [
                period for period in seed_periods
                if period.id not in {p.period_id for p in named}
            ]
            assert unnamed, "a monthly rule must leave a paycheck unnamed"

            # Move the row answering the LAST named occurrence back to the
            # earliest paycheck, and bound the pass so that paycheck is
            # outside its window -- the salary door's own shape.
            latest = max(created, key=lambda row: row.occurs_on)
            answered = latest.occurs_on
            latest.pay_period_id = unnamed[0].id
            latest.is_override = True
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template, schedule, scenario_id,
                effective_from=named[-1].start_date,
            )
            db.session.flush()

            assert db.session.query(Transaction).filter_by(
                template_id=template.id, occurs_on=answered,
            ).count() == 1, (
                f"occurrence {answered} is answered twice -- the maintain "
                f"pass decided CREATE from a window-scoped read and could not "
                f"see the row that moved out of it"
            )

    def test_an_undated_row_claims_its_whole_paycheck(
        self, app, db, seed_user, seed_periods
    ):
        """The rule that is worth ``$20,500``, with a control of its own.

        A row whose ``occurs_on`` is NULL answers no occurrence, so the only
        claim it can make is the pre-R17 one: it holds the paycheck it sits in.
        Letting it claim NOTHING was measured at the unarchive door on the
        developer's archived ``Emergency Fund`` template -- **52 rows /
        $26,000** written where the correct answer is 11 / $5,500, 41 phantom
        transfers beside rows the owner had deleted.

        **Stated as its own named case because it was resting on a side effect
        of another test's fixture**, which an adversarial review found: the
        extendability case happens to build undated rows, so it was the only
        thing in the suite that would fail if this rule were dropped -- and its
        name and docstring give a future author no reason to keep the fixture
        undated.

        Both states are covered, because the measured scenario is the
        SOFT-DELETED one: the unarchive door restores rows the owner removed
        and then generates, and those rows are undated because the backfill
        does not walk an archived template.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) >= 2

            live, removed = created[0], created[1]
            live.occurs_on = None
            removed.occurs_on = None
            removed.is_deleted = True
            db.session.flush()

            again = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert again == [], (
                "an undated row stopped holding its paycheck -- a live one, a "
                "soft-deleted one, or both"
            )
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == len(created)

    def test_the_predictor_reads_the_claims_of_rows_that_exist(
        self, app, db, seed_user, seed_periods
    ):
        """``can_generate_in_period`` against a POPULATED table.

        The existing agreement case calls the predictor before generation, on a
        table holding no row for the template -- so step 4, the claims read and
        the whole of what plan step R17 changed, is a no-op in every assertion
        it makes.  An adversarial review measured that.

        Both directions R17 added to step 4 are asserted here: a row the owner
        MOVED out of a period must still block that period's occurrence, and a
        row that answers an occurrence must block it wherever it sits.  The
        carry-forward executor acts on this prediction and then calls
        generation, so a predictor that drifts writes real rows -- which is
        defect D22 exactly, and cost 32 of 61 periods when the two last
        disagreed.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            assert len(created) >= 2

            # Every period is answered, so the predictor must say NO everywhere.
            for row in created:
                assert recurrence_engine.can_generate_in_period(
                    template, row.pay_period_id, scenario_id, schedule=schedule,
                ) is False, "the predictor offered a period already answered"

            # Move one row out of its period.  The period it LEFT is still
            # answered -- by the row that moved -- so the predictor must still
            # refuse it.  A period-keyed predictor says yes here, and the
            # carry-forward executor would then write a duplicate.
            moved, neighbour = created[0], created[1]
            vacated = moved.pay_period_id
            moved.pay_period_id = neighbour.pay_period_id
            moved.is_override = True
            db.session.flush()

            assert recurrence_engine.can_generate_in_period(
                template, vacated, scenario_id, schedule=schedule,
            ) is False, (
                "the predictor offered the period a moved row left, whose "
                "occurrence that row still answers -- D57 through the "
                "prediction door"
            )

    def test_a_moved_row_keeps_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """Moving a row to another paycheck changes its FUNDING, not its cadence.

        The whole point of the column, and the shape of ledger row **D57**:
        ``pay_period_id`` is what the owner may move and ``occurs_on`` is what
        no move touches.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            row, neighbour = created[0], created[1]
            was = row.occurs_on
            assert was is not None
            # What the PATCH door does to a template-linked row whose period
            # moves (``routes/transactions/mutations``): the period changes and
            # the row becomes the owner's.
            row.pay_period_id = neighbour.pay_period_id
            row.is_override = True
            db.session.flush()
            db.session.refresh(row)

            assert row.occurs_on == was
            assert row.pay_period_id == neighbour.pay_period_id

    def test_maintain_creates_a_missing_row_with_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """The maintain pass's CREATE arm states an occurrence too.

        ``MaintainWork.create_in`` held bare period ids before this step, so
        the create arm had nothing to write.  Deleting one generated row and
        re-running the pass is what reaches that arm.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            gap = created[0]
            gap_period_id, gap_occurrence = gap.pay_period_id, gap.occurs_on
            db.session.delete(gap)
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            refilled = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id,
                           pay_period_id=gap_period_id)
                .one()
            )
            assert refilled.occurs_on == gap_occurrence, (
                "the maintain create arm wrote no occurrence, or the wrong one"
            )
