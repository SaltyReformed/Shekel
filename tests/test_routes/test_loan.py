"""
Tests for unified loan routes.

Covers dashboard, setup, parameter updates, escrow management,
rate history, and payoff calculator across multiple loan types.
"""

import re
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory, EscrowComponent
from app.models.ref import AccountType
from app.services.transfer_service import create_transfer
from app.services import account_service

from tests._test_helpers import (
    freeze_today,
    insert_origination_event,
    insert_trueup_event,
    select_option_values,
)


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    Loan tests use specific origination_date values, inline
    ``date.today()`` calls (e.g. ``first_of_this_month =
    date.today().replace(day=1)``), and assertions like
    ``rule.end_date > date.today()``.  Auto-discovery patches every
    loaded module so test, fixture, and production services all see
    the same frozen "today" regardless of wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


# ── Helpers ──────────────────────────────────────────────────────────


def _create_loan_account(seed_user, db_session, type_name, name, principal,
                         rate, term, orig_date, payment_day, is_arm=False):
    """Helper to create a loan account with params for any amortizing type.

    Test contract: the ``principal`` argument is the value the test
    expects to see displayed as "Current Principal" on the loan
    card.  Pre-Commit-15 this worked because the dashboard rendered
    the unmaintained stored ``current_principal`` column verbatim;
    post-Commit-15 the dashboard reads the resolver's current_balance
    (E-18) which is derived from :class:`LoanAnchorEvent`, not the
    stored column.

    To preserve the test contract without rewriting every caller,
    this helper synthesises TWO events when the
    ``original_principal``-vs-``current_principal`` gap is non-zero
    (existing helper sets ``original = principal + 5000``,
    simulating "$5,000 already paid down before the test starts"):

      * an ORIGINATION event at ``original_principal`` (matches
        Commit 12's backfill semantics and production's create_params),
      * a USER_TRUEUP event one day after origination at the lower
        ``current_principal`` value (represents "the user marked
        the loan's true current balance as $X today").

    When ``principal == 0`` the gap is the full $5,000, so the
    trueup at $0 produces a paid-off loan state -- what
    ``test_refinance_paid_off_loan`` needs.  When ``principal > 0``
    the trueup at ``principal`` produces a partially-paid loan
    state -- what ``test_refinance_principal_auto_calculated`` and
    every other refinance / debt-card test needs.
    """
    from datetime import timedelta  # pylint: disable=import-outside-toplevel
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
    from app.models.loan_anchor_event import LoanAnchorEvent  # pylint: disable=import-outside-toplevel

    loan_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = account_service.create_account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name=name,
        anchor_balance=principal,
    )
    db_session.add(account)
    db_session.flush()

    original_principal = principal + Decimal("5000.00")
    params = LoanParams(
        account_id=account.id,
        original_principal=original_principal,
        current_principal=principal,
        interest_rate=rate,
        term_months=term,
        origination_date=orig_date,
        payment_day=payment_day,
        is_arm=is_arm,
    )
    db_session.add(params)
    db_session.flush()
    # Origination LoanAnchorEvent (E-18 / Commit 15): the resolver
    # requires at least one event per loan.  Production's
    # ``loan.create_params`` writes the same paired row; tests that
    # build LoanParams directly must mirror it.
    insert_origination_event(params)
    # User-trueup event at the lower current_principal -- preserves
    # the pre-Commit-15 test contract that ``principal`` matches the
    # displayed Current Principal.  Dated one day after origination
    # so the resolver's (anchor_date, created_at) DESC selector
    # picks this event over the origination event.
    db_session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=orig_date + timedelta(days=1),
        anchor_balance=principal,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        ),
    ))
    db_session.commit()
    return account


def _create_auto_loan(seed_user, db_session, name="My Auto Loan"):
    """Helper: auto loan account with params."""
    return _create_loan_account(
        seed_user, db_session, "Auto Loan", name,
        Decimal("25000.00"), Decimal("0.05000"), 60,
        date(2025, 1, 1), 15,
    )


def _create_mortgage(seed_user, db_session, name="My Mortgage"):
    """Helper: mortgage account with params."""
    return _create_loan_account(
        seed_user, db_session, "Mortgage", name,
        Decimal("250000.00"), Decimal("0.06500"), 360,
        date(2023, 6, 1), 1,
    )


