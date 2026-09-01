"""Tests for ``pay_period_admin`` slice (b): the lock classifier, plus the
reusable ``assert_pay_period_invariants`` checker's own self-tests.

The classifier (`classify_schedule_locks`) is the
single place that decides whether a pay period may be deleted or rebuilt.
Getting it wrong risks either silently wiping real money (a settled
period misread as mutable) or refusing legitimate edits, so every reason
and the precedence between them is asserted here.

**It classifies DERIVED periods since plan step C2-f3b**, so every case here
resolves its ORM row through the owner's calendar first (:func:`_derived`) --
the same derivation the four destructive doors classify against.  Two
properties that retype creates are graded in their own classes below: the
HISTORICAL test reads the DERIVED end rather than the stored ``end_date``
column, and an unmaterialised period is refused rather than keyed under
``None``.

The invariant checker is the load-bearing safety net every later
mutation test calls.  A checker that always passes is worse than none,
so its self-tests prove it both PASSES on a healthy schedule and RAISES
on a corrupted one.  See ``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

import pathlib
from datetime import date, timedelta

import pytest

from app.enums import RecurrenceUnitEnum, StatusEnum
from app.exceptions import PayPeriodLocked, ValidationError
from app.models.pay_period import PayPeriod
from app.services import (
    pay_period_admin,
    pay_period_locks,
    pay_period_write,
)
from app.services.pay_calendar import (
    DerivedPeriod,
    PayCalendarError,
    calendar_for,
)
from app.services.pay_period_locks import PeriodLockReason
from app.services.recurrence import RecurrenceSpec, author_rule
from app.utils.dates import display_today
from tests._test_helpers import (
    add_txn,
    assert_pay_period_invariants,
    bare_expense_template,
    freeze_today,
    open_calendar_hole,
)


# Today is well after the seed_user bootstrap period (2024) and before
# these generated periods, so the generated ones are genuinely "future"
# under the default as_of while the bootstrap is historical.
_FUTURE_START = date(2026, 7, 3)
_BOOTSTRAP_AS_OF = date(2024, 1, 1)  # before the bootstrap period ends


def _make_future_periods(db_session, seed_user, count=5):
    """Generate ``count`` future pay periods for ``seed_user``.

    Appended after the fixture's bootstrap period (index 0), so these
    take indices 1..count and all end after today.
    """
    periods = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=_FUTURE_START,
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()
    return periods


def _repo_path(relative_path):
    """Return an absolute path to *relative_path* under the repository root.

    Derived from THIS file rather than from the working directory: the censuses
    below read source off disk, and a relative path would make them silently
    empty -- and so vacuously green -- for any invocation whose cwd is not the
    checkout root.

    Args:
        relative_path: A path relative to the repository root.

    Returns:
        The absolute :class:`pathlib.Path`.
    """
    return pathlib.Path(__file__).resolve().parents[2] / relative_path


def _lock(period, as_of):
    """Return the lock reason the application gives *period* on *as_of*.

    The classifier takes the owner's whole CALENDAR since plan step C2-f3b, so
    this is the suite's door onto the same call the four destructive doors and
    the settings list make: derive the owner's schedule, classify it, read the
    one period out.  Written once here rather than at each case, so no case can
    classify against a set the application never assembles.

    Args:
        period: An ORM :class:`~app.models.pay_period.PayPeriod`.
        as_of: The civil day to classify against.

    Returns:
        Its :class:`PeriodLockReason`, or ``None`` when it is mutable.
    """
    return pay_period_locks.classify_schedule_locks(
        calendar_for(period.user_id), as_of=as_of,
    )[period.id]


def _derived(period):
    """Return the :class:`DerivedPeriod` the application classifies *period* as.

    The classifier stopped taking ORM rows at plan step C2-f3b, and this is the
    suite's door onto the same value the four destructive doors hand it: the
    owner's calendar, derived from their paydays, looked up by id.  Resolving it
    HERE rather than constructing a ``DerivedPeriod`` by hand is what keeps a
    case from asserting against bounds the application never computes -- the
    shape ``period_window`` in ``tests/_test_helpers`` exists for one level up.

    Args:
        period: An ORM :class:`~app.models.pay_period.PayPeriod`.

    Returns:
        Its :class:`DerivedPeriod`.
    """
    resolved = calendar_for(period.user_id).period_by_id(period.id)
    assert resolved is not None, f"period {period.id} is not in its own calendar"
    return resolved


# ``test_account_anchor_locks`` was DELETED at plan step X-f1c3c (ruling
# R-EO): ``PeriodLockReason.ACCOUNT_ANCHOR`` is gone, because neither an
# account nor a balance assertion references a pay period any more, so the
# state it classified cannot arise.  The other four reasons are graded
# unchanged, and the precedence test below still pins their ordering.


class TestClassifyPeriodLock:
    """The classifier returns the correct reason, or None for a mutable period."""

    def test_future_empty_period_is_mutable(self, app, db, seed_user):
        """A future period with no settled txn / anchor / rule -> None."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            assert _lock(periods[2], display_today()) is None

    def test_historical_period_is_locked(self, app, seed_user):
        """A period that has already ended -> HISTORICAL."""
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            assert _lock(
                bootstrap, date(2026, 6, 13),
            ) == PeriodLockReason.HISTORICAL

    def test_settled_transaction_locks(self, app, db, seed_user):
        """A future period holding a Paid (settled) txn -> SETTLED_TXN."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, periods[1], "Rent", "1200.00",
                status_enum=StatusEnum.DONE,
            )
            assert _lock(periods[1], display_today()) == PeriodLockReason.SETTLED_TXN

    def test_projected_only_period_not_locked(self, app, db, seed_user):
        """A future period holding only a Projected txn -> None (mutable)."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, periods[1], "Rent", "1200.00",
                status_enum=StatusEnum.PROJECTED,
            )
            assert _lock(periods[1], display_today()) is None

    def test_soft_deleted_settled_not_locked(self, app, db, seed_user):
        """A soft-deleted settled row does not lock -- the user removed it."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, periods[1], "Rent", "1200.00",
                status_enum=StatusEnum.DONE, is_deleted=True,
            )
            assert _lock(periods[1], display_today()) is None

    def test_cancelled_transaction_not_settled_lock(self, app, db, seed_user):
        """A Cancelled txn is not settled, so it does not SETTLED_TXN-lock.

        This is the basis for the discard-gate split: Credit / Cancelled
        are deliberate-intent rows handled by the overridable confirm
        gate, NOT a hard settled lock.
        """
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, periods[1], "Rent", "1200.00",
                status_enum=StatusEnum.CANCELLED,
            )
            assert _lock(periods[1], display_today()) is None

    def test_anchor_period_with_opening_reports_ledger_postings(
        self, app, seed_user,
    ):
        """The seeded anchor period -> LEDGER_POSTINGS (the opening lives there).

        The Step-5 accepted behavior change (plan Section 3.5): the seed
        Checking's $1000.00 opening correction is attributed to its anchor
        period, whose per-ledger nets are therefore non-zero, and the
        double-entry gate precedes ACCOUNT_ANCHOR.  ``as_of`` is set before
        the bootstrap period ends so the historical check does not pre-empt
        either reason.
        """
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            assert _lock(bootstrap, _BOOTSTRAP_AS_OF) == PeriodLockReason.LEDGER_POSTINGS

    def test_a_recurrence_rules_start_no_longer_locks_a_period(
        self, app, db, seed_user,
    ):
        """``RECURRENCE_ANCHOR`` was DELETED at plan step R7b-4.

        It refused to delete a period some rule's ``start_period_id`` pointed
        at, and the hazard was real: that FK is ``ON DELETE SET NULL``, so
        deleting the period silently ERASED the rule's opening bound.  R7b-4
        folded the FK into a DATE, which no schedule operation can cascade --
        so the bound now survives the deletion of any period and the lock
        guarded a loss that cannot happen.  Plan step R7c-b renamed that date
        to ``starts_on`` and made it the rule's FIRST OCCURRENCE (ruling
        R-R16); the property here is unchanged, and it is the column the rule
        actually carries that the case must read.

        Asserted rather than merely deleted, because "this period is now
        mutable" is a change to what a DESTRUCTIVE operation is allowed to
        touch: a rule whose stated start falls inside a period says nothing
        about whether that period may be rebuilt.
        """
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            rule = author_rule(
                RecurrenceSpec(
                    user_id=seed_user["user"].id,
                    unit=RecurrenceUnitEnum.PERIOD,
                    starts_on=periods[3].start_date,
                ),
                calendar_for(seed_user["user"].id),
                # The definition the rule belongs to (plan step R-F6).
                bare_expense_template(db.session, seed_user),
            )
            db.session.flush()

            assert rule.starts_on == periods[3].start_date
            assert _lock(periods[3], display_today()) is None

    def test_historical_precedes_settled(self, app, db, seed_user):
        """A historical period with a settled txn still reports HISTORICAL."""
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            add_txn(
                db.session, seed_user, bootstrap, "Old Rent", "1200.00",
                status_enum=StatusEnum.DONE,
            )
            assert _lock(
                bootstrap, date(2026, 6, 13),
            ) == PeriodLockReason.HISTORICAL

    def test_settled_precedes_account_anchor(self, app, db, seed_user):
        """A non-historical anchor period with a settled txn reports SETTLED_TXN."""
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            add_txn(
                db.session, seed_user, bootstrap, "Rent", "1200.00",
                status_enum=StatusEnum.DONE,
            )
            # Not historical at this as_of, and it IS the account anchor,
            # but the settled txn outranks the anchor reason.
            assert _lock(bootstrap, _BOOTSTRAP_AS_OF) == PeriodLockReason.SETTLED_TXN


class TestClassifyScheduleLocks:
    """The door answers for EXACTLY the owner's saved periods, and every branch.

    *It compared the bulk classifier against N single-period calls until plan
    step C2-f3b, and that comparison lost its subject: the single-period door
    was a delegating wrapper (``0c7bb2a``) with no ``app/`` caller, so the
    assertion read "the door agrees with itself".  It is DELETED, and what
    replaces it grades the two properties a caller actually rests on -- the KEY
    SET, because ``_gate_deletable_tail`` indexes this map rather than
    ``.get``-ing it, and that every reason is reachable.*
    """

    def test_an_owner_with_no_paydays_gets_an_empty_map(self, app, bare_user):
        """No periods -> empty dict, and no queries to issue."""
        with app.app_context():
            assert pay_period_locks.classify_schedule_locks(
                calendar_for(bare_user["user"].id), as_of=display_today(),
            ) == {}

    def test_it_keys_every_saved_period_and_nothing_else(
        self, app, db, seed_user,
    ):
        """The key set is the owner's saved schedule, exactly.

        ``_gate_deletable_tail`` reads ``locks[period.period_id]`` for every
        period it is about to delete, and its docstring argues that indexing
        rather than ``.get``-ing is the fail-closed choice.  That argument only
        holds while the map covers the whole schedule, which is what this pins.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _make_future_periods(db.session, seed_user)
            locks = pay_period_locks.classify_schedule_locks(
                calendar_for(user_id), as_of=display_today(),
            )
            assert set(locks) == {
                period.id
                for period in db.session.query(PayPeriod)
                .filter_by(user_id=user_id).all()
            }

    def test_one_schedule_reaches_three_different_reasons(
        self, app, db, seed_user,
    ):
        """HISTORICAL, SETTLED_TXN and mutable all appear in one answer.

        A single fixed ``as_of`` exercises every branch, so a classifier that
        collapsed two reasons into one -- or answered ``None`` throughout --
        fails here rather than in whichever door happened to hit the case.

        **The ``as_of`` moved forward at plan step C3-b, and the reason is the
        fixture rather than the classifier.**  It was 2026-06-13, where the 2024
        bootstrap supplied HISTORICAL because its stored end was 2024-01-18.  The
        writer now materialises the payday derivation, so the bootstrap ends the
        day before the next payday (2026-07-02) and is the CURRENT period at that
        date -- ledger row **P27**'s absorption, on this fixture.  2026-07-20 puts
        the bootstrap and the first generated period behind it instead.
        """
        as_of = date(2026, 7, 20)
        with app.app_context():
            futures = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, futures[1], "Rent", "1200.00",
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()

            locks = pay_period_locks.classify_schedule_locks(
                calendar_for(seed_user["user"].id), as_of=as_of,
            )
            assert PeriodLockReason.HISTORICAL in locks.values()
            assert PeriodLockReason.SETTLED_TXN in locks.values()
            assert None in locks.values()


