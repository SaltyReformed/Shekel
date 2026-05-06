"""Tests for commit C-18: stale-form prevention across every PATCH endpoint.

Mirrors the structure of the C-17 tests in test_accounts.py: schema
invariants, lifecycle behaviour, true concurrent races, and per-route
stale-form / StaleDataError handling.  Every model that grew a
``version_id`` column in commit C-18 is exercised against the same
contract:

  1. The live database carries a NOT NULL ``version_id`` column with
     a positive CHECK constraint.
  2. The mapper declares ``version_id_col`` so SQLAlchemy narrows
     UPDATE/DELETE statements with ``WHERE version_id = ?``.
  3. Pure SELECT operations leave ``version_id`` unchanged; UPDATE
     bumps it by exactly one.
  4. A truly concurrent commit that bumps the row out from under
     the test session raises :class:`StaleDataError`.
  5. PATCH/POST endpoints that ship the row's ``version_id`` reject
     stale submissions.  HTMX endpoints render a 409 conflict cell;
     full-page form endpoints flash + redirect.
  6. Edit-form templates ship a hidden ``version_id`` input set to
     the current row's counter so the round-trip closes.

Together, these tests are the load-bearing automated check that
the optimistic-lock contract holds end-to-end.  Audit reference:
F-010 (High) / commit C-18 of the 2026-04-15 security remediation
plan.
"""

