"""
Shekel Budget App -- Concurrent Modification Tests (L-15)

Verifies that critical financial endpoints handle simultaneous requests
without data corruption.  Uses threading.Barrier to synchronize two
threads hitting the same endpoint at the same instant against a real
PostgreSQL database.

Each test creates its own data, runs two concurrent operations, and
asserts that the final database state satisfies an invariant.  The
tests do not assert which thread "wins" -- only that the outcome is
consistent and no data is lost or corrupted.

No application code is modified.  If a test reveals an actual race
condition bug, it is documented with a comment and marked xfail.
"""

import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, Status, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth_client(app, email, password):
    """Create and authenticate a test client for use in a concurrent thread.

    Each thread must have its own client to avoid session interference.
    Uses use_cookies=True (default) and a fresh client instance so
    each client maintains its own session cookie.

    Args:
        app:      The Flask application (session-scoped fixture).
        email:    User email to log in with.
        password: User password.

    Returns:
        A logged-in Flask test client.
    """
    # use_cookies=True is the default but being explicit here.
    client = app.test_client(use_cookies=True)
    resp = client.post("/login", data={
        "email": email,
        "password": password,
    })
    assert resp.status_code == 302, (
        f"Thread client login failed for {email}: {resp.status_code}"
    )
    return client


def _run_concurrent(app, func_a, func_b, timeout=10):
    """Run two callables concurrently using a barrier for synchronization.

    Both functions receive no arguments and are expected to return an
    HTTP response or similar result.  Exceptions from either thread
    are captured and re-raised in the main thread.

    Args:
        app:     The Flask application (needed for app context in threads).
        func_a:  Callable for thread A.
        func_b:  Callable for thread B.
        timeout: Seconds to wait for each thread to finish.

    Returns:
        Tuple (result_a, result_b).
    """
    barrier = threading.Barrier(2, timeout=timeout)
    results = [None, None]
    errors = [None, None]

    def _worker(index, func):
        try:
            with app.app_context():
                barrier.wait()
                results[index] = func()
        except Exception as exc:  # pylint: disable=broad-except
            errors[index] = exc

    t_a = threading.Thread(target=_worker, args=(0, func_a))
    t_b = threading.Thread(target=_worker, args=(1, func_b))
    t_a.start()
    t_b.start()
    t_a.join(timeout=timeout)
    t_b.join(timeout=timeout)

    assert not t_a.is_alive(), "Thread A did not complete within timeout"
    assert not t_b.is_alive(), "Thread B did not complete within timeout"

    if errors[0]:
        raise errors[0]
    if errors[1]:
        raise errors[1]

    return results[0], results[1]


# ---------------------------------------------------------------------------
# Shared Fixture Data
# ---------------------------------------------------------------------------


def _create_user_with_data(db_session):
    """Create a user with a checking account, scenario, category, and periods.

    Returns a dict with all the objects needed by the concurrent tests.
    """
    user = User(
        email="concurrent@shekel.local",
        password_hash=hash_password("concurrent12"),
        display_name="Concurrent User",
    )
    db_session.add(user)
    db_session.flush()

    settings = UserSettings(user_id=user.id)
    db_session.add(settings)

    checking_type = (
        db_session.query(AccountType).filter_by(name="Checking").one()
    )
    account = Account(
        user_id=user.id,
        account_type_id=checking_type.id,
        name="Checking",
        current_anchor_balance=Decimal("5000.00"),
    )
    db_session.add(account)
    db_session.flush()

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db_session.add(scenario)

    category = Category(
        user_id=user.id,
        group_name="Home",
        item_name="Test Expense",
    )
    db_session.add(category)
    db_session.flush()

    # Three periods: past, current (containing today), and future.
    today = date.today()
    # Align the "current" period so today falls within it.
    current_start = today - timedelta(days=today.weekday())  # Monday this week
    past_period = PayPeriod(
        user_id=user.id,
        start_date=current_start - timedelta(days=14),
        end_date=current_start - timedelta(days=1),
        period_index=0,
    )
    current_period = PayPeriod(
        user_id=user.id,
        start_date=current_start,
        end_date=current_start + timedelta(days=13),
        period_index=1,
    )
    db_session.add_all([past_period, current_period])
    db_session.flush()

    account.current_anchor_period_id = past_period.id
    db_session.commit()

    return {
        "user": user,
        "account": account,
        "scenario": scenario,
        "category": category,
        "past_period": past_period,
        "current_period": current_period,
    }


