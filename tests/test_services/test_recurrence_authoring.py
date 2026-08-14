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

import ast
import inspect
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
    decode_pattern,
    reauthor_rule,
    recurrence_spec,
    resolve,
    rule_occurrences,
)
# Imported as a MODULE, and from its DEFINITION site: the census below reads
# ``_author``'s own source, so naming the package re-export would let the
# assertion pass while looking at a different function than the one under test.
from app.services.recurrence import _authoring
from tests._test_helpers import create_loan_account


#: Every column of ``budget.recurrence_rules`` a user authors.  Named once so
#: :func:`authored_columns` cannot silently stop covering one that is added,
#: and pinned against the table itself by
#: :class:`TestTheAuthoredSurfaceIsWholeAndClosed`.
_AUTHORED_COLUMNS = (
    "user_id", "pattern_id", "interval_n", "offset_periods", "day_of_month",
    "due_day_of_month", "month_of_year", "start_date",
    "end_date", "max_occurrences",
)

#: The two columns the DATABASE assigns, which no caller may author: the
#: surrogate key and the insert timestamp (``CreatedAtMixin``, server default).
_DB_ASSIGNED_COLUMNS = frozenset({"id", "created_at"})

#: RETIRED columns: on the table, written by nobody, read by nobody, awaiting
#: the migration that drops them.
#:
#: A third category rather than a loosened assertion, and the distinction is
#: the point.  The census below is the gate that catches a column the write
#: door FORGOT -- one that would keep its server default forever while every
#: behavioural test passed, because a value nobody writes also never changes.
#: ``start_period_id`` is not forgotten: plan step R7b-4 folded it into
#: ``start_date`` (a MAXIMUM, measured equal on all 46 live rules), NULLed it
#: in that migration, and deleted its last reader.  Dropping the column
#: belongs with the four others plan step R7c drops in one transaction.
#:
#: Naming it here is what keeps the gate STRICT for everything else: a column
#: added and forgotten still fails, because it will not be on this list.  The
#: list must EMPTY at R7c, and a name left on it after its column is dropped
#: fails :meth:`TestTheAuthoredSurfaceIsWholeAndClosed.test_every_retired_column_still_exists`.
_RETIRED_COLUMNS = frozenset({"start_period_id"})


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


def spec_for(pattern: RecurrencePatternEnum, **overrides) -> RecurrenceSpec:
    """Return a spec for the cadence *pattern* names, with *overrides* applied.

    Keyed on the closed-set member for the reason the twin helper in
    ``test_recurrence_resolution.py`` is: every case here was written against a
    named pattern and re-keying them by hand would be a silent opportunity to
    change what each one measures.  The translation goes through
    :func:`~app.services.recurrence.decode_pattern`, the same seam the read door
    uses.

    ``interval_n`` is applied AFTER the decode, so a case may state a cadence
    the pattern's own name does not -- which is the whole point of the two-axis
    vocabulary.

    Args:
        pattern: The pattern member whose cadence to build.
        **overrides: Any :class:`~app.services.recurrence.RecurrenceSpec`
            field to set.  ``user_id`` is required, as it is on the spec.

    Returns:
        The spec.
    """
    # Decoded at TWO for the reason the twin helper in
    # ``test_recurrence_resolution.py`` records: at one, ``Every N Periods``
    # reads identically to ``Every Period`` and drops out of every whole-enum
    # sweep built on this helper.
    interval_override = overrides.pop("interval_n", None)
    reading = decode_pattern(ref_cache.recurrence_pattern_id(pattern), 2)
    return RecurrenceSpec(
        unit=reading.cadence.unit,
        interval_n=(
            reading.cadence.interval_n if interval_override is None
            else interval_override
        ),
        placement=reading.placement,
        **overrides,
    )


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


def _columns_assigned_by_the_write_door() -> set[str]:
    """Return every ``rule.<column> = ...`` target inside ``_author``.

    Read from the SOURCE rather than inferred from behaviour, because the
    property is about what the function can write at all, and a column it never
    assigns is invisible to every behavioural check -- a value nobody writes
    also never changes.

    Returns:
        The assigned attribute names.
    """
    source = inspect.getsource(_authoring)
    tree = ast.parse(source)
    door = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_author"
    )
    return {
        target.attr
        for node in ast.walk(door)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "rule"
    }


