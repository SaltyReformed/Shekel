"""
Shekel Budget App -- Idempotency / Double-Submit Tests

Tests that every POST endpoint handles double-submission safely:
  - Login double-submit refreshes session
  - Templates allow duplicates (no unique constraint)
  - Raises reject duplicates (F-051 / C-23 -- composite unique)
  - Deductions reject duplicates (F-052 / C-23 -- composite unique)

NOTE: Several idempotency tests already exist in their respective
route test files: accounts, salary profiles, transfers, savings goals,
categories, and pay periods. This file covers the remaining cases.
"""

from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    CalcMethod, DeductionTiming, FilingStatus, RaiseType,
    RecurrencePattern, Status, TransactionType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate


# ── Helpers ──────────────────────────────────────────────────────────


def _create_profile(seed_user):
    """Helper: create a salary profile with linked template and recurrence."""
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()
    every_period = db.session.query(RecurrencePattern).filter_by(name="Every Period").one()

    cat = (
        db.session.query(Category)
        .filter_by(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        .first()
    )
    if not cat:
        cat = Category(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        db.session.add(cat)
        db.session.flush()

    rule = RecurrenceRule(user_id=seed_user["user"].id, pattern_id=every_period.id)
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=income_type.id,
        name="Day Job",
        default_amount=Decimal("2884.62"),
    )
    db.session.add(template)
    db.session.flush()

    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
        name="Day Job",
        annual_salary=Decimal("75000.00"),
        filing_status_id=filing_status.id,
        state_code="NC",
    )
    db.session.add(profile)
    db.session.commit()
    return profile


# ── Tests ────────────────────────────────────────────────────────────


class TestLoginDoubleSubmit:
    """Double login refreshes session without error."""

    def test_double_login_succeeds(self, app, client, seed_user):
        """POST /login twice with valid credentials succeeds both times
        and the session remains functional after each login. Verifies
        session integrity is preserved across double-submit."""
        with app.app_context():
            data = {"email": "test@shekel.local", "password": "testpass"}

            # First login.
            resp1 = client.post("/login", data=data, follow_redirects=False)
            assert resp1.status_code == 302

            # Verify session is live after FIRST login.
            protected_resp1 = client.get("/settings")
            assert protected_resp1.status_code == 200
            assert b"Settings" in protected_resp1.data

            # Second login while already authenticated -- redirects to grid.
            resp2 = client.post("/login", data=data, follow_redirects=False)
            assert resp2.status_code == 302

            # Verify session is still live after SECOND login --
            # double-login must not corrupt or destroy the session.
            protected_resp2 = client.get("/settings")
            assert protected_resp2.status_code == 200
            assert b"Settings" in protected_resp2.data


