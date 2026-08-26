"""
Shekel Budget App -- Account Route Tests

Tests for account CRUD, anchor balance true-up, and account type
management endpoints (§2.1 of the test plan).
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import (
    AcctCategoryEnum,
    CompoundingFrequencyEnum,
    EmployerContributionTypeEnum,
    SettlementBasisEnum,
    StatusEnum,
)
from app.exceptions import RequiredRecordMissing
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.utils.dates import display_today
from tests._test_helpers import (
    all_periods,
    an_entered_day,
    append_balance_assertion,
    create_account_of_type,
    create_loan_account,
    create_transfer,
    current_pay_period,
    settle_day_columns,
    settle_instant_on,
    settlement_basis_id,
    settlement_if_settling,
)
from app.models.interest_params import InterestParams
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.models.investment_params import InvestmentParams
from app.models.user import User, UserSettings
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    account_service,
    balance_at,
    cash_ledger,
    pay_period_service,
    pay_period_write,
    status_seam,
)
from app.services.auth_service import hash_password
from app.services.row_valuation import owned_contribution, settled_figure
from app.services.settle_day import record_settle_day


#: The out-of-band swap that carries the balance acknowledgement into
#: ``base.html``'s single mount.  Spelled once because three tests slice the
#: response on it: the swap STYLE is load-bearing, not incidental -- an
#: outerHTML swap of the mount destroys a still-visible predecessor, which is
#: finding N-206, so a test that matched only the mount NAME would keep passing
#: through the regression this constant exists to catch.
_ACK_SWAP = 'hx-swap-oob="beforeend:#anchor-ack-mount"'


def _create_other_user_account():
    """Create a second user with their own account.

    Pay periods are generated before the account so the canonical
    account factory (E-19, Commit 3) can resolve / receive an anchor
    period without raising ``ValidationError``.

    Returns:
        dict with keys: user, account.
    """
    from datetime import date, timedelta  # pylint: disable=import-outside-toplevel
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
    from app.services import account_service  # pylint: disable=import-outside-toplevel

    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    # Bootstrap pay period (E-19, Commit 3): the factory needs at
    # least one period to anchor against.  Dated far before any
    # test's typical 2026 range so it does not collide with periods
    # generated later by the test body.
    bootstrap = PayPeriod(
        user_id=other_user.id,
        start_date=date(2024, 1, 5),
        end_date=date(2024, 1, 18),
        period_index=0,
    )
    db.session.add(bootstrap)
    db.session.flush()

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )
    db.session.commit()

    return {"user": other_user, "account": account}


# ── Account CRUD ───────────────────────────────────────────────────


class TestAccountList:
    """Tests for GET /accounts (the retired table is now a redirect, P4)."""

    def test_accounts_url_redirects_to_cockpit(self, app, auth_client, seed_user):
        """GET /accounts 302-redirects to the Net Worth Cockpit.

        Loop B P4 retired the standalone /accounts management table; the
        endpoint is kept only as a permanent redirect to savings.dashboard
        so old bookmarks resolve and the unauthenticated-redirect contract
        stays green.  No list page is rendered here anymore.
        """
        with app.app_context():
            response = auth_client.get("/accounts")

            assert response.status_code == 302
            assert response.headers["Location"].endswith("/savings")

    def test_accounts_redirect_lands_on_cockpit_with_accounts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Following the /accounts redirect shows the user's accounts on the cockpit."""
        with app.app_context():
            response = auth_client.get("/accounts", follow_redirects=True)

            assert response.status_code == 200
            assert b"Checking" in response.data

    def test_new_account_form_renders(self, app, auth_client, seed_user):
        """GET /accounts/new renders the account creation form."""
        with app.app_context():
            response = auth_client.get("/accounts/new")

            assert response.status_code == 200
            assert b'name="name"' in response.data
            assert b'name="anchor_balance"' in response.data
            assert b"New Account" in response.data


class TestEditFormDangerZone:
    """The shared edit form hosts hard-delete in a Danger Zone (Loop B P4).

    P4 retired the /accounts table that used to expose hard-delete; the
    developer ruling (audit decision 12) relocated it to the edit form so
    every account type can be deleted from one shared surface, including
    Savings and Credit Card, which have no per-type detail page.
    """

    def test_edit_form_has_delete_danger_zone(self, app, auth_client, seed_user):
        """GET /accounts/<id>/edit renders a hard-delete form in the Danger Zone."""
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.get(f"/accounts/{account_id}/edit")

            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Danger Zone" in html
            # The delete form posts to the hard-delete route for THIS account.
            assert f"/accounts/{account_id}/hard-delete" in html

    def test_create_form_has_no_danger_zone(self, app, auth_client, seed_user):
        """GET /accounts/new (create mode) shows no delete affordance.

        The Danger Zone is gated on ``{% if account %}`` so it never appears
        while creating a brand-new account.
        """
        with app.app_context():
            resp = auth_client.get("/accounts/new")

            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Danger Zone" not in html
            assert "hard-delete" not in html


