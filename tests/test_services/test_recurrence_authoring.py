"""The recurrence write door and the writers routed through it (step R2c).

``tests/test_services/test_recurrence_resolution.py`` covers the pure
derivation.  This file covers what it means for the application to have ONE
write door: that every path which creates or changes a rule goes through it,
and that the two vocabularies ``budget.recurrence_rules`` carries cannot end up
describing different cadences.

The invariant under test throughout is one sentence: **a rule's two-axis
columns are always the resolution of its own closed-set columns.**
:class:`TestEveryRuleIsSelfConsistent` states it directly, over every rule each
writer produces; the classes above it pin the specific values each writer
intends, and the two defect classes pin edits that used to break it.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db as _db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import FilingStatus
from app.services import loan_recurrence_sync, pay_period_admin
from app.services.recurrence import (
    RecurrenceSpec,
    author_rule,
    calendar_for,
    reauthor_rule,
    recurrence_spec,
    resolve,
)
from tests._test_helpers import create_loan_account


def assert_self_consistent(rule: RecurrenceRule) -> None:
    """Assert a rule's two-axis columns re-derive from its own closed-set ones.

    The invariant the write door exists to hold.  Reading the rule's authored
    state back and re-resolving it must reproduce every stored value -- if it
    does not, some writer moved one vocabulary and left the other behind.

    Args:
        rule: The persisted rule to check.
    """
    expected = resolve(recurrence_spec(rule), calendar_for(rule.user_id))

    assert rule.interval_n == expected.interval_n
    assert rule.offset_periods == expected.offset_periods
    assert rule.unit_id == expected.unit_id
    assert rule.anchor_date == expected.anchor_date
    assert rule.placement_id == expected.placement_id
    assert rule.shift_id == expected.shift_id
    stored_nominal = (
        rule.month_anchor.nominal_day if rule.month_anchor else None
    )
    assert stored_nominal == expected.nominal_day


def assert_two_axis_complete(rule: RecurrenceRule) -> None:
    """Assert every column plan step R2c tightens to NOT NULL is populated.

    Args:
        rule: The persisted rule to check.
    """
    assert rule.unit_id is not None
    assert rule.anchor_date is not None
    assert rule.placement_id is not None
    assert rule.shift_id is not None


class TestSalaryProfileWriter:
    """``salary.create_profile`` -- one of the five production writers."""

    def test_a_created_profile_carries_a_resolved_every_period_rule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The salary template's rule resolves to every-paycheck from period 0.

        ``seed_periods`` opens the schedule on 2026-01-02, and the rule names
        no start period, so its first occurrence is that opening payday.
        """
        filing_status = db.session.query(FilingStatus).filter_by(
            name="single",
        ).one()

        resp = auth_client.post("/salary", data={
            "name": "Day Job",
            "annual_salary": "104000.00",
            "filing_status_id": str(filing_status.id),
            "state_code": "PA",
            "pay_periods_per_year": "26",
        })
        assert resp.status_code == 302

        rule = db.session.query(RecurrenceRule).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert_two_axis_complete(rule)
        assert_self_consistent(rule)
        assert rule.anchor_date == seed_periods[0].start_date
        assert rule.unit_id == ref_cache.recurrence_unit_id(
            RecurrenceUnitEnum.PERIOD,
        )
        assert rule.interval_n == 1


class TestLoanPaymentTransferWriter:
    """``loan.create_payment_transfer`` plus the loan-sync re-author."""

    @pytest.mark.usefixtures("seed_periods")
    def test_the_created_rule_anchors_on_the_loans_contractual_day(
        self, auth_client, seed_user, db,
    ):
        """The anchor is the first payment day at or after the first installment.

        The route creates the rule with ``day_of_month = payment_day`` and
        then ``bind_rule_to_loan`` stamps ``start_date`` = the loan's first
        contractual installment, re-authoring it.  With an origination of
        2023-06-01 and a payment day of 1, that installment is 2023-07-01 --
        which precedes the schedule opening (2026-01-02), so the effective
        bound is the opening and the first day-1 occurrence at or after it is
        2026-02-01.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Mortgage",
            principal=Decimal("250000.00"), rate=Decimal("0.06500"), term=360,
            origination_date=date(2023, 6, 1), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )

        resp = auth_client.post(
            f"/accounts/{loan.id}/loan/create-transfer",
            data={"source_account_id": str(seed_user["account"].id)},
        )
        assert resp.status_code == 302

        rule = db.session.query(RecurrenceRule).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert_two_axis_complete(rule)
        assert_self_consistent(rule)
        assert rule.day_of_month == 1
        assert rule.start_date == date(2023, 7, 1)
        assert rule.anchor_date == date(2026, 2, 1)
        assert rule.unit_id == ref_cache.recurrence_unit_id(
            RecurrenceUnitEnum.MONTH,
        )

    @pytest.mark.usefixtures("seed_periods")
    def test_a_payment_day_edit_moves_the_anchor_with_it(
        self, seed_user, db,
    ):
        """``_sync_loan_cadence`` re-derives the anchor, not just the day.

        Before the write door this wrote ``day_of_month`` and ``start_date``
        alone, leaving ``anchor_date`` on the day the servicer no longer bills
        -- and no query could tell that stale value from a fresh one.  Moving
        the payment day from the 1st to the 20th must move the first
        occurrence from 2026-02-01 to 2026-01-20.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Auto",
            principal=Decimal("20000.00"), rate=Decimal("0.04000"), term=48,
            origination_date=date(2023, 6, 1), payment_day=1,
            account_type=AcctTypeEnum.AUTO_LOAN,
        )
        rule = author_rule(
            RecurrenceSpec(
                user_id=seed_user["user"].id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.MONTHLY,
                ),
                day_of_month=1,
            ),
            calendar_for(seed_user["user"].id),
        )
        loan_recurrence_sync.bind_rule_to_loan(rule, loan.id)
        db.session.flush()
        assert rule.anchor_date == date(2026, 2, 1)

        params = loan.loan_params
        params.payment_day = 20
        loan_recurrence_sync.bind_rule_to_loan(rule, loan.id)
        db.session.flush()

        assert rule.day_of_month == 20
        assert rule.anchor_date == date(2026, 1, 20)
        assert_self_consistent(rule)