class TestTheHistoricalTestReadsTheDerivedEnd:
    """The lock follows the PAYDAYS, not the stored ``end_date`` column.

    Plan step **C2-f3b**.  ``budget.pay_periods.end_date`` is a stored copy of
    ``lead(start_date) - 1`` that nothing reconciles against the paydays it
    derives from, and plan step **C4** drops it; the classifier read it until
    this step.  The two agree on every row this app writes -- the writer
    materialises the derivation -- so the only way to grade which one is read is
    to make them DISAGREE, which is what ``open_calendar_hole`` is for: it
    writes the column directly, the way rows written before plan step C3-b hold
    it.
    """

    def test_a_stored_end_in_the_past_does_not_make_a_period_historical(
        self, app, db, seed_user,
    ):
        """Stored end 2026-07-19, derived end 2026-07-30, as_of 2026-07-25.

        The period runs 2026-07-17 .. 2026-07-30, because the next payday is
        2026-07-31 and a period ends the day before its successor opens.  Its
        STORED end is shortened to 2026-07-19, which is what a row written by
        the pre-C3-b writer can hold.  At ``as_of`` 2026-07-25 the two columns
        answer opposite questions, and the derivation is the one that is right:
        the owner's paycheck of 2026-07-17 has not ended, so the period is
        mutable rather than history.

        The three-line preamble is the FIRING CONTROL: it asserts the split
        exists before asserting which side of it the classifier lands on, so a
        fixture that silently failed to doctor the column cannot pass this.
        """
        as_of = date(2026, 7, 25)
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            open_calendar_hole(db.session, periods[1], date(2026, 7, 19))

            stored = db.session.get(PayPeriod, periods[1].id)
            derived = _derived(periods[1])
            assert stored.end_date < as_of <= derived.end_date

            assert _lock(periods[1], as_of) is None