class TestAccountCreate:
    """Tests for POST /accounts."""

    def test_create_account(self, app, auth_client, seed_user):
        """POST /accounts creates a new account and redirects to the list."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            response = auth_client.post("/accounts", data={
                "name": "Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Account &#39;Savings&#39; created." in response.data

            # Verify in the database.
            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Savings")
                .one()
            )
            assert cash_ledger.resolve_anchor(acct).balance == Decimal("500.00")

    def test_create_account_stores_the_submitted_observed_on(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A back-dated "Balance as of" is stored, and sets the anchor period.

        The user is entering an account they already had: they type today's
        balance but say it was true on a day inside an EARLIER pay period.  Two
        things must follow (ruling R-DH, plan step 2):

        * the origination row's ``observed_on`` is the submitted day, not the
          day the form was submitted -- it is the day the whole anchor/settle
          partition compares settled rows against;
        * the anchor PERIOD is resolved FROM that day, not from today.  A row
          asserting an earlier day while filed against today's period would put
          its correction's journal entry in a period its own ``entry_date``
          falls outside.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            earlier = seed_periods_today[0]
            observed = earlier.start_date + timedelta(days=1)

            response = auth_client.post("/accounts", data={
                "name": "Old Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
                "observed_on": observed.isoformat(),
            }, follow_redirects=True)
            assert response.status_code == 200

            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Old Savings")
                .one()
            )
            row = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct.id)
                .one()
            )
            assert row.observed_on == observed
            # Non-vacuity: the submitted day is genuinely not today, so this
            # cannot pass by the default coinciding with the assertion.
            assert observed != display_today()
            # The ASSERTION carries no period since ruling R-EO -- a fact
            # about a bank is not filed under a budgeting artifact -- and the
            # account carries none either since ruling R-EH.  The rule this
            # case exists for is that the SUBMITTED day survives the round
            # trip, which the assertion above grades; the period half of it
            # went with the columns.
            assert not hasattr(row, "pay_period_id")

    def test_create_account_refuses_a_future_observed_on(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A balance cannot be observed on a day that has not happened.

        The route surfaces the service's own message and returns the user to
        the form -- not to /pay-periods/generate, which is the other shape that
        raises the same exception type.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            tomorrow = display_today() + timedelta(days=1)

            response = auth_client.post("/accounts", data={
                "name": "Future Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
                "observed_on": tomorrow.isoformat(),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"has not happened yet" in response.data
            # Back to the account form, NOT to /pay-periods/generate -- the
            # other shape that raises the same exception type.
            assert b"New Account -- Shekel" in response.data
            assert (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Future Savings")
                .first()
            ) is None

    def test_create_account_refuses_an_observed_on_before_the_schedule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A day before the recorded history has no period to be filed against.

        Unbounded back-dating is not a cosmetic problem: ``observed_on`` opens
        the modelled-return accrual window and the contribution model's first
        period, so a day in the distant past fabricates history and folds over
        every calendar day since (finding N-133, the review of the F1 revert).
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            too_early = seed_periods_today[0].start_date - timedelta(days=1)
            assert too_early < display_today()

            response = auth_client.post("/accounts", data={
                "name": "Ancient Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
                "observed_on": too_early.isoformat(),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"recorded history starts on" in response.data
            assert (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Ancient Savings")
                .first()
            ) is None

    def test_create_account_defaults_observed_on_to_today(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An omitted "Balance as of" means today, applied by the factory.

        The schema drops the empty input, so the route passes ``None`` and the
        default is stated once in ``account_service.create_account`` rather
        than per caller.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            auth_client.post("/accounts", data={
                "name": "Today Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
                "observed_on": "",
            }, follow_redirects=True)

            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Today Savings")
                .one()
            )
            row = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct.id)
                .one()
            )
            assert row.observed_on == display_today()

    def test_new_account_form_bounds_the_observed_on_input(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The date input carries the same two bounds the service enforces.

        The browser refuses what the service would refuse, rather than
        round-tripping a rejection.  The floor is read from
        ``pay_period_service.earliest_recordable_day`` -- the SAME function
        ``anchor_service.resolve_observation_day`` refuses below -- rather than
        from the fixture's own first period, so this pins the form to the
        implementation instead of to a value that merely happens to match.
        It named ``account_service.earliest_observable_day`` until ruling
        **R-ER** deleted that pass-through (plan step X-f1c4c).
        """
        with app.app_context():
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )
            # Non-vacuity: the fixture's first period IS that floor today, so
            # state the equality rather than letting the two silently diverge.
            assert floor == seed_periods_today[0].start_date

            html = auth_client.get("/accounts/new").data.decode()

            assert 'name="observed_on"' in html
            assert f'value="{display_today().isoformat()}"' in html
            assert f'max="{display_today().isoformat()}"' in html
            assert f'min="{floor.isoformat()}"' in html

    def test_creating_an_account_without_a_schedule_is_refused_not_500(
        self, app, auth_client, seed_user,
    ):
        """The schedule precondition, which no test could see before.

        Ruling **R-ER** split this arm out of the shared day rule into
        ``account_service._require_pay_period_schedule``, promoted it to run
        FIRST in ``create_account``, and rewrote its rationale.  A neutral
        review then deleted the guard outright and watched **6,218 tests stay
        green** -- so the split, the reorder and the message were all shipping
        ungraded.

        What it protects: ``create_account``'s tail posts the opening's anchor
        correction, and that reconcile derives each correction's pay period from
        the day it asserts (ruling R-EA).  With an empty calendar there is no
        such period and the reconcile raises (finding **N-192**), so without
        this guard the user gets an unhandled 500 on a money route instead of a
        flash pointing at the repair.
        """
        # Imported here, matching this module's established local-import idiom
        # for the model (see ``_create_other_user_account``).
        from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel

        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete()
            db.session.commit()
            assert db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count() == 0, "precondition: the owner has no schedule"
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            accounts_before = db.session.query(Account).filter_by(
                user_id=user_id,
            ).count()

            response = auth_client.post("/accounts", data={
                "name": "Scheduleless Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
            }, follow_redirects=True)

            # A designed refusal, not a crash, and it points at the repair.
            assert response.status_code == 200
            assert b"Generate pay periods" in response.data
            # No account was created -- the guard runs before the factory.
            assert db.session.query(Account).filter_by(
                user_id=user_id,
            ).count() == accounts_before

    def test_create_account_zero_anchor_balance(self, app, auth_client, seed_user):
        """POST /accounts with anchor_balance "0" stores an exact zero.

        A submitted zero opening balance is a value, not a missing one:
        the route must persist ``Decimal("0")`` rather than treating the
        falsy zero as absent.  Omitting the field entirely also defaults
        to zero.
        """
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            response = auth_client.post("/accounts", data={
                "name": "Zero Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            }, follow_redirects=True)
            assert response.status_code == 200
            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Zero Savings")
                .one()
            )
            assert cash_ledger.resolve_anchor(acct).balance == Decimal("0")

            # Omitting anchor_balance entirely also defaults to zero.
            response = auth_client.post("/accounts", data={
                "name": "No Balance Savings",
                "account_type_id": savings_type.id,
            }, follow_redirects=True)
            assert response.status_code == 200
            acct2 = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="No Balance Savings")
                .one()
            )
            assert cash_ledger.resolve_anchor(acct2).balance == Decimal("0")

    def test_create_account_validation_error(self, app, auth_client, seed_user):
        """POST /accounts with missing name shows a validation error."""
        with app.app_context():
            response = auth_client.post("/accounts", data={
                "name": "",
                "account_type_id": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_account_duplicate_name(self, app, auth_client, seed_user):
        """POST /accounts with a duplicate name shows a warning flash."""
        with app.app_context():
            # "Checking" already exists from seed_user.
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            response = auth_client.post("/accounts", data={
                "name": "Checking",
                "account_type_id": checking_type.id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"An account with that name already exists." in response.data


class TestAccountUpdate:
    """Tests for GET/POST /accounts/<id>/edit."""

    def test_edit_account_form_renders(self, app, auth_client, seed_user):
        """GET /accounts/<id>/edit renders the edit form."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.get(f"/accounts/{account_id}/edit")

            assert response.status_code == 200
            assert b"Checking" in response.data

    def test_edit_form_offers_no_balance_field(
        self, app, auth_client, seed_user,
    ):
        """The EDIT form has no way to assert a balance (X-f1e, N-195).

        The surface half of the deletion, and it is tested separately from the
        route half because the two fail independently: a route that ignores the
        field while the form still renders it shows the user a control that
        silently does nothing, and a form that drops it while the route still
        accepts it leaves the second door reachable by a forged POST.

        Paired with the CREATE form below, which must KEEP both inputs -- an
        account's opening balance is a different fact from a later reading, and
        a deletion that took the opening with it would be a regression wearing
        this step's clothes.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            html = auth_client.get(f"/accounts/{account_id}/edit").data.decode()

            assert 'name="anchor_balance"' not in html
            assert 'name="observed_on"' not in html
            # The fields this door DOES own are still there.
            assert 'name="name"' in html
            assert 'name="account_type_id"' in html

    def test_create_form_keeps_the_opening_assertion(
        self, app, auth_client, seed_user,
    ):
        """The CREATE form keeps the balance AND the day it was true."""
        with app.app_context():
            html = auth_client.get("/accounts/new").data.decode()

            assert 'name="anchor_balance"' in html
            assert 'name="observed_on"' in html

    def test_update_account(self, app, auth_client, seed_user):
        """POST /accounts/<id> updates the account and redirects."""
        with app.app_context():
            account_id = seed_user["account"].id
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            response = auth_client.post(f"/accounts/{account_id}", data={
                "name": "Primary Checking",
                "account_type_id": checking_type.id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Account &#39;Primary Checking&#39; updated." in response.data

            # Verify in the database.
            acct = db.session.get(Account, account_id)
            assert acct.name == "Primary Checking"

    def test_update_account_duplicate_name(self, app, auth_client, seed_user):
        """POST /accounts/<id> with a duplicate name shows a warning."""
        with app.app_context():
            # Create a second account first.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            second = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(second)
            db.session.commit()

            # Try to rename it to "Checking" (already exists).
            response = auth_client.post(f"/accounts/{second.id}", data={
                "name": "Checking",
                "account_type_id": savings_type.id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"An account with that name already exists." in response.data

    def test_edit_other_users_account_redirects(self, app, auth_client, seed_user):
        """GET /accounts/<id>/edit for another user's account returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.get(
                f"/accounts/{other['account'].id}/edit",
                follow_redirects=True,
            )

            assert response.status_code == 404

    def test_update_other_users_account_redirects(self, app, auth_client, seed_user):
        """POST /accounts/<id> for another user's account returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.post(
                f"/accounts/{other['account'].id}",
                data={"name": "Hacked"},
                follow_redirects=True,
            )

            assert response.status_code == 404

            # Verify name was not changed.
            acct = db.session.get(Account, other["account"].id)
            assert acct.name == "Other Checking"


class TestAccountArchive:
    """Tests for POST /accounts/<id>/archive and /unarchive."""

    def test_archive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/archive archives the account."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.post(
                f"/accounts/{account_id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"archived" in response.data

            acct = db.session.get(Account, account_id)
            assert acct.is_active is False

    def test_unarchive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/unarchive restores an archived account."""
        with app.app_context():
            account_id = seed_user["account"].id

            # Archive first.
            seed_user["account"].is_active = False
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{account_id}/unarchive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"unarchived" in response.data

            acct = db.session.get(Account, account_id)
            assert acct.is_active is True

    def test_archive_account_with_active_transfers(
        self, app, auth_client, seed_user
    ):
        """POST /accounts/<id>/archive is blocked when active transfer templates reference it."""
        with app.app_context():
            from app.models.transfer_template import TransferTemplate

            # Create a second account and an active transfer template.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Monthly Savings",
                default_amount=Decimal("200.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Cannot archive this account" in response.data

            # Account should still be active.
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.is_active is True

    def test_archive_other_users_account_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /accounts/<id>/archive for another user's account returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.post(
                f"/accounts/{other['account'].id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 404

            # Other user's account should still be active.
            acct = db.session.get(Account, other["account"].id)
            assert acct.is_active is True

    def test_create_account_double_submit(self, app, auth_client, seed_user):
        """POST /accounts twice with the same name flashes duplicate on 2nd."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            data = {
                "name": "Emergency Fund",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            }

            # First submit succeeds.
            response1 = auth_client.post("/accounts", data=data, follow_redirects=True)
            assert b"created" in response1.data

            # Second submit hits duplicate guard.
            response2 = auth_client.post("/accounts", data=data, follow_redirects=True)
            assert b"An account with that name already exists." in response2.data


class TestTypeChangeBoundaryGuard:
    """The C6 posting-boundary guards on account re-type and type edits.

    Build-Order Step 5 (plan Section 3.3, point 7): an ``account_type_id``
    change that crosses the amortizing boundary or flips the linked
    ledger's Asset/Liability class is refused while the account carries
    ledger postings -- it would strand one correction family or silently
    re-interpret posted legs' balance-sheet meaning.  The second crossing
    vector (editing a custom type's ``has_amortization`` / ``category_id``
    in place) is guarded the same way.  Allowed crossings (empty ledgers)
    re-class the empty linked row and re-sync the corrections.
    """

    def test_retype_posted_account_across_class_boundary_refused(
        self, app, auth_client, seed_user,
    ):
        """Re-typing the posted Checking to Credit Card (class flip) refuses.

        The seed Checking's $1000.00 opening is posted ledger history;
        Checking is Asset-category and Credit Card Liability-category, so
        the re-type crosses the class boundary and the guard rejects it,
        leaving the type unchanged.
        """
        with app.app_context():
            checking = seed_user["account"]
            old_type_id = checking.account_type_id
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None)
                .one()
            )

            response = auth_client.post(
                f"/accounts/{checking.id}",
                data={"account_type_id": str(cc_type.id)},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"posting-ledger history" in response.data
            # Re-fetch: the request's session lifecycle detached the fixture
            # object.
            reloaded = db.session.get(Account, checking.id)
            assert reloaded.account_type_id == old_type_id

    def test_retype_unposted_account_reclasses_linked_row(
        self, app, auth_client, seed_user,
    ):
        """A $0-anchor account re-types across the boundary and re-classes.

        A zero opening books nothing, so the ledger is empty and the
        crossing is allowed; the route re-snapshots the empty linked row's
        class to the new category's (Asset -> Liability) so future postings
        land in the right balance-sheet section.
        """
        # Pylint: ``import-outside-toplevel`` -- localized to the one test
        # that needs these helpers, matching the file's convention.
        # pylint: disable=import-outside-toplevel
        from app.enums import LedgerAccountClassEnum
        from tests._test_helpers import linked_ledger_account

        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings", user_id=None)
                .one()
            )
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None)
                .one()
            )
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Reclass Target",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.commit()
            linked = linked_ledger_account(db.session, account.id)
            assert linked.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.ASSET,
            )

            response = auth_client.post(
                f"/accounts/{account.id}",
                data={"account_type_id": str(cc_type.id)},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            # Re-fetch: the request's session lifecycle detached the objects.
            reloaded = db.session.get(Account, account.id)
            assert reloaded.account_type_id == cc_type.id
            relinked = linked_ledger_account(db.session, account.id)
            assert relinked.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )

    def test_type_edit_amortization_flip_refused_with_posted_accounts(
        self, app, auth_client, seed_user,
    ):
        """Flipping ``has_amortization`` on a type with posted accounts refuses.

        The second crossing vector (C4 adversarial review M2): the custom
        Liability type's account carries a posted opening, and flipping the
        flag in place would move the account across the correction-family
        boundary with no ``account_type_id`` change to guard.
        """
        with app.app_context():
            liability_id = ref_cache.acct_category_id(
                AcctCategoryEnum.LIABILITY,
            )
            custom_type = AccountType(
                name="store_card",
                category_id=liability_id,
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()
            account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=custom_type.id,
                    name="Posted Store Card",
                    anchor_balance=Decimal("-250.00"),
                ),
            )
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={
                    "has_amortization": "true",
                    "category_id": str(liability_id),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"posting-ledger history" in response.data
            db.session.refresh(custom_type)
            assert custom_type.has_amortization is False

    def test_type_edit_category_flip_reclasses_unposted_accounts(
        self, app, auth_client, seed_user,
    ):
        """A category flip on a type whose accounts are unposted re-classes them.

        The type's only account has a $0 anchor (empty ledger), so the
        Asset -> Liability category flip is allowed and the account's empty
        linked row re-snapshots to the Liability class.
        """
        # Pylint: ``import-outside-toplevel`` -- localized to the one test
        # that needs these helpers, matching the file's convention.
        # pylint: disable=import-outside-toplevel
        from app.enums import LedgerAccountClassEnum
        from tests._test_helpers import linked_ledger_account

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

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={
                    "category_id": str(ref_cache.acct_category_id(
                        AcctCategoryEnum.LIABILITY,
                    )),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            linked = linked_ledger_account(db.session, account.id)
            assert linked.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )

    def test_update_account_anchor_edit_books_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The account EDIT door asserts no balance (plan step X-f1e, N-195).

        The inverse of the test this replaces, which asserted that POSTing an
        ``anchor_balance`` here booked a true-up correction and moved the
        Checking total from $1000.00 to $1500.00.  That door was the app's
        SECOND balance-assertion surface and it answered the same submission
        differently from the first, so it was deleted rather than aligned.

        Three things are checked because the deletion has three layers and any
        one of them alone would be a fence: the schema DISCARDS the field, so a
        forged submission cannot reach a writer; no assertion row is appended;
        and the posted ledger does not move.  The rest of the edit still
        applies, which is what says the route was narrowed rather than broken.
        """
        # Pylint: ``import-outside-toplevel`` -- localized to the one test
        # that needs this reader, matching the file's convention.
        # pylint: disable=import-outside-toplevel
        from app.services import posting_service

        with app.app_context():
            checking = seed_user["account"]
            checking_id = checking.id
            scenario_id = seed_user["scenario"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            assertions_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=checking_id).count()
            )
            assert posting_service.account_posting_total(
                checking_id, scenario_id,
            ) == Decimal("1000.00")

            response = auth_client.post(
                f"/accounts/{checking_id}",
                data={
                    "name": "Renamed Checking",
                    "account_type_id": str(checking_type.id),
                    "anchor_balance": "1500.00",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.expire_all()
            # The balance the form submitted reached nothing.
            assert posting_service.account_posting_total(
                checking_id, scenario_id,
            ) == Decimal("1000.00")
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=checking_id).count()
            ) == assertions_before
            # ...and the edit this door DOES own still landed.
            assert db.session.get(Account, checking_id).name == "Renamed Checking"


class TestHardDeletePostingLedgerGuard:
    """Hard-delete archives an account with surviving posting-ledger history.

    Regression for the Build-Order Step 2 / Commit 5 cascade-imbalance hole:
    settling a transfer writes balanced journal entries onto both accounts'
    ledgers, and those entries survive a transfer hard-delete
    (``journal_entries.transfer_id`` SET NULL).  Hard-deleting a participating
    account would then CASCADE-delete only ITS legs, stranding the paired legs
    as unbalanced single-leg entries (the balanced trigger does not fire on
    DELETE).  Guard 5 in ``hard_delete_account`` archives the account instead,
    keeping every ledger entry balanced.
    """

    def test_hard_delete_archives_account_with_surviving_postings(
        self, app, auth_client, seed_user,
    ):
        """An account with ledger postings (its transfer gone) archives, not deletes.

        Sequence: settle a $100 transfer between two fresh accounts (auto-posts
        a balanced entry), hard-delete the transfer (shadows go; the settle +
        reversal entries survive with transfer_id nulled, legs on both
        ledgers), then hard-delete the source account.  It has no transaction
        history but does have ledger postings, so Guard 5 archives it: the
        source ledger account is NOT deleted, no leg is orphaned, and every
        surviving journal entry still has >= 2 legs summing to zero.
        """
        from app.models.journal_entry import JournalEntry, Posting
        from app.services import transfer_service
        from app.utils import archive_helpers
        from tests._test_helpers import (
            create_account_of_type,
            create_settled_transfer,
        )

        with app.app_context():
            source = create_account_of_type(
                seed_user, db.session, "Checking", "Cascade Source",
            )
            dest = create_account_of_type(
                seed_user, db.session, "Savings", "Cascade Dest",
            )
            db.session.commit()
            transfer = create_settled_transfer(
                seed_user, db.session, source, dest,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            db.session.commit()

            # Hard-delete the transfer: the shadows are removed, but the
            # balanced settle + reversal entries survive (transfer_id nulled),
            # with legs on both ledgers.  The source account now has NO
            # transaction history but DOES have ledger postings.
            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()
            assert archive_helpers.account_has_history(source.id) is False
            assert archive_helpers.account_has_ledger_postings(source.id) is True

            response = auth_client.post(
                f"/accounts/{source.id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"posting-ledger history" in response.data
            # Archived, NOT deleted: the account row survives, inactive.
            acct = db.session.get(Account, source.id)
            assert acct is not None
            assert acct.is_active is False
            # No orphaned legs: every surviving entry still balances.
            entries = db.session.query(JournalEntry).all()
            assert len(entries) >= 2
            for entry in entries:
                legs = (
                    db.session.query(Posting)
                    .filter_by(journal_entry_id=entry.id)
                    .all()
                )
                assert len(legs) >= 2
                assert sum(leg.amount for leg in legs) == Decimal("0.00")


# ── Anchor Balance (Inline + True-up) ─────────────────────────────


class TestTrueUp:
    """Tests for the grid anchor balance true-up endpoints."""

    @pytest.mark.server_clock
    def test_true_up_updates_balance(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /accounts/<id>/true-up updates the balance and creates history.

        Re-pin (E-19, Commit 3): under the post-E-19 canonical
        account factory, every account already has a fixture-time
        origination AccountAnchorHistory row before the test fires.
        The true-up therefore produces a SECOND history row.  The
        assertion uses ``.order_by(created_at.desc()).first()`` to
        target the row the true-up just wrote -- the same audit-row
        the prior assertion claimed via ``.one()``.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "3000.00"},
            )

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            acct = db.session.get(Account, account_id)
            assert cash_ledger.resolve_anchor(acct).balance == Decimal("3000.00")

            # The true-up's history row is the most recent for this
            # account; the fixture-time origination row is the prior
            # one and is unaffected by the route call.
            history = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account_id)
                .order_by(AccountAnchorHistory.created_at.desc())
                .first()
            )
            assert history.anchor_balance == Decimal("3000.00")

    def test_true_up_records_without_a_current_pay_period(
        self, app, auth_client, seed_user, db,
    ):
        """A true-up with no period covering today is RECORDED, not refused.

        The direct inversion of ``test_true_up_no_current_period``, which
        asserted a 400 and the message "No current pay period found".  Ruling
        R-EO deleted the pay period from a balance assertion, so this door has
        no period to resolve and nothing it can fail to find -- refusing a
        balance the user read off their bank because a BUDGETING artifact is
        missing was finding N-134's shape on the true-up door.

        Non-vacuity: ``seed_user`` alone seeds no period covering today (that
        is what made the old 400 fire), and the assertion is read back out of
        the history table rather than off the response.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            # The PRECONDITION, asserted rather than assumed.  This test is
            # about the no-current-period door, so it grades nothing if the
            # fixture ever starts seeding a period covering today -- it would
            # pass as an ordinary true-up.  ``seed_user``'s bootstrap period is
            # HARDCODED to 2026-01-05..2026-01-18 (``conftest``), not derived
            # from the user's creation date as an earlier version of this
            # comment said, so no period contains any day after 2026-01-18
            # and this is not date-fragile.
            assert current_pay_period(
                seed_user["user"].id,
            ) is None, (
                "this test grades the no-current-period door; the fixture now "
                "seeds a period covering today, so it would pass vacuously"
            )

            before_rows = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count()

            # NOT the fixture's own $1,000.00.  A first version of this test
            # asserted the resolver returned "1000.00" -- which ``seed_user``'s
            # origination assertion already says, so a true-up that returned 200
            # and wrote NOTHING passed it.  Proven vacuous by a neutral review,
            # which made ``stage_anchor_true_up`` return before its
            # ``session.add`` and watched this test stay green.
            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1750.25"},
            )

            assert response.status_code == 200, response.data[:200]
            db.session.expire_all()
            account = db.session.get(Account, account_id)
            anchor = cash_ledger.resolve_anchor(account)
            assert anchor.balance == Decimal("1750.25")
            # Recorded AS OF the user's civil day, not the process's.
            assert anchor.observed_on == display_today()
            # Exactly one row appended -- an INSERT that also destroyed the
            # origination assertion would satisfy the balance check alone.
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() == before_rows + 1

    def test_true_up_invalid_amount(self, app, auth_client, seed_user, seed_periods_today):
        """An unparseable balance is refused with a 400 the browser RENDERS.

        The contract changed at plan step X-f1c4c, on the developer's ruling,
        and the old one is why: this door answered ``jsonify(errors=...)`` with
        no marker header, and ``base.html`` configures 4xx as ``swap:false`` --
        so a correct 400 with a correct message rendered NOTHING and the form
        sat there.  The test that graded it asserted only that the JSON carried
        an ``errors`` key, which the invisible response satisfied.

        What is graded now is the property that was missing: the response
        carries ``Shekel-Designed-Fragment``, which is the ONLY thing that makes
        htmx swap a 4xx, and its body is the editor re-rendered with the reason
        in it.  Every clause is load-bearing -- drop the header and the user
        sees a dead form again, drop the body check and a bare 400 passes.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            rows_before = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count()

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "abc"},
            )

            assert response.status_code == 400
            assert response.headers.get("Shekel-Designed-Fragment") == "1", (
                "a 4xx without the marker header is non-swapping, so the "
                "refusal would render nothing at all"
            )
            html = response.data.decode()
            # The editor came back, in EDIT mode, so the user can correct the
            # value in place rather than losing the surface.
            assert 'name="anchor_balance"' in html
            # ...and it says WHY.  The VALIDATOR's own message, not the field
            # name: ``assert "anchor_balance" in html`` was implied by the line
            # above and could not fail on its own, so a response carrying the
            # editor and no error text at all passed it.
            assert "Not a valid number" in html
            assert 'role="alert"' in html
            # Nothing was WRITTEN, counted rather than read back.  Asserting the
            # resolved balance is still $1,000.00 would be vacuous: that is
            # ``seed_user``'s own origination figure, so a path that appended a
            # fresh $1,000.00 assertion dated today -- which would move
            # ``reconciled_through`` and absorb outstanding purchases -- passes
            # it.  The same trap is documented twenty lines above, on the
            # positive side.
            db.session.expire_all()
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() == rows_before

    def test_true_up_other_users_account(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /accounts/<id>/true-up for another user's account returns 404.

        IDOR write-path: must verify the anchor balance was not changed.
        """
        with app.app_context():
            other = _create_other_user_account()
            orig_balance = cash_ledger.resolve_anchor(other["account"]).balance

            response = auth_client.patch(
                f"/accounts/{other['account'].id}/true-up",
                data={"anchor_balance": "9999.00"},
            )

            assert response.status_code == 404

            # Prove no state change occurred.
            db.session.expire_all()
            db.session.refresh(other["account"])
            assert cash_ledger.resolve_anchor(other["account"]).balance == orig_balance

    @pytest.mark.parametrize(
        ("revert", "expected"),
        [
            (None, True),
            ("dashboard", False),
            ("accounts", False),
            ("investment", False),
            ("cash", False),
        ],
        ids=["grid", "dashboard", "cockpit", "investment-hero", "cash-hero"],
    )
    def test_the_as_of_snippet_goes_to_the_grid_and_only_the_grid(
        self, app, auth_client, seed_user, seed_periods_today, revert, expected,
    ):
        """The out-of-band "as of" caption update is the GRID's alone.

        **One rule, no per-surface exception list** (plan step X-f1e3): an
        out-of-band caption update goes to the caption nothing else refreshes.

        The grid's ``#anchor-as-of`` sits at page level in ``grid/grid.html``,
        so nothing on that page redraws it and the snippet is the only way it
        can move.  Every other opener re-fetches its own region on the
        ``balanceChanged`` this response fires:

        * the cockpit, the investment hero and the cash hero carry no
          ``#anchor-as-of`` element at all, so a snippet would orphan-target
          (``htmx:oobErrorNoTarget``);
        * the DASHBOARD carries one, and it was emitted there until this step.
          That was a redundant SECOND render of one fact -- ``#pulse-section``
          re-renders the same caption from ``reconciled_through``, which
          ``dashboard_service._pulse`` states is provably equal to the
          ``resolve_anchor`` day the snippet carries -- and it was the
          mechanism that destroyed the back-dated acknowledgement riding on it
          (finding N-199).

        The dashboard case is the one this parametrization exists for: with
        the two original tests covering only ``accounts`` and the default, a
        regression that re-emitted the snippet for the dashboard passed.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            url = f"/accounts/{acct_id}/true-up"
            if revert is not None:
                url = f"{url}?revert={revert}"
            response = auth_client.patch(
                url, data={"anchor_balance": "3210.00"},
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"
            # **The whole opening tag, so the id and its OUT-OF-BAND-ness are
            # graded together.**  The bare ``hx-swap-oob`` attribute would
            # match whichever fragment happened to ride along (the reconcile
            # prompt and the acknowledgement are out-of-band too), and the id
            # alone leaves the attribute ungraded -- an adversarial review
            # deleted it and measured the whole suite still passing, with the
            # caption then rendering INSIDE the grid balance cell and the
            # page-level ``#anchor-as-of`` never updating again.
            assert ((
                '<small class="text-muted" id="anchor-as-of" '
                'hx-swap-oob="true">'
            ) in response.data.decode()) is expected


class TestTrueUpSameDayDuplicate:
    """Ruling R-EQ: a re-submit of the governing balance writes nothing."""

    def test_double_submit_creates_one_history_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two identical true-ups same day produce exactly one history row.

        The second submit asserts what the first made governing, so the
        write door appends nothing and the route returns the already-current
        balance -- the user sees idempotent success rather than a 500 or a
        duplicate row.  **It was a unique-index rejection translated by an
        exception handler until plan step X-f1c4b** (ruling R-EQ); the
        user-visible behaviour is unchanged, which is why this test is.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            r1 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1234.56"},
            )
            assert r1.status_code == 200

            r2 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1234.56"},
            )
            # Idempotent success: both requests return 200.
            assert r2.status_code == 200

            # Exactly one true-up audit row was added.  Re-pin
            # (E-19, Commit 3): the factory writes a fixture-time
            # origination row at $1000.00 (seed_user's seed balance),
            # so the total count after the double-submit is 2 (one
            # origination + one true-up), not 1.  The claim is that the
            # true-up balance ($1234.56) only appears once -- the second
            # submit changed nothing and so wrote nothing.
            db.session.expire_all()
            history_at_trueup_balance = (
                db.session.query(AccountAnchorHistory)
                .filter_by(
                    account_id=account_id,
                    anchor_balance=Decimal("1234.56"),
                )
                .all()
            )
            assert len(history_at_trueup_balance) == 1, (
                f"Expected 1 anchor history row at the true-up balance "
                f"after double-submit, found {len(history_at_trueup_balance)}; "
                "a submission that changes nothing must write nothing."
            )

    def test_same_day_different_balance_creates_two_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Same-day true-ups with different balances both succeed.

        A legitimate same-day correction (the user noticed an error and
        re-trued at a different amount) changes what governs, so it must NOT
        be blocked.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            r1 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1000.00"},
            )
            r2 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1100.00"},
            )
            assert r1.status_code == 200
            assert r2.status_code == 200

            # Re-pin (E-19, Commit 3): the factory writes a
            # fixture-time origination row before the test starts.
            # Filter on the true-up balances ($1000, $1100) to verify
            # both distinct true-ups produced their own audit row.  The
            # fixture's origination row is at the same $1000 balance as r1
            # but on an EARLIER day, so r1 changed what governs (the day
            # moved) and appended; r2 then appended the $1100 correction.
            db.session.expire_all()
            history_balances = {
                h.anchor_balance for h in
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account_id)
                .all()
            }
            assert history_balances == {
                Decimal("1000.00"), Decimal("1100.00"),
            }, (
                f"Expected exactly the two true-up balances in the "
                f"history, got {sorted(history_balances)}"
            )


class TestTrueUpStatementDay:
    """The true-up carries the day it was read from (plan step X-f1c4c).

    Rulings **R-EE** (the true-up form gets its own statement date) and
    **R-EI** (it goes on a second line).  The service contract is pinned in
    ``tests/test_services/test_anchor_service.py``; these cases grade the WIRING
    -- that the form offers the field bounded, that the submitted day reaches
    the write door, and that a refusal renders where the submission was made.
    """

    #: Days back from today for a day that is assertable on any calendar day.
    #: ``seed_periods_today`` starts its schedule ``today.weekday() + 56`` days
    #: back, so 20 is inside the floor with room to spare.
    STATEMENT_DAYS_BACK = 20

    def _statement_day(self, seed_user):
        """Return a past day inside both bounds, asserting the fixture affords it."""
        day = display_today() - timedelta(days=self.STATEMENT_DAYS_BACK)
        floor = pay_period_service.earliest_recordable_day(seed_user["user"].id)
        assert floor <= day, (
            f"the schedule starts {floor}, so {day} is not assertable; "
            "seed_periods_today no longer affords 20 days of history"
        )
        return day

    def test_the_editor_offers_a_bounded_statement_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The date box is present, defaults to today, and carries both bounds.

        The bounds are read from the same functions the seam refuses by, not
        written as literals, so this cannot pass against a second definition of
        the floor.  Default = today is ruling R-EE's "one click stays one
        click": leaving the box alone means "I am reading my bank now".
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )
            today = display_today()

            html = auth_client.get(
                f"/accounts/{acct_id}/anchor-form"
            ).data.decode()

            assert 'name="observed_on"' in html
            assert f'value="{today.isoformat()}"' in html
            assert f'min="{floor.isoformat()}"' in html
            assert f'max="{today.isoformat()}"' in html

    def test_a_submitted_day_is_what_the_assertion_carries(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A back-dated true-up through the route lands on the day submitted."""
        with app.app_context():
            acct_id = seed_user["account"].id
            statement_day = self._statement_day(seed_user)

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "1750.25",
                    "observed_on": statement_day.isoformat(),
                },
            )

            assert response.status_code == 200, response.data[:200]
            db.session.expire_all()
            row = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id, observed_on=statement_day)
                .one()
            )
            assert row.anchor_balance == Decimal("1750.25")
            # Non-vacuity: the stamped day is not the default, so a route that
            # dropped the field could not pass.
            assert statement_day != display_today()

    def test_a_blank_day_means_today(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An empty date box is "now", not a validation error.

        An untouched HTML date input submits ``""``, and ``fields.Date()``
        deserializes that to an error rather than to "absent" -- so without the
        schema's ``@pre_load`` normalizer, clearing the box would 400 instead of
        meaning today.  This is that normalizer's control, submitted the way a
        browser actually submits it.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={"anchor_balance": "1751.25", "observed_on": ""},
            )

            assert response.status_code == 200, response.data[:200]
            db.session.expire_all()
            anchor = cash_ledger.resolve_anchor(
                db.session.get(Account, acct_id),
            )
            assert anchor.balance == Decimal("1751.25")
            assert anchor.observed_on == display_today()

    @pytest.mark.parametrize(
        "offset_days, expected_phrase",
        [
            (1, "has not happened yet"),
            (None, "recorded history starts on"),
        ],
        ids=["future-day", "below-the-schedule"],
    )
    def test_an_out_of_bounds_day_is_refused_and_RENDERED(
        self, app, auth_client, seed_user, seed_periods_today,
        offset_days, expected_phrase,
    ):
        """Both bounds refuse with a 400 the browser will actually swap.

        The marker header is the load-bearing assertion: ``base.html``
        configures 4xx as non-swapping, so a refusal without it renders
        nothing and the form appears to ignore the click.  Parametrized over
        the two bounds because they are one surface with one contract -- a fix
        that rendered only the future case would pass a single-case test.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            before = db.session.query(AccountAnchorHistory).filter_by(
                account_id=acct_id,
            ).count()
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )
            if offset_days is None:
                bad_day = floor - timedelta(days=1)
            else:
                bad_day = display_today() + timedelta(days=offset_days)

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "1750.25",
                    "observed_on": bad_day.isoformat(),
                },
            )

            assert response.status_code == 400
            assert response.headers.get("Shekel-Designed-Fragment") == "1", (
                "a 4xx without the marker header is non-swapping, so this "
                "refusal would render nothing at all"
            )
            html = response.data.decode()
            assert expected_phrase in html
            # The editor came back with the user's own inputs, so the wrong
            # field can be corrected without retyping the right one.
            assert 'name="observed_on"' in html
            assert f'value="{bad_day.isoformat()}"' in html
            assert 'value="1750.25"' in html
            # The re-rendered editor still carries BOTH bounds.  Graded here as
            # well as on the GET because the error path builds its context
            # separately: a template change that dropped them from the
            # rejection render would leave the corrected re-submit unbounded in
            # the browser, and only the GET test would have noticed.
            assert f'min="{floor.isoformat()}"' in html
            assert f'max="{display_today().isoformat()}"' in html
            # Escape reverts from EITHER field.  A substring check passes on the
            # balance input alone, so the count is what pins the date input's
            # own wiring -- ruling R-EI's second line is where the user's cursor
            # ends up, and Escape silently dying there is not visible in review.
            assert html.count('data-action="anchor-cancel-on-escape"') == 2
            # Nothing was written.
            db.session.expire_all()
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=acct_id,
            ).count() == before

    def test_a_rename_through_the_edit_door_moves_no_coverage_boundary(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A rename is not a balance reading (plan step X-f1e, finding N-195).

        This is the defect the deleted door made reachable, tested at the level
        that matters rather than at the field: the edit form PRE-FILLED the
        current balance, so an ordinary rename re-submitted it, and the two
        doors disagreed about whether that is an assertion.  The route's own
        gate ("did the FIELD change") said no; ruling R-EQ's rule in
        ``stage_anchor_true_up`` ("does this change what GOVERNS", the day
        included) said yes -- and the day is what moves ``reconciled_through``,
        which decides which outstanding purchases the walk treats as already
        inside the asserted balance.

        Aligning the route on the service's rule would have made a rename
        absorb purchases the user never reconciled.  Deleting the surface makes
        the question unaskable, and that is what this asserts: after an edit
        through this door, the coverage boundary is exactly where it was.

        It replaces a test that pinned the OLD door's day, whose own docstring
        said it existed only until this step landed.

        **The submitted balance is CHANGED, deliberately.**  An unchanged echo
        -- what the form actually pre-filled -- is the one input the deleted
        route gate short-circuited, so a test built on it passes against the old
        code too and grades nothing.  Measured: a first version of this test did
        exactly that and survived a revert.  A CHANGED balance is what the old
        door staged an assertion from, dated today, which is what moved the
        boundary; so this fails on revert, which is the only reason it is here.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            account = db.session.get(Account, acct_id)
            governing_before = cash_ledger.resolve_anchor(account)
            boundary_before = cash_ledger.reconciled_through(acct_id).observed_day
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )

            response = auth_client.post(f"/accounts/{acct_id}", data={
                "name": "Renamed By Hand",
                "account_type_id": str(checking_type.id),
                "anchor_balance": str(governing_before.balance + Decimal("250.00")),
                "version_id": str(account.version_id),
            }, follow_redirects=True)

            assert response.status_code == 200
            db.session.expire_all()
            assert cash_ledger.reconciled_through(acct_id).observed_day == (
                boundary_before
            ), (
                "a rename must not move the coverage boundary -- doing so "
                "silently absorbs purchases the user never reconciled"
            )
            governing_after = cash_ledger.resolve_anchor(
                db.session.get(Account, acct_id),
            )
            assert (governing_after.balance, governing_after.observed_on) == (
                governing_before.balance, governing_before.observed_on,
            )
            assert db.session.get(Account, acct_id).name == "Renamed By Hand"

    def test_a_refusal_keeps_the_surface_that_opened_the_editor(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancel after a refusal returns to the opener, not to the grid cell.

        The editor opens from five surfaces and threads a ``revert`` token so
        Cancel restores the right one.  A rejection RE-RENDERS the editor, so
        the token has to survive the rejection -- otherwise a refused dashboard
        true-up leaves the card able only to revert into the grid's display
        cell, which is not on that page.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up?revert=dashboard",
                data={
                    "anchor_balance": "1750.25",
                    "observed_on": (
                        display_today() + timedelta(days=1)
                    ).isoformat(),
                },
            )

            assert response.status_code == 400
            html = response.data.decode()
            # Both revert affordances -- the Cancel button's hx-get and the
            # Escape handler's data-revert-url -- must point at the dashboard
            # card, exactly as they do on the non-rejected GET.
            assert 'hx-get="/dashboard/balance"' in html, (
                "the revert token was dropped by the rejection path"
            )
            assert 'data-revert-url="/dashboard/balance"' in html
            assert f"/accounts/{acct_id}/anchor-display" not in html
            # The mutation URL keeps the token too, so a corrected re-submit
            # still lands back on the dashboard rather than on the grid.
            assert 'hx-patch="/accounts/'f'{acct_id}/true-up?revert=dashboard"' in html


class TestTheReconcileRoute:
    """The reconcile step's two doors -- plan step S1-c, ruling R-DH (d) / 12.5.

    **This class covered a DELETED function.**  It graded
    ``entry_service.clear_entries_for_anchor_true_up``: a bulk ``UPDATE`` that
    flipped ``is_cleared = TRUE`` on every past-dated entry of a projected
    parent, fired as a side effect of every checking anchor true-up.  Whether a
    purchase counted as reconciled was therefore decided by the order two
    buttons were pressed -- record then true up and it cleared, true up then
    record and it never did -- which is 14 of the developer's 53 same-day
    entries.  The flag, the bulk update and its two log events are gone.

    What replaced it is an ASKED question rather than a guessed one: after a
    successful true-up the app lists the purchases it still thinks are
    outstanding and the user ticks the ones their statement shows
    (``GET`` / ``POST /accounts/<id>/reconcile``).  A tick stamps
    ``settled_on`` with the assertion's own ``observed_on`` -- an upper bound on
    the true posting day, and the only bound the reconciliation predicate
    consumes, so no answer changes by sharpening it.

    The per-account scoping case SURVIVES the re-ruling because the invariant
    it names is unchanged and still real: reconciling account A must not touch
    account B's purchases.  The exhaustive scoping matrix (another user, a
    credit purchase, a settled parent, an already-recorded entry) is graded at
    the service in ``test_services/test_reconcile_service.py``; what is graded here
    is the ROUTE -- that it resolves the assertion day itself, refuses a
    non-owner, and commits.
    """

    def _make_grocery_txn_with_entries(
        self, seed_user, seed_periods_today, entries, account=None,
        name="Groceries",
    ):
        """Create a tracked grocery transaction with the given entries.

        Args:
            seed_user: seed_user fixture dict.
            seed_periods_today: list of PayPeriods.
            entries: list of ``(amount, purchased_on, is_credit, settled_on)``
                tuples.  ``settled_on`` is the day the bank was seen to take
                the purchase, or ``None`` for one not yet seen -- the fourth
                element was a ``is_cleared`` bool until plan step S1-c.
            account: the Account the template and transaction belong
                to.  Defaults to the seed_user's primary checking
                account; pass a second account to exercise the
                per-account scope of the reconcile.
            name: the envelope's name, on both the template and the row.
                Defaults to "Groceries"; pass a second name to exercise the
                per-envelope GROUPING (plan step X-f2-c1), which needs two
                distinguishable blocks in one panel.

        Returns:
            The Transaction object.
        """
        from app.models.transaction_entry import TransactionEntry
        from app.models.transaction_template import TransactionTemplate

        account = account if account is not None else seed_user["account"]
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=account.id,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            name=name,
            default_amount=Decimal("500.00"),
            is_envelope=True,
        )
        db.session.add(template)
        db.session.flush()

        txn = Transaction(
            template_id=template.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=account.id,
            status_id=projected.id,
            name=name,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("500.00"),
        )
        db.session.add(txn)
        db.session.flush()

        for amount, purchased_on, is_credit, settled_on in entries:
            db.session.add(TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=seed_user["user"].id,
                amount=Decimal(amount),
                description="Test purchase",
                purchased_on=purchased_on,
                is_credit=is_credit,
                **settle_day_columns(settled_on),
            ))
        db.session.commit()
        return txn

    @staticmethod
    def _true_up(auth_client, account_id, balance, observed_on=None, revert=None):
        """Assert *balance* through the real PATCH route.

        The reconcile route reads the account's LATEST asserted day and stamps
        it, so the fixtures true up first rather than hand-writing a history
        row: that is the production sequence (read your bank balance, enter it,
        then tick off what it contained) and it is the sequence whose ORDER the
        retired flag got wrong.

        Args:
            auth_client: the authenticated client.
            account_id: the account to assert about.
            balance: the balance string to submit.
            observed_on: an optional civil day to submit as the statement date
                (plan step X-f1c4c).  ``None`` submits the field not at all,
                which the write door reads as the user's today.
            revert: an optional surface token naming which of the five openers
                the editor was opened from.  ``None`` is the grid default.  It
                exists because the success response BRANCHES on it, and a
                caller that never passes one grades only the fall-through.
        """
        data = {"anchor_balance": balance}
        if observed_on is not None:
            data["observed_on"] = observed_on.isoformat()
        url = f"/accounts/{account_id}/true-up"
        if revert is not None:
            url = f"{url}?revert={revert}"
        response = auth_client.patch(url, data=data)
        assert response.status_code == 200
        return response

    @pytest.mark.parametrize(
        "revert",
        [None, "dashboard", "accounts", "investment", "cash"],
        ids=["grid", "dashboard", "cockpit", "investment-hero", "cash-hero"],
    )
    def test_a_back_dated_true_up_does_not_prompt_to_reconcile(
        self, app, auth_client, seed_user, seed_periods_today, revert,
    ):
        """The prompt follows the COVERAGE BOUNDARY, not the click.

        **This is the money defect a neutral adversarial review of plan step
        X-f1c4c reproduced end to end.**  The prompt is keyed on
        ``cash_ledger.reconciled_through`` -- ``MAX(observed_on)`` -- which a
        back-dated assertion does not move.  Before that step every cash true-up
        stamped today, so the submitted day and that maximum were the same value
        by construction; a user-supplied day decoupled them and nothing
        re-coupled them.

        What that cost: submit an OLD statement's balance and the modal opens
        against the LATEST assertion's day, offering purchases made after that
        statement.  Ticking one is a settlement the user cannot have observed,
        and it releases the envelope budget being held for it -- reproduced at
        ``$120.00`` of projected checking balance on money that never left the
        bank.

        The purchase here is dated AFTER the back-dated statement day precisely
        so it is one the statement could not show.

        **Parametrized over all five openers**, because the success response
        BRANCHES on the surface token and a re-review proved the branch
        ungraded: with both original cases submitting no ``revert``, a mutant
        that kept the pre-fix prompt on the ``accounts`` / ``investment`` /
        ``cash`` arm passed the whole 7,843-test suite.  ``cash`` is the cash
        detail page -- the surface a user would most plausibly enter an old
        statement from, and the one that already carries the reconcile section.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            statement_day = today - timedelta(days=20)
            # An outstanding purchase the modal WOULD offer.
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("120.00", today - timedelta(days=2), False, None)],
            )
            # Establish a recent coverage boundary, so the back-dated
            # submission below is genuinely not the account's latest day.
            self._true_up(auth_client, acct_id, "1000.00")

            response = self._true_up(
                auth_client, acct_id, "2500.00",
                observed_on=statement_day, revert=revert,
            )

            html = response.data.decode()
            assert "data-modal-auto-show" not in html, (
                "a back-dated assertion reconciles nothing new, so the prompt "
                "must not ask against a statement it does not describe"
            )
            # The user is TOLD the back-dated write landed, because the cell
            # re-renders from the GOVERNING assertion and would otherwise be
            # indistinguishable from having done nothing.
            #
            # **On ALL FIVE surfaces since plan step X-f1e3.**  It used to ride
            # the per-surface ``#anchor-as-of`` caption and so reached one:
            # three surfaces carry no such element and were skipped outright,
            # and the dashboard's copy was destroyed by the ``balanceChanged``
            # refresh this same response fires (finding N-199).  The mount
            # asserted below is what makes five a property of the structure.
            #
            # **It TARGETS the mount rather than carrying its id, since plan
            # step X-f2-b** (finding N-206).  The swap was an outerHTML
            # replacement of ``#anchor-ack-mount``, so a second back-dated save
            # inside the 8s autohide window destroyed the first toast; it
            # APPENDS now.  What is graded is unchanged -- the acknowledgement
            # reaches the ONE global mount rather than a per-surface element --
            # and the marker for it is the swap attribute that names the mount.
            assert _ACK_SWAP in html, (
                "the acknowledgement must ride the global base.html mount, "
                "not a per-surface element only some surfaces carry"
            )
            assert 'id="anchor-ack-mount"' not in html, (
                "the fragment must APPEND into the mount, never re-emit it: "
                "re-emitting IS the outerHTML swap that destroys a "
                "still-visible predecessor (N-206)"
            )
            # **The negatives are scoped to the acknowledgement fragment, not
            # to the whole body, and that is load-bearing.**  On the grid the
            # response ALSO carries the ``#anchor-as-of`` snippet, which
            # correctly says "as of <today>" because today's assertion is the
            # one that still governs.  A whole-body ``not in`` would read that
            # correct caption as the acknowledgement naming the wrong day and
            # fail the honest code, which is a control that fires on the truth.
            #
            # The as-of snippet is REMOVED before slicing rather than the
            # slice being taken on faith.  Taking everything after the swap
            # marker makes the negatives depend on the order the route
            # concatenates its fragments (``html + as_of + feedback``): move
            # the acknowledgement earlier and ``f"as of {today}" not in ack``
            # would silently start grading the as-of snippet instead, which is
            # a control that stops testing its own subject without failing.
            assert html.count(_ACK_SWAP) == 1
            ack = re.sub(
                r'<small[^>]*id="anchor-as-of".*?</small>', "", html,
                flags=re.DOTALL,
            ).split(_ACK_SWAP, 1)[1]
            assert "Balance recorded" in ack
            # **The attribute that makes the toast VISIBLE, graded** -- the
            # whole feature hangs on one token and nothing saw it.  Vendored
            # Bootstrap carries ``.toast:not(.show){display:none}``, and the
            # only thing that adds ``.show`` to a fragment arriving by
            # out-of-band swap is ``app.js``'s ``[data-toast-auto-show]``
            # handler.  Deleting this attribute leaves the acknowledgement in
            # the DOM and permanently invisible -- N-199's exact symptom --
            # and an adversarial review MEASURED the whole suite still
            # passing on that mutation.  The reconcile prompt's twin marker
            # is already graded two tests down; this one was the only
            # auto-show marker in the app that nothing checked.
            assert "data-toast-auto-show" in ack, (
                "without this marker the toast swaps in with display:none "
                "and the user sees nothing -- the defect N-199 records"
            )
            # The DAY is graded, not just the phrase: a mutant naming the
            # governing assertion's day instead of the submitted one also
            # passed the whole suite, and it is worse than silence -- it
            # affirmatively tells the user the correction landed on a day it
            # did not.
            assert f"as of {statement_day.strftime('%b %-d, %Y')}" in ack
            assert (
                f"as of {today.strftime('%b %-d, %Y')}" not in ack
            ), "the acknowledgement must name the SUBMITTED day, not today"
            # The BALANCE too, for the same reason the day is graded: the
            # acknowledgement is the only evidence the write landed, so naming
            # the governing figure instead of the submitted one would confirm
            # a value the user did not enter.
            assert "$2,500.00" in ack
            assert "$1,000.00" not in ack, (
                "the acknowledgement must name the SUBMITTED balance, not the "
                "one that still governs"
            )

    def test_re_recording_the_same_balance_later_is_acknowledged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A real write that changes no figure says so (finding **N-204**).

        The state the OLD predicate could not see, and the reason the
        acknowledgement stopped being the reconcile prompt's complement.
        Re-record the balance that already governs, for a LATER day, with
        nothing outstanding: a row IS appended (the day differs, so ruling
        R-EQ's duplicate test does not fire), the coverage boundary MOVES, and
        the balance cell re-renders to the figure it already showed -- so the
        submission was the boundary, took the prompt branch, and the prompt was
        empty.  Nothing at all reached the screen.

        Measured on production: this exact shape, an equal balance re-asserted
        on a later day, occurs once in the real Checking account's 57
        assertions.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            statement_day = display_today() - timedelta(days=6)

            self._true_up(
                auth_client, acct_id, "1500.00", observed_on=statement_day,
            )
            response = self._true_up(
                auth_client, acct_id, "1500.00", observed_on=display_today(),
            )

            html = response.data.decode()
            # A row really was written -- the coverage boundary moved -- so
            # this is a write that produced no visible change, not a no-op.
            assert (
                cash_ledger.reconciled_through(acct_id).observed_day
                == display_today()
            )
            assert "data-modal-auto-show" not in html, (
                "nothing is outstanding, so the prompt is empty and the "
                "acknowledgement is the only thing that can speak"
            )
            assert _ACK_SWAP in html
            ack = html.split(_ACK_SWAP, 1)[1]
            assert "Balance recorded" in ack
            assert "$1,500.00" in ack

    def test_a_write_that_moves_the_figure_is_not_acknowledged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The non-vacuity control for the case above.

        Identical fixture and identical days; only the submitted BALANCE
        differs, so the cell re-renders to a new figure and IS its own
        acknowledgement.  Without this, firing the toast unconditionally would
        pass the test above while burying every ordinary true-up under a
        redundant confirmation.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            statement_day = display_today() - timedelta(days=6)

            self._true_up(
                auth_client, acct_id, "1500.00", observed_on=statement_day,
            )
            response = self._true_up(
                auth_client, acct_id, "1600.00", observed_on=display_today(),
            )

            assert _ACK_SWAP not in response.data.decode(), (
                "the balance cell already shows the new figure, so a toast "
                "repeating it is noise"
            )

    def test_the_prompt_still_wins_when_there_is_something_to_reconcile(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A visible prompt IS the acknowledgement, so the toast stays quiet.

        The exclusivity, graded on the one state where the re-key could have
        produced BOTH: the figure does not move (so the toast's first clause
        holds) and the submission is the coverage boundary with a purchase
        outstanding (so the prompt opens).  The prompt is captioned with the
        balance and day just recorded, which is why it discharges the
        acknowledgement rather than competing with it.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("120.00", today - timedelta(days=2), False, None)],
            )
            self._true_up(
                auth_client, acct_id, "1500.00",
                observed_on=today - timedelta(days=6),
            )
            response = self._true_up(
                auth_client, acct_id, "1500.00", observed_on=today,
            )

            html = response.data.decode()
            assert "data-modal-auto-show" in html
            assert _ACK_SWAP not in html, (
                "the prompt names the balance and day just recorded, so a "
                "toast beside it says the same thing twice"
            )

    def test_a_blank_date_box_is_acknowledged_for_TODAY(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The acknowledgement names the resolved day, not a guess.

        The one arm of the day the suite did not grade.  A blank date box
        carries no day; the write door resolves it to the user's today, and the
        route may not re-read the clock to name it -- so it reads the day back
        off the assertion that GOVERNS after the write.  A mutation naming any
        other day (the account's ``created_at``, the previous assertion's day)
        renders a wrong date under a correct balance, which is worse than
        silence: it affirmatively tells the user their record landed on a day
        it did not.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()

            self._true_up(
                auth_client, acct_id, "1500.00",
                observed_on=today - timedelta(days=6),
            )
            # No ``observed_on`` at all -- the shape an older client submits,
            # and the one the fallback exists for.
            response = self._true_up(auth_client, acct_id, "1500.00")

            ack = response.data.decode().split(_ACK_SWAP, 1)[1]
            assert f"as of {today.strftime('%b %-d, %Y')}" in ack
            assert (
                f"as of {(today - timedelta(days=6)).strftime('%b %-d, %Y')}"
                not in ack
            ), "a blank date box means TODAY, not the day that governed before"

    def test_an_idempotent_re_assert_does_not_claim_to_have_recorded(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling R-EQ's UNCHANGED outcome gets its own copy.

        Submitting the governing balance for the governing DAY writes nothing
        and is rolled back, while the route reports success.  That state
        reaches the acknowledgement under the re-keyed predicate -- the figure
        did not move and the prompt is empty -- so the copy has to say what
        happened.  "Balance recorded" over a write that wrote nothing is the
        same class of untruth the acknowledgement exists to remove.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()

            self._true_up(auth_client, acct_id, "1500.00", observed_on=today)
            before = db.session.query(AccountAnchorHistory).filter_by(
                account_id=acct_id,
            ).count()
            response = self._true_up(
                auth_client, acct_id, "1500.00", observed_on=today,
            )

            # Nothing was written, which is the premise of the copy below.
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=acct_id,
            ).count() == before

            ack = response.data.decode().split(_ACK_SWAP, 1)[1]
            assert "Balance confirmed" in ack
            assert "Balance recorded" not in ack, (
                "nothing was recorded -- ruling R-EQ rolled the submission "
                "back -- so the copy must not say it was"
            )
            assert "$1,500.00" in ack

    @pytest.mark.parametrize(
        "revert",
        [None, "dashboard", "accounts", "investment", "cash"],
        ids=["grid", "dashboard", "cockpit", "investment-hero", "cash-hero"],
    )
    def test_an_ordinary_true_up_still_prompts_to_reconcile(
        self, app, auth_client, seed_user, seed_periods_today, revert,
    ):
        """The non-vacuity control for the case above.

        Identical fixture, identical outstanding purchase; only the submitted
        day differs.  Without this, suppressing the prompt UNCONDITIONALLY
        would pass the back-dated case -- and the date box is pre-filled with
        today, so every ordinary one-click true-up now submits an explicit day
        and would have been caught by a blanket rule.

        Both spellings of "now" are graded: the field omitted (an older client,
        and the shape every other true-up test submits) and the field carrying
        today (what the editor actually sends since plan step X-f1c4c).  Both
        are checked on EVERY opener, so the surface branch cannot regress on one
        of them silently.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("120.00", today - timedelta(days=2), False, None)],
            )

            omitted = self._true_up(
                auth_client, acct_id, "1000.00", revert=revert,
            )
            assert "data-modal-auto-show" in omitted.data.decode()

            explicit = self._true_up(
                auth_client, acct_id, "1100.00", observed_on=today,
                revert=revert,
            )
            html = explicit.data.decode()
            assert "data-modal-auto-show" in html, (
                "the editor pre-fills today, so this is the ORDINARY path -- "
                "a rule keyed on 'was a day submitted' would break it"
            )
            # Nothing to acknowledge: this submission IS the boundary.
            assert "recorded as of" not in html

    @staticmethod
    def _entries_of(txn_id):
        """Return a transaction's entries, freshest read, ordered by id."""
        from app.models.transaction_entry import TransactionEntry

        db.session.expire_all()
        return (
            db.session.query(TransactionEntry)
            .filter_by(transaction_id=txn_id)
            .order_by(TransactionEntry.id)
            .all()
        )

    def test_a_true_up_alone_records_no_posting_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """RE-RULED: the true-up route reconciles NOTHING (ruling R-DH (d)).

        The inversion this whole class turns on.  Three past-dated purchases,
        one true-up through the real route: every ``settled_on`` is still NULL
        afterwards, because whether the bank has taken a purchase is not
        derivable from the fact that a balance was entered.

        The response carries the reconcile PROMPT instead -- the question the
        engine used to answer on the user's behalf, now asked.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, None),
                    ("249.71", past, False, None),
                    ("105.77", past, False, None),
                ],
            )

            response = self._true_up(
                auth_client, seed_user["account"].id, "4537.66",
            )

            entries = self._entries_of(txn.id)
            assert len(entries) == 3
            assert all(e.settled_on is None for e in entries)
            # The prompt rides along on the true-up's own response.  The copy
            # widened at plan step X-f2-c2 (ruling R-FD: the panel offers
            # deposits too, so it cannot say "purchases"); what this asserts is
            # unchanged -- that the prompt is IN the true-up's own body.
            assert b"Tick everything your statement shows" in response.data

    def test_ticking_a_purchase_stamps_the_asserted_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST reconcile records the assertion's own day on the ticked rows.

        Two outstanding purchases; the user ticks one.  Its ``settled_on``
        becomes the day the balance was asserted for (today, via the true-up
        route), and the other is untouched -- the whole point of asking rather
        than guessing is that the answer can be partial.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, None),
                    ("249.71", past, False, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            ticked, untouched = self._entries_of(txn.id)
            ticked_id, untouched_id = ticked.id, untouched.id

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(ticked_id)]},
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            by_id = {e.id: e for e in self._entries_of(txn.id)}
            assert by_id[ticked_id].settled_on == display_today()
            assert by_id[untouched_id].settled_on is None
            # The re-rendered panel still offers the one left outstanding.
            assert b"249.71" in response.data

    def test_the_panel_lists_only_what_is_still_outstanding(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET reconcile offers the unrecorded purchases and drops the rest.

        One purchase already carries a posting day and one does not.  Only the
        second is offered: a purchase whose posting day is recorded is not
        outstanding, whatever that day is, so listing it would ask the user to
        confirm something they already have.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, past),
                    ("249.71", past, False, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            )

            assert response.status_code == 200
            assert b"249.71" in response.data
            assert b"106.86" not in response.data

    def test_a_purchase_made_after_the_statement_is_not_offered(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """RE-RULED from "future-dated entries are not cleared".

        A purchase made AFTER the day the balance was read cannot be inside it,
        so it is neither listed nor stamped -- and a forged id for it changes
        nothing.  The bound is real rather than cosmetic: stamping it would
        write a ``settled_on`` earlier than its own ``purchased_on``, which
        ``ck_transaction_entries_settled_not_before_purchase`` refuses at the
        database.  Filtering here makes that constraint a backstop instead of a
        reachable 500.
        """
        with app.app_context():
            future = display_today() + timedelta(days=7)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("50.00", future, False, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "5000.00")
            entry_id = self._entries_of(txn.id)[0].id

            listed = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            )
            # Asserted on the entry's OWN checkbox, not on its amount as a
            # substring.  The panel offers the envelope's close too since plan
            # step X-f2-c2, and that row's figure is sum(entries) = $50.00, so
            # a ``b"50.00" not in`` assertion now reads the wrong row's money
            # and would pass or fail for reasons unrelated to this bound.
            assert (
                f'name="entry_ids" value="{entry_id}"'.encode()
                not in listed.data
            )

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert response.status_code == 200
            assert self._entries_of(txn.id)[0].settled_on is None

    def test_a_purchase_on_a_settled_parent_IS_offered(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """RE-RULED TWICE; this is the third and current ruling.

        It began as "entries on non-projected parents are not cleared", became
        "the entry reservation prices only PROJECTED rows, so a purchase on a
        settled parent is inert", and is now the opposite of both (developer,
        2026-08-17).  Ruling **R-FM** falsified the "inert" premise one plan
        step earlier: ``cash_ledger.settled_cash_leg`` subtracts every POSTED
        purchase from a settled row's close, so recording the day moves that
        purchase's cash off the close's day and onto the bank's.  The total
        never moves; the DAY does, and a paper statement is reconciled by day.

        Driven through the ROUTE rather than the service, which is the half its
        sibling in ``test_reconcile_service`` cannot see: the panel must LIST
        the purchase as well as accept it, and the two are different code.

        The parent is settled through ``status_seam.apply_status_change``
        rather than by assigning ``status_id``.  A raw assignment builds a
        settled row carrying no settle day and no record -- one no door in the
        app can create, and one ``ck_transactions_settle_day_needs_a_record`` and
        ``row_valuation.settled_figure`` between them exist to keep out of the
        database and out of a balance.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, None),
                ],
            )
            paid = db.session.query(Status).filter_by(name="Paid").one()
            status_seam.apply_status_change(
                txn, paid.id,
                settlement=settlement_if_settling(txn, paid.id),
            )
            db.session.commit()
            self._true_up(auth_client, seed_user["account"].id, "5000.00")
            entry_id = self._entries_of(txn.id)[0].id

            listed = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            )
            assert b"100.00" in listed.data

            auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert self._entries_of(txn.id)[0].settled_on is not None

    def test_an_already_recorded_purchase_is_not_re_stamped(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """RE-RULED from "already cleared entries unchanged" -- idempotence.

        A purchase whose exact posting day the user already sharpened on the
        entry form keeps that day.  Re-submitting its id must not overwrite it
        with the assertion's coarser upper bound: the outstanding scope is
        ``settled_on IS NULL``, so the row simply does not match.
        """
        with app.app_context():
            past = display_today() - timedelta(days=2)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, past),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "5000.00")
            entry_id = self._entries_of(txn.id)[0].id

            auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert self._entries_of(txn.id)[0].settled_on == past

    def test_reconciling_one_account_does_not_touch_another(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A reconcile records ONLY the reconciled account's purchases.

        KEPT verbatim in intent from the retired bulk-clear class, because the
        invariant is unchanged by the re-ruling and is still the one that
        misprices real money.  Accounts carry no per-type uniqueness, so a user
        may hold more than one checking account.  Reconciling account A records
        what A's statement showed; recording B's purchases there would drop B's
        reservation without ever raising B's anchor, silently inflating B's
        projected balance (#8).  It fails under an owner-only filter and passes
        once the filter is scoped by ``account_id``.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)

            # Second checking account on the SAME user.
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            account_b = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Checking 2",
                    anchor_balance=Decimal("2000.00"),
                ),
            )
            db.session.add(account_b)
            db.session.commit()
            account_b_id = account_b.id

            txn_a = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("60.00", past, False, None)],
            )
            txn_b = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("75.00", past, False, None)],
                account=account_b,
            )

            # BOTH accounts have an asserted day, so B's purchase failing to
            # be recorded cannot be blamed on B having no assertion.
            self._true_up(auth_client, seed_user["account"].id, "5000.00")
            self._true_up(auth_client, account_b_id, "3000.00")

            entry_a_id = self._entries_of(txn_a.id)[0].id
            entry_b_id = self._entries_of(txn_b.id)[0].id

            # Reconcile account A, submitting BOTH ids -- the cross-account id
            # is exactly the forged submission the scope has to reject.
            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_a_id), str(entry_b_id)]},
            )
            assert response.status_code == 200

            assert self._entries_of(txn_a.id)[0].settled_on == display_today()
            assert self._entries_of(txn_b.id)[0].settled_on is None

    def test_the_stamped_day_is_the_ASSERTED_day_not_today(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The route resolves the account's asserted day; it does not use today.

        **The two are the same day in every other test in this class**, because
        each trues up through the PATCH route and ``apply_anchor_true_up``
        stamps ``display_today()``.  So none of them can tell
        ``cash_ledger.reconciled_through(account.id)`` apart from
        ``display_today()`` -- measured: substituting the latter in the POST
        door (then ``accounts.anchor.reconcile_purchases``, now
        ``accounts.reconcile.record_reconciliation``) left the whole 7,721-test
        suite green.  This is the case that separates them, and it is the only
        one.

        ``observed_on`` is USER-SUPPLIED (plan step 2), so a back-dated
        assertion is an ordinary state: "my statement is dated the 3rd, not
        today".  Here the account's latest assertion is for the day after the
        first period started -- weeks in the past -- and the ticked purchase
        must be stamped with THAT day.

        What the wrong clock costs, and why it is not merely untidy: the
        purchase would get ``settled_on = today``, which is AFTER the asserted
        day, so ``ReconciledThrough.covers`` answers False, the reservation never
        drops, and the projection stays low by the whole purchase.  Worse, the
        row now fails the ``settled_on IS NULL`` clause of
        ``reconcile_service._purchases._outstanding_scope``, so the panel can
        never offer it again -- the user cannot fix it from the surface that
        broke it.
        """
        with app.app_context():
            account = seed_user["account"]
            asserted_on = seed_periods_today[0].start_date + timedelta(days=1)
            purchased_on = seed_periods_today[0].start_date
            # The premise: the asserted day is genuinely in the past, so
            # "stamped the asserted day" and "stamped today" are different
            # answers rather than the same one twice.
            assert asserted_on < display_today()

            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("64.20", purchased_on, False, None)],
            )
            append_balance_assertion(
                db.session, account, seed_periods_today[0],
                Decimal("1000.00"), settle_instant_on(asserted_on),
            )
            db.session.commit()
            entry_id = self._entries_of(txn.id)[0].id

            # The panel offers it against the BACK-DATED day.
            listed = auth_client.get(f"/accounts/{account.id}/reconcile")
            assert b"64.20" in listed.data

            response = auth_client.post(
                f"/accounts/{account.id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )
            assert response.status_code == 200

            stamped = self._entries_of(txn.id)[0].settled_on
            assert stamped == asserted_on
            assert stamped != display_today()

    def test_a_companion_cannot_reach_the_reconcile_route(
        self, app, companion_client, seed_user, seed_periods_today,
    ):
        """Owner-only, and a companion gets 404 rather than 403.

        The COMPANION is the case that matters, not an unrelated stranger: a
        companion is authenticated AND holds granted access to this owner's
        transactions and their entries (``TestCompanionAccess`` in
        ``test_routes/test_entries.py``), so ``@require_owner`` refusing them is
        a real decision rather than a trivial one.  Reconciling is an owner
        judgement about the owner's own bank statement.

        The project's security response rule: 404 for both "not found" and
        "not yours", so a probe cannot use the status code as an existence
        oracle.  Both doors carry ``@require_owner``, so both are asserted --
        a read that leaked the outstanding list would disclose the owner's
        spending even if the write were refused.
        """
        with app.app_context():
            account_id = seed_user["account"].id

        assert companion_client.get(
            f"/accounts/{account_id}/reconcile",
        ).status_code == 404
        assert companion_client.post(
            f"/accounts/{account_id}/reconcile",
            data={"entry_ids": ["1"]},
        ).status_code == 404

    def test_reconciling_a_savings_account_leaves_checking_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Successor to the retired "a non-checking true-up does not clear".

        The bulk clear was checking-only by construction; the reconcile is
        scoped by ``account_id`` instead, so the property survives the re-ruling
        with a different mechanism and is re-asserted rather than assumed
        covered by the two-checking-accounts case beside it.  A debit purchase
        never reaches a savings statement, so reconciling savings must offer
        nothing and record nothing.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("90.00", past, False, None)],
            )
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.commit()
            savings_id = savings.id
            entry_id = self._entries_of(txn.id)[0].id

            self._true_up(auth_client, savings_id, "1500.00")

            listed = auth_client.get(f"/accounts/{savings_id}/reconcile")
            assert b"90.00" not in listed.data

            auth_client.post(
                f"/accounts/{savings_id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert self._entries_of(txn.id)[0].settled_on is None

    def test_a_submitted_id_that_is_not_a_number_does_not_raise(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Finding N-136: this door used to 500 on a forged ``entry_ids``.

        ``str.isdigit()`` is true for 888 characters and 128 of them make
        ``int()`` raise, and ``app/error_handlers.py`` has no ``ValueError``
        arm -- so a superscript two reached the user as an unhandled 500.
        (The 500 HANDLER still renders ``errors/500.html`` with ``DEBUG``
        off, so no traceback was shown; an earlier wording here said "and a
        stack trace" and was wrong about the response, not the raise.)  The
        submission is now answered: the value names no row, so it is
        dropped, and the purchase it could not name is left outstanding.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": ["\N{SUPERSCRIPT TWO}"]},
            )

            assert response.status_code == 200
            assert self._entries_of(txn.id)[0].settled_on is None
            # Still offered, so the panel tells the truth about what is left.
            assert b"106.86" in response.data

    def test_an_id_spelled_in_another_digit_script_reconciles_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One row id has ONE spelling (plan step X-ae's ASCII ruling).

        Measured before the fix: Eastern Arabic numerals pass ``isdigit()``
        AND convert cleanly, so this exact submission returned 200 and really
        stamped the entry.  The crash fix alone would have kept that -- an id
        would have had ten spellings, only one of which any form of ours
        emits.  It is the same purchase and the same real id, submitted in
        the other script, and now it records nothing.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            entry_id = self._entries_of(txn.id)[0].id

            eastern_arabic = str(entry_id).translate(
                str.maketrans("0123456789", "٠١٢٣٤"
                                            "٥٦٧٨٩"),
            )
            # The premise: this really is the same id, and it really does
            # satisfy the predicate the route used to guard with.
            assert eastern_arabic.isdigit()
            assert int(eastern_arabic) == entry_id

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [eastern_arabic]},
            )

            assert response.status_code == 200
            assert self._entries_of(txn.id)[0].settled_on is None

    def test_one_unparseable_id_does_not_discard_the_valid_ones(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A junk value costs its own id, not the whole submission.

        The set-operation posture the writer already takes toward an id that
        is real but not the user's -- refusing the batch would punish the
        user for a value their browser never sent.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, None),
                    ("249.71", past, False, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            ticked, untouched = self._entries_of(txn.id)
            ticked_id, untouched_id = ticked.id, untouched.id

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [
                    "\N{SUPERSCRIPT TWO}", str(ticked_id), "not-an-id",
                ]},
            )

            assert response.status_code == 200
            by_id = {e.id: e for e in self._entries_of(txn.id)}
            assert by_id[ticked_id].settled_on == display_today()
            assert by_id[untouched_id].settled_on is None

    def test_the_panel_404s_for_an_account_this_page_does_not_serve(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Finding **N-216**: both reconcile doors take the page's kind gate.

        The cash detail page and its other three fragments all resolve through
        ``_cash_page.load_cash_account_or_404``, which 404s the kinds this page
        does not serve; these two routes guarded on OWNERSHIP alone, so
        ``GET /accounts/<loan id>/reconcile`` rendered a cash-reconciliation
        panel for an amortizing account and answered "every purchase recorded
        on this account has been matched to your bank" -- a sentence that is
        not about a loan.

        **A 200 with an empty list is what it did, which is why a
        content assertion could not have caught it.** The list is empty because
        a loan's ``account_anchor_history`` carries only its origination row, so
        the offer set's date bound admits nothing: a property of the DATA, and
        one plan step X-f2-c2 removes when the set widens to transactions and a
        loan's projected transfer shadows become tickable. The status code is
        therefore the only honest oracle here.

        Both doors are asserted: a READ that rendered the panel is the leak, and
        a WRITE that accepted a tick against a loan is the money.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Van Loan")
            db.session.commit()
            loan_id = loan.id

        assert auth_client.get(
            f"/accounts/{loan_id}/reconcile",
        ).status_code == 404
        assert auth_client.post(
            f"/accounts/{loan_id}/reconcile",
            data={"entry_ids": ["1"]},
        ).status_code == 404

    def test_a_true_up_on_a_kind_this_panel_refuses_prompts_NOTHING(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Finding **N-216**'s THIRD door, and the regression the first fix made.

        The reconcile panel has three doors, not two: the two routes, and the
        post-true-up modal ``prompt_fragment`` mounts out of band.  The anchor
        editor opens on every kind except an amortizing one, so a 401(k) or
        Property true-up reaches that modal -- and once the two ROUTES 404'd
        those kinds, the modal still rendered with its checkboxes while its
        submit button POSTed to a door that refused.  **htmx swaps only 2xx**,
        so the button did nothing, silently and forever: strictly worse than
        the state before the gate, and the exact failure
        ``app/error_handlers.py`` refuses to ship for a mutating fragment.

        The premise is asserted, not assumed: the true-up itself must SUCCEED
        (200) on this kind, or the modal's absence would prove nothing.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            retirement = create_account_of_type(
                seed_user, db.session, "401(k)", "Empower 401(k)",
                anchor_balance=Decimal("50000.00"),
            )
            db.session.commit()
            retirement_id = retirement.id
            # An outstanding purchase ON that account, so the offer set is
            # non-empty and an ungated modal would really render.
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("40.00", past, False, None)],
                account=retirement,
            )

            trued_up = self._true_up(auth_client, retirement_id, "51000.00")

            assert trued_up.status_code == 200
            body = trued_up.data.decode()
            assert "data-modal-auto-show" not in body
            assert "Mark ticked as posted" not in body

    def test_purchases_are_grouped_under_their_own_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling **R-EW**: a purchase nests under the thing it belongs to.

        The flat list this replaced named each purchase's parent in a trailing
        fragment on its own line, so two purchases against one envelope repeated
        that envelope's name twice and a purchase against another sat between
        them under whatever purchase date ordered it. Grouped, each envelope's
        name appears ONCE and its purchases are contiguous beneath it.

        Graded on the rendered document rather than on the producer, because the
        producer's grouping is already pinned in
        ``test_services/test_reconcile_service.py`` and what this asserts is that
        the panel consumes it: the name appears exactly once per envelope, each
        block carries its own subtotal, and every purchase is still individually
        tickable (the two acts stay independent, R-EW's second half).
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            groceries = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("40.00", past, False, None),
                    ("60.00", past, False, None),
                ],
            )
            gas = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("25.00", past, False, None)],
                name="Gas",
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            # (i) Each block names itself ONCE, not once per purchase --
            # which is the whole difference between a heading and a trailing
            # fragment on every line.
            assert body.count("Groceries") == 1
            assert body.count("Gas") == 1
            # (ii) Its own subtotal: 40.00 + 60.00 = 100.00, hand-computed.
            assert "$100.00" in body
            # (iii) Every purchase is still its OWN tick (R-EW: the two acts
            # stay independent).
            grocery_ids = [e.id for e in self._entries_of(groceries.id)]
            gas_ids = [e.id for e in self._entries_of(gas.id)]
            for entry_id in grocery_ids + gas_ids:
                assert f'value="{entry_id}"' in body
            # (iv) And they are CONTIGUOUS beneath their own heading, which is
            # what "nested" means and what (i) alone does not prove: a flat
            # list that merely printed each name once would interleave.  Read
            # off the rendered document by position rather than asserted about
            # markup, so a restyle does not fail it and a re-ordering does.
            at = body.index
            assert at("Groceries") < min(
                at(f'value="{i}"') for i in grocery_ids
            )
            assert max(
                at(f'value="{i}"') for i in grocery_ids
            ) < at("Gas") < min(at(f'value="{i}"') for i in gas_ids)

    # ── Plan step X-f2-c2: the door settles ROWS too ──────────────

    def test_one_POST_settles_a_purchase_AND_its_parents_close(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The ORDER of the two writers, graded end to end.

        **This is the control for the defect the ordering exists to prevent.**
        The purchase arm's scope requires a PROJECTED parent, so settling the
        envelope's close FIRST takes that parent out of scope and every ticked
        purchase on it is silently skipped -- reported as success, with the
        purchases still reading outstanding on the next render.  Ticking a whole
        block at once is the ordinary way to walk a statement, not an exotic
        one.

        Shown to FIRE: swapping the two service calls in
        ``record_reconciliation`` leaves ``settled_on`` NULL on the purchase
        while the envelope settles.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            entry_id = self._entries_of(txn.id)[0].id

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "entry_ids": [str(entry_id)],
                    "transaction_ids": [str(txn.id)],
                },
            )

            assert response.status_code == 200
            db.session.expire_all()
            assert self._entries_of(txn.id)[0].settled_on == display_today()
            settled = db.session.get(Transaction, txn.id)
            assert settled.settled_on == display_today()
            # A ``purchases`` record stores no figure: the row's own entries
            # state it (plan step X-au-c3).
            assert settled_figure(settled) == Decimal("106.86")

    def test_a_submitted_amount_box_corrects_what_the_row_books(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling **R-FB**, through the real form field.

        The panel renders ``actual_amount-<id>`` for a correctable row; the
        route pairs it back to its own row by NAME rather than by position, so
        this grades the pairing as well as the write.  An envelope with no
        entries is the correctable case (ruling **R-FF**).
        """
        with app.app_context():
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(txn.id)],
                    f"settled_amount-{txn.id}": "412.09",
                },
            )

            assert response.status_code == 200
            db.session.expire_all()
            settled = db.session.get(Transaction, txn.id)
            assert settled.settled_amount == Decimal("412.09")
            assert settled.estimated_amount == Decimal("500.00")

    def test_a_malformed_amount_refuses_and_commits_NOTHING(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The panel's rejection surface, and the atomicity behind it.

        A crafted POST can send anything; the browser's ``type="number"`` is a
        convenience, not a boundary.  Without the designed refusal this is an
        unhandled Marshmallow error -- a 500 on a write door.

        **The ticked purchase must not survive either**, which is what makes
        this an atomicity control rather than a validation one: both writers
        run inside one transaction, so a refusal rolls back the half that had
        already succeeded.  It answers 400 WITH the designed-fragment header,
        because htmx leaves a bare 4xx unswapped and the button would read as
        broken.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            entry_id = self._entries_of(txn.id)[0].id

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "entry_ids": [str(entry_id)],
                    "transaction_ids": [str(txn.id)],
                    f"settled_amount-{txn.id}": "-12",
                },
            )

            assert response.status_code == 400
            assert response.headers["Shekel-Designed-Fragment"] == "1"
            db.session.expire_all()
            assert self._entries_of(txn.id)[0].settled_on is None
            assert db.session.get(Transaction, txn.id).settled_on is None

    def test_a_tick_that_landed_on_nothing_SAYS_so(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A submission the scope dropped is reported, not rendered as success.

        Both arms drop an out-of-scope id silently -- the set-operation form of
        "404 for not-found and not-yours" -- and the ordinary way to reach it is
        a second device settling the same rows while a statement is being
        walked.  For the purchase arm that hides a column stamp; for this one it
        hides a status change, an amount and a ledger posting, so "saved" would
        be a false sentence about money.

        Shown to FIRE: dropping the notice renders the plain success panel.
        """
        with app.app_context():
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            # Settle it out from under the panel, exactly as another device
            # would, then submit the tick the stale panel still shows.
            auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"transaction_ids": [str(txn.id)]},
            )

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"transaction_ids": [str(txn.id)]},
            )

            assert response.status_code == 200
            assert b"had already been settled" in response.data or (
                b"changed while you were reconciling" in response.data
            )

    def test_a_correctable_row_INSIDE_a_block_still_gets_its_box(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling **R-FF** is honoured in BOTH renderings of a settle row.

        The panel had two copies of that row and read ``is_correctable`` in only
        one, so a block with purchases printed a static figure whatever the
        producer said.  Reachable: a template's ``is_envelope`` is editable, so
        turning purchase-tracking off after its rows carry entries leaves a
        block with children whose settle IS correctable -- and the correction
        box vanished silently, on the screen ruling R-FB added it to.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("50.00", past, False, None)],
            )
            # Purchase-tracking off, entries already recorded.
            txn.template.is_envelope = False
            db.session.commit()
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            # The entry checkbox proves the block took the WITH-CHILDREN arm,
            # which is the arm that used to ignore the flag.
            assert 'name="entry_ids" value="' in body
            assert f'name="settled_amount-{txn.id}"' in body

    def test_the_write_door_still_404s_a_kind_this_panel_does_not_serve(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Finding **N-216**'s gate, re-graded now that the door writes more.

        The kind gate ran before the offer set widened, so its own oracle was
        the status code and nothing else -- a loan answered 200 with an empty
        list only because a loan's ``account_anchor_history`` carries just its
        origination row.  X-f2-c2 removes that accident: a loan's projected
        rows would now be offered, so the gate is the only thing refusing.
        """
        with app.app_context():
            # The gate's own predicate is ``has_amortization`` (plus
            # appreciation and the two investment categories), so the fixture
            # picks the type by THAT column rather than by a name.
            loan_type = (
                db.session.query(AccountType)
                .filter_by(has_amortization=True).first()
            )
            loan = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Van Loan",
                    account_type_id=loan_type.id,
                    anchor_balance=Decimal("9000.00"),
                ),
            )
            db.session.commit()

            assert auth_client.get(
                f"/accounts/{loan.id}/reconcile",
            ).status_code == 404
            assert auth_client.post(
                f"/accounts/{loan.id}/reconcile", data={},
            ).status_code == 404