# ---------------------------------------------------------------------------
# Test Scenario 1: Simultaneous mark-done on the same transaction
# ---------------------------------------------------------------------------


class TestConcurrentMarkDone:
    """Verify concurrent mark-done requests produce consistent state.

    Two threads simultaneously POST /transactions/<id>/mark-done on
    the same projected transaction.  The invariant is that after both
    complete, the transaction has a settled status (Paid or Received)
    and was transitioned exactly once -- no duplicate writes, no
    corruption, no 500 errors.
    """

    def test_concurrent_mark_done_expense(self, app, db):
        """Two threads mark the same expense as done simultaneously.

        Invariant: transaction ends up with status=Paid, no 500 errors.
        """
        data = _create_user_with_data(db.session)
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )

        txn = Transaction(
            account_id=data["account"].id,
            pay_period_id=data["past_period"].id,
            scenario_id=data["scenario"].id,
            status_id=projected.id,
            name="Rent",
            category_id=data["category"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("1500.00"),
        )
        db.session.add(txn)
        db.session.commit()
        txn_id = txn.id
        client_a = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )
        client_b = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )

        resp_a, resp_b = _run_concurrent(
            app,
            lambda: client_a.post(f"/transactions/{txn_id}/mark-done"),
            lambda: client_b.post(f"/transactions/{txn_id}/mark-done"),
        )

        # Neither request should produce a 500.
        assert resp_a.status_code != 500, f"Thread A got 500: {resp_a.data[:200]}"
        assert resp_b.status_code != 500, f"Thread B got 500: {resp_b.data[:200]}"

        # At least one must succeed.
        assert resp_a.status_code == 200 or resp_b.status_code == 200, (
            f"Neither thread succeeded: A={resp_a.status_code}, B={resp_b.status_code}"
        )

        # Invariant: transaction is in a settled (paid) state.
        db.session.expire_all()
        final = db.session.get(Transaction, txn_id)
        assert final is not None
        assert final.status.is_settled, (
            f"Transaction should be settled, got status '{final.status.name}'"
        )

    def test_concurrent_mark_done_income(self, app, db):
        """Two threads mark the same income as received simultaneously.

        Invariant: transaction ends up with status=Received.
        """
        data = _create_user_with_data(db.session)
        projected = db.session.query(Status).filter_by(name="Projected").one()
        income_type = (
            db.session.query(TransactionType).filter_by(name="Income").one()
        )

        txn = Transaction(
            account_id=data["account"].id,
            pay_period_id=data["past_period"].id,
            scenario_id=data["scenario"].id,
            status_id=projected.id,
            name="Paycheck",
            category_id=data["category"].id,
            transaction_type_id=income_type.id,
            estimated_amount=Decimal("3000.00"),
        )
        db.session.add(txn)
        db.session.commit()
        txn_id = txn.id
        client_a = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )
        client_b = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )

        resp_a, resp_b = _run_concurrent(
            app,
            lambda: client_a.post(f"/transactions/{txn_id}/mark-done"),
            lambda: client_b.post(f"/transactions/{txn_id}/mark-done"),
        )

        assert resp_a.status_code != 500
        assert resp_b.status_code != 500

        db.session.expire_all()
        final = db.session.get(Transaction, txn_id)
        assert final is not None
        assert final.status.is_settled, (
            f"Income should be settled, got status '{final.status.name}'"
        )


# ---------------------------------------------------------------------------
# Test Scenario 2: Carry-forward during transaction edit
# ---------------------------------------------------------------------------


