"""Route tests for plan step credit_card:CC-5-5d -- goals on a debt, end to end.

What a browser sends and sees: the goal form marks a debt, both goal saves go
through ONE door (``app.services.savings_goal_door``), /savings and the budget
dashboard caption a debt goal as a milestone to get UNDER, and re-typing an
account between savings and debt is refused while a goal is on it (ruling
R-CC87).  The door's arithmetic is graded in
``tests/test_services/test_debt_goals.py``; this grades the doors a request
reaches.

Every create POSTs what ``savings/goal_form.html`` emits for a debt: the mode
select carries the Fixed id, the income fields are empty selects/inputs, and
the per-period contribution is DISABLED by ``goal_mode_toggle.js`` (ruling
R-CC90), so a browser does not send it.
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AcctCategoryEnum, GoalModeEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.services import account_service, balance_at, savings_goal_door
from app.services.balance_at import BalanceContext
from app.utils.account_validation import GOAL_BLOCKS_DEBT_CROSSING
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    freeze_today,
)

#: Inside ``seed_periods`` (2026-01-02 .. 2026-05-21), so a tile has a period.
_TODAY = date(2026, 3, 20)


@pytest.fixture(autouse=True)
def _today_inside_the_seed_calendar(monkeypatch):
    """Freeze today inside the seeded pay periods (the file's clock)."""
    freeze_today(monkeypatch, _TODAY)


def _car_loan(seed_user):
    """A configured 0% loan: $12,000 over 24 months from 2026-01-01.

    Nothing is paid by the frozen today, so it owes exactly $12,000.00 -- a
    figure worked by hand, which the render assertions below compare against.
    """
    return create_loan_account(
        seed_user, db.session, name="Car Loan",
        principal=Decimal("12000.00"), rate=Decimal("0.00000"),
        term=24, origination_date=date(2026, 1, 1),
    )


def _owed_today(seed_user, loan):
    """What the loan owes today, from the loan domain's own producer."""
    ctx = BalanceContext.build(seed_user["user"].id)
    owed = balance_at.positions(loan, ctx, [ctx.as_of])[ctx.as_of]
    assert owed == Decimal("12000.00"), "the fixture's hand-worked figure"
    return owed


def _debt_form(account, target, name="Car under target"):
    """What ``goal_form.html`` submits for a debt (the contribution disabled)."""
    return {
        "account_id": str(account.id),
        "name": name,
        "goal_mode_id": str(ref_cache.goal_mode_id(GoalModeEnum.FIXED)),
        "target_amount": target,
        "income_unit_id": "",
        "income_multiplier": "",
        "target_date": "",
    }


def _zero_anchor_savings(seed_user, name):
    """A Savings account with a $0 opening: an empty ledger, so re-typable."""
    savings_type = (
        db.session.query(AccountType)
        .filter_by(name="Savings", user_id=None)
        .one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name=name,
            anchor_balance=Decimal("0.00"),
        ),
    )
    db.session.commit()
    return account


def _savings_goal_on(seed_user, account, name="Rainy day"):
    """A $5,000 savings goal on *account*."""
    goal = SavingsGoal(
        user_id=seed_user["user"].id,
        account_id=account.id,
        name=name,
        target_amount=Decimal("5000.00"),
    )
    db.session.add(goal)
    db.session.commit()
    return goal


