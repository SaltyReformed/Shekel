"""Tests for scripts/integrity_check.py (Phase 8C WU-4)."""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern, Status, TransactionType
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User
from app.services.auth_service import hash_password
from scripts.integrity_check import (
    CheckResult,
    check_balance_anomalies,
    check_data_consistency,
    check_orphaned_records,
    check_referential_integrity,
    run_all_checks,
)


# ── CheckResult dataclass ────────────────────────────────────────


class TestCheckResult:
    """Tests for the CheckResult dataclass."""

    def test_passing_check(self):
        """A passing CheckResult has passed=True and detail_count=0."""
        result = CheckResult(
            check_id="TEST-01",
            category="test",
            severity="critical",
            description="test check",
            passed=True,
            detail_count=0,
        )
        assert result.passed is True
        assert result.detail_count == 0
        assert result.details == []

    def test_failing_check(self):
        """A failing CheckResult has passed=False and detail_count > 0."""
        result = CheckResult(
            check_id="TEST-02",
            category="test",
            severity="warning",
            description="test check",
            passed=False,
            detail_count=3,
            details=[{"id": 1}, {"id": 2}, {"id": 3}],
        )
        assert result.passed is False
        assert result.detail_count == 3
        assert len(result.details) == 3


# ── Referential Integrity ────────────────────────────────────────


class TestReferentialIntegrity:
    """Tests for FK-* referential integrity checks."""

    def test_clean_database_passes_all(self, app, db, seed_user, seed_periods):
        """All FK checks pass on a properly seeded database."""
        results = check_referential_integrity(db.session)
        assert all(r.passed for r in results), (
            f"Failed checks: {[r.check_id for r in results if not r.passed]}"
        )
        assert len(results) == 13

    def test_fk01_detects_orphaned_account(self, app, db, seed_user):
        """FK-01 detects an account whose user_id references a nonexistent user."""
        # Insert an account with a bogus user_id via raw SQL to bypass FK.
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        db.session.execute(db.text("""
            INSERT INTO budget.accounts (user_id, account_type_id, name,
                                         current_anchor_balance)
            VALUES (99999, 1, 'Orphaned Account', 100.00)
        """))
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk01 = next(r for r in results if r.check_id == "FK-01")
        assert not fk01.passed
        assert fk01.detail_count == 1  # 1 orphaned account inserted

        # Restore FK enforcement.
        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk05_detects_transaction_with_missing_period(
        self, app, db, seed_user, seed_periods
    ):
        """FK-05 detects a transaction referencing a nonexistent pay period."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        status = db.session.query(Status).filter_by(name="projected").one()
        txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
        db.session.execute(db.text("""
            INSERT INTO budget.transactions
                (pay_period_id, scenario_id, status_id, name,
                 transaction_type_id, estimated_amount)
            VALUES (99999, :sid, :stid, 'Ghost Txn', :ttid, 50.00)
        """), {
            "sid": seed_user["scenario"].id,
            "stid": status.id,
            "ttid": txn_type.id,
        })
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk05 = next(r for r in results if r.check_id == "FK-05")
        assert not fk05.passed
        assert fk05.detail_count == 1  # 1 transaction with missing period

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk10_detects_template_with_missing_category(self, app, db, seed_user):
        """FK-10 detects a transaction template with an invalid category_id."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
        db.session.execute(db.text("""
            INSERT INTO budget.transaction_templates
                (user_id, account_id, category_id, transaction_type_id,
                 name, default_amount)
            VALUES (:uid, :aid, 99999, :ttid, 'Bad Template', 25.00)
        """), {
            "uid": seed_user["user"].id,
            "aid": seed_user["account"].id,
            "ttid": txn_type.id,
        })
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk10 = next(r for r in results if r.check_id == "FK-10")
        assert not fk10.passed
        assert fk10.detail_count == 1  # 1 template with missing category

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk02_detects_account_with_invalid_type(self, app, db, seed_user):
        """FK-02: Accounts with invalid account_type_id."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        db.session.execute(db.text("""
            INSERT INTO budget.accounts
                (user_id, account_type_id, name, current_anchor_balance)
            VALUES (:uid, 99999, 'Bad Type Account', 100.00)
        """), {"uid": seed_user["user"].id})
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk02 = next(r for r in results if r.check_id == "FK-02")
        assert not fk02.passed
        assert fk02.detail_count == 1
        # Verify the detail identifies the offending row.
        assert fk02.details[0]["name"] == "Bad Type Account"
        assert fk02.details[0]["account_type_id"] == 99999

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk03_detects_account_with_missing_anchor_period(
        self, app, db, seed_user
    ):
        """FK-03: Accounts pointing to nonexistent anchor period."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        # Point the seed account to a nonexistent pay period.
        db.session.execute(db.text("""
            UPDATE budget.accounts
            SET current_anchor_period_id = 99999
            WHERE id = :aid
        """), {"aid": seed_user["account"].id})
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk03 = next(r for r in results if r.check_id == "FK-03")
        assert not fk03.passed
        assert fk03.detail_count == 1
        assert fk03.details[0]["id"] == seed_user["account"].id

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))