class TestTheDoorsDecideOnTheOwnersDay:
    """"Has this paycheck ended" is asked of the OWNER's clock, not the process's.

    Finding **balance:N-191** named the lock classifier as one of two
    sites deciding something against the user's CALENDAR on ``date.today()``,
    and said each needed its own ruling; the developer ruled the owner's civil
    day on 2026-08-19, at plan step C2-f3b, which is the step that made the
    argument required.  Both compose files pin ``TZ: America/New_York``, so the
    two clocks agree in the deployed container -- this grades the rule where the
    pin does not reach (CI, a script, a bare ``flask run``).
    """

    def test_a_paycheck_that_has_not_ended_on_the_owners_clock_is_deletable(
        self, app, db, seed_user, monkeypatch,
    ):
        """The clocks are SPLIT, and the split is asserted rather than assumed.

        The paycheck of 2026-07-31 runs to 2026-08-13, because the next payday
        is 2026-08-14.  The display clock is set to the day it ENDS, where it is
        the owner's current paycheck and truncate may remove it; the process
        clock is frozen one day past that, where the retired rule read it as
        history and refused.  Both are named, and the assertion below proves
        they differ before the door is driven -- without it this would pass on
        any schedule where the two happened to agree.
        """
        display_day = date(2026, 8, 13)
        freeze_today(monkeypatch, date(2026, 8, 14))
        monkeypatch.setattr(
            pay_period_admin, "display_today", lambda: display_day,
        )
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            assert _derived(periods[2]).end_date == display_day
            assert date.today() > display_day

            deleted = pay_period_admin.truncate_pay_periods(
                seed_user["user"].id, periods[1].id,
            )
            assert deleted == 3

    def test_the_same_paycheck_one_day_later_is_history_and_refused(
        self, app, db, seed_user, monkeypatch,
    ):
        """The other side of the boundary, so the case above is not vacuous.

        The same schedule and the same door, one day on: a display clock reading
        2026-08-14 puts the 2026-07-31 paycheck behind the owner, and truncate
        refuses the whole tail.  The process clock stays on the suite's frozen
        2026-03-20, where nothing has ended -- so this case is split the OTHER
        way and the pair brackets the boundary from both sides.  Without it the
        case above would pass on a classifier that never locked anything.
        """
        monkeypatch.setattr(
            pay_period_admin, "display_today", lambda: date(2026, 8, 14),
        )
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            assert date.today() < date(2026, 8, 14)
            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    seed_user["user"].id, periods[1].id,
                )
            assert (
                excinfo.value.blocking[periods[2].id]
                == PeriodLockReason.HISTORICAL
            )


