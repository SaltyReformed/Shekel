"""
Shekel Budget App -- Account Route Tests

Tests for account CRUD, anchor balance true-up, and account type
management endpoints (§2.1 of the test plan).
"""

from datetime import date
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
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.user import User, UserSettings
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.services import account_service, balance_calculator, pay_period_service
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
            anchor_period_id=bootstrap.id,
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
            assert acct.current_anchor_balance == Decimal("500.00")

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
            assert acct.current_anchor_balance == Decimal("0")

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
            assert acct2.current_anchor_balance == Decimal("0")

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
                    anchor_period_id=seed_user["bootstrap_period"].id,
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
                    anchor_period_id=seed_user["bootstrap_period"].id,
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
            assert acct.current_anchor_balance == Decimal("3000.00")

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

    def test_true_up_no_current_period(self, app, auth_client, seed_user):
        """PATCH /accounts/<id>/true-up returns 400 when no pay periods exist."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1000.00"},
            )

            assert response.status_code == 400
            assert b"No current pay period found" in response.data

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
            orig_balance = other["account"].current_anchor_balance

            response = auth_client.patch(
                f"/accounts/{other['account'].id}/true-up",
                data={"anchor_balance": "9999.00"},
            )

            assert response.status_code == 404

            # Prove no state change occurred.
            db.session.expire_all()
            db.session.refresh(other["account"])
            assert other["account"].current_anchor_balance == orig_balance

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


class TestTrueUpClearsEntries:
    """Tests for the auto-clear behavior on checking anchor true-up.

    When the user trues up a checking anchor balance, the service
    flips is_cleared=TRUE on every past-dated entry whose parent
    transaction is Projected.  See the rationale in
    app/services/entry_service.py::clear_entries_for_anchor_true_up.

    These tests verify the scope of the update: past-dated entries on
    projected parents are cleared, while future-dated entries, entries
    on non-projected parents, and entries on non-checking-account
    true-ups are left alone.
    """

    def _make_grocery_txn_with_entries(
        self, seed_user, seed_periods_today, entries, account=None,
    ):
        """Create a tracked grocery transaction with the given entries.

        Args:
            seed_user: seed_user fixture dict.
            seed_periods_today: list of PayPeriods.
            entries: list of (amount, entry_date, is_credit, is_cleared)
                tuples.
            account: the Account the template and transaction belong
                to.  Defaults to the seed_user's primary checking
                account; pass a second account to exercise the
                per-account scope of the true-up clear.

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

        for amount, entry_date, is_credit, is_cleared in entries:
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal(amount),
                description="Test purchase",
                entry_date=entry_date,
                is_credit=is_credit,
                is_cleared=is_cleared,
            ))
        db.session.commit()
        return txn

    def test_past_dated_projected_entries_get_cleared(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """True-up flips past-dated uncleared debits on projected parents."""
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            past = date.today() - __import__("datetime").timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, False),
                    ("249.71", past, False, False),
                    ("105.77", past, False, False),
                ],
            )

            response = auth_client.patch(
                f"/accounts/{seed_user['account'].id}/true-up",
                data={"anchor_balance": "4537.66"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entries = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .all()
            )
            assert len(entries) == 3
            assert all(e.is_cleared for e in entries)

    def test_future_dated_entries_not_cleared(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Entries with entry_date > today must NOT be flipped by true-up."""
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            future = date.today() + __import__("datetime").timedelta(days=7)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("50.00", future, False, False),
                ],
            )

            response = auth_client.patch(
                f"/accounts/{seed_user['account'].id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entry = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .one()
            )
            assert entry.is_cleared is False

    def test_entries_on_non_projected_parent_not_cleared(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Entries on settled (Paid) parents are not touched by true-up.

        They're already excluded from the balance formula, but leaving
        their is_cleared alone is the correct behavior -- we only flip
        what the anchor change actually reconciles.
        """
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            past = date.today() - __import__("datetime").timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, False),
                ],
            )
            # Flip the parent to Paid after entry creation.
            paid = db.session.query(Status).filter_by(name="Paid").one()
            txn.status_id = paid.id
            db.session.commit()

            response = auth_client.patch(
                f"/accounts/{seed_user['account'].id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entry = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .one()
            )
            assert entry.is_cleared is False

    def test_non_checking_true_up_does_not_clear(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A true-up on a non-checking account does not affect entries.

        Debit entries only hit checking, so anchor updates on savings
        or loan accounts must not touch is_cleared.
        """
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            past = date.today() - __import__("datetime").timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, False),
                ],
            )

            # Create a non-checking account for the user.
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("1000.00"),
                    anchor_period_id=seed_periods_today[0].id,
                ),
            )
            db.session.add(savings)
            db.session.commit()

            response = auth_client.patch(
                f"/accounts/{savings.id}/true-up",
                data={"anchor_balance": "1500.00"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entry = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .one()
            )
            # Debit entries untouched because savings != checking.
            assert entry.is_cleared is False

    def test_already_cleared_entries_unchanged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Entries that are already cleared remain cleared -- true-up is idempotent."""
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            past = date.today() - __import__("datetime").timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, True),
                ],
            )

            response = auth_client.patch(
                f"/accounts/{seed_user['account'].id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entry = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .one()
            )
            assert entry.is_cleared is True

    def test_true_up_on_one_account_does_not_clear_other_checking_account(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A true-up reconciles ONLY the trued-up account's entries.

        Accounts carry no per-type uniqueness, so a user may hold more
        than one checking account.  Trueing up account A declares A's
        real balance; it must not flip is_cleared on account B's
        entries, because B's anchor was never raised -- clearing B's
        debits there would drop B's reservation and silently inflate
        B's projected balance (#8).  This is the regression guard for
        the per-account scope of clear_entries_for_anchor_true_up: it
        fails under the prior owner-only filter (which cleared B too)
        and passes once the filter is scoped by account_id.
        """
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            past = date.today() - __import__("datetime").timedelta(days=1)

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
                    anchor_period_id=seed_periods_today[0].id,
                ),
            )
            db.session.add(account_b)
            db.session.commit()

            # Past-dated uncleared debit entry on each account.
            txn_a = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("60.00", past, False, False)],
            )
            txn_b = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("75.00", past, False, False)],
                account=account_b,
            )

            # True up account A only.
            response = auth_client.patch(
                f"/accounts/{seed_user['account'].id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entry_a = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn_a.id)
                .one()
            )
            entry_b = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn_b.id)
                .one()
            )
            # A's entry is reconciled by its own true-up.
            assert entry_a.is_cleared is True
            # B's entry is untouched -- B was never trued up.
            assert entry_b.is_cleared is False


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
                    anchor_period_id=seed_user["bootstrap_period"].id,
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
        """HYSA creation redirects to HYSA detail with setup=1 and auto-creates InterestParams."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()

            resp = auth_client.post("/accounts", data={
                "name": "My HYSA",
                "account_type_id": hysa_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/interest" in location
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
            assert "/interest" in location
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
            assert "/interest" in resp.headers["Location"]

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
        """Interest detail page renders for any has_interest=True type."""
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hsa_type.id,
                    name="HSA Detail Test",
                    anchor_balance=500,
                    anchor_period_id=seed_periods_today[0].id,
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

            resp = auth_client.get(f"/accounts/{acct.id}/interest")
            assert resp.status_code == 200

    def test_interest_detail_rejects_non_interest_type(
        self, app, auth_client, seed_user,
    ):
        """Checking (has_interest=False) is rejected by interest detail."""
        with app.app_context():
            acct = seed_user["account"]
            resp = auth_client.get(
                f"/accounts/{acct.id}/interest", follow_redirects=True,
            )
            assert b"does not support interest parameters" in resp.data

    def test_has_interest_true_but_no_params_row(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """Interest detail auto-creates params if row missing."""
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hsa_type.id,
                    name="HSA No Params",
                    anchor_balance=100,
                    anchor_period_id=seed_periods_today[0].id,
                ),
            )
            db.session.add(acct)
            db.session.commit()

            # No InterestParams row exists yet.
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None

            resp = auth_client.get(f"/accounts/{acct.id}/interest")
            assert resp.status_code == 200

            # Auto-created by the detail route's safety fallback.
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
                    anchor_period_id=seed_periods_today[0].id,
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

            resp = auth_client.get(f"/accounts/{acct.id}/interest?setup=1")
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
                    anchor_period_id=seed_periods_today[0].id,
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

            resp = auth_client.get(f"/accounts/{acct.id}/interest")
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
                    anchor_period_id=seed_periods_today[0].id,
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
                    anchor_period_id=seed_periods_today[0].id,
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
    """Tests for the checking account detail page with balance projections."""

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
                anchor_period_id=periods[0].id,
            ),
        )
        db.session.add(acct)
        return acct

    def test_checking_detail_page_renders(self, app, auth_client, seed_user):
        """GET /accounts/<id>/checking renders the detail page with account name and balance."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=10,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")

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
                start_date=date.today(),
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

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
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
        """Checking detail projections use the same balance calculator as the grid.

        Calls calculate_balances() directly and verifies the detail page
        displays the same value for the 3-month projection.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Salary"]

            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
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

            # Call balance calculator directly (same function grid.py uses).
            acct_transactions = (
                db.session.query(Transaction)
                .filter(
                    Transaction.account_id == acct.id,
                    Transaction.pay_period_id.in_([p.id for p in periods]),
                    Transaction.scenario_id == scenario.id,
                    Transaction.is_deleted.is_(False),
                )
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=periods[0].id,
                periods=periods,
                transactions=acct_transactions,
            )

            # Get the 3-month balance from the calculator.
            target_period = periods[6]
            calc_balance = balances[target_period.id]

            # Verify the detail page shows this exact value.
            resp = auth_client.get(f"/accounts/{acct.id}/checking")
            assert resp.status_code == 200

            # The projection summary uses {:,.0f} format.
            expected_str = "${:,.0f}".format(float(calc_balance))
            assert expected_str.encode() in resp.data

    def test_checking_detail_rejects_non_checking_account(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/checking returns 404 for non-checking account types."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="My Savings",
                    anchor_balance=Decimal("1000.00"),
                    anchor_period_id=seed_user["bootstrap_period"].id,
                ),
            )
            db.session.add(savings)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{savings.id}/checking")
            assert resp.status_code == 404

    def test_checking_detail_rejects_other_users_account(
        self, app, auth_client, seed_user, second_user,
    ):
        """GET /accounts/<id>/checking returns 404 for another user's account (IDOR)."""
        with app.app_context():
            resp = auth_client.get(
                f"/accounts/{second_user['account'].id}/checking"
            )
            assert resp.status_code == 404

    def test_checking_detail_handles_no_transactions(
        self, app, auth_client, seed_user,
    ):
        """Checking detail with no transactions shows flat balance at anchor amount."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=27,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
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
                start_date=date.today(),
                num_periods=10,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
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
                start_date=date.today(),
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

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
            assert resp.status_code == 200

            # The credit expense should NOT reduce the balance.
            # Projections should still show $5,000 (flat from anchor).
            assert b"$5,000" in resp.data
            # Verify the balance was NOT reduced by the credit expense.
            assert b"$4,000" not in resp.data

    def test_checking_detail_shows_anchor_date(self, app, auth_client, seed_user):
        """Anchor period start date is displayed on the checking detail page."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=10,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
            assert resp.status_code == 200

            # The anchor period's start date should be displayed.
            anchor_date_str = periods[0].start_date.strftime("%b %-d, %Y")
            assert anchor_date_str.encode() in resp.data


