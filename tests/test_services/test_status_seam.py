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

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam
from app.utils.dates import display_today

from tests._test_helpers import freeze_today

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
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=ref_cache.status_id(status),
        name="Seam test expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=Decimal("50.00"),
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
                txn, ref_cache.status_id(StatusEnum.DONE), settled_on=explicit,
            )
            db.session.commit()

            db.session.refresh(txn)
            assert txn.settled_on == explicit

    def test_an_instant_is_refused_not_truncated(
        self, app, db, seed_user, seed_periods,
    ):
        """A ``datetime`` raises ``TypeError`` before the row is touched.

        Finding **N-179**, and it is a refusal rather than a conversion because
        the conversion is the defect: ``datetime`` subclasses ``date``, so the
        annotation catches nothing, and PostgreSQL coerces the value into the
        ``DATE`` column on the SESSION clock (UTC) -- filing an evening-Eastern
        settle on the following day, silently.  The instant below is
        2026-03-03 23:30 Eastern, which UTC calls 2026-03-04.
        """
        with app.app_context():
            txn = _make_txn(
                seed_user, seed_periods[0], status=StatusEnum.PROJECTED,
            )
            done_id = ref_cache.status_id(StatusEnum.DONE)
            instant = datetime(2026, 3, 4, 4, 30, tzinfo=timezone.utc)

            with pytest.raises(TypeError) as exc:
                status_seam.apply_status_change(
                    txn, done_id, settled_on=instant,
                )
            assert "must be a date" in str(exc.value)
            # Refused BEFORE any mutation: the check runs ahead of the
            # transition gate, so a rejected call leaves the row untouched.
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
                    txn, projected_id, settled_on=date(2026, 3, 15),
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
                txn, done_id, settled_on=settled_a_month_ago,
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
            status_seam.apply_status_change(txn, done_id)
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
            )
            db.session.commit()
            db.session.refresh(txn)
            assert txn.settled_on is not None

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
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
            status_seam.apply_status_change(txn, done_id)

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
            )
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

            db.session.rollback()
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert reloaded.settled_on is None