# ── Account Type CRUD ─────────────────────────────────────────────



class TestTheReconcileRoutesUngradedBranches:
    """Finding **N-232**: three branches of the POST that nothing could fail.

    Opened by X-f2-c2's own adversarial correctness review.  The route's code
    is correct under trace -- which is exactly why the holes survived -- but
    three of its arms had ZERO coverage, and one of them is the arm the route's
    own docstring argues matters most:

    * the PARTIAL-landing notice (``recorded < asked``).  The one test that
      reached this code took the nothing-landed arm, so deleting the partial
      branch or swapping the two message constants left the suite green.  For
      the purchase arm a dropped tick hides a column stamp; since X-f2-c2 it
      hides a status change, an amount and a ledger posting.
    * the ``StaleDataError`` refusal, which renders the panel as a designed 400
      rather than letting htmx leave a broken button.
    * that an amount box submitted WITHOUT its checkbox is INERT.  That is the
      modal case rather than an exotic one: the panel renders a box on every
      correctable row and an HTML form posts them ALL, so a five-row panel
      posts five figures on every submit.
    """

    _make_grocery_txn_with_entries = (
        TestTheReconcileRoute._make_grocery_txn_with_entries
    )
    _true_up = staticmethod(TestTheReconcileRoute._true_up)
    _entries_of = staticmethod(TestTheReconcileRoute._entries_of)

    @staticmethod
    def _bill(seed_user, period, *, name="Electricity", amount="180.00"):
        """Create a projected NON-envelope row -- correctable, so it draws a box."""
        from app.models.transaction_template import TransactionTemplate

        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            name=name,
            default_amount=Decimal(amount),
            is_envelope=False,
        )
        db.session.add(template)
        db.session.flush()
        txn = Transaction(
            template_id=template.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name=name,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal(amount),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_a_PARTLY_landed_submission_says_so(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One tick lands, one was already settled: the partial notice, not "saved".

        The ordinary way to reach it is a second device settling a row while a
        statement is being walked.  Answered as a 200 -- the request succeeded
        and the refreshed list is the useful part -- with the sentence that
        says half of it was left alone.  Shown to FIRE: swapping the partial
        and stale message constants fails this and its sibling below.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, None),
                    ("249.71", past, False, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            lands, already = self._entries_of(txn.id)
            lands_id, already_id = lands.id, already.id
            # The second entry is settled behind the panel's back -- the second
            # device.  The form still posts both ids.
            record_settle_day(db.session.get(type(already), already_id), an_entered_day(past))
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(lands_id), str(already_id)]},
            )

            assert response.status_code == 200
            assert b"had already been settled elsewhere" in response.data
            assert b"nothing was recorded" not in response.data
            by_id = {e.id: e for e in self._entries_of(txn.id)}
            assert by_id[lands_id].settled_on == display_today()

    def test_a_submission_that_lands_on_NOTHING_says_something_else(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control for the sibling above: the two arms say different things.

        Without both, one message constant answers every case and the branch
        that chooses between them grades nothing.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            entry_id = self._entries_of(txn.id)[0].id
            record_settle_day(
                db.session.get(
                    type(self._entries_of(txn.id)[0]), entry_id,
                ),
                an_entered_day(past),
            )
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert response.status_code == 200
            assert b"nothing was recorded" in response.data
            assert b"had already been settled elsewhere" not in response.data

    def test_a_stale_settle_re_renders_the_panel_as_a_designed_400(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A concurrent commit mid-reconcile is a designed refusal, not a 500.

        The race is engineered the way ``test_optimistic_locking_c18`` does it:
        a ``before_update`` mapper event bumps the row's version from a
        separate connection during the UPDATE, defeating the version-pinned
        WHERE.  The response carries ``Shekel-Designed-Fragment`` because htmx
        leaves a 4xx non-swapping, so a refusal without it renders NOTHING and
        the button reads as broken -- worse than the error it reports.  Shown
        to FIRE: deleting the route's ``except StaleDataError`` arm turns this
        into a 500.
        """
        from sqlalchemy import event

        with app.app_context():
            bill = self._bill(seed_user, seed_periods_today[0])
            bill_id = bill.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            fired = {"flag": False}

            def make_stale(_mapper, _connection, target):
                if fired["flag"] or target.id != bill_id:
                    return
                fired["flag"] = True
                with db.engine.connect() as conn:
                    conn.execute(
                        text(
                            "UPDATE budget.transactions "
                            "SET version_id = version_id + 1 WHERE id = :id"
                        ),
                        {"id": bill_id},
                    )
                    conn.commit()

            event.listen(Transaction, "before_update", make_stale)
            try:
                response = auth_client.post(
                    f"/accounts/{seed_user['account'].id}/reconcile",
                    data={"transaction_ids": [str(bill_id)]},
                )
            finally:
                event.remove(Transaction, "before_update", make_stale)

            assert response.status_code == 400, response.data
            assert response.headers.get("Shekel-Designed-Fragment") == "1"
            assert b"changed while you were reconciling" in response.data

            db.session.expire_all()
            assert db.session.get(Transaction, bill_id).settled_on is None

    def test_an_amount_box_submitted_WITHOUT_its_checkbox_is_inert(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The modal case: an HTML form posts every box it renders.

        The panel prefills a box on every correctable row, so a five-row panel
        posts five figures on every submit whether or not the user ticked
        those rows.  Nothing may happen to a row whose id is not in
        ``transaction_ids`` -- the corrections map is passed through and the
        arm's own scope is what decides, so an unticked row is simply not in
        the narrowed set.
        """
        with app.app_context():
            ticked = self._bill(seed_user, seed_periods_today[0],
                                name="Electricity", amount="180.00")
            unticked = self._bill(seed_user, seed_periods_today[0],
                                  name="Water", amount="60.00")
            ticked_id, unticked_id = ticked.id, unticked.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(ticked_id)],
                    f"settled_amount-{ticked_id}": "175.42",
                    f"settled_amount-{unticked_id}": "1.00",
                },
            )
            assert response.status_code == 200

            db.session.expire_all()
            settled = db.session.get(Transaction, ticked_id)
            left_alone = db.session.get(Transaction, unticked_id)
            assert settled.settled_amount == Decimal("175.42")
            assert left_alone.settled_amount is None
            assert left_alone.settled_on is None
            assert left_alone.status.name == "Projected"

    def test_a_forged_box_for_ANOTHER_accounts_row_changes_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A crafted correction cannot reach outside the reconciled account.

        The ids never leave the arm's scope, so a row on a second account is
        not in the narrowed set and its amount box is read by nothing.  The
        negative half of the "the scope is the security property" claim, taken
        from the ROUTE rather than from the service.
        """
        with app.app_context():
            other = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Second Checking",
                    account_type_id=seed_user["account"].account_type_id,
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.flush()
            elsewhere = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [], account=other,
                name="Elsewhere",
            )
            elsewhere_id = elsewhere.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(elsewhere_id)],
                    f"settled_amount-{elsewhere_id}": "999.99",
                },
            )
            assert response.status_code == 200
            assert b"nothing was recorded" in response.data

            db.session.expire_all()
            untouched = db.session.get(Transaction, elsewhere_id)
            assert untouched.settled_amount is None
            assert untouched.settled_on is None

    def test_a_row_the_verb_would_not_read_a_box_for_renders_NONE(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control for ruling **R-FF**'s box.

        An envelope CARRYING entries derives its amount, so the settle verb
        ignores a submitted figure -- and the panel must therefore render no
        input for it.  Without this the correctable cases are satisfied by a
        panel that draws a box on every row and silently drops two thirds of
        what it collects.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            envelope = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("106.86", past, False, None)],
            )
            bill = self._bill(seed_user, seed_periods_today[0])
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert f'name="settled_amount-{bill.id}"' in body
            assert f'name="settled_amount-{envelope.id}"' not in body

    def test_a_correction_ABOVE_the_columns_domain_is_a_designed_refusal(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A figure the column cannot hold is a 400, never a 500 mid-statement.

        ``budget.transactions.settled_amount`` is ``numeric(12, 2)``, so a
        figure at or above ``10 ** 10`` cannot be stored.  The schema bounded
        the field below (``>= 0``) and not above, so such a value passed
        validation, reached the settle verb and died at the DATABASE as a
        ``DataError`` -- an unhandled 500 on a door an ordinary crafted POST
        reaches.

        **The blast radius is what makes it worth a test rather than a shrug.**
        A statement walk is ONE act committed once (the route's own
        "four purchases and their envelope's close mean all five or none"), so
        a single unstorable box discards every other tick submitted with it.

        Shown to FIRE: removing the field's ``max`` turns this into a 500.
        """
        with app.app_context():
            ticked = self._bill(seed_user, seed_periods_today[0])
            alongside = self._bill(seed_user, seed_periods_today[0],
                                   name="Water", amount="60.00")
            ticked_id, alongside_id = ticked.id, alongside.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(ticked_id), str(alongside_id)],
                    f"settled_amount-{ticked_id}": "10000000000.00",
                },
            )

            assert response.status_code == 400, response.status_code
            assert response.headers.get("Shekel-Designed-Fragment") == "1"

            # The whole act was refused, so the row ticked ALONGSIDE the bad
            # box is still outstanding rather than half-committed.
            db.session.expire_all()
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            for row_id in (ticked_id, alongside_id):
                row = db.session.get(Transaction, row_id)
                assert row.status_id == projected_id
                assert row.settled_on is None
                assert row.settled_amount is None