# ── Commit 7: canonical entries-aware producer routing ─────────────
#
# Pre-Commit-7 the /accounts checking detail built its own transaction
# query without ``selectinload(Transaction.entries)`` (same shape as
# the savings tile pre-Commit-6) and forked on the NULL-anchor case
# differently from the grid.  The silent-degrade seam in
# ``balance_calculator._entry_aware_amount`` (closed at the math layer
# by Commit 5, structurally closed by the canonical producer in Commit
# 5) yielded $160.00 on the grid and $114.29 on /accounts for the
# audit's symptom #1 / #5 tuple.  Commit 7 routes the checking detail
# through ``balance_resolver.balances_for`` and resolves the anchor via
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
    history = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=pay_period.id,
        anchor_balance=anchor_balance,
        notes="C7 symptom #5 test: anchor override",
    )
    db_session.add(history)
    db_session.flush()
    account.current_anchor_balance = anchor_balance
    account.current_anchor_period_id = pay_period.id
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
        entry_date=date(2026, 1, 15),
        is_credit=False,
        is_cleared=True,
    ))
    db_session.flush()


class TestCheckingDetailCanonicalProducer:
    """C7: /accounts checking detail routed through balance_resolver.

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

        Both the grid (already routed in Commit 5) and the /accounts
        checking detail page (routed by Commit 7) MUST return
        Decimal("160.00").  Pre-Commit-7 the checking detail page
        reported the silent-degrade value Decimal("114.29") via the
        unloaded-entries seam.
        """
        from app.services import balance_resolver  # pylint: disable=import-outside-toplevel

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

            # Grid value via the canonical producer (the grid already
            # routes through ``balances_for`` post-Commit-5; replaying
            # it here is "what does the grid show" without a route
            # round-trip).
            grid_result = balance_resolver.balances_for(
                account, seed_user["scenario"].id, seed_periods_today,
            )
            grid_current_balance = grid_result.balances[current_period.id]

            # F-009 / CRIT-01: 614.29 - max(500 - 45.71 - 0, 0)
            #                = 614.29 - 454.29 = 160.00.
            assert grid_current_balance == Decimal("160.00")

            # Detail-page current balance is rendered via the {:,.2f}
            # format string at template line 43 (search "{:,.2f}" in
            # accounts/checking_detail.html).
            resp = auth_client.get(f"/accounts/{account.id}/checking")
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
                    anchor_period_id=current_period.id,
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
            resp_a = auth_client.get(f"/accounts/{account_a.id}/checking")
            assert resp_a.status_code == 200
            assert b"$160.00" in resp_a.data
            assert b"$114.29" not in resp_a.data

            resp_b = auth_client.get(f"/accounts/{account_b.id}/checking")
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
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
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
                    anchor_period_id=periods[0].id,
                ),
            )
            db.session.flush()
            db.session.commit()

            resp = auth_client.get(f"/accounts/{account.id}/checking")
            assert resp.status_code == 200
            assert b"Zero Anchor Checking" in resp.data
            # The current-balance display uses the {:,.2f} format
            # (see checking_detail.html line 43); a real zero anchor
            # must render as "$0.00", not blank or "--".
            assert b"$0.00" in resp.data
            # The 3-month horizon label appears when there is a period
            # at offset 6 from the current period; the explicit 10-
            # period setup guarantees one (matches the existing
            # ``test_checking_detail_handles_short_horizon`` pattern).
            assert b"3 months" in resp.data
            # The per-period projection table renders rows for every
            # period covered (anchor + forward).  Each row uses {:,.2f}
            # so the zero-anchor flat projection shows "$0.00" in the
            # balance column at least once beyond the summary card.
            assert resp.data.count(b"$0.00") >= 2

    def test_accounts_checking_balance_routed_through_resolver(self):
        """Static guard: detail-page balances route through the balance-at seam.

        F-6 lock, sibling of
        ``test_grid_balance_computation_routed_through_resolver``.
        The cross-page balance-equality regression test
        (``tests/test_integration/test_cross_page_balance_equality.py``,
        Commit 11 of the main remediation) cannot catch a route-handler
        bypass of the canonical producer because its /accounts reader
        re-runs ``balance_resolver.balances_for`` itself rather than
        parsing the rendered HTML.  A regression that swaps the detail
        routes to the bare entries-blind producer
        ``balance_calculator.calculate_balances`` (re-opening the F-009 /
        CRIT-01 silent-degrade seam) would drift silently through that
        lock.  This static guard closes the gap.

        Updated for Level-1 Commit 8: both detail routes now read balances
        through the balance-at seam -- ``checking_detail`` via the
        cash-flow entry ``balance_at.cash_balance_map`` and
        ``interest_detail`` via the kind-correct ``balance_at.balance_map``
        (plus the kernel's ``interest_by_period_for_account`` for the
        interest figure) -- which delegate to the canonical entries-aware
        producers.  The route no longer calls ``balance_resolver`` or
        ``balance_calculator`` for balances directly.

        Two assertions:
          1. ``balance_at.cash_balance_map`` must appear in the accounts
             detail-route file (positive: the Commit-8 seam wiring on
             ``checking_detail`` is intact).
          2. ``balance_calculator.calculate_balances(`` (the bare
             entries-blind producer) must NOT appear in the file -- the
             open-paren anchors the substring to the bare function name,
             so neither it nor the (now also absent)
             ``calculate_balances_with_interest(`` can re-open the
             entries-blind seam.

        File path note: Commit 21 of the follow-up remediation (F-1)
        split the monolithic ``app/routes/accounts.py`` into a per-
        sub-domain package; ``checking_detail`` and
        ``interest_detail`` now live in ``app/routes/accounts/detail.py``.
        The static guard reads that file directly.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        accounts_source = Path(
            "app/routes/accounts/detail.py",
        ).read_text(encoding="utf-8")
        assert "balance_at.cash_balance_map" in accounts_source, (
            "app/routes/accounts/detail.py no longer calls "
            "``balance_at.cash_balance_map`` -- regression on the "
            "Level-1 Commit 8 balance-at seam contract.  Route the "
            "checking-detail balance computation through the seam's "
            "cash-flow entry instead of a hand-rolled loop or a direct "
            "producer call."
        )
        assert "balance_calculator.calculate_balances(" not in accounts_source, (
            "app/routes/accounts/detail.py imports the bare entries-blind "
            "``balance_calculator.calculate_balances`` -- this bypasses "
            "the entries-aware reduction (F-009 / CRIT-01 fix).  Use the "
            "``balance_at`` seam instead."
        )


class TestCheckingDashboardLink:
    """Tests for the checking detail link on the savings/accounts dashboard."""

    def test_dashboard_has_checking_detail_link(self, app, auth_client, seed_user):
        """GET /savings dashboard includes a link to the checking detail page."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=10,
            )
            # Set anchor on the seed_user account so the dashboard
            # can compute balances for it.
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The dashboard should include a link to the checking detail page.
            expected_url = f"/accounts/{seed_user['account'].id}/checking"
            assert expected_url.encode() in resp.data

    def test_dashboard_checking_link_not_shown_for_other_types(
        self, app, auth_client, seed_user,
    ):
        """Dashboard does not show checking detail link for non-checking accounts."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
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
                    anchor_period_id=periods[0].id,
                ),
            )
            db.session.add(savings)

            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The savings account should NOT have a checking detail link.
            savings_checking_url = f"/accounts/{savings.id}/checking"
            assert savings_checking_url.encode() not in resp.data

            # But the checking account should have one.
            checking_url = f"/accounts/{seed_user['account'].id}/checking"
            assert checking_url.encode() in resp.data


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
                _ = db.session.get(Account, acct_id).current_anchor_balance
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

            acct.current_anchor_balance = Decimal("9999.00")

            with pytest.raises(StaleDataError):
                db.session.commit()

            db.session.rollback()

            db.session.expire_all()
            persisted = db.session.get(Account, acct_id)
            assert persisted.current_anchor_balance != Decimal("9999.00")
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
                    anchor_period_id=seed_user["bootstrap_period"].id,
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


class TestTrueUpStaleForm:
    """``true_up`` (PATCH /accounts/<id>/true-up) optimistic-locking behaviour."""

    def test_true_up_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A submitted ``version_id`` that matches the row succeeds and bumps the counter."""
        with app.app_context():
            acct_id = seed_user["account"].id
            initial_version = db.session.get(Account, acct_id).version_id

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "1100.00",
                    "version_id": str(initial_version),
                },
            )

            assert response.status_code == 200, response.data

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.current_anchor_balance == Decimal("1100.00")
            assert acct.version_id == initial_version + 1

    def test_true_up_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A submitted ``version_id`` older than the current row returns 409.

        The route MUST short-circuit before touching the database; the
        anchor balance and ``AccountAnchorHistory`` table both stay
        unchanged.  This is the manual-verification scenario from the
        C-17 plan: Tab 1 holds an old version, Tab 2 commits to bump
        the row, Tab 1 resubmits with the stale version -- the
        server must refuse.

        ``stale_version`` is captured from the row state before the
        bump rather than hard-coded to 1; ``seed_periods_today`` also
        commits an UPDATE to set the anchor period and would otherwise
        leave the row at version 2 before this test even runs.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            # Capture the version Tab 1 would have loaded.
            stale_version = db.session.get(Account, acct_id).version_id

            # Simulate Tab 2 having already advanced the row.
            _bump_account_version_outside_session(acct_id)
            db.session.expire_all()
            current_version = db.session.get(Account, acct_id).version_id
            assert current_version == stale_version + 1, (
                "fixture invariant: bump must advance the version by "
                "exactly one"
            )
            balance_before = db.session.get(Account, acct_id).current_anchor_balance
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id).count()
            )

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "1200.00",
                    "version_id": str(stale_version),
                },
            )

            assert response.status_code == 409, (
                f"stale version_id must produce 409 Conflict, got "
                f"{response.status_code}: {response.data!r}"
            )
            # The conflict UI must include the "changed by another action"
            # affordance and the latest balance.
            body = response.data.decode()
            assert "changed by another action" in body.lower()
            # The display partial uses the warning class plus icon.
            assert "text-warning" in body
            assert "exclamation-triangle" in body

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.current_anchor_balance == balance_before, (
                "Stale-form 409 must NOT mutate the anchor balance."
            )
            assert acct.version_id == current_version, (
                "Stale-form 409 must NOT bump the version counter."
            )
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id).count()
            ) == history_count_before, (
                "Stale-form 409 must NOT write a history row -- the "
                "audit trail records only the winner."
            )

    def test_true_up_route_catches_stale_data_error_as_409(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A StaleDataError raised at flush time is converted to a 409 response.

        Engineers a true race using a SQLAlchemy mapper event: the
        route loads the row at version N, mutates it, then begins
        the flush; the event listener fires during the UPDATE and
        bumps the row from a separate connection, defeating the
        version-pinned WHERE clause.  SQLAlchemy raises
        ``StaleDataError`` and the route's ``except`` clause
        converts it into the same 409 + conflict partial the form-
        side check produces.  This exercises the SQLAlchemy-tier
        of the optimistic lock end-to-end through the HTTP layer.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct_id = seed_user["account"].id
            balance_before = db.session.get(Account, acct_id).current_anchor_balance

            fired = {"flag": False}

            def make_stale(mapper, connection, target):
                """Bump version_id from a separate connection mid-flush."""
                if fired["flag"] or target.id != acct_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(acct_id)

            event.listen(Account, "before_update", make_stale)
            try:
                response = auth_client.patch(
                    f"/accounts/{acct_id}/true-up",
                    data={"anchor_balance": "5555.00"},
                )
            finally:
                event.remove(Account, "before_update", make_stale)

            assert response.status_code == 409, (
                f"StaleDataError must convert to 409, got "
                f"{response.status_code}"
            )
            body = response.data.decode()
            assert "changed by another action" in body.lower()
            assert "exclamation-triangle" in body

            db.session.expire_all()
            persisted = db.session.get(Account, acct_id)
            assert persisted.current_anchor_balance == balance_before, (
                "StaleDataError-on-commit must roll back the pending "
                "balance change."
            )

    def test_true_up_omitted_version_falls_through_to_db_check(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Omitting ``version_id`` skips the form-side check.

        Backwards-compat: a future programmatic client that has no
        way to plumb the version through still validates and reaches
        the SQLAlchemy-tier check.  In the no-conflict case the
        update succeeds; in the conflict case StaleDataError fires
        on flush -- both are tested elsewhere.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            v0 = db.session.get(Account, acct_id).version_id

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={"anchor_balance": "1400.00"},
            )

            assert response.status_code == 200
            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.current_anchor_balance == Decimal("1400.00")
            assert acct.version_id == v0 + 1


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
                    anchor_period_id=seed_user["bootstrap_period"].id,
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
                    anchor_period_id=seed_user["bootstrap_period"].id,
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
    """Templates that render anchor edit forms must include a hidden ``version_id`` input."""

    def test_grid_anchor_form_includes_version_pin(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/anchor-form returns a form with the version_id pin."""
        with app.app_context():
            acct_id = seed_user["account"].id
            current_version = db.session.get(Account, acct_id).version_id

            response = auth_client.get(f"/accounts/{acct_id}/anchor-form")

            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body, (
                "grid anchor form must ship version_id as a hidden "
                "input for the optimistic-lock contract."
            )
            assert f'value="{current_version}"' in body, (
                "version_id hidden input must carry the current row's "
                "version, not a placeholder."
            )

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
        must carry the ``revert`` token so a 409 conflict response can
        re-render the conflict cell with the dashboard retry target rather
        than stranding the card on the grid display cell.
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

    def test_grid_conflict_cell_retry_opener_carries_no_revert(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """M1 (grid conflict): the 409 conflict cell's retry opener is token-free.

        A stale-version PATCH with no ``revert`` token returns the grid
        conflict cell.  Its retry opener (the click-to-reopen ``hx-get``)
        must reopen the bare ``anchor-form`` -- no ``revert`` token -- so the
        grid conflict path is byte-for-byte unchanged.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            stale_version = db.session.get(Account, acct_id).version_id
            _bump_account_version_outside_session(acct_id)

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "1200.00",
                    "version_id": str(stale_version),
                },
            )
            assert response.status_code == 409
            body = response.data.decode()
            # Confirm this is the conflict cell, then pin its retry opener.
            assert "changed by another action" in body.lower()
            assert f'hx-get="/accounts/{acct_id}/anchor-form"' in body
            assert "revert=dashboard" not in body

    def test_dashboard_conflict_cell_retry_opener_carries_revert(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """M1 (dashboard conflict): the 409 conflict cell reopens in the dashboard.

        A stale-version PATCH carrying ``?revert=dashboard`` returns the
        conflict cell whose retry opener must reopen ``anchor-form`` WITH
        ``revert=dashboard``.  Without this, a conflict raised from the
        dashboard balance card would strand it on the grid display cell:
        the retry would reopen with no token, and Cancel would then revert
        to the grid rather than the dashboard balance section.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            stale_version = db.session.get(Account, acct_id).version_id
            _bump_account_version_outside_session(acct_id)

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up?revert=dashboard",
                data={
                    "anchor_balance": "1200.00",
                    "version_id": str(stale_version),
                },
            )
            assert response.status_code == 409
            body = response.data.decode()
            assert "changed by another action" in body.lower()
            assert (
                f'hx-get="/accounts/{acct_id}/anchor-form?revert=dashboard"'
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

        So a 409 conflict response can re-render the conflict cell with the
        cockpit retry target rather than stranding the card on the grid
        display cell.
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

    def test_accounts_conflict_cell_retry_opener_carries_revert(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The cockpit 409 conflict cell reopens in the cockpit surface.

        A stale-version PATCH carrying ``?revert=accounts`` returns the
        conflict cell whose retry opener must reopen ``anchor-form`` WITH
        ``revert=accounts``, so the cockpit card never strands on the grid.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            stale_version = db.session.get(Account, acct_id).version_id
            _bump_account_version_outside_session(acct_id)

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up?revert=accounts",
                data={
                    "anchor_balance": "1200.00",
                    "version_id": str(stale_version),
                },
            )
            assert response.status_code == 409
            body = response.data.decode()
            assert "changed by another action" in body.lower()
            assert (
                f'hx-get="/accounts/{acct_id}/anchor-form?revert=accounts"'
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