class TestScheduleRebuildRepoint:
    """``pay_period_admin._repoint_recurrence_rules`` after a full reset."""

    def test_a_rebuilt_schedule_re_anchors_every_rule_it_re_points(
        self, seed_user, db, seed_periods,
    ):
        """A reset moves the anchor onto the new schedule, not just the FK.

        The old path was a bulk ``query.update()``, which writes SQL beneath
        the ORM and so could not have touched ``anchor_date`` at all: a rule
        survived a reset carrying a first occurrence from a schedule that no
        longer existed.  Rebuilding from 2027-03-05 must move this rule's
        anchor there.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_PERIOD,
                ),
                start_period_id=seed_periods[0].id,
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert rule.anchor_date == date(2026, 1, 2)

        new_periods = pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, cadence_days=14,
        )
        db.session.flush()

        assert new_periods[0].start_date == date(2027, 3, 5)
        assert rule.start_period_id == new_periods[0].id
        assert rule.anchor_date == date(2027, 3, 5)
        assert_self_consistent(rule)

    def test_a_rule_with_NO_start_period_is_re_anchored_too(
        self, seed_user, db, seed_periods,
    ):
        """The anchor is not a function of the start period alone.

        Found by a neutral adversarial review, and the reason this pass
        re-authors EVERY rule rather than only the ones whose start period the
        wipe nulled.  ``resolve`` measures the anchor from the GREATEST of the
        start period, the rule's ``start_date``, and the SCHEDULE'S OPENING
        PAYDAY -- so a reset moves it for a rule that names no start period at
        all.  Re-authoring only the captured ids left such a rule holding
        ``2026-01-02``, a first occurrence from the schedule that had just
        been deleted.

        Three of the developer's 50 live rules are in exactly this shape, and
        plan step R2c-3's NOT NULL tightening would never have caught it: the
        value is not null, only wrong.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_PERIOD,
                ),
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert rule.start_period_id is None
        assert rule.anchor_date == seed_periods[0].start_date

        pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, cadence_days=14,
        )
        db.session.flush()

        assert rule.start_period_id is None
        assert rule.anchor_date == date(2027, 3, 5)
        assert_self_consistent(rule)

    def test_the_re_point_re_phases_an_every_n_rule(
        self, seed_user, db, seed_periods,
    ):
        """The phase is DERIVED from the new first period, not hardcoded to 0.

        The pre-seam bulk update wrote ``offset_periods = 0`` as a
        hand-maintained copy of ``first_period.period_index % interval_n``.
        Re-authoring computes it, and index 0 gives 0 for every interval -- so
        the value is the same and the copy is gone.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_N_PERIODS,
                ),
                interval_n=3,
                start_period_id=seed_periods[2].id,
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert rule.offset_periods == 2  # period_index 2 % interval 3

        pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, cadence_days=14,
        )
        db.session.flush()

        assert rule.offset_periods == 0  # new period_index 0 % interval 3
        assert rule.interval_n == 3
        assert_self_consistent(rule)


class TestMonthAnchorLifecycle:
    """The 0-or-1 subtype row moves with the day it describes."""

    @pytest.mark.usefixtures("seed_periods")
    def test_a_clamped_day_writes_the_subtype_row(
        self, seed_user, db,
    ):
        """A day-31 rule anchored in a 30-day month records its nominal day."""
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.MONTHLY,
                ),
                day_of_month=31,
                start_date=date(2026, 4, 1),
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        assert rule.anchor_date == date(2026, 4, 30)
        assert rule.month_anchor is not None
        assert rule.month_anchor.nominal_day == 31

    @pytest.mark.usefixtures("seed_periods")
    def test_changing_the_day_deletes_a_now_obsolete_subtype_row(
        self, seed_user, db,
    ):
        """Re-authoring to a day no month can clamp REMOVES the row.

        Presence is the discriminator, so a surviving row would restore the
        31st on the next read of a rule the user moved to the 15th.  An
        upsert-only backfill leaves exactly that residue; the write door
        deletes it.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.MONTHLY,
                ),
                day_of_month=31,
                start_date=date(2026, 4, 1),
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert rule.month_anchor is not None

        reauthor_rule(
            rule,
            replace(recurrence_spec(rule), day_of_month=15),
            calendar_for(user_id),
        )
        db.session.flush()

        assert rule.anchor_date == date(2026, 4, 15)
        assert rule.month_anchor is None
        assert_self_consistent(rule)


