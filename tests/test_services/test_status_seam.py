"""
Shekel Budget App -- Transaction Status Seam Tests

Tests for ``app.services.status_seam.apply_status_change`` -- the single
status-mechanics primitive every non-transfer status change routes through
(Build-Order Step 3, Commit 5).  The seam does the transition check, the
``status_id`` assignment, the ``settled_on`` maintenance, and the ``status``
relationship refresh; it does NOT post to the ledger (that is Commit 6) and does
NOT flush or commit (the caller owns the session boundary).

Each test verifies one contract with explicit values so a regression surfaces
with a precise message.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from unittest.mock import patch

from app import ref_cache
from app.enums import SettlementBasisEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import balance_at, pay_period_service, status_seam
from app.services.balance_at import BalanceContext
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import display_today

from tests._test_helpers import (
    an_entered_day,
    settlement_if_settling,
)
from tests._test_helpers import freeze_today
from app.services.settle_day import record_settle_day
from app.models.amount_ownership import AmountOwnership

#: A civil day whose EVENING in ``America/New_York`` falls on the PREVIOUS UTC
#: day's successor -- i.e. an instant where the two calendars disagree.  Frozen
#: at 01:00 UTC, which is 20:00 EST / 21:00 EDT the day before, so it separates
#: the two rules on either side of every DST transition.
_UTC_DAY_AT_AN_EASTERN_EVENING = date(2026, 3, 4)
_EASTERN_EVENING_UTC_TIME = time(1, 0)


def _make_txn(seed_user, period, *, status):
    """Create and flush an ad-hoc expense in the given status.

    The seam operates on any non-transfer transaction, so an ad-hoc expense
    (no template, no entries) is the minimal fixture.  ``status`` is a
    :class:`StatusEnum` member resolved to its id.
    """
    txn = Transaction(
        user_id=period.user_id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=ref_cache.status_id(status),
        name="Seam test expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        amount_ownership=AmountOwnership.own(Decimal("50.00")),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


class TestApplyStatusChangeSettleDay:
    """settled_on is derived from the new status unless explicitly supplied."""

    def test_enter_settled_stamps_the_users_today(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected -> Paid with no explicit day stamps the user's civil today.

        A ``date``, not an instant: since plan step X-f1 the column stores the
        civil DAY the money moved, and ``display_today()`` is the accessor for
        "what day is it on the user's clock".  The sibling test below is what
        distinguishes that from the process's UTC day; this one pins the type
        and the value on a day where the two agree.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            assert txn.settled_on is None

            expected = display_today()
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            db.session.commit()

            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on == expected
            assert not isinstance(txn.settled_on, datetime), (
                "settled_on must be a civil date, never an instant -- an "
                "instant here is truncated by PostgreSQL on the UTC session "
                "clock (finding N-179)."
            )

    def test_the_stamped_day_is_the_users_day_not_the_process_utc_day(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """An evening-Eastern settle is filed on the EASTERN day (ruling R-DH (b)).

        **The only test in the suite that can tell the two rules apart, and it
        exists because nothing could** (finding **N-182**).  ``freeze_today``
        defaults to NOON UTC, which is the same civil day in both calendars, so
        every clock-freezing test in the suite passes identically whether the
        seam reads ``display_today()`` or ``date.today()`` -- measured: swapping
        them shipped a GREEN suite.  Freezing at 01:00 UTC puts the user's clock
        at 20:00 the PREVIOUS evening, where the two answers differ by a day.

        This is the L9 / R-DH (b) / F3 rule three rulings closed, and the day it
        decides is not cosmetic: since plan step E1a it IS the ``entry_date``
        the row's postings are filed under, so a UTC read moves an evening
        payment's money into the next day -- and, for a payment near a period
        boundary, into the next pay period.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            freeze_today(
                monkeypatch,
                _UTC_DAY_AT_AN_EASTERN_EVENING,
                at_time=_EASTERN_EVENING_UTC_TIME,
            )
            eastern_day = _UTC_DAY_AT_AN_EASTERN_EVENING - timedelta(days=1)
            # The freeze is only a control if the two rules really disagree
            # under it; assert that before asserting which one the seam took.
            assert display_today() == eastern_day
            assert date.today() == _UTC_DAY_AT_AN_EASTERN_EVENING

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            db.session.commit()

            db.session.refresh(txn)
            assert txn.settled_on == eastern_day, (
                "The seam stamped the process's UTC day rather than the user's "
                f"civil day: {txn.settled_on} != {eastern_day}.  A settle at "
                "8pm Eastern belongs to that evening, not to tomorrow "
                "(ruling R-DH (b))."
            )

    def test_explicit_day_written_verbatim(
        self, app, db, seed_user, seed_periods,
    ):
        """A caller-supplied day (the correction door) is used as-is."""
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            explicit = date(2026, 3, 15)

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE), settle_day=an_entered_day(explicit),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            db.session.commit()

            db.session.refresh(txn)
            assert txn.settled_on == explicit

    def test_an_instant_never_REACHES_this_seam(
        self, app, db, seed_user, seed_periods,
    ):
        """A ``datetime`` is refused one layer out, so no call is ever made.

        Finding **N-179**, and it is a refusal rather than a conversion because
        the conversion is the defect: ``datetime`` subclasses ``date``, so the
        annotation catches nothing, and PostgreSQL coerces the value into the
        ``DATE`` column on the SESSION clock (UTC) -- filing an evening-Eastern
        settle on the following day, silently.  The instant below is
        2026-03-03 23:30 Eastern, which UTC calls 2026-03-04.

        **The guard MOVED at plan step X-az and this test moved with it.**  The
        seam used to call ``reject_settle_instant`` as its first statement,
        purely so a refused call left the row untouched; the day now arrives as
        a :class:`~app.services.settle_day.SettleDay`, whose constructor refuses
        the instant at the CALLER -- strictly earlier, and it buys the same
        property for free.  What this asserts is therefore that the seam is
        never entered at all, which is why the row is examined AFTER the
        ``raises`` block rather than inside it: a post-condition on a call that
        did not happen restates the fixture, which is what this test had become
        (adversarial review, 2026-08-22).
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            done_id = ref_cache.status_id(StatusEnum.DONE)
            instant = datetime(2026, 3, 4, 4, 30, tzinfo=timezone.utc)
            calls = []
            original = status_seam.apply_status_change

            def _record(*args, **kwargs):
                calls.append((args, kwargs))
                return original(*args, **kwargs)

            with patch.object(status_seam, "apply_status_change", _record):
                with pytest.raises(TypeError) as exc:
                    status_seam.apply_status_change(
                        txn, done_id, settle_day=an_entered_day(instant),
                        settlement=settlement_if_settling(txn, done_id),
                    )
            assert "must be a date" in str(exc.value)
            assert calls == [], (
                "the seam was ENTERED with an instant; the value type's "
                "constructor is supposed to refuse it at the caller"
            )
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None

    def test_a_day_for_a_non_settled_status_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """Supplying a day while moving OUT of the settled band raises.

        Finding **N-183**.  The invariant this step establishes is that a row
        carries a settle day if and only if it is settled, and the seam is the
        one door that writes both -- so the explicit arm must not be able to
        date a Projected row.  It could until this check landed, and
        ``transfer_service.update_transfer`` was a live caller that reached it.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            with pytest.raises(ValidationError) as exc:
                status_seam.apply_status_change(
                    txn, projected_id, settle_day=an_entered_day(date(2026, 3, 15)),
                    settlement=settlement_if_settling(txn, projected_id),
                )
            assert "not a settled status" in str(exc.value)
            assert txn.settled_on is None

    def test_re_settle_preserves_an_existing_day(
        self, app, db, seed_user, seed_periods,
    ):
        """An idempotent re-settle (Paid -> Paid) does NOT churn settled_on.

        ``apply_status_change`` stamps the user's today only when entering a
        settled status with no day yet; a row that already carries one keeps it,
        so editing a Paid row (which re-submits its unchanged status) never
        rewrites the day its money moved.

        **The row is back-dated THROUGH the seam first, and that is what makes
        this control able to fail** (finding **N-182**).  Settling twice under
        one clock leaves both calls returning the same ``display_today()``, so
        the assertion held for a row that was re-stamped as surely as for one
        that was preserved -- the same shape as the transfer-side pin for
        finding N-178, which back-dates before it replays.  Deleting the
        preserve arm now moves the day by 30, which is finding **N-146**'s whole
        class: since plan step E1a this day IS the posted ``entry_date``, so a
        re-stamp moves the money.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            done_id = ref_cache.status_id(StatusEnum.DONE)
            settled_a_month_ago = display_today() - timedelta(days=30)
            status_seam.apply_status_change(
                txn, done_id, settle_day=an_entered_day(settled_a_month_ago),
                settlement=settlement_if_settling(txn, done_id),
            )
            db.session.commit()
            db.session.refresh(txn)
            assert txn.settled_on == settled_a_month_ago
            assert txn.settled_on != display_today(), (
                "the back-date must differ from today or the assertion below "
                "cannot distinguish a preserved day from a re-stamped one"
            )

            # Re-settle (identity transition, allowed) with NO explicit day --
            # the shape an edit form's unchanged-status re-submit produces.
            status_seam.apply_status_change(txn, done_id, settlement=settlement_if_settling(txn, done_id))
            db.session.commit()
            db.session.refresh(txn)
            assert txn.settled_on == settled_a_month_ago, (
                "A re-settle re-dated the money: "
                f"{settled_a_month_ago} -> {txn.settled_on} (finding N-146)."
            )

    def test_leave_settled_clears_the_day(
        self, app, db, seed_user, seed_periods,
    ):
        """Paid -> Projected (a revert) clears settled_on."""
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            db.session.commit()
            db.session.refresh(txn)
            assert txn.settled_on is not None

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.PROJECTED)),
            )
            db.session.commit()
            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None

    def test_enter_non_settled_leaves_the_day_none(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected -> Cancelled (non-settled) leaves settled_on None."""
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.CANCELLED)),
            )
            db.session.commit()
            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert txn.settled_on is None


