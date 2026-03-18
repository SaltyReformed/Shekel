"""
Shekel Budget App - Fixture Validation Tests

Validates that the two-user isolation test fixtures create correct,
independent data. Catches fixture bugs before they cascade into
20+ failures in WU-4 and WU-5.
"""

from decimal import Decimal

from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings


class TestSeedSecondUser:
    """Validate the seed_second_user fixture."""

    def test_creates_independent_user(self, seed_user, seed_second_user):
        """Second user is a distinct User object with correct attributes."""
        assert seed_user["user"].id != seed_second_user["user"].id
        assert seed_user["user"].email != seed_second_user["user"].email
        assert seed_second_user["user"].email == "second@shekel.local"
        assert seed_second_user["user"].display_name == "Second User"

    def test_has_own_settings(self, db, seed_second_user):
        """Second user has exactly one UserSettings row."""
        rows = (
            db.session.query(UserSettings)
            .filter_by(user_id=seed_second_user["user"].id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == seed_second_user["settings"].id

    def test_has_own_account(self, seed_user, seed_second_user):
        """Second user has a distinct checking account with correct balance."""
        assert seed_user["account"].id != seed_second_user["account"].id
        assert seed_user["account"].account_type.name == "checking"
        assert seed_second_user["account"].account_type.name == "checking"
        assert seed_second_user["account"].current_anchor_balance == Decimal("2000.00")

    def test_has_own_scenario(self, seed_user, seed_second_user):
        """Second user has a distinct baseline scenario."""
        assert seed_user["scenario"].id != seed_second_user["scenario"].id
        assert seed_user["scenario"].is_baseline is True
        assert seed_second_user["scenario"].is_baseline is True
        assert seed_user["scenario"].user_id != seed_second_user["scenario"].user_id

    def test_has_own_categories(self, db, seed_user, seed_second_user):
        """Second user has 5 categories, none shared with first user."""
        user_a_cat_ids = {c.id for c in seed_user["categories"].values()}
        user_b_cat_ids = {c.id for c in seed_second_user["categories"].values()}
        assert len(user_a_cat_ids) == 5
        assert len(user_b_cat_ids) == 5
        assert user_a_cat_ids.isdisjoint(user_b_cat_ids)

        # Verify DB-level ownership.
        user_b_db_cats = (
            db.session.query(Category)
            .filter_by(user_id=seed_second_user["user"].id)
            .all()
        )
        assert len(user_b_db_cats) == 5

    def test_no_shared_foreign_keys(self, seed_user, seed_second_user):
        """No object from one user references the other user's ID."""
        assert seed_user["account"].user_id != seed_second_user["user"].id
        assert seed_second_user["account"].user_id != seed_user["user"].id
        assert seed_user["scenario"].user_id == seed_user["user"].id
        assert seed_second_user["scenario"].user_id == seed_second_user["user"].id


class TestSeedSecondPeriods:
    """Validate the seed_second_periods fixture."""

    def test_creates_10_periods(self, seed_second_periods):
        """Fixture creates exactly 10 pay periods."""
        assert len(seed_second_periods) == 10

    def test_periods_belong_to_second_user(self, seed_second_user, seed_second_periods):
        """Every period belongs to the second user."""
        user_b_id = seed_second_user["user"].id
        for period in seed_second_periods:
            assert period.user_id == user_b_id

    def test_periods_independent_from_first_user(
        self, seed_user, seed_periods, seed_second_user, seed_second_periods
    ):
        """No period ID appears in both users' period sets."""
        user_a_ids = {p.id for p in seed_periods}
        user_b_ids = {p.id for p in seed_second_periods}
        assert user_a_ids.isdisjoint(user_b_ids)

    def test_anchor_period_set(self, db, seed_second_user, seed_second_periods):
        """The second user's account anchor points to the first period."""
        account = db.session.get(Account, seed_second_user["account"].id)
        assert account.current_anchor_period_id == seed_second_periods[0].id


class TestSecondAuthClient:
    """Validate the second_auth_client fixture."""

    def test_second_client_is_authenticated(self, seed_second_user, second_auth_client):
        """Second client can access protected pages."""
        resp = second_auth_client.get("/settings")
        assert resp.status_code == 200

    def test_second_client_is_different_user(
        self, seed_user, auth_client, seed_second_user, second_auth_client
    ):
        """Both clients are authenticated simultaneously."""
        resp_a = auth_client.get("/settings")
        resp_b = second_auth_client.get("/settings")
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

    def test_second_client_independent_session(
        self, seed_user, auth_client, seed_second_user, second_auth_client
    ):
        """Logging out User B does not affect User A's session."""
        second_auth_client.post("/logout")
        resp = auth_client.get("/settings")
        assert resp.status_code == 200


class TestSeedFullUserData:
    """Validate the seed_full_user_data fixture."""

    def test_contains_all_expected_keys(self, seed_full_user_data):
        """Returned dict contains all expected keys with non-None values."""
        expected_keys = {
            "user", "settings", "account", "scenario", "categories",
            "periods", "template", "transaction", "savings_goal",
            "recurrence_rule", "savings_account", "transfer_template",
            "salary_profile",
        }
        assert set(seed_full_user_data.keys()) >= expected_keys
        for key in expected_keys:
            assert seed_full_user_data[key] is not None, f"{key} is None"

    def test_template_belongs_to_user(self, seed_full_user_data):
        """Transaction template belongs to the correct user."""
        data = seed_full_user_data
        assert data["template"].user_id == data["user"].id

    def test_transaction_in_first_period(self, seed_full_user_data):
        """Transaction is placed in the first pay period."""
        data = seed_full_user_data
        assert data["transaction"].pay_period_id == data["periods"][0].id

    def test_transaction_linked_to_template(self, seed_full_user_data):
        """Transaction is linked to its template."""
        data = seed_full_user_data
        assert data["transaction"].template_id == data["template"].id

    def test_savings_goal_belongs_to_user(self, seed_full_user_data):
        """Savings goal belongs to the correct user."""
        data = seed_full_user_data
        assert data["savings_goal"].user_id == data["user"].id

    def test_transfer_template_accounts_valid(self, seed_full_user_data):
        """Transfer template references two distinct accounts."""
        data = seed_full_user_data
        assert data["transfer_template"].from_account_id == data["account"].id
        assert data["transfer_template"].to_account_id == data["savings_account"].id
        assert (
            data["transfer_template"].from_account_id
            != data["transfer_template"].to_account_id
        )

    def test_salary_profile_belongs_to_user(self, seed_full_user_data):
        """Salary profile belongs to the correct user and scenario."""
        data = seed_full_user_data
        assert data["salary_profile"].user_id == data["user"].id
        assert data["salary_profile"].scenario_id == data["scenario"].id

    def test_all_amounts_are_decimal(self, seed_full_user_data):
        """All monetary values are Decimal, not float."""
        data = seed_full_user_data
        assert isinstance(data["template"].default_amount, Decimal)
        assert isinstance(data["transaction"].estimated_amount, Decimal)
        assert isinstance(data["savings_goal"].target_amount, Decimal)
        assert isinstance(data["transfer_template"].default_amount, Decimal)
        assert isinstance(data["salary_profile"].annual_salary, Decimal)
        assert isinstance(data["account"].current_anchor_balance, Decimal)


class TestSeedFullSecondUserData:
    """Validate the seed_full_second_user_data fixture."""

    def test_contains_all_expected_keys(self, seed_full_second_user_data):
        """Returned dict contains all expected keys with non-None values."""
        expected_keys = {
            "user", "settings", "account", "scenario", "categories",
            "periods", "template", "transaction", "savings_goal",
            "recurrence_rule", "savings_account", "transfer_template",
            "salary_profile",
        }
        assert set(seed_full_second_user_data.keys()) >= expected_keys
        for key in expected_keys:
            assert seed_full_second_user_data[key] is not None, f"{key} is None"

    def test_no_shared_objects_between_users(
        self, seed_full_user_data, seed_full_second_user_data
    ):
        """Every object ID is unique across users."""
        a = seed_full_user_data
        b = seed_full_second_user_data

        assert a["user"].id != b["user"].id
        assert a["account"].id != b["account"].id
        assert a["savings_account"].id != b["savings_account"].id
        assert a["scenario"].id != b["scenario"].id
        assert a["template"].id != b["template"].id
        assert a["transaction"].id != b["transaction"].id
        assert a["savings_goal"].id != b["savings_goal"].id
        assert a["transfer_template"].id != b["transfer_template"].id
        assert a["salary_profile"].id != b["salary_profile"].id

        period_ids_a = {p.id for p in a["periods"]}
        period_ids_b = {p.id for p in b["periods"]}
        assert period_ids_a.isdisjoint(period_ids_b)

    def test_distinguishable_names(
        self, seed_full_user_data, seed_full_second_user_data
    ):
        """Names differ between users for isolation test visibility."""
        a = seed_full_user_data
        b = seed_full_second_user_data

        assert a["template"].name != b["template"].name
        assert a["transaction"].name != b["transaction"].name
        assert a["savings_goal"].name != b["savings_goal"].name
        assert a["transfer_template"].name != b["transfer_template"].name
        assert a["salary_profile"].name != b["salary_profile"].name

    def test_distinguishable_amounts(
        self, seed_full_user_data, seed_full_second_user_data
    ):
        """Monetary amounts differ between users for isolation test visibility."""
        a = seed_full_user_data
        b = seed_full_second_user_data

        assert a["template"].default_amount != b["template"].default_amount
        assert a["transaction"].estimated_amount != b["transaction"].estimated_amount
        assert a["savings_goal"].target_amount != b["savings_goal"].target_amount
        assert a["account"].current_anchor_balance != b["account"].current_anchor_balance


class TestBothFullFixturesTogether:
    """Validate that both full fixtures can coexist in a single test."""

    def test_both_fixtures_coexist(
        self, db, seed_full_user_data, seed_full_second_user_data
    ):
        """Both fixtures load without FK conflicts or unique violations."""
        user_count = db.session.query(User).count()
        assert user_count == 2

        account_count = db.session.query(Account).count()
        assert account_count >= 4  # 2 checking + 2 savings

        template_count = db.session.query(TransactionTemplate).count()
        assert template_count == 2

    def test_database_isolation_query(
        self, db, seed_full_user_data, seed_full_second_user_data
    ):
        """The query pattern used by the grid route returns only the correct user's data."""
        data_a = seed_full_user_data
        data_b = seed_full_second_user_data

        # Query User A's transactions via pay periods.
        user_a_id = data_a["user"].id
        user_a_period_ids = [
            p.id for p in db.session.query(PayPeriod)
            .filter_by(user_id=user_a_id).all()
        ]
        user_a_txns = (
            db.session.query(Transaction)
            .filter(Transaction.pay_period_id.in_(user_a_period_ids))
            .all()
        )
        user_a_txn_names = {t.name for t in user_a_txns}
        assert data_a["transaction"].name in user_a_txn_names
        assert data_b["transaction"].name not in user_a_txn_names

        # Query User B's transactions via pay periods.
        user_b_id = data_b["user"].id
        user_b_period_ids = [
            p.id for p in db.session.query(PayPeriod)
            .filter_by(user_id=user_b_id).all()
        ]
        user_b_txns = (
            db.session.query(Transaction)
            .filter(Transaction.pay_period_id.in_(user_b_period_ids))
            .all()
        )
        user_b_txn_names = {t.name for t in user_b_txns}
        assert data_b["transaction"].name in user_b_txn_names
        assert data_a["transaction"].name not in user_b_txn_names