class TestTemplateDoubleSubmit:
    """Templates have no unique constraint -- duplicates are created."""

    def test_duplicate_template_creates_second(self, app, auth_client, seed_user):
        """POST /templates twice with same name creates two templates."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            data = {
                "name": "Monthly Rent",
                "default_amount": "1200.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            }

            # First submit.
            resp1 = auth_client.post("/templates", data=data, follow_redirects=True)
            assert resp1.status_code == 200

            # Second submit with same data -- no unique constraint, so it succeeds.
            resp2 = auth_client.post("/templates", data=data, follow_redirects=True)
            assert resp2.status_code == 200

            # Verify two templates exist with the same name.
            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Monthly Rent",
            ).count()
            assert count == 2


class TestRaiseDoubleSubmit:
    """F-051 / C-23: composite unique rejects duplicate raises.

    Before C-23, a double-submit on the raise form created two
    rows with identical effective dates and the paycheck calculator
    compounded the raise twice.  After C-23, the unique constraint
    ``uq_salary_raises_profile_type_year_month`` rejects the second
    INSERT and the route surfaces idempotent success.
    """

    def test_duplicate_raise_rejected(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/raises twice creates exactly one raise."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            data = {
                "raise_type_id": raise_type.id,
                "effective_month": "7",
                "effective_year": "2026",
                "percentage": "3",
            }

            # First submit -- success.
            resp1 = auth_client.post(
                f"/salary/{profile.id}/raises",
                data=data, follow_redirects=True,
            )
            assert b"Raise added." in resp1.data

            # Second submit with same data -- idempotent rejection.
            resp2 = auth_client.post(
                f"/salary/{profile.id}/raises",
                data=data, follow_redirects=True,
            )
            assert resp2.status_code == 200
            assert b"already exists" in resp2.data

            # Verify exactly one raise exists.
            db.session.expire_all()
            count = db.session.query(SalaryRaise).filter_by(
                salary_profile_id=profile.id,
            ).count()
            assert count == 1, (
                f"Expected 1 raise after double-submit, found {count}; "
                f"F-051 dedupe failed."
            )


class TestDeductionDoubleSubmit:
    """F-052 / C-23: composite unique rejects duplicate deductions.

    Before C-23, a double-submit on the deduction form created two
    rows with the same name and amount and the paycheck calculator
    subtracted the deduction twice.  After C-23, the unique
    constraint ``uq_paycheck_deductions_profile_name`` rejects the
    second INSERT and the route surfaces idempotent success.
    """

    def test_duplicate_deduction_rejected(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/deductions twice creates exactly one deduction."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            data = {
                "name": "401k",
                "deduction_timing_id": pre_tax.id,
                "calc_method_id": flat_method.id,
                "amount": "250.0000",
            }

            # First submit -- success.
            resp1 = auth_client.post(
                f"/salary/{profile.id}/deductions",
                data=data, follow_redirects=True,
            )
            assert b"401k" in resp1.data

            # Second submit with same data -- idempotent rejection.
            resp2 = auth_client.post(
                f"/salary/{profile.id}/deductions",
                data=data, follow_redirects=True,
            )
            assert resp2.status_code == 200
            assert b"already exists" in resp2.data

            # Verify exactly one deduction with this name exists.
            db.session.expire_all()
            count = db.session.query(PaycheckDeduction).filter_by(
                salary_profile_id=profile.id,
                name="401k",
            ).count()
            assert count == 1, (
                f"Expected 1 deduction after double-submit, found "
                f"{count}; F-052 dedupe failed."
            )


# ── Transaction Double-Submit Tests ──────────────────────────────────


class TestTransactionDoubleSubmit:
    """Double-submit on transaction creation is the most financially
    dangerous idempotency scenario: a duplicated $2000 rent payment
    silently doubles the projected expense."""

    def test_transaction_create_double_submit(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions twice with identical data creates two transactions.

        This is the most financially dangerous double-submit scenario in the
        application. Ad-hoc transactions (template_id=None) have no unique
        constraint, so double-clicking 'Add Transaction' creates two
        identical rows.

        # WARNING: No duplicate ad-hoc transaction prevention. Double-click
        # creates two transactions. User's projected balance will be off by
        # the duplicated amount. This is a real financial risk.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            data = {
                "name": "Rent",
                "estimated_amount": "2000.00",
                "pay_period_id": str(seed_periods[0].id),
                "scenario_id": str(seed_user["scenario"].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(expense_type.id),
                "account_id": str(seed_user["account"].id),
            }

            # First submit.
            resp1 = auth_client.post("/transactions", data=data)
            assert resp1.status_code == 201

            # Second submit with identical data.
            resp2 = auth_client.post("/transactions", data=data)
            assert resp2.status_code == 201

            # Verify database state: two identical transactions exist.
            db.session.expire_all()
            count = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                name="Rent",
            ).filter(Transaction.is_deleted.is_(False)).count()
            # WARNING: No duplicate transaction prevention. Double-click
            # creates two transactions. User's projected balance will be
            # off by $2000.00. This is a real financial risk.
            assert count == 2

            # Both transactions have the correct amount.
            txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                name="Rent",
            ).filter(Transaction.is_deleted.is_(False)).all()
            for txn in txns:
                assert txn.estimated_amount == Decimal("2000.00")

    def test_transaction_create_first_valid_second_invalid(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST valid transaction, then invalid -- only valid one persists.

        The failed second request must not corrupt or modify the first
        transaction. Verifies the database contains exactly 1 transaction
        with the original values unchanged.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            valid_data = {
                "name": "Electric Bill",
                "estimated_amount": "150.00",
                "pay_period_id": str(seed_periods[0].id),
                "scenario_id": str(seed_user["scenario"].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(expense_type.id),
                "account_id": str(seed_user["account"].id),
            }

            # First submit -- valid.
            resp1 = auth_client.post("/transactions", data=valid_data)
            assert resp1.status_code == 201

            # Second submit -- invalid (missing required name field).
            invalid_data = {
                "estimated_amount": "150.00",
                "pay_period_id": str(seed_periods[0].id),
                "scenario_id": str(seed_user["scenario"].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(expense_type.id),
                "account_id": str(seed_user["account"].id),
            }
            resp2 = auth_client.post("/transactions", data=invalid_data)
            assert resp2.status_code == 400

            # Database has exactly 1 transaction -- the valid one.
            db.session.expire_all()
            txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(txns) == 1
            assert txns[0].name == "Electric Bill"
            assert txns[0].estimated_amount == Decimal("150.00")

    def test_transaction_create_first_invalid_second_valid(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST invalid data first, then valid -- only valid one persists.

        A failed first submission must not prevent a subsequent valid
        submission from succeeding.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # First submit -- invalid (missing name).
            invalid_data = {
                "estimated_amount": "200.00",
                "pay_period_id": str(seed_periods[0].id),
                "scenario_id": str(seed_user["scenario"].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(expense_type.id),
                "account_id": str(seed_user["account"].id),
            }
            resp1 = auth_client.post("/transactions", data=invalid_data)
            assert resp1.status_code == 400

            # Second submit -- valid.
            valid_data = {
                "name": "Water Bill",
                "estimated_amount": "75.00",
                "pay_period_id": str(seed_periods[0].id),
                "scenario_id": str(seed_user["scenario"].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(expense_type.id),
                "account_id": str(seed_user["account"].id),
            }
            resp2 = auth_client.post("/transactions", data=valid_data)
            assert resp2.status_code == 201

            # Database has exactly 1 transaction from the second (valid) submit.
            db.session.expire_all()
            txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(txns) == 1
            assert txns[0].name == "Water Bill"
            assert txns[0].estimated_amount == Decimal("75.00")


# ── Pay Period Generation Idempotency ────────────────────────────────


class TestPayPeriodGenerationIdempotency:
    """Pay period generation deduplicates by start_date."""

    def test_double_submit_pay_period_generate(self, app, db, seed_user):
        """Generating pay periods twice with the same start date skips duplicates.

        The source checks existing start_dates and skips any that already
        exist. A second generation with the same parameters should produce
        0 new periods (all skipped as duplicates).
        """
        from app.services import pay_period_service
        from datetime import date as dt_date
        from app.models.pay_period import PayPeriod

        with app.app_context():
            user_id = seed_user["user"].id

            # First generation: 10 periods starting 2026-06-01.
            periods1 = pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=dt_date(2026, 6, 1),
                num_periods=10,
                cadence_days=14,
            )
            db.session.commit()
            assert len(periods1) == 10

            # Second generation: same parameters.
            periods2 = pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=dt_date(2026, 6, 1),
                num_periods=10,
                cadence_days=14,
            )
            db.session.commit()
            # All 10 start_dates already exist → 0 new periods.
            assert len(periods2) == 0

            # Database has exactly 10 periods total (not 20).
            db.session.expire_all()
            total = db.session.query(PayPeriod).filter_by(user_id=user_id).count()
            assert total == 10

            # Verify every period has the expected start/end dates.
            all_periods = (
                db.session.query(PayPeriod)
                .filter_by(user_id=user_id)
                .order_by(PayPeriod.period_index)
                .all()
            )
            from datetime import timedelta
            expected_start = dt_date(2026, 6, 1)
            for i, period in enumerate(all_periods):
                assert period.start_date == expected_start + timedelta(days=14 * i)
                assert period.end_date == expected_start + timedelta(days=14 * i + 13)

    def test_double_submit_pay_period_generate_overlapping_range(self, app, db, seed_user):
        """Generating periods with an overlapping date range deduplicates overlap.

        First batch: 10 periods starting 2026-06-01 (covers through ~2026-08-30).
        Second batch: 10 periods starting 2026-08-03 (overlaps with tail of first).
        Overlapping start dates are skipped; non-overlapping ones are appended.
        """
        from app.services import pay_period_service
        from datetime import date as dt_date, timedelta
        from app.models.pay_period import PayPeriod

        with app.app_context():
            user_id = seed_user["user"].id

            # First generation: 10 periods starting 2026-06-01.
            periods1 = pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=dt_date(2026, 6, 1),
                num_periods=10,
                cadence_days=14,
            )
            db.session.commit()
            assert len(periods1) == 10

            # Compute which start dates from batch 2 overlap with batch 1.
            batch1_starts = {dt_date(2026, 6, 1) + timedelta(days=14 * i) for i in range(10)}
            batch2_starts = {dt_date(2026, 8, 3) + timedelta(days=14 * i) for i in range(10)}
            overlapping = batch1_starts & batch2_starts
            expected_new = len(batch2_starts - batch1_starts)

            # Second generation: 10 periods starting 2026-08-03.
            periods2 = pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=dt_date(2026, 8, 3),
                num_periods=10,
                cadence_days=14,
            )
            db.session.commit()
            assert len(periods2) == expected_new

            # Total = 10 + expected_new (no duplicates).
            db.session.expire_all()
            total = db.session.query(PayPeriod).filter_by(user_id=user_id).count()
            assert total == 10 + expected_new


# ── Mark-Done Double Submit ──────────────────────────────────────────


class TestMarkDoneDoubleSubmit:
    """Marking a transaction as done twice is safely idempotent."""

    def test_mark_done_double_submit(self, app, auth_client, seed_user, seed_periods):
        """POST mark-done twice on same transaction is idempotent.

        The second mark-done simply re-sets the status to 'done' -- no error,
        no state change, no corruption of actual_amount.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Electricity",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("120.00"),
            )
            db.session.add(txn)
            db.session.commit()
            txn_id = txn.id

            # First mark-done with actual_amount.
            resp1 = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "115.50"},
            )
            assert resp1.status_code == 200

            # Second mark-done -- same operation again.
            resp2 = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "115.50"},
            )
            assert resp2.status_code == 200

            # Verify DB state: transaction is done with correct actual_amount.
            db.session.expire_all()
            txn = db.session.get(Transaction, txn_id)
            assert txn.status.name == "Paid"
            assert txn.actual_amount == Decimal("115.50")
            assert txn.estimated_amount == Decimal("120.00")