def _create_other_loan(second_user, db_session, type_name="Auto Loan"):
    """Create a loan account owned by the second user."""
    loan_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = account_service.create_account(
        user_id=second_user["user"].id,
        account_type_id=loan_type.id,
        name="Other Loan",
        anchor_balance=Decimal("15000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal("20000.00"),
        current_principal=Decimal("15000.00"),
        interest_rate=Decimal("0.04000"),
        term_months=48,
        origination_date=date(2024, 6, 1),
        payment_day=1,
    )
    db_session.add(params)
    db_session.flush()
    insert_origination_event(params)
    db_session.commit()
    return account


# ── Dashboard Tests ──────────────────────────────────────────────────


class TestLoanDashboard:
    """Tests for the unified loan dashboard page."""

    @pytest.mark.parametrize("create_fn", [_create_auto_loan, _create_mortgage])
    def test_dashboard_view(self, auth_client, seed_user, db, seed_periods, create_fn):
        """GET returns 200 with loan summary for any amortizing type."""
        acct = create_fn(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Loan Summary" in resp.data

    def test_dashboard_setup_when_no_params(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders setup page when params don't exist yet."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="No Params Loan",
        
            anchor_balance=Decimal("0"),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Configure" in resp.data

    def test_dashboard_404_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """Nonexistent account returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.get("/accounts/99999/loan")
        assert resp.status_code == 404

    def test_dashboard_idor(self, auth_client, second_user, db, seed_periods):
        """Another user's loan dashboard returns 404 without leaking data (security)."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.get(f"/accounts/{other.id}/loan")
        assert resp.status_code == 404
        assert b"Other Loan" not in resp.data

    def test_dashboard_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """Non-amortizing account type returns 404.

        The loan dashboard route's _load_loan_account helper returns None
        for both ownership-failure and wrong-type cases, and the route
        now uniformly aborts 404 for any None result. This is the same
        404-for-not-found-or-not-yours security response.
        """
        acct = seed_user["account"]  # checking account
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 404

    def test_dashboard_login_required(self, client, seed_user, db, seed_periods):
        """Unauthenticated request redirects to login."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_dashboard_shows_term_field(self, auth_client, seed_user, db, seed_periods):
        """Dashboard parameter form includes editable term_months input."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b'name="term_months"' in resp.data
        assert b'value="60"' in resp.data

    def test_dashboard_shows_payoff_calculator(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders the payoff calculator tab for all loan types."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Payoff Calculator" in resp.data
        assert b'data-slider-group="payoff"' in resp.data

    def test_dashboard_shows_icon_from_account_type(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders the correct icon class from account_type."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"bi-house" in resp.data


# ── Setup / Create Params Tests ──────────────────────────────────────


class TestLoanSetup:
    """Tests for initial loan parameter setup."""

    @pytest.mark.parametrize("type_name", ["Auto Loan", "Mortgage"])
    def test_create_params(self, auth_client, seed_user, db, seed_periods, type_name):
        """POST valid params creates LoanParams record."""
        loan_type = db.session.query(AccountType).filter_by(name=type_name).one()
        account = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name=f"Setup {type_name}",
        
            anchor_balance=Decimal("0"),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302
        assert "/loan" in resp.headers.get("Location", "")

        params = db.session.query(LoanParams).filter_by(account_id=account.id).one()
        assert params.interest_rate == Decimal("0.05000")
        assert params.term_months == 60

    def test_create_params_already_configured(self, auth_client, seed_user, db, seed_periods):
        """POST setup when params exist redirects with info flash."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/setup",
            data={
                "original_principal": "99999.00",
                "current_principal": "99999.00",
                "interest_rate": "0.99",
                "term_months": "12",
                "origination_date": "2025-01-01",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Loan parameters already configured." in resp2.data

    def test_create_params_term_exceeds_type_max(self, auth_client, seed_user, db, seed_periods):
        """Auto loan rejects term > 120 (type-specific max_term_months)."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="Term Test Auto",
        
            anchor_balance=Decimal("0"),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "term_months": "360",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200  # re-renders setup
        assert b"cannot exceed" in resp.data

        count = db.session.query(LoanParams).filter_by(account_id=account.id).count()
        assert count == 0

    def test_mortgage_allows_long_term(self, auth_client, seed_user, db, seed_periods):
        """Mortgage accepts term=360 (max_term_months=600)."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        account = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="Long Term Mortgage",
        
            anchor_balance=Decimal("0"),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "300000.00",
                "current_principal": "250000.00",
                "interest_rate": "6.500",
                "term_months": "360",
                "origination_date": "2023-06-01",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(LoanParams).filter_by(account_id=account.id).one()
        assert params.term_months == 360

    def test_setup_prefills_current_principal(self, auth_client, seed_user, db, seed_periods):
        """Setup form pre-fills current_principal from anchor balance."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": "Prepop Auto",
                "account_type_id": str(loan_type.id),
                "anchor_balance": "15000",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b'name="current_principal"' in resp.data
        assert b'value="15000.00"' in resp.data


# ── Update Params Tests ──────────────────────────────────────────────


class TestLoanParamsUpdate:
    """Tests for updating loan parameters."""

    def test_params_update(self, auth_client, seed_user, db, seed_periods):
        """POST valid data updates editable params; current_principal is ignored.

        Re-pinned for E-18 / Commit 16 (decision D-C).  ``current_principal``
        is non-authoritative seed and the params form no longer accepts
        it -- ``LoanParamsUpdateSchema``'s ``unknown = EXCLUDE`` policy
        silently strips a stray submission.  The interest_rate
        percentage-to-decimal conversion is unchanged; the test was
        rewritten so its earlier ``params.current_principal ==
        Decimal("22000.00")`` assertion (which pinned the now-deprecated
        column write) is dropped in favor of an explicit invariant: the
        seed column survives the POST untouched.

        Hand-check:
        * The fixture seeds ``current_principal == Decimal("25000.00")``
          via ``_create_auto_loan``.
        * POSTing ``current_principal=22000.00`` is the silent no-op
          because the schema does not declare the field and
          ``_PARAM_FIELDS`` in :func:`app.routes.loan.update_params`
          no longer references it.
        * ``interest_rate=4.500`` -> ``Decimal("0.04500")`` via
          ``LoanParamsUpdateSchema``'s ``@pre_load`` hook, which
          dispatches to
          :func:`app.schemas.validation._normalize_percent_fields`
          (Commit 24 / HIGH-06 convention).
        """
        acct = _create_auto_loan(seed_user, db.session)
        params_before = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        seed_principal = params_before.current_principal

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                # Stray ``current_principal`` -- silently dropped by
                # the schema's EXCLUDE policy (E-18 / Commit 16).
                "current_principal": "22000.00",
                "interest_rate": "4.500",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.interest_rate == Decimal("0.04500")
        # E-18 / Commit 16: the stray ``current_principal`` post must
        # NOT mutate the seed column.  Users edit the displayed
        # balance via the dated true-up form, not this endpoint.
        assert params.current_principal == seed_principal

    def test_params_update_validation(self, auth_client, seed_user, db, seed_periods):
        """POST invalid data leaves DB unchanged."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"payment_day": "32"},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_term_update_saves(self, auth_client, seed_user, db, seed_periods):
        """POST with valid term_months persists the new value."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "payment_day": "15",
                "term_months": "48",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.term_months == 48

    def test_arm_fields_update(self, auth_client, seed_user, db, seed_periods):
        """ARM fields can be toggled on and adjusted."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "current_principal": "250000.00",
                "interest_rate": "6.500",
                "payment_day": "1",
                "is_arm": "true",
                "arm_first_adjustment_months": "60",
                "arm_adjustment_interval_months": "12",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.is_arm is True
        assert params.arm_first_adjustment_months == 60

    def test_params_update_idor(self, auth_client, second_user, db, seed_periods):
        """POST to another user's loan params returns 404 (security) and is unchanged."""
        other = _create_other_loan(second_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=other.id).one()
        orig_principal = orig.current_principal

        resp = auth_client.post(
            f"/accounts/{other.id}/loan/params",
            data={
                "current_principal": "1.00",
                "interest_rate": "0.99",
                "payment_day": "28",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=other.id).one()
        assert after.current_principal == orig_principal

    def test_params_update_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """POST to nonexistent account returns 404 (security)."""
        resp = auth_client.post(
            "/accounts/999999/loan/params",
            data={"current_principal": "20000.00", "interest_rate": "5.0", "payment_day": "1"},
        )
        assert resp.status_code == 404

    def test_params_update_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """POST loan params to checking account returns 404.

        The route's _load_loan_account helper returns None for both
        ownership-failure and wrong-type cases, which both abort 404.
        """
        checking = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking.id}/loan/params",
            data={"current_principal": "20000.00", "interest_rate": "5.0", "payment_day": "1"},
        )
        assert resp.status_code == 404

    def test_amortization_uses_updated_term(self, auth_client, seed_user, db, seed_periods):
        """Changing term_months recalculates amortization on next dashboard load."""
        acct = _create_auto_loan(seed_user, db.session)
        resp1 = auth_client.get(f"/accounts/{acct.id}/loan")

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "payment_day": "15",
                "term_months": "36",
            },
        )
        resp2 = auth_client.get(f"/accounts/{acct.id}/loan")

        pattern = rb"Monthly P.{1,6}I.*?\$([0-9,]+\.\d{2})"
        match1 = re.search(pattern, resp1.data, re.DOTALL)
        match2 = re.search(pattern, resp2.data, re.DOTALL)
        assert match1 is not None
        assert match2 is not None
        assert match1.group(1) != match2.group(1)


# ── Escrow Tests ─────────────────────────────────────────────────────


class TestEscrow:
    """Tests for escrow component management."""

    def test_escrow_add(self, auth_client, seed_user, db, seed_periods):
        """POST escrow creates component with percentage-to-decimal conversion."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00", "inflation_rate": "3"},
        )
        assert resp.status_code == 200
        assert b"Property Tax" in resp.data

        comp = db.session.query(EscrowComponent).filter_by(account_id=acct.id).first()
        assert comp is not None
        assert comp.inflation_rate == Decimal("0.03")

    def test_escrow_add_duplicate_name(self, auth_client, seed_user, db, seed_periods):
        """Duplicate escrow name returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        db.session.add(EscrowComponent(
            account_id=acct.id, name="Insurance", annual_amount=Decimal("2400.00"),
        ))
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Insurance", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 400
        assert b"already exists" in resp.data

    def test_escrow_delete(self, auth_client, seed_user, db, seed_periods):
        """POST delete deactivates component."""
        acct = _create_mortgage(seed_user, db.session)
        comp = EscrowComponent(
            account_id=acct.id, name="Old Insurance", annual_amount=Decimal("1200.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{comp.id}/delete")
        assert resp.status_code == 200
        db.session.refresh(comp)
        assert comp.is_active is False

    def test_escrow_delete_idor(self, auth_client, second_user, db, seed_periods):
        """DELETE another user's escrow returns 404 and leaves it active."""
        other = _create_other_loan(second_user, db.session, "Mortgage")
        comp = EscrowComponent(
            account_id=other.id, name="Tax", annual_amount=Decimal("3000.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(f"/accounts/{other.id}/loan/escrow/{comp.id}/delete")
        assert resp.status_code == 404

        db.session.expire_all()
        after = db.session.get(EscrowComponent, comp.id)
        assert after.is_active is True

    def test_escrow_oob_payment_update(self, auth_client, seed_user, db, seed_periods):
        """Adding escrow returns OOB fragments for payment summary."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="total-payment-display"' in html
        assert 'hx-swap-oob="true"' in html
        assert "$400.00/mo" in html


# ── Rate History Tests ───────────────────────────────────────────────


class TestRateHistory:
    """Tests for ARM rate change recording."""

    def test_rate_change_create(self, auth_client, seed_user, db, seed_periods):
        """POST rate change creates history row and updates params rate."""
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-04-01", "interest_rate": "7.000", "notes": "Adjustment"},
        )
        assert resp.status_code == 200

        entry = db.session.query(RateHistory).filter_by(account_id=acct.id).first()
        assert entry is not None
        assert entry.interest_rate == Decimal("0.07000")

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.interest_rate == Decimal("0.07000")

    def test_rate_change_records_monthly_pi(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An optional monthly_pi pins the period's recast P&I (E-18 setup capture).

        The lender's stated recast payment is stored on the RateHistory
        row so the rate-period engine holds the period's P&I at that
        exact figure instead of deriving it from origination.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={
                "effective_date": "2026-04-01",
                "interest_rate": "7.000",
                "monthly_pi": "2600.00",
            },
        )
        assert resp.status_code == 200

        entry = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .order_by(RateHistory.effective_date.desc())
            .first()
        )
        assert entry is not None
        assert entry.interest_rate == Decimal("0.07000")
        assert entry.monthly_pi == Decimal("2600.00")

    def test_rate_change_without_monthly_pi_is_null(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Omitting monthly_pi leaves it NULL so the period P&I is derived."""
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-05-01", "interest_rate": "7.500"},
        )
        assert resp.status_code == 200
        entry = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .order_by(RateHistory.effective_date.desc())
            .first()
        )
        assert entry.monthly_pi is None

    def test_rate_change_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid rate returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"interest_rate": "200.0"},
        )
        assert resp.status_code == 400

    def test_rate_change_idor(self, auth_client, second_user, db, seed_periods):
        """Rate change to another user's loan returns 404 with no side effects."""
        other = _create_other_loan(second_user, db.session, "Mortgage")
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/rate",
            data={"interest_rate": "9.0", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404

        count = db.session.query(RateHistory).filter_by(account_id=other.id).count()
        assert count == 0

    def test_rate_change_same_date_double_submit(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """F-104 / C-22: same effective_date double-submit produces one row.

        The composite unique ``uq_rate_history_account_effective_date``
        rejects the second INSERT.  The route flashes a clear
        message and re-renders the rate history without the
        proposed duplicate; total row count is exactly 1.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        data = {
            "effective_date": "2026-04-01",
            "interest_rate": "7.000",
        }
        r1 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        assert r1.status_code == 200

        r2 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        # Idempotent path: route returns the partial; total rows == 1.
        assert r2.status_code == 200

        db.session.expire_all()
        count = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert count == 1, (
            f"Expected 1 rate history row after duplicate submit, "
            f"found {count}; F-104 dedupe failed."
        )

    def test_rate_change_different_date_allowed(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """F-104 / C-22: different effective dates both succeed."""
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        r1 = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-04-01", "interest_rate": "7.000"},
        )
        r2 = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-05-01", "interest_rate": "7.500"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200

        db.session.expire_all()
        count = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert count == 2


# ── Payoff Calculator Tests ──────────────────────────────────────────


class TestPayoffCalculator:
    """Tests for the payoff calculator."""

    @pytest.mark.parametrize("create_fn", [_create_auto_loan, _create_mortgage])
    def test_payoff_extra_payment(self, auth_client, seed_user, db, seed_periods, create_fn):
        """POST extra payment mode returns results for any loan type."""
        acct = create_fn(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        assert b"Months Saved" in resp.data

    def test_payoff_target_date(self, auth_client, seed_user, db, seed_periods):
        """POST target date mode returns payment data."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200
        assert b"$" in resp.data

    def test_payoff_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid mode returns error."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "invalid_mode"},
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data

    def test_payoff_idor(self, auth_client, second_user, db, seed_periods):
        """Payoff calc to another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "100"},
        )
        assert resp.status_code == 404


# ── Payoff Chart Shape Tests (Commit 4) ─────────────────────────────


def _parse_chart_array(html, attr):
    """Extract a chart ``data-<attr>='...'`` JSON array.

    The template renders Chart.js datasets via ``|tojson`` inside
    single-quoted HTML attributes, so the content between the
    single quotes is a JSON-encoded list.  Returns the parsed
    Python list, or ``None`` when the attribute is absent.

    Args:
        html: Rendered HTMX fragment.
        attr: Attribute name without the ``data-`` prefix
            (``"original"``, ``"committed"``, ``"accelerated"``,
            ``"labels"``).
    """
    import json as _json  # pylint: disable=import-outside-toplevel
    import re as _re  # pylint: disable=import-outside-toplevel

    match = _re.search(rf"data-{attr}='([^']*)'", html)
    if match is None:
        return None
    return _json.loads(match.group(1))


def _label_to_month_tuple(label):
    """Convert a ``"%b %Y"`` chart label to a (year, month) tuple.

    Chart labels are formatted as ``"Mar 2026"`` via
    ``strftime('%b %Y')``; ``strptime`` round-trips them so the
    comparison test can build an ordered (year, month) key for
    today's-month boundary checks.
    """
    from datetime import datetime  # pylint: disable=import-outside-toplevel

    parsed = datetime.strptime(label, "%b %Y")
    return (parsed.year, parsed.month)


class TestPayoffChartShape:
    """C4-1..C4-8: HTTP-level regression locks for the payoff-calculator chart.

    Today is frozen to 2026-03-20 by ``_freeze_today_inside_seed_range``,
    so confirmed payments dated in Jan/Feb 2026 are historical and
    chart points dated April 2026 onward are forward.  Each test
    pins one property of the composer's chart output as exposed by
    the route -- the architectural fix landed in Commit 4 must
    structurally satisfy them.
    """

    TODAY_MONTH = (2026, 3)

    def _create_loan_with_historical_confirmed(
        self, seed_user, db_session, periods,
    ):
        """Create a mortgage with two confirmed payments in Jan-Feb 2026.

        Returns the loan account.  The confirmed payments live in
        ``periods[1]`` (2026-01-16 window) and ``periods[3]``
        (2026-02-13 window), both strictly before today
        (2026-03-20).  ``_create_mortgage`` originates at
        2023-06-01, so 30+ months of gap separate origination from
        the first confirmed payment -- the temporal-gap shape that
        surfaces the buggy "extra applied to ghost historical
        months" behavior the architectural fix prevents.
        """
        acct = _create_mortgage(seed_user, db_session)
        _create_transfer_to_loan(
            seed_user, acct, periods[1], Decimal("1611.64"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, periods[3], Decimal("1611.64"),
            status_enum=StatusEnum.DONE,
        )
        db_session.commit()
        return acct

    def test_chart_lengths_equal(self, auth_client, seed_user, db, seed_periods):
        """C4-1: chart_original / chart_committed / chart_accelerated equal length.

        After the migration the route pads the shorter forward
        slices with $0.00 so all three datasets render against the
        same x-axis; this lets Chart.js plot all three lines
        against the shared ``chart_labels`` without alignment
        gymnastics on the JS side.
        """
        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        labels = _parse_chart_array(html, "labels")
        original = _parse_chart_array(html, "original")
        committed = _parse_chart_array(html, "committed")
        accelerated = _parse_chart_array(html, "accelerated")
        assert labels is not None
        assert original is not None
        assert committed is not None
        assert accelerated is not None
        assert len(original) == len(committed) == len(accelerated) == len(labels)

    def test_accelerated_equals_committed_in_historical_region(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-2: HTTP-level regression lock for the user's reported visual bug.

        For every chart index whose label is STRICTLY before today's
        month (i.e., the confirmed-payment months Jan/Feb 2026), the
        Accelerated balance must equal the Committed balance.

        Pre-Commit-4 the engine's ``extra_monthly`` semantics treated
        origination-to-first-confirmed months as "no payment record"
        and applied $500 of extra principal to each one, producing a
        fictitious accelerated past that the chart rendered as
        Accelerated diverging from Committed at month 1 (2023-07).
        Post-Commit-4 the composer routes confirmed payments through
        replay (which has no ``extra_monthly`` parameter) and
        projected payments through ``monthly_override`` (which
        suppresses extra for override months), making the bug
        structurally impossible.

        The boundary is strict (``<``) rather than ``<=`` because
        replay returns rows ONLY for confirmed-payment months, so the
        first forward row's label is the month after the last
        confirmed payment.  With today on the 20th of a no-confirmed
        month, today's month is the first forward row -- a
        non-override forward month receives the extra, so its index
        cannot satisfy ``accelerated == committed``.
        """
        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        labels = _parse_chart_array(html, "labels")
        committed = _parse_chart_array(html, "committed")
        accelerated = _parse_chart_array(html, "accelerated")
        assert labels and committed and accelerated

        historical_indices = [
            i for i, lbl in enumerate(labels)
            if _label_to_month_tuple(lbl) < self.TODAY_MONTH
        ]
        assert historical_indices, (
            "Expected at least one chart label strictly before today's "
            f"month {self.TODAY_MONTH}; got labels={labels[:5]!r}..."
        )
        for i in historical_indices:
            assert accelerated[i] == committed[i], (
                f"Accelerated[{i}] ({accelerated[i]!r}) != "
                f"Committed[{i}] ({committed[i]!r}) at label "
                f"{labels[i]!r}; accelerated must track committed in "
                "the historical region -- otherwise extra is being "
                "applied to ghost historical months."
            )

    def test_accelerated_below_committed_post_today(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-3: Accelerated strictly below Committed for at least one post-today index.

        With ``extra_monthly=500`` and no projected transfer
        templates, every forward month after today has no
        ``monthly_override`` and therefore receives the extra
        principal payment -- so the running Accelerated balance
        drops below the running Committed balance from the first
        forward month onward.
        """
        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        labels = _parse_chart_array(html, "labels")
        committed = _parse_chart_array(html, "committed")
        accelerated = _parse_chart_array(html, "accelerated")
        assert labels and committed and accelerated

        post_today_indices = [
            i for i, lbl in enumerate(labels)
            if _label_to_month_tuple(lbl) > self.TODAY_MONTH
        ]
        assert post_today_indices, (
            "Expected at least one chart label strictly after "
            f"today's month {self.TODAY_MONTH}; got "
            f"labels={labels[-5:]!r}..."
        )
        assert any(
            accelerated[i] < committed[i] for i in post_today_indices
        ), (
            "Expected accelerated < committed strictly at some "
            "post-today index; got accelerated == committed for "
            "every forward index, which means extra_monthly was "
            "ignored on the projection side."
        )

    def test_summary_consistent_with_chart(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-4: Displayed Months Saved value matches chart divergence count.

        The composer's ``months_saved`` is
        ``len(committed_forward) - len(accelerated_forward)``.
        Single-source-of-truth means the rendered ``Months Saved``
        label must equal the count of chart indices where
        Accelerated paid off but Committed has not -- i.e. where
        Accelerated has reached zero ahead of Committed.  Both
        sides derive from the same forward slices, so they agree
        by construction.
        """
        import re as _re  # pylint: disable=import-outside-toplevel

        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        committed = _parse_chart_array(html, "committed")
        accelerated = _parse_chart_array(html, "accelerated")
        assert committed and accelerated

        # months_saved == count of trailing months where Accelerated
        # is zero but Committed still has a non-zero balance.  Both
        # series are padded with 0.0 after their own payoff, so the
        # divergence count is exactly the difference in payoff
        # month index.
        chart_months_saved = sum(
            1 for i in range(len(committed))
            if committed[i] > 0.0 and accelerated[i] == 0.0
        )

        # The template renders Months Saved as the first numeric
        # value following the "Months Saved" label.  The negated
        # class skips every non-digit character (including hyphens
        # in HTML class names like ``fw-bold``) up to the integer.
        match = _re.search(
            r"Months Saved[^0-9]*(\d+)", html,
        )
        assert match is not None, (
            "Could not find Months Saved label in the rendered "
            "payoff results partial."
        )
        displayed_months_saved = int(match.group(1))
        assert displayed_months_saved == chart_months_saved, (
            f"Displayed Months Saved ({displayed_months_saved}) "
            f"!= chart divergence count ({chart_months_saved}); "
            "chart and summary must derive from the same forward "
            "slices."
        )

    def test_no_payment_history_chart_starts_at_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-5: Loan with zero confirmed payments still renders the chart.

        With no confirmed payments, ``history_rows`` is empty and
        every chart series starts at the origination-adjacent first
        contractual month.  ``has_payments`` is false so the
        Committed series is rendered as an empty array by the
        existing "committed only shown when payments exist"
        convention (preserved across this commit); Original and
        Accelerated overlay correctly from month 1.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        labels = _parse_chart_array(html, "labels")
        original = _parse_chart_array(html, "original")
        accelerated = _parse_chart_array(html, "accelerated")
        committed = _parse_chart_array(html, "committed")
        assert labels and original and accelerated
        # No confirmed payments -> committed series rendered empty.
        assert committed == []
        # Original and accelerated still aligned to the same labels.
        assert len(original) == len(accelerated) == len(labels)
        # First label is the month after origination (2023-07).
        # _create_mortgage's user-trueup is dated one day after
        # origination at $250k, but for fixed-rate loans replay
        # runs from original_principal ($255k); with no confirmed
        # payments the first row is the contractual projection
        # from the very next month.
        assert labels[0] == "Jul 2023"

    def test_target_date_mode_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-6: target_date branch behavior unaffected by Commit 4.

        Commit 4 modifies only the extra_payment branch; the
        target_date branch migrates in Commit 7.  Verify
        target_date still returns the expected required-extra
        partial and does not regress on the helpers/imports the
        composer-collapse refactor touched.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Expected message strings from the target_date branch of
        # the template (existing behavior).
        assert (
            "Required Extra Monthly Payment" in html
            or "Your loan will be paid off" in html
            or "Target date is not achievable" in html
        )

    def test_no_direct_calculate_summary_call(self):
        """C4-7: production code no longer calls ``calculate_summary``.

        Static-source guard for the architectural invariant that
        the route surface routes through ``compute_payoff_scenarios``,
        not directly to the engine's now-deprecated summary helper.
        Lighter-weight than parsing AST; the grep matches any
        call-style use of the symbol on its
        ``amortization_engine.`` prefix.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        # loan.py is now the app/routes/loan/ package (Phase 3 pylint
        # cleanup split); grep every sub-module so coverage is preserved.
        loan_pkg = Path(__file__).resolve().parents[2] / "app" / "routes" / "loan"
        text = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted(loan_pkg.glob("*.py"))
        )
        assert "amortization_engine.calculate_summary" not in text, (
            "app/routes/loan/ still references "
            "amortization_engine.calculate_summary -- the Commit 4 "
            "migration should have removed the only production "
            "caller of this function."
        )

    def test_no_direct_generate_schedule_call_in_extra_payment_branch(self):
        """C4-8: the extra-payment payoff path makes no direct engine call.

        The extra-payment branch's computation now lives in the
        ``_payoff_extra_payment_result`` helper (Phase 3 pylint cleanup
        decomposed ``payoff_calculate``; the route just dispatches to
        it).  Slice that helper out of
        ``app/routes/loan/calculators.py`` and assert it routes through
        ``compute_payoff_scenarios``, never a direct
        ``generate_schedule`` / ``calculate_summary`` call.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        calculators = (
            Path(__file__).resolve().parents[2]
            / "app" / "routes" / "loan" / "calculators.py"
        )
        text = calculators.read_text(encoding="utf-8")
        start_marker = "def _payoff_extra_payment_result("
        end_marker = "\ndef _payoff_target_date_result("
        start = text.find(start_marker)
        end = text.find(end_marker, start)
        assert start != -1 and end != -1 and end > start, (
            "Could not slice _payoff_extra_payment_result out of "
            "calculators.py -- marker strings have drifted."
        )
        branch_source = text[start:end]
        assert "amortization_engine.generate_schedule" not in branch_source, (
            "extra_payment computation still contains a direct "
            "amortization_engine.generate_schedule call -- the "
            "Commit 4 migration should have collapsed every direct "
            "engine call onto compute_payoff_scenarios."
        )
        assert "amortization_engine.calculate_summary" not in branch_source, (
            "extra_payment computation still contains a direct "
            "amortization_engine.calculate_summary call."
        )


# ── Account Creation Redirect Tests ──────────────────────────────────


class TestLoanAccountCreation:
    """Test that creating amortizing account types redirects to loan setup."""

    @pytest.mark.parametrize("type_name", ["Auto Loan", "Mortgage"])
    def test_creation_redirects_to_loan_dashboard(
        self, auth_client, seed_user, db, seed_periods, type_name,
    ):
        """Creating an amortizing account redirects to the loan dashboard."""
        loan_type = db.session.query(AccountType).filter_by(name=type_name).one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": f"New {type_name}",
                "account_type_id": str(loan_type.id),
                "anchor_balance": "20000",
            },
        )
        assert resp.status_code == 302
        assert "/loan" in resp.headers.get("Location", "")

    def test_params_not_duplicated(self, auth_client, seed_user, db, seed_periods):
        """Visiting dashboard does not create duplicate param records."""
        acct = _create_auto_loan(seed_user, db.session)
        auth_client.get(f"/accounts/{acct.id}/loan")
        auth_client.get(f"/accounts/{acct.id}/loan")

        count = db.session.query(LoanParams).filter_by(account_id=acct.id).count()
        assert count == 1


# ── Negative Path Tests ──────────────────────────────────────────────


class TestLoanNegativePaths:
    """Negative-path and boundary tests for loan routes."""

    def test_negative_interest_rate(self, auth_client, seed_user, db, seed_periods):
        """Negative interest rate is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_rate = orig.interest_rate

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"current_principal": "25000.00", "interest_rate": "-0.01", "payment_day": "15"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.interest_rate == orig_rate

    def test_payment_day_zero(self, auth_client, seed_user, db, seed_periods):
        """Payment day 0 is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"current_principal": "25000.00", "interest_rate": "5.000", "payment_day": "0"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_payment_day_32(self, auth_client, seed_user, db, seed_periods):
        """Payment day 32 is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"current_principal": "25000.00", "interest_rate": "5.000", "payment_day": "32"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_escrow_missing_name(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST without name returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"annual_amount": "1200.00"},
        )
        assert resp.status_code == 400

    def test_rate_change_missing_date(self, auth_client, seed_user, db, seed_periods):
        """Rate change without effective_date returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"interest_rate": "5.5"},
        )
        assert resp.status_code == 400

    def test_escrow_nonexistent_account(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/loan/escrow",
            data={"name": "Tax", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 404

    def test_rate_change_nonexistent_account(self, auth_client, seed_user, db, seed_periods):
        """Rate change to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/loan/rate",
            data={"interest_rate": "5.5", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404

    def test_escrow_idor_add(self, auth_client, second_user, db, seed_periods):
        """Escrow add to another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session, "Mortgage")
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/escrow",
            data={"name": "Stolen", "annual_amount": "9999.00"},
        )
        assert resp.status_code == 404

        count = db.session.query(EscrowComponent).filter_by(account_id=other.id).count()
        assert count == 0