class TestApplyStatusChangeTransition:
    """The seam enforces the state machine and refreshes the status relationship."""

    def test_illegal_transition_raises(
        self, app, db, seed_user, seed_periods,
    ):
        """An illegal move (Paid -> Cancelled) raises ValidationError, no mutation.

        Done -> Cancelled is not a legal transaction transition (the state
        machine admits done -> {done, projected, settled}); the seam must refuse
        it and leave status_id untouched.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.DONE,
            )
            done_id = ref_cache.status_id(StatusEnum.DONE)

            with pytest.raises(ValidationError):
                status_seam.apply_status_change(
                    txn, ref_cache.status_id(StatusEnum.CANCELLED),
                    settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.CANCELLED)),
                )
            # status_id is unchanged -- verify_transition runs before the assign.
            assert txn.status_id == done_id

    def test_status_relationship_is_fresh_after_change(
        self, app, db, seed_user, seed_periods,
    ):
        """The eagerly-joined status relationship reflects the NEW status pre-commit.

        Loads the (cached) Projected relationship first, so without the seam's
        ``expire`` the read after the change would be the stale Projected row.
        The seam expires it, so ``txn.status`` reloads to Paid -- proving the
        absorbed expire works.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            # Populate the cached relationship with the pre-change row.
            assert txn.status.id == ref_cache.status_id(StatusEnum.PROJECTED)

            done_id = ref_cache.status_id(StatusEnum.DONE)
            status_seam.apply_status_change(txn, done_id, settlement=settlement_if_settling(txn, done_id))

            # No commit: the fresh value comes from the seam's expire, not
            # expire_on_commit.
            assert txn.status.id == done_id

    def test_does_not_commit(
        self, app, db, seed_user, seed_periods,
    ):
        """The seam mutates in place but never commits -- a rollback reverts it."""
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            db.session.commit()
            txn_id = txn.id

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

            db.session.rollback()
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert reloaded.settled_on is None