class TestTheTransferArmThroughItsROUTE:
    """Plan step **X-f2-c3**: the TRANSFER arm, from the door rather than the service.

    The arm's rules are graded exhaustively at the service
    (``test_services/test_reconcile_service.py::TestTheTransferArm``).  What
    is graded HERE is the part only the route and the template can get wrong:
    that a transfer reaches the panel at all, that its section prints the
    sentence saying a SECOND account moves, and that a tick posted as an
    ordinary form submission settles all three rows.

    **The section note is the one piece of copy on this screen that describes
    a side effect.**  Every other row settles what the user is looking at; a
    transfer settles the matching row on an account the panel is not showing.
    A template branch nothing renders is a sentence that can be deleted
    without a single test noticing, on the screen whose whole job is telling
    the user what a tick is about to do.
    """

    _true_up = staticmethod(TestTheReconcileRoute._true_up)

    @staticmethod
    def _outstanding_transfer(seed_user, period, amount="75.00"):
        """Return ``(transfer, shadow on the reconciled account)``, projected.

        Dated into the past through the period it sits in, so the shared
        attribution bound admits it against a statement asserted today.
        """
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                name="Savings",
                account_type_id=seed_user["account"].account_type_id,
                anchor_balance=Decimal("100.00"),
            ),
        )
        db.session.flush()
        transfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings, period,
            amount=Decimal(amount),
        )
        db.session.commit()
        shadow = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id == transfer.id,
                Transaction.account_id == seed_user["account"].id,
            )
            .one()
        )
        return transfer, shadow

    def test_the_panel_prints_the_section_and_says_a_SECOND_account_moves(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The TRANSFER section renders its heading AND its note.

        Both are template branches that no test reached: the heading comes
        from ``group.section.label`` and the note from ``group.section.note``,
        which only :class:`OfferKind.TRANSFER` supplies.  Shown to FIRE:
        deleting the ``group.section.note`` block fails this.
        """
        with app.app_context():
            _transfer, shadow = self._outstanding_transfer(
                seed_user, seed_periods_today[0],
            )
            shadow_id = shadow.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            assert f'value="{shadow_id}"' in body, (
                "the transfer's shadow must be offered at all"
            )
            assert "Transfers" in body
            assert "settles both sides" in body
            assert "the matching row on the other account" in body

    def test_a_posted_tick_settles_the_parent_and_BOTH_legs(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The arm end to end: one form POST moves three rows.

        The service case proves the verb does it; this proves the ROUTE
        reaches the verb -- that the shadow's id posted in the shared
        ``transaction_ids`` field lands in the transfer arm rather than the
        transaction one, which would refuse a shadow outright.
        """
        with app.app_context():
            transfer, shadow = self._outstanding_transfer(
                seed_user, seed_periods_today[0],
            )
            transfer_id, shadow_id = transfer.id, shadow.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"transaction_ids": [str(shadow_id)]},
            )
            assert response.status_code == 200
            assert b"already been settled" not in response.data

            db.session.expire_all()
            # The status is compared by ID, not by its display name -- the
            # project's reference-table rule ("IDs for logic, strings for
            # display only"), which the DONE member makes concrete: its
            # ``name`` reads "Paid".
            done_id = ref_cache.status_id(StatusEnum.DONE)
            parent = db.session.get(Transfer, transfer_id)
            assert parent.status_id == done_id
            legs = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer_id)
                .all()
            )
            assert len(legs) == 2, "Transfer Invariant 1"
            for leg in legs:
                assert leg.status_id == done_id
                assert leg.settled_on == display_today()
                # Nobody typed a figure, so the record's basis is ``derived``
                # -- which is where "did a human correct this" lives since plan
                # step X-au-c3, rather than in the figure's NULL-ness.
                assert leg.settled_basis_id == settlement_basis_id(
                    SettlementBasisEnum.DERIVED,
                )

    def test_a_correction_typed_on_a_transfer_lands_on_BOTH_legs(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A figure read off the statement is a fact about the whole act.

        Both legs carry it, because Transfer Invariant 3 says the three rows
        state one amount -- a correction recorded on one side only would leave
        the two accounts disagreeing about how much money moved between them.
        """
        with app.app_context():
            transfer, shadow = self._outstanding_transfer(
                seed_user, seed_periods_today[0],
            )
            transfer_id, shadow_id = transfer.id, shadow.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(shadow_id)],
                    f"settled_amount-{shadow_id}": "80.25",
                },
            )
            assert response.status_code == 200

            db.session.expire_all()
            legs = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer_id)
                .all()
            )
            for leg in legs:
                assert leg.settled_amount == Decimal("80.25")
                assert owned_contribution(leg) == Decimal("80.25")

    def test_an_ECHOED_prefill_on_a_transfer_records_no_correction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control for the case above, at the ROUTE.

        The panel PREFILLS every correctable box, so an untouched tick posts
        the figure the row would book anyway.  Without this case the
        correction test is satisfied by a verb that writes whatever it is
        handed, which would populate ``actual_amount`` on every settled
        transfer and destroy the signal that says a human typed one.
        """
        with app.app_context():
            transfer, shadow = self._outstanding_transfer(
                seed_user, seed_periods_today[0],
            )
            transfer_id, shadow_id = transfer.id, shadow.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(shadow_id)],
                    f"settled_amount-{shadow_id}": "75.00",
                },
            )
            assert response.status_code == 200

            db.session.expire_all()
            legs = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer_id)
                .all()
            )
            for leg in legs:
                # An ECHOED prefill is not a correction: the record says
                # ``derived`` and states the figure the settle booked.
                assert leg.settled_basis_id == settlement_basis_id(
                    SettlementBasisEnum.DERIVED,
                )
                assert settled_figure(leg) == Decimal("75.00")
                assert owned_contribution(leg) == Decimal("75.00")