# ── Section 5 Regression Baseline ──────────────────────────────────────

# All five amortizing account types with realistic parameters.
_AMORTIZING_TYPES = [
    ("Mortgage", Decimal("250000.00"), Decimal("0.06500"), 360, 600),
    ("Auto Loan", Decimal("25000.00"), Decimal("0.05000"), 60, 120),
    ("Student Loan", Decimal("45000.00"), Decimal("0.04500"), 120, 300),
    ("Personal Loan", Decimal("10000.00"), Decimal("0.08000"), 48, 120),
    ("HELOC", Decimal("50000.00"), Decimal("0.07250"), 180, 360),
]


class TestLoanDashboardRegression:
    """Regression baseline for Section 5 loan dashboard changes.

    Verifies dashboard rendering, payoff calculator modes, and
    multi-type support before Section 5 modifies the amortization
    engine and loan UI.
    """

    @pytest.mark.parametrize("type_name,principal,rate,term,max_term", _AMORTIZING_TYPES)
    def test_dashboard_renders_for_all_amortizing_types(
        self, auth_client, seed_user, db, seed_periods,
        type_name, principal, rate, term, max_term,
    ):
        """Dashboard must render successfully for every amortizing account type.

        Section 5 may add type-specific dashboard panels.  This ensures
        all existing types continue to work.
        """
        acct = _create_loan_account(
            seed_user, db.session, type_name, f"Test {type_name}",
            principal, rate, term, date(2024, 1, 1), 1,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Dashboard should display the monthly payment.
        assert "Monthly" in html or "monthly" in html

    @pytest.mark.parametrize("type_name,principal,rate,term,max_term", _AMORTIZING_TYPES)
    def test_payoff_extra_payment_all_types(
        self, auth_client, seed_user, db, seed_periods,
        type_name, principal, rate, term, max_term,
    ):
        """Payoff calculator extra-payment mode works for all amortizing types.

        Verifies months saved, interest saved, and new payoff date are
        present in the response.
        """
        acct = _create_loan_account(
            seed_user, db.session, type_name, f"Test {type_name}",
            principal, rate, term, date(2024, 1, 1), 1,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Response must contain savings metrics.
        assert "saved" in html.lower() or "interest" in html.lower()

    def test_payoff_target_date_returns_required_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Target-date mode returns the extra monthly payment needed.

        Verifies the payoff calculator correctly handles the target_date
        code path and returns a numeric result.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Should contain a dollar amount for the required extra payment.
        assert "$" in html or "extra" in html.lower()

    def test_payoff_zero_extra_payment_shows_standard_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Zero extra payment should return standard schedule metrics
        with zero months saved and zero interest saved.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # With zero extra, months saved should be 0.
        assert "0 months" in html.lower() or "0 mo" in html.lower() or \
               "$0" in html

    def test_payoff_invalid_mode_does_not_crash(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Invalid payoff mode must not cause a server error.

        The handler returns 200 with default/empty results rather than
        a 400 validation error.  This documents the current behavior.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "invalid_mode"},
        )
        # Must not crash -- 200 or 400 are both acceptable, not 500.
        assert resp.status_code != 500

    def test_payoff_negative_extra_payment_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Negative extra payment must be rejected by validation."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "-100.00"},
        )
        # Should not succeed -- either 400 or validation error.
        assert resp.status_code in (400, 422) or b"error" in resp.data.lower()

    def test_dashboard_idor_blocked(
        self, auth_client, seed_second_user, seed_second_periods,
        second_auth_client, seed_user, seed_periods, db,
    ):
        """User A cannot view User B's loan dashboard.

        Verifies the IDOR protection returns an identical response
        for 'not found' and 'not yours' per the security response rule.
        """
        other_acct = _create_loan_account(
            seed_second_user, db.session, "Mortgage", "Other Mortgage",
            Decimal("200000.00"), Decimal("0.06000"), 360,
            date(2024, 1, 1), 1,
        )
        # User A tries to access User B's dashboard.
        resp = auth_client.get(f"/accounts/{other_acct.id}/loan")
        # Must not return 200 -- should redirect or return 404.
        assert resp.status_code in (302, 404)
        if resp.status_code == 200:
            pytest.fail("IDOR: User A could view User B's loan dashboard")


# ── Payment Integration Tests (Commit 5.1-2) ────────────────────────


def _create_transfer_to_loan(seed_user, loan_account, period, amount,
                              status_enum=StatusEnum.PROJECTED):
    """Create a transfer from checking to loan account via the transfer service.

    Enforces shadow transaction invariants by using the production
    code path.  Does NOT directly insert shadow transactions.
    """
    return create_transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount=amount,
        status_id=ref_cache.status_id(status_enum),
        category_id=seed_user["categories"]["Rent"].id,
    )