class TestSettleDayForStatus:
    """``settle_day_for_status`` -- the EDIT DOORS' half of the invariant.

    Ruling **R-EG** (plan step X-f1c).  Both full-edit forms submit the row's
    whole state on Save, and the documented way to unlock a finalised row is to
    set Status to Projected in that same form -- so a revert arrives carrying
    the day the row already had.  This function is the one place that decides
    what a form submission MEANS, so the transaction PATCH and the transfer
    PATCH cannot answer it differently.
    """

    def test_a_day_for_a_settled_status_is_kept(
        self, app, db, seed_user, seed_periods,
    ):
        """Moving into (or staying in) the settled band keeps the typed day.

        The correction case ruling R-ED exists for: the user read their
        statement and typed the day the bank really moved the money.

        **The band is DERIVED from ``settled_status_ids()``, not listed**, and
        the difference is the guarantee: a hardcoded ``(DONE, RECEIVED,
        SETTLED)`` is correct today and says nothing about a fourth settled
        status, which is exactly the silent fall-through this test claims to
        prevent.  Reading the predicate the function itself reads makes the
        claim true rather than currently-accurate.
        """
        with app.app_context():
            # Derived from the schedule, not written as a literal: a forwarded
            # day is bounded below by ruling R-EL, so a hard-coded date would
            # make this a test of the FLOOR on whatever calendar it ran.
            user_id = seed_user["user"].id
            typed = pay_period_service.earliest_recordable_day(user_id)
            settled_ids = settled_status_ids()
            assert settled_ids, "the settled band is empty; nothing is graded"
            for status_id in settled_ids:
                # The pair, not a bare day (plan step **X-az**): the reading
                # answers WHAT KIND of day a form submitted, and a day out of a
                # date box is the owner's own -- ``entered``.
                assert status_seam.settle_day_for_status(
                    user_id, status_id, typed,
                ) == an_entered_day(typed), (
                    f"status {status_id} dropped a legitimate correction"
                )

    def test_a_day_beside_a_revert_is_dropped(
        self, app, db, seed_user, seed_periods,
    ):
        """A day submitted alongside a non-settled status is dropped, not kept.

        The stale-echo case.  Handing the day on to the seam would raise
        ``ValidationError`` (``reject_settle_day_without_settled_status``) and
        break the only unlock path on every settled row; writing it would date a
        row whose money has not moved.  Dropping it lets the seam CLEAR the
        column, which is what picking Projected means.

        **The complement is DERIVED too** -- every ``StatusEnum`` member NOT in
        ``settled_status_ids()`` -- so a new non-settled status is covered the
        day it is added, and the two halves of this pair provably partition the
        vocabulary rather than sampling it.
        """
        with app.app_context():
            settled_ids = settled_status_ids()
            unsettled = [
                ref_cache.status_id(member) for member in StatusEnum
                if ref_cache.status_id(member) not in settled_ids
            ]
            assert unsettled, "no non-settled status exists; nothing is graded"
            for status_id in unsettled:
                # A day BELOW the schedule on purpose: a dropped day is never
                # bounded (ruling R-EL bounds only a FORWARDED one), because
                # dropping writes nothing.  Refusing here would break the unlock
                # path the drop exists to keep open.
                assert status_seam.settle_day_for_status(
                    seed_user["user"].id, status_id, date(1999, 1, 1),
                ) is None, (
                    f"status {status_id} kept a day for a row with no movement"
                )

    def test_no_submitted_day_is_none_for_every_status(
        self, app, db, seed_user, seed_periods,
    ):
        """An absent day yields ``None``, which the seam reads as "derive it".

        The everyday path: a form that carries no settle day at all (the
        quick-edit, a notes-only save, a mark-done) must leave the seam free to
        preserve an existing day or stamp today.  Returning anything else here
        would make every ordinary edit an assertion about when money moved.
        """
        with app.app_context():
            for member in StatusEnum:
                assert status_seam.settle_day_for_status(
                    seed_user['user'].id, ref_cache.status_id(member), None,
                ) is None

    def test_the_door_drops_exactly_what_the_service_guard_refuses(
        self, app, db, seed_user, seed_periods,
    ):
        """The two halves are exact complements, across EVERY status.

        The pair is the ruling: :func:`settle_day_for_status` makes a FORM
        submission forgiving, while a SERVICE caller passing a day for an
        unsettled status is asserting both facts on purpose and must still fail
        loud.  Were the refusal relaxed instead of the door made specific,
        ``update_transfer`` could date a Projected row again -- finding
        **N-183**.

        **What this grades is the COMPLEMENTARITY, not either half.**  Asserting
        only that the service refuses a Projected row duplicates
        ``test_a_day_for_a_non_settled_status_is_refused`` above -- same fixture,
        same call, same assertions, different date literal -- and one mutation
        would kill both, which a neutral review caught.  Iterating every
        ``StatusEnum`` member and requiring "the door passed the day through" and
        "the service accepted it" to agree on ALL of them is falsifiable on its
        own: widen the door and a status appears that the door forwards and the
        service rejects; narrow the refusal and one appears that the door drops
        while the service takes it.
        """
        with app.app_context():
            day = display_today() - timedelta(days=3)
            for member in StatusEnum:
                status_id = ref_cache.status_id(member)
                door_forwards = status_seam.settle_day_for_status(
                    seed_user["user"].id, status_id, day,
                ) is not None

                txn = _make_txn(
                    seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
                )
                try:
                    status_seam.apply_status_change(
                        txn, status_id, settle_day=an_entered_day(day),
                        settlement=settlement_if_settling(txn, status_id),
                    )
                except ValidationError as exc:
                    # The seam raises ``ValidationError`` for BOTH the day rule
                    # and an illegal transition, and only the first is evidence
                    # here.  The day refusal runs FIRST (it precedes
                    # ``verify_transition`` so a rejected call leaves the row
                    # untouched), so its message distinguishes them.
                    if "not a settled status" not in str(exc):
                        continue
                    service_accepts = False
                    assert txn.settled_on is None
                else:
                    service_accepts = True

                assert door_forwards == service_accepts, (
                    f"{member.name}: the form door "
                    f"{'forwards' if door_forwards else 'drops'} a settle day "
                    f"that the service "
                    f"{'accepts' if service_accepts else 'refuses'} -- the two "
                    "halves of ruling R-EG have stopped being complements"
                )