class TestTheCashFigureRendersBesideTheBookedOne:
    """Finding **N-226**'s caption, from the template rather than the producer.

    An envelope books ``sum(entries)`` over EVERY entry, and TWO kinds of those
    do not leave checking at the tick: a card purchase (it leaves through its
    own CC Payback sibling) and, since plan step X-f3b (ruling **R-FM**), one
    that has ALREADY posted on its own recorded day.  So a `$40` debit plus a
    `$60` card purchase is offered at `$100.00` on a screen captioned "tick
    everything your statement shows", against a statement showing `$40`.

    The developer ruled the remedy 2026-08-12: print the cash figure BESIDE
    the booked one.  The producer's half is graded at the service; the branch
    that RENDERS it had no test, so the caption could be deleted or the two
    figures swapped with the suite green.
    """

    _make_grocery_txn_with_entries = (
        TestTheReconcileRoute._make_grocery_txn_with_entries
    )
    _true_up = staticmethod(TestTheReconcileRoute._true_up)

    def test_a_card_purchase_prints_what_the_STATEMENT_shows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """`$100.00` booked, `$40.00` on the statement, and the panel says both.

        Shown to FIRE: deleting the ``cash_amount`` block leaves the panel
        offering `$100.00` against a statement showing `$40.00` with nothing
        explaining the difference.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("40.00", past, False, None),
                    ("60.00", past, True, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            assert "$100.00" in body, "the figure a tick BOOKS"
            assert "$40.00 leaves your account now" in body
            assert "on a card or has already posted" in body

    def test_an_envelope_with_NO_card_purchase_prints_one_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control: the caption appears only where it is true.

        Without it, a template that printed the line unconditionally -- or
        printed the booked figure twice -- would satisfy the case above while
        telling every user that part of every envelope had gone elsewhere.

        The single purchase here carries NO posting day, which is what keeps
        this a negative control after plan step X-f3b: one that had already
        posted would legitimately print the caption.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("40.00", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            assert "leaves your account now" not in body
            assert "on a card or has already posted" not in body


class TestAccountTypes:
    """Tests for account type create, rename, and delete."""

    def test_create_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types creates a new account type owned by the caller.

        After commit C-28 / F-044 every type the route inserts carries
        ``user_id = current_user.id``; built-ins remain
        ``user_id IS NULL`` and are seeded only by the ref-tables seed
        script.  The assertion on ``user_id`` ensures the multi-tenant
        ownership guard is wired through end-to-end.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            response = auth_client.post(
                "/accounts/types",
                data={"name": "investment", "category_id": asset_id},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account type &#39;investment&#39; created." in response.data

            acct_type = (
                db.session.query(AccountType).filter_by(name="investment").one()
            )
            assert acct_type.name == "investment"
            assert acct_type.category_id == asset_id
            assert acct_type.user_id == seed_user["user"].id

    def test_rename_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types/<id> renames a type the caller owns.

        The custom type is created with ``user_id = seed_user.id`` so
        the C-28 ownership guard accepts the rename.  A type with
        ``user_id IS NULL`` (a seeded built-in) would be rejected --
        that path is exercised in
        ``TestAccountTypeMultiTenantOwnership.test_owner_cannot_rename_seeded_builtin``.
        """
        with app.app_context():
            # Create a type to rename owned by the current user.
            new_type = AccountType(
                name="rename_source",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data={"name": "rename_target"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data

            db.session.refresh(new_type)
            assert new_type.name == "rename_target"

    def test_update_account_type_clears_max_term(self, app, auth_client, seed_user):
        """An emptied max-term input clears the stored term limit.

        The nullable-field clear rule: ``max_term_months`` is
        allow_none on the update schema, so the empty submit loads as
        an explicit None (it used to be DROPPED, making the limit
        unremovable from the UI) and the route's setattr loop nulls
        the column.  The loan route's term check is truthiness-guarded
        (``if max_term:``), so a NULL limit means unlimited.
        """
        with app.app_context():
            custom_type = AccountType(
                name="capped_loan",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY),
                has_amortization=True,
                max_term_months=360,
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={"max_term_months": ""},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.refresh(custom_type)
            assert custom_type.max_term_months is None

    def test_delete_unused_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types/<id>/delete removes a type the caller owns.

        Owner-scoped deletion mirrors the rename path: the row must
        belong to ``current_user`` (commit C-28 / F-044).  This test
        covers the happy path; the cross-owner refusal is in the
        new multi-tenant test class.
        """
        with app.app_context():
            new_type = AccountType(
                name="crypto",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()
            type_id = new_type.id

            response = auth_client.post(
                f"/accounts/types/{type_id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"deleted" in response.data

            assert db.session.get(AccountType, type_id) is None

    def test_create_duplicate_within_own_namespace(self, app, auth_client, seed_user):
        """A second create with the same name inside the caller's namespace
        is rejected with the duplicate-name warning.

        Per the C-28 acceptance criteria a user MAY create a custom
        type that shadows a seeded built-in (per-user copy), but they
        may NOT create two custom types with the same name -- the
        partial unique index ``uq_account_types_user_name`` is the
        storage-tier backstop and the route surfaces the conflict
        with the same flash the legacy global-UNIQUE produced.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            # First create -- a per-user copy of a seeded name is allowed.
            first = auth_client.post(
                "/accounts/types",
                data={"name": "Checking", "category_id": asset_id},
                follow_redirects=True,
            )
            assert first.status_code == 200
            assert b"Account type &#39;Checking&#39; created." in first.data

            # Second create with the same name within the same owner's
            # namespace -- rejected.
            response = auth_client.post(
                "/accounts/types",
                data={"name": "Checking", "category_id": asset_id},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"An account type with that name already exists." in response.data

            # Exactly one user-owned "Checking" plus the seeded built-in.
            owned = (
                db.session.query(AccountType)
                .filter_by(name="Checking", user_id=seed_user["user"].id)
                .all()
            )
            assert len(owned) == 1

    def test_delete_account_type_in_use(self, app, auth_client, seed_user):
        """An in-use owner-scoped type cannot be deleted.

        Constructs a custom type owned by ``seed_user`` and a single
        account that references it, then confirms the delete refuses
        with the in-use warning and leaves the type in place so the
        FK relationship from ``budget.accounts`` does not dangle.
        """
        with app.app_context():
            in_use_type = AccountType(
                name="MyCustomType",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(in_use_type)
            db.session.flush()

            using_account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=in_use_type.id,
                    name="Custom Account",
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.add(using_account)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{in_use_type.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Cannot delete this account type" in response.data

            # Type should still exist.
            assert db.session.get(AccountType, in_use_type.id) is not None


# ── Account Type Metadata Validation ─────────────────────────────


class TestAccountTypeMetadataValidation:
    """Tests for cross-field validation on account type create/update schemas."""

    def test_create_account_type_with_category(self, app, auth_client, seed_user):
        """POST with category and flags creates a type with correct metadata."""
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Test Debt",
                    "category_id": liability_id,
                    "has_parameters": "true",
                    "has_amortization": "true",
                    "max_term_months": "240",
                    "icon_class": "bi-cash-coin",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"created" in resp.data

            acct_type = db.session.query(AccountType).filter_by(
                name="Test Debt",
            ).one()
            assert acct_type.category_id == liability_id
            assert acct_type.has_parameters is True
            assert acct_type.has_amortization is True
            assert acct_type.max_term_months == 240
            assert acct_type.icon_class == "bi-cash-coin"

    def test_create_account_type_invalid_flag_combo(
        self, app, auth_client, seed_user,
    ):
        """has_amortization=True with Asset category is rejected."""
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Bad Combo",
                    "category_id": asset_id,
                    "has_amortization": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            # Validation error redirects with flash.
            assert b"correct the highlighted errors" in resp.data
            # Type should NOT have been created.
            assert db.session.query(AccountType).filter_by(
                name="Bad Combo",
            ).first() is None

    def test_create_account_type_mutual_exclusion(
        self, app, auth_client, seed_user,
    ):
        """has_amortization and has_interest together is rejected."""
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Bad Exclusive",
                    "category_id": liability_id,
                    "has_amortization": "true",
                    "has_interest": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the highlighted errors" in resp.data

    def test_max_term_without_amortization(self, app, auth_client, seed_user):
        """max_term_months without has_amortization is rejected."""
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Bad Term",
                    "category_id": asset_id,
                    "max_term_months": "120",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the highlighted errors" in resp.data

    def test_update_account_type_metadata(self, app, auth_client, seed_user):
        """POST update changes metadata fields on a user-owned type.

        The C-28 ownership guard requires ``user_id = seed_user.id``
        on the row before the update route accepts mutations; that
        column is set explicitly here so the test exercises the
        metadata-write path independent of the multi-tenant guard.
        """
        with app.app_context():
            new_type = AccountType(
                name="update_meta_test",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data={
                    "name": "update_meta_test",
                    "has_parameters": "true",
                    "has_interest": "true",
                    "is_liquid": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(new_type)
            assert new_type.has_parameters is True
            assert new_type.has_interest is True
            assert new_type.is_liquid is True

    def test_update_account_type_multidict_checkboxes(self, app, auth_client, seed_user):
        """Boolean flags resolve correctly from MultiDict form data.

        Browsers submit hidden-input + checkbox pairs as duplicate keys
        in a MultiDict.  Checked checkboxes send ('field', 'false') and
        ('field', 'true'); unchecked send only ('field', 'false').  The
        schema must take the last value so checked boxes resolve to True.
        Regression test for a bug where Flask's MultiDict.items() returned
        only the first value, making all booleans always False.
        """
        from werkzeug.datastructures import MultiDict

        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            new_type = AccountType(
                name="multidict_test",
                category_id=asset_id,
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()

            # Simulate browser form with checked checkboxes (hidden + checkbox).
            resp = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data=MultiDict([
                    ("name", "multidict_test"),
                    ("category_id", str(asset_id)),
                    ("has_parameters", "false"),
                    ("has_parameters", "true"),
                    ("has_amortization", "false"),
                    ("has_interest", "false"),
                    ("has_interest", "true"),
                    ("is_pretax", "false"),
                    ("is_liquid", "false"),
                    ("is_liquid", "true"),
                    ("icon_class", "bi-cash-stack"),
                    ("max_term_months", ""),
                ]),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.refresh(new_type)
            # Checked checkboxes must resolve to True.
            assert new_type.has_parameters is True
            assert new_type.has_interest is True
            assert new_type.is_liquid is True
            # Unchecked checkboxes must resolve to False.
            assert new_type.has_amortization is False
            assert new_type.is_pretax is False

    def test_update_account_type_multidict_all_unchecked(self, app, auth_client, seed_user):
        """Unchecking all boolean flags via MultiDict sets them to False.

        Starts with all flags True, then submits a form where every
        checkbox is unchecked (only hidden 'false' values sent).
        """
        from werkzeug.datastructures import MultiDict

        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            new_type = AccountType(
                name="multidict_uncheck_test",
                category_id=asset_id,
                has_parameters=True,
                has_interest=True,
                is_liquid=True,
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()

            # Simulate browser form with all checkboxes unchecked.
            resp = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data=MultiDict([
                    ("name", "multidict_uncheck_test"),
                    ("category_id", str(asset_id)),
                    ("has_parameters", "false"),
                    ("has_amortization", "false"),
                    ("has_interest", "false"),
                    ("is_pretax", "false"),
                    ("is_liquid", "false"),
                    ("icon_class", "bi-cash-stack"),
                    ("max_term_months", ""),
                ]),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.refresh(new_type)
            assert new_type.has_parameters is False
            assert new_type.has_interest is False
            assert new_type.is_liquid is False


# ── Account Type Metadata Columns ────────────────────────────────


class TestAccountTypeMetadataColumns:
    """Verify the has_interest, is_pretax, and is_liquid metadata columns
    are seeded correctly on ref.account_types."""

    def test_account_type_has_interest_column(self, app, seed_user):
        """HYSA and HSA have has_interest=True; other types do not."""
        with app.app_context():
            hysa = db.session.query(AccountType).filter_by(name="HYSA").one()
            hsa = db.session.query(AccountType).filter_by(name="HSA").one()
            checking = db.session.query(AccountType).filter_by(name="Checking").one()
            mortgage = db.session.query(AccountType).filter_by(name="Mortgage").one()

            assert hysa.has_interest is True
            assert hsa.has_interest is True
            assert checking.has_interest is False
            assert mortgage.has_interest is False

            # Column is non-nullable.
            col = AccountType.__table__.columns["has_interest"]
            assert col.nullable is False

    def test_account_type_is_pretax_column(self, app, seed_user):
        """401(k) and Traditional IRA have is_pretax=True; Roth types do not."""
        with app.app_context():
            k401 = db.session.query(AccountType).filter_by(name="401(k)").one()
            trad_ira = db.session.query(AccountType).filter_by(
                name="Traditional IRA",
            ).one()
            roth_401k = db.session.query(AccountType).filter_by(
                name="Roth 401(k)",
            ).one()
            roth_ira = db.session.query(AccountType).filter_by(name="Roth IRA").one()
            brokerage = db.session.query(AccountType).filter_by(
                name="Brokerage",
            ).one()

            assert k401.is_pretax is True
            assert trad_ira.is_pretax is True
            assert roth_401k.is_pretax is False
            assert roth_ira.is_pretax is False
            assert brokerage.is_pretax is False

            # Column is non-nullable.
            col = AccountType.__table__.columns["is_pretax"]
            assert col.nullable is False

    def test_account_type_is_liquid_column(self, app, seed_user):
        """Checking, Savings, HYSA, Money Market have is_liquid=True."""
        with app.app_context():
            checking = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            savings = db.session.query(AccountType).filter_by(name="Savings").one()
            hysa = db.session.query(AccountType).filter_by(name="HYSA").one()
            money_market = db.session.query(AccountType).filter_by(
                name="Money Market",
            ).one()
            cd = db.session.query(AccountType).filter_by(name="CD").one()
            hsa = db.session.query(AccountType).filter_by(name="HSA").one()
            credit_card = db.session.query(AccountType).filter_by(
                name="Credit Card",
            ).one()
            k401 = db.session.query(AccountType).filter_by(name="401(k)").one()

            assert checking.is_liquid is True
            assert savings.is_liquid is True
            assert hysa.is_liquid is True
            assert money_market.is_liquid is True
            assert cd.is_liquid is False
            assert hsa.is_liquid is False
            assert credit_card.is_liquid is False
            assert k401.is_liquid is False

            # Column is non-nullable.
            col = AccountType.__table__.columns["is_liquid"]
            assert col.nullable is False

    def test_hsa_has_parameters_true(self, app, seed_user):
        """HSA now has has_parameters=True (changed from False)."""
        with app.app_context():
            hsa = db.session.query(AccountType).filter_by(name="HSA").one()
            assert hsa.has_parameters is True
            assert hsa.has_interest is True


# ── Negative Paths ────────────────────────────────────────────────


class TestAccountNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR, idempotent ops, validation, XSS."""

    def test_edit_nonexistent_account(self, app, auth_client, seed_user):
        """GET /accounts/999999/edit for a nonexistent account returns 404 (security)."""
        with app.app_context():
            resp = auth_client.get("/accounts/999999/edit", follow_redirects=True)

            assert resp.status_code == 404

    def test_update_nonexistent_account(self, app, auth_client, seed_user):
        """POST /accounts/999999 for a nonexistent account returns 404 (security)."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            resp = auth_client.post("/accounts/999999", data={
                "name": "Ghost",
                "account_type_id": checking_type.id,
            }, follow_redirects=True)

            assert resp.status_code == 404

    def test_archive_nonexistent_account(self, app, auth_client, seed_user):
        """POST /accounts/999999/archive for a nonexistent account returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/accounts/999999/archive", follow_redirects=True,
            )

            assert resp.status_code == 404

    def test_unarchive_other_users_account_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /accounts/<id>/unarchive for another user's archived account returns 404 (security)."""
        with app.app_context():
            # Re-query to ensure the object is in the current session.
            acct_id = second_user["account"].id
            other_acct = db.session.get(Account, acct_id)
            other_acct.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{acct_id}/unarchive",
                follow_redirects=True,
            )

            assert resp.status_code == 404

            # Verify DB state unchanged: account still inactive.
            db.session.expire_all()
            refreshed = db.session.get(Account, acct_id)
            assert refreshed.is_active is False

    def test_archive_already_inactive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/archive on an already-inactive account is idempotent."""
        with app.app_context():
            account_id = seed_user["account"].id

            # First archive via the route.
            resp1 = auth_client.post(
                f"/accounts/{account_id}/archive",
                follow_redirects=True,
            )
            assert resp1.status_code == 200
            assert b"archived" in resp1.data

            # Second archive -- account is already inactive.
            resp2 = auth_client.post(
                f"/accounts/{account_id}/archive",
                follow_redirects=True,
            )

            # Route does not guard against double-archive; it sets
            # is_active=False and commits. This is idempotent behavior.
            assert resp2.status_code == 200
            assert b"archived" in resp2.data

            db.session.expire_all()
            refreshed = db.session.get(Account, account_id)
            assert refreshed.is_active is False

    def test_unarchive_already_active_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/unarchive on an already-active account is idempotent."""
        with app.app_context():
            account_id = seed_user["account"].id

            # Account starts active (default from seed). Unarchive anyway.
            resp = auth_client.post(
                f"/accounts/{account_id}/unarchive",
                follow_redirects=True,
            )

            # Route does not guard against unarchiving an already-active
            # account; it sets is_active=True and commits.
            assert resp.status_code == 200
            assert b"unarchived" in resp.data

            db.session.expire_all()
            refreshed = db.session.get(Account, account_id)
            assert refreshed.is_active is True

    def test_create_account_missing_name(self, app, auth_client, seed_user):
        """POST /accounts with missing name field fails schema validation and creates no record."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            resp = auth_client.post("/accounts", data={
                "account_type_id": checking_type.id,
                "anchor_balance": "500.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no extra account was created (seed_user has exactly 1).
            count = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 1

    def test_create_account_xss_in_name(self, app, auth_client, seed_user):
        """POST /accounts with script tag in name is stored but Jinja2 auto-escapes on render."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            # Schema accepts the name (no character restrictions, 32 chars < 100 max).
            resp = auth_client.post("/accounts", data={
                "name": "<script>alert(1)</script>",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            }, follow_redirects=True)

            assert resp.status_code == 200

            # Verify account was created in DB.
            acct = (
                db.session.query(Account)
                .filter_by(
                    user_id=seed_user["user"].id,
                    name="<script>alert(1)</script>",
                )
                .one()
            )
            assert acct is not None

            # Verify the XSS payload does not appear unescaped in the response.
            assert b"<script>alert(1)</script>" not in resp.data
            # Verify the escaped form is present (Jinja2 auto-escaping).
            assert b"&lt;script&gt;" in resp.data


# ── Account Creation Redirect Tests ──────────────────────────────


class TestAccountCreationRedirects:
    """Tests for post-creation redirect routing.

    Parameterized account types redirect to their configuration pages
    with setup=1.  Non-parameterized types redirect to the unified
    accounts cockpit (savings.dashboard), the retired /accounts table's
    successor (Loop B P4).
    """

    def test_hysa_creation_redirects_to_detail(
        self, app, auth_client, seed_user,
    ):
        """HYSA creation redirects to the cash detail page (setup=1) and auto-creates params.

        The Fable 5 overhaul merged the interest detail page into
        ``accounts.cash_detail`` (URL ``/accounts/<id>/details``), so the
        post-create redirect for an interest-bearing account lands there
        with ``setup=1`` for the wizard banner.
        """
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()

            resp = auth_client.post("/accounts", data={
                "name": "My HYSA",
                "account_type_id": hysa_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/details" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My HYSA",
            ).one()
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id
            ).first() is not None

    def test_mortgage_creation_redirects_to_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Mortgage creation redirects to loan dashboard with setup=1."""
        with app.app_context():
            mortgage_type = db.session.query(AccountType).filter_by(
                name="Mortgage"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Home Mortgage",
                "account_type_id": mortgage_type.id,
                "anchor_balance": "250000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/loan" in location
            assert "setup=1" in location

    def test_auto_loan_creation_redirects_to_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Auto loan creation redirects to loan dashboard with setup=1."""
        with app.app_context():
            auto_loan_type = db.session.query(AccountType).filter_by(
                name="Auto Loan"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Car Loan",
                "account_type_id": auto_loan_type.id,
                "anchor_balance": "20000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/loan" in location
            assert "setup=1" in location

    def test_401k_creation_redirects_to_investment_dashboard(
        self, app, auth_client, seed_user,
    ):
        """401(k) creation redirects to investment dashboard and auto-creates InvestmentParams."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Work 401k",
                "account_type_id": k401_type.id,
                "anchor_balance": "10000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Work 401k",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is not None

    def test_roth_ira_creation_redirects_to_investment_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Roth IRA creation routes to investment dashboard with InvestmentParams."""
        with app.app_context():
            roth_ira_type = db.session.query(AccountType).filter_by(
                name="Roth IRA"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "My Roth IRA",
                "account_type_id": roth_ira_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My Roth IRA",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is not None

    def test_brokerage_creation_redirects_to_investment_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Brokerage creation routes to investment dashboard with InvestmentParams."""
        with app.app_context():
            brokerage_type = db.session.query(AccountType).filter_by(
                name="Brokerage"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "My Brokerage",
                "account_type_id": brokerage_type.id,
                "anchor_balance": "1000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

    def test_checking_creation_redirects_to_cockpit(
        self, app, auth_client, seed_user,
    ):
        """Checking (non-parameterized) creation redirects to the cockpit, no setup param."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Secondary Checking",
                "account_type_id": checking_type.id,
                "anchor_balance": "0",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert location.endswith("/savings")
            assert "setup" not in location

    def test_savings_creation_redirects_to_cockpit(
        self, app, auth_client, seed_user,
    ):
        """Plain savings account creation redirects to the cockpit."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Emergency Fund",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert location.endswith("/savings")
            assert "setup" not in location

    def test_student_loan_creation_redirects_to_loan(
        self, app, auth_client, seed_user,
    ):
        """Student loan creation redirects to loan dashboard for setup.

        Student loans have has_amortization=True and are now served by
        the unified loan routes.  They must not be routed to the
        investment dashboard.
        """
        with app.app_context():
            sl_type = db.session.query(AccountType).filter_by(
                name="Student Loan"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Student Loan",
                "account_type_id": sl_type.id,
                "anchor_balance": "30000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "investment" not in location
            assert "/loan" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Student Loan",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is None

    def test_personal_loan_creation_no_investment_params(
        self, app, auth_client, seed_user,
    ):
        """Personal loan creation does NOT create InvestmentParams."""
        with app.app_context():
            pl_type = db.session.query(AccountType).filter_by(
                name="Personal Loan"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Personal Loan",
                "account_type_id": pl_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            assert "investment" not in resp.headers["Location"]

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Personal Loan",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is None

    def test_investment_params_not_duplicated(
        self, app, auth_client, seed_user,
    ):
        """Auto-creation of InvestmentParams produces exactly one record."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()

            auth_client.post("/accounts", data={
                "name": "Dupe Test 401k",
                "account_type_id": k401_type.id,
                "anchor_balance": "10000.00",
            })

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Dupe Test 401k",
            ).one()

            count = db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).count()
            assert count == 1

    def test_investment_params_defaults_are_reasonable(
        self, app, auth_client, seed_user,
    ):
        """Auto-created InvestmentParams have sensible default values."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()

            auth_client.post("/accounts", data={
                "name": "Default 401k",
                "account_type_id": k401_type.id,
                "anchor_balance": "0",
            })

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Default 401k",
            ).one()
            params = db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).one()

            assert params.assumed_annual_return == Decimal("0.07000")
            assert params.employer_contribution_type_id == (
                ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                )
            )
            assert params.assumed_annual_return >= 0


# ── Metadata-Driven Interest Dispatch ────────────────────────────


class TestInterestDispatch:
    """Verify that has_interest metadata flag drives auto-creation,
    redirect, and detail page access instead of hardcoded HYSA type ID."""

    def test_create_account_hsa_auto_creates_interest_params(
        self, app, auth_client, seed_user,
    ):
        """HSA has has_interest=True; creating one auto-creates InterestParams."""
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            assert hsa_type.has_interest is True

            resp = auth_client.post("/accounts", data={
                "name": "My HSA",
                "account_type_id": hsa_type.id,
                "anchor_balance": "1200.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/details" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My HSA",
            ).one()
            params = db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first()
            assert params is not None, "InterestParams not auto-created for HSA"

    def test_create_account_money_market_with_interest(
        self, app, auth_client, seed_user, db,
    ):
        """Money Market with has_interest=True auto-creates InterestParams."""
        with app.app_context():
            mm_type = db.session.query(AccountType).filter_by(
                name="Money Market",
            ).one()
            mm_type.has_interest = True
            mm_type.has_parameters = True
            db.session.commit()

            resp = auth_client.post("/accounts", data={
                "name": "My MM",
                "account_type_id": mm_type.id,
                "anchor_balance": "3000.00",
            })

            assert resp.status_code == 302
            assert "/details" in resp.headers["Location"]

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My MM",
            ).one()
            params = db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first()
            assert params is not None

    def test_interest_detail_accepts_any_interest_type(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """Cash detail page renders (with APY) for any has_interest=True type."""
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hsa_type.id,
                    name="HSA Detail Test",
                    anchor_balance=500,
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),  # HIGH-06: apy NOT NULL, no server_default
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200
            # The interest-bearing variant shows the parameters card.
            assert b"APY" in resp.data

    def test_has_interest_true_but_no_params_row(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """Cash detail REFUSES a missing params row; it does not manufacture one.

        **This test asserted the opposite until plan step balance:X-i3**, which
        deleted the auto-create branch on the developer's ruling.  The old
        behaviour was a render repairing data: it wrote inside a read, which
        costs the page the one snapshot every figure on it is computed against,
        and it hid the door that should have written the row (the type-change
        gap the sibling test below now covers).

        A zero-rate row manufactured here renders on screen exactly like a rate
        the owner configured, so the refusal is the honest answer and the
        message names the repair.
        """
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hsa_type.id,
                    name="HSA No Params",
                    anchor_balance=100,
                ),
            )
            db.session.add(acct)
            db.session.commit()

            # No InterestParams row exists yet.  ``account_service`` builds the
            # Account; the params row is the ACCOUNT ROUTE's, which is why this
            # state is reachable from the service and not from a browser.
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None

            with pytest.raises(RequiredRecordMissing, match="interest_params"):
                auth_client.get(f"/accounts/{acct.id}/details")

            # And it manufactured NOTHING on the way out.
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None

    def test_editing_the_TYPE_into_an_interest_kind_seeds_every_account_on_it(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """The THIRD door, and the one the step's own first draft missed.

        Plan step balance:X-i3.  ``POST /accounts/types/<id>`` may flip
        ``has_interest`` on an owner's OWN custom type, which changes the
        projection kind of every account already on it -- with no
        ``account_type_id`` change for the account-update door to see and no
        account row touched at all.  Deleting the detail page's auto-create
        without holding the rule here would have turned a legitimate settings
        edit into a 500 on the account's own page.

        Asserted through the RENDER, not just the row: the page is what the
        deleted repair existed to keep working.
        """
        with app.app_context():
            plain_type = AccountType(
                user_id=seed_user["user"].id,
                name="My Cash Pot",
                category_id=db.session.query(AccountType).filter_by(
                    name="Checking",
                ).one().category_id,
            )
            db.session.add(plain_type)
            db.session.flush()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=plain_type.id,
                    name="Cash Pot",
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None, "a plain custom type starts with no params row"

            resp = auth_client.post(f"/accounts/types/{plain_type.id}", data={
                "name": plain_type.name,
                "category_id": plain_type.category_id,
                "has_interest": "true",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            # The precondition, asserted rather than assumed: a schema refusal
            # here flashes and redirects, which ``follow_redirects`` would also
            # render as a 200.
            assert db.session.get(AccountType, plain_type.id).has_interest

            params = db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).one()
            assert params.apy == Decimal("0")
            assert auth_client.get(
                f"/accounts/{acct.id}/details",
            ).status_code == 200

    def test_reclassing_into_an_interest_type_seeds_its_params_row(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """The gap the deleted auto-create was covering, closed at its door.

        Plan step balance:X-i3.  The seeder had exactly ONE caller -- account
        CREATION -- so an account re-classed into a parameterised kind carried
        no params row, and two detail pages each repaired it on a GET.
        ``update_account`` now seeds through the shared
        ``_type_params.ensure_type_params``, so the invariant holds at the door
        that establishes the kind.

        The FIRING half matters: without the seeding line this test fails at
        the render below, which is the state the auto-create used to hide.
        """
        with app.app_context():
            checking = seed_user["account"]
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            assert db.session.query(InterestParams).filter_by(
                account_id=checking.id,
            ).first() is None, "a Checking account starts with no params row"

            resp = auth_client.post(f"/accounts/{checking.id}", data={
                "name": checking.name,
                "account_type_id": hsa_type.id,
                "version_id": checking.version_id,
            }, follow_redirects=True)
            assert resp.status_code == 200

            params = db.session.query(InterestParams).filter_by(
                account_id=checking.id,
            ).one()
            # The explicit zero sentinel the create door uses (E-12 / HIGH-06):
            # a missing rate is never projected as a server-default one.
            assert params.apy == Decimal("0")

            # And the page the missing row used to repair now renders.
            assert auth_client.get(
                f"/accounts/{checking.id}/details",
            ).status_code == 200


# ── Investment Dispatch (Metadata-Driven) ────────────────────────


class TestInvestmentDispatch:
    """Verify that investment/retirement auto-creation and redirect use
    metadata flags instead of hardcoded type ID frozensets."""

    def test_create_account_user_type_retirement_auto_creates_params(
        self, app, auth_client, seed_user, db,
    ):
        """A user-created Retirement type with has_parameters=True auto-creates
        InvestmentParams and redirects to the investment dashboard."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum

        with app.app_context():
            custom_type = AccountType(
                name="TestSEPIRA",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
                has_parameters=True,
            )
            db.session.add(custom_type)
            db.session.commit()

            resp = auth_client.post("/accounts", data={
                "name": "My SEP IRA",
                "account_type_id": custom_type.id,
                "anchor_balance": "10000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My SEP IRA",
            ).one()
            params = db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).first()
            assert params is not None, "InvestmentParams not auto-created"

    def test_has_parameters_false_no_auto_create(
        self, app, auth_client, seed_user,
    ):
        """An account type with has_parameters=False gets no params and
        redirects to the cockpit (savings.dashboard)."""
        with app.app_context():
            # Savings has has_parameters=False.
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            assert savings_type.has_parameters is False

            resp = auth_client.post("/accounts", data={
                "name": "Plain Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/savings" in location
            assert "setup" not in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Plain Savings",
            ).one()
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).first() is None

    def test_529_plan_has_parameters_true_in_seed(self, app, seed_user):
        """529 Plan has has_parameters=True in seed data."""
        with app.app_context():
            plan_type = db.session.query(AccountType).filter_by(
                name="529 Plan",
            ).one()
            assert plan_type.has_parameters is True

    def test_create_account_529_auto_creates_investment_params(
        self, app, auth_client, seed_user,
    ):
        """529 Plan auto-creates InvestmentParams and redirects to investment dashboard."""
        with app.app_context():
            plan_type = db.session.query(AccountType).filter_by(
                name="529 Plan",
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "College Fund",
                "account_type_id": plan_type.id,
                "anchor_balance": "2000.00",
            })

            assert resp.status_code == 302
            assert "/investment" in resp.headers["Location"]

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="College Fund",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).first() is not None


# ── Wizard Banner Tests ──────────────────────────────────────────


class TestWizardBanner:
    """Tests for the setup wizard banner on parameter pages.

    The banner appears when ?setup=1 is in the query string, indicating
    the user just created the account and should review configuration.
    """

    def test_wizard_banner_shown_on_hysa_with_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """HYSA detail page shows wizard banner when ?setup=1 is present."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Banner HYSA",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),  # HIGH-06: apy NOT NULL, no server_default
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details?setup=1")
            assert resp.status_code == 200
            assert b"Configure the settings below" in resp.data
            assert b"alert-dismissible" in resp.data

    def test_wizard_banner_hidden_on_hysa_without_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """HYSA detail page does NOT show wizard banner without ?setup=1."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="No Banner HYSA",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),  # HIGH-06: apy NOT NULL, no server_default
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200
            assert b"Configure the settings below" not in resp.data

    def test_wizard_banner_shown_on_investment_with_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Investment dashboard shows wizard banner when ?setup=1 is present."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="Banner 401k",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(
                account_id=acct.id,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(
                f"/accounts/{acct.id}/investment?setup=1"
            )
            assert resp.status_code == 200
            assert b"Configure the settings below" in resp.data
            assert b"alert-dismissible" in resp.data

    def test_wizard_banner_hidden_on_investment_without_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Investment dashboard does NOT show wizard banner without ?setup=1."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="No Banner 401k",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(
                account_id=acct.id,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/investment")
            assert resp.status_code == 200
            assert b"Configure the settings below" not in resp.data


# ── Checking Detail ──────────────────────────────────────────────


class TestCheckingDetail:
    """Tests for a checking account on the merged cash detail page.

    The Fable 5 overhaul merged the checking / interest detail pages into
    ``accounts.cash_detail`` (URL ``/accounts/<id>/details``); a plain
    checking account renders its balance hero, horizon chips, and trend
    chart there.  The per-period table was dropped (developer ruling);
    exact per-period values live in the chart tooltip and, for checking,
    on the grid.
    """

    def _create_checking_account(self, seed_user, periods, balance="5000.00"):
        """Create a new checking account with anchor set to period 0.

        Creates a fresh account (avoiding session identity map caching
        from seed_user's account) with the specified anchor balance.
        """
        checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
        acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Detail Checking",
                anchor_balance=Decimal(balance),
            ),
        )
        db.session.add(acct)
        return acct

    def test_checking_detail_page_renders(self, app, auth_client, seed_user):
        """GET /accounts/<id>/checking renders the detail page with account name and balance."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, cadence_days=14,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")

            assert resp.status_code == 200
            assert b"Detail Checking" in resp.data
            assert b"$5,000.00" in resp.data

    def test_checking_detail_projection_values_are_correct(
        self, app, auth_client, seed_user,
    ):
        """Checking detail projections match expected balance calculations.

        With anchor $5,000 and net +$500 per period, projections are:
        3 months (6 periods) = $8,000, 6 months (13) = $11,500, 1 year (26) = $18,000.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Salary"]

            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=27, cadence_days=14,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.flush()

            projected_status = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create income and expense in all post-anchor periods.
            for p in periods[1:]:
                db.session.add(Transaction(
                    pay_period_id=p.id,
                    scenario_id=scenario.id,
                    account_id=acct.id,
                    status_id=projected_status.id,
                    name="Paycheck",
                    category_id=category.id,
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("2000.00"),
                ))
                db.session.add(Transaction(
                    pay_period_id=p.id,
                    scenario_id=scenario.id,
                    account_id=acct.id,
                    status_id=projected_status.id,
                    name="Expenses",
                    category_id=category.id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("1500.00"),
                ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # 3 months (6 periods): 5000 + 6*500 = 8000
            assert b"$8,000" in resp.data
            # 6 months (13 periods): 5000 + 13*500 = 11500
            assert b"$11,500" in resp.data
            # 1 year (26 periods): 5000 + 26*500 = 18000
            assert b"$18,000" in resp.data

    def test_checking_detail_matches_grid_balance(
        self, app, auth_client, seed_user,
    ):
        """The detail page's 3-month projection is the HAND-COMPUTED figure.

        It used to derive its expected value by calling the balance producer
        the route also called, which proved only that one function is
        deterministic.  Plan step X-g4b deleted that producer and the
        derivation with it: the figure below is arithmetic, so the page and the
        seam must both be right rather than merely agree.

        Hand-computed: a $5,000.00 opening assertion, then 26 periods each
        carrying +$2,000.00 income and -$1,500.00 expense (net +$500.00).  The
        3-month projection is period 6, six periods forward of the anchor:
        ``5,000.00 + 6 x 500.00 = $8,000.00``.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Salary"]

            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=27, cadence_days=14,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.flush()

            projected_status = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            for p in periods[1:]:
                db.session.add(Transaction(
                    pay_period_id=p.id,
                    scenario_id=scenario.id,
                    account_id=acct.id,
                    status_id=projected_status.id,
                    name="Paycheck",
                    category_id=category.id,
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("2000.00"),
                ))
                db.session.add(Transaction(
                    pay_period_id=p.id,
                    scenario_id=scenario.id,
                    account_id=acct.id,
                    status_id=projected_status.id,
                    name="Bills",
                    category_id=category.id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("1500.00"),
                ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # The projection summary uses {:,.0f} format.
            assert b"$8,000" in resp.data

    def test_cash_detail_serves_plain_savings(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/details returns 200 for a plain Savings account.

        The merged cash detail page closed the audit's Surface 6 coverage
        gap: plain Savings (and Credit Card / custom cash) types -- which
        had NO detail page before -- are now served with the plain context
        shape (no interest parameters card).
        """
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="My Savings",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.add(savings)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{savings.id}/details")
            assert resp.status_code == 200
            assert b"My Savings" in resp.data
            # Plain cash: no interest parameters card (no APY input).
            assert b'name="apy"' not in resp.data

    def test_cash_detail_rejects_other_users_account(
        self, app, auth_client, seed_user, second_user,
    ):
        """GET /accounts/<id>/details returns 404 for another user's account (IDOR)."""
        with app.app_context():
            resp = auth_client.get(
                f"/accounts/{second_user['account'].id}/details"
            )
            assert resp.status_code == 404

    def test_checking_detail_handles_no_transactions(
        self, app, auth_client, seed_user,
    ):
        """Checking detail with no transactions shows flat balance at anchor amount."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=27, cadence_days=14,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # With no transactions, projections should show the anchor balance.
            assert b"$5,000" in resp.data

    def test_checking_detail_handles_short_horizon(
        self, app, auth_client, seed_user,
    ):
        """Short horizon: 3-month projection available, 12-month projection missing."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, cadence_days=14,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # 3-month target (period index 6) is within range (10 periods).
            assert b"3 months" in resp.data
            # 6-month (index 13) and 12-month (index 26) are beyond our horizon.
            assert b"6 months" not in resp.data
            assert b"1 year" not in resp.data

    def test_checking_detail_excludes_credit_transactions(
        self, app, auth_client, seed_user,
    ):
        """Credit-status transactions are excluded from the projected balance.

        A credit expense should not reduce the checking balance because
        credit transactions are not paid from checking.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Rent"]

            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, cadence_days=14,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.flush()

            credit_status = db.session.query(Status).filter_by(name="Credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create a credit expense in the first post-anchor period.
            db.session.add(Transaction(
                pay_period_id=periods[1].id,
                scenario_id=scenario.id,
                account_id=acct.id,
                status_id=credit_status.id,
                name="Credit Card Groceries",
                category_id=category.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1000.00"),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # The credit expense should NOT reduce the balance.
            # Projections should still show $5,000 (flat from anchor).
            assert b"$5,000" in resp.data
            # Verify the balance was NOT reduced by the credit expense.
            assert b"$4,000" not in resp.data

    @pytest.mark.server_clock
    def test_checking_detail_shows_anchor_date(self, app, auth_client, seed_user):
        """The anchored-as-of date is displayed in the balance hero caption.

        The rebuilt hero caption renders ``anchor_as_of`` -- the anchor
        EVENT instant (the origination ``AccountAnchorHistory`` row), in the
        user's DISPLAY timezone -- not the anchor period's start date (audit
        finding #2) and not the UTC-day ``as_of_date`` (which shows the wrong
        civil day for a late-evening-Eastern event).  The expected string is
        computed from the anchor's ``created_at`` via ``to_display_date`` --
        NOT ``date.today()``, which reads the PROCESS timezone and would
        diverge from the Eastern caption in a UTC CI runner during the
        late-evening-Eastern window.  Deriving both sides from the same
        ``created_at`` also makes the assertion immune to a midnight race.
        The distinct-date (event vs period start) case is pinned in
        ``TestCashDetailContext.test_anchor_as_of_is_event_date_not_period_start``.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.utils.dates import to_display_date  # pylint: disable=import-outside-toplevel
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, cadence_days=14,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # The caption shows the anchor event's DISPLAY-timezone civil date,
            # computed here from the same ``created_at`` the caption renders.
            anchor = cash_ledger.resolve_anchor(acct)
            anchor_date_str = to_display_date(anchor.created_at).strftime(
                "%b %-d, %Y",
            )
            assert anchor_date_str.encode() in resp.data


# ── Commit 7: canonical entries-aware producer routing ─────────────
#
# Pre-Commit-7 the /accounts checking detail built its own transaction
# query without ``selectinload(Transaction.entries)`` (same shape as
# the savings tile pre-Commit-6) and forked on the NULL-anchor case
# differently from the grid.  The silent-degrade seam in
# ``cash_ledger._amounts._entry_aware_amount`` (closed at the math layer
# by Commit 5, structurally closed by the canonical producer in Commit
# 5) yielded $160.00 on the grid and $114.29 on /accounts for the
# audit's symptom #1 / #5 tuple.  Commit 7 routes the checking detail
# through the balance-at seam and resolves the anchor via
# the dated ``AccountAnchorHistory`` SoT, so both divergence axes
# (entries seam, NULL-anchor fork) close.  The NULL-anchor fork is
# dead post-Commit-3 and was deleted rather than left unreachable.


def _override_account_anchor(db_session, account, pay_period, anchor_balance):
    """Replace ``account``'s current anchor with the given balance + period.

    Appends a fresh :class:`AccountAnchorHistory` row (latest-wins by
    ``created_at``) and syncs the cache columns so the resolver's
    cache-reconciliation log does NOT fire.  Required because the
    ``seed_user`` factory writes an origination anchor of $1,000
    against the seed_periods anchor period; the Commit-7 symptom
    reproductions need $614.29 anchored to today's period.

    Mirrors ``tests/test_services/test_savings_dashboard_service.py``'s
    ``_override_anchor`` (Commit 6).  Kept private to this module so
    later moves to a shared fixture remain a refactor rather than a
    contract change.
    """
    from tests._test_helpers import override_anchor  # pylint: disable=import-outside-toplevel

    override_anchor(
        db_session, account, pay_period, anchor_balance,
    )
    db_session.commit()


def _make_projected_envelope_expense(
    db_session, *, seed_user, pay_period, account_id, estimated,
    name="Groceries",
):
    """Create a Projected envelope expense + its template in ``pay_period``.

    Mirrors the helper in ``test_savings_dashboard_service.py``.  Uses
    the seed user's Groceries category so the row matches the symptom
    #1 / #5 worked example.
    """
    from app.models.transaction_template import TransactionTemplate  # pylint: disable=import-outside-toplevel

    projected = db_session.query(Status).filter_by(name="Projected").one()
    expense_type = (
        db_session.query(TransactionType).filter_by(name="Expense").one()
    )

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account_id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        name=name,
        default_amount=estimated,
        is_envelope=True,
    )
    db_session.add(template)
    db_session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=pay_period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=account_id,
        status_id=projected.id,
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=estimated,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _add_cleared_debit_entry(db_session, *, txn, user_id, amount):
    """Add a CLEARED, DEBIT :class:`TransactionEntry` to ``txn``.

    These are the entries that produce the F-009 / CRIT-01 silent-
    degrade gap: pre-Commit-5 the entry-aware reduction would not run
    on consumers that did not eager-load entries, so the cleared
    amount (already in the anchor) would be implicitly subtracted a
    second time off the projection.
    """
    from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel

    db_session.add(TransactionEntry(
        transaction_id=txn.id, account_id=txn.account_id,
        user_id=user_id,
        amount=amount,
        description="Cleared purchase",
        purchased_on=date(2026, 1, 15),
        is_credit=False,
        **settle_day_columns(date(2026, 1, 15)),
    ))
    db_session.flush()


class TestCheckingDetailCanonicalProducer:
    """C7: /accounts checking detail routed through the balance-at seam.

    Pins the symptom #5 fix: the per-account detail page now produces
    the same checking balance as the grid and /savings for the same
    inputs.  Tests track plan IDs C7-1 through C7-3 plus the
    verification gates listed in the Commit 7 spec.
    """

    def test_accounts_checking_equals_grid(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """C7-1: /accounts checking detail current balance == grid balance.

        Reproduction of symptom #5 / F-009 worked example
        (``05_symptoms.md``):

          - Real checking anchor 614.29 on the current pay period.
          - One Projected envelope expense ``estimated_amount = 500.00``
            in the same period (so ``sum_projected`` applies).
          - Three CLEARED debit entries 20.00 + 15.71 + 10.00 = 45.71.
            No credit, no uncleared.

        Hand arithmetic (F-009):

          cleared_debit   = 20.00 + 15.71 + 10.00 = 45.71
          uncleared_debit = 0
          sum_credit      = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          anchor_period_balance = 614.29 + 0 - 454.29 = 160.00

        Both the grid and the /accounts checking detail page MUST return
        Decimal("160.00") -- one seam entry, ``balance_at.cash_balance_map``,
        read twice.  Pre-Commit-7 the checking detail page reported the
        silent-degrade value Decimal("114.29") via the unloaded-entries seam.

        The $160.00 survives the basis change at plan step X-c2b2 for a reason
        this fixture makes plain: the account holds ONE asserted balance and the
        only row is still PROJECTED, so the fold has the same assertion to
        replay and the same entries-aware reservation to hold back.  The two
        bases diverge only where money has SETTLED, and nothing here has.
        """
        # Pylint: import-outside-toplevel -- module-scoped imports in this file
        # are the route-test convention; the seam read is local to this case.
        from app.services import balance_at  # pylint: disable=import-outside-toplevel
        from app.services.balance_at import BalanceContext  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current_period = current_pay_period(
                seed_user["user"].id
            )
            assert current_period is not None
            account = seed_user["account"]
            _override_account_anchor(
                db.session, account, current_period, Decimal("614.29"),
            )

            txn = _make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                account_id=account.id,
                estimated=Decimal("500.00"),
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_cleared_debit_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                )
            db.session.commit()

            # Grid value via the seam entry the grid's balance row reads, so
            # replaying it here is "what does the grid show" without a route
            # round-trip.  It has been repointed TWICE for the same reason -- a
            # guard that had stopped guarding (the N-63 shape): from
            # ``_cash_engine.balances_for`` at plan step X-c2b2, and from
            # ``cash_balance_map`` at X-g3b, where the grid stopped reading the
            # cash view for a modelled account (ruling R-W).  This account is
            # PLAIN, so the two agree by construction -- which is exactly the
            # condition that made the previous spelling look harmless.
            grid_current_balance = balance_at.grid_balance_view(
                account,
                BalanceContext.build(seed_user["user"].id),
            ).columns[current_period.id].balance

            # F-009 / CRIT-01: 614.29 - max(500 - 45.71 - 0, 0)
            #                = 614.29 - 454.29 = 160.00.
            assert grid_current_balance == Decimal("160.00")

            # The cash-detail balance hero renders ``money(current_balance)``
            # (accounts/cash_detail.html), so the entries-aware $160.00
            # appears verbatim.
            resp = auth_client.get(f"/accounts/{account.id}/details")
            assert resp.status_code == 200
            assert b"$160.00" in resp.data
            # Pre-Commit-7 the page showed the silent-degrade value.
            # Asserting its absence locks the regression.
            assert b"$114.29" not in resp.data

    def test_accounts_anchor_null_fork_removed(self):
        """C7-2: the dead anchor-NULL fallback fork is deleted from accounts.

        Verification gate from the Commit 7 spec.  Post-Commit-3 the
        anchor columns are NOT NULL with a CHECK constraint, so the
        legacy ``account.current_anchor_period_id or current_period``
        substitution is unreachable.  Leaving it in place would be a
        dead branch (CLAUDE.md rule 1: do it right) and a regression
        risk if the CHECK is ever loosened again.  This test greps
        every module in the accounts route package to fail loud if
        the fork is ever reintroduced.

        File-path note: Commit 21 of the follow-up remediation (F-1)
        split the monolithic ``app/routes/accounts.py`` into a per-
        sub-domain package.  The anchor-related routes now live in
        ``app/routes/accounts/crud.py``,
        ``app/routes/accounts/anchor.py``, and
        ``app/routes/accounts/detail.py``; the guard sweeps all of
        them so a regression in any sub-module trips this lock.
        """
        import os  # pylint: disable=import-outside-toplevel
        import re  # pylint: disable=import-outside-toplevel

        package_dir = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "app", "routes", "accounts",
        )
        sources = []
        for name in sorted(os.listdir(package_dir)):
            if not name.endswith(".py"):
                continue
            with open(
                os.path.join(package_dir, name), "r", encoding="utf-8",
            ) as fh:
                sources.append((name, fh.read()))

        for name, src in sources:
            # Strip comments before matching: documentation that
            # references the legacy pattern by name is fine; only live
            # code is forbidden.
            code_lines = [
                line for line in src.splitlines()
                if not line.lstrip().startswith("#")
            ]
            code = "\n".join(code_lines)

            # The legacy fork had two shapes; both are dead post-Commit-3.
            assert not re.search(r"current_anchor_period_id\s+or\s+", code), (
                f"anchor-NULL fork (`... or current_period`) found in "
                f"app/routes/accounts/{name}"
            )
            assert not re.search(r"current_anchor_balance\s+or\s+", code), (
                f"anchor-NULL fork (`... or 0`) found in "
                f"app/routes/accounts/{name}"
            )

    def test_accounts_multi_account_each_correct(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """C7-3: two checking accounts with entries each project correctly.

        Pins that the entries-aware reduction applies per-account:
        account A has cleared entries totaling $45.71 against a $500
        estimate, account B has cleared entries totaling $100.00
        against a $300 estimate, both on the current period.

          Account A: 614.29 + 0 - max(500 - 45.71, 0) = 160.00
          Account B: 1000.00 + 0 - max(300 - 100,    0) = 800.00

        Pre-Commit-7 both pages would have silently degraded to the
        non-entries-aware projection ($114.29 and $700.00).
        """
        with app.app_context():
            current_period = current_pay_period(
                seed_user["user"].id
            )
            assert current_period is not None

            # Account A: the seed_user account, anchored at 614.29.
            account_a = seed_user["account"]
            _override_account_anchor(
                db.session, account_a, current_period, Decimal("614.29"),
            )

            # Account B: a second checking account anchored at 1000.00.
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            account_b = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Second Checking",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.flush()

            txn_a = _make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                account_id=account_a.id,
                estimated=Decimal("500.00"),
                name="Groceries A",
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_cleared_debit_entry(
                    db.session,
                    txn=txn_a,
                    user_id=seed_user["user"].id,
                    amount=amt,
                )

            txn_b = _make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                account_id=account_b.id,
                estimated=Decimal("300.00"),
                name="Groceries B",
            )
            _add_cleared_debit_entry(
                db.session,
                txn=txn_b,
                user_id=seed_user["user"].id,
                amount=Decimal("100.00"),
            )
            db.session.commit()

            # F-009 / CRIT-01: each account projects from its own
            # anchor minus the entries-aware reservation on its own
            # transactions.  Pre-Commit-7 both pages showed the
            # non-entries-aware value ($114.29 and $700.00).
            resp_a = auth_client.get(f"/accounts/{account_a.id}/details")
            assert resp_a.status_code == 200
            assert b"$160.00" in resp_a.data
            assert b"$114.29" not in resp_a.data

            resp_b = auth_client.get(f"/accounts/{account_b.id}/details")
            assert resp_b.status_code == 200
            # 1000.00 - max(300 - 100, 0) = 1000.00 - 200.00 = 800.00.
            assert b"$800.00" in resp_b.data
            assert b"$700.00" not in resp_b.data

    def test_accounts_checking_zero_anchor_renders_projection(
        self, app, auth_client, db, seed_user,
    ):
        """Zero-anchor account shows a populated zero-anchored projection.

        Commit 7 verification gate: an account with anchor balance
        ``Decimal("0.00")`` (a real zero per E-12, not "missing") must
        render with a populated period projection -- not blank, not
        omitted.  Pre-Commit-3 the anchor-NULL fork would have made the
        page degrade differently from the grid; post-Commit-3 the
        anchor is non-NULL and post-Commit-7 the producer routes the
        zero anchor through ``balances_for`` like any other balance.

        Hand arithmetic: no transactions, anchor 0.00, projection is
        flat 0.00 across every period covered by the anchor period
        forward.  The current-balance display, the balance-projection
        table, and the 3-month horizon ($0) must all appear.

        Periods are generated with ``num_periods=10`` starting today,
        so the anchor sits on period 0 and ``period_index`` 6 (the
        3-month horizon) is reachable.  ``seed_periods_today`` would
        anchor today on period 4 and ``4 + 6 = 10`` exceeds the
        fixture's 10-period window, omitting the horizon -- the
        explicit ``generate_pay_periods`` here mirrors the other
        ``TestCheckingDetail`` tests so the horizon is in range.
        """
        with app.app_context():
            # The CALL is the setup: it creates the ten periods this
            # page projects across.  The return value is unused since
            # the anchor no longer names a period.
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, cadence_days=14,
            )

            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Zero Anchor Checking",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.flush()
            db.session.commit()

            resp = auth_client.get(f"/accounts/{account.id}/details")
            assert resp.status_code == 200
            assert b"Zero Anchor Checking" in resp.data
            # The balance hero renders ``money(current_balance)``; a real
            # zero anchor (E-12: zero is a value, not "missing") must show
            # as "$0.00", not blank or "--".
            assert b"$0.00" in resp.data
            # The 3-month horizon chip appears when there is a period at
            # offset 6 from the current period; the explicit 10-period
            # setup guarantees one (matches the existing
            # ``test_checking_detail_handles_short_horizon`` pattern).  The
            # chip label is "In 3 months".
            assert b"3 months" in resp.data
            # The trend chart is populated (a real zero projection, not an
            # empty state): the serialized series carries the flat-zero
            # balances the chart renders, so the data-chart canvas is
            # present rather than the "No projection to chart yet" state.
            assert b"data-chart=" in resp.data
            assert b"No projection to chart yet" not in resp.data

    def test_cash_detail_balance_routed_through_seam(self):
        """Static guard: cash_detail balances route through the balance-at seam.

        F-6 lock, sibling of
        ``test_grid_balance_computation_routed_through_resolver``.
        The cross-page balance-equality regression test
        (``tests/test_integration/test_cross_page_balance_equality.py``,
        Commit 11 of the main remediation) cannot catch a route-handler
        bypass of the canonical producer because its /accounts reader
        re-runs ``balance_at.cash_balance_map`` itself rather than parsing
        the rendered HTML.  A regression that swaps the detail route for a
        hand-rolled balance loop would drift silently through that lock.  This
        static guard closes the gap.

        The single ``cash_detail`` route reads plain cash balances via the
        cash-flow entry ``balance_at.cash_balance_map`` and interest-bearing
        balances via ``balance_at.interest_projection_for_account``, which
        returns the accrued balances AND the earned-interest map from ONE cash
        fold.  It reaches no producer directly -- structurally since plan step
        D3, W9910 forbidding any import of a seam-private module.

        **Every arm matches the CALL, with its open paren, and that is the
        whole reason this guard was rewritten** (plan finding N-63, promoted to
        a Section 8 lesson: "a static guard that greps for a NAME cannot tell
        code from prose").  Its second arm used to look for the bare name
        ``balance_at.balance_map`` -- and plan step X-c2b2 replaced that call
        with ``interest_projection_for_account``, leaving the string alive in
        three docstrings, so the guard went on reporting the wiring intact while
        what it locked had moved.  Measured on this tree before the fix: zero
        call sites, three prose mentions, green.  The docstrings were corrected
        with it.

        Three assertions:
          1. ``balance_at.cash_balance_map(`` must appear (positive: the
             plain-cash seam wiring is intact).
          2. ``balance_at.interest_projection_for_account(`` must appear
             (positive: the interest wiring is intact).
          3. ``balance_at.balance_map(`` must NOT appear -- the whole-account
             kind-correct map is the shape the page used before X-c2b2, and
             reaching it beside the interest map is what made the page fold the
             SAME account twice per render (finding N-64).  This arm is what
             keeps that fix from silently regressing.

        **A FOURTH arm forbade ``balance_calculator.calculate_balances(`` and
        was deleted at plan step X-g4b, with the producer.**  Section 8's rule:
        when the name a negative arm forbids no longer exists, the arm stops
        being a guard and becomes a sentence that can never fail -- which reads
        as coverage and is not.  It deletes WITH the name.

        File path note: the merged ``cash_detail`` route (and the
        ``checking_detail`` / ``interest_detail`` redirect stubs) live in
        ``app/routes/accounts/detail.py``; the static guard reads that file
        directly.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        accounts_source = Path(
            "app/routes/accounts/detail.py",
        ).read_text(encoding="utf-8")
        assert "balance_at.cash_balance_map(" in accounts_source, (
            "app/routes/accounts/detail.py no longer CALLS "
            "``balance_at.cash_balance_map(`` -- regression on the "
            "balance-at seam contract.  Route the plain-cash balance "
            "computation through the seam's cash-flow entry instead of a "
            "hand-rolled loop or a direct producer call."
        )
        assert (
            "balance_at.interest_projection_for_account(" in accounts_source
        ), (
            "app/routes/accounts/detail.py no longer CALLS "
            "``balance_at.interest_projection_for_account(`` -- regression on "
            "the balance-at seam contract.  An interest account's accrued "
            "balances and its earned-interest map must come from ONE seam "
            "call, so the page cannot fold the account twice (N-64) or show a "
            "chip that explains a balance change the chart does not."
        )
        assert "balance_at.balance_map(" not in accounts_source, (
            "app/routes/accounts/detail.py CALLS the whole-account "
            "kind-correct ``balance_at.balance_map(`` -- that is the "
            "pre-X-c2b2 shape, and calling it beside the interest map folds "
            "the same account twice per render (N-64).  Read both halves from "
            "``interest_projection_for_account`` instead."
        )


class TestCheckingDashboardLink:
    """Tests for the cash-detail links on the savings/accounts dashboard.

    The cockpit's ``detail_endpoint`` macro (shared via _acct_macros.html)
    now routes EVERY cash account -- checking, interest-bearing, and the
    previously page-less plain types -- to the unified
    ``accounts.cash_detail`` page (account_detail_audit.md, rebuild
    decisions 1-2), so the retired type-specific ``/checking`` URL must no
    longer appear anywhere on the dashboard.
    """

    def test_dashboard_has_checking_detail_link(self, app, auth_client, seed_user):
        """GET /savings links the checking card to the unified detail page."""
        with app.app_context():
            # The CALL is the setup: the dashboard can only compute a
            # balance for the seed account across periods that exist.
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, cadence_days=14,
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The checking card links to the unified cash detail page,
            # not the retired type-specific /checking URL.
            expected_url = f"/accounts/{seed_user['account'].id}/details"
            assert expected_url.encode() in resp.data
            legacy_url = f"/accounts/{seed_user['account'].id}/checking"
            assert legacy_url.encode() not in resp.data

    def test_dashboard_links_plain_types_to_cash_detail(
        self, app, auth_client, seed_user,
    ):
        """A plain Savings card links to the unified page (decision 2).

        Pre-rebuild, Savings / Credit Card cards had NO detail link (the
        macro's empty fall-through branch); the coverage ruling gives
        them the same cash detail page as checking.
        """
        with app.app_context():
            # The CALL is the setup: the dashboard can only compute a
            # balance for either card across periods that exist.
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, cadence_days=14,
            )

            # Create a savings account.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="My Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)

            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The savings card now links to the unified cash detail page.
            savings_details_url = f"/accounts/{savings.id}/details"
            assert savings_details_url.encode() in resp.data

            # And the checking card does too.
            checking_url = f"/accounts/{seed_user['account'].id}/details"
            assert checking_url.encode() in resp.data


# ── Cash Detail: merged-page context contract (Fable 5 rebuild) ─────


def _capture_cash_detail_context(app, auth_client, account_id, *, setup=False):
    """Return the exact context ``cash_detail`` handed ``render_template``.

    Uses Flask's ``template_rendered`` signal so the test reads the route's
    context contract (the Decimals, the horizons list, the chart JSON,
    ``anchor_as_of``) WITHOUT parsing HTML -- the established chart / net-
    worth route-test pattern (see
    ``tests/test_routes/test_savings.py::_capture_dashboard_context``).
    Asserts the cash-detail template rendered (a 200 page), so the helper
    fails loud on a redirect / 404 rather than reading a wrong context.
    """
    # Pylint: import-outside-toplevel -- deferred import is the file-wide
    # test convention.
    from flask import template_rendered  # pylint: disable=import-outside-toplevel

    recorded = []

    def _record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(_record, app)
    try:
        url = f"/accounts/{account_id}/details"
        if setup:
            url += "?setup=1"
        response = auth_client.get(url)
    finally:
        template_rendered.disconnect(_record, app)

    assert response.status_code == 200, (
        f"GET {url} returned {response.status_code}; expected 200"
    )
    records = [
        c for t, c in recorded if t.name == "accounts/cash_detail.html"
    ]
    assert records, (
        "GET did not render accounts/cash_detail.html; rendered: "
        f"{[t.name for t, _ in recorded]!r}"
    )
    return records[0]


class TestCashDetailContext:
    """The merged cash-detail route's context contract (Fable 5 rebuild).

    Asserts the route hands the template the exact figures the rebuild
    specifies -- the hero balance, the horizon chip rows with Decimal
    deltas, the interest-next-year health figure, the anchored-as-of event
    date, and the Chart.js series -- read straight from the render context
    so the assertions are template-presentation independent.
    """

    def _checking_with_income(self, seed_user, num_periods=27, cadence_days=14):
        """Create a checking account with +$500/period net income.

        Anchor $5,000 at ``periods[0]`` (today), then a $2,000 income and a
        $1,500 expense in every post-anchor period -- the same shape as
        ``test_checking_detail_projection_values_are_correct`` so the
        balances are the hand-computed 5000 + n*500.  Returns
        ``(account, periods)``.

        ``cadence_days`` defaults to the biweekly rhythm every case here used
        before recurrence plan step **R-F17**; the horizon cases pass another
        one, because at 14 days the derived offsets and the hardcoded 6 / 13 /
        26 they replaced are the same numbers and no case could tell them
        apart.
        """
        periods = pay_period_write.record_paydays(
            user_id=seed_user["user"].id,
            first_payday=display_today(),
            num_periods=num_periods, cadence_days=cadence_days,
        )
        checking_type = db.session.query(AccountType).filter_by(
            name="Checking",
        ).one()
        acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Ctx Checking",
                anchor_balance=Decimal("5000.00"),
            ),
        )
        db.session.add(acct)
        db.session.flush()

        projected = db.session.query(Status).filter_by(name="Projected").one()
        income_type = db.session.query(TransactionType).filter_by(
            name="Income",
        ).one()
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        category = seed_user["categories"]["Salary"]
        for period in periods[1:]:
            db.session.add(Transaction(
                pay_period_id=period.id, scenario_id=seed_user["scenario"].id,
                account_id=acct.id, status_id=projected.id, name="Paycheck",
                category_id=category.id, transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            ))
            db.session.add(Transaction(
                pay_period_id=period.id, scenario_id=seed_user["scenario"].id,
                account_id=acct.id, status_id=projected.id, name="Bills",
                category_id=category.id, transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1500.00"),
            ))
        db.session.commit()
        return acct, periods

    def test_horizons_carry_decimal_deltas(self, app, auth_client, seed_user):
        """The horizon chip rows carry the projected value and the Decimal delta.

        With anchor $5,000 at the current period (period 0, no transactions
        there) and +$500 net per post-anchor period, the hero balance is
        $5,000 and the horizons are (value, delta = value - current):

          3 months  -> period  6: 5000 + 6*500  = 8000.00,  delta 3000.00
          6 months  -> period 13: 5000 + 13*500 = 11500.00, delta 6500.00
          1 year    -> period 26: 5000 + 26*500 = 18000.00, delta 13000.00
        """
        with app.app_context():
            acct, _periods = self._checking_with_income(seed_user, num_periods=27)
            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert context["current_balance"] == Decimal("5000.00")
            triples = [
                (h["label"], h["value"], h["delta"])
                for h in context["horizons"]
            ]
            assert triples == [
                ("3 months", Decimal("8000.00"), Decimal("3000.00")),
                ("6 months", Decimal("11500.00"), Decimal("6500.00")),
                ("1 year", Decimal("18000.00"), Decimal("13000.00")),
            ]

    def test_the_horizons_follow_the_owners_cadence(
        self, app, auth_client, seed_user,
    ):
        """A WEEKLY owner reads 13 / 26 / 52 periods out, not 6 / 13 / 26.

        Recurrence plan step **R-F17**, ledger row **F-17**.  Identical
        account shape to ``test_horizons_carry_decimal_deltas`` above --
        $5,000 anchored at the current period, +$500 net per period after it
        -- so the two cases differ in the owner's cadence and in nothing else,
        and the dollars are the same arithmetic:

          3 months  -> period 13: 5000 + 13*500 = 11500.00, delta  6500.00
          6 months  -> period 26: 5000 + 26*500 = 18000.00, delta 13000.00
          1 year    -> period 52: 5000 + 52*500 = 31000.00, delta 26000.00

        Before the derivation this owner's "1 year" chip read period 26 --
        $18,000.00, their SIX-month balance, under a label saying a year.
        """
        with app.app_context():
            acct, _periods = self._checking_with_income(
                seed_user, num_periods=53, cadence_days=7,
            )
            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert context["current_balance"] == Decimal("5000.00")
            assert [
                (h["label"], h["value"], h["delta"])
                for h in context["horizons"]
            ] == [
                ("3 months", Decimal("11500.00"), Decimal("6500.00")),
                ("6 months", Decimal("18000.00"), Decimal("13000.00")),
                ("1 year", Decimal("31000.00"), Decimal("26000.00")),
            ]

    def test_a_horizon_no_paycheck_reaches_is_not_shown_at_all(
        self, app, auth_client, seed_user,
    ):
        """An owner paid every 300 days is shown "1 year" and nothing shorter.

        Ruling **R-R31**: no paycheck arrives inside three months, and the pay
        period is the finest forward resolution this page has -- so there is
        no column to value and the chip is absent rather than repeating the
        hero's own figure under a label naming a shorter span.  The "1 year"
        chip survives, one period out: $5,000 + $500 = $5,500.
        """
        with app.app_context():
            acct, _periods = self._checking_with_income(
                seed_user, num_periods=3, cadence_days=300,
            )
            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert [
                (h["label"], h["value"], h["delta"])
                for h in context["horizons"]
            ] == [("1 year", Decimal("5500.00"), Decimal("500.00"))]

    def test_an_owner_with_no_paydays_gets_the_page_and_no_chips(
        self, app, auth_client, seed_user, db,
    ):
        """No paydays at all: 200 with empty chips, not a 500.

        **The state that makes the cadence read's ORDERING load-bearing**, and
        it is measured rather than assumed: an account no longer carries an
        anchor PERIOD (rulings R-EH / R-EO deleted both columns), so an owner
        can hold accounts and no pay periods -- and with no
        ``budget.pay_schedule`` row either,
        :attr:`app.services.pay_calendar.PayCalendar.cadence` REFUSES, because
        nothing has said how often they are paid.

        Recurrence plan step **R-F17** made both forward figures on this page
        functions of that cadence, so reading it before the no-current-period
        return would turn this 200 into a 500.  ``_build_horizons`` reads it
        past its own ``None`` return and the interest chip reads it inside its
        conditional expression, which is what keeps this case answering.

        **The two conjuncts of the interest chip's guard are covered by
        SEPARATE cases, and a first draft covered only one.**  That draft
        asserted both on the seeded Checking account, where ``is_interest and
        current_period is not None`` short-circuits on the FIRST conjunct -- so
        ``interest_next_year is None`` was satisfied by the account kind alone
        and the second conjunct was never exercised.  An adversarial review
        proved it by deleting that conjunct: all 268 cases in this file still
        passed.  This case is the CADENCE half (a paydayless owner, where
        reading the cadence at all would raise); the lapsed-schedule case below
        is the CURRENT-PERIOD half.
        """
        with app.app_context():
            uid = seed_user["user"].id
            assert db.session.query(PaySchedule).filter_by(
                user_id=uid,
            ).count() == 0, (
                "this case needs an owner with no stored cadence; the fixture "
                "now writes one, so the refusal it exercises has moved"
            )
            db.session.query(PayPeriod).filter_by(user_id=uid).delete()
            db.session.commit()

            context = _capture_cash_detail_context(
                app, auth_client, seed_user["account"].id,
            )

            assert context["current_period"] is None
            assert context["horizons"] == []
            assert context["interest_next_year"] is None

    def test_an_interest_account_on_a_lapsed_schedule_still_renders(
        self, app, auth_client, seed_user, db,
    ):
        """No paycheck covers TODAY, on an account that DOES show the chip.

        The other half of the interest chip's guard, and the ordinary one: the
        owner has paydays -- so their cadence is perfectly readable -- but the
        schedule has lapsed and none of them covers the read pass's day.
        ``_interest_next_year`` dereferences ``current_period.period_index``,
        so without the ``current_period is not None`` conjunct this is an
        ``AttributeError`` and a 500 on an interest-bearing account whose owner
        simply has not extended their calendar.

        ``seed_user``'s only period is its 2024 bootstrap, and the HYSA is
        anchored INTO it -- ``AccountSpec.observed_on`` takes a past day by
        design, which is what lets an account exist on a schedule that no
        longer reaches today.
        """
        with app.app_context():
            uid = seed_user["user"].id
            bootstrap = (
                db.session.query(PayPeriod).filter_by(user_id=uid)
                .order_by(PayPeriod.period_index).first()
            )
            assert bootstrap.start_date < display_today(), (
                "this case needs a schedule that no longer reaches today"
            )
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA",
            ).one()
            hysa = account_service.create_account(
                account_service.AccountSpec(
                    user_id=uid,
                    account_type_id=hysa_type.id,
                    name="Lapsed HYSA",
                    anchor_balance=Decimal("2500.00"),
                    observed_on=bootstrap.start_date,
                ),
            )
            db.session.add(hysa)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=hysa.id, apy=Decimal("0.04000"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            context = _capture_cash_detail_context(app, auth_client, hysa.id)

            # is_interest is TRUE, so the guard's first conjunct passes and the
            # SECOND is what keeps the page at 200.
            assert context["is_interest"] is True
            assert context["current_period"] is None
            assert context["interest_next_year"] is None
            assert context["horizons"] == []

    def test_chart_json_structure_and_current_index(
        self, app, auth_client, seed_user,
    ):
        """chart_json parses to the labeled float series with an int current_index.

        The series is every period that has a projected balance, in
        ``period_index`` order -- which since plan step X-c2b2 is EVERY period
        the user has, because the fold answers a pre-anchor period with the
        balance in force then instead of omitting it (finding cash D3).  The
        chart therefore opens on the ``seed_user`` bootstrap period that
        precedes this fixture's own range, carrying the account's opening
        $5,000.00 back-projected flat (ruling R-I), and ``current_index`` is
        the current period's position in that full list rather than 0.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        import json  # pylint: disable=import-outside-toplevel
        with app.app_context():
            acct, _periods = self._checking_with_income(seed_user, num_periods=27)
            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert context["has_chart"] is True
            chart = json.loads(context["chart_json"])
            assert set(chart.keys()) == {"labels", "balance", "current_index"}
            n = len(chart["balance"])
            assert n > 0
            assert len(chart["labels"]) == n
            assert all(isinstance(v, float) for v in chart["balance"])
            assert isinstance(chart["current_index"], int)
            assert 0 <= chart["current_index"] < n
            # The current period's position in the user's FULL period list --
            # one past the bootstrap period the chart now also draws.
            owner_periods = all_periods(
                seed_user["user"].id,
            )
            assert n == len(owner_periods)
            assert chart["current_index"] == [
                p.id for p in owner_periods
            ].index(_periods[0].id) == 1
            # Every period before the anchor holds the opening flat (R-I), so
            # the series opens on the hero figure either way.
            assert chart["balance"][0] == 5000.0

    def test_anchor_as_of_is_event_date_not_period_start(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """anchor_as_of is the AccountAnchorHistory event date, not the period start.

        The account is anchored at ``seed_periods_today[0]`` (whose
        ``start_date`` is roughly eight weeks before today), but its
        origination ``AccountAnchorHistory`` row is created now, so the
        anchor EVENT date (today) differs from the anchor PERIOD's start
        date.  The context must carry the event INSTANT (the audit's
        finding #2 fix), NOT the period start; the template renders that
        instant in the user's display timezone.

        **The non-vacuity check reads the event's DISPLAY day**, which is what
        the caption shows.  It read ``AnchorPoint.as_of_date`` -- a UTC day --
        until that field was deleted (finding N-133 / F12): it had no
        production reader, and its own docstring justified the UTC choice by an
        index that now keys on the stored ``observed_on`` instead.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.utils.dates import to_display_date  # pylint: disable=import-outside-toplevel
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Anchor Date Checking",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            anchor = cash_ledger.resolve_anchor(acct)
            # Non-vacuity: the event date and the period start genuinely differ.
            # The period comes off the FIXTURE now, not off the AnchorPoint --
            # ruling R-EO deleted ``AnchorPoint.period``, because an assertion
            # is a day and a balance.
            anchor_period_start = seed_periods_today[0].start_date
            assert to_display_date(anchor.created_at) != anchor_period_start

            context = _capture_cash_detail_context(app, auth_client, acct.id)
            # The context carries the day the balance was TRUE
            # (``AnchorPoint.observed_on``), not the day the row was recorded.
            # The two were the same day by construction until ``observed_on``
            # became a stored, user-supplied column (ruling R-DH, plan step 2);
            # a caption reading ``created_at`` would name the keystroke.
            assert context["anchor_as_of"] == anchor.observed_on
            assert context["anchor_as_of"] != anchor_period_start

    def test_interest_next_year_zero_for_zero_apy(
        self, app, auth_client, seed_user, db,
    ):
        """A zero-APY interest account's next-year figure is exactly $0.00.

        Zero APY accrues no interest in any period, so the next-year window
        sum is ``Decimal("0.00")`` -- a legitimate value (E-12: zero is a
        value, not "missing"), not ``None``.  ``None`` is reserved for
        plain (non-interest) accounts.
        """
        with app.app_context():
            # The CALL is the setup: the next-year window needs a year
            # of periods to sum interest across.
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=30, cadence_days=14,
            )
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Zero APY HYSA",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            context = _capture_cash_detail_context(app, auth_client, acct.id)
            assert context["is_interest"] is True
            assert context["interest_next_year"] == Decimal("0.00")

    def test_interest_next_year_sums_only_the_next_year_window(
        self, app, auth_client, seed_user, db,
    ):
        """interest_next_year sums exactly the periods in [current+1, current+26].

        Thirty-three periods are generated so periods beyond the one-year
        window (``current.period_index + 26``) still accrue interest; the
        route's figure must sum ONLY the 26 in-window periods, so it equals
        the independently-summed window and is STRICTLY LESS than the sum
        over every period (the out-of-window tail proves the window bites).
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.services.balance_at import _kernel as net_worth_kernel  # pylint: disable=import-outside-toplevel
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=33, cadence_days=14,
            )
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Window HYSA",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.05000"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            params = db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).one()
            current = current_pay_period(seed_user["user"].id)
            # pylint: disable=import-outside-toplevel
            from app.services.balance_at import BalanceContext
            ibp = net_worth_kernel.interest_by_period_for_account(
                acct, BalanceContext.build(seed_user["user"].id),
            )
            lo = current.period_index + 1
            hi = current.period_index + 26  # 26 biweekly periods = 1 year.
            window_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods
                 if lo <= p.period_index <= hi),
                Decimal("0.00"),
            )
            grand_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods),
                Decimal("0.00"),
            )

            context = _capture_cash_detail_context(app, auth_client, acct.id)
            assert context["interest_next_year"] == window_total
            # Non-vacuity: periods beyond the window accrue interest that the
            # route must NOT include, so the window sum is strictly smaller.
            assert window_total < grand_total

    def test_interest_window_is_the_owners_year_not_twenty_six_periods(
        self, app, auth_client, seed_user, db,
    ):
        """A WEEKLY owner's "next 12 mo" chip sums 52 periods, not 26.

        Recurrence plan step **R-F17**, ledger row **F-17**.  The window was a
        hardcoded ``_ONE_YEAR_PERIODS = 26`` whose own comment asserted it
        matched the "1 year" balance chip beside it; at this cadence 26
        periods is SIX months, so the chip summed half the interest it named
        and the two chips disagreed about what a year is.

        Sixty periods are generated so the tail beyond the window still
        accrues -- the route's figure must equal the independently summed
        ``[current + 1, current + 52]`` and be STRICTLY LESS than the sum over
        every period, which is what proves the window bites rather than
        happening to cover everything.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.services.balance_at import _kernel as net_worth_kernel  # pylint: disable=import-outside-toplevel
        from app.services.balance_at import BalanceContext  # pylint: disable=import-outside-toplevel
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=60, cadence_days=7,
            )
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Weekly HYSA",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.05000"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            current = current_pay_period(seed_user["user"].id)
            ibp = net_worth_kernel.interest_by_period_for_account(
                acct, BalanceContext.build(seed_user["user"].id),
            )
            lo = current.period_index + 1
            hi = current.period_index + 52  # 52 WEEKLY periods = 1 year.
            window_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods
                 if lo <= p.period_index <= hi),
                Decimal("0.00"),
            )
            grand_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods),
                Decimal("0.00"),
            )
            half_year_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods
                 if lo <= p.period_index <= current.period_index + 26),
                Decimal("0.00"),
            )

            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert context["interest_next_year"] == window_total
            assert window_total < grand_total
            # The number the hardcoded 26 would have produced, shown to be a
            # DIFFERENT figure rather than merely a different expression.
            assert half_year_total < window_total

    def test_plain_account_interest_next_year_is_none(
        self, app, auth_client, seed_user,
    ):
        """A plain (non-interest) account carries interest_next_year = None.

        The next-year interest chip is interest-only; a checking account
        has no interest projection, so the figure is ``None`` (the template
        omits the chip), NOT ``Decimal("0.00")``.
        """
        with app.app_context():
            acct, _periods = self._checking_with_income(seed_user, num_periods=10)
            context = _capture_cash_detail_context(app, auth_client, acct.id)
            assert context["is_interest"] is False
            assert context["interest_next_year"] is None
            assert context["params"] is None
            assert context["compounding_frequencies"] == []


class TestCashDetailRedirectStubs:
    """The legacy /checking and /interest URLs redirect to the merged page.

    The Fable 5 overhaul kept ``checking_detail`` / ``interest_detail`` as
    thin redirect stubs (not deletions) so external bookmarks and the
    not-yet-updated cockpit ``detail_endpoint`` macro still resolve; the
    ``setup=1`` onboarding arg is forwarded so a post-create redirect still
    lands on the wizard banner.
    """

    def test_checking_stub_redirects_to_details(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/checking 302-redirects to /accounts/<id>/details."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(f"/accounts/{acct_id}/checking")
            assert resp.status_code == 302
            assert f"/accounts/{acct_id}/details" in resp.headers["Location"]

    def test_interest_stub_redirects_to_details(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/interest 302-redirects to /accounts/<id>/details."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(f"/accounts/{acct_id}/interest")
            assert resp.status_code == 302
            assert f"/accounts/{acct_id}/details" in resp.headers["Location"]

    def test_interest_stub_preserves_setup_param(
        self, app, auth_client, seed_user,
    ):
        """The interest stub forwards ?setup=1 so onboarding survives the redirect."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(f"/accounts/{acct_id}/interest?setup=1")
            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert f"/accounts/{acct_id}/details" in location
            assert "setup=1" in location