class TestTheAuthoredSurfaceIsWholeAndClosed:
    """Every column of the table is either authored or assigned by the database.

    The door writes a whole ``RecurrenceSpec`` rather than a field at a time,
    which is only meaningful if the door actually COVERS the table.  Add a
    column to ``budget.recurrence_rules`` and forget the door, and it silently
    stops being able to author it: the column takes its server default forever,
    no writer can set it, and no other test notices -- the round-trip and
    idempotence checks both pass, because a value nobody writes also never
    changes.

    **Asserted against what ``_author`` ASSIGNS, not against the spec's field
    names**, and plan step R7b is why.  The two were the same set while every
    spec field was a column; they are not any more -- a caller authors ``unit``
    and ``placement`` and the door encodes both into ``pattern_id`` -- and a
    shape comparison between two vocabularies can only be kept true by a
    hand-maintained exception list, which is the thing this class exists
    instead of.  The assignment census is also STRONGER: it fails on a column
    the spec carries and the door forgets, which the old comparison could not
    see.

    This is what will fail, on purpose, at plan step R7c, when ``unit_id`` /
    ``anchor_date`` / ``placement_id`` / ``shift_id`` arrive as columns.
    """

    def test_the_write_door_assigns_every_column_the_database_does_not(self):
        """``_author`` writes every column but the DB-assigned and RETIRED ones."""
        table_columns = {
            column.key for column in RecurrenceRule.__table__.columns
        }

        assert _columns_assigned_by_the_write_door() == (
            table_columns - _DB_ASSIGNED_COLUMNS - _RETIRED_COLUMNS
        ), (
            "budget.recurrence_rules and the write door have diverged.  A "
            "column ``_author`` does not assign cannot be authored at all -- "
            "it would keep its server default forever, and neither the "
            "round-trip nor the idempotence check would notice, because a "
            "value nobody writes also never changes."
        )

    def test_the_helper_covers_every_authored_column(self):
        """:data:`_AUTHORED_COLUMNS` is every authored column, not a subset.

        The comparison helpers in this file read only these names, so a name
        missing here would exempt that column from every assertion built on
        them.  Keyed on the TABLE because that is what they ``getattr`` off.
        """
        table_columns = {
            column.key for column in RecurrenceRule.__table__.columns
        }

        assert set(_AUTHORED_COLUMNS) == (
            table_columns - _DB_ASSIGNED_COLUMNS - _RETIRED_COLUMNS
        )

    def test_every_retired_column_still_exists(self):
        """:data:`_RETIRED_COLUMNS` names columns, not memories.

        The exemption list above is what keeps the write-door census passing
        for a column nothing writes, so a stale name on it would silently
        exempt nothing while reading as though it did -- and once plan step
        R7c drops these columns, this is what says the list must empty with
        them.
        """
        table_columns = {
            column.key for column in RecurrenceRule.__table__.columns
        }

        assert _RETIRED_COLUMNS <= table_columns, (
            f"_RETIRED_COLUMNS names "
            f"{_RETIRED_COLUMNS - table_columns}, which "
            f"budget.recurrence_rules does not carry.  A dropped column needs "
            f"no exemption; remove the name with the migration that drops it."
        )

    def test_every_spec_field_reaches_the_row(self):
        """No field of the spec is silently dropped on the way to the table.

        The census above says the door fills every column; this says the door
        READS every field.  Together they close the two directions a write door
        can be incomplete in.

        **The exception list is EMPTY since plan step R7b-4**, and that is the
        strongest this assertion has ever been.  ``offset_periods`` sat on it
        -- a spec field ``_author`` deliberately did not read, taking the
        resolver's answer instead because the value is DERIVED on every write
        (defect **D1**'s fix).  A field a caller can state and the door
        ignores is a field that lies about what it does, so R7b-4 removed it
        from the spec rather than from this list: nobody authors a phase now.
        """
        source = inspect.getsource(_authoring)
        read_from_spec = {
            node.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "spec"
        }
        spec_fields = {field.name for field in fields(RecurrenceSpec)}

        assert spec_fields - read_from_spec == set(), (
            "a RecurrenceSpec field the write door never reads is one a "
            "caller can state and the table will not carry."
        )


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
            spec_for(
                RecurrencePatternEnum.MONTHLY,
                user_id=seed_user["user"].id,
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
    """What a full schedule RESET does to a recurrence rule: nothing.

    ``pay_period_admin`` used to capture every rule carrying a start period
    before the wipe and re-point it at the new schedule's first period
    afterwards, because the pay-period delete cascade SET-NULLed that FK --
    which made a rule the cascade nulled indistinguishable from one that
    legitimately had no explicit start.  Plan step R7b-4 deleted both halves:
    a rule's opening bound is a DATE, which no cascade can reach, and
    ``resolve`` measures it against whatever schedule the owner has now.

    So the class name outlives the function it named, and these cases pin what
    replaced it.
    """

    def test_a_reset_leaves_a_bounded_rule_untouched(
        self, seed_user, db, seed_periods,
    ):
        """The rule is not written at all, and its anchor still follows.

        Rebuilding from 2027-03-05 leaves every authored column byte-identical
        -- there is no FK to re-point -- while the anchor moves, because it is
        ``max(new opening payday, start_date)`` and the new opening dominates
        a bound from the deleted schedule.  Same first occurrence the re-point
        produced, from a rule nothing rewrote.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD,
                user_id=user_id,
                start_date=seed_periods[0].start_date,
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert resolved_for(rule).anchor_date == date(2026, 1, 2)
        before = authored_columns(rule)

        new_periods = pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, cadence_days=14,
        )
        db.session.flush()

        assert new_periods[0].start_date == date(2027, 3, 5)
        assert authored_columns(rule) == before
        assert resolved_for(rule).anchor_date == date(2027, 3, 5)
        assert_reauthoring_changes_nothing(rule)

    def test_a_reset_keeps_a_bound_that_falls_INSIDE_the_new_schedule(
        self, seed_user, db, seed_periods,
    ):
        """The half the re-point got wrong, and the reason it is gone.

        The re-point moved EVERY captured rule to the new first period,
        whatever date the user had chosen.  Rebuild a schedule to open on
        2026-01-02 while a rule states it starts 2026-03-27, and the re-point
        answered "starts 2026-01-02" -- silently discarding the user's stated
        start because the FK it had to restore could only name the first
        period.  A date has nothing to restore: the bound survives, and
        ``max(2026-01-02, 2026-03-27)`` keeps it.

        Index 5 of a 14-day schedule opening 2026-01-02 is 2026-03-13, so a
        bound of 2026-03-27 lands inside index 6 and the rule opens there
        rather than at the schedule's own start.
        """
        user_id = seed_user["user"].id
        stated_start = date(2026, 3, 27)
        rule = author_rule(
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD,
                user_id=user_id,
                start_date=stated_start,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        pay_period_admin.reset_pay_periods(
            user_id, date(2026, 1, 2), num_periods=10, cadence_days=14,
        )
        db.session.flush()

        assert rule.start_date == stated_start
        assert resolved_for(rule).anchor_date == stated_start

    def test_a_reset_re_phases_an_every_n_rule_through_the_new_schedule(
        self, seed_user, db, seed_periods,
    ):
        """The phase follows the bound onto the rebuilt schedule.

        The pre-seam bulk update wrote ``offset_periods = 0`` as a
        hand-maintained copy of ``first_period.period_index % interval_n``.
        Nothing transcribes it now and nothing re-points either: the rule
        keeps its stated bound, the new schedule's opening dominates it, and
        the phase is the ordinal of the paycheck that maximum falls in --
        index 0, so 0 for every interval.  Same value the copy asserted, with
        no writer left to get it wrong.

        The COLUMN is stale until something re-authors the rule, and that is
        correct rather than overlooked: nothing reads it (plan step R7b-4), so
        the resolved answer below is what every consumer sees.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS,
                user_id=user_id,
                interval_n=3,
                start_date=seed_periods[2].start_date,
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert resolved_for(rule).offset_periods == 2  # index 2 % interval 3

        pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, cadence_days=14,
        )
        db.session.flush()

        resolved = resolved_for(rule)
        assert resolved.offset_periods == 0  # new index 0 % interval 3
        assert resolved.interval_n == 3

        # The COLUMN still holds the OLD schedule's phase, and re-authoring is
        # what brings it into line.  Asserted rather than glossed, because it
        # is the one place a reader could suspect the reset left something
        # wrong: the column is a stale DERIVATIVE, not a stale fact.  Nothing
        # reads it (plan step R7b-4), so the resolved answer above is what
        # every consumer sees, and plan step R7c drops it.
        assert rule.offset_periods == 2
        reauthor_rule(rule, recurrence_spec(rule), calendar_for(user_id))
        db.session.flush()
        assert rule.offset_periods == 0

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
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD,
                user_id=user_id,
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert rule.start_date is None
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
            spec_for(
                RecurrencePatternEnum.MONTHLY,
                user_id=user_id,
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
            spec_for(
                RecurrencePatternEnum.MONTHLY,
                user_id=user_id,
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
        rule shifted by one pay period on an amount-only edit.

        **The defect became UNCONSTRUCTIBLE at plan step R7b-4**, and this
        case is what says so.  Plan step R2d made the phase derive from the
        rule's start period on every write, which fixed the behaviour while
        leaving a phase field on the spec that a caller could still state and
        the door still ignored.  R7b-4 deleted the field: an edit re-reads the
        rule's authored state, replaces the one fact it owns, and re-authors,
        so there is no longer any value a payload can carry that names a
        phase at all.  The edit below is the very one that used to re-phase
        the rule -- everything but the amount left alone -- and the phase is
        still the paycheck the bound falls in.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS,
                user_id=user_id,
                interval_n=3,
                start_date=seed_periods[2].start_date,
            ),
            calendar_for(user_id),
        )
        db.session.flush()
        assert rule.offset_periods == 2  # index 2 % interval 3

        # An amount-only edit: read the rule whole, change nothing the phase
        # depends on, write it whole.
        reauthor_rule(
            rule, recurrence_spec(rule), calendar_for(user_id),
        )
        db.session.flush()

        assert rule.offset_periods == 2