class TestTheGateRefusesAMapItCannotIndex:
    """The documented fail-closed choice, with the control its docstring owes.

    :func:`_gate_deletable_tail` reads ``locks[period.period_id]`` and its
    docstring argues the reason: *"treating the miss as 'unlocked' is the
    direction that deletes settled money."*  An adversarial review measured
    ``.get`` passing the whole suite, so the argument had no control -- and
    verification standard rule 4 says a guard whose control does not fire is not
    a guard.
    """

    def test_a_lock_map_missing_a_doomed_period_raises_rather_than_deleting(
        self, app, db, seed_user,
    ):
        """A caller that classified a different set fails loud.

        Unreachable through the two doors -- both classify the same window they
        gate -- which is why it is driven directly here rather than through one.
        What it pins is the DIRECTION of the failure: a ``KeyError`` naming the
        period, never a silent "not locked" on a period nobody classified.
        """
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            calendar = calendar_for(seed_user["user"].id)
            saved = calendar.saved()
            kept = calendar.period_by_id(periods[0].id)
            complete = pay_period_locks.classify_schedule_locks(
                calendar, as_of=display_today(),
            )
            # Every period AFTER the kept one is doomed; drop the last of them.
            missing = max(
                period.period_id for period in saved
                if period.start_date > kept.start_date
            )
            partial = {
                pid: reason for pid, reason in complete.items()
                if pid != missing
            }

            with pytest.raises(KeyError) as excinfo:
                # pylint: disable=protected-access
                pay_period_admin._gate_deletable_tail(
                    saved, kept, False, partial,
                )
            assert str(missing) in str(excinfo.value)

    def test_the_same_call_with_the_complete_map_answers(
        self, app, db, seed_user,
    ):
        """The control's other side, so the raise above is not the only outcome."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            calendar = calendar_for(seed_user["user"].id)
            kept = calendar.period_by_id(periods[0].id)
            # pylint: disable=protected-access
            doomed = pay_period_admin._gate_deletable_tail(
                calendar.saved(), kept, False,
                pay_period_locks.classify_schedule_locks(
                    calendar, as_of=display_today(),
                ),
            )
            assert {period.period_id for period in doomed} == {
                period.id for period in periods[1:]
            }


class TestAnOwnerWithNoPaydaysReachesEveryDoor:
    """The cardinality no other case varies (adversarial review, 2026-08-19).

    Every other case here runs on four or more periods.  An owner with NONE is
    an ordinary state since plan step ``balance:X-ad-a`` stopped writing a
    bootstrap payday at registration, and it is the state each door's empty
    branch exists for: ``calendar.saved()`` is empty, ``classify_schedule_locks``
    returns ``{}`` without a query, and ``_regenerate_keep_through_period``
    answers ``None`` from its own ``if not periods``.
    """

    def test_extend_refuses_and_names_the_remedy(self, app, bare_user):
        """Extend has nothing to continue from, so it refuses."""
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                pay_period_admin.extend_pay_periods(bare_user["user"].id, 2)
            assert "Generate" in str(excinfo.value)

    def test_regenerate_appends_rather_than_rebuilding(self, app, db, bare_user):
        """With no tail to retire, regenerate degrades to a plain append."""
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=date(2026, 9, 4), num_periods=3,
                cadence_days=14,
            )
            db.session.commit()
            assert [period.start_date for period in created] == [
                date(2026, 9, 4), date(2026, 9, 18), date(2026, 10, 2),
            ]

    def test_reset_builds_a_schedule_from_nothing(self, app, db, bare_user):
        """Reset retires an empty set and records the batch."""
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=date(2026, 9, 4), num_periods=2,
                cadence_days=14,
            )
            db.session.commit()
            assert len(created) == 2

    def test_the_lock_door_answers_without_a_query(self, app, bare_user):
        """An empty calendar classifies to ``{}`` -- there is nothing to ask about."""
        with app.app_context():
            assert pay_period_locks.classify_schedule_locks(
                calendar_for(bare_user["user"].id), as_of=display_today(),
            ) == {}


class TestTruncateResolvesItsFactsOnce:
    """Truncate reads one clock and one lock map, like regenerate beside it.

    ``TestRegenerateResolvesItsFactsOnce`` grades the door that used to classify
    TWICE.  Truncate classified once already -- but over the TAIL, and plan step
    C2-f3b widened it to the whole schedule so that one map can serve both
    doors.  An adversarial review named the widened argument as ungraded, and it
    is the argument that makes ``_gate_deletable_tail``'s indexing safe.
    """

    def test_it_classifies_the_whole_schedule_exactly_once(
        self, app, db, seed_user, monkeypatch,
    ):
        """One call, keyed over every saved period rather than the doomed tail."""
        calls = []
        real = pay_period_admin.classify_schedule_locks

        def _counting(calendar, *, as_of):
            """Record each classification and delegate to the real one."""
            answer = real(calendar, as_of=as_of)
            calls.append((tuple(sorted(answer)), as_of))
            return answer

        monkeypatch.setattr(
            pay_period_admin, "classify_schedule_locks", _counting,
        )
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            user_id = seed_user["user"].id
            expected_ids = {period.id for period in periods}
            expected_ids.add(seed_user["bootstrap_period"].id)

            assert pay_period_admin.truncate_pay_periods(
                user_id, periods[1].id,
            ) == 3
            db.session.commit()

            assert len(calls) == 1
            classified, as_of = calls[0]
            assert set(classified) == expected_ids
            assert as_of == display_today()


class TestADoorDecidesOnTheDerivationEndToEnd:
    """A DOOR, not the classifier, driven over a stored/derived disagreement.

    The split reached the classifier directly and the settings LABEL, and no
    case put it under a door that deletes -- which an adversarial review named
    as the axis no destructive case varies.  This is the money-shaped version:
    the stored column says the paycheck is history and hard-locked, the paydays
    say it is current and deletable, and truncate must follow the paydays.
    """

    def test_truncate_follows_the_paydays_where_the_column_disagrees(
        self, app, db, seed_user, monkeypatch,
    ):
        """Stored end 2026-08-01, derived end 2026-08-13, owner's day 2026-08-05.

        The DOOMED period is the one doctored, because the lock gate reads the
        periods a truncate would delete rather than the one it keeps -- a first
        cut of this case shortened the KEPT period and graded nothing.  On the
        stored column that paycheck ended four days ago and ``PayPeriodLocked``
        refuses the whole tail; on the paydays it is the owner's current
        paycheck and the tail may go.

        The preamble is the firing control: it asserts the two columns really
        disagree, and that the disagreement straddles the day being asked about,
        before the door is driven.
        """
        owner_day = date(2026, 8, 5)
        monkeypatch.setattr(
            pay_period_admin, "display_today", lambda: owner_day,
        )
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            open_calendar_hole(db.session, periods[2], date(2026, 8, 1))
            db.session.commit()

            stored = db.session.get(PayPeriod, periods[2].id)
            assert stored.end_date < owner_day <= _derived(periods[2]).end_date

            assert pay_period_admin.truncate_pay_periods(
                seed_user["user"].id, periods[1].id,
            ) == 3


class TestTheDestructiveDoorsHoldNoDerivedColumn:
    """The end state of plan step **C2-f3b**, graded as a census.

    Each of the four doors reads the owner's schedule through ``calendar_for``
    and decides in ``DerivedPeriod`` values, so ``end_date`` and
    ``period_index`` -- the two columns plan step **C4** drops -- are read
    nowhere in this module or in the settings page's period list.  **The one
    reader that survived C2-f3b, ``_future_period_count`` on the rolling
    top-up, went at C4's first commit**, so the census below asserts the EMPTY
    set for both files (finding **P70**).

    **Two adversarial reviews of this step measured the FIRST cut of this class
    passing on the defect it names**, and the rewrite is theirs.  That predicate
    matched only ``PayPeriod.<column>`` -- an ``ast.Name`` receiver -- and
    walked only inside function definitions, so an INSTANCE read
    (``row.end_date``, which is how every ORM consumer in this codebase actually
    reads a column), a module-level constant, and an aliased import were all
    invisible; two mutations were measured surviving a green suite.  This one
    records ``(scope, attribute, receiver)`` for EVERY spelling over the WHOLE
    module and asserts the exact set, so any new read fails the census whatever
    it is written on.  See ``docs/plans/lessons.md`` on a guard graded by a
    census narrower than its own claim.
    """

    _DROPPED_COLUMNS = frozenset({"end_date", "period_index"})

    def _column_reads(self, relative_path):
        """Return ``{(scope, attribute, receiver)}`` for every dropped-column read.

        Args:
            relative_path: Path under the repository root to parse.

        Returns:
            A set of ``(enclosing scope name, attribute, receiver source)``
            triples.  The scope is ``"<module>"`` for a read outside any
            function, which is one of the two shapes the first cut missed.
        """
        # pylint: disable=import-outside-toplevel
        import ast

        tree = ast.parse(_repo_path(relative_path).read_text())
        scopes = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    scopes[inner] = node.name
        found = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in self._DROPPED_COLUMNS
            ):
                found.add((
                    scopes.get(node, "<module>"),
                    node.attr,
                    ast.unparse(node.value),
                ))
        return found

    def _imported_names(self, relative_path):
        """Return every ``(module, imported name)`` pair, alias-proof.

        Args:
            relative_path: Path under the repository root to parse.

        Returns:
            The set of pairs.  Keyed on what was IMPORTED rather than on the
            local binding, so ``import x as y`` cannot hide from the census --
            which a substring grep for the call site could not say.
        """
        # pylint: disable=import-outside-toplevel
        import ast

        tree = ast.parse(_repo_path(relative_path).read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update((alias.name, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.update(
                    (node.module or "", alias.name) for alias in node.names
                )
        return names

    @pytest.mark.parametrize("module", [
        "app/services/pay_period_admin.py",
        "app/services/pay_period_rolling.py",
    ])
    def test_the_schedule_doors_read_no_dropped_column_anywhere(self, module):
        """Neither module names either column, in any scope, on any receiver.

        **Both, because plan step C4 SPLIT them** (finding **P31**): the
        rolling top-up moved to ``pay_period_rolling`` and it is the door that
        held the last read, so a census scoped to the file it used to live in
        would be green about a module the read had simply left.

        **The last survivor went at plan step C4** (finding **P70**):
        ``_future_period_count`` counted ``PayPeriod.end_date >= as_of`` in SQL
        and now derives the calendar from the owner's paydays and asks
        :meth:`~app.services.pay_calendar.PayCalendar.current_and_future`.
        **Not ``overlapping``**, which is the same question with its bounds
        written out and which REFUSES them past the horizon -- the distinction
        the new producer exists for, and one an earlier draft of this docstring
        got backwards.

        The empty set is a STRONGER claim than the one-element set this
        replaced, and it is the strongest this census can make: the predicate
        matches an attribute NAME on any receiver in any scope, so it cannot
        tell an ORM read from a ``DerivedPeriod`` one -- which means a module
        that reads neither name is a module no future edit can quietly regrow
        either read in.  Counting through a window rather than comparing ends
        here is what buys that, and it is why the replacement does not simply
        filter on ``period.end_date``.

        Args:
            module: The file to census, relative to the repository root.
        """
        assert self._column_reads(module) == set()

    def test_the_settings_period_list_reads_none(self):
        """The page that RENDERS the schedule reads neither column."""
        assert self._column_reads("app/routes/settings.py") == set()

    def test_neither_module_can_reach_the_retired_period_list(self):
        """Neither module reaches ``pay_period_service``, under any alias.

        ``get_all_periods`` itself is GONE since plan step C2-f3c -- the
        generation seam was its last caller and now takes a ``PayCalendar`` --
        so what this still pins is the module boundary rather than one name: no
        schedule DOOR and no settings render may reach back into the pay-period
        reader module for anything.  Graded on the IMPORT rather than on the
        call, because ``from ... import get_all_periods as rows`` defeats a
        grep for the call site and was measured doing so.
        """
        for relative in (
            "app/services/pay_period_admin.py",
            "app/routes/settings.py",
        ):
            imported = self._imported_names(relative)
            offenders = {
                pair for pair in imported
                if pair[1] in {"pay_period_service", "get_all_periods"}
                or pair[0].endswith("pay_period_service")
            }
            assert offenders == set(), (relative, offenders)


class TestInvariantChecker:
    """``assert_pay_period_invariants`` passes on healthy, raises on corrupt."""

    def test_passes_on_healthy_schedule(self, app, db, bare_periods):
        """A contiguous, in-order schedule satisfies every invariant."""
        with app.app_context():
            assert_pay_period_invariants(db.session, bare_periods[0].user_id)

    def test_passes_on_full_user_data(self, app, db, seed_full_user_data):
        """A user with accounts, periods, and transactions passes.

        Exercises the anchor-integrity and orphan invariants on real
        account + transaction rows, not just bare periods.
        """
        with app.app_context():
            assert_pay_period_invariants(
                db.session, seed_full_user_data["user"].id,
            )

    def test_raises_when_index_order_differs_from_dates(
        self, app, db, bare_user_with_cadence,
    ):
        """Index order not matching calendar order is caught.

        Two periods are inserted so the lower index has the LATER start
        date -- the exact corruption that makes the balance resolver walk
        periods out of order and silently drop transactions.
        """
        user_id = bare_user_with_cadence["user"].id
        with app.app_context():
            db.session.add_all([
                PayPeriod(
                    user_id=user_id, period_index=0,
                    start_date=date(2026, 6, 1), end_date=date(2026, 6, 14),
                ),
                PayPeriod(
                    user_id=user_id, period_index=1,
                    start_date=date(2026, 1, 1), end_date=date(2026, 1, 14),
                ),
            ])
            db.session.commit()
            with pytest.raises(AssertionError, match="calendar order"):
                assert_pay_period_invariants(db.session, user_id)

    def test_raises_on_index_gap(self, app, db, bare_user_with_cadence):
        """A non-contiguous period_index sequence is caught."""
        user_id = bare_user_with_cadence["user"].id
        with app.app_context():
            db.session.add_all([
                PayPeriod(
                    user_id=user_id, period_index=0,
                    start_date=date(2026, 1, 1), end_date=date(2026, 1, 14),
                ),
                PayPeriod(
                    user_id=user_id, period_index=2,
                    start_date=date(2026, 1, 15), end_date=date(2026, 1, 28),
                ),
            ])
            db.session.commit()
            with pytest.raises(AssertionError, match="gap"):
                assert_pay_period_invariants(db.session, user_id)

    def test_raises_on_date_overlap(self, app, db, bare_user_with_cadence):
        """Two periods whose date spans overlap is caught."""
        user_id = bare_user_with_cadence["user"].id
        with app.app_context():
            db.session.add_all([
                PayPeriod(
                    user_id=user_id, period_index=0,
                    start_date=date(2026, 1, 1), end_date=date(2026, 2, 1),
                ),
                PayPeriod(
                    user_id=user_id, period_index=1,
                    start_date=date(2026, 1, 15), end_date=date(2026, 3, 1),
                ),
            ])
            db.session.commit()
            with pytest.raises(AssertionError, match="overlaps"):
                assert_pay_period_invariants(db.session, user_id)
