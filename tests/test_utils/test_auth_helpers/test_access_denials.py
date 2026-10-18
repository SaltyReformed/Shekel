"""Auth helpers tests: the denial each ownership door logs (F-144), another owner's deleted row
probed at the transaction door, and require_owner failing closed.
"""

import logging
from datetime import date
from decimal import Decimal

import pytest
from flask_login import current_user, login_user

from app.extensions import db
from app.models.account import Account
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.utils.auth_helpers import (
    get_accessible_transaction,
    get_accessible_transaction_or_deleted,
    get_or_404,
    get_owned_via_parent,
    require_owner,
)
from app.utils.log_events import (
    ACCESS,
    EVT_ACCESS_DENIED_CROSS_USER,
    EVT_ACCESS_DENIED_OWNER_ONLY,
    EVT_RESOURCE_NOT_FOUND,
)
from tests._test_helpers import rhythm_of, one_off_row_of
from tests._test_helpers import record_paydays_across_a_hole
from tests._test_helpers import a_deleted_occurrence

from ._helpers import _AuthHelpersLogCapture


class TestAccessDeniedLogging:
    """Audit finding F-144 / commit C-14.

    The ownership helpers must emit structured events on every denial
    branch.  Pre-C-14 they returned ``None`` silently, so a probing
    companion or a cross-user attacker left no application-tier
    trace.
    """

    def test_get_or_404_emits_resource_not_found_for_missing_pk(
        self, app, db, seed_user,
    ):
        """A missing-PK denial emits ``resource_not_found`` at INFO level."""
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_or_404(Account, 999_999)

        assert result is None
        record = cap.find(EVT_RESOURCE_NOT_FOUND)
        assert record is not None, (
            "Missing-PK branch did not emit ``resource_not_found``; "
            f"records observed: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.INFO
        assert record.category == ACCESS
        assert record.user_id == seed_user["user"].id
        assert record.model == "Account"
        assert record.pk == 999_999
        assert record.path == "/some/path"

    def test_get_or_404_emits_cross_user_for_other_users_record(
        self, app, db, seed_user, second_user,
    ):
        """A cross-user denial emits ``access_denied_cross_user`` at WARNING level.

        This is the IDOR signal SOC tooling alerts on, so the level
        is WARNING (not INFO like the missing-PK case).
        """
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_or_404(Account, second_user["account"].id)

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None, (
            "Cross-user branch did not emit ``access_denied_cross_user``; "
            f"records observed: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.category == ACCESS
        assert record.user_id == seed_user["user"].id
        assert record.model == "Account"
        assert record.pk == second_user["account"].id
        assert record.owner_id == second_user["user"].id
        assert record.path == "/some/path"

    def test_get_or_404_silent_on_success(self, app, db, seed_user):
        """A successful load emits NO ACCESS event.

        Otherwise every page load would write multiple records and
        drown the failure signal.  Only the per-request summary in
        logging_config covers the success path.
        """
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_or_404(Account, seed_user["account"].id)

        assert result is not None
        access_records = [
            r for r in cap.records if getattr(r, "category", None) == ACCESS
        ]
        assert not access_records, (
            "Successful get_or_404 emitted ACCESS events; the helpers "
            "must stay silent on the happy path.  Records: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )

    def test_get_owned_via_parent_emits_resource_not_found_for_missing_pk(
        self, app, db, seed_user, seed_periods,
    ):
        """A missing-PK denial through the parent path emits ``resource_not_found``."""
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_owned_via_parent(Transaction, 999_999, "pay_period")

        assert result is None
        record = cap.find(EVT_RESOURCE_NOT_FOUND)
        assert record is not None
        assert record.levelno == logging.INFO
        assert record.model == "Transaction"
        assert record.pk == 999_999

    def test_get_owned_via_parent_emits_cross_user_for_other_users_child(
        self, app, db, seed_user, second_user, seed_periods,
    ):
        """A cross-user denial through the parent path emits ``access_denied_cross_user``."""
        # Build a transaction owned by the second user.
        from app.services import pay_period_write  # noqa: WPS433
        periods2 = record_paydays_across_a_hole(
            user_id=second_user["user"].id,
            first_payday=date(2026, 3, 1),
            num_periods=2,
            rhythm=rhythm_of(14),
        )
        db.session.flush()

        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense"
        ).one()
        txn2 = one_off_row_of(
            periods2[0],
            name="Other User Expense",
            amount=Decimal("99.00"),
            user_id=periods2[0].user_id,
            account_id=second_user["account"].id,
            scenario_id=second_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=second_user["categories"]["Rent"].id,
        )
        db.session.flush()

        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_owned_via_parent(
                    Transaction, txn2.id, "pay_period",
                )

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None
        assert record.levelno == logging.WARNING
        assert record.user_id == seed_user["user"].id
        assert record.owner_id == second_user["user"].id
        assert record.parent_attr == "pay_period"

    def test_require_owner_emits_access_denied_for_companion(
        self, app, db, seed_user,
    ):
        """A non-owner hitting an owner-only route emits ``access_denied_owner_only``.

        The decorator must abort(404) AFTER the log_event call so the
        forensic event lands even though the response shape mirrors
        a true 404.
        """
        from app import ref_cache  # noqa: WPS433
        from app.enums import RoleEnum  # noqa: WPS433

        # Build a companion user attached to the seed user.
        from app.models.user import User  # noqa: WPS433
        companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
        companion = User(
            email="companion-access-test@example.com",
            password_hash="$2b$12$dummyhashfortestpurposesonlyx",
            display_name="Test Companion",
            role_id=companion_role_id,
            linked_owner_id=seed_user["user"].id,
        )
        db.session.add(companion)
        db.session.flush()

        # Define a fake decorated view in isolation so the test does
        # not exercise an unrelated production route.
        @require_owner
        def owner_only_view():
            """Stub view that should never run for a companion."""
            return "should-not-reach"

        with app.test_request_context("/owner-only", method="POST"):
            login_user(companion)
            with _AuthHelpersLogCapture() as cap:
                # require_owner aborts via abort(404) -- exercise that.
                from werkzeug.exceptions import NotFound  # noqa: WPS433
                with pytest.raises(NotFound):
                    owner_only_view()

        record = cap.find(EVT_ACCESS_DENIED_OWNER_ONLY)
        assert record is not None, (
            "Companion access did not emit ``access_denied_owner_only``; "
            f"records observed: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.category == ACCESS
        assert record.user_id == companion.id
        assert record.role_id == companion_role_id
        assert record.path == "/owner-only"
        assert record.method == "POST"

    def test_require_owner_silent_for_owner(self, app, db, seed_user):
        """An owner hitting an owner-only route emits NO ACCESS event."""
        @require_owner
        def owner_only_view():
            """Stub view returning a sentinel string."""
            return "owner-ok"

        with app.test_request_context("/owner-only"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = owner_only_view()

        assert result == "owner-ok"
        access_records = [
            r for r in cap.records if getattr(r, "category", None) == ACCESS
        ]
        assert not access_records, (
            "Successful require_owner emitted ACCESS events; the "
            "decorator must stay silent on the happy path."
        )

    # ── get_accessible_transaction F-144 logging (deep-hunt #85) ──────

    @staticmethod
    def _make_owned_txn(db, owner, period):
        """Create a one-off, non-companion-visible expense owned by *owner*.

        A rule-less definition's placed row (plan step balance:X-bi-7c), so
        ``visible_to_companion`` reads the DEFINITION's ``companion_visible``,
        which the producer leaves False -- a companion of the owner is
        therefore denied.
        """
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        txn = one_off_row_of(
            period,
            name="Access-log test expense",
            amount=Decimal("50.00"),
            user_id=period.user_id,
            account_id=owner["account"].id,
            scenario_id=owner["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=next(iter(owner["categories"].values())).id,
        )
        db.session.flush()
        return txn

    def test_get_accessible_transaction_emits_resource_not_found(
        self, app, db, seed_user,
    ):
        """A missing-PK denial emits ``resource_not_found`` at INFO (deep-hunt #85)."""
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(999_999)

        assert result is None
        record = cap.find(EVT_RESOURCE_NOT_FOUND)
        assert record is not None, (
            "Missing-PK branch did not emit ``resource_not_found``; "
            f"records: {[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.INFO
        assert record.category == ACCESS
        assert record.user_id == seed_user["user"].id
        assert record.model == "Transaction"
        assert record.pk == 999_999
        assert record.path == "/some/path"

    def test_get_accessible_transaction_emits_cross_user_for_other_owner(
        self, app, db, seed_user, second_user,
    ):
        """Owner A reaching owner B's transaction emits ``access_denied_cross_user``.

        deep-hunt #85: the owner-path denial was silent before.  WARNING
        level matches the sibling get_or_404 cross-user event.
        """
        from app.services import pay_period_write  # noqa: WPS433
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            periods2 = record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=date(2026, 3, 1),
                num_periods=2,
                rhythm=rhythm_of(14),
            )
            db.session.flush()
            txn = self._make_owned_txn(db, second_user, periods2[0])

            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(txn.id)

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None, (
            "Owner cross-user branch did not emit ``access_denied_cross_user``; "
            f"records: {[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.category == ACCESS
        assert record.user_id == seed_user["user"].id
        assert record.model == "Transaction"
        assert record.pk == txn.id
        assert record.owner_id == second_user["user"].id
        assert record.path == "/some/path"

    def test_get_accessible_transaction_emits_cross_user_for_companion_denied(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """A companion denied by visibility emits ``access_denied_cross_user``.

        deep-hunt #85 headline: the companion-visibility denial was the
        one ownership-failure branch in auth_helpers that stayed silent.
        The companion is linked to seed_user (owner_id == linked_owner_id),
        so this isolates the ``not visible_to_companion`` denial.
        """
        with app.test_request_context("/some/path"):
            # Ad-hoc txn owned by seed_user -> companion_visible False.
            txn = self._make_owned_txn(db, seed_user, seed_periods[0])
            assert txn.visible_to_companion is False
            login_user(seed_companion["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(txn.id)

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None, (
            "Companion-visibility branch did not emit "
            "``access_denied_cross_user``; "
            f"records: {[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.category == ACCESS
        assert record.user_id == seed_companion["user"].id
        assert record.model == "Transaction"
        assert record.pk == txn.id
        # owner_id is the txn's actual owner (== the companion's linked owner).
        assert record.owner_id == seed_user["user"].id
        assert record.path == "/some/path"

    def test_get_accessible_transaction_silent_on_success(
        self, app, db, seed_user, seed_periods,
    ):
        """A successful owner load emits NO ACCESS event (deep-hunt #85)."""
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            txn = self._make_owned_txn(db, seed_user, seed_periods[0])
            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(txn.id)

        assert result is not None
        assert result.id == txn.id
        access_records = [
            r for r in cap.records if getattr(r, "category", None) == ACCESS
        ]
        assert not access_records, (
            "Successful get_accessible_transaction emitted ACCESS events; "
            "the helper must stay silent on the happy path.  Records: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )

    def test_get_accessible_transaction_denies_an_anonymous_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """No session: the door answers ``None`` and logs the probe, never raising.

        Plan step ``balance:X-bn`` reads both access branches off ONE
        ``User.data_owner_id``, through a ``getattr`` whose only purpose is
        this principal: Flask-Login's ``AnonymousUserMixin`` has no such
        attribute and reads ``None``, and ``transactions.user_id`` is NOT
        NULL, so no row's owner equals it.  Reachable only where the login
        gate is off, which is where a second layer must hold.
        ``current_user`` is asserted anonymous BEFORE the call, for the reason
        :class:`TestRequireOwnerFailsClosed` gives: a login earlier in the
        same app context would leave the case measuring an owner.
        """
        with app.test_request_context("/some/path"):
            txn = self._make_owned_txn(db, seed_user, seed_periods[0])
            assert not current_user.is_authenticated
            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(txn.id)

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None, (
            "An anonymous principal's denial did not emit "
            "``access_denied_cross_user``; "
            f"records: {[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.user_id is None
        assert record.pk == txn.id
        assert record.owner_id == seed_user["user"].id


# ``TestATransferShadowRowAtTheTransactionDoor`` stood here until plan step
# balance:X-bi-6-4d-3 (ruling **R-BAL300**, developer 2026-10-15: "Delete all
# 27").  Its two cases pinned WHERE plan step X-bi-6-1's shadow fence
# (``is_transfer_shadow``) sits in ``get_accessible_transaction``: after both
# access branches, so the owner's own shadow logged ``resource_not_found`` at
# INFO with its transfer named, and a stranger's probe of one logged
# ``access_denied_cross_user`` at WARNING, first.  No shadow can exist now
# (the database refuses one, ``ck_transactions_names_no_transfer``, ruling
# R-BAL258, pinned once by
# ``TestTheUpgrade::test_the_database_refuses_a_twin`` in
# ``tests/test_models/test_a_transfer_is_one_row.py``,
# and no app code builds one), so the fence is reachable by no row and goes
# with the column at plan step X-bi-6-5.  The order its stranger's case graded
# is graded below, on the row the shared predicate can still refuse: a DELETED
# one, probed by a stranger and by a companion.


class TestAProbeOfAnotherOwnersDeletedRowIsADenial:
    """Another owner's DELETED row, probed by a stranger or a companion: a denial, not "not found".

    ``get_accessible_transaction_or_deleted`` asks the not-found predicate
    (``is_not_found_to_transaction_doors``) AFTER both access branches, and its
    comment says why: either probe is an access denial first (``access_denied_cross_user``
    at WARNING, the F-144 contract) and "not found" second, so the predicate never runs
    and logs nothing.  Measured at plan step ``balance:X-bi-6-4d-3`` (ruling **R-BAL300**):
    evaluating the predicate ahead of the access branches, every return unchanged, logged
    the row as ``resource_not_found`` before the denial and failed no test that captures a
    log, for either requester (for the companion, at the deletion's parent too).  These two
    cases are that grade.  Moving the whole block ahead also hands the requester the row's
    NAME: both cases fail on that too, and ``TestNothingLeaksThroughTheName``
    (``tests/test_routes/test_cc5_4a4_hidden_row_doors.py``) grades the name at every
    surface.
    """

    @staticmethod
    def _probe(requester, row):
        """*requester* asks the door for *row*: ``(its answer, the auth_helpers records)``."""
        login_user(requester)
        with _AuthHelpersLogCapture() as cap:
            answer = get_accessible_transaction_or_deleted(row.id)
        return answer, cap

    @staticmethod
    def _is_the_denial_alone(answer, cap):
        """Assert the door answered None, logged ONE WARNING denial and no "not found"."""
        seen = [(r.levelname, getattr(r, "event", None)) for r in cap.records]
        assert answer is None, (
            f"The requester was answered {answer!r}, not None; records: {seen}"
        )
        denials = [
            r for r in cap.records
            if getattr(r, "event", None) == EVT_ACCESS_DENIED_CROSS_USER
        ]
        assert [r.levelno for r in denials] == [logging.WARNING], (
            f"Expected exactly one WARNING access denial; records: {seen}"
        )
        assert cap.find(EVT_RESOURCE_NOT_FOUND) is None, (
            f"The requester's probe logged the row as not found; records: {seen}"
        )

    def test_a_strangers_probe(self, app, seed_user, seed_second_user):
        """seed_user names another user's deleted $45.00 Pool: one WARNING, no "not found"."""
        with app.test_request_context("/some/path"):
            theirs = a_deleted_occurrence(
                db.session, seed_second_user, seed_second_user["bootstrap_period"],
                amount="45.00", name="Pool",
            )
            # Precondition, read back after the builder's commit: a DELETED
            # row of another user's, so the probe below is a stranger's.
            assert theirs.is_deleted is True
            assert theirs.user_id == seed_second_user["user"].id
            assert theirs.user_id != seed_user["user"].id
            answer, cap = self._probe(seed_user["user"], theirs)

        self._is_the_denial_alone(answer, cap)

    def test_a_companions_probe(self, app, seed_user, seed_companion):
        """The owner's deleted $70.00 Massage, never shown to the companion: the same denial."""
        with app.test_request_context("/some/path"):
            row = a_deleted_occurrence(
                db.session, seed_user, seed_user["bootstrap_period"],
                amount="70.00", name="Massage", companion_visible=False,
            )
            companion = seed_companion["user"]
            # Precondition, read back after the builder's commit: the requester
            # is the owner's COMPANION (not the owner, yet acting on the owner's
            # data, ``User.data_owner_id``), and the row is hidden from it and DELETED.
            assert companion.id != row.user_id
            assert companion.data_owner_id == row.user_id == seed_user["user"].id
            assert row.visible_to_companion is False
            assert row.is_deleted is True
            answer, cap = self._probe(companion, row)

        self._is_the_denial_alone(answer, cap)


class TestRequireOwnerFailsClosed:
    """Plan step ``bank_import:X-gs`` (ledger row **BI-486**).

    ``require_owner`` read ``getattr(current_user, "role_id", owner_id)``,
    so a principal with no ``role_id`` at all -- Flask-Login's
    ``AnonymousUserMixin`` -- was defaulted to the OWNER's id and ran the
    view.  Unreachable behind the login gate, which bounces every
    anonymous request first; reachable exactly where the gate is off
    (``LOGIN_DISABLED``, or an app built without it), which is where a
    second layer is supposed to hold.  The read is outright now, and this
    case is the one the owner-id default admitted.
    """

    def test_an_anonymous_principal_never_reaches_the_view(self, app):
        """No session, so no ``role_id``: the wrapped view does not run.

        A direct call on a stub view, so the case grades the decorator and
        not the gate: through the test client the gate answers first and the
        decorator is never consulted.  ``current_user`` is asserted anonymous
        BEFORE the call because Flask-Login caches the loaded user on ``g``
        for the life of the app context the autouse ``db`` fixture holds; a login
        earlier in the same context would leave this case measuring an owner
        and passing for the wrong reason.
        """
        reached = []

        @require_owner
        def owner_only_view():
            """Stub view that must never run without a session."""
            reached.append(True)
            return "should-not-reach"

        with app.test_request_context("/owner-only"):
            assert not current_user.is_authenticated
            with pytest.raises(AttributeError, match="role_id"):
                owner_only_view()

        assert not reached