class TestRejectFutureSettleDay:
    """``reject_future_settle_day`` -- ruling **R-EJ**, at the one write door.

    A row carries a settle day if and only if it is settled, and settled means
    the money HAS moved -- so a day that has not happened is not a fact about
    money.  The guard lives on the seam rather than in each route because the
    seam is the single writer of the column; both edit doors also carry ``max``
    = today so the browser refuses first.
    """

    def test_a_future_day_is_refused(self, app, db, seed_user, seed_periods):
        """Tomorrow is refused, and the row is left entirely untouched.

        The untouched half matters as much as the refusal: the guard is ordered
        ahead of ``verify_transition`` and the ``status_id`` assignment, so a
        rejected call cannot leave a row status-changed and undated -- the state
        the balance walk REFUSES.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            with pytest.raises(ValidationError) as exc:
                status_seam.apply_status_change(
                    txn,
                    ref_cache.status_id(StatusEnum.DONE),
                    settle_day=an_entered_day(display_today() + timedelta(days=1)),
                    settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
                )
            assert "has not happened yet" in str(exc.value)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None

    def test_today_and_the_past_are_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """Today is on the allowed side, and so is any day back to the floor.

        The boundary control: the comparison is ``>`` today, not ``>=``, because
        a one-click settle stamps exactly today.  A settle legitimately falls
        well before its own pay period, so a day months back is a legal
        correction rather than a suspicious one -- **down to the schedule's own
        start**, which is where ruling R-EL puts the floor (its own class
        below).  The deepest day tested is derived from that floor rather than
        written as an offset literal, so this stays a test of the CEILING and
        cannot start failing on the floor when the fixture's calendar moves.
        """
        with app.app_context():
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )
            today = display_today()
            for day in (today, today - timedelta(days=1), floor):
                txn = _make_txn(
                    seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
                )
                status_seam.apply_status_change(
                    txn, ref_cache.status_id(StatusEnum.DONE), settle_day=an_entered_day(day),
                    settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
                )
                assert txn.settled_on == day

    def test_the_refusal_reads_the_DISPLAY_clock(
        self, app, db, monkeypatch, seed_user, seed_periods,
    ):
        """The bound is the USER's today, not the process's UTC day.

        Ruling R-DH (b), one layer down.  Frozen at 01:00 UTC -- 20:00 EST /
        21:00 EDT the previous evening -- the two calendars disagree: the
        process is already on the NEXT day.  A guard written against
        ``date.today()`` would therefore ACCEPT a day the user has not reached,
        which is the whole defect, so the Eastern day must be the ceiling and
        the UTC day must be refused.
        """
        with app.app_context():
            freeze_today(
                monkeypatch,
                _UTC_DAY_AT_AN_EASTERN_EVENING,
                at_time=_EASTERN_EVENING_UTC_TIME,
            )
            eastern_day = _UTC_DAY_AT_AN_EASTERN_EVENING - timedelta(days=1)
            # The freeze is only a control if the two rules really disagree
            # under it; assert that before asserting which one the guard took.
            assert display_today() == eastern_day
            assert date.today() == _UTC_DAY_AT_AN_EASTERN_EVENING

            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            # The UTC calendar's day, which is the Eastern day PLUS one at this
            # instant -- the exact value a ``date.today()`` guard would allow.
            with pytest.raises(ValidationError) as exc:
                status_seam.apply_status_change(
                    txn,
                    ref_cache.status_id(StatusEnum.DONE),
                    settle_day=an_entered_day(_UTC_DAY_AT_AN_EASTERN_EVENING),
                    settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
                )
            assert "has not happened yet" in str(exc.value)

    def test_no_day_supplied_is_always_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """``None`` passes: it means "derive the day", the everyday path.

        Every mark-done, cancel and notes-only edit reaches the seam with no
        day.  A guard that treated ``None`` as a value would break all of them.
        """
        with app.app_context():
            status_seam.reject_future_settle_day(None)
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            assert txn.settled_on == display_today()


class TestTheSettleDayFloor:
    """The FLOOR on a submitted settle day -- ruling **R-EL**, at the DOOR.

    ``reject_future_settle_day`` refuses a future day from ANY caller, because
    no caller can legitimately record money that has not moved.  **The floor is
    deliberately not like that.**  A day at or before an assertion is absorbed
    into it by ``cash_ledger._walk``, which then resets the running total to the
    asserted balance -- and for a GENUINE pre-schedule settle that is CORRECT
    (ruling R-EB: an assertion reconciles, so anything before it is already
    inside the asserted balance).  Recording money that moved before you started
    budgeting is a real thing to do, and a bank import would do it in bulk.

    So the bound lives in :func:`status_seam.settle_day_for_status`, the edit
    doors' rule, and NOT in ``apply_status_change``.  Enforcing it at the seam
    was tried first and refused SIX existing tests whose scenario is a payment
    budgeted to a 2026 pay period whose cash moved in December 2025 -- the
    year-boundary attribution rule, and exactly the shape an import produces.

    The bound itself is ``pay_period_service.earliest_recordable_day``, the same
    floor an anchor's ``observed_on`` has used since finding **N-133**.
    """

    def test_a_day_before_the_schedule_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """One day below the floor raises.

        Asserted at the boundary rather than at an arbitrary depth, so the
        comparison direction is pinned: a ``<`` where the code has ``<=`` (or
        the reverse) moves exactly this day across the line.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            floor = pay_period_service.earliest_recordable_day(user_id)
            with pytest.raises(ValidationError) as exc:
                status_seam.settle_day_for_status(
                    user_id, ref_cache.status_id(StatusEnum.DONE),
                    floor - timedelta(days=1),
                )
            assert "before this budget's schedule starts" in str(exc.value)

    def test_the_floor_itself_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The floor day is legal -- the bound is inclusive.

        Paired with the case above so neither can pass while the comparison is
        off by one day in either direction.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            floor = pay_period_service.earliest_recordable_day(user_id)
            assert status_seam.settle_day_for_status(
                user_id, ref_cache.status_id(StatusEnum.DONE), floor,
            ) == an_entered_day(floor)

    def test_a_dropped_day_is_never_bounded(
        self, app, db, seed_user, seed_periods,
    ):
        """A day beside a REVERT is dropped without being graded.

        The interaction ruling R-EG and ruling R-EL have with each other, and it
        only goes one way: a dropped day writes nothing, so bounding it would
        refuse the unlock path on a settled row whose own day happens to precede
        the schedule -- breaking the path the drop exists to keep open.
        """
        with app.app_context():
            assert status_seam.settle_day_for_status(
                seed_user["user"].id,
                ref_cache.status_id(StatusEnum.PROJECTED),
                date(1999, 1, 1),
            ) is None

    def test_the_service_itself_still_accepts_a_pre_schedule_day(
        self, app, db, seed_user, seed_periods,
    ):
        """``apply_status_change`` does NOT enforce the floor, on purpose.

        The load-bearing half of ruling R-EL's placement, and the reason it is a
        separate test rather than a sentence in a docstring: a future step's bank
        import writes settles for money that moved before the schedule began, and
        a seam-level floor would refuse them.  Moving the bound onto the seam
        makes this test fail, which is what stops it drifting back there.
        """
        with app.app_context():
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            before_the_schedule = floor - timedelta(days=45)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settle_day=an_entered_day(before_the_schedule),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            assert txn.settled_on == before_the_schedule