class TestWhatTheEncodingNORMALISES:
    """The two columns a re-author moves, stated rather than discovered.

    Plan step R7b-1 claimed "no behaviour change" and an adversarial review
    measured two exceptions.  Neither moves an occurrence, a pay period or an
    amount -- the cadence is identical both times -- but both change a STORED
    column, and a claim that broad has to be either true or narrowed.  Narrowed:
    **no occurrence, period or amount moves.**

    Neither is visible in the two pieces of evidence the step leaned on, and
    that is worth saying: the 430-shape baseline sets columns directly and never
    calls the write door at all, and the production clone holds no rule of
    either shape.  A control has to be written for a state the corpus does not
    contain.
    """

    @pytest.mark.usefixtures("seed_periods")
    def test_every_one_paycheck_is_stored_under_its_own_name(
        self, seed_user, db,
    ):
        """"Every 1 paycheck" and "every paycheck" are ONE cadence.

        Reachable from the form -- the interval input's floor is 1 on both the
        schema (``Range(min=1)``) and the markup -- so a user who picks "Every
        N paychecks" with N = 1 now gets a row stored as ``Every Period``, and
        the edit page afterwards reads "Every paycheck" with the interval
        control hidden.

        The canonicalisation is deliberate: two names for one reading make plan
        step R7c's downgrade ambiguous, so the encoder picks the named one.
        Pinned HERE as well as at the encoder because this is the shape a user
        can actually reach, and because nothing else in the suite authors an
        every-N rule at interval 1.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS,
                user_id=user_id, interval_n=1,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        assert rule.pattern_id == ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.EVERY_PERIOD,
        )
        assert resolved_for(rule).interval_n == 1
        assert resolved_for(rule).unit is RecurrenceUnitEnum.PERIOD

    def test_a_stale_phase_on_a_every_paycheck_rule_is_normalised_to_zero(
        self, seed_user, db, seed_periods,
    ):
        """A phase means nothing at interval 1, and a re-author says so.

        ``ck_recurrence_rules_valid_offset`` is only ``offset_periods >= 0``,
        so ``(interval_n = 1, offset_periods = 2)`` is a DB-legal row -- and the
        old derivation, keyed on the pattern rather than the cadence, carried
        that 2 back out untouched.  The new one returns 0, because every
        paycheck satisfies ``(index - offset) % 1``.

        Inert by construction, and asserted as such: the occurrences either
        side of the re-author are identical.  Without that half this case would
        pin a column and say nothing about whether money moved.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(RecurrencePatternEnum.EVERY_PERIOD, user_id=user_id),
            calendar_for(user_id),
        )
        db.session.flush()
        rule.offset_periods = 2
        db.session.flush()
        before = [
            placement.occurrence
            for placement in rule_occurrences(rule, calendar_for(user_id))
        ]

        reauthor_rule(rule, recurrence_spec(rule), calendar_for(user_id))
        db.session.flush()

        assert rule.offset_periods == 0
        assert [
            placement.occurrence
            for placement in rule_occurrences(rule, calendar_for(user_id))
        ] == before