class TestTheGoalFormMarksADebt:
    """The form names which accounts are debts, so its script can relabel."""

    def test_a_debt_option_carries_the_debt_flag(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The loan's option says ``data-is-debt="true"``, the Checking's ``false``."""
        with app.app_context():
            loan = _car_loan(seed_user)

            html = auth_client.get("/savings/goals/new").get_data(as_text=True)

            assert f'value="{loan.id}"\n            data-is-debt="true"' in html
            checking = seed_user["account"].id
            assert (
                f'value="{checking}"\n            data-is-debt="false"' in html
            )
            assert 'data-debt-label="Owe Less Than"' in html


class TestADebtGoalSave:
    """Both goal routes reach the ONE door."""

    def test_a_target_at_what_the_debt_owes_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """At what it owes today, the create refuses and writes nothing."""
        with app.app_context():
            loan = _car_loan(seed_user)
            owed = _owed_today(seed_user, loan)

            resp = auth_client.post(
                "/savings/goals", data=_debt_form(loan, f"{owed:.2f}"),
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"must target less than it owes today" in resp.data
            assert db.session.query(SavingsGoal).filter_by(
                account_id=loan.id,
            ).count() == 0

    def test_a_target_under_it_and_a_zero_target_are_created(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A cent under what it owes, and ``$0.00`` (ruling R-CC72), both save."""
        with app.app_context():
            loan = _car_loan(seed_user)
            owed = _owed_today(seed_user, loan)

            for name, target in (
                ("Under it", f"{owed - Decimal('0.01'):.2f}"),
                ("Paid off", "0.00"),
            ):
                resp = auth_client.post(
                    "/savings/goals", data=_debt_form(loan, target, name=name),
                    follow_redirects=True,
                )
                assert resp.status_code == 200
                assert b"created" in resp.data

            targets = {
                goal.name: goal.target_amount
                for goal in db.session.query(SavingsGoal).filter_by(
                    account_id=loan.id,
                )
            }
            assert targets == {
                "Under it": owed - Decimal("0.01"),
                "Paid off": Decimal("0.00"),
            }

    def test_a_zero_savings_target_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Ruling R-CC72's other half, through the create route."""
        with app.app_context():
            resp = auth_client.post(
                "/savings/goals",
                data=_debt_form(seed_user["account"], "0.00", name="Nothing"),
                follow_redirects=True,
            )

            assert b"target must be above $0.00" in resp.data
            assert db.session.query(SavingsGoal).filter_by(
                name="Nothing",
            ).count() == 0

    def test_an_edit_cannot_move_a_savings_goal_onto_a_debt(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Ruling R-CC87 through the edit route: refused, the goal unmoved."""
        with app.app_context():
            loan = _car_loan(seed_user)
            goal = _savings_goal_on(seed_user, seed_user["account"])

            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "account_id": str(loan.id),
                "name": goal.name,
                "goal_mode_id": str(ref_cache.goal_mode_id(GoalModeEnum.FIXED)),
                "target_amount": "5000.00",
                "income_unit_id": "",
                "income_multiplier": "",
                "target_date": "",
                "contribution_per_period": "",
                "version_id": str(goal.version_id),
            }, follow_redirects=True)

            assert savings_goal_door.MOVE_REFUSED.encode() in resp.data
            db.session.expire_all()
            assert db.session.get(SavingsGoal, goal.id).account_id == (
                seed_user["account"].id
            )


class TestAGoalEdit:
    """What an edit can and cannot change."""

    def test_a_card_goal_created_through_the_route_records_its_start(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Ruling R-CC91: a card owing $750.00 records start 750.00; a loan goal none."""
        with app.app_context():
            visa = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-750.00"),
                observed_on=seed_periods[5].start_date,
            )
            loan = _car_loan(seed_user)
            db.session.commit()

            for account, name in ((visa, "Visa down"), (loan, "Loan down")):
                auth_client.post(
                    "/savings/goals",
                    data=_debt_form(account, "100.00", name=name),
                    follow_redirects=True,
                )

            starts = {
                goal.name: goal.start_owed
                for goal in db.session.query(SavingsGoal)
            }
            assert starts == {
                "Visa down": Decimal("750.00"), "Loan down": None,
            }

    def test_an_edit_cannot_bring_a_deleted_goal_back(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Review M1: ``is_active`` is not an edit field; delete is its only writer."""
        with app.app_context():
            goal = _savings_goal_on(seed_user, seed_user["account"])
            goal.is_active = False
            db.session.commit()

            auth_client.post(f"/savings/goals/{goal.id}", data={
                "account_id": str(seed_user["account"].id),
                "name": goal.name,
                "goal_mode_id": str(ref_cache.goal_mode_id(GoalModeEnum.FIXED)),
                "target_amount": "5000.00",
                "income_unit_id": "",
                "income_multiplier": "",
                "target_date": "",
                "contribution_per_period": "",
                "version_id": str(goal.version_id),
                "is_active": "true",
            }, follow_redirects=True)

            db.session.expire_all()
            assert db.session.get(SavingsGoal, goal.id).is_active is False

    def test_the_edit_page_deletes_the_goal(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Ruling R-CC94: the edit page carries a confirmed Delete form; it deactivates."""
        with app.app_context():
            goal = _savings_goal_on(seed_user, seed_user["account"])

            html = auth_client.get(
                f"/savings/goals/{goal.id}/edit",
            ).get_data(as_text=True)
            action = f'action="/savings/goals/{goal.id}/delete"'
            assert action in html
            form = html.split(action, 1)[1].split("</form>", 1)[0]
            assert 'data-confirm="Delete the goal' in form
            assert 'name="csrf_token"' in form

            auth_client.post(
                f"/savings/goals/{goal.id}/delete", follow_redirects=True,
            )
            db.session.expire_all()
            assert db.session.get(SavingsGoal, goal.id).is_active is False

    def test_the_edit_form_offers_what_the_door_accepts(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A debt goal's form lists its own debt only; a savings goal's, savings
        accounts plus its own even when archived (review L1/L10)."""
        with app.app_context():
            loan = _car_loan(seed_user)
            archived = _zero_anchor_savings(seed_user, "Old Pocket")
            savings_goal = _savings_goal_on(seed_user, archived)
            archived.is_active = False
            debt_goal = SavingsGoal(
                user_id=seed_user["user"].id, account_id=loan.id,
                name="Loan down", target_amount=Decimal("10000.00"),
            )
            db.session.add(debt_goal)
            db.session.commit()

            def offered(goal):
                html = auth_client.get(
                    f"/savings/goals/{goal.id}/edit",
                ).get_data(as_text=True)
                select = html.split('name="account_id"', 1)[1].split(
                    "</select>", 1,
                )[0]
                return set(re.findall(r'value="(\d+)"', select))

            assert offered(debt_goal) == {str(loan.id)}
            savings_offer = offered(savings_goal)
            assert str(archived.id) in savings_offer
            assert str(seed_user["account"].id) in savings_offer
            assert str(loan.id) not in savings_offer


class TestADebtGoalRenders:
    """/savings and the budget dashboard caption a debt goal as one to get under."""

    def test_savings_shows_what_it_owes_and_the_start(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The debt block: $12,000 owed, under $10,000, 0% paid down from $12,000.

        The figures are the OWED sign (``shown_balance``); the held figure
        would render ``-$12,000``.
        """
        with app.app_context():
            loan = _car_loan(seed_user)
            _owed_today(seed_user, loan)
            auth_client.post(
                "/savings/goals", data=_debt_form(loan, "10000.00"),
                follow_redirects=True,
            )

            html = auth_client.get("/savings").get_data(as_text=True)

            assert "Car under target" in html
            assert '<span class="font-mono">$12,000</span> owed' in html
            assert 'goal: under <span class="font-mono">$10,000</span>' in html
            assert "0% paid down from\n          " in html
            assert '<span class="font-mono">$12,000</span> when set' in html
            assert "-$12,000" not in html.split("Car under target", 1)[1][:3000]
            assert "Projected under target:" in html

    def test_the_budget_dashboard_track_says_owed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The metro track: "$12,000.00" then "owed · goal under $10,000.00"."""
        with app.app_context():
            loan = _car_loan(seed_user)
            _owed_today(seed_user, loan)
            auth_client.post(
                "/savings/goals", data=_debt_form(loan, "10000.00"),
                follow_redirects=True,
            )

            html = auth_client.get("/").get_data(as_text=True)

            assert (
                '<span class="track__pos-num font-mono">$12,000.00</span>'
            ) in html
            assert "owed &middot; goal under $10,000.00" in html


class TestARetypeUnderAGoalIsRefused:
    """Ruling R-CC87 through the account's type: both vectors."""

    def test_retyping_an_account_a_goal_is_on_into_a_debt_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An empty-ledger Savings account re-types to Credit Card only without a goal."""
        with app.app_context():
            account = _zero_anchor_savings(seed_user, "Pocket")
            _savings_goal_on(seed_user, account)
            savings_type_id = account.account_type_id
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None)
                .one()
            )

            resp = auth_client.post(
                f"/accounts/{account.id}",
                data={"account_type_id": str(cc_type.id)},
                follow_redirects=True,
            )

            assert GOAL_BLOCKS_DEBT_CROSSING[0].encode() in resp.data
            db.session.expire_all()
            assert db.session.get(Account, account.id).account_type_id == (
                savings_type_id
            )

    def test_retyping_a_card_goals_account_to_a_loan_type_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Ruling R-CC91: a card-style debt cannot become a loan type under its goal."""
        with app.app_context():
            card_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None)
                .one()
            )
            auto_type = (
                db.session.query(AccountType)
                .filter_by(name="Auto Loan", user_id=None)
                .one()
            )
            card = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=card_type.id,
                    name="Store Card",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(SavingsGoal(
                user_id=seed_user["user"].id, account_id=card.id,
                name="Store down", target_amount=Decimal("50.00"),
                start_owed=Decimal("100.00"),
            ))
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{card.id}",
                data={"account_type_id": str(auto_type.id)},
                follow_redirects=True,
            )

            assert GOAL_BLOCKS_DEBT_CROSSING[0].encode() in resp.data
            db.session.expire_all()
            assert db.session.get(Account, card.id).account_type_id == (
                card_type.id
            )

    def test_flipping_a_custom_types_category_under_a_goal_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Editing the type in place is the second door to the same crossing."""
        with app.app_context():
            custom_type = AccountType(
                name="side_pocket",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=custom_type.id,
                    name="Unposted Pocket",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.commit()
            _savings_goal_on(seed_user, account)
            asset_id = custom_type.category_id

            resp = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={"category_id": str(ref_cache.acct_category_id(
                    AcctCategoryEnum.LIABILITY,
                ))},
                follow_redirects=True,
            )

            assert GOAL_BLOCKS_DEBT_CROSSING[0].encode() in resp.data
            db.session.expire_all()
            assert db.session.get(AccountType, custom_type.id).category_id == (
                asset_id
            )