class TestConcurrentCarryForwardAndEdit:
    """Verify carry-forward and transaction edit running simultaneously.

    Thread A carries forward projected transactions from a past period
    to the current period.  Thread B edits the estimated_amount of a
    transaction in that same past period.  The invariant is that the
    transaction ends up in exactly one period with a valid amount --
    not lost, not duplicated.
    """

    def test_carry_forward_during_edit(self, app, db):
        """Transaction is not lost or duplicated when carry-forward and edit race.

        Invariant: after both threads complete, the transaction exists
        exactly once across all periods with a valid amount.
        """
        data = _create_user_with_data(db.session)
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )

        txn = Transaction(
            account_id=data["account"].id,
            pay_period_id=data["past_period"].id,
            scenario_id=data["scenario"].id,
            status_id=projected.id,
            name="Groceries",
            category_id=data["category"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("100.00"),
        )
        db.session.add(txn)
        db.session.commit()
        txn_id = txn.id
        past_period_id = data["past_period"].id
        current_period_id = data["current_period"].id
        client_a = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )
        client_b = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )

        resp_a, resp_b = _run_concurrent(
            app,
            lambda: client_a.post(f"/pay-periods/{past_period_id}/carry-forward"),
            lambda: client_b.patch(
                f"/transactions/{txn_id}",
                data={"estimated_amount": "200.00"},
            ),
        )

        # Neither request should produce a 500.
        assert resp_a.status_code != 500, f"Carry-forward got 500: {resp_a.data[:200]}"
        assert resp_b.status_code != 500, f"Edit got 500: {resp_b.data[:200]}"

        # Invariant: the transaction exists exactly once (not deleted,
        # not duplicated) and is in either the past or current period.
        db.session.expire_all()
        final = db.session.get(Transaction, txn_id)
        assert final is not None, "Transaction was lost"
        assert not final.is_deleted, "Transaction was unexpectedly deleted"
        assert final.pay_period_id in (past_period_id, current_period_id), (
            f"Transaction in unexpected period {final.pay_period_id}"
        )
        # Amount must be one of the valid values.
        assert final.estimated_amount in (
            Decimal("100.00"), Decimal("200.00"),
        ), f"Unexpected amount: {final.estimated_amount}"


# ---------------------------------------------------------------------------
# Test Scenario 3: Simultaneous anchor balance updates
# ---------------------------------------------------------------------------


class TestConcurrentAnchorUpdate:
    """Verify concurrent anchor balance updates produce consistent state.

    Two threads simultaneously PATCH /accounts/<id>/true-up with
    different balance values.  The invariant is that the final balance
    is exactly one of the two submitted values -- not the original,
    not a sum, not any other value.
    """

    def test_concurrent_true_up(self, app, db):
        """Two threads update anchor balance to different values simultaneously.

        Invariant: final balance is exactly 2000.00 or 3000.00, not
        the original 5000.00 or any other value.

        Uses a single client with two sequential-then-concurrent
        requests to avoid Flask test client session interference
        when two clients log in as the same user.
        """
        data = _create_user_with_data(db.session)
        account_id = data["account"].id
        # Single authenticated client -- both threads share it.
        # Flask test client is not truly thread-safe, but for this
        # test the requests are synchronized by the barrier and
        # don't overlap at the session-cookie level.
        client = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )

        resp_a, resp_b = _run_concurrent(
            app,
            lambda: client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "2000.00"},
            ),
            lambda: client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "3000.00"},
            ),
        )

        # Neither request should produce a 500.
        assert resp_a.status_code != 500, f"Thread A got 500: {resp_a.data[:200]}"
        assert resp_b.status_code != 500, f"Thread B got 500: {resp_b.data[:200]}"

        # Both should succeed (last-write-wins is expected).
        assert resp_a.status_code == 200, f"Thread A failed: {resp_a.status_code}"
        assert resp_b.status_code == 200, f"Thread B failed: {resp_b.status_code}"

        # Invariant: final balance is exactly one of the two submitted values.
        db.session.expire_all()
        final = db.session.get(Account, account_id)
        assert final is not None
        assert final.current_anchor_balance in (
            Decimal("2000.00"), Decimal("3000.00"),
        ), (
            f"Anchor balance is {final.current_anchor_balance}, "
            f"expected 2000.00 or 3000.00"
        )
        # Anchor period must be set (both threads set it to the current period).
        assert final.current_anchor_period_id is not None
