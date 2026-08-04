"""
Shekel Budget App -- Account Route Tests

Tests for account CRUD, anchor balance true-up, and account type
management endpoints (§2.1 of the test plan).
"""

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
)
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.utils.dates import display_today
from tests._test_helpers import (
    append_balance_assertion,
    settle_instant_on,
)
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.user import User, UserSettings
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.services import (
    account_service,
    cash_ledger,
    pay_period_service,
)
from app.services.auth_service import hash_password


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
            notes="_create_other_user_account fixture",
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
        round-tripping a rejection -- and both bounds come from
        ``account_service.earliest_observable_day`` / ``display_today`` so the
        form cannot drift from the validation behind it.
        """
        with app.app_context():
            html = auth_client.get("/accounts/new").data.decode()

            assert 'name="observed_on"' in html
            assert f'value="{display_today().isoformat()}"' in html
            assert f'max="{display_today().isoformat()}"' in html
            assert (
                f'min="{seed_periods_today[0].start_date.isoformat()}"' in html
            )

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

    def test_update_account_anchor_edit_posts_trueup(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The direct anchor edit books its true-up correction (C6 point 3).

        POSTing a new ``anchor_balance`` through the account edit form runs
        the guarded reconcile: the history row lands AND the Step-5 sync
        books the true-up delta in the same transaction, so the Checking
        total moves from its $1000.00 opening to the asserted $1500.00.
        """
        # Pylint: ``import-outside-toplevel`` -- localized to the one test
        # that needs this reader, matching the file's convention.
        # pylint: disable=import-outside-toplevel
        from app.services import posting_service

        with app.app_context():
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")

            response = auth_client.post(
                f"/accounts/{checking.id}",
                data={"anchor_balance": "1500.00"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1500.00")


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
            # comment said, so ``get_current_period`` is ``None`` on every day
            # after 2026-01-18 and this is not date-fragile.
            assert pay_period_service.get_current_period(
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
        """PATCH /accounts/<id>/true-up with invalid amount returns 400 with errors JSON."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "abc"},
            )

            assert response.status_code == 400
            body = response.get_json()
            assert "errors" in body, "400 response must contain validation errors"

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

    def test_true_up_accounts_revert_skips_as_of_oob(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """With ?revert=accounts, the success response omits the as-of OOB.

        The cockpit is multi-card and has no singleton ``#anchor-as-of``
        element, and it re-syncs the whole region via ``balanceChanged``, so
        the out-of-band "as of" snippet (which would orphan-target) is
        dropped.  The ``balanceChanged`` trigger still fires.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up?revert=accounts",
                data={"anchor_balance": "3210.00"},
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"
            body = response.data.decode()
            assert 'id="anchor-as-of"' not in body
            assert 'hx-swap-oob="true"' not in body

    def test_true_up_grid_default_includes_as_of_oob(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The grid / default success response keeps the #anchor-as-of OOB.

        The single-account grid and dashboard surfaces have an
        ``#anchor-as-of`` caption to update, so the OOB snippet stays -- the
        cockpit skip must not regress them.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={"anchor_balance": "3211.00"},
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"
            body = response.data.decode()
            assert 'id="anchor-as-of"' in body
            assert 'hx-swap-oob="true"' in body


class TestTrueUpSameDayDuplicate:
    """F-103 / C-22: same-day same-balance double-submit dedupe."""

    def test_double_submit_creates_one_history_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two identical true-ups same day produce exactly one history row.

        F-103 / C-22: the partial unique expression index
        ``uq_anchor_history_account_period_balance_day`` rejects the
        second INSERT when the user clicks Save twice in a row.
        The route catches the IntegrityError and returns the
        already-current balance so the user sees idempotent success
        instead of a 500.
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
            # origination + one true-up), not 1.  The F-103 dedupe
            # claim is that the true-up balance ($1234.56) only
            # appears once -- the second submit is suppressed.
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
                "F-103 dedupe failed."
            )

    def test_same_day_different_balance_creates_two_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Same-day true-ups with different balances both succeed.

        F-103 / C-22: the unique constraint includes
        ``anchor_balance``, so a legitimate same-day correction (the
        user noticed an error and re-trued at a different amount)
        must NOT be blocked.
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
            # both distinct true-ups produced their own audit row
            # (which is what F-103 actually asserts).  The fixture's
            # origination row is at the same $1000 balance as r1,
            # which is correctly suppressed by the unique index --
            # only one $1000 row survives, plus the new $1100 row.
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
    the service in ``test_services/test_entry_service.py``; what is graded here
    is the ROUTE -- that it resolves the assertion day itself, refuses a
    non-owner, and commits.
    """

    def _make_grocery_txn_with_entries(
        self, seed_user, seed_periods_today, entries, account=None,
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
            name="Groceries",
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
            name="Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("500.00"),
        )
        db.session.add(txn)
        db.session.flush()

        for amount, purchased_on, is_credit, settled_on in entries:
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal(amount),
                description="Test purchase",
                purchased_on=purchased_on,
                is_credit=is_credit,
                settled_on=settled_on,
            ))
        db.session.commit()
        return txn

    @staticmethod
    def _true_up(auth_client, account_id, balance):
        """Assert *balance* for today through the real PATCH route.

        The reconcile route reads the account's LATEST asserted day and stamps
        it, so the fixtures true up first rather than hand-writing a history
        row: that is the production sequence (read your bank balance, enter it,
        then tick off what it contained) and it is the sequence whose ORDER the
        retired flag got wrong.
        """
        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={"anchor_balance": balance},
        )
        assert response.status_code == 200
        return response

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
            # The prompt rides along on the true-up's own response.
            assert b"Tick the ones your statement shows" in response.data

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
            assert b"50.00" not in listed.data

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert response.status_code == 200
            assert self._entries_of(txn.id)[0].settled_on is None

    def test_a_purchase_on_a_settled_parent_is_not_offered(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """RE-RULED from "entries on non-projected parents are not cleared".

        The entry reservation prices only PROJECTED rows, so a purchase on a
        settled parent is inert -- listing it would ask the user to reconcile
        something that cannot move a figure, and stamping it would record a
        fact nothing reads.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, None),
                ],
            )
            paid = db.session.query(Status).filter_by(name="Paid").one()
            txn.status_id = paid.id
            db.session.commit()
            self._true_up(auth_client, seed_user["account"].id, "5000.00")
            entry_id = self._entries_of(txn.id)[0].id

            listed = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            )
            assert b"100.00" not in listed.data

            auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert self._entries_of(txn.id)[0].settled_on is None

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
        ``display_today()`` -- measured: substituting the latter in
        ``accounts.anchor.reconcile_purchases`` left the whole 7,721-test suite
        green.  This is the case that separates them, and it is the only one.

        ``observed_on`` is USER-SUPPLIED (plan step 2), so a back-dated
        assertion is an ordinary state: "my statement is dated the 3rd, not
        today".  Here the account's latest assertion is for the day after the
        first period started -- weeks in the past -- and the ticked purchase
        must be stamped with THAT day.

        What the wrong clock costs, and why it is not merely untidy: the
        purchase would get ``settled_on = today``, which is AFTER the asserted
        day, so ``ReconciledThrough.covers`` answers False, the reservation never
        drops, and the projection stays low by the whole purchase.  Worse, the
        row now fails ``_outstanding_scope``'s ``settled_on IS NULL`` clause, so
        the panel can never offer it again -- the user cannot fix it from the
        surface that broke it.
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