# ── Carry Forward Double Submit ──────────────────────────────────────


class TestCarryForwardDoubleSubmit:
    """Double carry-forward: second call is a no-op since source has 0 projected."""

    def test_carry_forward_double_submit(self, app, db, seed_user, seed_periods):
        """Carrying forward twice moves items only once.

        After the first carry-forward, source period has 0 projected
        transactions, so the second carry-forward moves nothing. Target
        period has exactly N transactions (not 2N).
        """
        from app.services import carry_forward_service

        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create 3 projected transactions in period 0.
            amounts = [Decimal("850.00"), Decimal("125.50"), Decimal("43.99")]
            for i, amount in enumerate(amounts):
                txn = Transaction(
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name=f"Item {i}",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=amount,
                )
                db.session.add(txn)
            db.session.commit()

            # First carry-forward: moves 3 items.
            count1 = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()
            assert count1 == 3

            # Second carry-forward: source is empty, moves 0 items.
            count2 = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()
            assert count2 == 0

            # Target period has exactly 3 transactions (not 6).
            db.session.expire_all()
            target_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(target_txns) == 3

            # All amounts are preserved.
            actual_amounts = sorted(t.estimated_amount for t in target_txns)
            assert actual_amounts == sorted(amounts)

            # Source period has 0 projected transactions.
            source_projected = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(
                Transaction.status.has(name="projected"),
                Transaction.is_deleted.is_(False),
            ).count()
            assert source_projected == 0