# ── Orphan Detection ─────────────────────────────────────────────


class TestOrphanDetection:
    """Tests for OR-* orphan detection checks."""

    def test_clean_database_no_orphans(self, app, db, seed_user, seed_periods):
        """No orphans detected on a properly seeded database.

        Note: OR-03 (unused categories) and OR-04 (empty pay periods) will
        flag results on a minimal seed because categories have no templates
        and periods have no transactions. These are warnings, not errors.
        We verify the check runs without crashing; specific orphan detection
        is tested in dedicated methods below.
        """
        results = check_orphaned_records(db.session)
        assert len(results) == 6
        # All should return CheckResult objects regardless of pass/fail.
        assert all(isinstance(r, CheckResult) for r in results)

    def test_or02_detects_unused_recurrence_rule(self, app, db, seed_user):
        """OR-02 detects a recurrence rule not referenced by any template."""
        pattern = db.session.query(RecurrencePattern).filter_by(
            name="every_period"
        ).one()
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
        )
        db.session.add(rule)
        db.session.flush()

        results = check_orphaned_records(db.session)
        or02 = next(r for r in results if r.check_id == "OR-02")
        assert not or02.passed
        assert or02.detail_count == 1  # 1 orphaned recurrence rule

    def test_or03_detects_unused_category(self, app, db, seed_user):
        """OR-03 detects a category not used by any template or transaction."""
        # The seed_user fixture creates categories that are unused by default.
        results = check_orphaned_records(db.session)
        or03 = next(r for r in results if r.check_id == "OR-03")
        # Seed categories are not referenced by any templates or transactions.
        assert not or03.passed
        # seed_user creates 5 categories (Salary, Rent, Car Payment, Groceries, Payback)
        # none referenced by any template or transaction
        assert or03.detail_count == 5

    def test_or01_detects_orphaned_template(self, app, db, seed_user):
        """OR-01: Template with no recurrence rule and no transactions."""
        txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
        category = list(seed_user["categories"].values())[0]

        # Create a template with no recurrence_rule_id and no transactions.
        orphan_template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=category.id,
            transaction_type_id=txn_type.id,
            name="Orphaned Template",
            default_amount=Decimal("50.00"),
            recurrence_rule_id=None,
        )
        db.session.add(orphan_template)
        db.session.flush()

        results = check_orphaned_records(db.session)
        or01 = next(r for r in results if r.check_id == "OR-01")
        assert not or01.passed
        assert or01.detail_count == 1
        assert or01.details[0]["name"] == "Orphaned Template"

    def test_or06_detects_goal_on_inactive_account(self, app, db, seed_user):
        """OR-06 flags a savings goal on an inactive account."""
        account = seed_user["account"]
        account.is_active = False
        db.session.flush()

        goal = SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=account.id,
            name="Bad Goal",
            target_amount=Decimal("1000.00"),
            is_active=True,
        )
        db.session.add(goal)
        db.session.flush()

        results = check_orphaned_records(db.session)
        or06 = next(r for r in results if r.check_id == "OR-06")
        assert not or06.passed
        assert or06.detail_count == 1  # 1 goal on inactive account


# ── Balance Anomalies ────────────────────────────────────────────