class TestCashDetailWrongTypeMatrix:
    """Non-cash account kinds 404 out of the merged cash detail page.

    The page serves cash accounts only; loans (has_amortization), physical
    assets (has_appreciation), and retirement / investment accounts
    (category RETIREMENT / INVESTMENT) keep their own screens and must 404
    here -- resolved by boolean type flag and integer category id, never a
    ref-table name string.
    """

    @pytest.mark.parametrize(
        "type_name", ["Mortgage", "Property", "401(k)", "Brokerage"],
    )
    def test_non_cash_type_404(
        self, app, auth_client, seed_user, db, type_name,
    ):
        """A loan / property / retirement / investment account 404s on /details."""
        with app.app_context():
            acct_type = db.session.query(AccountType).filter_by(
                name=type_name,
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=acct_type.id,
                    name=f"WrongType {type_name}",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 404


class TestCashDetailNewCoverage:
    """Previously page-less cash types are now served (audit Surface 6)."""

    def test_credit_card_served_as_plain(
        self, app, auth_client, seed_user, db,
    ):
        """A Credit Card account renders on the cash page with the plain shape.

        Credit Card (Liability category, no amortization / appreciation /
        interest) had no detail page before the merge; it is now served
        with the plain context shape -- no interest params, no compounding
        list.
        """
        with app.app_context():
            cc_type = db.session.query(AccountType).filter_by(
                name="Credit Card",
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=cc_type.id,
                    name="My Card",
                    anchor_balance=Decimal("-250.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            context = _capture_cash_detail_context(app, auth_client, acct.id)
            assert context["is_interest"] is False
            assert context["params"] is None
            assert context["compounding_frequencies"] == []

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200
            assert b"My Card" in resp.data
            # Plain cash: no interest parameters card (no APY input).
            assert b'name="apy"' not in resp.data


# ── Optimistic Locking (commit C-17 / F-009) ────────────────────────


def _bump_account_version_outside_session(account_id):
    """Simulate a concurrent commit by bumping ``version_id`` directly.

    Uses a fresh DB connection (NOT the test session) so the in-memory
    identity map of the calling session is unaffected.  After this
    helper returns, any object the caller previously loaded for
    ``account_id`` retains its old in-memory ``version_id`` while the
    database row carries the bumped value -- exactly the state a
    concurrent request from another browser tab would produce.

    The connection commit is essential: without it the UPDATE would
    sit in an open transaction and ``READ COMMITTED`` MVCC would
    hide the bump from the test session.
    """
    with db.engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE budget.accounts "
                "SET version_id = version_id + 1 "
                "WHERE id = :id"
            ),
            {"id": account_id},
        )
        conn.commit()


class TestAccountVersionIdColumn:
    """Schema-level invariants for the optimistic-lock counter."""

    def test_the_anchor_cache_columns_are_gone(self, app):
        """``budget.accounts`` carries NO anchor cache columns (ruling R-EH).

        **Asserted rather than merely deleted**, for the reason the sibling
        lock one table over gives (``test_c43_ondelete_and_naming_convention
        .test_account_anchor_history_has_no_pay_period_fk``): a future
        migration re-adding ``current_anchor_balance`` /
        ``current_anchor_period_id`` would re-open a denormalized second
        balance -- the state whose divergence this arc spent a step deleting --
        with nothing objecting, and "the tests stopped mentioning it" is not
        something a reader can distinguish from an accident.

        The CHECK and the deferrable FK that existed only for those columns are
        asserted gone in the same pass, because each is independently
        re-addable and each carried its own cost: the CHECK was redundant with
        a NOT NULL, and the FK's deferrability existed solely so the pay-period
        reset could re-point the column mid-transaction.
        """
        with app.app_context():
            insp = inspect(db.engine)
            cols = {
                c["name"] for c in insp.get_columns("accounts", schema="budget")
            }
            assert "current_anchor_balance" not in cols
            assert "current_anchor_period_id" not in cols

            checks = {
                c["name"]
                for c in insp.get_check_constraints("accounts", schema="budget")
            }
            assert "ck_accounts_anchor_balance_present" not in checks

            fks = {
                fk.get("name")
                for fk in insp.get_foreign_keys("accounts", schema="budget")
            }
            assert "accounts_current_anchor_period_id_fkey" not in fks

    def test_version_id_column_present_and_not_null(self, app):
        """The live ``budget.accounts`` table carries a NOT NULL ``version_id``."""
        with app.app_context():
            insp = inspect(db.engine)
            cols = {
                c["name"]: c
                for c in insp.get_columns("accounts", schema="budget")
            }

            assert "version_id" in cols, (
                "Account.version_id column is missing from the live "
                "schema -- migration 861a48e11960 may not have run."
            )
            assert cols["version_id"]["nullable"] is False, (
                "Account.version_id must be NOT NULL or the optimistic "
                "lock silently fails on rows that have a NULL counter."
            )

    def test_version_id_check_constraint_present(self, app):
        """The CHECK constraint that pins ``version_id > 0`` is on the live table."""
        with app.app_context():
            insp = inspect(db.engine)
            checks = {
                c["name"]: c["sqltext"]
                for c in insp.get_check_constraints(
                    "accounts", schema="budget",
                )
            }

            assert "ck_accounts_version_id_positive" in checks, (
                "ck_accounts_version_id_positive missing -- the schema "
                "no longer matches the model declaration."
            )
            # PostgreSQL normalises the predicate; either form is valid.
            normalised = checks["ck_accounts_version_id_positive"].lower().replace(" ", "")
            assert "version_id>0" in normalised, (
                "CHECK constraint expression has changed; rerun the "
                "migration or update the model in lockstep."
            )

    def test_version_id_check_rejects_zero(self, app, db, seed_user):
        """Inserting a row with ``version_id = 0`` raises IntegrityError.

        The application never sets ``version_id`` directly; this test
        exercises the database-tier guard against a future raw-SQL
        path or a buggy migration that writes 0.
        """
        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            with pytest.raises(IntegrityError):
                db.session.execute(
                    text(
                        "INSERT INTO budget.accounts "
                        "(user_id, account_type_id, name, version_id) "
                        "VALUES (:u, :t, :n, 0)"
                    ),
                    {
                        "u": seed_user["user"].id,
                        "t": checking_type.id,
                        "n": "Bad Version",
                    },
                )
                db.session.flush()
            db.session.rollback()

    def test_mapper_declares_version_id_col(self, app):
        """``Account.__mapper_args__`` exposes the version counter to SQLAlchemy.

        Without this declaration SQLAlchemy emits ``UPDATE`` without
        the ``WHERE version_id = ?`` narrowing and the optimistic-lock
        contract collapses; the rest of the test class would still pass
        but production would silently regress.
        """
        with app.app_context():
            mapper = inspect(Account)
            assert mapper.version_id_col is not None, (
                "Account mapper has no version_id_col -- "
                "__mapper_args__ regression."
            )
            assert mapper.version_id_col.name == "version_id"


class TestAccountVersionIdLifecycle:
    """End-to-end behaviour of the ``version_id`` counter through ORM operations."""

    def test_new_account_starts_at_version_one(self, app, auth_client, seed_user):
        """Newly created accounts have ``version_id == 1``.

        ``server_default='1'`` on the column guarantees this for rows
        inserted via SQLAlchemy with no explicit ``version_id``.  The
        seed_user fixture path exercises this exact code path.
        """
        with app.app_context():
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.version_id == 1

    def test_seed_user_account_starts_at_version_one(self, app, seed_user):
        """The seed fixture's account has ``version_id == 1`` after creation."""
        with app.app_context():
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.version_id == 1, (
                f"seed_user fixture account should start at version 1, "
                f"got {acct.version_id}"
            )

    def test_version_does_not_increment_on_read(self, app, db, seed_user):
        """Pure SELECT operations leave ``version_id`` unchanged.

        The optimistic-lock contract increments only on UPDATE/DELETE;
        a regression here would inflate the counter on every page
        view and turn every form submit into a stale-form 409.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            initial_version = db.session.get(Account, acct_id).version_id

            for _ in range(5):
                _ = db.session.get(Account, acct_id).name
                db.session.expire_all()

            final_version = db.session.get(Account, acct_id).version_id
            assert final_version == initial_version

    def test_version_increments_on_update(self, app, db, seed_user):
        """Each ORM-emitted UPDATE bumps ``version_id`` by exactly one."""
        with app.app_context():
            acct_id = seed_user["account"].id
            v0 = db.session.get(Account, acct_id).version_id

            acct = db.session.get(Account, acct_id)
            acct.name = "Renamed Once"
            db.session.commit()
            v1 = db.session.get(Account, acct_id).version_id

            acct.name = "Renamed Twice"
            db.session.commit()
            v2 = db.session.get(Account, acct_id).version_id

            assert v1 == v0 + 1
            assert v2 == v1 + 1


class TestAccountConcurrentMutationStaleData:
    """SQLAlchemy ``StaleDataError`` is raised on truly concurrent races."""

    def test_concurrent_update_raises_stale_data_error(
        self, app, db, seed_user,
    ):
        """A race that bumps the version between load and commit raises StaleDataError.

        The simulated concurrent commit advances the row to version 2;
        the test session, still holding an in-memory copy at version
        1, attempts an UPDATE -- the version-pinned WHERE matches no
        rows and SQLAlchemy raises ``StaleDataError``.  This is the
        load-bearing invariant that makes the SQLAlchemy tier of the
        optimistic lock work.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            acct = db.session.get(Account, acct_id)
            assert acct.version_id == 1

            _bump_account_version_outside_session(acct_id)

            # ANY mutable column proves the version pin -- the point is that
            # the ORM narrows the UPDATE by ``version_id``, not which field is
            # written.  It used to write ``current_anchor_balance``, deleted at
            # plan step X-f1c3c (ruling R-EH); ``name`` is the column the
            # account-edit form actually writes and the one whose door still
            # carries this lock.
            acct.name = "Renamed Under A Stale Version"

            with pytest.raises(StaleDataError):
                db.session.commit()

            db.session.rollback()

            db.session.expire_all()
            persisted = db.session.get(Account, acct_id)
            assert persisted.name != "Renamed Under A Stale Version"
            assert persisted.version_id == 2

    def test_concurrent_delete_raises_stale_data_error(
        self, app, db, seed_user,
    ):
        """DELETE also enforces the version pin; concurrent bump blocks the delete."""
        with app.app_context():
            checking_type = (
                db.session.query(AccountType)
                .filter_by(name="Checking").one()
            )
            spare = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Spare",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(spare)
            db.session.commit()
            spare_id = spare.id

            _bump_account_version_outside_session(spare_id)

            db.session.delete(spare)
            with pytest.raises(StaleDataError):
                db.session.commit()
            db.session.rollback()

            persisted = db.session.get(Account, spare_id)
            assert persisted is not None, (
                "Stale-data DELETE must leave the row intact for the "
                "winner of the race to handle."
            )


class TestTrueUpIsAppendOnly:
    """``true_up`` (PATCH /accounts/<id>/true-up) records, it does not contend.

    **This class graded the C-17 optimistic lock until plan step X-f1c3c**, in
    four cases: a matching ``version_id`` succeeds and bumps the counter, a
    stale one answers 409 with the conflict cell, a flush-time
    ``StaleDataError`` answers the same 409, and an omitted version falls
    through to the database check.  Ruling R-EN deleted all of it, because the
    path stopped writing the row the lock guarded -- a true-up APPENDS an
    assertion, so there is nothing for a second tab to overwrite.  The four
    cases are replaced by the one that states the new contract, plus the
    concurrent case in ``test_race_conditions.TestConcurrentAnchorUpdate``
    (both requests 200, both assertions recorded).
    """

    def test_a_stale_form_still_records_its_assertion(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """A true-up submitted against a stale row succeeds and is recorded.

        The direct inversion of ``test_true_up_returns_409_on_stale_version``.
        The account row is advanced out from under the form (simulating another
        tab having edited the account), and the true-up is submitted anyway.
        It must be accepted: what the user is declaring is what their bank
        holds, and that statement does not become false because some other
        field on the account changed.

        **The payload carries the stale ``version_id``, and that is the whole
        point of the test.**  A first version of it advanced the row and then
        submitted no version at all, which graded a form that never had the
        pin rather than the one R-EN actually makes tolerable: a page cached
        BEFORE the deploy, whose form still renders the hidden field.  That
        payload reaches ``AnchorUpdateSchema``, which no longer declares
        ``version_id`` -- so this also pins the ``unknown = EXCLUDE``
        backward-compatibility the deletion rests on.  A schema that started
        rejecting unknown fields would 400 here.

        Non-vacuity: the version is genuinely advanced first (asserted), the
        submitted pin is asserted to be the STALE one, and the assertion is
        read back out of ``account_anchor_history`` rather than off the
        response, so a 200 that recorded nothing fails here.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            before = db.session.get(Account, acct_id).version_id
            _bump_account_version_outside_session(acct_id)
            db.session.expire_all()
            current = db.session.get(Account, acct_id).version_id
            assert current != before

            resp = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "4242.42",
                    # The pin the pre-deploy form still renders, and it is
                    # STALE -- the row has moved past it.  Before R-EN this
                    # exact payload answered 409.
                    "version_id": str(before),
                },
            )
            assert resp.status_code == 200, resp.data[:200]

            db.session.expire_all()
            account = db.session.get(Account, acct_id)
            assert cash_ledger.resolve_anchor(account).balance == Decimal(
                "4242.42",
            )


class TestUpdateAccountStaleForm:
    """``update_account`` (POST /accounts/<id>) optimistic locking on the full edit form."""

    def test_update_account_succeeds_with_matching_version(
        self, app, auth_client, seed_user,
    ):
        """A matching ``version_id`` on the edit form updates and bumps the counter."""
        with app.app_context():
            acct_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            v0 = db.session.get(Account, acct_id).version_id

            response = auth_client.post(
                f"/accounts/{acct_id}",
                data={
                    "name": "Primary Checking",
                    "account_type_id": str(checking_type.id),
                    "version_id": str(v0),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account &#39;Primary Checking&#39; updated." in response.data

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.name == "Primary Checking"
            assert acct.version_id == v0 + 1

    def test_update_account_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """A stale ``version_id`` redirects back to the edit form with a warning flash.

        The non-HTMX update_account path uses flash + redirect rather
        than a 409 partial because the surrounding UX is a full-page
        form, not a swap.  The behaviour invariant is the same: NO
        write occurs and the user is told the row changed.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            stale_version = db.session.get(Account, acct_id).version_id

            _bump_account_version_outside_session(acct_id)
            db.session.expire_all()
            name_before = db.session.get(Account, acct_id).name

            response = auth_client.post(
                f"/accounts/{acct_id}",
                data={
                    "name": "Should Not Apply",
                    "account_type_id": str(checking_type.id),
                    "version_id": str(stale_version),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.name == name_before, (
                "Stale-form on update_account must NOT mutate any field."
            )


class TestArchiveAndDeleteStaleData:
    """``archive_account`` / ``unarchive_account`` / ``hard_delete_account`` StaleDataError handling."""

    def test_archive_account_stale_data_redirects_with_warning(
        self, app, db, auth_client, seed_user,
    ):
        """A StaleDataError during archive surfaces as a flash + redirect.

        The contract: the user always receives a useful response,
        never a 500.  The account stays unchanged; the user reloads
        and retries.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            spare = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Archive Target",
                    anchor_balance=Decimal("0.00"),
                ),
                is_active=True,
            )
            db.session.add(spare)
            db.session.commit()
            spare_id = spare.id

            fired = {"flag": False}

            def make_stale(mapper, connection, target):
                if fired["flag"] or target.id != spare_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(spare_id)

            event.listen(Account, "before_update", make_stale)
            try:
                response = auth_client.post(
                    f"/accounts/{spare_id}/archive",
                    follow_redirects=True,
                )
            finally:
                event.remove(Account, "before_update", make_stale)

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(Account, spare_id)
            assert persisted.is_active is True, (
                "StaleDataError on archive must NOT flip is_active."
            )

    def test_hard_delete_account_stale_data_redirects_with_warning(
        self, app, db, auth_client, seed_user,
    ):
        """A StaleDataError during hard-delete leaves the row intact.

        Unlike a normal delete, the row does NOT get removed when the
        version race goes against this request.  The user receives a
        warning flash and the row remains for the winner of the race.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            spare = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Delete Target",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(spare)
            db.session.commit()
            spare_id = spare.id

            fired = {"flag": False}

            def make_stale(mapper, connection, target):
                if fired["flag"] or target.id != spare_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(spare_id)

            event.listen(Account, "before_delete", make_stale)
            try:
                response = auth_client.post(
                    f"/accounts/{spare_id}/hard-delete",
                    follow_redirects=True,
                )
            finally:
                event.remove(Account, "before_delete", make_stale)

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(Account, spare_id)
            assert persisted is not None, (
                "StaleDataError on hard-delete must leave the row in "
                "place for the winner of the race to handle."
            )


class TestAnchorTemplatesEmitVersionPin:
    """Which anchor-related forms carry a hidden ``version_id`` pin, and which must not.

    It read "templates that render anchor edit forms MUST include a hidden
    ``version_id`` input" until plan step X-f1c3c.  Ruling R-EN split that in
    two: the ANCHOR editor must not ship one (its handler stopped reading it
    when the true-up stopped writing the ``accounts`` row the counter guards),
    while the full ACCOUNT edit form still must (it writes real columns).  Both
    halves are graded here, which is what keeps the deletion from reading as an
    oversight.

    Three ``*_conflict_cell_retry_opener_*`` cases were DELETED here in the same
    step: they asserted that a 409 conflict cell's retry opener carried the
    right ``revert`` token, and there is no 409.  The revert-token routing they
    shared is still graded by the ``anchor_form`` and ``hx-patch`` cases below,
    per surface.
    """

    def test_anchor_form_ships_no_version_pin(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/anchor-form ships NO version_id pin (ruling R-EN).

        The direct inversion of ``test_grid_anchor_form_includes_version_pin``.
        The pin existed for the C-17 optimistic lock, which the true-up path
        lost at plan step X-f1c3c when it stopped writing the ``accounts`` row
        that ``version_id`` guards.  Shipping a pin the handler no longer reads
        would be a field the form lies about.

        Non-vacuity is the sibling below: the full ACCOUNT edit form must still
        ship one, because that door does write real columns.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            response = auth_client.get(f"/accounts/{acct_id}/anchor-form")

            assert response.status_code == 200
            assert 'name="version_id"' not in response.data.decode()

    def test_anchor_form_default_revert_is_grid_display(
        self, app, auth_client, seed_user,
    ):
        """Without ?revert, the editor reverts to the grid display cell.

        Fix C: the grid path passes no ``revert`` token, so Cancel /
        Escape keep their original target -- the ``accounts.anchor_display``
        GET endpoint -- byte-for-byte unchanged.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(f"/accounts/{acct_id}/anchor-form")
            assert response.status_code == 200
            body = response.data.decode()
            display_url = f"/accounts/{acct_id}/anchor-display"
            # Cancel hx-get and Escape data-revert-url both target the
            # grid display cell.
            assert f'hx-get="{display_url}"' in body
            assert f'data-revert-url="{display_url}"' in body
            assert "/dashboard/balance" not in body

    def test_anchor_form_dashboard_revert_targets_balance_section(
        self, app, auth_client, seed_user,
    ):
        """With ?revert=dashboard, the editor reverts to the balance card.

        Fix C: opened from the dashboard balance card, Cancel / Escape must
        restore THAT card (``dashboard.balance_section``) rather than
        stranding the dashboard on the grid's whole-dollar display cell.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=dashboard"
            )
            assert response.status_code == 200
            body = response.data.decode()
            # Cancel hx-get and Escape data-revert-url both point at the
            # dashboard balance-section partial, not the grid display cell.
            assert 'hx-get="/dashboard/balance"' in body
            assert 'data-revert-url="/dashboard/balance"' in body
            assert f"/accounts/{acct_id}/anchor-display" not in body

    def test_grid_anchor_form_patch_url_carries_no_revert(
        self, app, auth_client, seed_user,
    ):
        """M1 (grid): the edit form's hx-patch carries no ``revert`` token.

        The grid path passes no ``revert`` query param, so the form's
        mutation URL must be the bare ``/true-up`` endpoint -- byte-for-byte
        unchanged from before the revert round-trip was added.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(f"/accounts/{acct_id}/anchor-form")
            assert response.status_code == 200
            body = response.data.decode()
            assert f'hx-patch="/accounts/{acct_id}/true-up"' in body
            assert "revert=dashboard" not in body

    def test_dashboard_anchor_form_patch_url_threads_revert(
        self, app, auth_client, seed_user,
    ):
        """M1 (dashboard): the edit form's hx-patch threads ``revert=dashboard``.

        Opened from the dashboard balance card, the form's mutation URL
        must carry the ``revert`` token so the success re-render lands on the
        dashboard card rather than stranding it on the grid display cell.
        The token also routed a 409 conflict cell's retry opener, until ruling
        R-EN deleted that response (plan step X-f1c3c).
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=dashboard"
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'hx-patch="/accounts/{acct_id}/true-up?revert=dashboard"'
                in body
            )

    def test_anchor_form_accounts_revert_targets_cockpit_balance(
        self, app, auth_client, seed_user,
    ):
        """With ?revert=accounts, the editor reverts to the cockpit card cell.

        Opened from the Net Worth Cockpit's per-card balance, Cancel /
        Escape must restore THAT card's cell (``savings.cockpit_balance``)
        rather than the grid display cell -- the multi-card analog of the
        dashboard ``revert=dashboard`` round-trip.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=accounts"
            )
            assert response.status_code == 200
            body = response.data.decode()
            cell_url = f"/savings/cockpit/{acct_id}/balance"
            assert f'hx-get="{cell_url}"' in body
            assert f'data-revert-url="{cell_url}"' in body
            assert f"/accounts/{acct_id}/anchor-display" not in body

    def test_accounts_anchor_form_patch_url_threads_revert(
        self, app, auth_client, seed_user,
    ):
        """The cockpit edit form's hx-patch threads ``revert=accounts``.

        So the success re-render lands on the cockpit card rather than
        stranding it on the grid display cell.  The token also routed a 409
        conflict cell's retry opener, until ruling R-EN deleted that response
        (plan step X-f1c3c).
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=accounts"
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'hx-patch="/accounts/{acct_id}/true-up?revert=accounts"'
                in body
            )

    def test_account_edit_form_includes_version_pin(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/edit ships ``version_id`` so the POST round-trips."""
        with app.app_context():
            acct_id = seed_user["account"].id
            current_version = db.session.get(Account, acct_id).version_id

            response = auth_client.get(f"/accounts/{acct_id}/edit")

            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{current_version}"' in body

    def test_account_create_form_omits_version_pin(
        self, app, auth_client, seed_user,
    ):
        """The create form has no ``version_id`` -- there is no row to pin yet.

        Catching the regression of a copy-paste that puts an
        ``account.version_id`` reference into the create form would
        produce a Jinja UndefinedError because ``account`` is None
        on that path.
        """
        with app.app_context():
            response = auth_client.get("/accounts/new")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' not in body


# ── Cash Detail Click-to-Edit Hero (S8 / D14 port) ────────────────


class TestCashDetailClickToEditHero:
    """S8 / D14: the cash detail hero doubles as the anchor true-up control.

    Cash previously had NO on-page anchor recording (P-DT8).  The hero
    reuses the shared anchor editor (accounts.anchor_form /
    accounts.true_up / anchor_service) via a new ``revert=cash``
    surface: ``accounts.cash_balance_hero`` is the Cancel / Escape
    revert target (it was a 409-conflict target too, until ruling R-EN
    deleted the 409 at plan step X-f1c3c), and a save fires
    ``balanceChanged`` so the page's ``#cash-band-region`` re-fetches
    ``accounts.cash_band`` -- the hero, horizon chips, interest chip, and
    chart all recompute from the new anchor together.

    ``test_cash_conflict_cell_retry_opener_carries_revert`` was DELETED here at
    plan step X-f1c3c, the fourth of the four conflict-cell cases the ruling
    removed (the other three were in ``TestAnchorTemplatesEmitVersionPin``,
    which records them).  It asserted that a 409 conflict cell's retry opener
    carried ``revert=cash``; there is no 409.  What the token still routes --
    Cancel, Escape and the success re-render -- is graded by
    ``test_anchor_form_cash_revert_targets_cash_hero`` and
    ``test_cash_anchor_form_patch_url_threads_revert`` below, so no live
    behaviour lost its coverage.
    """

    def test_page_hero_is_click_to_edit_inside_band_region(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The detail page wires the band region and the editor opener.

        The hero opens the shared editor scoped to the cash surface, and
        the band region re-fetches accounts.cash_band on balanceChanged
        (the L6 oracle's data-current-balance hook must survive on the
        hero cell).
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(f"/accounts/{acct_id}/details")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'id="cash-band-region"' in body
            assert f'hx-get="/accounts/{acct_id}/details/band"' in body
            assert 'hx-trigger="balanceChanged from:body"' in body
            assert 'id="cash-balance-hero"' in body
            assert (
                f'hx-get="/accounts/{acct_id}/anchor-form?revert=cash"'
                in body
            )
            assert "data-current-balance=" in body

    def test_cash_balance_hero_renders_display_cell(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET (HX) returns the resolver balance and the editor opener.

        The seed account is anchored at $1000.00 with no transactions,
        so the resolver current-period balance the hero shows is exactly
        1,000.00, and the L6 oracle hook carries the raw Decimal.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/details/balance-hero",
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert "$1,000.00" in body
            assert 'data-current-balance="1000.00"' in body
            assert 'id="cash-balance-hero"' in body
            assert (
                f'hx-get="/accounts/{acct_id}/anchor-form?revert=cash"'
                in body
            )

    def test_cash_balance_hero_redirects_without_htmx(
        self, app, auth_client, seed_user,
    ):
        """GET without HX-Request redirects to the detail page."""
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/details/balance-hero",
            )
            assert response.status_code == 302
            assert f"/accounts/{acct_id}/details" in response.headers.get(
                "Location", "",
            )

    def test_cash_balance_hero_idor(self, app, auth_client, seed_user):
        """GET another user's cash hero returns 404 and leaks nothing."""
        with app.app_context():
            other = _create_other_user_account()
            response = auth_client.get(
                f"/accounts/{other['account'].id}/details/balance-hero",
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 404

    def test_cash_band_renders_full_band(
        self, app, auth_client, seed_user,
    ):
        """GET (HX) returns the whole band: hero, caption, chips, chart.

        The balanceChanged refresh target must carry every
        anchor-derived figure so no band surface can disagree with a
        fresh anchor.  Periods are generated with ``num_periods=10``
        starting today so the anchor sits on period 0 and the 3-month
        horizon (period_index 6) is reachable -- ``seed_periods_today``
        would anchor today on period 4 and ``4 + 6 = 10`` exceeds its
        window, omitting the chip (the ``TestCheckingDetail`` pattern).
        Hand arithmetic: anchor 2500.00, no transactions -> the hero
        and every horizon chip read a flat $2,500.
        """
        with app.app_context():
            # The CALL is the setup: it creates the ten periods the
            # band's horizon chips read across.
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, cadence_days=14,
            )
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Band Checking",
                    anchor_balance=Decimal("2500.00"),
                ),
            )
            db.session.flush()
            db.session.commit()

            response = auth_client.get(
                f"/accounts/{account.id}/details/band",
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'id="cash-balance-hero"' in body
            assert "$2,500.00" in body
            assert "current period" in body
            assert "In 3 months" in body
            assert 'id="account-detail-chart-canvas"' in body

    def test_cash_band_redirects_without_htmx(
        self, app, auth_client, seed_user,
    ):
        """GET without HX-Request redirects to the detail page."""
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(f"/accounts/{acct_id}/details/band")
            assert response.status_code == 302
            assert f"/accounts/{acct_id}/details" in response.headers.get(
                "Location", "",
            )

    def test_cash_band_idor(self, app, auth_client, seed_user):
        """GET another user's band returns 404 and leaks nothing."""
        with app.app_context():
            other = _create_other_user_account()
            response = auth_client.get(
                f"/accounts/{other['account'].id}/details/band",
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 404

    def test_anchor_form_cash_revert_targets_cash_hero(
        self, app, auth_client, seed_user,
    ):
        """With ?revert=cash, the editor reverts to the cash hero cell.

        Opened from the cash detail hero, Cancel / Escape must restore
        THAT cell (``accounts.cash_balance_hero``) rather than the grid
        display cell -- the cash analog of the ``revert=accounts``
        round-trip.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=cash",
            )
            assert response.status_code == 200
            body = response.data.decode()
            cell_url = f"/accounts/{acct_id}/details/balance-hero"
            assert f'hx-get="{cell_url}"' in body
            assert f'data-revert-url="{cell_url}"' in body
            assert f"/accounts/{acct_id}/anchor-display" not in body

    def test_cash_anchor_form_patch_url_threads_revert(
        self, app, auth_client, seed_user,
    ):
        """The cash edit form's hx-patch threads ``revert=cash``.

        So a 409 conflict response can re-render the conflict cell with
        the cash retry target rather than stranding the hero on the grid
        display cell.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=cash",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'hx-patch="/accounts/{acct_id}/true-up?revert=cash"'
                in body
            )

    # ``test_true_up_cash_revert_skips_as_of_oob`` was DELETED at plan step
    # X-f1e3.  It is the ``cash-hero`` case of
    # ``TestTrueUp.test_the_as_of_snippet_goes_to_the_grid_and_only_the_grid``,
    # which grades all five openers in one parametrization -- keeping a third
    # copy is the duplication that parametrization removed.  Its second
    # assertion had also become false in principle: it read
    # ``'hx-swap-oob="true"' not in body``, meaning "this surface receives no
    # out-of-band fragment at all", and BOTH the reconcile prompt and the
    # back-dated acknowledgement now ride out-of-band on all five surfaces by
    # design.  It passed only because this fixture has nothing outstanding and
    # submits no ``observed_on``; the first purchase added to it would have
    # failed here and read as a regression in the new feature.


# ── Multi-Tenant Account Type Ownership (commit C-28 / F-044) ─────


def _login_as(app, email, password):
    """Build a fresh ``test_client`` and log it in as the given user.

    Wrapper around the well-known ``auth_client`` /
    ``second_auth_client`` cookie-interaction work-around documented
    in ``tests/test_integration/test_fixture_validation.py`` and
    re-applied in ``tests/test_routes/test_security_event_banner.py``.
    Each call returns an isolated client whose cookie jar is not
    cross-contaminated by any other client built earlier in the same
    test, which is the only reliable way to stage a two-owner
    interaction without one client's session leaking into the other.
    """
    client = app.test_client()
    resp = client.post("/login", data={"email": email, "password": password})
    assert resp.status_code == 302, (
        f"login as {email} failed; got status {resp.status_code}"
    )
    return client


class TestAccountTypeMultiTenantOwnership:
    """Multi-tenant guard for ``ref.account_types`` (C-28 / F-044).

    Every test in this class exercises the per-user namespace policy:

      * Built-in types (``user_id IS NULL``) are seeded by
        ``scripts/seed_ref_tables.py`` and are read-only to every
        owner.
      * Owner-scoped types carry ``user_id = <creator>``.  Only the
        creator may rename or delete them; other owners do not see
        them in any listing and cannot reference them by ID through
        a forged form post.
      * Two different owners may each carry their own custom type
        with the same name; an owner may shadow a seeded built-in
        with their own copy.

    The route response for "type belongs to another owner" is the
    same as for "type does not exist" so the response cannot be used
    to enumerate other owners' catalogues.

    Two-owner scenarios use ``_login_as`` rather than the
    ``second_auth_client`` fixture so each client gets its own clean
    cookie jar.  The second_auth_client fixture interacts oddly with
    auth_client's cookies in the same test session (documented in
    ``test_fixture_validation.py``).
    """

    def test_create_type_persists_owner_user_id(
        self, app, auth_client, seed_user,
    ):
        """A new custom type carries ``user_id = current_user.id``.

        End-to-end check that the route layer's
        ``AccountType(user_id=current_user.id, **data)`` is in fact
        the path the form post takes.  Without this assertion a
        regression that dropped ``user_id`` on insert would silently
        re-introduce a global type and bypass the multi-tenant guard
        on every subsequent rename/delete attempt.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            response = auth_client.post(
                "/accounts/types",
                data={"name": "OwnerScopedType", "category_id": asset_id},
                follow_redirects=True,
            )
            assert response.status_code == 200

            row = (
                db.session.query(AccountType)
                .filter_by(name="OwnerScopedType")
                .one()
            )
            assert row.user_id == seed_user["user"].id, (
                "create route must stamp user_id from current_user"
            )

    def test_owner_b_cannot_rename_owner_a_custom_type(
        self, app, db, seed_user, second_user,
    ):
        """A cross-owner rename returns 404, identical to a missing row.

        Owner A creates a custom type; Owner B (logged in via a
        fresh test_client) attempts to rename it via the route.
        The 404 response is identical to attempting to rename a
        non-existent type so Owner B cannot use the response to
        discover the existence of Owner A's catalogue.
        """
        with app.app_context():
            owner_a_type = AccountType(
                name="A_CustomType",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(owner_a_type)
            db.session.commit()
            type_id = owner_a_type.id

        owner_b_client = _login_as(app, "other@shekel.local", "otherpass")

        # Owner B attempts the rename.
        response = owner_b_client.post(
            f"/accounts/types/{type_id}",
            data={"name": "Hijacked"},
            follow_redirects=True,
        )

        assert response.status_code == 404
        # Same response shape as a non-existent ID -- no leak.
        ghost_response = owner_b_client.post(
            "/accounts/types/9999999",
            data={"name": "Hijacked"},
            follow_redirects=True,
        )
        assert ghost_response.status_code == 404

        # The original row is unchanged.
        with app.app_context():
            unchanged = db.session.get(AccountType, type_id)
            assert unchanged.name == "A_CustomType"
            assert unchanged.user_id == seed_user["user"].id

    def test_owner_b_cannot_delete_owner_a_custom_type(
        self, app, db, seed_user, second_user,
    ):
        """A cross-owner delete returns the same flash as a missing row.

        The companion to the rename test: confirms the ownership
        guard fires on the delete path too.  Without the guard
        Owner B could enumerate Owner A's IDs by repeated deletes
        and watching for the type-in-use vs not-found responses.
        """
        with app.app_context():
            owner_a_type = AccountType(
                name="A_DeleteTarget",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(owner_a_type)
            db.session.commit()
            type_id = owner_a_type.id

        owner_b_client = _login_as(app, "other@shekel.local", "otherpass")

        response = owner_b_client.post(
            f"/accounts/types/{type_id}/delete",
            follow_redirects=True,
        )

        assert response.status_code == 404

        with app.app_context():
            assert db.session.get(AccountType, type_id) is not None

    def test_owner_cannot_rename_seeded_builtin(
        self, app, auth_client, seed_user,
    ):
        """A seeded built-in (``user_id IS NULL``) is read-only.

        The route's ownership guard treats the seed-time NULL the
        same as another user's ID: ``account_type.user_id !=
        current_user.id`` is True for both.  The route returns 404
        and the row's name does not change.
        """
        with app.app_context():
            checking = (
                db.session.query(AccountType)
                .filter_by(name="Checking", user_id=None)
                .one()
            )

            response = auth_client.post(
                f"/accounts/types/{checking.id}",
                data={"name": "RenamedSeed"},
                follow_redirects=True,
            )

            assert response.status_code == 404

            db.session.expire(checking)
            db.session.refresh(checking)
            assert checking.name == "Checking"
            assert checking.user_id is None

    def test_owner_cannot_delete_seeded_builtin(
        self, app, auth_client, seed_user,
    ):
        """A seeded built-in cannot be deleted through the route.

        Mirrors the rename test for the delete path.  The seeded
        catalogue must remain stable so the ``ref_cache`` enum-to-id
        contract holds across application restarts; allowing owners
        to delete a built-in would silently break every consumer
        that resolves ``AcctTypeEnum.CHECKING``.
        """
        with app.app_context():
            checking = (
                db.session.query(AccountType)
                .filter_by(name="Checking", user_id=None)
                .one()
            )

            response = auth_client.post(
                f"/accounts/types/{checking.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 404

            assert db.session.get(AccountType, checking.id) is not None

    def test_two_owners_can_share_custom_name(
        self, app, db, seed_user, second_user,
    ):
        """Owner A and Owner B may each carry a custom "Crypto".

        The two custom rows are distinct (different ``id`` and
        ``user_id``); each owner sees only their own.  This is the
        core multi-tenant promise the partial unique index
        ``uq_account_types_user_name`` (``UNIQUE (user_id, name)
        WHERE user_id IS NOT NULL``) enforces -- the legacy global
        UNIQUE on ``name`` would have rejected the second row.

        Owner A's row is created through the ORM directly while
        Owner B's row goes through the route; only one fresh client
        is logged in to side-step the double-login fixture quirk
        documented at the top of this class.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            # Owner A's row -- direct ORM insert.
            a_row = AccountType(
                name="Crypto",
                category_id=asset_id,
                user_id=seed_user["user"].id,
            )
            db.session.add(a_row)
            db.session.commit()

        owner_b_client = _login_as(app, "other@shekel.local", "otherpass")

        # Owner B creates "Crypto" through the route -- distinct row.
        resp_b = owner_b_client.post(
            "/accounts/types",
            data={"name": "Crypto", "category_id": asset_id},
            follow_redirects=True,
        )
        assert resp_b.status_code == 200
        assert b"created" in resp_b.data

        with app.app_context():
            rows = (
                db.session.query(AccountType)
                .filter_by(name="Crypto")
                .order_by(AccountType.id)
                .all()
            )
            owners = {r.user_id for r in rows}
            assert owners == {
                seed_user["user"].id, second_user["user"].id,
            }
            assert len(rows) == 2

    def test_owner_can_create_per_user_copy_of_seed_name(
        self, app, auth_client, seed_user,
    ):
        """An owner may create a custom type whose name shadows a built-in.

        Per the C-28 acceptance criteria.  The two rows coexist:
        ``Checking`` with ``user_id IS NULL`` (built-in) and
        ``Checking`` with ``user_id = seed_user.id`` (custom).
        The seeded-name partial index restricts only the ``user_id
        IS NULL`` namespace; the user-name partial index restricts
        only the ``user_id IS NOT NULL`` namespace; the predicates
        are disjoint so both rows pass.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)

        response = auth_client.post(
            "/accounts/types",
            data={"name": "Checking", "category_id": asset_id},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"created" in response.data

        with app.app_context():
            rows = (
                db.session.query(AccountType)
                .filter_by(name="Checking")
                .order_by(AccountType.user_id.asc().nullsfirst())
                .all()
            )
            assert len(rows) == 2
            seeded, custom = rows
            assert seeded.user_id is None
            assert custom.user_id == seed_user["user"].id

    def test_settings_listing_excludes_other_owners_custom_types(
        self, app, db, seed_user, second_user,
    ):
        """The settings page shows seeded + own; other owners' types are hidden.

        Owner B's "B_Secret" type is inserted via the ORM (same
        rationale as ``test_two_owners_can_share_custom_name``);
        Owner A logs in via a fresh client and the page body must
        not contain the string "B_Secret".  Owner A's own page
        still includes every seeded built-in (sanity check that
        the filter is OR, not AND).
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            secret = AccountType(
                name="B_Secret",
                category_id=asset_id,
                user_id=second_user["user"].id,
            )
            db.session.add(secret)
            db.session.commit()

        owner_a_client = _login_as(app, "test@shekel.local", "testpass")

        # Owner A loads settings -- must not see "B_Secret".
        resp_a = owner_a_client.get("/settings?section=account-types")
        assert resp_a.status_code == 200
        body = resp_a.data.decode()
        assert "B_Secret" not in body
        # Sanity: built-ins are still present for Owner A.
        assert "Checking" in body

    def test_settings_listing_includes_own_custom_types(
        self, app, auth_client, seed_user,
    ):
        """Owners see their own custom types alongside the seeded built-ins."""
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            owned = AccountType(
                name="OwnVisibleType",
                category_id=asset_id,
                user_id=seed_user["user"].id,
            )
            db.session.add(owned)
            db.session.commit()

        resp = auth_client.get("/settings?section=account-types")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "OwnVisibleType" in body
        # A built-in is still rendered.
        assert "Checking" in body

    def test_account_form_dropdown_excludes_other_owners_types(
        self, app, db, seed_user, second_user,
    ):
        """The /accounts/new dropdown shows seeded + own only.

        A leak in the dropdown would let Owner A select Owner B's
        custom type by name, and a successful POST would create
        a cross-owner FK -- exactly the IDOR the route-layer
        ``_account_type_is_visible`` guard is meant to close.
        Owner B's type is inserted via the ORM to avoid the
        double-login fixture quirk.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            trap = AccountType(
                name="B_DropdownTrap",
                category_id=asset_id,
                user_id=second_user["user"].id,
            )
            db.session.add(trap)
            db.session.commit()

        owner_a_client = _login_as(app, "test@shekel.local", "testpass")
        resp = owner_a_client.get("/accounts/new")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "B_DropdownTrap" not in body
        # Sanity: built-ins remain available.
        assert "Checking" in body

    def test_create_account_with_other_owner_type_id_rejected(
        self, app, db, seed_user, second_user,
    ):
        """A forged ``account_type_id`` referencing another owner's type is rejected.

        Closes the IDOR that C-28 itself opens.  The dropdown
        already excludes the foreign type, but a hand-crafted POST
        that passes the FK by ID must also fail.  The response is
        an "Invalid account type." flash on the new-account form
        and no row is inserted into ``budget.accounts``.  Owner B's
        type is inserted via the ORM to side-step the double-login
        fixture quirk.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            foreign_type = AccountType(
                name="B_OnlyMine",
                category_id=asset_id,
                user_id=second_user["user"].id,
            )
            db.session.add(foreign_type)
            db.session.commit()
            foreign_id = foreign_type.id

            before_count = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            )

        owner_a_client = _login_as(app, "test@shekel.local", "testpass")
        response = owner_a_client.post(
            "/accounts",
            data={
                "name": "ForgedAccount",
                "account_type_id": foreign_id,
                "anchor_balance": "0",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Invalid account type." in response.data

        with app.app_context():
            after_count = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            )
            assert after_count == before_count, (
                "no account row should have been created on rejected post"
            )
            # And no account row anywhere references the foreign type
            # under Owner A.
            forged = (
                db.session.query(Account)
                .filter_by(
                    user_id=seed_user["user"].id,
                    account_type_id=foreign_id,
                )
                .first()
            )
            assert forged is None

    def test_update_account_with_other_owner_type_id_rejected(
        self, app, db, seed_user, second_user,
    ):
        """An update that re-parents to another owner's type is rejected.

        Mirror of the create test for the update path.  Without the
        ``_account_type_is_visible`` guard a malicious POST against
        ``/accounts/<id>`` could change ``account_type_id`` to
        another owner's type and bypass the dropdown filter entirely.
        Owner B's type is inserted via the ORM to side-step the
        double-login fixture quirk.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            foreign_type = AccountType(
                name="B_NotOurs",
                category_id=asset_id,
                user_id=second_user["user"].id,
            )
            db.session.add(foreign_type)
            db.session.commit()
            foreign_id = foreign_type.id
            account_id = seed_user["account"].id
            original_type_id = seed_user["account"].account_type_id
            account_name = seed_user["account"].name

        owner_a_client = _login_as(app, "test@shekel.local", "testpass")
        response = owner_a_client.post(
            f"/accounts/{account_id}",
            data={
                "name": account_name,
                "account_type_id": foreign_id,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Invalid account type." in response.data

        with app.app_context():
            account = db.session.get(Account, account_id)
            assert account.account_type_id == original_type_id, (
                "account_type_id must remain pinned to the original type"
            )

    def test_audit_trigger_logs_account_type_mutations(
        self, app, auth_client, seed_user,
    ):
        """Mutations on ``ref.account_types`` land in ``system.audit_log``.

        Commit C-28 added ``("ref", "account_types")`` to
        ``AUDITED_TABLES``.  This test fires an INSERT through the
        route, then an UPDATE, then a DELETE, and asserts each
        operation produced a matching row in the forensic table
        with the calling user's ID populated -- closing the
        forensic-trail gap that pre-C-28 left for owner-driven
        type churn.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            user_id = seed_user["user"].id

            before_count = db.session.execute(text(
                "SELECT count(*) FROM system.audit_log "
                "WHERE table_schema = 'ref' AND table_name = 'account_types'"
            )).scalar()

        # INSERT
        resp_create = auth_client.post(
            "/accounts/types",
            data={"name": "AuditedType", "category_id": asset_id},
            follow_redirects=True,
        )
        assert resp_create.status_code == 200

        with app.app_context():
            row = (
                db.session.query(AccountType)
                .filter_by(name="AuditedType", user_id=user_id)
                .one()
            )
            type_id = row.id

        # UPDATE (rename)
        resp_update = auth_client.post(
            f"/accounts/types/{type_id}",
            data={"name": "AuditedTypeRenamed"},
            follow_redirects=True,
        )
        assert resp_update.status_code == 200

        # DELETE
        resp_delete = auth_client.post(
            f"/accounts/types/{type_id}/delete",
            follow_redirects=True,
        )
        assert resp_delete.status_code == 200

        with app.app_context():
            rows = db.session.execute(text(
                "SELECT operation, user_id "
                "FROM system.audit_log "
                "WHERE table_schema = 'ref' AND table_name = 'account_types' "
                "  AND row_id = :row_id "
                "ORDER BY id"
            ), {"row_id": type_id}).fetchall()

            ops = [r[0] for r in rows]
            assert "INSERT" in ops
            assert "UPDATE" in ops
            assert "DELETE" in ops

            for op_name, audit_user in rows:
                assert audit_user == user_id, (
                    f"{op_name} audit row missing user_id "
                    f"(expected {user_id}, got {audit_user})"
                )

            after_count = db.session.execute(text(
                "SELECT count(*) FROM system.audit_log "
                "WHERE table_schema = 'ref' AND table_name = 'account_types'"
            )).scalar()
            assert after_count >= before_count + 3, (
                "expected at least three new audit rows "
                f"(insert + update + delete), gained {after_count - before_count}"
            )

    def test_legacy_global_unique_replaced_by_partial_indexes(
        self, app, db,
    ):
        """The migration-time partial unique indexes are present and active.

        Storage-tier sanity check that complements the route-tier
        tests above.  An INSERT of a duplicate per-user row raises
        IntegrityError naming ``uq_account_types_user_name``; an
        INSERT of a duplicate seeded row raises IntegrityError
        naming ``uq_account_types_seeded_name``.  The legacy
        ``account_types_name_key`` UNIQUE constraint must NOT be
        present, otherwise per-user copies of seeded names would
        be rejected at insert time.
        """
        with app.app_context():
            indexes = {
                row[0]
                for row in db.session.execute(text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'ref' AND tablename = 'account_types'"
                ))
            }
            assert "uq_account_types_seeded_name" in indexes
            assert "uq_account_types_user_name" in indexes
            assert "ix_account_types_user_id" in indexes

            unique_constraints = {
                row[0]
                for row in db.session.execute(text(
                    "SELECT constraint_name "
                    "FROM information_schema.table_constraints "
                    "WHERE table_schema = 'ref' "
                    "  AND table_name = 'account_types' "
                    "  AND constraint_type = 'UNIQUE'"
                ))
            }
            # The legacy global UNIQUE(name) must be gone -- otherwise
            # the per-user-copy contract would be impossible.
            assert "account_types_name_key" not in unique_constraints

    def test_seeded_partial_index_blocks_duplicate_seed(
        self, app, db,
    ):
        """Two seeded rows with the same name violate the seeded partial index.

        Defensive coverage for the seed script: a future change that
        accidentally inserts a duplicate seed row (no user_id) must
        be caught by the storage tier rather than producing a
        silently-corrupt cache where ``ref_cache`` resolves an enum
        member to one ID on one boot and a different ID on the next.
        """
        with app.app_context():
            asset_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            duplicate = AccountType(
                name="Checking",  # Already seeded with user_id IS NULL
                category_id=asset_cat_id,
                user_id=None,
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_user_partial_index_blocks_same_user_duplicate(
        self, app, db, seed_user,
    ):
        """Two custom rows with the same (user_id, name) violate the user partial index.

        Defensive coverage for the route-layer per-user duplicate
        check.  If the route's pre-flight is bypassed (concurrent
        request, future code change) the partial unique index is
        the last line of defence and surfaces an IntegrityError on
        flush instead of silently committing two rows.
        """
        with app.app_context():
            asset_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            first = AccountType(
                name="DupName",
                category_id=asset_cat_id,
                user_id=seed_user["user"].id,
            )
            second = AccountType(
                name="DupName",
                category_id=asset_cat_id,
                user_id=seed_user["user"].id,
            )
            db.session.add_all([first, second])
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_user_partial_index_allows_cross_user_duplicate(
        self, app, db, seed_user, seed_second_user,
    ):
        """Two custom rows with the same name but different user_id coexist.

        Direct ORM insert path -- bypasses the route to assert the
        storage tier is the correct shape.  Two owners must each be
        able to carry a custom type called "Shared".
        """
        with app.app_context():
            asset_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            for_a = AccountType(
                name="Shared",
                category_id=asset_cat_id,
                user_id=seed_user["user"].id,
            )
            for_b = AccountType(
                name="Shared",
                category_id=asset_cat_id,
                user_id=seed_second_user["user"].id,
            )
            db.session.add_all([for_a, for_b])
            db.session.flush()  # No IntegrityError.
            db.session.commit()

            rows = (
                db.session.query(AccountType)
                .filter_by(name="Shared")
                .order_by(AccountType.user_id)
                .all()
            )
            assert len(rows) == 2
            assert {r.user_id for r in rows} == {
                seed_user["user"].id, seed_second_user["user"].id,
            }

    def test_audit_table_is_registered(self):
        """``ref.account_types`` is in ``AUDITED_TABLES``.

        Registry-level check: pre-C-28 the table was excluded on the
        "ref schema is read-only" rationale; post-C-28 the rule's
        premise no longer holds for this specific table and the
        registry must reflect that so the entrypoint trigger-count
        health check refuses to start a deployment whose triggers
        do not match.
        """
        from app.audit_infrastructure import AUDITED_TABLES  # pylint: disable=import-outside-toplevel
        assert ("ref", "account_types") in AUDITED_TABLES


class TestAccountsBlueprintReExport:
    """C1-2: pin the F-25 ``accounts_bp`` re-export contract.

    Post-F-25 the blueprint declaration lives in
    :mod:`app.routes.accounts._bp` so the package <-> submodule
    import round-trip no longer trips pylint's ``R0401`` cyclic-
    import detector.  ``app/__init__.py`` and any other consumer
    that historically did ``from app.routes.accounts import
    accounts_bp`` continues to resolve because the package init
    re-exports the symbol.  A future cleanup that drops the
    re-export would silently break the factory-time blueprint
    registration; this test pins the contract so the regression
    surfaces at the unit-test layer rather than at app boot.
    """

    def test_package_reexports_same_blueprint_instance(self):
        """The package-level ``accounts_bp`` is the leaf-module instance.

        ``is`` rather than ``==`` -- a copy of the blueprint would
        register a parallel set of routes against the app and the
        URL surface would silently diverge.
        """
        from app.routes.accounts import accounts_bp as package_bp  # pylint: disable=import-outside-toplevel
        from app.routes.accounts._bp import accounts_bp as leaf_bp  # pylint: disable=import-outside-toplevel
        assert package_bp is leaf_bp


class TestAnchorKindGate:
    """The D4 / A1 amortizing-kind gate on every cash-anchor write door.

    Finding B-15: ``PATCH /accounts/<id>/true-up`` (and the full-form
    edit) wrote ``accounts.current_anchor_balance`` for an AMORTIZING
    loan -- a second, stored, never-reconciled loan balance, which the
    grid then rendered (the real Mortgage's column was set to $1.00
    with an HTTP 200).  A loan's balance is ledger-derived and is
    asserted through the loan page's own true-up; every cash-anchor
    door now refuses the kind.
    """

    @staticmethod
    def _loan(seed_user):
        """Create a fully configured loan through the shared builder."""
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        return create_loan_account(
            seed_user, db.session, name="Gate Test Loan",
            principal=Decimal("10000.00"), rate=Decimal("0.05000"),
        )

    def test_true_up_refuses_amortizing_loan(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH true-up on a loan: 422, and NOTHING is written.

        Asserts all three no-write invariants: the cash anchor column is
        unchanged, no ``AccountAnchorHistory`` row was appended, and no
        ``LoanAnchorEvent`` was created (the refusal must not leak into
        the loan's REAL true-up path either).
        """
        from app.models.loan_anchor_event import LoanAnchorEvent  # pylint: disable=import-outside-toplevel

        with app.app_context():
            loan = self._loan(seed_user)
            column_before = cash_ledger.resolve_anchor(loan).balance
            history_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            )
            events_before = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=loan.id).count()
            )

            response = auth_client.patch(
                f"/accounts/{loan.id}/true-up",
                data={"anchor_balance": "1.00"},
            )

            assert response.status_code == 422
            assert b"not a cash anchor" in response.data

            refreshed = db.session.get(Account, loan.id)
            assert cash_ledger.resolve_anchor(refreshed).balance == column_before
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            ) == history_before
            assert (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=loan.id).count()
            ) == events_before

    def test_the_kind_refusal_is_a_fragment_the_submitting_form_can_render(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The kind refusal is DESIGNED, so the user actually sees it.

        **Plan step X-f1e3, finding N-199.**  This arm answered a raw string
        body, and ``base.html``'s htmx config leaves 4xx non-swapping, so the
        refusal rendered NOTHING and the form simply sat there -- the exact
        defect X-f1c4c converted the door's other arm to prevent.

        It was left raw on the argument that the arm answers a forged request:
        ``anchor_form`` refuses to OPEN the editor for a loan, so no user could
        reach the PATCH.  **That argument is false, and the test below proves
        the path.**  An account's kind is editable, and a boundary-crossing
        re-type is permitted while the account carries no ledger postings, so a
        form opened on a cash account can be submitted after that account has
        become a loan.

        Graded on the MARKER HEADER, not on the status: the header is what the
        global ``htmx:beforeSwap`` listener reads to swap a 4xx at all, so a
        fragment without it is invisible however well it renders.  The 422 is
        asserted too -- a designed fragment does not need a 2xx, and flattening
        this onto the input-shaped 400 would tell a non-htmx client the payload
        was malformed when it was the entity that could not be processed.
        """
        with app.app_context():
            loan = self._loan(seed_user)

            response = auth_client.patch(
                f"/accounts/{loan.id}/true-up",
                data={"anchor_balance": "1.00"},
            )

            assert response.status_code == 422
            assert response.headers.get("Shekel-Designed-Fragment") == "1", (
                "without the marker header htmx leaves this 4xx non-swapping, "
                "so the refusal is invisible and the form sits there"
            )
            body = response.data.decode()
            assert "not a cash anchor" in body
            assert 'role="alert"' in body
            # **It renders the read-only DISPLAY cell, not the editor.**  An
            # adversarial review caught the first version of this fix
            # re-rendering the editor: a live input and a Save button whose
            # PATCH is guaranteed to be refused again -- the dead-end
            # affordance this module's own ``anchor_form`` docstring forbids,
            # twelve lines below the arm that was offering it.  There is
            # nothing here to resubmit, so the response must not look like
            # there is.
            assert 'name="anchor_balance"' not in body, (
                "a refusal that can never be satisfied must not re-offer the "
                "form that cannot satisfy it"
            )
            assert 'id="anchor-display"' not in body
            assert "hx-get" not in body

    def test_a_cash_account_can_become_a_loan_under_an_open_editor(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The kind refusal's user path exists -- it is not a forged request.

        **The measurement behind the docstring correction in
        ``_true_up_request_gates``** (finding N-199).  This test does not
        assert on the refusal; it asserts the STATE the refusal answers is
        reachable through ordinary routes, which is what makes a designed
        fragment worth rendering.

        The sequence is two browser tabs: open the anchor editor on a cash
        account, re-type that account to an amortizing kind, then submit the
        still-open form.  The re-type is permitted here because the account
        carries no ledger postings -- a ``$0.00`` opening emits no legs, so
        ``_validate_account_type_change``'s posting guard does not bite.
        """
        with app.app_context():
            # A SAVINGS account whose opening asserts $0.00: a zero delta
            # emits no legs, so its ledger is empty and the posting guard in
            # ``_validate_account_type_change`` does not bite.  Built through
            # the service, the shape ``TestTypeChangeBoundaryGuard`` uses.
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings", user_id=None).one()
            )
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Opened At Zero",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.commit()
            account_id = account.id

            # Tab A opens the editor: the account is cash, so it opens.
            assert auth_client.get(
                f"/accounts/{account_id}/anchor-form",
            ).status_code == 200

            # Tab B re-types it across the amortizing boundary.
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="Mortgage", user_id=None).one()
            )
            assert mortgage_type.has_amortization, (
                "the fixture type is not amortizing, so this test would not "
                "cross the boundary it exists to cross"
            )
            auth_client.post(
                f"/accounts/{account_id}",
                data={"account_type_id": str(mortgage_type.id)},
                follow_redirects=True,
            )
            db.session.expire_all()
            assert db.session.get(
                Account, account_id,
            ).account_type.has_amortization, (
                "the re-type was refused, so this test is no longer proving "
                "the kind refusal is reachable -- re-check "
                "_validate_account_type_change's posting guard"
            )

            # Tab A submits the form it opened while the account was cash.
            submitted = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1.00"},
            )
            assert submitted.status_code == 422
            assert submitted.headers.get("Shekel-Designed-Fragment") == "1"

    def test_anchor_form_refuses_amortizing_loan(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET anchor-form for a loan: 422 -- the editor never even opens.

        Designed since plan step X-f1e3, for the same reason as its PATCH
        twin: a raw 4xx is left non-swapping by ``base.html``, so the click
        produced NOTHING at all -- and unlike the PATCH there was not even a
        form sitting there to explain it.
        """
        with app.app_context():
            loan = self._loan(seed_user)

            response = auth_client.get(f"/accounts/{loan.id}/anchor-form")

            assert response.status_code == 422
            assert b"not a cash anchor" in response.data
            assert response.headers.get("Shekel-Designed-Fragment") == "1"

    def test_a_loan_balance_cell_offers_no_click_to_edit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The refused affordance is DELETED, not decorated with a refusal.

        **Plan step X-f1e3's root fix for finding N-199's second half.**  Both
        anchor doors refuse an amortizing account, and the shared display
        partial offered the click anyway on every surface that includes it, so
        an ordinary click reached a refusal the page could not render.  A
        control that cannot succeed is not offered: the cell renders read-only,
        which is the rule ``savings/_cockpit_balance.html`` already followed
        and the other four surfaces did not.

        Graded on the ABSENCE of the opener, not on the presence of the
        figure: a mutant that kept ``hx-get`` while adding a title attribute
        would still be a dead click.
        """
        with app.app_context():
            loan = self._loan(seed_user)

            body = auth_client.get(
                f"/accounts/{loan.id}/anchor-display",
            ).data.decode()

            assert "anchor-balance-display" in body, (
                "the loan's balance must still be SHOWN -- this step removes "
                "the edit affordance, not the figure"
            )
            assert 'id="anchor-display"' not in body
            assert "hx-get" not in body
            assert "data-keyboard-activate" not in body
            assert 'role="button"' not in body

    def test_a_cash_balance_cell_still_offers_click_to_edit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control for the read-only branch, on a CASH account.

        Without this, deleting the affordance for EVERY account would pass the
        test above -- and would silently remove the one-click true-up habit
        this whole arc is built around.
        """
        with app.app_context():
            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/anchor-display",
            ).data.decode()

            assert 'id="anchor-display"' in body
            assert "hx-get" in body
            assert 'role="button"' in body

    def test_update_form_cannot_assert_a_loan_balance_at_all(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan anchor through the edit door is unreachable, not refused.

        This door used to need its OWN copy of the amortizing-kind gate: it
        reached ``stage_anchor_true_up`` without passing
        ``apply_anchor_true_up``'s :class:`AmortizingAccountAnchorError`, so
        without the copy a forged POST could write a cash assertion onto a loan
        -- finding B-15, where the real Mortgage's balance was set to $1.00 with
        an HTTP 200 while the ledger said $177,277.97.

        Plan step X-f1e deleted the door, so the gate is not needed here and was
        deleted with it.  The predecessor of this test asserted the REFUSAL
        ("not a cash anchor" in the response); refusing is now the wrong thing
        to assert, because there is nothing to refuse -- the schema discards the
        field.

        **What separates the two worlds is whether the REST of the edit
        applied**, and that is the assertion this test turns on.  "200, balance
        unmoved, nothing appended" was true under the old gate as well: it
        flashed and redirected, which ``follow_redirects`` renders as a 200, and
        it wrote nothing -- including the rename the user also asked for.  So
        the rename is CHANGED here and asserted: the old door threw the whole
        edit away, the new one ignores one field and applies the rest.  Without
        that line this test passes against a revert, which a neutral review
        measured before it was added.
        """
        with app.app_context():
            loan = self._loan(seed_user)
            loan_id = loan.id
            balance_before = cash_ledger.resolve_anchor(loan).balance
            assertions_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan_id).count()
            )

            response = auth_client.post(f"/accounts/{loan_id}", data={
                "name": "Renamed Gate Loan Two",
                "account_type_id": loan.account_type_id,
                "anchor_balance": "1.00",
            }, follow_redirects=True)

            assert response.status_code == 200
            db.session.expire_all()
            refreshed = db.session.get(Account, loan_id)
            # The loan's balance is untouched -- finding B-15's $1.00.
            assert cash_ledger.resolve_anchor(refreshed).balance == balance_before
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan_id).count()
            ) == assertions_before
            # ...and the edit this door DOES own still landed, which the old
            # gate's flash-and-redirect refusal would have discarded.
            assert refreshed.name == "Renamed Gate Loan Two"

    def test_update_form_allows_loan_edit_with_unchanged_anchor(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan rename with the form's unchanged anchor echo succeeds.

        The edit form used to round-trip the asserted balance on every submit,
        and an unchanged echo is not an assertion, so gating it would have
        broken every ordinary loan edit.  Plan step X-f1e removed the field, so
        a stale client still sending it must not break the edit either -- which
        is what this now pins.
        """
        with app.app_context():
            loan = self._loan(seed_user)

            response = auth_client.post(f"/accounts/{loan.id}", data={
                "name": "Renamed Gate Loan",
                "account_type_id": loan.account_type_id,
                "anchor_balance": str(cash_ledger.resolve_anchor(loan).balance),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"not a cash anchor" not in response.data

            refreshed = db.session.get(Account, loan.id)
            assert refreshed.name == "Renamed Gate Loan"


class TestAnchorDifference:
    """The true-up form shows the difference BEFORE it is saved.

    Plan step **X-f2-a**, ruling **R-EU** -- R-DH (f)'s second half.  The
    figure's own contract is pinned in
    ``tests/test_services/test_balance_at.py::TestRecordsBalanceAt``; these
    cases grade the WIRING: that the editor mounts the region without previewing
    its own prefill, that the two boxes reach the producer by the write door's
    parsing rules, and that a state the save would refuse is refused here.
    """

    STATEMENT_DAYS_BACK = 20

    def _statement_day(self, seed_user):
        """Return a past day inside both bounds, asserting the fixture affords it."""
        day = display_today() - timedelta(days=self.STATEMENT_DAYS_BACK)
        floor = pay_period_service.earliest_recordable_day(seed_user["user"].id)
        assert floor <= day, (
            f"the schedule starts {floor}, so {day} is not assertable; "
            "seed_periods_today no longer affords 20 days of history"
        )
        return day

    def _preview(self, auth_client, account_id, **params):
        """GET the preview region, returning its decoded body."""
        return auth_client.get(
            f"/accounts/{account_id}/anchor-difference", query_string=params,
        ).data.decode()

    def test_the_editor_mounts_the_region_without_previewing_its_prefill(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The region is present, empty, and NOT triggered on load.

        **The ``load`` trigger is the defect, not an optimisation.**  The editor
        opens prefilled with the governing balance, so a preview on open reads
        "You entered <a figure the user did not enter>" and captions the gap as
        the app's fault -- over records that may be perfectly complete.  Pressing
        Enter on that prefill then asserts the stale balance.  Asserting the
        ABSENCE of the trigger is the only way this stays fixed: the feature
        works either way, and only this control says which way is correct.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = auth_client.get(
                f"/accounts/{acct_id}/anchor-form"
            ).data.decode()

            assert f'id="anchor-difference-{acct_id}"' in html
            assert f'/accounts/{acct_id}/anchor-difference' in html
            assert 'hx-include="closest form"' in html
            trigger = re.search(
                r'id="anchor-difference-\d+"[^>]*hx-trigger="([^"]*)"', html,
            )
            assert trigger is not None, "the region carries no hx-trigger"
            spec = trigger.group(1)
            assert "load" not in spec
            # ONE trigger spec, measured in Chromium against the repo's own
            # vendored htmx: a ``keyup`` + ``change`` PAIR costs a second
            # identical fold on blur, because htmx tracks ``changed`` per spec
            # and the ``change`` arm sees the typed value for the first time.
            assert "," not in spec, f"more than one trigger spec: {spec!r}"

    def test_the_region_targets_itself_rather_than_inheriting_the_forms(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The preview swaps into ITSELF, never into the form containing it.

        **``hx-target`` is an INHERITED htmx attribute, and this region sits
        inside the true-up form, which declares ``hx-target="this"`` for its own
        PATCH.**  htmx resolves an inherited ``"this"`` to the element that
        DECLARED the attribute, never to the element issuing the request
        (``getTarget`` -> ``closest(elt, '[hx-target]')``).  So a region with no
        target of its own lands its ``innerHTML`` response on the FORM: the
        first preview replaces the balance box, the date box and both buttons
        with the difference table, mid-keystroke.

        Reproduced in a real browser against production data before this test
        existed.  Typing ``2193.69`` with a pause after ``21`` fired the preview,
        the editor ceased to exist, and the htmx trace read
        ``elt=div#anchor-difference-1 target=form.d-inline-block`` followed by
        ``htmx:beforeCleanupElement`` on the balance input and the Save button.
        No balance could be entered and none was ever recorded -- the account
        sat at a stale anchor while every projection flowed forward from it.

        **Asserting the attribute is the only thing that keeps this fixed.**  The
        route, the fragment and the trigger were all correct; a response body
        cannot show where it will be swapped, so every route test here passed
        with the editor being destroyed on each preview.  The sibling test above
        pins the trigger for the same reason -- both are attributes no assertion
        over the rendered figures can see.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = auth_client.get(
                f"/accounts/{acct_id}/anchor-form"
            ).data.decode()

            region = re.search(
                rf'<div id="anchor-difference-{acct_id}"[^>]*>', html,
            )
            assert region is not None, "the preview region is not rendered"
            target = re.search(r'hx-target="([^"]*)"', region.group(0))
            assert target is not None, (
                "the preview region declares no hx-target, so it inherits the "
                "form's and swaps over the editor"
            )
            # "this" resolves to the declaring element -- now the region itself;
            # its own id is the equally correct spelling.  Anything else names
            # an element outside the region.
            assert target.group(1) in ("this", f"#anchor-difference-{acct_id}"), (
                f"the preview targets {target.group(1)!r}, not itself"
            )

    def test_it_renders_records_entered_and_difference(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """All three figures render, against the RECORDS.

        The entered balance is placed a known distance below the records', so a
        response that echoed one figure twice, or subtracted the other way,
        could not pass.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            day = self._statement_day(seed_user)
            records = balance_at.records_balance_at(
                account, balance_at.BalanceContext.build(user_id), day,
            )
            entered = records - Decimal("48.25")

            html = self._preview(
                auth_client, account.id,
                anchor_balance=str(entered), observed_on=day.isoformat(),
            )

            assert "Your records" in html
            assert f"${records:,.2f}" in html
            assert f"${entered:,.2f}" in html
            assert "-$48.25" in html
            assert day.strftime("%b %-d, %Y") in html

    def test_it_reports_the_records_gap_on_a_day_already_recorded(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-opening a recorded day compares against the RECORDS, not the entry.

        The defect that sent this leaf back: the first build compared against the
        account's current balance, which an assertion RESETS -- so on a
        correction it reported the gap between the user's two successive guesses,
        with a sign that can oppose the real one.  Both figures are hand-computed
        here so a regression to the reset value fails rather than merely
        differing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            day = self._statement_day(seed_user)
            records = balance_at.records_balance_at(
                account, balance_at.BalanceContext.build(user_id), day,
            )

            auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={
                    "anchor_balance": str(records + Decimal("625.00")),
                    "observed_on": day.isoformat(),
                },
            )
            db.session.expire_all()

            entered = records + Decimal("445.00")
            html = self._preview(
                auth_client, account.id,
                anchor_balance=str(entered), observed_on=day.isoformat(),
            )

            assert f"${records:,.2f}" in html
            assert "+$445.00" not in html  # the macro renders a bare figure
            assert "$445.00" in html
            # The reset value must be absent: it is what the broken build showed.
            assert f"${records + Decimal('625.00'):,.2f}" not in html
            # And the sign is the TRUE one -- money the records do not account
            # for -- not the "-$180.00" a comparison against the entry produces.
            assert "money Shekel has not accounted for" in html

    def test_the_three_verdicts_each_render_their_own_sentence(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Negative, zero and positive each get their own caption.

        The sign's MEANING is decided in the route (``_difference_verdict``) and
        the partial only maps it to copy -- but nothing asserted the mapping, so
        a wrong caption was invisible to the suite.  All three branches, one
        case, because the value of this control is that it covers the set.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            day = self._statement_day(seed_user)
            records = balance_at.records_balance_at(
                account, balance_at.BalanceContext.build(user_id), day,
            )

            for delta, expected in (
                (Decimal("-25.00"), "spend Shekel has not recorded"),
                (Decimal("0.00"), "your records agree with this balance"),
                (Decimal("25.00"), "money Shekel has not accounted for"),
            ):
                html = self._preview(
                    auth_client, account.id,
                    anchor_balance=str(records + delta),
                    observed_on=day.isoformat(),
                )
                assert expected in html, f"delta {delta} rendered: {html!r}"

    def test_a_modelled_account_previews_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An account carrying a modelled tier renders an EMPTY region.

        A brokerage is not being reconciled against a bank statement, and its
        recorded cash is a fraction of what its screens show -- so a difference
        drawn against it would caption a model-vs-market gap as untracked spend
        (finding **N-213**).
        """
        from tests._test_helpers import make_investment_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            user_id = seed_user["user"].id
            periods = all_periods(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            db.session.commit()

            html = self._preview(
                auth_client, inv.id, anchor_balance="10500.00",
            )

            assert html.strip() == ""

    def test_a_blank_balance_box_previews_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Clearing the balance leaves an EMPTY region, not a placeholder.

        A rendered ``$0.00`` difference is indistinguishable from "your records
        agree", so the empty state has to actually be empty.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            day = self._statement_day(seed_user)

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="", observed_on=day.isoformat(),
            )

            assert html.strip() == ""

    def test_a_blank_date_box_means_today(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An empty date box previews TODAY, the same default the save applies.

        ``resolve_observation_day`` owns "when is an assertion dated" for both
        anchor write doors (ruling R-ER); this path calls it rather than reading
        a clock of its own.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="1000.00", observed_on="",
            )

            assert display_today().strftime("%b %-d, %Y") in html

    def test_an_unparseable_day_is_refused_not_defaulted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A date box holding a non-date says so instead of previewing today.

        Blank means "now"; ``2026-13-40`` means the user is mid-edit on a day
        that is not a day, and previewing today's figure under a date box showing
        something else would caption a figure with the wrong day.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="1000.00", observed_on="2026-13-40",
            )

            assert "Enter a date" in html
            assert "Your records" not in html
            assert display_today().strftime("%b %-d, %Y") not in html
            # The schema's FIELD NAME must not reach the user: the box they see
            # is labelled "Balance as of", not "observed_on".
            assert "observed_on" not in html

    def test_a_future_day_is_refused_by_the_write_door_s_own_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A day the save would refuse previews the refusal, not a figure."""
        with app.app_context():
            acct_id = seed_user["account"].id
            tomorrow = display_today() + timedelta(days=1)

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="1000.00", observed_on=tomorrow.isoformat(),
            )

            assert "has not happened yet" in html
            assert "Your records" not in html

    def test_a_day_below_the_schedule_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The FLOOR is previewed as a refusal too, not only the ceiling."""
        with app.app_context():
            user_id = seed_user["user"].id
            acct_id = seed_user["account"].id
            below = pay_period_service.earliest_recordable_day(
                user_id,
            ) - timedelta(days=1)

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="1000.00", observed_on=below.isoformat(),
            )

            assert "recorded history starts" in html
            assert "Your records" not in html

    def test_it_refuses_an_amortizing_loan(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan previews the kind refusal, matching both anchor doors.

        Reachable as a RACE rather than an ordinary click -- an account's kind is
        editable -- and the region answers the same sentence ``anchor_form`` and
        ``true_up`` do rather than a balance nobody may assert here.
        """
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Preview Gate Loan",
                principal=Decimal("10000.00"), rate=Decimal("0.05000"),
            )
            db.session.commit()

            html = self._preview(
                auth_client, loan.id, anchor_balance="1000.00",
            )

            assert "not a cash anchor" in html
            assert "Your records" not in html

    def test_another_users_account_is_not_previewable(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The preview is an ownership-scoped read, like every other anchor route.

        It renders a real balance, so an unscoped one would be an IDOR that leaks
        another owner's figure through a route nobody thinks of as a balance
        endpoint.  404 exactly -- ``get_or_404`` collapses not-found and
        not-yours, and admitting a redirect here would let an auth regression
        pass this control.
        """
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.get(
                f"/accounts/{other['account'].id}/anchor-difference",
                query_string={"anchor_balance": "1000.00"},
            )

            assert response.status_code == 404
            assert b"Your records" not in response.data


class TestTheBalanceHistoryCard:
    """The durable record of a balance assertion -- plan step X-f2-b (R-EV).

    The card closes finding **N-205**: before it, the only evidence a
    back-dated assertion landed was an 8-second toast, and no template read
    ``AccountAnchorHistory`` at all except the GOVERNING assertion's "as of"
    caption -- which by definition is never the back-dated row.

    Every figure is graded at the producer
    (``test_services/test_balance_at.py::TestCashAnchorHistory``); what is
    graded HERE is the route's own three decisions -- which rows are shown by
    default, that the card is reachable only by its owner, and that it refreshes
    when a write appends to it.
    """

    @staticmethod
    def _assert_balance(auth_client, account_id, balance, observed_on=None):
        """Record a balance through the real PATCH route."""
        data = {"anchor_balance": balance}
        if observed_on is not None:
            data["observed_on"] = observed_on.isoformat()
        response = auth_client.patch(
            f"/accounts/{account_id}/true-up", data=data,
        )
        assert response.status_code == 200

    def test_the_page_carries_the_log_and_refreshes_it_on_a_write(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The card renders on the page and re-fetches on ``balanceChanged``.

        The refresh is not decoration: a true-up APPENDS an assertion, so a
        card left un-refreshed would omit the very row the user just recorded
        -- the defect the card exists to close, one layer up.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            self._assert_balance(auth_client, acct_id, "1234.56")

            html = auth_client.get(
                f"/accounts/{acct_id}/details",
            ).data.decode()

            assert "Balance history" in html
            assert f'id="balance-history-{acct_id}"' in html
            assert f"/accounts/{acct_id}/balance-history" in html
            region = html.split(f'id="balance-history-{acct_id}"', 1)[1]
            assert 'hx-trigger="balanceChanged from:body"' in region.split(
                "</div>", 1,
            )[0]
            assert "$1,234.56" in html

    def test_the_fragment_shows_a_write_the_page_predates(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The refresh target really does pick up a newly appended row.

        The region's trigger is asserted above; this is the other half -- that
        what it fetches has moved.  Without it a card wired to a stale producer
        would pass the markup test and still show yesterday's log forever.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            before = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()
            assert "$4,321.00" not in before

            self._assert_balance(auth_client, acct_id, "4321.00")

            after = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()
            assert "$4,321.00" in after

    def test_the_default_view_is_capped_and_says_what_it_hides(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Past twelve rows the card discloses the rest rather than growing.

        The real Checking account carries 57 assertions over 133 days and grows
        by about 13 a month, so the loan twin's "render every row" shape -- it
        has ONE anchor -- would make the log the page's dominant element
        (developer ruling 1, 2026-08-10).
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            # 14 true-ups on distinct days, plus the account's opening = 15.
            for offset in range(14, 0, -1):
                self._assert_balance(
                    auth_client, acct_id, f"{1000 + offset}.00",
                    observed_on=today - timedelta(days=offset),
                )

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            assert html.count("<tr>") == 1 + 15, (
                "every assertion is in the DOM -- the disclosure hides rows, "
                "it does not drop them"
            )
            assert "Show all 15" in html
            assert "12 of 15 recorded" in html
            # The rows past the cap are behind the collapse rather than gone.
            hidden = html.split('class="collapse"', 1)[1]
            assert hidden.count("<tr>") == 3

    def test_the_rows_shown_are_the_most_recently_RECORDED(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A back-dated write is visible without expanding the disclosure.

        The card's whole job is giving that write a retrieval path (**N-205**),
        and sorting by the day a balance was TRUE defeats it: a balance
        recorded today for a day three weeks back sits at that day's position,
        which on the real Checking account is 40 rows down.  So the default
        view is chosen by ``recorded_on`` and rendered in ``observed_on`` order.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            for offset in range(14, 0, -1):
                self._assert_balance(
                    auth_client, acct_id, f"{2000 + offset}.00",
                    observed_on=today - timedelta(days=offset),
                )
            # NOW record an old statement.  Its ``observed_on`` is older than
            # every row above, so an ``observed_on``-ranked cap would bury it.
            self._assert_balance(
                auth_client, acct_id, "1777.77",
                observed_on=today - timedelta(days=30),
            )

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()
            shown = html.split('class="collapse"', 1)[0]

            assert "$1,777.77" in shown, (
                "the row just recorded must be visible without expanding, or "
                "the card does not answer the question it exists for"
            )
            # And it announces that it is back-dated, so it is not read as a
            # balance that was true today.
            #
            # **Scoped to the row under test.**  A bare ``"entered" in shown``
            # is near-vacuous here: every row in this fixture is back-dated by
            # construction, so the caption is present whether or not THIS row
            # carries one.  Slicing to the cell that holds the figure is what
            # makes the assertion about the row it names.
            row = shown.split("$1,777.77", 1)[0].rsplit("<tr>", 1)[1]
            assert f"entered {today.strftime('%b %-d, %Y')}" in row, (
                "the back-dated row must name the day it was TYPED, or a "
                "reader cannot tell it from a balance that was true that day"
            )

    def test_an_ordinary_row_does_not_claim_to_be_back_dated(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The non-vacuity control for the caption above.

        A same-day true-up records one civil day under both names, so the
        caption must be absent -- one that fired on every row would stop
        distinguishing anything.

        **It said "leaves the two CLOCKS equal" until finding N-299**, and that
        was the defect in one sentence: the entered day was PostgreSQL's and
        the observed day the application's, so a same-day true-up left them
        equal only where the two clocks agreed.  Under the calendar sweep they
        do not, and this control was red on every matrix date from 2026-08-10.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            self._assert_balance(
                auth_client, acct_id, "1500.00", observed_on=display_today(),
            )

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            assert "$1,500.00" in html
            assert "entered" not in html

    def test_the_entered_day_is_stored_not_read_back_off_created_at(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """The caption's day is the app's, and moving ``created_at`` cannot move it.

        **The regression control for finding N-299**, and it is what makes the
        two sweep tests above non-vacuous on an ordinary clock: they only
        distinguish a stored day from a derived one on a calendar position
        where the Python and PostgreSQL clocks disagree, which is exactly the
        position the sweep runs at and the ordinary suite never reaches.

        This reaches it directly instead.  A same-day true-up is recorded
        through the real door, and then the row's ``created_at`` alone is moved
        three days -- the state the midnight race produces in production, where
        ``display_today()`` answers one civil day and PostgreSQL's ``now()``
        the next.  The caption must stay ABSENT: the day the card compares is
        stored, not read back off the instant.  A revert to the derived
        property fails here with an ``entered`` caption naming the moved day.

        ``created_at`` keeps its own job either way, and this test does not
        touch it: it orders assertions that share an ``observed_on``.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            self._assert_balance(
                auth_client, acct_id, "1500.00", observed_on=today,
            )

            row = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id)
                .order_by(AccountAnchorHistory.id.desc())
                .first()
            )
            assert row.recorded_on == today, (
                "the write door must record the APPLICATION's civil day"
            )
            # Move ONLY the recording instant, leaving the stored entered day.
            row.created_at = row.created_at + timedelta(days=3)
            db.session.commit()

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            assert "$1,500.00" in html
            assert "entered" not in html, (
                "the entered day is a stored fact; deriving it from "
                "created_at is what made the caption compare two clocks"
            )

    def test_a_modelled_account_shows_no_reconciliation_columns(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """An HYSA logs what was declared and claims no reconciliation.

        The walk sees recorded CASH only, so an accruing account's difference
        would name interest as untracked spend -- on production the HYSA's one
        row would read $4,863.56 against a $5,363.56 balance (finding N-213).
        The log itself still renders, because "what did I declare, and when" is
        a fact for every kind.
        """
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA",
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="HYSA History",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            html = auth_client.get(
                f"/accounts/{acct.id}/balance-history",
            ).data.decode()

            assert "$5,000.00" in html
            assert "Ledger" not in html
            assert "Correction" not in html
            assert "nothing here to reconcile against" in html

    def test_a_plain_account_shows_them(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The non-vacuity control for the case above.

        Same route, same fixture shape, only the KIND differs -- so the missing
        columns there are a property of the account rather than of the card
        never rendering them at all.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            self._assert_balance(auth_client, acct_id, "1500.00")

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            assert "Ledger" in html
            assert "Correction" in html

    def test_the_opening_row_publishes_no_difference(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The opening is badged and its reconciliation cells are withheld.

        Its "ledger" is the sum of rows dated before the account existed
        (open finding **N-37**) and its gap is opening EQUITY, the figure plan
        step X-f5 books -- not a correction, so the card refuses to caption it
        as one.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            assert "Opening" in html
            # The lone row is the opening, so every reconciliation cell on the
            # page is the withheld dash.
            assert html.count(">--<") == 2

    def test_a_companion_cannot_reach_the_card(
        self, app, companion_client, seed_user, seed_periods_today,
    ):
        """The owner-role decorator on the fragment is graded, not assumed.

        The COMPANION is the case that matters rather than an unrelated
        stranger, for the reason the sibling reconcile route's twin gives: a
        companion is authenticated AND holds granted access to this owner's
        transactions, so ``@require_owner`` refusing them is a real decision.
        This card publishes MORE than that panel does -- every balance the
        owner has ever recorded, with the day each was entered.

        404 rather than 403, per the project's security response rule, so the
        status cannot be used as an existence oracle.
        """
        with app.app_context():
            account_id = seed_user["account"].id

        assert companion_client.get(
            f"/accounts/{account_id}/balance-history",
        ).status_code == 404

    def test_another_users_account_is_not_reachable(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """404 for both "not found" and "not yours" (the security rule).

        The card publishes an account's whole balance history, so an
        unscoped read here would leak more than most: every figure the owner
        has ever recorded, with dates.
        """
        with app.app_context():
            other = _create_other_user_account()
            response = auth_client.get(
                f"/accounts/{other['account'].id}/balance-history",
            )
            assert response.status_code == 404
