"""
Shekel Budget App - Fixture Validation Tests

Validates that the two-user isolation test fixtures create correct,
independent data. Catches fixture bugs before they cascade into
20+ failures in WU-4 and WU-5.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.exceptions import RecurrenceWindowError
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services import cash_ledger
from tests._test_helpers import (
    bare_expense_template,
    definition_firing_twice_in_a_paycheck,
    generate_row_of,
    make_cadence_rule,
    make_expense_template,
)
from tests.conftest import SEED_USER_BOOTSTRAP_START
from tests.oracles.recurrence_baseline import MONTHLY


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
        assert seed_user["account"].account_type.name == "Checking"
        assert seed_second_user["account"].account_type.name == "Checking"
        assert cash_ledger.resolve_anchor(
            seed_second_user["account"],
        ).balance == Decimal("2000.00")

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

    def test_the_anchor_assertion_is_the_owners_own_bootstrap_day(
        self, db, seed_second_user, seed_second_periods,
    ):
        """The second user's account resolves an anchor, dated where it was written.

        It read ``account.current_anchor_period_id == seed_second_periods[0].id``
        until plan step X-f1c3c deleted that column, and then "the origination
        assertion is dated inside the first period" until plan step X-f3c-2c
        made ``budget.account_anchor_history`` append-only.  The periods fixture
        used to re-point that row onto the calendar it built; a fixture PLACES
        an assertion now and never edits one, so the account asserts where
        ``account_service.create_account`` wrote it -- the owner's bootstrap
        day, which precedes the calendar.

        **That is a production shape rather than a fixture artefact**: an
        account whose books open before the budget does is exactly what finding
        **N-368**'s bank import creates for the developer's own Checking.  What
        the producers key on is unchanged and is what is pinned here -- the
        account resolves an anchor at all, and it is the row that was written
        for it.
        """
        account = db.session.get(Account, seed_second_user["account"].id)
        anchor = cash_ledger.resolve_anchor(account)
        assert anchor.observed_on == SEED_USER_BOOTSTRAP_START
        # The BALANCE too, because the day alone would still be pinned by a
        # fixture that had lost the row and grown a new one: this is the
        # ``$2,000.00`` ``build_seed_second_user`` types, unchanged.
        assert anchor.balance == Decimal("2000.00")


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
        """Logging out User B does not affect User A's session.

        Note: Flask test clients sharing a single app may have session
        interference.  The test verifies the second client's logout
        doesn't cause an error, and that the first client can re-authenticate.
        """
        second_auth_client.post("/logout")

        # Re-login the first user if the shared test app session was
        # invalidated by the second client's logout.
        resp = auth_client.get("/settings")
        if resp.status_code == 302:
            auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
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
        """All monetary values are Decimal, not float.

        The transaction is the ENGINE's row since plan step balance:X-cf, so
        it stores no figure of its own and its worth is what the amount model
        resolves from its definition's series -- which is the figure this
        asserts the type of, rather than the raw column that is ``None`` on
        every derived row.
        """
        data = seed_full_user_data
        assert isinstance(data["template"].default_amount, Decimal)
        assert isinstance(
            cash_ledger.resolve_transaction_amount(
                data["transaction"],
                cash_ledger.amount_basis(data["user"].id, data["scenario"].id),
            ),
            Decimal,
        )
        assert isinstance(data["savings_goal"].target_amount, Decimal)
        assert isinstance(data["transfer_template"].default_amount, Decimal)
        assert isinstance(data["salary_profile"].annual_salary, Decimal)
        assert isinstance(
            cash_ledger.resolve_anchor(data["account"]).balance, Decimal,
        )


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
        """Monetary amounts differ between users for isolation test visibility.

        Each transaction is its owner's ENGINE row (plan step balance:X-cf),
        priced by its own definition's series, so the two are compared as the
        amount model resolves them rather than through a raw column that is
        ``None`` on both -- which would have read as "equal" and passed nothing.
        """
        a = seed_full_user_data
        b = seed_full_second_user_data

        assert a["template"].default_amount != b["template"].default_amount
        assert cash_ledger.resolve_transaction_amount(
            a["transaction"],
            cash_ledger.amount_basis(a["user"].id, a["scenario"].id),
        ) != cash_ledger.resolve_transaction_amount(
            b["transaction"],
            cash_ledger.amount_basis(b["user"].id, b["scenario"].id),
        )
        assert a["savings_goal"].target_amount != b["savings_goal"].target_amount
        assert (
            cash_ledger.resolve_anchor(a["account"]).balance
            != cash_ledger.resolve_anchor(b["account"]).balance
        )


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


class TestGenerateRowOf:
    """The suite's one builder for a row of a definition (plan step X-cf).

    It hands back what the ENGINE wrote, so these grade that the row has the
    engine's shape, that it is priced by its definition, and that each of the
    builder's refusals fires on the cause its message names -- no cadence, a
    paycheck the rule names no occurrence in, a paycheck a row already claims,
    a cadence firing twice in one paycheck, and another owner's paycheck -- a
    helper's refusal being code a green suite never runs otherwise.
    """

    def test_the_row_is_the_engines(self, app, db, seed_user, seed_periods):
        """Derived, dated, answering an occurrence, Projected, not overridden.

        Every column here is one the engine sets and a hand-built fixture used
        to get wrong or leave empty: undated (the state the CHECK plan step
        balance:X-bv-2 binds refuses), and the pre-X-au-e shape of an OWN
        figure with ``is_override=False``.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            row = generate_row_of(template, seed_periods[1])
            assert row.id is not None
            assert row.template_id == template.id
            assert row.pay_period_id == seed_periods[1].id
            assert row.scenario_id == seed_user["scenario"].id
            assert row.due_date is not None
            assert row.occurs_on is not None
            assert row.is_override is False
            assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert row.amount_ownership.figure is None
            assert row.amount_ownership.source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )

    def test_the_row_is_worth_what_its_definition_states(
        self, app, db, seed_user, seed_periods,
    ):
        """The figure a fixture expects is the template's series' answer."""
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="1234.56",
            )
            row = generate_row_of(template, seed_periods[0])
            assert cash_ledger.resolve_transaction_amount(
                row,
                cash_ledger.amount_basis(
                    seed_user["user"].id, seed_user["scenario"].id,
                ),
            ) == Decimal("1234.56")

    def test_a_definition_with_no_cadence_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A template that does not repeat has no row for the engine to write."""
        with app.app_context():
            template = bare_expense_template(db.session, seed_user)
            with pytest.raises(ValueError, match="has no cadence"):
                generate_row_of(template, seed_periods[0])

    def test_a_second_row_in_the_same_paycheck_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The engine writes nothing where a row already claims the occurrence."""
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            generate_row_of(template, seed_periods[0])
            with pytest.raises(ValueError, match="wrote 0 rows"):
                generate_row_of(template, seed_periods[0])

    def test_another_owners_paycheck_is_refused_before_any_write(
        self, app, db, seed_user, seed_periods, seed_second_periods,
    ):
        """A period outside the owner's calendar never reaches the engine.

        The refusal is the schedule's, one tier above the composite key
        ``fk_transactions_owner_period`` that would refuse the INSERT -- so a
        fixture cannot even ask for the cross-owner row a hand-built one used
        to have to be refused by the database.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            with pytest.raises(RecurrenceWindowError):
                generate_row_of(template, seed_second_periods[0])
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == 0

    def test_a_paycheck_the_rule_names_no_occurrence_in_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A monthly rule that starts AFTER the paycheck fires nowhere in it.

        The 0-rows refusal's other cause, distinct from an already-claimed
        paycheck: nothing was written before and nothing is written now.
        """
        with app.app_context():
            template = bare_expense_template(db.session, seed_user)
            make_cadence_rule(
                template, MONTHLY, starts_on=seed_periods[5].start_date,
            )
            with pytest.raises(ValueError, match="wrote 0 rows"):
                generate_row_of(template, seed_periods[0])
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == 0

    def test_a_cadence_firing_twice_in_one_paycheck_is_refused(
        self, app, db, seed_user,
    ):
        """A monthly rule inside a 60-day paycheck names two occurrences.

        The engine writes both -- a paycheck CAN hold two rows of one
        definition (plan step R17) -- and the builder refuses to pick, because
        which of the two a test means is a generation test's subject.  The two
        rows stay flushed, as the docstring says they do.  The paycheck is
        :func:`definition_firing_twice_in_a_paycheck`'s, pinned to a date: on
        a today-relative calendar this case read THREE rows every summer.
        """
        with app.app_context():
            template, _first, period = definition_firing_twice_in_a_paycheck(
                db.session, seed_user, name="Cadence Under Test",
            )
            with pytest.raises(ValueError, match="wrote 2 rows"):
                generate_row_of(template, period)
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == 2

    def test_the_pinned_definition_fires_ONCE_in_the_paycheck_before(
        self, app, db, seed_user,
    ):
        """The same definition's first paycheck holds exactly one occurrence.

        The other half of :func:`definition_firing_twice_in_a_paycheck`'s
        promise (plan step balance:X-cf-3b): its rule starts on 2026-04-01,
        inside a paycheck opening 2026-03-02, so that paycheck holds the 1st
        of April and not the 1st of March -- one row, which is what a case
        rolling a leftover INTO the pair takes as its source.  Graded on the
        occurrence the row answers as well as on the count, because the count
        alone cannot tell the 1st from a rule that started mid-April and
        named some other day once here and twice in May.
        """
        with app.app_context():
            template, first, _second = definition_firing_twice_in_a_paycheck(
                db.session, seed_user, name="Cadence Under Test",
            )
            row = generate_row_of(template, first)
            assert row.pay_period_id == first.id
            assert row.occurs_on == date(2026, 4, 1)
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == 1