class TestASettleDayNeedsARecord:
    """``ck_transactions_settle_day_needs_a_record``, said in words at the door.

    The constraint is the surviving half of a repealed biconditional: a row
    asserting the day its money moved must record WHAT moved, while a record
    with no day is the legal RETAINED state a revert leaves.

    **The guard exists because the constraint was the only thing saying it, and
    a CHECK cannot hold a conversation.**  The full-edit popover offers the
    settle-day box to an UNDATED settled row deliberately -- that row is the one
    that most needs to state the real day (finding **N-181**).  But a row from
    BEFORE the settlement record carries no record either, so stating the day
    alone violated the CHECK and surfaced as an ``IntegrityError`` rendered to
    the user as "invalid reference": a message naming nothing they could act on,
    for a save no amount of re-typing would have fixed.
    """

    def test_a_day_alone_on_a_recordless_settled_row_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The legacy shape: settled, recording nothing, asked for a day.

        Refused with the repair in the message, and the row untouched -- the
        guard is ordered with the seam's other pre-mutation refusals for exactly
        that reason.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods[0], status=StatusEnum.DONE)
            # The legacy shape, reproduced the only way it can be: straight at
            # the columns.  The seam refuses to CREATE one.
            record_settle_day(txn, None)
            txn.settled_amount = None
            txn.settled_basis_id = None
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                status_seam.apply_status_change(
                    txn, txn.status_id, settle_day=an_entered_day(display_today()),
                )

            assert "records nothing that moved" in str(exc.value)
            assert txn.settled_on is None, "a refused call wrote the day anyway"

    def test_the_same_day_lands_when_the_record_arrives_WITH_it(
        self, app, db, seed_user, seed_periods,
    ):
        """The firing control, and the repair the message names.

        Identical call plus a settlement: both halves of the assertion in one
        act, which is what the Actual box beside the day box makes expressible.
        Without this the test above would pass against a guard that refused
        every day.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods[0], status=StatusEnum.DONE)
            record_settle_day(txn, None)
            txn.settled_amount = None
            txn.settled_basis_id = None
            db.session.flush()

            status_seam.apply_status_change(
                txn, txn.status_id, settle_day=an_entered_day(display_today()),
                settlement=status_seam.Settlement(
                    amount=Decimal("50.00"),
                    basis=SettlementBasisEnum.CORRECTED,
                ),
            )
            db.session.flush()

            assert txn.settled_on == display_today()
            assert txn.settled_amount == Decimal("50.00")

    def test_an_ordinary_day_correction_is_untouched_by_the_guard(
        self, app, db, seed_user, seed_periods,
    ):
        """A row that ALREADY records something corrects its day as before.

        The second firing control, and the one that matters most: ruling
        **R-ED**'s door is the commonest path through this code, and a guard
        that caught it would break every settle-day correction in the app.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=status_seam.Settlement(
                    amount=Decimal("50.00"),
                    basis=SettlementBasisEnum.DERIVED,
                ),
            )
            db.session.flush()
            yesterday = display_today() - timedelta(days=1)

            status_seam.apply_status_change(
                txn, txn.status_id, settle_day=an_entered_day(yesterday),
            )

            assert txn.settled_on == yesterday