# ── Account Type CRUD ─────────────────────────────────────────────


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
        """Cash detail auto-creates interest params if the row is missing."""
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

            # No InterestParams row exists yet.
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200

            # Auto-created by the cash-detail route's safety fallback.
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is not None


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
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=10,
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

            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=27,
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

            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=27,
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
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=27,
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
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=10,
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

            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=10,
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
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=10,
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
        notes="C7 symptom #5 test: anchor override",
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
        transaction_id=txn.id,
        user_id=user_id,
        amount=amount,
        description="Cleared purchase",
        purchased_on=date(2026, 1, 15),
        is_credit=False,
        settled_on=date(2026, 1, 15),
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
            current_period = pay_period_service.get_current_period(
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
                seed_periods_today,
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
            current_period = pay_period_service.get_current_period(
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
            pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=10,
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
            pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=10,
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
            pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=10,
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

    def _checking_with_income(self, seed_user, num_periods=27):
        """Create a checking account with +$500/period net income.

        Anchor $5,000 at ``periods[0]`` (today), then a $2,000 income and a
        $1,500 expense in every post-anchor period -- the same shape as
        ``test_checking_detail_projection_values_are_correct`` so the
        balances are the hand-computed 5000 + n*500.  Returns
        ``(account, periods)``.
        """
        periods = pay_period_service.generate_pay_periods(
            user_id=seed_user["user"].id,
            start_date=display_today(),
            num_periods=num_periods,
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
            all_periods = pay_period_service.get_all_periods(
                seed_user["user"].id,
            )
            assert n == len(all_periods)
            assert chart["current_index"] == [
                p.id for p in all_periods
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
            pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=30,
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
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=33,
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
            current = pay_period_service.get_current_period(seed_user["user"].id)
            # pylint: disable=import-outside-toplevel
            from app.services.balance_at import BalanceContext
            ibp = net_worth_kernel.interest_by_period_for_account(
                acct, BalanceContext.build(seed_user["user"].id), periods,
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
            pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=display_today(),
                num_periods=10,
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

    def test_true_up_cash_revert_skips_as_of_oob(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """With ?revert=cash, the success response omits the as-of OOB.

        The cash detail page has no singleton ``#anchor-as-of`` element
        (the band's caption re-renders with the region on
        ``balanceChanged``), so the out-of-band "as of" snippet -- which
        would orphan-target -- is dropped.  The ``balanceChanged``
        trigger still fires.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up?revert=cash",
                data={"anchor_balance": "3210.00"},
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"
            body = response.data.decode()
            assert 'id="anchor-as-of"' not in body
            assert 'hx-swap-oob="true"' not in body


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

    def test_anchor_form_refuses_amortizing_loan(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET anchor-form for a loan: 422 -- the editor never even opens."""
        with app.app_context():
            loan = self._loan(seed_user)

            response = auth_client.get(f"/accounts/{loan.id}/anchor-form")

            assert response.status_code == 422
            assert b"not a cash anchor" in response.data

    def test_update_form_rejects_loan_anchor_change(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /accounts/<id> with a CHANGED anchor on a loan is refused."""
        with app.app_context():
            loan = self._loan(seed_user)
            column_before = cash_ledger.resolve_anchor(loan).balance

            response = auth_client.post(f"/accounts/{loan.id}", data={
                "name": loan.name,
                "account_type_id": loan.account_type_id,
                "anchor_balance": "1.00",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"not a cash anchor" in response.data

            refreshed = db.session.get(Account, loan.id)
            assert cash_ledger.resolve_anchor(refreshed).balance == column_before

    def test_update_form_allows_loan_edit_with_unchanged_anchor(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan rename with the form's unchanged anchor echo succeeds.

        The edit form round-trips ``current_anchor_balance`` on every
        submit; an unchanged echo is not an assertion, so gating it
        would break every ordinary loan edit.
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