# pylint: disable=too-many-lines

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.account import Account
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import (
    AccountType, CalcMethod, DeductionTiming, FilingStatus,
    RaiseType, Status, TransactionType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate


# ── Helpers ─────────────────────────────────────────────────────────


def _bump_version_outside_session(schema, table, row_id):
    """Bump ``version_id`` on a table row from a fresh DB connection.

    Simulates a concurrent commit by another browser tab.  Uses a
    connection independent of the test session so the calling
    session's identity map keeps the stale value while the database
    row carries the bumped value.

    The connection commit is essential: without it the UPDATE would
    sit in an open transaction and READ COMMITTED MVCC would hide
    the bump from the test session.
    """
    with db.engine.connect() as conn:
        conn.execute(
            text(
                f"UPDATE {schema}.{table} "
                "SET version_id = version_id + 1 "
                "WHERE id = :id"
            ),
            {"id": row_id},
        )
        conn.commit()


def _read_version(model, row_id):
    """Refetch a row and return its ``version_id`` after expiring the session."""
    db.session.expire_all()
    obj = db.session.get(model, row_id)
    return obj.version_id if obj is not None else None


# ── Test data factories (kept tight so each test reads as a story) ─


def _make_template(user_id, account_id, category_id):
    """Insert a TransactionTemplate and return it with id populated."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    template = TransactionTemplate(
        user_id=user_id,
        account_id=account_id,
        category_id=category_id,
        transaction_type_id=expense_type.id,
        name="Optimistic-Lock Test",
        default_amount=Decimal("100.00"),
        is_active=True,
    )
    db.session.add(template)
    db.session.commit()
    return template


def _make_transaction(seed_user, period):
    """Insert a Transaction in the given pay period and return it."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    cat = seed_user["categories"]["Groceries"]
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=projected.id,
        category_id=cat.id,
        transaction_type_id=expense_type.id,
        name="Test Txn",
        estimated_amount=Decimal("50.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _make_envelope_template_and_txn(seed_user, period):
    """Insert a tracked (envelope) template + transaction and return both."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    cat = seed_user["categories"]["Groceries"]
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        transaction_type_id=expense_type.id,
        name="Tracked Groceries",
        default_amount=Decimal("400.00"),
        is_active=True,
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
        status_id=projected.id,
        category_id=cat.id,
        transaction_type_id=expense_type.id,
        name="Tracked Groceries",
        estimated_amount=Decimal("400.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return template, txn


def _make_entry(txn_id, user_id):
    """Insert a TransactionEntry on the given transaction."""
    entry = TransactionEntry(
        transaction_id=txn_id,
        user_id=user_id,
        amount=Decimal("25.00"),
        description="Kroger",
        entry_date=date.today(),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def _make_savings_account(user_id):
    """Insert a savings Account that goals can reference."""
    savings_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    acct = Account(
        user_id=user_id,
        account_type_id=savings_type.id,
        name="Optimistic-Lock Savings",
        current_anchor_balance=Decimal("0.00"),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _make_savings_goal(user_id, account_id):
    """Insert a fixed-mode savings goal."""
    goal = SavingsGoal(
        user_id=user_id,
        account_id=account_id,
        name="Emergency Fund",
        target_amount=Decimal("10000.00"),
    )
    db.session.add(goal)
    db.session.commit()
    return goal


def _make_salary_profile(user_id, scenario_id):
    """Insert a SalaryProfile and return it (no template / linked txns)."""
    filing_status = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing_status.id,
        name="Day Job",
        annual_salary=Decimal("60000.00"),
        state_code="NC",
        pay_periods_per_year=26,
    )
    db.session.add(profile)
    db.session.commit()
    return profile


def _make_salary_raise(profile_id):
    """Insert a SalaryRaise (percentage-based, recurring)."""
    raise_type = (
        db.session.query(RaiseType).filter_by(name="merit").one()
    )
    sraise = SalaryRaise(
        salary_profile_id=profile_id,
        raise_type_id=raise_type.id,
        effective_month=6,
        effective_year=2027,
        percentage=Decimal("0.03"),
    )
    db.session.add(sraise)
    db.session.commit()
    return sraise


def _make_paycheck_deduction(profile_id):
    """Insert a PaycheckDeduction (fixed amount, pre-tax)."""
    timing = (
        db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
    )
    method = (
        db.session.query(CalcMethod).filter_by(name="flat").one()
    )
    ded = PaycheckDeduction(
        salary_profile_id=profile_id,
        deduction_timing_id=timing.id,
        calc_method_id=method.id,
        name="Health Insurance",
        amount=Decimal("100.00"),
        deductions_per_year=26,
    )
    db.session.add(ded)
    db.session.commit()
    return ded


def _make_transfer(seed_user, period):
    """Insert a transfer (and its two shadows) via the service."""
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel

    savings = _make_savings_account(seed_user["user"].id)
    projected = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    cat = seed_user["categories"]["Groceries"]
    xfer = transfer_service.create_transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount=Decimal("250.00"),
        status_id=projected.id,
        category_id=cat.id,
        name="Test Xfer",
    )
    db.session.commit()
    return xfer


def _make_transfer_template(user_id, from_acct_id, to_acct_id, category_id):
    """Insert a TransferTemplate."""
    tmpl = TransferTemplate(
        user_id=user_id,
        from_account_id=from_acct_id,
        to_account_id=to_acct_id,
        category_id=category_id,
        name="Monthly Savings",
        default_amount=Decimal("500.00"),
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.commit()
    return tmpl


# ═════════════════════════════════════════════════════════════════════
# Schema invariants
# ═════════════════════════════════════════════════════════════════════


# Each entry: (schema, table, model, check_constraint_name)
_VERSIONED_ROWS = [
    ("budget", "transactions", Transaction, "ck_transactions_version_id_positive"),
    ("budget", "transfers", Transfer, "ck_transfers_version_id_positive"),
    (
        "budget", "transaction_templates", TransactionTemplate,
        "ck_transaction_templates_version_id_positive",
    ),
    (
        "budget", "transfer_templates", TransferTemplate,
        "ck_transfer_templates_version_id_positive",
    ),
    ("budget", "savings_goals", SavingsGoal, "ck_savings_goals_version_id_positive"),
    (
        "budget", "transaction_entries", TransactionEntry,
        "ck_transaction_entries_version_id_positive",
    ),
    ("salary", "salary_profiles", SalaryProfile, "ck_salary_profiles_version_id_positive"),
    ("salary", "salary_raises", SalaryRaise, "ck_salary_raises_version_id_positive"),
    (
        "salary", "paycheck_deductions", PaycheckDeduction,
        "ck_paycheck_deductions_version_id_positive",
    ),
]


@pytest.mark.parametrize("schema, table, _model, _check", _VERSIONED_ROWS)
def test_version_id_column_present_and_not_null(app, schema, table, _model, _check):
    """Every versioned table carries a NOT NULL ``version_id``.

    A NULL counter would silently disable the optimistic lock on
    that row (``WHERE version_id IS NULL`` does not match the
    SQLAlchemy-emitted comparison), so this assertion is the
    minimal regression guard against a future migration drift.
    """
    with app.app_context():
        insp = inspect(db.engine)
        cols = {
            c["name"]: c
            for c in insp.get_columns(table, schema=schema)
        }

        assert "version_id" in cols, (
            f"{schema}.{table}.version_id column missing -- migration "
            f"a6c122211261 may not have run."
        )
        assert cols["version_id"]["nullable"] is False, (
            f"{schema}.{table}.version_id must be NOT NULL or the "
            f"optimistic lock silently fails on NULL counters."
        )


@pytest.mark.parametrize("schema, table, _model, check_name", _VERSIONED_ROWS)
def test_version_id_check_constraint_present(app, schema, table, _model, check_name):
    """Every versioned table has a ``version_id > 0`` CHECK.

    The CHECK is the database-tier guard against a future raw-SQL
    path that writes 0 or a negative value.  Its presence is
    asserted independently of the model declaration so a future
    edit that drops the constraint without removing it from the
    matching ``__table_args__`` block does not silently pass.
    """
    with app.app_context():
        insp = inspect(db.engine)
        checks = {
            c["name"]: c["sqltext"]
            for c in insp.get_check_constraints(table, schema=schema)
        }
        assert check_name in checks, (
            f"{check_name} missing from {schema}.{table} -- the schema "
            f"no longer matches the model declaration."
        )
        normalised = checks[check_name].lower().replace(" ", "")
        assert "version_id>0" in normalised, (
            f"CHECK constraint expression on {check_name} has changed; "
            f"rerun the migration or update the model in lockstep."
        )


@pytest.mark.parametrize("_schema, _table, model, _check", _VERSIONED_ROWS)
def test_mapper_declares_version_id_col(app, _schema, _table, model, _check):
    """Every versioned model exposes ``version_id_col`` on its mapper.

    Without this declaration SQLAlchemy emits ``UPDATE`` without
    the ``WHERE version_id = ?`` narrowing and the optimistic-lock
    contract collapses; the rest of the test class would still
    pass against the schema but production would silently regress.
    """
    with app.app_context():
        mapper = inspect(model)
        assert mapper.version_id_col is not None, (
            f"{model.__name__} mapper has no version_id_col -- "
            f"__mapper_args__ regression."
        )
        assert mapper.version_id_col.name == "version_id"


# ═════════════════════════════════════════════════════════════════════
# Lifecycle: insert, read, update, delete bumps
# ═════════════════════════════════════════════════════════════════════


class TestTransactionVersionLifecycle:
    """End-to-end behaviour of ``Transaction.version_id`` through the ORM."""

    def test_new_transaction_starts_at_version_one(
        self, app, seed_user, seed_periods,
    ):
        """``server_default='1'`` populates new rows at version 1."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            assert txn.version_id == 1

    def test_version_increments_only_on_update(
        self, app, seed_user, seed_periods,
    ):
        """Pure SELECT does not bump the counter; UPDATE bumps by one.

        A regression in either direction would be catastrophic:
        bumping on read would make every cached form stale; not
        bumping on update would make the lock no-op.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            v0 = txn.version_id
            txn_id = txn.id

            # Five reads must not bump the counter.
            for _ in range(5):
                _ = db.session.get(Transaction, txn_id).estimated_amount
                db.session.expire_all()
            assert _read_version(Transaction, txn_id) == v0

            # First update bumps by exactly one.
            txn = db.session.get(Transaction, txn_id)
            txn.notes = "updated"
            db.session.commit()
            assert _read_version(Transaction, txn_id) == v0 + 1

            # Second update bumps by exactly one more.
            txn = db.session.get(Transaction, txn_id)
            txn.notes = "updated again"
            db.session.commit()
            assert _read_version(Transaction, txn_id) == v0 + 2


@pytest.mark.parametrize(
    "factory_fn",
    [
        # Each factory is a callable taking (seed_user, seed_periods)
        # and returning the model id to bump.  Keeps the parameterised
        # test row-shape narrow.
        pytest.param("transfer", id="Transfer"),
        pytest.param("txn_template", id="TransactionTemplate"),
        pytest.param("xfer_template", id="TransferTemplate"),
        pytest.param("savings_goal", id="SavingsGoal"),
        pytest.param("salary_profile", id="SalaryProfile"),
        pytest.param("salary_raise", id="SalaryRaise"),
        pytest.param("paycheck_deduction", id="PaycheckDeduction"),
        pytest.param("transaction_entry", id="TransactionEntry"),
    ],
)
def test_concurrent_update_raises_stale_data_error(
    app, seed_user, seed_periods, factory_fn,
):
    """A version-bump out from under the test session raises StaleDataError.

    Same shape as test_concurrent_update_raises_stale_data_error
    in test_accounts.py but parameterised across every C-18
    versioned model.  This is the load-bearing invariant that
    makes the SQLAlchemy tier of the optimistic lock work.
    """
    with app.app_context():
        period = seed_periods[0]
        if factory_fn == "transfer":
            obj = _make_transfer(seed_user, period)
            schema, table, model = "budget", "transfers", Transfer

            def mutate(o):
                o.amount = Decimal("999.99")
        elif factory_fn == "txn_template":
            cat = seed_user["categories"]["Groceries"]
            obj = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            schema, table, model = (
                "budget", "transaction_templates", TransactionTemplate,
            )

            def mutate(o):
                o.default_amount = Decimal("999.99")
        elif factory_fn == "xfer_template":
            savings = _make_savings_account(seed_user["user"].id)
            cat = seed_user["categories"]["Groceries"]
            obj = _make_transfer_template(
                seed_user["user"].id,
                seed_user["account"].id,
                savings.id,
                cat.id,
            )
            schema, table, model = (
                "budget", "transfer_templates", TransferTemplate,
            )

            def mutate(o):
                o.default_amount = Decimal("999.99")
        elif factory_fn == "savings_goal":
            savings = _make_savings_account(seed_user["user"].id)
            obj = _make_savings_goal(seed_user["user"].id, savings.id)
            schema, table, model = "budget", "savings_goals", SavingsGoal

            def mutate(o):
                o.target_amount = Decimal("999.99")
        elif factory_fn == "salary_profile":
            obj = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            schema, table, model = (
                "salary", "salary_profiles", SalaryProfile,
            )

            def mutate(o):
                o.annual_salary = Decimal("99999.99")
        elif factory_fn == "salary_raise":
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            obj = _make_salary_raise(profile.id)
            schema, table, model = "salary", "salary_raises", SalaryRaise

            def mutate(o):
                o.percentage = Decimal("0.05")
        elif factory_fn == "paycheck_deduction":
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            obj = _make_paycheck_deduction(profile.id)
            schema, table, model = (
                "salary", "paycheck_deductions", PaycheckDeduction,
            )

            def mutate(o):
                o.amount = Decimal("999.99")
        else:  # transaction_entry
            _tmpl, txn = _make_envelope_template_and_txn(seed_user, period)
            obj = _make_entry(txn.id, seed_user["user"].id)
            schema, table, model = (
                "budget", "transaction_entries", TransactionEntry,
            )

            def mutate(o):
                o.amount = Decimal("99.99")

        obj_id = obj.id
        assert _read_version(model, obj_id) == 1

        # Test session loads at version 1.
        loaded = db.session.get(model, obj_id)
        # Concurrent commit bumps the row to version 2.
        _bump_version_outside_session(schema, table, obj_id)
        # Test session attempts to mutate the stale row.
        mutate(loaded)
        with pytest.raises(StaleDataError):
            db.session.commit()
        db.session.rollback()

        # Persisted row carries the winner's version, not the loser's.
        assert _read_version(model, obj_id) == 2


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- HTMX endpoints (Transaction)
# ═════════════════════════════════════════════════════════════════════


class TestTransactionStaleFormPrevention:
    """``update_transaction`` (PATCH /transactions/<id>) optimistic locking."""

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Submitting the row's current ``version_id`` updates and bumps."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            v0 = txn.version_id

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "estimated_amount": "75.00",
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200, response.data
            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.estimated_amount == Decimal("75.00")
            assert persisted.version_id == v0 + 1

    def test_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A submitted version older than the row's current counter returns 409.

        The route MUST short-circuit before touching the database;
        the financial value (``estimated_amount``) stays unchanged
        and the version counter is not bumped.  The 409 body
        carries the conflict cell so the user sees the latest value
        and a warning indicator.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            stale = txn.version_id

            _bump_version_outside_session("budget", "transactions", txn_id)
            db.session.expire_all()
            current = db.session.get(Transaction, txn_id).version_id
            assert current == stale + 1

            amount_before = db.session.get(Transaction, txn_id).estimated_amount

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "estimated_amount": "999.99",
                    "version_id": str(stale),
                },
            )

            assert response.status_code == 409, response.data
            body = response.data.decode()
            # Conflict cell carries the warning indicator.
            assert "exclamation-triangle" in body
            assert "text-warning" in body

            # No mutation, no version bump.
            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.estimated_amount == amount_before
            assert persisted.version_id == current

    def test_route_catches_stale_data_error_as_409(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A StaleDataError raised at flush time becomes a 409 + conflict cell.

        Engineers a true race: the test session loads the row at
        version N, mutates it, then a SQLAlchemy mapper event fires
        during the UPDATE and bumps the row from a separate
        connection, defeating the version-pinned WHERE clause.
        SQLAlchemy raises StaleDataError and the route's except
        clause converts it into the same 409 + conflict cell the
        form-side check produces.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            amount_before = txn.estimated_amount

            fired = {"flag": False}

            def make_stale(_mapper, _connection, target):
                if fired["flag"] or target.id != txn_id:
                    return
                fired["flag"] = True
                _bump_version_outside_session(
                    "budget", "transactions", txn_id,
                )

            event.listen(Transaction, "before_update", make_stale)
            try:
                response = auth_client.patch(
                    f"/transactions/{txn_id}",
                    data={"estimated_amount": "555.55"},
                )
            finally:
                event.remove(Transaction, "before_update", make_stale)

            assert response.status_code == 409, response.data

            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.estimated_amount == amount_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- HTMX endpoints (Transfer)
# ═════════════════════════════════════════════════════════════════════


class TestTransferStaleFormPrevention:
    """``update_transfer`` (PATCH /transfers/instance/<id>) optimistic locking."""

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Submitting the row's current ``version_id`` updates and bumps."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            v0 = xfer.version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "amount": "300.00",
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200, response.data
            db.session.expire_all()
            persisted = db.session.get(Transfer, xfer_id)
            assert persisted.amount == Decimal("300.00")
            assert persisted.version_id == v0 + 1

    def test_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stale ``version_id`` returns 409 + conflict cell, no mutation."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            stale = xfer.version_id

            _bump_version_outside_session("budget", "transfers", xfer_id)
            db.session.expire_all()
            amount_before = db.session.get(Transfer, xfer_id).amount

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "amount": "999.99",
                    "version_id": str(stale),
                },
            )

            assert response.status_code == 409
            body = response.data.decode()
            assert "exclamation-triangle" in body
            assert "text-warning" in body

            db.session.expire_all()
            persisted = db.session.get(Transfer, xfer_id)
            assert persisted.amount == amount_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- form-page endpoints (TransactionTemplate)
# ═════════════════════════════════════════════════════════════════════


class TestTransactionTemplateStaleFormPrevention:
    """``update_template`` (POST /templates/<id>) optimistic locking.

    Form-page route: stale-form check redirects with a flash
    warning rather than rendering an HTMX 409 partial.  Same
    semantics: no mutation, user reloads to retry.
    """

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user,
    ):
        """Matching version updates the template and bumps the counter."""
        with app.app_context():
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            tmpl_id = tmpl.id
            v0 = tmpl.version_id
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            response = auth_client.post(
                f"/templates/{tmpl_id}",
                data={
                    "name": "Renamed Template",
                    "default_amount": "150.00",
                    "category_id": str(cat.id),
                    "transaction_type_id": str(expense_type.id),
                    "account_id": str(seed_user["account"].id),
                    "version_id": str(v0),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            db.session.expire_all()
            persisted = db.session.get(TransactionTemplate, tmpl_id)
            assert persisted.name == "Renamed Template"
            assert persisted.version_id == v0 + 1

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """A stale version flashes a warning and rolls the form back."""
        with app.app_context():
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            tmpl_id = tmpl.id
            stale = tmpl.version_id

            _bump_version_outside_session(
                "budget", "transaction_templates", tmpl_id,
            )
            db.session.expire_all()
            name_before = db.session.get(
                TransactionTemplate, tmpl_id,
            ).name
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            response = auth_client.post(
                f"/templates/{tmpl_id}",
                data={
                    "name": "Should Not Apply",
                    "default_amount": "999.99",
                    "category_id": str(cat.id),
                    "transaction_type_id": str(expense_type.id),
                    "account_id": str(seed_user["account"].id),
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(TransactionTemplate, tmpl_id)
            assert persisted.name == name_before, (
                "Stale-form must NOT mutate the template."
            )


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- form-page endpoints (TransferTemplate)
# ═════════════════════════════════════════════════════════════════════


class TestTransferTemplateStaleFormPrevention:
    """``update_transfer_template`` (POST /transfers/<id>) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on transfer-template update flashes a warning."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_transfer_template(
                seed_user["user"].id,
                seed_user["account"].id,
                savings.id,
                cat.id,
            )
            tmpl_id = tmpl.id
            stale = tmpl.version_id

            _bump_version_outside_session(
                "budget", "transfer_templates", tmpl_id,
            )
            db.session.expire_all()
            name_before = db.session.get(TransferTemplate, tmpl_id).name

            response = auth_client.post(
                f"/transfers/{tmpl_id}",
                data={
                    "name": "Should Not Apply",
                    "default_amount": "9999.99",
                    "from_account_id": str(seed_user["account"].id),
                    "to_account_id": str(savings.id),
                    "category_id": str(cat.id),
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(TransferTemplate, tmpl_id)
            assert persisted.name == name_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- SavingsGoal
# ═════════════════════════════════════════════════════════════════════


class TestSavingsGoalStaleFormPrevention:
    """``update_goal`` (POST /savings/goals/<id>) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on savings-goal update flashes a warning."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            goal = _make_savings_goal(seed_user["user"].id, savings.id)
            goal_id = goal.id
            stale = goal.version_id

            _bump_version_outside_session("budget", "savings_goals", goal_id)
            db.session.expire_all()
            target_before = db.session.get(SavingsGoal, goal_id).target_amount

            response = auth_client.post(
                f"/savings/goals/{goal_id}",
                data={
                    "account_id": str(savings.id),
                    "name": "Renamed",
                    "target_amount": "99999.99",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(SavingsGoal, goal_id)
            assert persisted.target_amount == target_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- SalaryProfile / SalaryRaise / PaycheckDeduction
# ═════════════════════════════════════════════════════════════════════


class TestSalaryProfileStaleFormPrevention:
    """``update_profile`` (POST /salary/<id>) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on salary-profile update flashes a warning."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            profile_id = profile.id
            stale = profile.version_id
            filing_status = profile.filing_status_id

            _bump_version_outside_session(
                "salary", "salary_profiles", profile_id,
            )
            db.session.expire_all()
            salary_before = db.session.get(
                SalaryProfile, profile_id,
            ).annual_salary

            response = auth_client.post(
                f"/salary/{profile_id}",
                data={
                    "name": "Renamed Job",
                    "annual_salary": "99999.99",
                    "filing_status_id": str(filing_status),
                    "state_code": "NC",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(SalaryProfile, profile_id)
            assert persisted.annual_salary == salary_before


class TestSalaryRaiseStaleFormPrevention:
    """``update_raise`` (POST /salary/raises/<id>/edit) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on raise update flashes a warning."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            sraise = _make_salary_raise(profile.id)
            raise_id = sraise.id
            stale = sraise.version_id
            raise_type = sraise.raise_type_id

            _bump_version_outside_session(
                "salary", "salary_raises", raise_id,
            )
            db.session.expire_all()
            pct_before = db.session.get(SalaryRaise, raise_id).percentage

            response = auth_client.post(
                f"/salary/raises/{raise_id}/edit",
                data={
                    "raise_type_id": str(raise_type),
                    "effective_month": "6",
                    "effective_year": "2027",
                    "percentage": "10",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(SalaryRaise, raise_id)
            assert persisted.percentage == pct_before


class TestPaycheckDeductionStaleFormPrevention:
    """``update_deduction`` (POST /salary/deductions/<id>/edit) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on deduction update flashes a warning."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            ded = _make_paycheck_deduction(profile.id)
            ded_id = ded.id
            stale = ded.version_id
            timing_id = ded.deduction_timing_id
            method_id = ded.calc_method_id

            _bump_version_outside_session(
                "salary", "paycheck_deductions", ded_id,
            )
            db.session.expire_all()
            amount_before = db.session.get(
                PaycheckDeduction, ded_id,
            ).amount

            response = auth_client.post(
                f"/salary/deductions/{ded_id}/edit",
                data={
                    "name": "Renamed Deduction",
                    "deduction_timing_id": str(timing_id),
                    "calc_method_id": str(method_id),
                    "amount": "999.99",
                    "deductions_per_year": "26",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(PaycheckDeduction, ded_id)
            assert persisted.amount == amount_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- TransactionEntry
# ═════════════════════════════════════════════════════════════════════


class TestTransactionEntryStaleFormPrevention:
    """``update_entry`` (PATCH /transactions/<id>/entries/<id>) optimistic locking."""

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Matching version updates the entry and bumps the counter."""
        with app.app_context():
            _tmpl, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            entry = _make_entry(txn.id, seed_user["user"].id)
            entry_id = entry.id
            v0 = entry.version_id

            response = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry_id}",
                data={
                    "amount": "30.00",
                    "description": "Walmart",
                    "entry_date": entry.entry_date.isoformat(),
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200, response.data
            db.session.expire_all()
            persisted = db.session.get(TransactionEntry, entry_id)
            assert persisted.amount == Decimal("30.00")
            assert persisted.description == "Walmart"
            assert persisted.version_id == v0 + 1

    def test_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stale ``version_id`` returns 409 + entry list with warning banner."""
        with app.app_context():
            _tmpl, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            entry = _make_entry(txn.id, seed_user["user"].id)
            entry_id = entry.id
            stale = entry.version_id

            _bump_version_outside_session(
                "budget", "transaction_entries", entry_id,
            )
            db.session.expire_all()
            amount_before = db.session.get(
                TransactionEntry, entry_id,
            ).amount

            response = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry_id}",
                data={
                    "amount": "9999.99",
                    "description": "Should Not Apply",
                    "entry_date": entry.entry_date.isoformat(),
                    "version_id": str(stale),
                },
            )

            assert response.status_code == 409, response.data
            body = response.data.decode()
            assert "changed by another action" in body.lower()

            db.session.expire_all()
            persisted = db.session.get(TransactionEntry, entry_id)
            assert persisted.amount == amount_before


# ═════════════════════════════════════════════════════════════════════
# Templates ship the version_id pin -- regression guards
# ═════════════════════════════════════════════════════════════════════


class TestEditTemplatesEmitVersionPin:
    """Edit forms must include ``<input type="hidden" name="version_id">``."""

    def test_transaction_quick_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid quick-edit template ships the txn's current version."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            v = txn.version_id

            response = auth_client.get(f"/transactions/{txn_id}/quick-edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transaction_full_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid full-edit popover ships the txn's current version."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            v = txn.version_id

            response = auth_client.get(f"/transactions/{txn_id}/full-edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transfer_quick_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid transfer quick-edit ships the xfer's current version."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            v = xfer.version_id

            response = auth_client.get(
                f"/transfers/quick-edit/{xfer_id}",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transfer_full_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid transfer full-edit ships the xfer's current version."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            v = xfer.version_id

            response = auth_client.get(
                f"/transfers/{xfer_id}/full-edit",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transaction_template_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The recurring-transaction edit form ships the row's version."""
        with app.app_context():
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            tmpl_id = tmpl.id
            v = tmpl.version_id

            response = auth_client.get(f"/templates/{tmpl_id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transaction_template_create_form_omits_version(
        self, app, auth_client,
    ):
        """The create form has no version pin (no row to lock yet).

        Mirrors test_account_create_form_omits_version_pin in
        test_accounts.py: a copy-paste regression that put a
        ``template.version_id`` reference into the create path
        would Jinja-error against ``template = None``.
        """
        with app.app_context():
            response = auth_client.get("/templates/new")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' not in body

    def test_transfer_template_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The recurring-transfer edit form ships the row's version."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_transfer_template(
                seed_user["user"].id,
                seed_user["account"].id,
                savings.id,
                cat.id,
            )
            tmpl_id = tmpl.id
            v = tmpl.version_id

            response = auth_client.get(f"/transfers/{tmpl_id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_savings_goal_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The savings-goal edit form ships the row's version."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            goal = _make_savings_goal(seed_user["user"].id, savings.id)
            goal_id = goal.id
            v = goal.version_id

            response = auth_client.get(
                f"/savings/goals/{goal_id}/edit",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_salary_profile_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The salary-profile edit form ships the row's version."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            profile_id = profile.id
            v = profile.version_id

            response = auth_client.get(f"/salary/{profile_id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_salary_raise_edit_button_carries_version(
        self, app, auth_client, seed_user,
    ):
        """The raise edit button carries ``data-raise-version-id``.

        Salary raises share a single form for create and edit; the
        version pin is plumbed into the form by app.js when the user
        clicks the edit button, which carries the row's version_id
        as a data attribute.  This test asserts the data attribute
        is rendered server-side so the JS hand-off cannot regress
        silently.
        """
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            sraise = _make_salary_raise(profile.id)
            v = sraise.version_id

            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'data-raise-version-id="{v}"' in body
            ), (
                "Raise edit button must include data-raise-version-id "
                "so app.js can populate the form's hidden version "
                "input on edit."
            )

    def test_paycheck_deduction_edit_button_carries_version(
        self, app, auth_client, seed_user,
    ):
        """The deduction edit button carries ``data-ded-version-id``.

        Same shape as the raise test: the edit button surfaces the
        row's current version as a data attribute that app.js wires
        into the shared add/edit form.
        """
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            ded = _make_paycheck_deduction(profile.id)
            v = ded.version_id

            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'data-ded-version-id="{v}"' in body
            ), (
                "Deduction edit button must include "
                "data-ded-version-id so app.js can populate the "
                "form's hidden version input on edit."
            )

    def test_entry_inline_edit_form_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The entry inline-edit form ships the row's version_id."""
        with app.app_context():
            _tmpl, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            entry = _make_entry(txn.id, seed_user["user"].id)
            entry_id = entry.id
            v = entry.version_id

            response = auth_client.get(
                f"/transactions/{txn.id}/entries?editing={entry_id}",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body