class TestTheIntervalSurvivesTheStorageEncoding:
    """A cadence's interval means the same thing after a round trip through the
    closed-set columns.

    **This class changed subject at plan step R7b and the old subject is now
    impossible.**  It used to assert that a calendar PATTERN names its own
    interval so the form's hidden input could not reach it.  A caller now
    states the interval directly, so there is no submitted value to be immune
    to -- and the protection did not disappear, it MOVED: the route decodes the
    posted pattern id through
    :func:`~app.services.recurrence.decode_pattern`, which ignores the posted
    interval for every pattern that names its own.  That is the first case
    below.

    What replaces it is the property the whole seam rests on: an authored
    cadence written through the door and read back through
    ``recurrence_spec`` is the SAME cadence, even though the column it passed
    through cannot hold it.
    """

    @pytest.mark.parametrize("submitted", [1, 3, 7, 99])
    def test_a_posted_interval_cannot_change_a_named_cadence(
        self, app, submitted,
    ):
        """Decoding a Quarterly id answers every 3 months, whatever is posted.

        The form collects an interval only for the paycheck cadence, but a
        hidden input still SUBMITS its default of 1 for every pattern.  If
        that 1 became the two-axis interval, ``(1, MONTH)`` IS monthly --
        three times the projected spend for a quarterly bill.  The decoder
        reads the interval off the PATTERN, so no posted value can say
        anything about a Quarterly rule.
        """
        with app.app_context():
            reading = decode_pattern(
                ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.QUARTERLY,
                ),
                submitted,
            )

        assert reading.cadence.interval_n == 3
        assert reading.cadence.unit is RecurrenceUnitEnum.MONTH

    @pytest.mark.usefixtures("seed_periods")
    def test_a_quarterly_cadence_round_trips_through_a_column_that_holds_1(
        self, seed_user, db,
    ):
        """Every 3 months stores ``interval_n = 1`` and reads back as 3.

        The column is spelled "repeat every N pay PERIODS" and cannot hold a
        month count, so the encoder puts the interval in the pattern's NAME and
        writes 1.  Both halves are asserted: the stored column, because a
        regression that wrote 3 there would give the row two meanings while
        every ``resolved_for`` assertion in this file stayed green; and the
        decoded cadence, because that is what every reader consumes.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                RecurrencePatternEnum.QUARTERLY,
                user_id=user_id,
                month_of_year=2, day_of_month=10,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        assert rule.interval_n == 1
        assert recurrence_spec(rule).interval_n == 3
        assert resolved_for(rule).interval_n == 3
        assert resolved_for(rule).unit is RecurrenceUnitEnum.MONTH

    def test_a_paycheck_cadence_keeps_its_interval_in_the_column(
        self, seed_user, db, seed_periods,
    ):
        """Every 4 paychecks is the one cadence the column itself carries."""
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS,
                user_id=user_id,
                interval_n=4,
                start_date=seed_periods[0].start_date,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        assert rule.interval_n == 4
        assert resolved_for(rule).interval_n == 4
        assert resolved_for(rule).unit is RecurrenceUnitEnum.PERIOD

    def test_switching_from_paychecks_to_quarterly_restates_the_cadence(
        self, seed_user, db, seed_periods,
    ):
        """An edit that changes the UNIT changes the interval with it.

        The reverse case the old pattern-scoped guard left open, and under the
        two-axis vocabulary it is no longer a guard at all: a caller states
        ``(3, MONTH)`` and the row cannot come to mean "every 4 months",
        because the interval it stated is what it gets back.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                RecurrencePatternEnum.EVERY_N_PERIODS,
                user_id=user_id,
                interval_n=4,
                start_date=seed_periods[0].start_date,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        reauthor_rule(
            rule,
            replace(
                recurrence_spec(rule),
                unit=RecurrenceUnitEnum.MONTH,
                interval_n=3,
                month_of_year=2, day_of_month=10,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        resolved = resolved_for(rule)
        assert resolved.interval_n == 3
        assert resolved.unit is RecurrenceUnitEnum.MONTH
        assert resolved.placement is PeriodPlacementEnum.CONTAINING_DATE
        # The paycheck interval did not survive into a column that now means
        # something else.
        assert rule.interval_n == 1
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
            spec_for(
                pattern,
                user_id=user_id,
                day_of_month=15, month_of_year=3,
                start_date=seed_periods[1].start_date,
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
            spec_for(
                RecurrencePatternEnum.EVERY_PERIOD,
                user_id=user_id,
            ),
            calendar_for(user_id),
        )

        assert rule.id is not None
        assert _db.session.get(RecurrenceRule, rule.id) is rule
