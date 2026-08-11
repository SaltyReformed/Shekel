"""The recurrence write door and the writers routed through it (R2c, R2d).

``tests/test_services/test_recurrence_resolution.py`` covers the pure
derivation.  This file covers what it means for the application to have ONE
write door: that every path which creates or changes a rule goes through it,
and that what each writer INTENDED is what the rule resolves to afterwards.

Two invariants run through it.

* **Authoring is idempotent.**  Reading a rule's spec back and re-authoring it
  changes no column (:func:`assert_reauthoring_changes_nothing`).  That is
  what makes "a caller owning one fact replaces that fact and re-authors" a
  safe idiom rather than a rewrite with side effects.
* **A rule always resolves, completely.**  Every pattern the application can
  author produces a whole :class:`~app.services.recurrence.ResolvedRecurrence`
  (:func:`assert_resolves_completely`), which is the property plan step R7c's
  NOT NULL columns will rest on.

**What this file no longer asserts, and why.**  Before plan step R2d the
invariant was "a rule's two-axis COLUMNS are always the resolution of its own
closed-set columns" -- a consistency check between two stored halves.  Those
columns are gone: the two-axis view is computed on demand, so the halves
cannot disagree and there is nothing to check.  What replaced it is stronger
and simpler -- the value is derived at every read, so the tests assert the
DERIVATION (in the resolution suite) and the AUTHORING (here), with no
consistency relation in between.
"""

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db as _db
from app.models.recurrence_anchors import RecurrenceMonthAnchor
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import FilingStatus
from app.services import loan_recurrence_sync, pay_period_admin
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    RecurrenceSpec,
    ResolvedRecurrence,
    author_rule,
    reauthor_rule,
    recurrence_spec,
    resolve,
)
from tests._test_helpers import create_loan_account


#: Every column of ``budget.recurrence_rules`` a user authors.  Named once so
#: :func:`authored_columns` cannot silently stop covering one that is added,
#: and pinned against the table itself by
#: :class:`TestTheAuthoredSurfaceIsWholeAndClosed`.
_AUTHORED_COLUMNS = (
    "user_id", "pattern_id", "interval_n", "offset_periods", "day_of_month",
    "due_day_of_month", "month_of_year", "start_period_id", "start_date",
    "end_date", "max_occurrences",
)

#: The two columns the DATABASE assigns, which no caller may author: the
#: surrogate key and the insert timestamp (``CreatedAtMixin``, server default).
_DB_ASSIGNED_COLUMNS = frozenset({"id", "created_at"})


def authored_columns(rule: RecurrenceRule) -> dict:
    """Return every authored column of *rule* as a plain dict.

    Args:
        rule: The rule to read.

    Returns:
        ``{column_name: value}`` over :data:`_AUTHORED_COLUMNS`.
    """
    return {name: getattr(rule, name) for name in _AUTHORED_COLUMNS}


def resolved_for(rule: RecurrenceRule) -> ResolvedRecurrence:
    """Return the two-axis meaning of a persisted rule.

    The rule stores no part of this: it is recomputed from the rule's own
    authored columns and its owner's schedule, which is exactly how every
    reader in the application will obtain it.

    Args:
        rule: The persisted rule to resolve.

    Returns:
        Its :class:`~app.services.recurrence.ResolvedRecurrence`.
    """
    return resolve(recurrence_spec(rule), calendar_for(rule.user_id))


def assert_reauthoring_changes_nothing(rule: RecurrenceRule) -> None:
    """Assert re-authoring a rule from its own spec is a no-op.

    Idempotence is what makes the read-modify-re-author idiom safe: a caller
    that owns ONE fact reads the spec, replaces that fact, and writes the
    whole thing back, so every OTHER fact must survive the round trip
    untouched.  A writer that derived something differently on the way out
    than on the way in would move a column here.

    Args:
        rule: The persisted rule to round-trip.
    """
    before = authored_columns(rule)

    reauthor_rule(rule, recurrence_spec(rule), calendar_for(rule.user_id))

    assert authored_columns(rule) == before