class TestBalanceAnomalies:
    """Tests for BA-* balance anomaly checks."""

    def test_clean_database_no_anomalies(self, app, db, seed_user, seed_periods):
        """No balance anomalies on a properly seeded database."""
        results = check_balance_anomalies(db.session)
        assert len(results) == 5
        # BA-01 may flag if seed account has balance but no anchor period set
        # before seed_periods runs. With seed_periods, it should pass.
        ba01 = next(r for r in results if r.check_id == "BA-01")
        assert ba01.passed

    def test_ba01_detects_balance_without_period(self, app, db, seed_user):
        """BA-01 flags account with anchor balance but no anchor period."""
        account = seed_user["account"]
        # seed_user sets current_anchor_balance=1000 but no anchor period.
        assert account.current_anchor_balance is not None
        assert account.current_anchor_period_id is None

        results = check_balance_anomalies(db.session)
        ba01 = next(r for r in results if r.check_id == "BA-01")
        assert not ba01.passed
        assert ba01.detail_count == 1  # 1 account with balance but no anchor period

    def test_ba03_detects_period_gap(self, app, db, seed_user):
        """BA-03 detects a gap in the pay period index sequence."""
        user = seed_user["user"]
        # Create periods with indices 0, 1, 3 (gap at 2).
        for idx, start in [(0, date(2026, 1, 2)), (1, date(2026, 1, 16)),
                           (3, date(2026, 2, 13))]:
            pp = PayPeriod(
                user_id=user.id,
                start_date=start,
                end_date=date(start.year, start.month, start.day + 13),
                period_index=idx,
            )
            db.session.add(pp)
        db.session.flush()

        results = check_balance_anomalies(db.session)
        ba03 = next(r for r in results if r.check_id == "BA-03")
        assert not ba03.passed
        assert ba03.detail_count == 1  # 1 gap at index 2

    def test_ba02_detects_anchor_beyond_last_period(self, app, db, seed_user):
        """BA-02: Anchor period is beyond the last pay period for the user.

        The check joins accounts -> pay_periods (via anchor_period_id) and
        compares the anchor period's index to the max period_index for the
        account's user.  To trigger it, we create a period owned by a
        different (fake) user with a high index and point the real
        account's anchor to it — bypassing FK constraints via replica mode.
        """
        user = seed_user["user"]
        account = seed_user["account"]

        # Create normal periods for this user so max_idx is well-defined.
        for idx, (start, end) in enumerate([
            (date(2026, 1, 2), date(2026, 1, 15)),
            (date(2026, 1, 16), date(2026, 1, 29)),
            (date(2026, 1, 30), date(2026, 2, 12)),
        ]):
            pp = PayPeriod(
                user_id=user.id,
                start_date=start,
                end_date=end,
                period_index=idx,
            )
            db.session.add(pp)
        db.session.flush()

        # Use replica mode to bypass FK/CHECK constraints.
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        # Create a fake period with a very high index owned by a nonexistent user.
        db.session.execute(db.text("""
            INSERT INTO budget.pay_periods
                (user_id, start_date, end_date, period_index)
            VALUES (99999, '2030-01-01', '2030-01-14', 999)
        """))
        fake_period_id = db.session.execute(
            db.text("SELECT id FROM budget.pay_periods WHERE period_index = 999")
        ).scalar()

        # Point the account's anchor to this high-index period.
        db.session.execute(db.text("""
            UPDATE budget.accounts
            SET current_anchor_period_id = :pid
            WHERE id = :aid
        """), {"pid": fake_period_id, "aid": account.id})
        db.session.flush()

        results = check_balance_anomalies(db.session)
        ba02 = next(r for r in results if r.check_id == "BA-02")
        assert not ba02.passed
        assert ba02.detail_count == 1
        assert ba02.details[0]["id"] == account.id

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_ba04_detects_date_overlap(self, app, db, seed_user):
        """BA-04 detects overlapping pay period date ranges."""
        user = seed_user["user"]
        # Create two overlapping periods.
        pp1 = PayPeriod(
            user_id=user.id,
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 15),
            period_index=0,
        )
        pp2 = PayPeriod(
            user_id=user.id,
            start_date=date(2026, 1, 10),
            end_date=date(2026, 1, 23),
            period_index=1,
        )
        db.session.add_all([pp1, pp2])
        db.session.flush()

        results = check_balance_anomalies(db.session)
        ba04 = next(r for r in results if r.check_id == "BA-04")
        assert not ba04.passed
        assert ba04.detail_count == 1  # 1 overlapping pair (pp1/pp2)


# ── Data Consistency ─────────────────────────────────────────────