class TestPhasePreservedAcrossAnEdit:
    """Defect D1: an edit used to re-phase every future occurrence."""

    def test_re_authoring_keeps_the_phase_the_start_period_states(
        self, seed_user, db, seed_periods,
    ):
        """An edit that does not touch the schedule leaves the phase alone.

        The pre-seam update path wrote ``offset_periods`` from the payload,
        and no template renders an offset input -- so the value was always the
        schema default 0, and every future occurrence of an every-3-paychecks
        rule shifted by one pay period on an amount-only edit.  Deriving the
        phase from the rule's own start period on every write is what makes
        that unreachable.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_N_PERIODS,
                ),
                interval_n=3,
                start_period_id=seed_periods[2].id,
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert rule.offset_periods == 2

        # An edit carrying the payload's default phase, as the form submits.
        reauthor_rule(
            rule,
            replace(recurrence_spec(rule), offset_periods=0),
            calendar_for(user_id),
        )
        db.session.flush()

        assert rule.offset_periods == 2


class TestIntervalSurvivesAPatternChange:
    """The interval is DERIVED for every pattern that names one."""

    @pytest.mark.usefixtures("seed_periods")
    def test_a_quarterly_rule_keeps_its_three_month_cadence(
        self, seed_user, db,
    ):
        """The form's hidden interval input cannot reach a Quarterly rule.

        ``interval_n`` doubles as the two-axis interval since plan step R2b:
        3 on a Quarterly rule.  The form collects it only for
        ``Every N Periods`` -- but a hidden input still SUBMITS its default of
        1, and at plan step R4 ``(interval_n=1, unit=month)`` IS monthly:
        three times the projected spend with nothing in the row to detect the
        loss by.  Resolution derives the interval from the pattern, so the
        submitted 1 is discarded rather than guarded against.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.QUARTERLY,
                ),
                month_of_year=2, day_of_month=10,
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert rule.interval_n == 3

        reauthor_rule(
            rule,
            replace(recurrence_spec(rule), interval_n=1),
            calendar_for(user_id),
        )
        db.session.flush()

        assert rule.interval_n == 3

    def test_switching_from_every_n_to_quarterly_re_derives_the_interval(
        self, seed_user, db, seed_periods,
    ):
        """The reverse case the old pattern-scoped guard left open.

        That guard wrote the submitted interval only for ``Every N Periods``,
        so switching an every-4-PAYCHECKS rule to Quarterly kept the 4 -- a
        Quarterly rule reading "every 4 months" at plan step R4.  Deriving
        rather than guarding closes both directions with one rule.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_N_PERIODS,
                ),
                interval_n=4,
                start_period_id=seed_periods[0].id,
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert rule.interval_n == 4

        reauthor_rule(
            rule,
            replace(
                recurrence_spec(rule),
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.QUARTERLY,
                ),
                month_of_year=2, day_of_month=10,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        assert rule.interval_n == 3
        assert rule.unit_id == ref_cache.recurrence_unit_id(
            RecurrenceUnitEnum.MONTH,
        )
        assert rule.placement_id == ref_cache.period_placement_id(
            PeriodPlacementEnum.CONTAINING_DATE,
        )
        assert_self_consistent(rule)


class TestEveryRuleIsSelfConsistent:
    """The invariant, stated over every pattern the application can author."""

    @pytest.mark.parametrize("pattern", list(RecurrencePatternEnum))
    def test_an_authored_rule_re_derives_to_itself(
        self, seed_user, db, seed_periods, pattern,
    ):
        """Authoring then re-resolving reproduces every stored value.

        Parametrised over the whole enum rather than a sample: the property
        plan step R2c's NOT NULL tightening rests on is that NO pattern can
        produce an incomplete or inconsistent row.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(pattern),
                day_of_month=15, month_of_year=3,
                start_period_id=seed_periods[1].id,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        assert_two_axis_complete(rule)
        assert_self_consistent(rule)

    @pytest.mark.usefixtures("db", "seed_periods")
    def test_the_rule_is_flushed_with_an_id_the_caller_can_link(
        self, seed_user,
    ):
        """``author_rule`` flushes, because every caller links the rule next."""
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_PERIOD,
                ),
            ),
            calendar_for(user_id),
        )

        assert rule.id is not None
        assert _db.session.get(RecurrenceRule, rule.id) is rule
