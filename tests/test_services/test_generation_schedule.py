"""R4b-1: a generate pass reads the OWNER's schedule, not the caller's window.

Both recurrence engines took ONE ``periods`` list and used it for two unrelated
jobs -- the schedule each rule was RESOLVED against, and the set of periods the
pass could WRITE into.  ``period_population`` hands them only the newly created
periods, so every schedule extend re-read every rule as though the owner's pay
history began at the new batch.

Three measured consequences, all live on production (2026-08-08, against a
streamed clone of ``shekel-prod-db``), one class each below:

* **D22** -- a ``Monthly First`` rule re-fired in a month it had already
  covered: 3 spurious ``Phone Allowance`` rows, $118.62, one per extend landing
  a new period in a covered month.
* **D25** -- ``calculate_paycheck`` received the same truncated list as its
  ``all_periods``, so third-paycheck detection read 1-3 periods instead of 61.
  One salary row was STORED $502.45 below its true net pay.  The read-time
  recompute (``income_service.live_projected_net``) kept that figure off every
  surface, so what it endangered was the stored cache the grid's inline editor
  pre-fills from -- see the migration's docstring for the measurement.
* **D2** -- a rule's chosen start period was not in the window, so the opening
  bound it states was dropped.

**What was shown RED against the pre-R4b-1 code, and by what means.**  The
authority is the production path, not a ported test file: a ``git worktree`` at
``HEAD`` driving ``pay_period_admin.extend_pay_periods`` six times over a
streamed clone of ``shekel-prod-db``, against the same six on this code.

```text
                              HEAD          this code
duplicate Monthly First months   3 NEW           0
salary rows disagreeing with a
  whole-schedule recompute       1 ($502.45)     0
```

Two of the classes below were additionally ported back onto the old
``periods``-list signature and run at ``HEAD``, where they fail on their own
assertions: ``TestTheStartPeriodBoundSurvivesTheWindow`` generates two rows
into paychecks the rule's start period excludes, and
``TestThePredictionIsTheGenerationCall``'s predictor names all ten periods
where the engine fires in five.

**A textual port is lossy, and saying so is the point.**  An earlier draft of
this docstring claimed all four defects had been shown red that way.  Two of
them had not: the port's own rewrite had left a ``NameError``, and a test that
fails to import is not a test that fails.  An adversarial review caught it by
redoing the port.  The classes with no ``HEAD`` counterpart at all --
``TestTheWindowStillNarrowsWhatIsWritten``, ``TestAWindowMustBelongToTheOwner``,
``TestOneScheduleReadServesTheWholeRequest``,
``TestRegenerateSweepsOnlyWhatItRewrites`` and
``TestABoundedRuleDoesNotRestartItsCount`` -- are forward guards over shapes
this step created or an adversarial review named, not reproductions of a
measured defect, and they are labelled as such rather than counted as
evidence.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import RecurrencePatternEnum, StatusEnum, TxnTypeEnum
from app.exceptions import RecurrenceWindowError
from app import ref_cache
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import CalcMethod, DeductionTiming, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import pay_period_service, pay_period_write, period_population, recurrence_engine
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    seed_fica_config,
    seed_state_tax_config,
    seed_tax_bracket_set,
)

# ``seed_periods`` runs 10 biweekly periods from 2026-01-02, so the paydays are
# 01-02, 01-16, 01-30, 02-13, 02-27, 03-13, 03-27, 04-10, 04-24, 05-08.
# Two facts below depend on that shape and are asserted rather than assumed:
#   * JANUARY holds THREE of them (indices 0, 1, 2) -- the shape that produced
#     the $502.45 error;
#   * MAY holds one so far (index 9), so appending period 10 (2026-05-22) lands
#     a NEW period in a month a ``Monthly First`` rule has already covered --
#     the shape that produced the duplicate rows.
_JANUARY_THIRD_PAYCHECK_INDEX = 2
_LAST_SEEDED_INDEX = 9


def _make_template(seed_user, pattern_enum, **rule_kwargs):
    """Create a recurring expense template and its rule.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        pattern_enum: The :class:`~app.enums.RecurrencePatternEnum` member.
        **rule_kwargs: Extra columns for the rule (``day_of_month``,
            ``start_date``, ...).

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`.
    """
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=ref_cache.recurrence_pattern_id(pattern_enum),
        interval_n=rule_kwargs.pop("interval_n", 1),
        offset_periods=rule_kwargs.pop("offset_periods", 0),
        **rule_kwargs,
    )
    db.session.add(rule)
    db.session.flush()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Car Payment"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Phone Allowance",
        default_amount=Decimal("39.54"),
    )
    db.session.add(template)
    db.session.flush()
    return template


def _rows(template, scenario_id):
    """Return the template's rows keyed by their period's start date.

    Args:
        template: The template whose rows to read.
        scenario_id: The scenario to read within.

    Returns:
        ``{start_date: [Transaction, ...]}``, one entry per period holding a
        row.
    """
    result: dict[date, list[Transaction]] = {}
    rows = (
        db.session.query(Transaction)
        .filter_by(template_id=template.id, scenario_id=scenario_id)
        .all()
    )
    for row in rows:
        result.setdefault(row.pay_period.start_date, []).append(row)
    return result


def _append_period(seed_user, seed_periods):
    """Append one 14-day pay period after the seeded schedule.

    Reproduces what ``pay_period_admin.extend_pay_periods`` creates before it
    calls ``period_population``: a flushed batch of new periods, and nothing
    else changed.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        seed_periods: The seeded schedule.

    Returns:
        The new ``[PayPeriod]`` batch, flushed.
    """
    created = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=seed_periods[-1].end_date + timedelta(days=1),
        num_periods=1,
        cadence_days=14,
    )
    db.session.flush()
    return created


class TestTheScheduleShapeTheseTestsRestOn:
    """The fixture facts every class below depends on, asserted not assumed."""

    def test_january_holds_three_paychecks(self, app, seed_user, seed_periods):
        """Index 2 (2026-01-30) is January's THIRD payday.

        The D25 class needs a third paycheck to exist; a fixture change that
        removed one would otherwise make those tests pass vacuously.
        """
        with app.app_context():
            january = [
                period for period in seed_periods
                if period.start_date.year == 2026
                and period.start_date.month == 1
            ]
            assert [period.period_index for period in january] == [0, 1, 2]
            assert (
                seed_periods[_JANUARY_THIRD_PAYCHECK_INDEX].start_date
                == date(2026, 1, 30)
            )

    def test_appending_one_period_lands_in_an_already_covered_month(
        self, app, db, seed_user, seed_periods,
    ):
        """The appended period opens in MAY, which index 9 already covers.

        The D22 class needs the new period to fall in a month the rule has
        already fired in; on a schedule where it did not, no duplicate could
        arise and the test would prove nothing.
        """
        with app.app_context():
            assert seed_periods[_LAST_SEEDED_INDEX].start_date == date(2026, 5, 8)
            appended = _append_period(seed_user, seed_periods)
            assert appended[0].start_date == date(2026, 5, 22)
            assert appended[0].start_date.month == 5


class TestAnExtendDoesNotDuplicateAMonthlyFirstRow:
    """D22: the window's own first payday is not the owner's."""

    def test_appending_a_period_to_a_covered_month_adds_no_second_row(
        self, app, db, seed_user, seed_periods,
    ):
        """A month already holding the rule's row gains nothing on an extend.

        ``Monthly First`` fires on the 1st of each month and places on the
        first PAYCHECK at or after it, so May 2026 is owed exactly one row --
        the 2026-05-08 one.  Resolved against the extend's own window the
        anchor became 2026-05-01 all over again, because 2026-05-22 is the
        window's earliest May payday, and the placement then produced a second
        row.  Measured on production: 3 such rows, $39.54 each.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, RecurrencePatternEnum.MONTHLY_FIRST,
            )
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(seed_user["user"].id),
                scenario_id,
            )
            db.session.flush()
            before = _rows(template, scenario_id)
            assert date(2026, 5, 8) in before, (
                "May's first paycheck must already hold the row, or the "
                "duplicate this test guards against cannot arise"
            )

            appended = _append_period(seed_user, seed_periods)
            period_population.populate_periods_from_active_templates(
                seed_user["user"].id, appended,
            )
            db.session.flush()

            after = _rows(template, scenario_id)
            may_rows = [
                start for start in after
                if start.year == 2026 and start.month == 5
            ]
            assert may_rows == [date(2026, 5, 8)], (
                f"May 2026 is owed one row and holds {sorted(may_rows)}"
            )
            assert len(after) == len(before)

    def test_a_month_the_extend_first_reaches_is_still_generated(
        self, app, db, seed_user, seed_periods,
    ):
        """The narrowing removes duplicates, not the rows that are owed.

        The paired half of the test above: an extend that opens a NEW calendar
        month must still generate that month's row, or the fix would have
        traded a duplicate for a silent omission.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, RecurrencePatternEnum.MONTHLY_FIRST,
            )
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(seed_user["user"].id),
                scenario_id,
            )
            db.session.flush()

            # Two appends: 2026-05-22 (May, covered) then 2026-06-05, which
            # opens June.
            first = _append_period(seed_user, seed_periods)
            period_population.populate_periods_from_active_templates(
                seed_user["user"].id, first,
            )
            second = _append_period(seed_user, seed_periods + first)
            assert second[0].start_date == date(2026, 6, 5)
            period_population.populate_periods_from_active_templates(
                seed_user["user"].id, second,
            )
            db.session.flush()

            after = _rows(template, scenario_id)
            assert date(2026, 6, 5) in after, (
                "June's first paycheck is owed a row and did not get one"
            )
            assert len(after[date(2026, 6, 5)]) == 1


class TestTheWindowStillNarrowsWhatIsWritten:
    """The schedule widened; the write window must not have."""

    def test_a_windowed_pass_writes_only_into_its_window(
        self, app, db, seed_user, seed_periods,
    ):
        """An every-paycheck rule, generated into ONE period, writes one row.

        Resolving against the owner's whole schedule names all ten periods.
        Were the narrowing dropped, this pass would write ten rows -- and a
        real extend would re-walk every historical period on every run.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_periods(
                    seed_user["user"].id, [seed_periods[4]],
                ),
                scenario_id,
            )
            db.session.flush()

            assert len(created) == 1
            assert created[0].pay_period_id == seed_periods[4].id
            assert list(_rows(template, scenario_id)) == [
                seed_periods[4].start_date,
            ]


class TestTheStartPeriodBoundSurvivesTheWindow:
    """D2: the chosen "First paycheck" is part of the rule, not of the pass."""

    def test_a_windowed_pass_with_its_own_boundary_still_honours_the_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """The batch-boundary shape: an explicit ``effective_from`` + a window.

        **This is the only shape that reproduces D2**, and getting that wrong
        cost this file a false claim.  A window alone does not: the pre-R4b-1
        code defaulted ``effective_from`` to the rule's start period whenever
        the caller passed ``None``, which reproduced the bound by accident.  It
        only vanished for a caller that supplied its OWN boundary -- and
        ``period_population`` supplies exactly that, the new batch's opening
        payday, on every schedule extend.

        With the caller's boundary set, the old code took the explicit value,
        resolved the rule against the window, failed to find start period 5 in
        it, dropped the bound entirely, and generated into a paycheck the user
        had excluded.  Resolved against the owner's schedule the start period
        is found and the bound holds.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user,
                RecurrencePatternEnum.EVERY_PERIOD,
                start_date=seed_periods[5].start_date,
            )
            window = seed_periods[2:4]

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_periods(
                    seed_user["user"].id, window,
                ),
                scenario_id,
                effective_from=window[0].start_date,
            )
            db.session.flush()

            assert created == []
            assert _rows(template, scenario_id) == {}

    def test_the_extend_path_itself_honours_the_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """The same shape through the real repopulation entry point.

        ``populate_periods_from_active_templates`` is what an extend runs, and
        it defaults its boundary to the new batch's opening payday -- so this
        drives D2's actual production path rather than a reconstruction of it.
        A rule whose first paycheck is index 5 must gain nothing when periods
        2-3 are (re)populated.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user,
                RecurrencePatternEnum.EVERY_PERIOD,
                start_date=seed_periods[5].start_date,
            )

            period_population.populate_periods_from_active_templates(
                seed_user["user"].id, seed_periods[2:4],
            )
            db.session.flush()

            assert _rows(template, scenario_id) == {}
            assert scenario_id == seed_user["scenario"].id

    def test_the_same_rule_still_generates_from_its_stated_start_on(
        self, app, db, seed_user, seed_periods,
    ):
        """The bound is honoured, not merely restrictive.

        Without this the test above would pass against a rule that generates
        nothing at all.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user,
                RecurrencePatternEnum.EVERY_PERIOD,
                start_date=seed_periods[5].start_date,
            )

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_periods(
                    seed_user["user"].id, [seed_periods[5]],
                ),
                scenario_id,
            )
            db.session.flush()

            assert [row.pay_period_id for row in created] == [
                seed_periods[5].id,
            ]


class TestThePredictionIsTheGenerationCall:
    """``can_generate_in_period`` answered from a one-period schedule."""

    def test_the_predictor_agrees_with_generation_in_every_period(
        self, app, db, seed_user, seed_periods,
    ):
        """For a ``Monthly First`` rule the two answers must coincide.

        The predictor drives the carry-forward GENERATE branch.  Reading a
        one-period schedule it saw one month holding one payday and answered
        "the engine fires here" for EVERY period: measured on production, 32 of
        61 for the live ``Phone Allowance`` rule, where the true answer is each
        month's first paycheck only.

        ONE schedule serves all ten questions, which is also the shape the
        carry-forward context now threads: building one per call would repeat
        the schedule query and the forward walk once per envelope row.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, RecurrencePatternEnum.MONTHLY_FIRST,
            )
            schedule = GenerationSchedule.for_user(seed_user["user"].id)

            predicted = [
                period.start_date for period in seed_periods
                if recurrence_engine.can_generate_in_period(
                    template, period, scenario_id, schedule=schedule,
                )
            ]

            recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            generated = sorted(_rows(template, scenario_id))

            assert predicted == generated
            # Stated absolutely too: two equal-but-wrong answers would satisfy
            # the comparison above.  Each 2026 month in the fixture window is
            # owed exactly one row, on its FIRST payday.
            assert generated == [
                date(2026, 1, 2), date(2026, 2, 13), date(2026, 3, 13),
                date(2026, 4, 10), date(2026, 5, 8),
            ]


class TestThePaycheckSeesTheWholeSchedule:
    """D25: ``all_periods`` is the owner's schedule, not the pass's window."""

    def _salary_template(self, seed_user):
        """Create a salary profile whose deduction skips the 3rd paycheck.

        ``deductions_per_year=24`` is the cadence
        ``paycheck_calculator._deduction_applies_in_period`` skips on a month's
        third payday -- the exact judgement the truncated ``all_periods``
        got wrong on production.

        Args:
            seed_user: The ``seed_user`` fixture dict.

        Returns:
            ``(template, profile)``, both flushed.
        """
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_PERIOD,
            ),
            interval_n=1,
            offset_periods=0,
        )
        db.session.add(rule)
        db.session.flush()
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            name="Day Job",
            default_amount=Decimal("0.00"),
        )
        db.session.add(template)
        db.session.flush()

        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            template_id=template.id,
            filing_status_id=db.session.query(FilingStatus)
            .filter_by(name="single").one().id,
            name="Day Job",
            annual_salary=Decimal("104000.00"),
            state_code="NC",
            pay_periods_per_year=26,
        )
        db.session.add(profile)
        db.session.flush()

        db.session.add(PaycheckDeduction(
            salary_profile_id=profile.id,
            deduction_timing_id=db.session.query(DeductionTiming)
            .filter_by(name="pre_tax").one().id,
            calc_method_id=db.session.query(CalcMethod)
            .filter_by(name="flat").one().id,
            name="Health Insurance",
            amount=Decimal("500.00"),
            deductions_per_year=24,
        ))
        seed_tax_bracket_set(seed_user["user"].id)
        seed_state_tax_config(seed_user["user"].id, Decimal("0.0399"))
        seed_fica_config(seed_user["user"].id)
        db.session.flush()
        return template, profile

    def test_a_windowed_pass_computes_the_whole_schedule_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """Generating index 2 alone gives January's THIRD-paycheck net pay.

        2026-01-30 is January's third payday, so the 24x/yr deduction is
        SKIPPED and the net is higher.  Handed only ``[period 2]`` the
        calculator counted one January payday, called it the first, and took
        the $500 deduction: on production that shape understated one paycheck
        by $502.45.

        The expected figure is not a literal: it is what
        ``calculate_paycheck`` answers for this period against the owner's
        whole schedule, which is the contract being asserted.  The absolute
        claim is made separately, below.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template, profile = self._salary_template(seed_user)
            period = seed_periods[_JANUARY_THIRD_PAYCHECK_INDEX]

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_periods(
                    seed_user["user"].id, [period],
                ),
                scenario_id,
            )
            db.session.flush()

            # Pylint: ``import-outside-toplevel`` -- the calculator is
            # imported here only to recompute the contract this test asserts;
            # at module scope it would read as a collaborator of the tests.
            from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel
            # Pylint: ``import-outside-toplevel`` -- see above.
            from app.services.tax_config_service import (  # pylint: disable=import-outside-toplevel
                load_tax_configs_for_year,
            )
            configs = load_tax_configs_for_year(profile.user_id, profile, 2026)
            whole_break = paycheck_calculator.calculate_paycheck(
                profile, period, list(seed_periods), configs,
            )
            windowed_break = paycheck_calculator.calculate_paycheck(
                profile, period, [period], configs,
            )
            whole = whole_break.earnings.net_pay
            windowed = windowed_break.earnings.net_pay

            assert len(created) == 1
            assert created[0].estimated_amount == whole
            assert whole != windowed, (
                "the window and the whole schedule now agree for this period, "
                "so this test no longer guards anything -- check that "
                "seed_periods still puts three paydays in January"
            )
            # The direction is the money: the truncated read took a deduction
            # a third paycheck does not pay, so it UNDERSTATED the income.
            #
            # The DEDUCTION is asserted, not the net gap, and an adversarial
            # review is why.  The gap was a $480.05 literal with a stated
            # derivation of "$500 less the 3.99% state rate, because federal
            # does not move" -- and federal does not move here for the opposite
            # of the stated reason: the SHARED ``seed_tax_bracket_set`` fixture
            # has no open-ended top bracket, so both annualised bases saturate
            # it and the federal lines match by accident of the fixture.  The
            # first person to give that helper a realistic top bracket would
            # have broken a financial assertion and been sent to the wrong
            # cause.  The pre-tax total is the fact this defect is ABOUT, and
            # it is exact on any bracket table.
            assert (
                windowed_break.deductions.total_pre_tax
                - whole_break.deductions.total_pre_tax
            ) == Decimal("500.00"), (
                "the windowed read must have taken the 24x/yr deduction a "
                "third paycheck skips -- that IS the defect"
            )
            assert whole_break.deductions.total_pre_tax == Decimal("0.00")
            assert whole > windowed

    def test_the_deduction_is_skipped_on_the_third_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """Stated absolutely: the generated row differs from a 1st-payday one.

        The comparison above is relative -- two equal-but-wrong figures would
        satisfy it.  January's FIRST payday pays the $500 deduction and its
        THIRD does not, so the two generated rows must differ by exactly that,
        net of the tax the deduction shelters.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template, _profile = self._salary_template(seed_user)

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(seed_user["user"].id),
                scenario_id,
            )
            db.session.flush()

            by_start = {
                row.pay_period.start_date: row.estimated_amount
                for row in created
            }
            first = by_start[date(2026, 1, 2)]
            third = by_start[date(2026, 1, 30)]
            assert third > first, (
                "a third paycheck skips the 24x/yr deduction, so it must net "
                f"MORE than the month's first: {third} vs {first}"
            )
            # The deduction is PRE-TAX, so skipping it adds the $500 back and
            # then taxes it: the gap is smaller than $500 by exactly the tax
            # on $500, and strictly positive.
            assert Decimal("0.00") < third - first < Decimal("500.00")


class TestAWindowMustBelongToTheOwner:
    """The refusals that make a partial schedule unconstructible."""

    def test_another_users_period_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A window period outside the owner's schedule raises.

        Silent would be worse than loud: the intersection would match nothing
        and the pass would report "generated 0 rows" for a definition that
        fires every paycheck.
        """
        with app.app_context():
            foreign = (
                db.session.query(PayPeriod)
                .filter_by(user_id=seed_second_user["user"].id)
                .first()
            )
            assert foreign is not None, "the second user needs a period to lend"

            with pytest.raises(RecurrenceWindowError, match="not in this owner"):
                GenerationSchedule.for_periods(
                    seed_user["user"].id, [foreign],
                )

    def test_an_unsaved_period_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A window period with no id cannot be matched against the schedule.

        The repopulation paths flush before populating, so this names a caller
        that skipped that rather than a state the application reaches.
        """
        with app.app_context():
            unsaved = PayPeriod(
                user_id=seed_user["user"].id,
                start_date=date(2026, 6, 5),
                end_date=date(2026, 6, 18),
                period_index=99,
            )

            with pytest.raises(RecurrenceWindowError, match="has no id"):
                GenerationSchedule.for_periods(
                    seed_user["user"].id, [unsaved],
                )

    def test_a_stored_ordinal_out_of_payday_order_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The check plan step C2-b2 gave a new subject, exercised.

        ``periods`` is read ordered by the stored ``period_index`` and
        ``calendar`` by payday, so the two agree only while the stored ordinal
        agrees with payday order.  C2-b2 made that a real comparison rather
        than a tautology -- before it, the calendar was built FROM ``periods``
        and could not disagree -- and an adversarial review pointed out the
        raise had no test at all, which is the shape this class's own docstring
        warns about.

        The state is legacy or direct-DB only: ``pay_period_write`` writes
        ``period_index`` from the derivation, where it IS the position in
        payday order.  Reaching it silently would re-phase every
        ``Every N Periods`` rule for this owner, so it is refused.

        The swap parks one row on a spare ordinal and FLUSHES between each
        step: ``uq_pay_periods_user_index`` is checked per statement, so a
        direct exchange collides inside the one flush that would carry both
        ``UPDATE`` statements.
        """
        with app.app_context():
            rows = pay_period_service.get_all_periods(seed_user["user"].id)
            first, second = rows[0], rows[1]
            first_index, second_index = first.period_index, second.period_index
            spare = max(row.period_index for row in rows) + 1

            first.period_index = spare
            db.session.flush()
            second.period_index = first_index
            db.session.flush()
            first.period_index = second_index
            db.session.flush()

            # The premise: the stored ordinal now disagrees with payday order.
            reread = pay_period_service.get_all_periods(seed_user["user"].id)
            assert reread[0].id == second.id
            assert reread[0].start_date > reread[1].start_date

            with pytest.raises(
                RecurrenceWindowError, match="not the same periods",
            ):
                GenerationSchedule.for_user(seed_user["user"].id)

    def test_the_whole_schedule_is_loaded_not_taken_from_the_caller(
        self, app, db, seed_user, seed_periods,
    ):
        """``for_periods`` reads the owner's periods itself.

        This is what makes the D22 shape unconstructible rather than merely
        discouraged: a caller states the window and has no way to state the
        schedule, so the window can never stand in for it again.
        """
        with app.app_context():
            schedule = GenerationSchedule.for_periods(
                seed_user["user"].id, [seed_periods[9]],
            )

            assert len(schedule.write_periods) == 1
            assert [p.period_index for p in schedule.periods] == list(range(10))
            assert [
                p.period_index for p in schedule.calendar.periods
            ] == list(range(10))

    def test_the_read_only_window_cannot_be_mutated(
        self, app, db, seed_user, seed_periods,
    ):
        """``write_periods`` is a mapping proxy, so the value stays frozen."""
        with app.app_context():
            schedule = GenerationSchedule.for_user(seed_user["user"].id)

            with pytest.raises(TypeError):
                schedule.write_periods[999] = seed_periods[0]


class TestGenerationIsStillGated:
    """The gates R4b-1 moved but did not remove."""

    def test_a_cross_user_scenario_generates_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The ownership defence still fires before any resolution."""
        with app.app_context():
            template = _make_template(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(seed_user["user"].id),
                seed_second_user["scenario"].id,
            )

            assert created == []

    def test_a_populated_period_is_not_written_twice(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-running a pass over the same window creates nothing new."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            schedule = GenerationSchedule.for_user(seed_user["user"].id)

            first = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            second = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()

            assert len(first) == 10
            assert second == []

    def test_a_settled_row_is_left_alone(
        self, app, db, seed_user, seed_periods,
    ):
        """An immutable row blocks regeneration into its period."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(seed_user["user"].id),
                scenario_id,
            )
            db.session.flush()
            settled = created[0]
            settled.status_id = ref_cache.status_id(StatusEnum.DONE)
            settled.estimated_amount = Decimal("77.77")
            db.session.flush()

            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(seed_user["user"].id),
                scenario_id,
            )
            db.session.flush()

            rows = _rows(template, scenario_id)[seed_periods[0].start_date]
            assert len(rows) == 1
            assert rows[0].estimated_amount == Decimal("77.77")


class TestABoundedRuleDoesNotRestartItsCount:
    """``max_occurrences`` counts from the ANCHOR, so the anchor must be real.

    The axis an adversarial review named as the one this step's migration
    could not repair.  ``_occurrence._bounded`` stops after
    ``max_occurrences`` occurrences *from the anchor*, and the anchor is
    measured against whatever schedule the rule was resolved against -- so a
    narrower window moved the anchor forward and RESTARTED the count, emitting
    a fresh full allowance into every extend's new batch.  That is the same
    defect family as D22 and the migration's signature would not have found it.

    Moot on production and measured rather than assumed: 0 of the 46 live rules
    carry a ``max_occurrences`` (no form authors one until plan step R7b), so
    the migration is complete as shipped.  This is what keeps it moot.
    """

    def test_a_bounded_rule_is_exhausted_and_an_extend_adds_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """Three occurrences total, not three per window.

        The rule fires every paycheck and stops after 3, so it owns periods
        0-2 and nothing else.  Populating a LATER window must add no row: the
        allowance is spent.  Resolved against that window instead, the anchor
        becomes the window's own opening and the rule fires three more times.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            template.recurrence_rule.max_occurrences = 3
            db.session.flush()

            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(seed_user["user"].id),
                scenario_id,
            )
            db.session.flush()
            assert sorted(_rows(template, scenario_id)) == [
                seed_periods[0].start_date,
                seed_periods[1].start_date,
                seed_periods[2].start_date,
            ]

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_periods(
                    seed_user["user"].id, seed_periods[5:8],
                ),
                scenario_id,
            )
            db.session.flush()

            assert created == []
            assert len(_rows(template, scenario_id)) == 3


class TestTheTransferEngineTakesTheSameSchedule:
    """Half the changed surface, and the migration ships a DELETE for it."""

    def _transfer_template(self, seed_user, pattern_enum):
        """Create a recurring transfer template into a fresh savings account.

        Args:
            seed_user: The ``seed_user`` fixture dict.
            pattern_enum: The recurrence pattern to author.

        Returns:
            The flushed :class:`~app.models.transfer_template.TransferTemplate`.
        """
        # Pylint: ``import-outside-toplevel`` -- the account helper and the
        # transfer model are needed by this class alone; at module scope they
        # would load for every test in the file.
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        # Pylint: ``import-outside-toplevel`` -- see above.
        from tests._test_helpers import create_savings_account  # pylint: disable=import-outside-toplevel

        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=ref_cache.recurrence_pattern_id(pattern_enum),
            interval_n=1,
            offset_periods=0,
        )
        db.session.add(rule)
        db.session.flush()
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            recurrence_rule_id=rule.id,
            name="Savings Sweep",
            default_amount=Decimal("50.00"),
        )
        db.session.add(template)
        db.session.flush()
        return template

    def test_an_extend_does_not_duplicate_a_monthly_first_transfer(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer engine inherits the fix through the shared preamble.

        ``transfer_recurrence`` reaches the schedule through
        ``recurrence_engine.resolve_generation_plan``, so D22 is structurally
        shared -- but "shared by construction" is an argument, and the two
        engines are deliberate parallels that have drifted before.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the transfer engine and
            # its model are this class's collaborators only.
            from app.models.transfer import Transfer  # pylint: disable=import-outside-toplevel
            # Pylint: ``import-outside-toplevel`` -- see above.
            from app.services import transfer_recurrence  # pylint: disable=import-outside-toplevel

            scenario_id = seed_user["scenario"].id
            template = self._transfer_template(
                seed_user, RecurrencePatternEnum.MONTHLY_FIRST,
            )
            transfer_recurrence.generate_for_template(
                template,
                GenerationSchedule.for_user(seed_user["user"].id),
                scenario_id,
            )
            db.session.flush()

            def may_starts():
                """Return the May 2026 pay-period starts holding a transfer."""
                rows = (
                    db.session.query(Transfer)
                    .filter_by(
                        transfer_template_id=template.id,
                        scenario_id=scenario_id,
                    ).all()
                )
                return sorted(
                    row.pay_period.start_date for row in rows
                    if row.pay_period.start_date.month == 5
                )

            assert may_starts() == [date(2026, 5, 8)]

            appended = _append_period(seed_user, seed_periods)
            period_population.populate_periods_from_active_templates(
                seed_user["user"].id, appended,
            )
            db.session.flush()

            assert may_starts() == [date(2026, 5, 8)]


class TestRegenerateSweepsOnlyWhatItRewrites:
    """The delete bound and the write bound are ONE bound.

    Found by adversarial review of this step: ``regenerate_for_template``'s
    ``effective_from=None`` default used to take the SCHEDULE's opening payday
    while ``generate_for_template`` wrote only inside the window, so a narrow
    window deleted every row from the owner's first payday forward and
    recreated only a few.  No route reaches it -- both callers pass a
    whole-schedule window -- and nothing in the signature said it was illegal.
    """

    def test_a_narrow_window_does_not_sweep_the_whole_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """Regenerating into periods 5-9 leaves periods 0-4 alone."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(seed_user["user"].id),
                scenario_id,
            )
            db.session.flush()
            assert len(_rows(template, scenario_id)) == 10

            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_periods(
                    seed_user["user"].id, seed_periods[5:],
                ),
                scenario_id,
            )
            db.session.flush()

            # All ten survive: 0-4 were never in the sweep's window, and 5-9
            # were deleted and regenerated.
            assert sorted(_rows(template, scenario_id)) == [
                period.start_date for period in seed_periods
            ]


class TestOneScheduleReadServesTheWholeRequest:
    """The N+1 an adversarial review measured, and the gate against its return.

    ``can_generate_in_period`` runs once per envelope row being carried
    forward.  Building a ``GenerationSchedule`` inside it cost one
    ``get_all_periods`` query AND one full forward occurrence walk per row --
    measured at 12 queries for the preview and 24 for the execute over 12
    envelope rows, against a baseline of 0.  The schedule is now resolved once
    in ``_build_carry_forward_context`` and threaded.
    """

    def test_a_twelve_row_carry_forward_reads_the_schedule_once(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Twelve envelope rows, one schedule read on each path."""
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the carry-forward service
            # is this class's collaborator only.
            from app.services import carry_forward_service  # pylint: disable=import-outside-toplevel

            scenario_id = seed_user["scenario"].id
            for index in range(12):
                template = _make_template(
                    seed_user, RecurrencePatternEnum.EVERY_PERIOD,
                )
                template.name = f"Envelope {index}"
                template.is_envelope = True
                db.session.flush()
                recurrence_engine.generate_for_template(
                    template,
                    GenerationSchedule.for_user(seed_user["user"].id),
                    scenario_id,
                )
            db.session.commit()

            calls = {"n": 0}
            real = pay_period_service.get_all_periods

            def counting(user_id):
                """Count each schedule read, then delegate."""
                calls["n"] += 1
                return real(user_id)

            monkeypatch.setattr(
                pay_period_service, "get_all_periods", counting,
            )

            calls["n"] = 0
            carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, scenario_id,
            )
            assert calls["n"] == 1, (
                f"the preview read the schedule {calls['n']} times for 12 "
                f"envelope rows; it must resolve one and thread it"
            )

            calls["n"] = 0
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["user"].id, scenario_id,
            )
            assert calls["n"] == 1, (
                f"the execute path read the schedule {calls['n']} times for "
                f"12 envelope rows; it must resolve one and thread it"
            )