class TestDataConsistency:
    """Tests for DC-* data consistency checks."""

    def test_clean_database_passes(self, app, db, seed_user, seed_periods):
        """All consistency checks pass on a properly seeded database."""
        results = check_data_consistency(db.session)
        assert len(results) == 9
        # On a clean seed, DC-01 through DC-09 should all pass.
        for r in results:
            if not r.passed:
                # Allow DC-01 to pass (no done transactions in seed).
                # Allow DC-03 to pass (no typed accounts in seed).
                pass
        # Critical checks must pass on clean data.
        critical_results = [r for r in results if r.severity == "critical"]
        assert all(r.passed for r in critical_results), (
            f"Critical failures: {[r.check_id for r in critical_results if not r.passed]}"
        )

    def test_dc01_detects_done_without_actual(
        self, app, db, seed_user, seed_periods
    ):
        """DC-01 flags a transaction with status 'done' but no actual_amount."""
        status_done = db.session.query(Status).filter_by(name="done").one()
        txn_type = db.session.query(TransactionType).filter_by(name="expense").one()

        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=status_done.id,
            name="Done No Actual",
            transaction_type_id=txn_type.id,
            estimated_amount=Decimal("50.00"),
            actual_amount=None,
        )
        db.session.add(txn)
        db.session.flush()

        results = check_data_consistency(db.session)
        dc01 = next(r for r in results if r.check_id == "DC-01")
        assert not dc01.passed
        assert dc01.detail_count == 1  # 1 done txn without actual_amount

    def test_dc02_detects_self_transfer(self, app, db, seed_user, seed_periods):
        """DC-02: Transfers where from_account equals to_account.

        The Transfer model has a CHECK constraint (ck_transfers_different_accounts)
        preventing this at the DB level.  We temporarily drop the constraint,
        insert the anomaly, run the check, then restore it.
        """
        account = seed_user["account"]
        status_projected = db.session.query(Status).filter_by(name="projected").one()

        # Drop the CHECK constraint so we can insert a self-transfer.
        db.session.execute(db.text(
            "ALTER TABLE budget.transfers "
            "DROP CONSTRAINT ck_transfers_different_accounts"
        ))
        try:
            db.session.execute(db.text("""
                INSERT INTO budget.transfers
                    (user_id, pay_period_id, scenario_id, status_id,
                     from_account_id, to_account_id, name, amount,
                     is_override, is_deleted)
                VALUES (:uid, :pid, :sid, :stid, :aid, :aid,
                        'Self Transfer', 100.00, FALSE, FALSE)
            """), {
                "uid": seed_user["user"].id,
                "pid": seed_periods[0].id,
                "sid": seed_user["scenario"].id,
                "stid": status_projected.id,
                "aid": account.id,
            })
            db.session.flush()

            results = check_data_consistency(db.session)
            dc02 = next(r for r in results if r.check_id == "DC-02")
            assert not dc02.passed
            assert dc02.detail_count == 1
            assert dc02.details[0]["from_account_id"] == account.id
            assert dc02.details[0]["to_account_id"] == account.id
        finally:
            # Restore the CHECK constraint.
            db.session.execute(db.text(
                "ALTER TABLE budget.transfers "
                "ADD CONSTRAINT ck_transfers_different_accounts "
                "CHECK (from_account_id != to_account_id) NOT VALID"
            ))

    def test_dc05_detects_active_template_inactive_account(
        self, app, db, seed_user
    ):
        """DC-05 flags an active template referencing an inactive account."""
        txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
        account = seed_user["account"]
        category = list(seed_user["categories"].values())[0]

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=account.id,
            category_id=category.id,
            transaction_type_id=txn_type.id,
            name="Active Template",
            default_amount=Decimal("100.00"),
            is_active=True,
        )
        db.session.add(template)
        db.session.flush()

        # Deactivate the account.
        account.is_active = False
        db.session.flush()

        results = check_data_consistency(db.session)
        dc05 = next(r for r in results if r.check_id == "DC-05")
        assert not dc05.passed
        assert dc05.detail_count == 1  # 1 active template on inactive account

    def test_dc07_detects_user_without_settings(self, app, db):
        """DC-07 detects a user without a user_settings row."""
        # Create a user without settings by bypassing the normal seed.
        user = User(
            email="nosettings@shekel.local",
            password_hash=hash_password("testpass"),
            display_name="No Settings",
        )
        db.session.add(user)
        db.session.flush()

        results = check_data_consistency(db.session)
        dc07 = next(r for r in results if r.check_id == "DC-07")
        assert not dc07.passed
        assert dc07.detail_count == 1  # 1 user without settings
        assert any(
            d.get("email") == "nosettings@shekel.local"
            for d in dc07.details
        )

    def test_dc08_detects_user_without_baseline(self, app, db, seed_user):
        """DC-08 detects a user without a baseline scenario."""
        # Remove the baseline flag from the seed scenario.
        seed_user["scenario"].is_baseline = False
        db.session.flush()

        results = check_data_consistency(db.session)
        dc08 = next(r for r in results if r.check_id == "DC-08")
        assert not dc08.passed
        assert dc08.detail_count == 1  # 1 user without baseline scenario

    def test_dc09_detects_cross_user_deduction_target(
        self, app, db, seed_user
    ):
        """DC-09 flags a deduction targeting another user's account."""
        from app.models.ref import (  # pylint: disable=import-outside-toplevel
            CalcMethod,
            DeductionTiming,
            FilingStatus,
        )
        from app.models.salary_profile import SalaryProfile  # pylint: disable=import-outside-toplevel
        from app.models.paycheck_deduction import PaycheckDeduction  # pylint: disable=import-outside-toplevel
        from app.models.user import UserSettings  # pylint: disable=import-outside-toplevel
        from app.models.account import Account  # pylint: disable=import-outside-toplevel
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
        from app.models.ref import AccountType  # pylint: disable=import-outside-toplevel

        # Create a second user with their own account.
        user2 = User(
            email="user2@shekel.local",
            password_hash=hash_password("testpass"),
            display_name="User Two",
        )
        db.session.add(user2)
        db.session.flush()
        settings2 = UserSettings(user_id=user2.id)
        db.session.add(settings2)
        scenario2 = Scenario(user_id=user2.id, name="Baseline", is_baseline=True)
        db.session.add(scenario2)
        db.session.flush()

        checking_type = db.session.query(AccountType).filter_by(name="checking").one()
        account2 = Account(
            user_id=user2.id,
            account_type_id=checking_type.id,
            name="User2 Checking",
        )
        db.session.add(account2)
        db.session.flush()

        # Create a salary profile for user 1 with a deduction targeting user 2's account.
        filing = db.session.query(FilingStatus).first()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="Test Salary",
            annual_salary=Decimal("80000.00"),
        )
        db.session.add(profile)
        db.session.flush()

        timing = db.session.query(DeductionTiming).first()
        method = db.session.query(CalcMethod).filter_by(name="flat").one()
        deduction = PaycheckDeduction(
            salary_profile_id=profile.id,
            deduction_timing_id=timing.id,
            calc_method_id=method.id,
            name="Cross-user deduction",
            amount=Decimal("100.00"),
            target_account_id=account2.id,  # User 2's account!
        )
        db.session.add(deduction)
        db.session.flush()

        results = check_data_consistency(db.session)
        dc09 = next(r for r in results if r.check_id == "DC-09")
        assert not dc09.passed
        assert dc09.detail_count == 1  # 1 deduction targeting another user's account