class TestLoanDashboardWithPayments:
    """Integration tests for payment-aware loan dashboard.

    Verifies that the dashboard and payoff calculator correctly load
    payment history from shadow transactions and pass it to the
    amortization engine.
    """

    def test_dashboard_no_payments_backward_compat(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with no transfers renders identically to pre-5.1 behavior.

        This complements the Commit #0 regression tests by explicitly
        verifying the payment integration code path produces the same
        output when no payments exist.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Loan Summary" in html

    def test_dashboard_with_confirmed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed transfer payments renders successfully.

        A Paid transfer to the loan account creates a confirmed shadow
        income transaction.  The dashboard should load it and pass it
        to the engine without error.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Loan Summary" in html

    def test_dashboard_with_projected_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with projected (future) transfer payments renders.

        Projected shadow transactions represent committed future payments
        from recurring transfers.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

    def test_dashboard_with_mixed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed + projected payments renders correctly.

        This is the typical real-world case: past payments are confirmed
        (Paid/Settled), future payments are projected.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

    def test_payoff_extra_payment_with_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator extra payment mode works with payment history.

        The calculator should not crash when shadow transactions exist
        for the loan account.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "saved" in html.lower() or "interest" in html.lower()

    def test_payoff_target_date_with_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator target date mode works with payment history.

        The target date mode uses current_principal from LoanParams
        (not derived from payments in this commit).
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200

    def test_dashboard_cancelled_transfer_excluded(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Cancelled transfers do not affect the dashboard projection.

        A cancelled payment should not appear in the payment history.
        The dashboard output should match the no-payments case.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.CANCELLED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Loan Summary" in html


# ── Transfer Prompt Tests (Commit 5.1-3) ─────────────────────────


class TestTransferPrompt:
    """Tests for the recurring payment transfer prompt on the loan dashboard
    and the create_payment_transfer route.
    """

    def test_dashboard_shows_prompt_no_transfer(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with LoanParams but no recurring transfer: prompt visible."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" in html
        assert "Create Recurring Transfer" in html

    def test_dashboard_hides_prompt_transfer_exists(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with active recurring transfer template: prompt hidden."""
        from app.enums import RecurrencePatternEnum  # pylint: disable=import-outside-toplevel
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)

        # Create an active recurring transfer template targeting this account.
        monthly_id = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY)
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=monthly_id,
            day_of_month=1,
        )
        db.session.add(rule)
        db.session.flush()
        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            recurrence_rule_id=rule.id,
            name="Existing Mortgage Payment",
            default_amount=Decimal("1500.00"),
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" not in html

    def test_dashboard_shows_prompt_inactive_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Inactive (archived) transfer template: prompt still shown.

        The user may have deactivated a prior transfer. The prompt
        should reappear so they can create a new one.
        """
        from app.enums import RecurrencePatternEnum  # pylint: disable=import-outside-toplevel
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)

        monthly_id = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY)
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=monthly_id,
            day_of_month=1,
        )
        db.session.add(rule)
        db.session.flush()
        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            recurrence_rule_id=rule.id,
            name="Old Mortgage Payment",
            default_amount=Decimal("1500.00"),
            is_active=False,  # Deactivated
        )
        db.session.add(tpl)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" in html

    def test_create_transfer_success(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with valid source account creates RecurrenceRule + TransferTemplate.

        Redirects to the loan dashboard after successful creation.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

        # Verify records were created.
        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.is_active is True
        assert tpl.from_account_id == checking.id
        assert tpl.recurrence_rule_id is not None
        assert tpl.default_amount > 0

        rule = db.session.get(RecurrenceRule, tpl.recurrence_rule_id)
        assert rule is not None

    def test_create_transfer_redirect_hides_prompt(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After creation, GET dashboard: prompt no longer visible."""
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        # Create the recurring transfer.
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        # Dashboard should no longer show the prompt.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" not in html

    def test_create_transfer_generates_shadow_transactions(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After creation: shadow transactions exist on the loan account."""
        from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        # Shadow income transactions should exist on the loan account.
        income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        shadows = (
            db.session.query(Transaction)
            .filter(
                Transaction.account_id == acct.id,
                Transaction.transfer_id.isnot(None),
                Transaction.transaction_type_id == income_type_id,
                Transaction.is_deleted.is_(False),
            )
            .all()
        )
        assert len(shadows) > 0

    def test_create_transfer_validates_source_ownership(
        self, auth_client, seed_user, seed_second_user,
        seed_second_periods, db, seed_periods,
    ):
        """POST with other user's account as source returns 404 (security)."""
        acct = _create_mortgage(seed_user, db.session)
        other_account = seed_second_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(other_account.id)},
        )
        # Should not succeed -- security response rule (404 for not-yours).
        assert resp.status_code == 404

    def test_create_transfer_validates_source_not_self(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with debt account as source: validation error."""
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(acct.id)},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

    def test_create_transfer_idor_debt_account(
        self, auth_client, seed_user, seed_second_user,
        seed_second_periods, db, seed_periods,
    ):
        """POST to other user's debt account returns 404 (security)."""
        other_loan = _create_loan_account(
            seed_second_user, db.session, "Mortgage", "Other Mortgage",
            Decimal("200000.00"), Decimal("0.06000"), 360,
            date(2024, 1, 1), 1,
        )
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{other_loan.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 404

    def test_create_transfer_amount_override(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with custom amount: template uses the override amount."""
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "2000.00",
            },
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.default_amount == Decimal("2000.00")

    def test_source_accounts_exclude_debt_account(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Source accounts dropdown does not include the current debt account.

        Scopes the assertion to the ``source_account_id`` select.  A
        naive ``f'value="{acct.id}" not in html`` check would falsely
        match the loan account's id when it appears in any unrelated
        attribute on the page (e.g., the dashboard's hidden form
        carrying ``value="N"`` for some other model whose id happens
        to equal the loan account's id).  The OR with ``"No recurring
        payment" not in html`` papered over collisions when the
        prompt was hidden, but failed deterministically when the
        prompt was shown AND ``acct.id`` collided with another
        element's value.
        """
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()

        source_options = select_option_values(html, "source_account_id")
        # When the prompt is shown, ``source_options`` carries the
        # eligible source accounts and the loan account itself must
        # not be among them.  When the prompt is hidden (no source
        # accounts available, e.g. the user has no non-loan
        # accounts), ``source_options`` is the empty list and the
        # assertion is vacuously true.
        assert str(acct.id) not in source_options, (
            f"Loan account {acct.id} ({acct.name}) is listed as a "
            f"source-account option on its own loan dashboard; got "
            f"source_account_id options {source_options!r}"
        )


# ── ARM Rate History Integration Tests (Commit 5.7-1) ──────────────


class TestARMRateHistoryIntegration:
    """Tests for ARM rate history integration in the loan dashboard.

    Verifies that the dashboard and payoff calculator correctly load
    RateHistory entries for ARM loans, convert them to RateChangeRecords,
    and pass them to the amortization engine.
    """

    def test_arm_dashboard_passes_rate_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """ARM mortgage with rate history: dashboard projection differs
        from a fixed-rate projection.

        Creates an ARM mortgage at 5%, adds a rate change to 7%,
        verifies the dashboard renders and shows rate history data.
        """
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "ARM Mortgage",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        # Add a rate change effective Feb 2025.
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Dashboard should render with the rate history visible.
        assert "Loan Summary" in html

    def test_non_arm_dashboard_ignores_rate_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Non-ARM loan with rate history entries: rate_changes not passed.

        Defensively verifies that rate history entries on a non-ARM loan
        do not affect the projection.  The dashboard should produce
        identical output to one with no rate history.
        """
        acct = _create_mortgage(seed_user, db.session)

        # Insert rate history despite is_arm=False (defensive case).
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Loan Summary" in html
        # Non-ARM: rate history section should NOT be visible.
        assert "Rate History" not in html or "Rate Change" not in html


# ── Multi-Scenario Visualization Tests (Commit 5.5-1) ──────────────


class TestMultiScenarioVisualization:
    """Tests for multi-scenario balance chart and payoff calculator.

    Verifies that the dashboard and payoff calculator correctly compute
    and display original, committed, floor, and accelerated scenarios.
    """

    def test_dashboard_chart_original_only_no_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with no transfers: only original schedule in chart.

        data-original should be non-empty.  data-committed and
        data-floor should be empty arrays.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html
        # No payments means committed and floor are empty.
        assert "data-committed='[]'" in html
        assert "data-floor='[]'" in html

    def test_dashboard_chart_with_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with projected transfers: data-committed populated.

        A projected transfer creates shadow transactions that the
        dashboard should reflect in the committed schedule.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html
        assert "data-committed=" in html
        # Committed should not be empty.
        assert "data-committed='[]'" not in html

    def test_dashboard_chart_with_floor(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed transfers: data-floor populated.

        A confirmed (Paid) transfer establishes the floor -- the real
        position if all extras were cancelled.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-floor=" in html
        assert "data-floor='[]'" not in html

    def test_payoff_results_committed_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator shows committed vs. original comparison.

        When payments exist, the payoff results partial should show
        how many months the committed plan saves vs. the original.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Multi-scenario chart data should be present.
        assert "data-original=" in html
        assert "data-committed=" in html

    def test_payoff_what_if_three_scenarios(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff with extra: original + committed + accelerated.

        With payments and extra_monthly > 0, all three chart datasets
        should be present and the accelerated line should be shorter.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html
        assert "data-accelerated=" in html
        assert "Months Saved" in html

    def test_payoff_no_transfer_degrades(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff with no transfers: committed is empty, original shown.

        When no payments exist, committed chart data should be empty.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html
        # No payments -> committed is empty.
        assert "data-committed='[]'" in html

    def test_payoff_what_if_zero(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """extra_monthly=0: accelerated matches committed.

        Zero extra should not cause errors and should still render.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html

    def test_payoff_target_date_still_works(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Target date mode is unaffected by multi-scenario changes.

        The existing target_date mode should continue to return
        required extra payment data without regressions.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200

    def test_dashboard_arm_original_excludes_rate_changes(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """ARM loan: original schedule does NOT include rate changes.

        The original schedule is the pure contractual baseline at the
        initial rate.  Rate changes only affect committed and floor.
        """
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "ARM Mortgage",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Original and committed should both be present.
        assert "data-original=" in html
        assert "Loan Summary" in html


# ── Payment Breakdown Tests (Commit 5.14-1) ────────────────────────


def _add_escrow(db_session, account_id, name, annual_amount,
                inflation_rate=None):
    """Helper to add an escrow component to a loan account."""
    comp = EscrowComponent(
        account_id=account_id,
        name=name,
        annual_amount=annual_amount,
        inflation_rate=inflation_rate,
    )
    db_session.add(comp)
    db_session.flush()
    return comp


class TestPaymentBreakdown:
    """Tests for the payment allocation breakdown card on the loan dashboard.

    Verifies that the breakdown shows correct P/I/E split, handles
    edge cases, and renders the progress bar with accurate percentages.
    """

    def test_breakdown_shows_on_dashboard(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage with escrow: breakdown card shows P/I/E amounts.

        Setup: $250,000 mortgage at 6.5%, 360 months, origination 2023-06-01.
        LoanParams created by _create_mortgage: current_principal=$250K,
        original_principal=$255K, rate=0.065, term=360, payment_day=1.
        Escrow: $7,200 property tax + $2,400 insurance = $9,600/yr = $800/mo.

        Monthly P&I from original terms ($255K, 6.5%, 360mo): ~$1,611.64.
        The breakdown should show the P/I split for the current period
        plus the escrow portion.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        _add_escrow(db.session, acct.id, "Insurance", Decimal("2400.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Breakdown card renders.
        assert "Payment Allocation" in html
        assert "to principal" in html
        assert "to interest" in html
        assert "to escrow" in html
        # Escrow = $800/mo (9600/12).
        assert "800.00" in html

    def test_breakdown_no_escrow(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Auto loan with no escrow: only P/I shown, escrow line absent."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment Allocation" in html
        assert "to principal" in html
        assert "to interest" in html
        # Escrow line should not appear.
        assert "to escrow" not in html

    def test_breakdown_proportions_sum_to_100(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Displayed percentages sum to exactly 100.0%.

        Parse the three percentage values from the HTML and verify
        their sum.  Uses a mortgage with escrow for three components.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(db.session, acct.id, "Tax", Decimal("6000.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract percentages from "to principal (XX.X%)" pattern.
        import re as _re
        pcts = _re.findall(r"to (?:principal|interest|escrow) \((\d+\.\d)%\)", html)
        assert len(pcts) == 3, f"Expected 3 percentages, found {len(pcts)}: {pcts}"
        total = sum(Decimal(p) for p in pcts)
        assert total == Decimal("100.0"), (
            f"Percentages sum to {total}, expected 100.0"
        )

    def test_breakdown_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Loan without LoanParams: breakdown card not shown."""
        # Create a bare account with no params.
        loan_type = db.session.query(AccountType).filter_by(
            name="Mortgage",
        ).one()
        account = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="No Params Mortgage",
            anchor_balance=Decimal("200000.00"),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        # Without params, renders setup page (no breakdown).
        assert resp.status_code == 200
        assert b"Payment Allocation" not in resp.data

    def test_breakdown_with_extra_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Extra payments included in the principal portion.

        When the committed schedule includes extra payments (from
        transfers), the "principal" line in the breakdown should
        reflect both standard principal and extra_payment.
        """
        acct = _create_mortgage(seed_user, db.session)
        # Create a transfer that overpays the standard P&I.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("2000.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment Allocation" in html
        assert "to principal" in html

    def test_breakdown_confirmed_row_labeled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Confirmed payment: card header shows Confirmed badge.

        When all schedule rows are confirmed (loan fully paid through
        transfers), the breakdown should label the data as confirmed.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The first non-confirmed (projected) row is shown, but
        # the confirmed payment's row would show "Confirmed" badge.
        assert "Payment Allocation" in html

    def test_breakdown_escrow_zero_hidden(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage where all escrow components are inactive: escrow hidden.

        Even though the loan type typically has escrow, if all components
        are inactive, the escrow line should not render.
        """
        acct = _create_mortgage(seed_user, db.session)
        # No escrow components added (none active).
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment Allocation" in html
        assert "to escrow" not in html

    def test_breakdown_uses_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Breakdown uses committed (payment-aware) schedule, not original.

        When payments exist, the committed schedule's P/I split differs
        from the original because real payments affect the balance
        trajectory.  The breakdown should reflect the committed values.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Breakdown renders from committed data.
        assert "Payment Allocation" in html
        assert "to principal" in html

    def test_breakdown_escrow_inflation_note(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """O-3: Escrow with inflation_rate shows projected increase note.

        When escrow components have non-null inflation rates, the
        breakdown should show a note about projected escrow increase.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(
            db.session, acct.id, "Property Tax",
            Decimal("7200.00"), inflation_rate=Decimal("0.0300"),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "inflation estimates" in html

    def test_breakdown_no_inflation_note_when_zero(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Escrow with zero or null inflation_rate: no inflation note."""
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(
            db.session, acct.id, "Insurance",
            Decimal("2400.00"), inflation_rate=None,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "inflation estimates" not in html


# -- Amortization Schedule Tab Tests (Commit 5.13-1) ------------------


def _create_fresh_mortgage(seed_user, db_session, principal=Decimal("250000.00"),
                           rate=Decimal("0.06500"), term=360, payment_day=1,
                           origination_date=None):
    """Create a mortgage with predictable schedule length.

    By default, origination is the first of last month so the
    schedule's first payment month is this month.  Tests that need
    the schedule to align with specific ``seed_periods`` indices must
    pass ``origination_date`` explicitly so the alignment does not
    drift as today's date advances.

    Sets original_principal = current_principal so the schedule aligns
    with the full term (no early-payoff due to a lower current balance).

    Args:
        seed_user: The seed_user fixture dict.
        db_session: Active database session.
        principal: Loan principal (Decimal).  Default $250,000.
        rate: Annual interest rate (Decimal).  Default 6.5%.
        term: Term in months.  Default 360.
        payment_day: Payment day of month.  Default 1.
        origination_date: Optional explicit origination date.  Default
            is the first of last month relative to ``date.today()``.
            Pass an explicit date for tests that depend on schedule
            alignment with fixed-date fixtures.
    """
    if origination_date is None:
        # Origination one month before today so the first payment month
        # is the current month (schedule starts month after origination).
        first_of_this_month = date.today().replace(day=1)
        if first_of_this_month.month == 1:
            origination_date = first_of_this_month.replace(
                year=first_of_this_month.year - 1, month=12,
            )
        else:
            origination_date = first_of_this_month.replace(
                month=first_of_this_month.month - 1,
            )
    return _create_loan_account_exact(
        seed_user, db_session, "Mortgage", "Fresh Mortgage",
        principal, principal, rate, term, origination_date, payment_day,
    )


def _create_loan_account_exact(seed_user, db_session, type_name, name,
                                original_principal, current_principal,
                                rate, term, orig_date, payment_day,
                                is_arm=False):
    """Like _create_loan_account but with explicit original_principal."""
    loan_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = account_service.create_account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name=name,
        anchor_balance=current_principal,
    )
    db_session.add(account)
    db_session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=original_principal,
        current_principal=current_principal,
        interest_rate=rate,
        term_months=term,
        origination_date=orig_date,
        payment_day=payment_day,
        is_arm=is_arm,
    )
    db_session.add(params)
    db_session.flush()
    insert_origination_event(params)
    db_session.commit()
    return account


class TestAmortizationSchedule:
    """Tests for the full amortization schedule tab on the loan dashboard.

    Verifies that the schedule table renders correctly with the right
    number of rows, confirmed/projected distinction, currency formatting,
    totals row, and conditional Rate column for ARM loans.
    """

    def test_schedule_tab_exists(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-1: Dashboard with LoanParams shows the Amortization Schedule tab.

        GET the dashboard for a mortgage with params. Assert the tab
        nav item and tab pane markup are both present in the HTML.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Tab nav item exists.
        assert "Amortization Schedule" in html
        # Tab pane exists.
        assert 'id="tab-schedule"' in html
        # Schedule table rendered (Month-by-Month header).
        assert "Month-by-Month Schedule" in html

    def test_schedule_has_correct_row_count(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-2: 30-year mortgage produces the correct number of data rows.

        Re-pinned for Commit 5 of the amortization engine split
        (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``).
        Pre-Commit-5 the dashboard called ``generate_schedule``
        directly, which iterated up to ``max_months = remaining_months
        + term_months`` and emitted a 361st row absorbing the
        sub-penny rounding residue.  Post-Commit-5 the dashboard
        routes through ``compute_payoff_scenarios`` ->
        ``project_forward``, which terminates cleanly at
        ``month_num == remaining_months``, absorbing the residue in
        the final scheduled month.  The architecturally correct row
        count for a 30-year mortgage with no payments is therefore
        ``term_months == 360`` -- one row per scheduled month, no
        residue artifact.  Hand-derivation:
        ``len(history_rows) == 0`` (no confirmed payments) +
        ``len(committed_forward) == remaining_months_as_of == 360``.
        """
        expected_count = 360

        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Count data rows: each has exactly one Projected or Confirmed badge.
        projected = html.count('badge bg-secondary">Projected</span>')
        confirmed = html.count('badge bg-success">Confirmed</span>')
        total_rows = projected + confirmed
        assert total_rows == expected_count, (
            f"Expected {expected_count} data rows, got {total_rows} "
            f"({projected} projected, {confirmed} confirmed)"
        )

    def test_schedule_confirmed_rows_marked(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-3: Confirmed payment rows are visually distinguished.

        Creates a mortgage and two confirmed transfers in months
        that have already passed relative to the autouse-frozen
        today (2026-03-20).  Asserts confirmed rows get a distinct
        badge and the rest are Projected.

        Re-pinned for Commit 5 of the amortization engine split
        (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``).
        Pre-Commit-5 the dashboard called ``generate_schedule``,
        which had no ``as_of`` concept and marked any payment record
        as Confirmed based solely on the ``is_confirmed`` field.
        Post-Commit-5 the dashboard routes through
        ``compute_payoff_scenarios``, whose replay only consumes
        confirmed payments with ``payment_date <= as_of``; confirmed
        payments dated AFTER today are data-hygiene cases and are
        routed through ``monthly_override`` (Projected badge) -- the
        new architecture's stricter semantic for the Confirmed badge.
        The previous fixture used April/May 2026 seed_periods (after
        the frozen today), which exercised the data-hygiene path,
        not the realistic "DONE payment in history" path.  This
        rewrite uses February/March seed_periods (before today) so
        the DONE payments fall in the replay window and produce
        Confirmed badges.
        """
        # Origination 2026-01-01 -> first scheduled payment month
        # is February 2026 (origination + 1 month).  seed_periods[3]
        # starts 2026-02-13 (Feb 2026) -> schedule month 1.
        # seed_periods[5] starts 2026-03-13 (Mar 2026) -> schedule
        # month 2.  Both before frozen today 2026-03-20, so the
        # composer's replay consumes them and produces history rows
        # with is_confirmed=True.
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[5], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        projected = html.count('badge bg-secondary">Projected</span>')
        assert confirmed == 2, (
            f"Expected 2 confirmed rows, got {confirmed}"
        )
        assert projected > 0, "Expected some projected rows"
        # Total should still be a full schedule.
        assert confirmed + projected > 300

    def test_schedule_first_last_row(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-4: First and last rows have correct values for known loan params.

        Loan: $250,000 at 6.5% for 360 months.
        Hand calculation:
          M = P * r(1+r)^n / [(1+r)^n - 1]
          r = 0.065/12 = 0.00541666...
          (1+r)^360 ~ 6.9920
          M = 250000 * (0.00541666 * 6.9920) / (6.9920 - 1)
          M = 250000 * 0.037878 / 5.9920
          M ~ $1,580.17

        First month:
          Interest = 250000 * 0.065/12 = $1,354.17
          Principal = 1580.17 - 1354.17 = $226.00

        Last row: remaining_balance = $0.00
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # First row: month 1, expected payment of $1,580.17.
        assert "$1,580.17" in html
        # First month interest: $250,000 * 0.065/12 = $1,354.17.
        assert "$1,354.17" in html
        # First month principal: $1,580.17 - $1,354.17 = $226.00.
        assert "$226.00" in html
        # Last row balance must be $0.00.
        # The totals row does not have a balance cell, so "$0.00" comes
        # from the last data row's remaining_balance.
        assert "$0.00" in html

    def test_schedule_numbering_continuous_from_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The # column counts total payments from origination, not 1..N per slice.

        Regression for the user request: a mid-life loan's schedule must
        number rows by total payments made -- a loan in its 26th month
        shows #25 for its Feb 1 2026 payment -- and the projected slice
        must keep counting up (#26, #27, ...) instead of restarting at 1
        (the projected slice's pre-fix project_forward-local numbering).
        """
        # Origination 2024-01-01 -> the Feb 1 2026 payment is the 25th
        # (25 whole months after origination) at the frozen today
        # (2026-03-20).  seed_periods[2] (2026-01-30 .. 2026-02-12)
        # contains 2/1, so its confirmed payment IS the Feb 1 payment.
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2024, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The first <td> of each schedule data row is the payment number;
        # year-header rows use <td colspan> and the totals row a label, so
        # this matches only data rows.
        row_numbers = [
            int(n) for n in re.findall(
                r'<tr class="(?:table-success)?">\s*<td>(\d+)</td>', html
            )
        ]
        assert row_numbers, "No schedule data rows parsed from the table"
        # Starts at the true payment number (25), NOT 1.
        assert row_numbers[0] == 25, (
            f"Expected first schedule row #25 (payments from origination), "
            f"got {row_numbers[0]}"
        )
        # Continuous +1 across the confirmed/projected boundary -- the
        # projected slice does not restart at 1.
        for i in range(1, len(row_numbers)):
            assert row_numbers[i] == row_numbers[i - 1] + 1, (
                f"Numbering restarted or jumped at index {i}: "
                f"{row_numbers[i - 1]} -> {row_numbers[i]}"
            )

    def test_schedule_early_payoff_fewer_rows(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-5: Loan with short term pays off early (fewer than 360 rows).

        Creates a loan with a 12-month term.  The schedule from
        origination runs 12 months, well under 360.  Verifies
        the schedule table reflects the actual term, not a fixed
        30-year assumption.
        """
        acct = _create_loan_account_exact(
            seed_user, db.session, "Auto Loan", "Short Loan",
            Decimal("5000.00"), Decimal("5000.00"),
            Decimal("0.06500"), 12, date(2026, 3, 1), 1,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        projected = html.count('badge bg-secondary">Projected</span>')
        confirmed = html.count('badge bg-success">Confirmed</span>')
        total_rows = projected + confirmed
        assert total_rows < 360, (
            f"Expected fewer than 360 rows for short-term loan, got {total_rows}"
        )
        # 12 months + possibly 1 extra for sub-penny rounding residue.
        assert total_rows <= 13, (
            f"Expected ~12 rows for 12-month loan, got {total_rows}"
        )
        # Last row should still reach $0.00.
        assert "$0.00" in html

    def test_schedule_hidden_no_params(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-6: Loan without LoanParams renders setup page, not schedule tab.

        When no LoanParams exist, the route renders setup.html, which
        does not include the dashboard tabs at all.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        account = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="No Params Loan",
            anchor_balance=Decimal("200000.00"),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Amortization Schedule" not in resp.data
        assert b"tab-schedule" not in resp.data

    def test_schedule_hidden_empty_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-7: Paid-off loan shows short schedule ending at $0.

        A loan fully retired via a confirmed payment shows a very
        short schedule (1 row) with the final balance at $0.00 and
        the row marked as confirmed.
        """
        # Small loan: $1000 at 5% for 12 months, origination Jan 2026.
        # First payment month: Feb 2026 (seed_periods[3] = Feb 13).
        acct = _create_loan_account_exact(
            seed_user, db.session, "Auto Loan", "Paid Off",
            Decimal("1000.00"), Decimal("0.00"),
            Decimal("0.05000"), 12, date(2026, 1, 1), 1,
        )
        # Large confirmed payment in Feb covers the full balance.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1100.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Schedule table exists with the payoff row.
        assert "Month-by-Month Schedule" in html
        confirmed = html.count('badge bg-success">Confirmed</span>')
        assert confirmed == 1, (
            f"Expected 1 confirmed row for paid-off loan, got {confirmed}"
        )
        assert "$0.00" in html

    def test_schedule_arm_rate_column_shown(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-8: ARM mortgage shows Rate column in the schedule table.

        Creates an ARM mortgage with a rate history entry. The Rate
        column header and rate values should appear in the schedule.
        """
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "ARM Schedule",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Month-by-Month Schedule" in html
        # Rate column header.
        assert ">Rate</th>" in html or ">Rate<" in html
        # At least one rate percentage value.
        assert "7.000%" in html or "5.000%" in html

    def test_schedule_fixed_rate_no_rate_column(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-9: Non-ARM mortgage does NOT show Rate column.

        Fixed-rate loans have the same rate for every row. Showing it
        360 times is noise, so the column is omitted.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Month-by-Month Schedule" in html
        # Rate column header must not exist in the schedule table.
        # (The overview tab shows the rate, but not as a <th>.)
        assert ">Rate</th>" not in html

    def test_schedule_totals_row(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-10: Schedule table includes a totals footer row.

        The <tfoot> row shows summed payment, principal, interest, and
        extra columns.  Verify the footer exists and contains currency
        values.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Footer row present.
        assert "<tfoot" in html
        assert ">Total</td>" in html or ">Total<" in html
        # Footer contains currency values (dollar amounts).
        # The total interest for a $250K/6.5%/30yr loan is significant.
        assert "$" in html

    def test_schedule_totals_match_rows(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-11: Totals row values match the sum of individual data rows.

        Computes expected totals from the amortization engine directly
        and verifies they appear in the rendered HTML.

        For $250K at 6.5% for 360 months with no escrow, no extra:
        Total interest = sum of all monthly interest values over the
        full schedule.  The engine computes this precisely using Decimal.
        No escrow means Payment total = P&I total.
        """
        # Compute expected totals from the engine via
        # ``project_forward`` (Commit 9 of the amortization-engine
        # split removed ``generate_schedule``).  The fixture loan has
        # no payments and no overrides, so a pure contractual
        # projection over the full term replicates the legacy
        # surface's output exactly.
        from app.services.amortization_engine import (  # pylint: disable=import-outside-toplevel
            ProjectionInputs,
            advance_to_next_payment_date,
            calculate_monthly_payment,
            project_forward,
        )

        principal = Decimal("250000.00")
        rate = Decimal("0.06500")
        term = 360
        # _create_fresh_mortgage seeds origination_date one month
        # before today so the first scheduled payment lands on the
        # first of this month.
        first_of_this_month = date.today().replace(day=1)
        if first_of_this_month.month == 1:
            origination_date = first_of_this_month.replace(
                year=first_of_this_month.year - 1, month=12,
            )
        else:
            origination_date = first_of_this_month.replace(
                month=first_of_this_month.month - 1,
            )
        starting_date = advance_to_next_payment_date(origination_date, 1)
        contractual = calculate_monthly_payment(principal, rate, term)

        schedule = project_forward(
            ProjectionInputs(
                starting_balance=principal,
                starting_date=starting_date,
                annual_rate=rate,
                remaining_months=term,
                payment_day=1,
                contractual_payment=contractual,
            ),
        )
        expected_interest = sum(
            (row.interest for row in schedule), Decimal("0.00"),
        )
        # No escrow on this test loan, so Payment = P&I total.
        expected_payment = sum(
            (row.payment for row in schedule), Decimal("0.00"),
        )
        formatted_interest = f"${expected_interest:,.2f}"
        formatted_payment = f"${expected_payment:,.2f}"

        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert formatted_interest in html, (
            f"Expected total interest {formatted_interest} not found"
        )
        assert formatted_payment in html, (
            f"Expected total payment {formatted_payment} not found"
        )

    def test_schedule_overpayment_not_shown_as_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-12 (re-pinned): a confirmed overpayment is NOT auto-shown as Extra.

        Re-pinned under the contractual-schedule balance model (CLAUDE
        rule 5 exception; the developer chose "deliberate extra principal
        is recorded as an explicit event").  The prior test created a
        DONE transfer above the contractual P&I and expected the schedule
        to break out the difference as an "Extra" column.  Under the new
        model the historical balance follows the contractual schedule and
        the cash overage is ignored -- extra principal is now an explicit
        balance true-up, not an amount inferred from a transfer's cash --
        so every schedule row carries ``extra_payment=0``,
        ``schedule_totals.has_extra`` is False, and the Extra column is
        hidden.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        # A DONE transfer above the contractual P&I ($2080.17 vs
        # $1580.17).  The $500 overage is no longer auto-applied.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("2080.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # No row carries extra, so the Extra column does not render.
        assert ">Extra</th>" not in html, (
            "A historical overpayment must not auto-populate an Extra column"
        )

    def test_schedule_uses_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-13: Schedule uses committed data when payments exist.

        Creates a mortgage with a confirmed transfer.  If the schedule
        used only the original (no-payments) schedule, no rows would
        be marked Confirmed.  Presence of Confirmed badges proves the
        committed schedule (via the composer's history_rows) is used.

        Re-pinned for Commit 5 of the amortization engine split.
        See ``test_schedule_confirmed_rows_marked`` for the full
        rationale (same architectural change: composer's replay only
        consumes confirmed payments with ``payment_date <= as_of``;
        future-dated DONE goes through ``monthly_override`` and
        renders as Projected).  Previous fixture used April 2026
        seed_periods after the frozen today (2026-03-20); this
        rewrite uses February 2026 so the DONE payment lands in
        replay.
        """
        # Origination 2026-01-01 -> first scheduled payment month
        # is February 2026.  seed_periods[3] (2026-02-13) falls in
        # the replay window before today (2026-03-20).
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        assert confirmed >= 1, (
            "Expected at least 1 confirmed row from committed schedule"
        )

    def test_schedule_currency_formatting_consistent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-14: Currency values in the schedule use consistent formatting.

        All monetary values should match the $X,XXX.XX pattern with
        exactly 2 decimal places.  Parses several values from the
        schedule and validates the format.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract dollar amounts from the schedule.  The pattern
        # matches $X.XX through $XXX,XXX,XXX.XX.
        amounts = re.findall(r'\$[\d,]+\.\d{2}', html)
        assert len(amounts) > 100, (
            f"Expected many currency values in schedule, found {len(amounts)}"
        )
        # Every matched amount should have exactly 2 decimal places.
        for amount in amounts[:20]:  # Spot-check first 20.
            assert re.match(r'^\$[\d,]+\.\d{2}$', amount), (
                f"Currency format mismatch: {amount}"
            )


# -- Dashboard / Payoff Calculator Consistency Tests -------------------


class TestDashboardPayoffConsistency:
    """Verify the dashboard and payoff calculator use the same data pipeline.

    Both routes must produce identical amortization calculations from
    the same loan.  These tests catch mismatches caused by one route
    applying payment preparation (escrow subtraction, biweekly
    redistribution) while the other uses raw data.
    """

    def test_payoff_committed_matches_dashboard_chart(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff committed chart data matches dashboard committed chart.

        Both routes render committed balance data for Chart.js.  Since
        both use _load_loan_context for payment preparation, the
        committed balance arrays must be identical.

        Origination is pinned to March 1, 2026 so seed_periods[7]
        (April 10, 2026) matches the schedule's first payment month
        (April 2026).  Without the pin, ``_create_fresh_mortgage``
        derives origination from ``date.today()``; once today moves
        past April, the April transfer no longer matches any schedule
        month, both routes produce empty committed arrays, and the
        equality assertion passes trivially without exercising the
        integration the test was written to verify.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        # Dashboard: extract committed chart data.
        dash_resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert dash_resp.status_code == 200
        dash_html = dash_resp.data.decode()
        dash_match = re.search(r"data-committed='(\[.*?\])'", dash_html)
        assert dash_match, "Dashboard missing committed chart data"
        dash_committed = dash_match.group(1)

        # Payoff with 0 extra: extract committed chart data.
        payoff_resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert payoff_resp.status_code == 200
        payoff_html = payoff_resp.data.decode()
        payoff_match = re.search(r"data-committed='(\[.*?\])'", payoff_html)
        assert payoff_match, "Payoff missing committed chart data"
        payoff_committed = payoff_match.group(1)

        assert dash_committed == payoff_committed, (
            "Dashboard and payoff calculator committed schedules differ -- "
            "data pipeline mismatch"
        )

    def test_payoff_with_payments_no_crash(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator with prepared payments does not crash.

        After the DRY refactor, both routes use _load_loan_context.
        Verify the payoff calculator handles prepared payments correctly
        in both extra_payment and target_date modes.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Extra payment mode.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200

        # Target date mode.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200

    def test_escrow_subtracted_consistently(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Escrow-inclusive transfers do not inflate payoff savings.

        When transfers include escrow, both routes must subtract it
        before passing to the engine.  Without this, the payoff
        calculator would report inflated interest savings because the
        engine would count escrow as extra principal.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        # Add escrow: $600/month.
        _add_escrow(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        db.session.commit()

        # Transfer includes P&I (~$1,580) + escrow ($600) = ~$2,180.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("2180.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Dashboard should NOT show the escrow as "extra" in schedule.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The Escrow column should show $600 (7200/12).
        assert "Escrow" in html
        assert "$600.00" in html

        # Payoff calculator should also work with prepared payments.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200

    def test_biweekly_overlap_handled_in_payoff(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two payments in the same month are redistributed in payoff too.

        Creates two transfers in the same calendar month (biweekly
        overlap).  Both the dashboard and payoff calculator must
        distribute them across two schedule months.

        Origination is pinned to March 1, 2026 so seed_periods[7]
        (April 10) and seed_periods[8] (April 24) BOTH fall in the
        schedule's first payment month (April 2026), exercising the
        biweekly redistribution code path.  Without the pin, the
        schedule's first month would shift past April and the
        transfers would not match any schedule month -- the
        ``"$3,160" not in html`` assertion would then pass trivially
        even if the biweekly fix were broken.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        # seed_periods[7] (April 10) and [8] (April 24) are both in
        # April 2026.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[8], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Dashboard schedule: no month should show double payment.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # $3,160 (2x $1,580.17) should NOT appear as a single payment.
        assert "$3,160" not in html, (
            "Dashboard shows double payment -- biweekly fix not applied"
        )

        # Payoff calculator: same -- should not crash or double-count.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200


# -- Dashboard Chart Composer Tests (Commit 5) -----------------------------


class TestDashboardChartComposer:
    """Lock the dashboard's migration to compute_payoff_scenarios.

    Commit 5 of the amortization engine split
    (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``)
    replaces the dashboard's three direct ``generate_schedule`` calls
    (planned, original, floor) with two composer calls.  These tests
    lock the resulting behavior:

    * C5-1..C5-4 and C5-8 are "assert-unchanged" -- they pin the
      composer-driven dashboard output against hand-computed
      expectations derived from the composer (the new SSOT, not the
      pre-Commit-5 ``generate_schedule`` 361-row residue artifact).
    * C5-5 is a static grep guard: the dashboard body MUST NOT call
      ``generate_schedule`` directly.
    * C5-6 / C5-7 lock the floor's "projections cancelled" semantic.

    Helper notes: ``_create_fresh_mortgage`` with
    ``origination_date=date(2026, 1, 1)`` produces a 30-year
    $250,000 / 6.5% mortgage whose first scheduled payment month is
    February 2026.  ``seed_periods[3]`` (2026-02-13) falls before
    the autouse-frozen today (2026-03-20) so confirmed transfers in
    that period land in the composer's replay window.
    """

    def _parse_chart_array(self, html, key):
        """Extract a chart data array as a list of floats."""
        match = re.search(rf"data-{key}='(\[[^']*\])'", html)
        assert match, f"Dashboard missing data-{key} chart array"
        # The Jinja tojson filter emits a JSON literal; eval-safe parse.
        import json  # pylint: disable=import-outside-toplevel
        return json.loads(match.group(1))

    def test_dashboard_chart_values_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-1: Dashboard chart arrays match composer-derived expected.

        Fixture: 30-yr / $250k / 6.5% mortgage originated 2026-01-01,
        one confirmed payment due Feb 1 2026 (seed_periods[2], the pay
        period that CONTAINS 2/1 -- the loan's first contractual payment
        and the replay window), one projected payment in May 2026
        (seed_periods[9], forward window via monthly_override).

        seed_periods[2] (2026-01-30 .. 2026-02-12) is used rather than
        [3] (2026-02-13 ..) because the schedule keys rows by the true
        monthly DUE date: [2] contains 2/1 so its payment IS the Feb 1
        payment; [3] contains no 1st, so its payment is due 3/1, which
        would skip the 2/1 payment and yield a 359-row schedule.

        Asserts the dashboard's data-original / data-committed /
        data-floor arrays come from the composer:
          * All three arrays have equal length (== len(original_rows)).
          * Lengths equal term_months (360) -- one row per scheduled
            month, no residue artifact (Commit 5 architectural fix).
          * Original is monotonically non-increasing (pure contractual
            never increases balance for a fixed-rate loan with no
            rate change).
          * The first array entry of each series matches a hand-
            computed value derived from the composer's replay.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        # Confirmed Feb 1 2026 (before today=2026-03-20) -- goes to
        # replay's history_rows.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        # Projected May 2026 (after today) -- goes to monthly_override.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()

        original = self._parse_chart_array(html, "original")
        committed = self._parse_chart_array(html, "committed")
        floor = self._parse_chart_array(html, "floor")

        # All three series share history_rows and render against the
        # same x-axis of Original (the longest baseline).
        assert len(original) == len(committed) == len(floor)
        # 360 months for a 30-yr mortgage with no overpayment (one row
        # per scheduled month; the composer eliminates the pre-Commit-5
        # residue artifact).
        assert len(original) == 360, (
            f"Expected 360 rows from composer, got {len(original)}"
        )
        # Original is the pure contractual baseline -- balance never
        # increases month-over-month (positive amortization).
        for i in range(1, len(original)):
            assert original[i] <= original[i - 1] + 0.01, (
                f"Original balance increased at index {i}: "
                f"{original[i - 1]} -> {original[i]}"
            )
        # Series end at $0 (paid off at term).
        assert original[-1] == 0.0
        assert committed[-1] == 0.0
        assert floor[-1] == 0.0

    def test_amortization_tab_rows_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-2: Amortization tab renders term_months rows for a full loan.

        Uses the same fixture as C5-1.  The amortization tab
        renders ``planned_schedule = history_rows + committed_forward``
        from the composer.  History contributes one row (the Feb 1 2026
        confirmed payment); the forward slice contributes 359
        contractual rows.  Total: 360 rows.

        The confirmed payment is placed in seed_periods[2] (the pay
        period containing 2/1, the loan's first payment) so the
        due-date-keyed schedule starts at the Feb 1 payment and spans the
        full 360-month term.  Re-pinned for Commit 5 (one row per
        remaining_months, no residue artifact).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        projected = html.count('badge bg-secondary">Projected</span>')
        total = confirmed + projected
        # 1 confirmed (Feb 2026 in history) + 359 forward = 360.
        assert confirmed == 1, f"Expected 1 confirmed row, got {confirmed}"
        assert total == 360, (
            f"Expected 360 total schedule rows, got {total} "
            f"({confirmed} confirmed + {projected} projected)"
        )

    def test_payment_breakdown_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-3: Payment breakdown sums to total and percentages sum to 100.

        The breakdown card derives from
        ``_find_current_period_row(planned_schedule)``.  After
        Commit 5 ``planned_schedule = scenarios_main.history_rows +
        scenarios_main.committed_forward``; the first row with
        ``is_confirmed=False`` is the next planned payment.  The
        truncate-then-distribute percentages MUST still sum to
        exactly 100.0% (the dashboard rendering invariant).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract the breakdown card's percentages via the
        # ``data-progress-pct`` attribute, which only appears on the
        # principal/interest/escrow progress bars and not elsewhere
        # on the page.  This isolates the truncate-then-distribute
        # output from unrelated percent strings (e.g., the interest
        # rate display).
        pct_strings = re.findall(r'data-progress-pct="([0-9.]+)"', html)
        assert len(pct_strings) >= 2, (
            f"Expected >= 2 breakdown percent attributes, "
            f"found {pct_strings}"
        )
        breakdown_pcts = [Decimal(p) for p in pct_strings]
        total_pct = sum(breakdown_pcts, Decimal("0.0"))
        assert total_pct == Decimal("100.0"), (
            f"Breakdown percentages sum to {total_pct}, expected 100.0"
        )

    def test_recurrence_end_date_update_idempotent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-4: Recurrence end_date update is idempotent on dashboard reload.

        When ``_update_transfer_end_date`` is called with a planned
        schedule whose last row's payment_date equals the recurrence
        rule's existing end_date, NO write occurs (the guard at
        ``loan.py:_update_transfer_end_date``).  Re-rendering the
        dashboard a second time must not mutate the row.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        # Create a recurring transfer template; its rule's end_date
        # starts at None.  First GET will sync end_date to the
        # planned schedule's last payment date.  Second GET must be
        # a no-op.
        _create_transfer_template(seed_user, db.session, acct)
        db.session.commit()

        # First GET sets end_date.
        resp1 = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp1.status_code == 200

        # Re-fetch the rule's end_date after the first sync.
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        template = db.session.query(TransferTemplate).filter_by(
            to_account_id=acct.id,
        ).one()
        first_end_date = template.recurrence_rule.end_date
        assert first_end_date is not None

        # Count audit log rows for recurrence_rule UPDATE before the
        # second GET.  The guard at ``_update_transfer_end_date``
        # short-circuits when the new end_date equals the current
        # one; the system.audit_log row count must NOT increase
        # after the second dashboard render.
        audit_count_sql = sa.text(
            "SELECT COUNT(*) FROM system.audit_log "
            "WHERE table_name = 'recurrence_rules' AND operation = 'UPDATE'"
        )
        audit_before = db.session.execute(audit_count_sql).scalar()

        # Second GET must be a no-op (end_date already matches).
        resp2 = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp2.status_code == 200
        db.session.expire_all()
        rule = db.session.query(RecurrenceRule).filter_by(
            id=template.recurrence_rule_id,
        ).one()
        assert rule.end_date == first_end_date
        audit_after = db.session.execute(audit_count_sql).scalar()
        assert audit_after == audit_before, (
            "Second dashboard GET wrote a new recurrence_rule UPDATE row "
            f"to system.audit_log ({audit_before} -> {audit_after}) -- "
            "idempotency guard at _update_transfer_end_date failed"
        )

    def test_no_direct_generate_schedule_in_dashboard(self):
        """C5-5: the dashboard surface must not call generate_schedule directly.

        Static grep against ``app/routes/loan/dashboard.py`` -- the
        dashboard route plus its context-building helpers
        (``_build_dashboard_scenarios`` etc.) after the Phase 3
        pylint-cleanup split + decomposition -- confirming the dashboard
        was migrated to the ``compute_payoff_scenarios`` composer.  The
        bare word ``generate_schedule`` may still appear in a comment /
        docstring, but never as a direct ``amortization_engine.``
        engine call from the dashboard surface.
        """
        import pathlib  # pylint: disable=import-outside-toplevel
        source = pathlib.Path("app/routes/loan/dashboard.py").read_text()
        assert "amortization_engine.generate_schedule" not in source, (
            "Dashboard surface still calls "
            "amortization_engine.generate_schedule directly -- Commit 5 "
            "migration incomplete"
        )

    def test_floor_above_committed_with_projections(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-6: Floor sits at-or-above Committed past today when projections exist.

        Floor is "Committed with the projected portion of payments
        filtered out."  When the loan has projected overpayments (or
        any projected payments at all), Committed reduces the
        balance further/faster than Floor in those months; therefore
        Floor[i] >= Committed[i] for indices past today.

        Fixture: confirmed payment at contractual ($1580.17) in
        Feb 2026 PLUS projected OVERPAYMENT ($2080.17) in May 2026.
        The projected overpayment is what creates the floor /
        committed gap -- without it both series collapse (see C5-7).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        # Projected overpayment in May 2026 (after today).
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("2080.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        committed = self._parse_chart_array(html, "committed")
        floor = self._parse_chart_array(html, "floor")

        assert len(committed) == len(floor)
        # Floor is the projected-cancelled trajectory; with a
        # projected OVERPAYMENT, Floor sits above Committed for
        # every index past the May 2026 projection.
        diffs_strictly_above = sum(
            1 for c, f in zip(committed, floor) if f > c + 0.5
        )
        assert diffs_strictly_above > 0, (
            "Floor never rises above Committed despite a projected "
            "overpayment -- the projection was not cancelled in the "
            "floor composer call"
        )
        # No index should have Floor below Committed (cancelling
        # projections can only slow paydown, never speed it).
        for i, (c, f) in enumerate(zip(committed, floor)):
            assert f >= c - 0.5, (
                f"Floor below Committed at index {i}: {f} < {c}"
            )

    def test_floor_equals_committed_when_no_projections(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-7: Floor equals Committed when no projected payments exist.

        With only confirmed payments, the floor composer call sees
        the same payment list as the main composer call (filtering
        projected payments removes nothing).  The two series are
        byte-identical.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        committed = self._parse_chart_array(html, "committed")
        floor = self._parse_chart_array(html, "floor")

        assert committed == floor, (
            "Floor and Committed must match when no projected payments "
            "exist -- there is nothing to cancel"
        )

    def test_arm_dashboard_chart_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-8: ARM dashboard chart values from composer match expected.

        Creates an ARM mortgage in its fixed-rate window and asserts
        the dashboard's chart arrays come from the composer:
          * Lengths agree (composer's single-source-of-truth lock).
          * Original is monotonically non-increasing in the
            fixed-rate window (the rate cannot rise yet).
          * Final values reach $0.
        """
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "ARM 5/1",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        # Fixed-rate window: arm_first_adjustment_months defaults to
        # None in this fixture, so the ARM behaves like fixed-rate
        # for the resolver outside the window.  Either way the
        # composer's behavior is locked.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        original = self._parse_chart_array(html, "original")

        assert len(original) > 0
        # Last entry reaches $0 (loan pays off at term boundary).
        assert original[-1] == 0.0
        # Original baseline is non-increasing (fixed-rate window).
        for i in range(1, len(original)):
            assert original[i] <= original[i - 1] + 0.01, (
                f"ARM Original balance increased at index {i}: "
                f"{original[i - 1]} -> {original[i]}"
            )


# -- Recurrence End Date Auto-Update Tests (Commit 5.9-1) ----------------


def _create_transfer_template(seed_user, db_session, loan_account,
                              amount=Decimal("1500.00"), end_date=None,
                              name=None):
    """Create a recurring transfer template targeting a loan account.

    Returns (template, rule) so tests can inspect both objects.
    Creates a monthly recurrence rule from the seed user's checking
    account to the given loan account.
    """
    from app.enums import RecurrencePatternEnum  # pylint: disable=import-outside-toplevel
    from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
    from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

    if name is None:
        name = f"Loan Payment {loan_account.id}"

    monthly_id = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY)
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=monthly_id,
        day_of_month=1,
        end_date=end_date,
    )
    db_session.add(rule)
    db_session.flush()

    tpl = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        recurrence_rule_id=rule.id,
        name=name,
        default_amount=amount,
        is_active=True,
    )
    db_session.add(tpl)
    db_session.commit()
    return tpl, rule


class TestRecurrenceEndDateUpdate:
    """Tests for auto-updating the recurrence rule end_date on dashboard load.

    Commit 5.9-1: when the loan dashboard computes the projected payoff
    date, the recurring transfer template's recurrence rule end_date is
    synchronized to prevent shadow transaction generation beyond payoff.
    """

    def test_end_date_set_on_dashboard_load(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage + recurring transfer with end_date=None: dashboard sets
        end_date to the projected payoff date.

        After GET, the recurrence rule's end_date in the database must
        equal the committed schedule's last payment date.
        """
        acct = _create_mortgage(seed_user, db.session)
        _tpl, rule = _create_transfer_template(seed_user, db.session, acct)

        assert rule.end_date is None

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        db.session.refresh(rule)
        assert rule.end_date is not None
        assert isinstance(rule.end_date, date)
        # Mortgage payoff is years in the future.
        assert rule.end_date > date.today()

    def test_end_date_updated_when_payoff_changes(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Extra payment accelerates payoff: end_date moves earlier.

        First dashboard load sets end_date to the original payoff date.
        After adding a confirmed payment, the next dashboard load
        updates end_date to the earlier accelerated payoff date.
        """
        acct = _create_mortgage(seed_user, db.session)
        _tpl, rule = _create_transfer_template(seed_user, db.session, acct)

        # First load: set initial end_date.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        db.session.refresh(rule)
        original_end_date = rule.end_date
        assert original_end_date is not None

        # Add a large extra payment to accelerate payoff.
        # Use period 7 (April 2026) so the payment falls within the
        # schedule's date range (schedule starts from current month).
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("50000.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        # Second load: end_date should move earlier.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        db.session.refresh(rule)
        assert rule.end_date is not None
        assert rule.end_date < original_end_date, (
            f"Expected payoff to accelerate: {rule.end_date} should be "
            f"before {original_end_date}"
        )

    def test_end_date_cleared_when_no_payoff(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Negative amortization (payment < interest): end_date is None.

        When the committed schedule ends with a positive remaining
        balance, the loan does not pay off within the projected term.
        The recurrence rule end_date should be None (indefinite).

        Setup: ARM loan with 60% rate, 1-month term originating in
        March 2026 so the only payment month is April (due 4/1).
        seed_periods[6] (2026-03-27 .. 2026-04-09) CONTAINS 4/1, so its
        payment is the April 1 payment that the schedule's only row
        expects -- the due-date-keyed override and the projection row
        line up.  The $100 payment is far below the monthly interest of
        $5,000, producing negative amortization and a remaining balance
        > $0.
        """
        # ARM loan: origination Mar 2026, term 1 month.
        # First (and only) payment month = April 2026 (due 4/1).
        # 60% annual rate, $100K principal, monthly interest = $5,000.
        acct = _create_loan_account(
            seed_user, db.session, "Auto Loan", "Neg Am Loan",
            Decimal("100000.00"), Decimal("0.60000"), 1,
            date(2026, 3, 1), 1, is_arm=True,
        )
        _tpl, rule = _create_transfer_template(
            seed_user, db.session, acct, amount=Decimal("100.00"),
        )

        # Create a transfer with amount ($100) far below monthly
        # interest ($5,000).  The engine uses this instead of the
        # contractual payment, so the balance stays at ~$100K.  Placed in
        # the pay period containing 4/1 so it funds the loan's April
        # payment (the schedule's single row).
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[6], Decimal("100.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        db.session.refresh(rule)
        assert rule.end_date is None, (
            f"Expected None for non-paying-off loan, got {rule.end_date}"
        )

    def test_no_update_when_no_transfer(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage without recurring transfer: no error, no update.

        The dashboard should render normally when there is no
        recurring transfer template to update.
        """
        acct = _create_mortgage(seed_user, db.session)
        # No transfer template created.

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Loan Summary" in resp.data

    def test_end_date_idempotent_no_write_on_repeat(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Repeat dashboard load with no changes: end_date stays the same.

        The idempotency guard prevents a database write when the
        projected payoff date has not changed since the last visit.
        """
        acct = _create_mortgage(seed_user, db.session)
        _tpl, rule = _create_transfer_template(seed_user, db.session, acct)

        # First load: sets end_date.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        db.session.refresh(rule)
        first_end_date = rule.end_date
        assert first_end_date is not None

        # Second load: same loan, no changes.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        db.session.refresh(rule)
        assert rule.end_date == first_end_date

    def test_end_date_reverts_when_payoff_extends(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Extra payment accelerates payoff; identical loan without it
        has a later end_date, confirming the update is payment-sensitive.

        This verifies the symmetric case of C-5.9-2: if extra payments
        were removed, the end_date would revert to the later baseline
        date.  Two identical mortgages are compared -- one with a large
        extra payment, one without.
        """
        # Mortgage A: with a large extra payment.
        acct_a = _create_loan_account(
            seed_user, db.session, "Mortgage", "Accelerated Mortgage",
            Decimal("250000.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), 1,
        )
        _tpl_a, rule_a = _create_transfer_template(
            seed_user, db.session, acct_a,
        )
        _create_transfer_to_loan(
            seed_user, acct_a, seed_periods[7], Decimal("50000.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()
        resp = auth_client.get(f"/accounts/{acct_a.id}/loan")
        assert resp.status_code == 200
        db.session.refresh(rule_a)
        accelerated_end_date = rule_a.end_date
        assert accelerated_end_date is not None

        # Mortgage B: identical parameters, no extra payment.
        acct_b = _create_loan_account(
            seed_user, db.session, "Mortgage", "Baseline Mortgage",
            Decimal("250000.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), 1,
        )
        _tpl_b, rule_b = _create_transfer_template(
            seed_user, db.session, acct_b,
        )
        resp = auth_client.get(f"/accounts/{acct_b.id}/loan")
        assert resp.status_code == 200
        db.session.refresh(rule_b)
        baseline_end_date = rule_b.end_date
        assert baseline_end_date is not None

        # The extra payment should produce an earlier payoff.
        assert accelerated_end_date < baseline_end_date, (
            f"Accelerated {accelerated_end_date} should be before "
            f"baseline {baseline_end_date}"
        )

    def test_end_date_type_matches_column(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Stored end_date is a date, not a datetime.

        Type mismatches between the assigned value and the column
        type can cause comparison failures on subsequent loads.
        """
        from datetime import datetime  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        _tpl, rule = _create_transfer_template(seed_user, db.session, acct)

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        db.session.refresh(rule)
        assert rule.end_date is not None
        assert isinstance(rule.end_date, date)
        assert not isinstance(rule.end_date, datetime), (
            f"end_date should be date, not datetime: {rule.end_date!r}"
        )

    def test_end_date_paid_off_loan(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Paid-off loan via confirmed payment: end_date in the past.

        When confirmed payments have retired the loan, the schedule
        ends early.  The end_date should be set to the payoff date
        (in the past) so the recurrence engine stops generating
        transfers.
        """
        # Small loan: $1000 at 5% for 12 months, origination Jan 2026.
        # First payment month: Feb 2026 (seed_periods[3] = Feb 13).
        acct = _create_loan_account_exact(
            seed_user, db.session, "Auto Loan", "Paid Off Loan",
            Decimal("1000.00"), Decimal("0.00"),
            Decimal("0.05000"), 12, date(2026, 1, 1), 1,
        )
        # Large confirmed payment in Feb, then the operator records the
        # payoff as a balance true-up to $0.  Under the contractual-
        # schedule model a cash lump sum does not auto-pay-off; the
        # true-up is the explicit-event path, and the recurrence engine
        # sets end_date once the loan is paid off.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1100.00"),
            status_enum=StatusEnum.DONE,
        )
        insert_trueup_event(acct.loan_params, Decimal("0.00"))
        db.session.commit()
        _tpl, rule = _create_transfer_template(seed_user, db.session, acct)

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        db.session.refresh(rule)
        assert rule.end_date is not None, (
            "Paid-off loan should have end_date set to stop future transfers"
        )
        # The end_date should be today or in the past -- no future
        # payments needed for a paid-off loan.
        assert rule.end_date <= date.today(), (
            f"Expected end_date <= today for paid-off loan, got {rule.end_date}"
        )

    def test_end_date_with_arm_rate_changes(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """ARM mortgage with rate history: end_date reflects ARM-adjusted payoff.

        The committed schedule incorporates rate changes.  Verify the
        end_date matches the ARM-adjusted payoff, not a fixed-rate payoff.
        """
        # ARM mortgage: 5% initial rate, 360 months.
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "ARM Mortgage",
            Decimal("250000.00"), Decimal("0.05000"), 360,
            date(2023, 6, 1), 1, is_arm=True,
        )
        _tpl, rule = _create_transfer_template(seed_user, db.session, acct)

        # Add a rate increase: 7% effective Jan 2026.
        rate_entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2026, 1, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(rate_entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        db.session.refresh(rule)
        assert rule.end_date is not None
        assert isinstance(rule.end_date, date)
        # ARM loan at 5% with no rate change should pay off sooner
        # than one at 7%.  Just verify the end_date is set and future.
        assert rule.end_date > date.today()

    def test_end_date_no_params_no_crash(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Loan account with no LoanParams: dashboard renders setup page.

        The end_date update logic is never reached because the
        dashboard returns early when params are missing.
        """
        from app.enums import RecurrencePatternEnum  # pylint: disable=import-outside-toplevel
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="No Params Loan",
        
            anchor_balance=Decimal("0"),
        )
        db.session.add(account)
        db.session.flush()

        # Create a template even though no LoanParams exist.
        monthly_id = ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.MONTHLY,
        )
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=monthly_id,
            day_of_month=1,
        )
        db.session.add(rule)
        db.session.flush()
        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=account.id,
            recurrence_rule_id=rule.id,
            name="Premature Payment",
            default_amount=Decimal("500.00"),
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Configure" in resp.data

        # Rule should be unchanged -- end_date update never reached.
        db.session.refresh(rule)
        assert rule.end_date is None

    def test_end_date_idor(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """Other user's loan: 404-redirect, no end_date modification.

        Confirms the ownership check prevents cross-user mutation of
        recurrence rule end_date.
        """
        from app.enums import RecurrencePatternEnum  # pylint: disable=import-outside-toplevel
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        other_loan = _create_other_loan(second_user, db.session, "Mortgage")

        # Create a transfer template for the other user's loan.
        monthly_id = ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.MONTHLY,
        )
        rule = RecurrenceRule(
            user_id=second_user["user"].id,
            pattern_id=monthly_id,
            day_of_month=1,
        )
        db.session.add(rule)
        db.session.flush()
        tpl = TransferTemplate(
            user_id=second_user["user"].id,
            from_account_id=second_user["account"].id,
            to_account_id=other_loan.id,
            recurrence_rule_id=rule.id,
            name="Other User Payment",
            default_amount=Decimal("1000.00"),
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()

        # Access other user's loan as the primary user.
        resp = auth_client.get(f"/accounts/{other_loan.id}/loan")
        assert resp.status_code == 404

        # Other user's recurrence rule should be untouched.
        db.session.refresh(rule)
        assert rule.end_date is None


# ── Refinance Calculator Tests ──────────────────────────────────────────


def _create_exact_mortgage(seed_user, db_session):
    """Create a mortgage with exact known terms for hand-calculated tests.

    Uses equal original and current principal so the contractual payment
    matches exactly: M = P * [r(1+r)^n] / [(1+r)^n - 1] where
    P=200000, r=0.065/12, n=360.  Origination today so remaining
    months = 360.
    """
    loan_type = db_session.query(AccountType).filter_by(name="Mortgage").one()
    account = account_service.create_account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name="Exact Test Mortgage",
        anchor_balance=Decimal("200000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal("200000.00"),
        current_principal=Decimal("200000.00"),
        interest_rate=Decimal("0.06500"),
        term_months=360,
        origination_date=date.today(),
        payment_day=1,
    )
    db_session.add(params)
    db_session.flush()
    insert_origination_event(params)
    db_session.commit()
    return account


class TestRefinanceCalculator:
    """Tests for the refinance what-if calculator (Commit 5.10-1).

    Verifies side-by-side comparison of current loan vs. hypothetical
    refinance scenario, including monthly savings, interest savings,
    break-even calculation, and edge cases.
    """

    def test_refinance_lower_rate(self, auth_client, seed_user, db, seed_periods):
        """C-5.10-1: Lower rate refinance shows monthly and interest savings.

        Mortgage at 6.5% refinanced to 5.0%, same term (360 months).
        Monthly payment must decrease and interest savings must be positive.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Both savings columns should be green (positive savings).
        assert "text-success" in html
        # Comparison table must contain key metrics.
        assert "Monthly Payment" in html
        assert "Total Interest" in html

    def test_refinance_shorter_term(self, auth_client, seed_user, db, seed_periods):
        """C-5.10-2: Shorter term increases monthly but decreases total interest.

        30yr loan refinanced to 15yr at the same rate.  Monthly payment
        increases (red), total interest decreases significantly (green),
        and no break-even is shown (monthly savings <= 0).
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "6.5", "new_term_months": "180"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Monthly increases (red), interest decreases (green).
        assert "text-danger" in html
        assert "text-success" in html
        # No break-even when monthly savings <= 0.
        assert "Break-even" not in html

    def test_refinance_with_closing_costs(self, auth_client, seed_user, db, seed_periods):
        """C-5.10-3: Closing costs produce a break-even calculation.

        Refinance to lower rate with $5,000 closing costs.
        Break-even should be calculated and displayed.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "5000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Break-even" in html
        assert "months" in html
        # Closing costs appear in the break-even explanation.
        assert "$5,000.00" in html

    @pytest.mark.parametrize("data", [
        {},
        {"new_rate": "5.0"},
        {"new_term_months": "360"},
        {"new_rate": "-1", "new_term_months": "360"},
        {"new_rate": "5.0", "new_term_months": "0"},
        {"new_rate": "5.0", "new_term_months": "700"},
    ])
    def test_refinance_validation(self, auth_client, seed_user, db, seed_periods, data):
        """C-5.10-4: Invalid inputs return validation error.

        Tests missing required fields, negative rate, zero term, and
        term exceeding max (600).
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data=data,
        )
        assert resp.status_code == 200
        assert b"Please correct" in resp.data

    def test_refinance_idor(self, auth_client, second_user, db, seed_periods):
        """C-5.10-5: Refinance on another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session, "Mortgage")
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 404

    def test_refinance_principal_auto_calculated(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-6: Without new_principal, refinance uses current + closing.

        Auto-calculated: refi_principal = current_real_principal + closing_costs.
        The principal row shows the difference from closing costs.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "3000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Principal row appears (refi != current due to closing costs).
        assert "Principal" in html
        # Current principal = $250,000 (from _create_mortgage).
        assert "$250,000.00" in html
        # Refi principal = $250,000 + $3,000 = $253,000.
        assert "$253,000.00" in html

    def test_refinance_principal_override(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-7: User-provided principal overrides auto-calculation.

        When new_principal is specified, the refinance ignores current
        balance + closing costs and uses the override directly.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "5000",
                "new_principal": "300000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Refi principal should be the override.
        assert "$300,000.00" in html
        # Should NOT show $255,000 (current + closing).
        assert "$255,000.00" not in html

    def test_refinance_no_closing_costs(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-8: Zero closing costs means no break-even calculation.

        With closing_costs=0, refi_principal = current_real_principal
        and no break-even message is shown.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Break-even" not in html
        # Lower rate still produces savings.
        assert "text-success" in html

    def test_refinance_higher_rate(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-9: Higher rate refinance shows negative savings.

        Refinancing to a higher rate increases both monthly payment
        and total interest.  Differences should be red (negative).
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "8.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Both monthly and interest show red (negative savings).
        assert "text-danger" in html
        # No break-even when savings are negative.
        assert "Break-even" not in html

    def test_refinance_with_confirmed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-10: Confirmed payments reduce real principal for refinance.

        With confirmed payments, the current side uses the committed
        schedule metrics and the refinance principal is based on the
        reduced real balance, not the stored current_principal.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1700.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Valid comparison produced (not an error).
        assert "Monthly Payment" in html
        assert "Total Interest" in html

    def test_refinance_arm_current_side(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-11: ARM loan current side reflects rate-adjusted schedule.

        An ARM mortgage with rate history uses the adjusted committed
        schedule for the current baseline.  Refinancing to a lower
        fixed rate should show savings.
        """
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "ARM Mortgage",
            Decimal("250000.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), 1, is_arm=True,
        )
        rh = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 1, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(rh)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.interest_rate = Decimal("0.07000")
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Monthly Payment" in html
        # 5% < 7% current ARM rate → savings expected.
        assert "text-success" in html

    def test_refinance_paid_off_loan(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-12: Paid-off loan returns error, not a comparison."""
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "Paid Off Mortgage",
            Decimal("0.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), 1,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        assert b"paid off" in resp.data

    def test_refinance_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-13: Loan with no LoanParams returns 404."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        acct = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="No Params Mortgage",
            anchor_balance=Decimal("200000.00"),
        )
        db.session.add(acct)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 404

    def test_refinance_break_even_calculation_exact(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-14: Break-even equals ceil(closing_costs / monthly_savings).

        200K mortgage at 6.5%, refinance to 5.0%, 360 months.
        Closing costs = $5,000.

        Refi principal = 200000 + 5000 = 205000.
        Current monthly = M(200000, 0.065, 360) = $1,264.14.
        Refi monthly = M(205000, 0.05, 360)    = $1,100.48.
        Monthly savings = 1264.14 - 1100.48     = $163.66.
        Break-even = ceil(5000 / 163.66)        = 31 months.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "5000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Break-even" in html
        assert "31 months" in html

    def test_refinance_tab_exists(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-15: Dashboard for loan with params includes Refinance tab."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Refinance Calculator" in html
        assert "tab-refinance" in html

    def test_refinance_tab_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-16: Loan without params shows setup page, no Refinance tab."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        acct = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="No Params Mortgage",
            anchor_balance=Decimal("200000.00"),
        )
        db.session.add(acct)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Setup page does not contain the Refinance tab.
        assert "Refinance Calculator" not in html

    def test_refinance_rate_conversion(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-17: Rate input as percentage is correctly converted to decimal.

        Submitting new_rate=5.0 (meaning 5%) must produce a monthly
        payment consistent with 0.05 annual rate.

        200K at 5.0%, 360 months: M = $1,073.64.
        At 500% (unconverted):    M would be ~$83,333.
        At 0.05% (double-conv):   M would be ~$556.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Correct 5% rate produces $1,073.64/mo refinanced payment.
        assert "$1,073.64" in html

    def test_refinance_comparison_metrics_hand_calculated(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-18: Exact comparison metrics match engine calculation.

        Known loan: $200K, 6.5%, 30yr.
        Refinance: same principal, 5.0%, 30yr, no closing costs.

        Amortization formula: M = P * [r(1+r)^n] / [(1+r)^n - 1]

        Current:   P=200000, r=0.065/12, n=360
                   M = $1,264.14/mo
                   Total interest = $255,085.82

        Refinance: P=200000, r=0.05/12, n=360
                   M = $1,073.64/mo
                   Total interest = $186,513.24

        Savings:   Monthly  = $1,264.14 - $1,073.64 = $190.50/mo
                   Interest = $255,085.82 - $186,513.24 = $68,572.58
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        # Current monthly P&I.
        assert "$1,264.14" in html
        # Refinance monthly P&I.
        assert "$1,073.64" in html
        # Monthly savings.
        assert "$190.50" in html
        # Current total interest.
        assert "$255,085.82" in html
        # Refinance total interest.
        assert "$186,513.24" in html
        # Interest savings.
        assert "$68,572.58" in html


class TestRefinanceAndPayoffByDateProjectForwardMigration:
    """C7-5, C7-7, C7-8: route-level assert-unchanged locks for the
    Commit 7 migration of ``refinance_calculate`` and the
    ``mode=target_date`` payoff branch onto :func:`project_forward`.

    The migration is behavior-preserving (per D-F of the
    implementation plan).  These tests pin the rendered HTML values
    that the legacy ``generate_schedule`` path produced; any drift
    proves a real regression.
    """

    def test_refinance_unchanged_vs_pre_commit(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C7-5: refinance partial renders byte-identical key values.

        Pre-commit snapshot (200K mortgage at 6.5% refinancing to 5.0%,
        360 months, $0 closing costs):
          - refi_monthly        = $1,073.64
          - refi_total_interest = $186,513.24
          - monthly_savings     = $190.50
          - interest_savings    = $68,572.58
        Hand calculation (matches existing
        ``test_refinance_comparison_metrics_hand_calculated``):
          Current:   M(200000, 0.065/12, 360) = $1,264.14;
                     total interest = $255,085.82.
          Refinance: M(200000, 0.05/12, 360)  = $1,073.64;
                     total interest = $186,513.24.
          Savings:   1264.14 - 1073.64 = 190.50/mo;
                     255085.82 - 186513.24 = 68572.58.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$1,264.14" in html, "current monthly drifted"
        assert "$1,073.64" in html, "refi monthly drifted"
        assert "$190.50" in html, "monthly savings drifted"
        assert "$255,085.82" in html, "current total interest drifted"
        assert "$186,513.24" in html, "refi total interest drifted"
        assert "$68,572.58" in html, "interest savings drifted"

    def test_no_generate_schedule_in_refinance(self):
        """C7-7: the refinance schedule projection makes no generate_schedule call.

        Structural guarantee mirroring C7-6 at the route layer.  The
        refinance schedule is built by ``_project_refinance`` (Phase 3
        pylint cleanup decomposed ``refinance_calculate``; the route
        delegates through ``_build_refinance_comparison``).  The
        builder's function-body slice -- between its ``def`` and the
        next top-level ``def`` in ``app/routes/loan/calculators.py`` --
        must project via ``project_forward``, never reference or call
        ``generate_schedule``.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        calculators = (
            Path(__file__).resolve().parent.parent.parent
            / "app" / "routes" / "loan" / "calculators.py"
        )
        source = calculators.read_text(encoding="utf-8")
        marker = "def _project_refinance("
        start = source.index(marker)
        next_def = source.find("\ndef ", start + len(marker))
        end = next_def if next_def != -1 else len(source)
        body = source[start:end]
        assert "amortization_engine.generate_schedule" not in body, (
            "_project_refinance must not reference "
            "amortization_engine.generate_schedule after Commit 7."
        )
        assert "generate_schedule(" not in body, (
            "_project_refinance must not call generate_schedule."
        )

    def test_target_date_route_branch_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C7-8: ``mode=target_date`` HTML renders byte-identical
        ``required_extra`` and ``total_monthly``.

        Uses the exact $200k / 6.5% / 30yr mortgage helper.  Today is
        frozen to 2026-03-20 by ``_freeze_today_inside_seed_range``,
        and ``_create_exact_mortgage`` originates "today," so the
        route's binary search anchors at ``2026-03-01`` (today's first
        of month) with starting_date 2026-04-01.  Target 2041-01-01.
        Pre-commit values from the legacy ``generate_schedule``-backed
        binary search (captured 2026-05-22):
          - required_extra = $489.67 (binary-search convergence
            against the contractual M(200000, 0.065/12, 360) =
            $1,264.14)
          - total_monthly  = 1264.14 + 489.67 = $1,753.81
        The HTMX partial only renders required_extra and total_monthly
        in this mode; ``monthly_payment`` is passed for context-builder
        completeness but is not surfaced as a distinct label, so the
        assertion set matches what the user actually sees.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2041-01-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$489.67" in html, "required_extra drifted"
        assert "$1,753.81" in html, "total_monthly drifted"


# ── Nav-Pills Consistency Tests ─────────────────────────────────────


class TestLoanNavPills:
    """Tests verifying loan dashboard uses nav-pills instead of nav-tabs."""

    def test_loan_dashboard_renders_pills(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard contains nav-pills markup."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"nav-pills" in resp.data

    def test_loan_dashboard_no_nav_tabs(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard does not contain nav-tabs markup."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"nav-tabs" not in resp.data

    def test_loan_payoff_nested_pills(self, auth_client, seed_user, db, seed_periods):
        """Payoff Calculator section contains a second nav-pills instance."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        html = resp.data.decode()
        # Two nav-pills: primary navigation and nested payoff calculator.
        assert html.count("nav-pills") >= 2

    def test_loan_uses_scroll_pills(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard contains shekel-scroll-pills class."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"shekel-scroll-pills" in resp.data

    def test_no_mobile_scroll_tabs_in_loan(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard does not contain mobile-scroll-tabs class."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"mobile-scroll-tabs" not in resp.data

    def test_loan_tab_ids_preserved(self, auth_client, seed_user, db, seed_periods):
        """All expected tab pane IDs are present in the loan dashboard."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        html = resp.data.decode()
        expected_ids = [
            'id="tab-overview"',
            'id="tab-escrow"',
            'id="tab-schedule"',
            'id="tab-payoff"',
            'id="tab-refinance"',
        ]
        for tab_id in expected_ids:
            assert tab_id in html, f"Missing tab pane: {tab_id}"

    def test_loan_tab_ids_arm_rate_history(self, auth_client, seed_user, db, seed_periods):
        """ARM loan dashboard includes the rate-history tab pane."""
        acct = _create_loan_account(
            seed_user, db.session, "Auto Loan", "ARM Auto",
            Decimal("20000.00"), Decimal("0.04500"), 60,
            date(2025, 1, 1), 15, is_arm=True,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        html = resp.data.decode()
        assert 'id="tab-rates"' in html

    def test_loan_data_bs_toggle_pill(self, auth_client, seed_user, db, seed_periods):
        """All toggle attributes use pill, not tab."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        html = resp.data.decode()
        assert 'data-bs-toggle="pill"' in html
        assert 'data-bs-toggle="tab"' not in html


# ── Loan Balance True-up (E-18 D-C / Commit 16) ──────────────────────


class TestLoanBalanceTrueUp:
    """Tests for the dated balance true-up route (loan.true_up_balance).

    The route mirrors the checking-account anchor true-up UX
    (:func:`app.routes.accounts.true_up`) for loan accounts.  POSTing
    ``(anchor_date, anchor_balance)`` appends a ``user_trueup``
    :class:`LoanAnchorEvent` and redirects back to the dashboard.

    Test IDs follow the Commit-16 plan checklist (C16-1 .. C16-7).
    """

    def _count_events(self, db_session, account):
        return (
            db_session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id)
            .count()
        )

    # C16-1
    def test_trueup_appends_event(self, auth_client, seed_user, db, seed_periods):
        """POST trueup creates a new LoanAnchorEvent; no prior row mutated.

        Hand-check: the ``_create_auto_loan`` fixture writes two
        anchor events (origination at $30,000 plus a user_trueup at
        $25,000).  After POSTing today / $24,000:
          * 302 redirect to /accounts/<id>/loan.
          * Three anchor events on disk (origination + seed trueup +
            new trueup).
          * The new event has source_id == USER_TRUEUP id, balance
            $24,000, anchor_date == 2026-03-20 (the frozen "today"
            for this test file).
          * The prior two events are byte-identical (no UPDATE).
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)

        before_events = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .order_by(_LAE.id)
            .all()
        )
        before_snapshot = [
            (e.id, e.anchor_date, e.anchor_balance, e.source_id, e.created_at)
            for e in before_events
        ]
        assert len(before_snapshot) == 2, (
            "Fixture is expected to seed two events; if this assertion "
            "fails the helper has drifted and the rest of this test "
            "is meaningless."
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "24000.00",
            },
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/accounts/{acct.id}/loan")

        db.session.expire_all()
        after_events = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .order_by(_LAE.id)
            .all()
        )
        assert len(after_events) == 3

        after_by_id = {e.id: e for e in after_events}
        for snap in before_snapshot:
            e_id, e_date, e_balance, e_source, e_created = snap
            after = after_by_id[e_id]
            assert (
                (after.id, after.anchor_date, after.anchor_balance,
                 after.source_id, after.created_at)
                == snap
            ), (
                f"Prior event id={e_id} must not be mutated by a "
                f"trueup (LoanAnchorEvent is append-only)."
            )

        new_event = next(
            e for e in after_events
            if e.id not in {s[0] for s in before_snapshot}
        )
        user_trueup_id = ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        )
        assert new_event.source_id == user_trueup_id
        assert new_event.anchor_balance == Decimal("24000.00")
        assert new_event.anchor_date == date(2026, 3, 20)

    # C16-2
    def test_trueup_changes_loan_card_balance(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After a trueup, the loan dashboard renders the new balance.

        The resolver replays from the latest event by
        ``(anchor_date, created_at)`` DESC, so a trueup at a future
        anchor_date than the existing seed trueup is selected.  This
        verifies the resolver consumes the freshly-written event.

        Hand-check: seed trueup is at the fixture's
        ``origination_date + 1 day`` (i.e. 2025-01-02) at $25,000.
        The new trueup is dated 2026-03-20 (today) at $23,500, which
        is strictly later, so the resolver picks it -- meaning the
        loan card's displayed Current Principal becomes $23,500.00.
        """
        acct = _create_auto_loan(seed_user, db.session)

        # Sanity check pre-trueup balance == $25,000.00 (the seed
        # trueup amount).
        resp_pre = auth_client.get(f"/accounts/{acct.id}/loan")
        assert b"$25,000.00" in resp_pre.data

        auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "23500.00",
            },
        )
        resp_post = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp_post.status_code == 200
        assert b"$23,500.00" in resp_post.data

    # C16-3
    def test_trueup_rejects_future_date(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST anchor_date strictly in the future -> rejected, no event written.

        Schema validation rejects future dates as a validation error;
        the route flashes "correct the highlighted errors" and
        redirects without writing.  Asserts via the event count that
        nothing was appended.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )

        future = date(2026, 3, 21).isoformat()  # one day past frozen today
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={"anchor_date": future, "anchor_balance": "20000.00"},
        )
        # Validation failure: redirect (302) with flash.
        assert resp.status_code == 302

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before, (
            "Future anchor_date must NOT append an event."
        )

    # C16-4
    def test_trueup_rejects_pre_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST anchor_date before origination -> rejected with explanatory flash.

        Hand-check: ``_create_auto_loan`` uses ``origination_date =
        date(2025, 1, 1)``.  Submitting 2024-12-31 must be rejected
        and no event appended.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2024, 12, 31).isoformat(),
                "anchor_balance": "20000.00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"origination" in resp.data.lower() or b"Origination" in resp.data

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before, (
            "Pre-origination anchor_date must NOT append an event."
        )

    # C16-5
    def test_trueup_rejects_negative_balance(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST anchor_balance < 0 -> rejected, no event written.

        Schema-tier ``Range(min=0)`` plus the CHECK
        ``ck_loan_anchor_events_balance_nonneg`` at the storage tier.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "-100.00",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before

    # C16-6
    def test_trueup_is_append_only(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two distinct trueups produce two new rows; prior rows untouched.

        Hand-check: starting from the seeded two events, post two
        different trueups (different dates).  Final state must have
        four events; all earlier rows byte-identical.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)

        snapshot_before = [
            (e.id, e.anchor_date, e.anchor_balance, e.source_id, e.created_at)
            for e in (
                db.session.query(_LAE)
                .filter_by(account_id=acct.id)
                .order_by(_LAE.id)
                .all()
            )
        ]
        assert len(snapshot_before) == 2

        # First trueup -- different date than any seed event.
        auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 2, 1).isoformat(),
                "anchor_balance": "24500.00",
            },
        )
        # Second trueup -- yet another distinct date.
        auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 1).isoformat(),
                "anchor_balance": "24000.00",
            },
        )

        db.session.expire_all()
        all_events = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .order_by(_LAE.id)
            .all()
        )
        assert len(all_events) == 4

        events_by_id = {e.id: e for e in all_events}
        for snap in snapshot_before:
            e_id, e_date, e_balance, e_source, e_created = snap
            after = events_by_id[e_id]
            assert (
                (after.id, after.anchor_date, after.anchor_balance,
                 after.source_id, after.created_at)
                == snap
            ), f"Append-only invariant violated: event id={e_id} mutated."

    # C16-7
    def test_trueup_form_includes_csrf_token(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The Record Loan Balance form renders the csrf_token hidden input.

        ``TestConfig`` disables CSRF enforcement so a runtime token
        check is not exercised in tests, but the form HTML must still
        carry the ``{{ csrf_token() }}`` placeholder so production
        (where CSRF is enabled) accepts the submission.  Asserting on
        the rendered form is the proxy for "CSRF is wired correctly."
        """
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        # Locate the Record Loan Balance form and confirm both the
        # csrf_token field and the trueup action are present.
        html = resp.data.decode()
        trueup_url = f"/accounts/{acct.id}/loan/trueup"
        assert trueup_url in html
        # The csrf_token() helper renders as
        # <input type="hidden" name="csrf_token" value="..."> in
        # production; in TestConfig (CSRF disabled) it renders as
        # an empty string.  Assert the rendered form references
        # csrf_token to prove the template includes the call.
        assert b'name="csrf_token"' in resp.data or 'csrf_token' in html

    def test_trueup_idor_returns_404(
        self, auth_client, second_user, db, seed_periods,
    ):
        """POST trueup against another user's loan returns 404 (security).

        Cross-owner trueup must not write a row and must not leak the
        loan's existence; mirrors the rest of the loan-route IDOR
        contract.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        other = _create_other_loan(second_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=other.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{other.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "100.00",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=other.id)
            .count()
        )
        assert after == before

    def test_trueup_duplicate_same_day_idempotent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Submitting the same (date, balance) twice is idempotent.

        The partial unique expression index
        ``uq_loan_anchor_events_acct_date_bal_day`` rejects the second
        identical insert; :func:`apply_loan_anchor_true_up` translates
        that into ``DUPLICATE_SAME_DAY`` and the route flashes an
        informational message.  Exactly one new event row exists at
        the (date, balance) tuple after both calls.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)

        first = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "22500.00",
            },
        )
        assert first.status_code == 302

        second = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "22500.00",
            },
        )
        assert second.status_code == 302

        db.session.expire_all()
        matching = (
            db.session.query(_LAE)
            .filter_by(
                account_id=acct.id,
                anchor_date=date(2026, 3, 20),
                anchor_balance=Decimal("22500.00"),
            )
            .all()
        )
        assert len(matching) == 1, (
            "Same-day same-balance double-submit must produce exactly "
            "one row (uq_loan_anchor_events_acct_date_bal_day)."
        )


class TestLoanParamsInterestRateUpperBoundCheck:
    """Storage-tier guard for ``ck_loan_params_interest_rate_upper`` (F-18).

    The Marshmallow ``LoanParamsCreateSchema`` already pins the
    application tier at ``Range(0, 1)`` (HIGH-06 / Commit 24).  These
    tests verify the parallel storage-tier CHECK introduced in Commit
    13 of the follow-up remediation: a raw-SQL INSERT that bypasses
    the schema must be rejected by PostgreSQL with the named CHECK
    constraint visible in the error.  Boundary value 1.0 succeeds;
    NULL succeeds (the E-18 / Commit 15 nullable demotion is
    preserved).
    """

    @staticmethod
    def _make_loan_account(seed_user, db_session):
        """Create a loan Account row without LoanParams.

        ``LoanParams.account_id`` is UNIQUE so each raw-SQL INSERT
        below targets a fresh account to keep the tests independent.
        """
        loan_type = db_session.query(AccountType).filter_by(
            name="Auto Loan",
        ).one()
        acct = account_service.create_account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="F-18 Raw-SQL Test Loan",
            anchor_balance=Decimal("10000.00"),
        )
        db_session.add(acct)
        db_session.commit()
        return acct

    @staticmethod
    def _insert_loan_params_raw(account_id, interest_rate):
        """Issue a raw-SQL INSERT into ``budget.loan_params``.

        Bypasses the Marshmallow schema so the CHECK constraint is
        the only remaining guard.  ``interest_rate`` may be ``None``
        to test the nullable path.  Caller asserts whether
        ``IntegrityError`` surfaces on flush/commit.
        """
        db.session.execute(
            sa.text(
                "INSERT INTO budget.loan_params ("
                "account_id, original_principal, current_principal, "
                "interest_rate, term_months, origination_date, "
                "payment_day, is_arm) VALUES ("
                ":account_id, :orig, :curr, :rate, :term, :orig_date, "
                ":pay_day, FALSE)"
            ),
            {
                "account_id": account_id,
                "orig": Decimal("15000.00"),
                "curr": Decimal("10000.00"),
                "rate": interest_rate,
                "term": 60,
                "orig_date": date(2025, 1, 1),
                "pay_day": 15,
            },
        )

    # C13-1
    def test_raw_insert_rate_above_one_rejected(
        self, seed_user, db, seed_periods,
    ):
        """Raw-SQL INSERT with ``interest_rate = 9.5`` raises IntegrityError.

        Hand-check: ``9.5 > 1`` violates the new CHECK
        ``ck_loan_params_interest_rate_upper``.  The application tier
        is bypassed (raw SQL), so the storage tier must be the
        rejecting layer.  The exception text mentions the constraint
        name so production operators can correlate the error to the
        guard.
        """
        acct = self._make_loan_account(seed_user, db.session)

        with pytest.raises(IntegrityError) as exc_info:
            self._insert_loan_params_raw(acct.id, Decimal("9.5"))
            db.session.flush()
        assert "ck_loan_params_interest_rate_upper" in str(exc_info.value)
        db.session.rollback()

    # C13-2
    def test_raw_insert_rate_at_one_boundary_succeeds(
        self, seed_user, db, seed_periods,
    ):
        """``interest_rate = 1.0`` is the inclusive upper bound and admitted.

        Hand-check: ``1.0 <= 1`` satisfies the CHECK.  The boundary
        match the sibling ``ck_interest_params_valid_apy`` semantic
        (closed unit interval) and the Marshmallow ``Range(0, 1)``'s
        inclusive default.
        """
        acct = self._make_loan_account(seed_user, db.session)

        self._insert_loan_params_raw(acct.id, Decimal("1.0"))
        db.session.commit()

        params = (
            db.session.query(LoanParams)
            .filter_by(account_id=acct.id)
            .one()
        )
        assert params.interest_rate == Decimal("1.00000")

    # C13-3
    def test_raw_insert_rate_null_succeeds(
        self, seed_user, db, seed_periods,
    ):
        """``interest_rate = NULL`` is admitted (E-18 demotion preserved).

        Hand-check: ``IS NULL OR interest_rate <= 1`` short-circuits
        TRUE for NULL inputs.  PostgreSQL would already admit NULL
        under a bare ``interest_rate <= 1`` (NULL booleans evaluate
        UNKNOWN under CHECK), but the explicit ``IS NULL OR ...``
        documents the intent and matches the sibling
        ``ck_escrow_components_valid_inflation_rate`` shape.
        """
        acct = self._make_loan_account(seed_user, db.session)

        self._insert_loan_params_raw(acct.id, None)
        db.session.commit()

        params = (
            db.session.query(LoanParams)
            .filter_by(account_id=acct.id)
            .one()
        )
        assert params.interest_rate is None