def assert_resolves_completely(rule: RecurrenceRule) -> None:
    """Assert *rule* resolves to a whole two-axis value.

    The property plan step R7c's NOT NULL columns will rest on: no rule the
    application can author may resolve to a partial value, because at that
    step the partial value becomes an un-migratable row.

    Args:
        rule: The persisted rule to check.
    """
    resolved = resolved_for(rule)

    assert resolved.anchor_date is not None
    assert isinstance(resolved.unit, RecurrenceUnitEnum)
    assert isinstance(resolved.placement, PeriodPlacementEnum)
    assert isinstance(resolved.shift, BusinessDayShiftEnum)
    assert resolved.interval_n >= 1


class TestTheAuthoredSurfaceIsWholeAndClosed:
    """Every column of the table is either authored or assigned by the database.

    The door writes a whole ``RecurrenceSpec`` rather than a field at a time,
    which is only meaningful if the spec actually COVERS the table.  Add a
    column to ``budget.recurrence_rules`` and forget the spec, and the door
    silently stops being able to author it: the column takes its server
    default forever, no writer can set it, and no other test notices -- the
    round-trip and idempotence checks both pass, because a value nobody writes
    also never changes.

    So the relationship is asserted against the mapped table rather than
    against a second hand-maintained list.  This is also what will fail, on
    purpose, at plan step R7c, when ``unit_id`` / ``anchor_date`` /
    ``placement_id`` / ``shift_id`` return as AUTHORED columns and must join
    the spec.
    """

    def test_the_spec_covers_every_column_the_database_does_not_assign(self):
        """The spec's fields are exactly the table's non-DB-assigned columns."""
        table_columns = {
            column.key for column in RecurrenceRule.__table__.columns
        }
        spec_fields = {field.name for field in fields(RecurrenceSpec)}

        assert table_columns - _DB_ASSIGNED_COLUMNS == spec_fields, (
            "budget.recurrence_rules and RecurrenceSpec have diverged.  A "
            "column the spec does not carry cannot be authored through the "
            "write door at all -- it would keep its server default forever, "
            "and neither the round-trip nor the idempotence check would "
            "notice, because a value nobody writes also never changes."
        )

    def test_the_helper_covers_every_authored_column(self):
        """:data:`_AUTHORED_COLUMNS` is the spec's fields, not a subset.

        The comparison helpers in this file read only these names, so a name
        missing here would exempt that column from every assertion built on
        them.
        """
        assert set(_AUTHORED_COLUMNS) == {
            field.name for field in fields(RecurrenceSpec)
        }


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
        assert_resolves_completely(rule)
        assert_reauthoring_changes_nothing(rule)
        resolved = resolved_for(rule)
        assert resolved.anchor_date == seed_periods[0].start_date
        assert resolved.unit is RecurrenceUnitEnum.PERIOD
        assert resolved.interval_n == 1


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
        assert_resolves_completely(rule)
        assert_reauthoring_changes_nothing(rule)
        assert rule.day_of_month == 1
        assert rule.start_date == date(2023, 7, 1)
        resolved = resolved_for(rule)
        assert resolved.anchor_date == date(2026, 2, 1)
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    @pytest.mark.usefixtures("seed_periods")
    def test_a_payment_day_edit_moves_the_anchor_with_it(
        self, seed_user, db,
    ):
        """Moving the payment day moves the first occurrence with it.

        The anchor is computed from ``day_of_month``, so the two cannot
        disagree -- which is the point of plan step R2d.  Before the write
        door existed, ``_sync_loan_cadence`` wrote ``day_of_month`` and
        ``start_date`` alone; while the anchor was a stored column that left
        it on the day the servicer no longer bills, with no query able to tell
        the stale value from a fresh one.  Moving the payment day from the 1st
        to the 20th must move the first occurrence from 2026-02-01 to
        2026-01-20.
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
        assert resolved_for(rule).anchor_date == date(2026, 2, 1)

        params = loan.loan_params
        params.payment_day = 20
        loan_recurrence_sync.bind_rule_to_loan(rule, loan.id)
        db.session.flush()

        assert rule.day_of_month == 20
        assert resolved_for(rule).anchor_date == date(2026, 1, 20)
        assert_reauthoring_changes_nothing(rule)


class TestScheduleRebuildRepoint:
    """``pay_period_admin._repoint_recurrence_rules`` after a full reset."""

    def test_a_rebuilt_schedule_re_anchors_every_rule_it_re_points(
        self, seed_user, db, seed_periods,
    ):
        """A reset re-points the FK, and the anchor follows.

        The old path was a bulk ``query.update()`` writing ``start_period_id``
        and a hardcoded ``offset_periods = 0``.  Going through the seam
        derives the phase instead of transcribing it.  Rebuilding from
        2027-03-05 must both re-point the rule and move its first occurrence
        there.
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
        assert resolved_for(rule).anchor_date == date(2026, 1, 2)

        new_periods = pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, cadence_days=14,
        )
        db.session.flush()

        assert new_periods[0].start_date == date(2027, 3, 5)
        assert rule.start_period_id == new_periods[0].id
        assert resolved_for(rule).anchor_date == date(2027, 3, 5)
        assert_reauthoring_changes_nothing(rule)

    def test_a_rule_with_NO_start_period_follows_the_reset_without_a_write(
        self, seed_user, db, seed_periods,
    ):
        """Its anchor moves because it is COMPUTED, not because it is rewritten.

        This is the case that made plan step R2d worth doing, and it inverts
        an earlier test.  ``resolve`` measures the anchor from the GREATEST of
        the start period, the rule's ``start_date``, and the SCHEDULE'S
        OPENING PAYDAY -- so a reset moves it for a rule naming no start
        period at all.  While the anchor was a stored column that had to be
        re-WRITTEN, and a neutral review found that the re-point pass did not:
        three of the developer's 50 live rules kept a first occurrence from
        the schedule that had just been deleted, a value NOT NULL could never
        have caught because it was not null, only wrong.

        With the anchor computed there is nothing to strand.  The rule is not
        touched at all -- every authored column is byte-identical afterwards
        -- and it still resolves onto the new schedule.
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
        assert resolved_for(rule).anchor_date == seed_periods[0].start_date
        before = authored_columns(rule)

        pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, cadence_days=14,
        )
        db.session.flush()

        # Every authored column survives the reset unchanged.  This does not
        # by itself prove the rule was never re-authored -- re-authoring it
        # would also be a value no-op -- and it does not need to: what matters
        # is that no stored value went stale, which is the failure the old
        # stored anchor had.
        assert authored_columns(rule) == before
        # And it still answers for the schedule that exists now.
        assert resolved_for(rule).anchor_date == date(2027, 3, 5)

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
        assert_reauthoring_changes_nothing(rule)


class TestTheClampIsResolvedNeverStored:
    """The month-end clamp is carried by the resolved value, not a row.

    ``budget.recurrence_month_anchors`` exists to hold the day an
    ``anchor_date`` COLUMN clamped -- and there is no such column until plan
    step R7c, so the table must stay empty.  These are the regression guard
    for re-introducing subtype writing ahead of the column it describes: an
    anchor row written now would describe a value nothing stores, and nothing
    would read it.
    """

    @pytest.mark.usefixtures("seed_periods")
    def test_a_clamped_day_resolves_its_nominal_day_and_writes_no_row(
        self, seed_user, db,
    ):
        """A day-31 rule anchored in a 30-day month: April has no 31st.

        The rule's own ``day_of_month`` still holds 31, and the resolved
        anchor clamps to 2026-04-30 while ``nominal_day`` carries the 31 the
        user meant -- so nothing is lost and nothing is written.
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

        resolved = resolved_for(rule)
        assert resolved.anchor_date == date(2026, 4, 30)
        assert resolved.nominal_day == 31
        assert rule.day_of_month == 31
        assert rule.month_anchor is None
        assert db.session.query(RecurrenceMonthAnchor).count() == 0

    @pytest.mark.usefixtures("seed_periods")
    def test_changing_the_day_changes_the_resolved_clamp(
        self, seed_user, db,
    ):
        """Moving the rule to the 15th drops the clamp, with no row to clean up.

        Presence was the discriminator when the row existed, and a surviving
        row would have restored the 31st on the next read of a rule the user
        moved to the 15th -- the residue an upsert-only backfill leaves.
        Recomputing removes the failure mode rather than the residue.
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
        assert resolved_for(rule).nominal_day == 31

        reauthor_rule(
            rule,
            replace(recurrence_spec(rule), day_of_month=15),
            calendar_for(user_id),
        )
        db.session.flush()

        resolved = resolved_for(rule)
        assert resolved.anchor_date == date(2026, 4, 15)
        assert resolved.nominal_day is None
        assert db.session.query(RecurrenceMonthAnchor).count() == 0
        assert_reauthoring_changes_nothing(rule)


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


class TestTheResolvedIntervalComesFromThePattern:
    """A calendar pattern names its own interval; the column cannot override it.

    ``budget.recurrence_rules.interval_n`` keeps its single original meaning
    -- "repeat every N pay periods", read only in the occurrence engine's
    ``EVERY_N_PERIODS`` branch -- and the two-axis interval is derived from
    the pattern.  That separation is what makes the form's hidden input
    harmless.
    """

    @pytest.mark.usefixtures("seed_periods")
    def test_a_quarterly_rule_resolves_to_three_months_whatever_was_submitted(
        self, seed_user, db,
    ):
        """The form's hidden interval input cannot reach a Quarterly cadence.

        The form collects an interval only for ``Every N Periods``, but a
        hidden input still SUBMITS its default of 1 for every pattern.  If
        that 1 were the two-axis interval, ``(interval_n=1, unit=month)`` IS
        monthly -- three times the projected spend for a quarterly bill.
        Resolution reads the interval off the PATTERN, so the submitted value
        is structurally unable to say anything about a Quarterly rule.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            RecurrenceSpec(
                user_id=user_id,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.QUARTERLY,
                ),
                month_of_year=2, day_of_month=10, interval_n=1,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        assert resolved_for(rule).interval_n == 3
        assert resolved_for(rule).unit is RecurrenceUnitEnum.MONTH

        reauthor_rule(
            rule,
            replace(recurrence_spec(rule), interval_n=7),
            calendar_for(user_id),
        )
        db.session.flush()

        assert resolved_for(rule).interval_n == 3
        # And the COLUMN keeps the authored value, which is the half a
        # regression would hide: if ``_author`` went back to writing the
        # RESOLVED interval, the column would read 3, the row would carry two
        # meanings again -- and every other assertion in this file would stay
        # green, because they all read ``resolved_for``.
        assert rule.interval_n == 7

    def test_switching_from_every_n_to_quarterly_re_derives_the_interval(
        self, seed_user, db, seed_periods,
    ):
        """The reverse case the old pattern-scoped guard left open.

        A rule authored as every-4-PAYCHECKS carries ``interval_n = 4``.
        Switching it to Quarterly leaves that 4 in the column -- it is the
        authored pay-period interval, and nothing reads it for a calendar
        pattern -- while the recurrence resolves to every 3 MONTHS.  The two
        readings cannot be confused because only one of them is derived from
        the pattern.
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
        assert resolved_for(rule).interval_n == 4
        assert resolved_for(rule).unit is RecurrenceUnitEnum.PERIOD

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

        resolved = resolved_for(rule)
        assert resolved.interval_n == 3
        assert resolved.unit is RecurrenceUnitEnum.MONTH
        assert resolved.placement is PeriodPlacementEnum.CONTAINING_DATE
        assert_reauthoring_changes_nothing(rule)


class TestEveryPatternAuthorsAndResolves:
    """Both invariants, stated over every pattern the application can author."""

    @pytest.mark.parametrize("pattern", list(RecurrencePatternEnum))
    def test_an_authored_rule_resolves_and_round_trips(
        self, seed_user, db, seed_periods, pattern,
    ):
        """Every pattern resolves completely, and re-authoring is a no-op.

        Parametrised over the whole enum rather than a sample.  Completeness
        is the property plan step R7c's NOT NULL columns will rest on -- a
        pattern that resolved partially becomes an un-migratable row there --
        and idempotence is what makes the read-modify-re-author idiom safe for
        every writer in the application.
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

        assert_resolves_completely(rule)
        assert_reauthoring_changes_nothing(rule)

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