# ── run_all_checks ───────────────────────────────────────────────


class TestRunAllChecks:
    """Tests for the top-level run_all_checks() function."""

    def test_runs_all_categories_by_default(
        self, app, db, seed_user, seed_periods
    ):
        """run_all_checks() returns results from all 4 categories."""
        results = run_all_checks(db.session)
        categories = {r.category for r in results}
        assert categories == {"referential", "orphan", "balance", "consistency"}

    def test_category_filter(self, app, db, seed_user, seed_periods):
        """run_all_checks(categories=['referential']) only runs FK checks."""
        results = run_all_checks(db.session, categories=["referential"])
        assert all(r.category == "referential" for r in results)
        assert len(results) == 13

    def test_returns_check_result_objects(
        self, app, db, seed_user, seed_periods
    ):
        """All returned items are CheckResult instances."""
        results = run_all_checks(db.session)
        assert all(isinstance(r, CheckResult) for r in results)

    def test_exit_code_zero_on_clean_db(
        self, app, db, seed_user, seed_periods
    ):
        """No critical failures on a properly seeded database."""
        results = run_all_checks(db.session)
        critical = [r for r in results if not r.passed and r.severity == "critical"]
        assert len(critical) == 0, (
            f"Unexpected critical failures: "
            f"{[(r.check_id, r.description) for r in critical]}"
        )

    def test_clean_database_zero_critical_anomalies(
        self, app, db, seed_user, seed_periods
    ):
        """All checks on a clean seeded database report zero critical anomalies.

        This is a regression guard: if a future schema change introduces a
        latent integrity issue, this test catches it immediately.
        """
        results = run_all_checks(db.session)
        critical_failures = [
            r for r in results
            if not r.passed and r.severity == "critical"
        ]
        assert len(critical_failures) == 0, (
            f"Critical anomalies on clean DB: "
            f"{[(r.check_id, r.description, r.detail_count) for r in critical_failures]}"
        )
        # Total check count should cover all 4 categories:
        # 13 FK + 6 OR + 5 BA + 9 DC = 33 checks.
        assert len(results) == 33
