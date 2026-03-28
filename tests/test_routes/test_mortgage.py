"""
Tests for mortgage routes.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.mortgage_params import MortgageParams, MortgageRateHistory, EscrowComponent
from app.models.ref import AccountType


def _create_mortgage_account(seed_user, db_session, name="My Mortgage"):
    """Helper to create a mortgage account with params."""
    mortgage_type = db_session.query(AccountType).filter_by(name="Mortgage").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=mortgage_type.id,
        name=name,
        current_anchor_balance=Decimal("250000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = MortgageParams(
        account_id=account.id,
        original_principal=Decimal("300000.00"),
        current_principal=Decimal("250000.00"),
        interest_rate=Decimal("0.06500"),
        term_months=360,
        origination_date=date(2023, 6, 1),
        payment_day=1,
    )
    db_session.add(params)
    db_session.commit()
    return account


def _create_other_mortgage(second_user, db_session):
    """Create a mortgage account owned by the second user.

    Builds on the shared second_user fixture. Returns the Account
    with MortgageParams already attached.
    """
    mortgage_type = db_session.query(AccountType).filter_by(
        name="Mortgage"
    ).one()
    account = Account(
        user_id=second_user["user"].id,
        account_type_id=mortgage_type.id,
        name="Other Mortgage",
        current_anchor_balance=Decimal("100000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = MortgageParams(
        account_id=account.id,
        original_principal=Decimal("100000.00"),
        current_principal=Decimal("100000.00"),
        interest_rate=Decimal("0.05000"),
        term_months=360,
        origination_date=date(2024, 1, 1),
        payment_day=1,
    )
    db_session.add(params)
    db_session.commit()
    return account


class TestMortgageDashboard:
    """Tests for the mortgage dashboard page."""

    def test_dashboard_view(self, auth_client, seed_user, db, seed_periods):
        """GET returns 200 with summary data."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/mortgage")
        assert resp.status_code == 200
        assert b"Loan Summary" in resp.data
        assert b"250,000.00" in resp.data

    def test_dashboard_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """GET another user's mortgage dashboard is rejected
        and does not leak victim data."""
        other_acct = _create_other_mortgage(second_user, db.session)

        resp = auth_client.get(f"/accounts/{other_acct.id}/mortgage")
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/savings" in location, (
            f"IDOR redirect went to {location}, expected /savings"
        )
        assert b"Other Mortgage" not in resp.data, (
            "IDOR response leaked victim's account name"
        )

    def test_dashboard_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """Non-mortgage account → redirect to savings dashboard."""
        # seed_user has a checking account
        acct = seed_user["account"]
        resp = auth_client.get(f"/accounts/{acct.id}/mortgage")
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

    def test_dashboard_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """Bad ID → redirect to savings dashboard."""
        resp = auth_client.get("/accounts/99999/mortgage")
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

    def test_dashboard_login_required(self, client, seed_user, db, seed_periods):
        """Unauthenticated → redirect to login."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = client.get(f"/accounts/{acct.id}/mortgage")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


class TestMortgageParamsUpdate:
    """Tests for updating mortgage parameters."""

    def test_params_update(self, auth_client, seed_user, db, seed_periods):
        """POST valid params → updates (percentage input converted to decimal)."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/params",
            data={
                "current_principal": "240000.00",
                "interest_rate": "6.000",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302

        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        assert params.current_principal == Decimal("240000.00")
        assert params.interest_rate == Decimal("0.06000")
        assert params.payment_day == 15

    def test_params_update_validation(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST invalid data is rejected and leaves DB unchanged."""
        acct = _create_mortgage_account(seed_user, db.session)

        # Snapshot mutable fields before the request.
        original = db.session.query(MortgageParams).filter_by(
            account_id=acct.id
        ).one()
        orig_principal = original.current_principal
        orig_rate = original.interest_rate
        orig_day = original.payment_day
        orig_arm = original.is_arm

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/params",
            data={"payment_day": "32"},  # Invalid
        )
        assert resp.status_code == 302

        # Verify DB unchanged after invalid submission.
        db.session.expire_all()
        after = db.session.query(MortgageParams).filter_by(
            account_id=acct.id
        ).one()
        assert after.current_principal == orig_principal, (
            "Validation failure modified current_principal!"
        )
        assert after.interest_rate == orig_rate, (
            "Validation failure modified interest_rate!"
        )
        assert after.payment_day == orig_day, (
            "Validation failure modified payment_day!"
        )
        assert after.is_arm == orig_arm, (
            "Validation failure modified is_arm!"
        )

    def test_params_update_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """POST to another user's mortgage params is rejected
        and leaves the victim's data completely unchanged."""
        # Phase A: Setup victim's data with known values.
        other_acct = _create_other_mortgage(second_user, db.session)
        original = db.session.query(MortgageParams).filter_by(
            account_id=other_acct.id
        ).one()
        orig_principal = original.current_principal
        orig_rate = original.interest_rate
        orig_day = original.payment_day
        orig_arm = original.is_arm

        # Phase B: Attack.
        resp = auth_client.post(
            f"/accounts/{other_acct.id}/mortgage/params",
            data={
                "current_principal": "1.00",
                "interest_rate": "99.000",
                "payment_day": "28",
                "is_arm": "true",
            },
        )

        # Phase C: Verify no state change.
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/savings" in location, (
            f"IDOR redirect went to {location}, expected /savings"
        )

        db.session.expire_all()
        after = db.session.query(MortgageParams).filter_by(
            account_id=other_acct.id
        ).one()
        assert after.current_principal == orig_principal, (
            "IDOR attack modified current_principal!"
        )
        assert after.interest_rate == orig_rate, (
            "IDOR attack modified interest_rate!"
        )
        assert after.payment_day == orig_day, (
            "IDOR attack modified payment_day!"
        )
        assert after.is_arm == orig_arm, (
            "IDOR attack modified is_arm!"
        )


class TestRateChange:
    """Tests for ARM rate change recording."""

    def test_rate_change_create(self, auth_client, seed_user, db, seed_periods):
        """POST rate change → creates history row (percentage input converted to decimal)."""
        acct = _create_mortgage_account(seed_user, db.session)
        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/rate",
            data={
                "effective_date": "2026-04-01",
                "interest_rate": "7.000",
                "notes": "Rate adjustment",
            },
        )
        assert resp.status_code == 200

        entry = db.session.query(MortgageRateHistory).filter_by(account_id=acct.id).first()
        assert entry is not None
        assert entry.interest_rate == Decimal("0.07000")

    def test_rate_change_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid rate → error with validation message."""
        acct = _create_mortgage_account(seed_user, db.session)
        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/rate",
            data={"interest_rate": "200.0"},  # > 100, invalid
        )
        assert resp.status_code == 400
        assert b"Please correct the highlighted errors" in resp.data


    def test_percentage_input_stored_as_decimal(self, auth_client, seed_user, db, seed_periods):
        """Submitting 6.5 as interest rate stores 0.065 in the database."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/params",
            data={
                "interest_rate": "6.5",
                "current_principal": "250000.00",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302

        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        assert params.interest_rate == Decimal("0.065")


class TestEscrow:
    """Tests for escrow component management."""

    def test_escrow_add(self, auth_client, seed_user, db, seed_periods):
        """POST escrow component with percentage inflation input creates correctly.

        The form sends inflation_rate as a percentage (e.g. '3' for 3%).
        The schema validates it, and the route converts to decimal (0.03)
        before storage.
        """
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={
                "name": "Property Tax",
                "annual_amount": "4800.00",
                "inflation_rate": "3",
            },
        )
        assert resp.status_code == 200
        assert b"Property Tax" in resp.data

        comp = db.session.query(EscrowComponent).filter_by(account_id=acct.id).first()
        assert comp is not None
        assert comp.name == "Property Tax"
        assert comp.annual_amount == Decimal("4800.00")
        assert comp.inflation_rate == Decimal("0.03"), (
            f"Expected 0.03 (3% converted to decimal), got {comp.inflation_rate}"
        )

    def test_escrow_add_duplicate_name(self, auth_client, seed_user, db, seed_periods):
        """Duplicate name → error message, and DB still has exactly 1 component."""
        acct = _create_mortgage_account(seed_user, db.session)
        # Add first component.
        comp = EscrowComponent(
            account_id=acct.id,
            name="Insurance",
            annual_amount=Decimal("2400.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={"name": "Insurance", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 400
        assert b"already exists" in resp.data

        # Verify DB still has exactly 1 escrow with that name.
        db.session.expire_all()
        count = db.session.query(EscrowComponent).filter_by(
            account_id=acct.id, name="Insurance",
        ).count()
        assert count == 1

    def test_escrow_delete(self, auth_client, seed_user, db, seed_periods):
        """POST delete → deactivates component."""
        acct = _create_mortgage_account(seed_user, db.session)
        comp = EscrowComponent(
            account_id=acct.id,
            name="Old Insurance",
            annual_amount=Decimal("1200.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow/{comp.id}/delete",
        )
        assert resp.status_code == 200

        db.session.refresh(comp)
        assert comp.is_active is False

    def test_escrow_delete_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """DELETE another user's escrow returns 404
        and leaves the component active."""
        other_acct = _create_other_mortgage(second_user, db.session)
        comp = EscrowComponent(
            account_id=other_acct.id,
            name="Tax",
            annual_amount=Decimal("3000.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/mortgage/escrow/{comp.id}/delete",
        )
        assert resp.status_code == 404

        # Verify the escrow component is still active.
        db.session.expire_all()
        after = db.session.get(EscrowComponent, comp.id)
        assert after.is_active is True, (
            "IDOR attack deactivated victim's escrow component!"
        )
        assert after.name == "Tax", (
            "IDOR attack modified victim's escrow name!"
        )
        assert after.annual_amount == Decimal("3000.00"), (
            "IDOR attack modified victim's escrow amount!"
        )

    def test_escrow_add_with_zero_inflation_rate(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Inflation rate of '0' is valid and stored as zero."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={
                "name": "Insurance",
                "annual_amount": "2400.00",
                "inflation_rate": "0",
            },
        )
        assert resp.status_code == 200

        comp = db.session.query(EscrowComponent).filter_by(
            account_id=acct.id, name="Insurance",
        ).first()
        assert comp is not None
        assert comp.inflation_rate == Decimal("0")

    def test_escrow_add_with_empty_inflation_rate(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Empty inflation rate is accepted as None (no inflation)."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={
                "name": "HOA",
                "annual_amount": "3600.00",
                "inflation_rate": "",
            },
        )
        assert resp.status_code == 200

        comp = db.session.query(EscrowComponent).filter_by(
            account_id=acct.id, name="HOA",
        ).first()
        assert comp is not None
        assert comp.inflation_rate is None

    def test_escrow_add_with_negative_inflation_rate_rejected(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Negative inflation rate is rejected by schema validation."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={
                "name": "Tax",
                "annual_amount": "4800.00",
                "inflation_rate": "-2",
            },
        )
        assert resp.status_code == 400

        count = db.session.query(EscrowComponent).filter_by(
            account_id=acct.id,
        ).count()
        assert count == 0


class TestEscrowOobUpdates:
    """Tests that escrow add/delete responses include OOB payment summary updates."""

    def test_escrow_add_response_includes_oob_payment_summary(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Adding an escrow component returns OOB fragments for payment summary and badge."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={
                "name": "Property Tax",
                "annual_amount": "4800.00",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        # OOB total payment display must be present with hx-swap-oob.
        assert 'id="total-payment-display"' in html
        assert 'hx-swap-oob="true"' in html

        # OOB escrow badge must be present.
        assert 'id="escrow-badge"' in html

        # Monthly escrow for $4800/yr = $400/mo should appear in the badge.
        assert "$400.00/mo" in html

    def test_escrow_delete_response_includes_oob_payment_summary(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Deleting an escrow component returns OOB fragments with updated totals."""
        acct = _create_mortgage_account(seed_user, db.session)

        # Add two components directly.
        comp1 = EscrowComponent(
            account_id=acct.id, name="Tax", annual_amount=Decimal("4800.00"),
        )
        comp2 = EscrowComponent(
            account_id=acct.id, name="Insurance", annual_amount=Decimal("2400.00"),
        )
        db.session.add_all([comp1, comp2])
        db.session.commit()

        # Delete one.
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow/{comp1.id}/delete",
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        # OOB fragments must be present.
        assert 'id="total-payment-display"' in html
        assert 'hx-swap-oob="true"' in html
        assert 'id="escrow-badge"' in html

        # Only Insurance ($2400/yr = $200/mo) should remain.
        assert "$200.00/mo" in html


class TestPayoffCalculator:
    """Tests for the payoff calculator."""

    def test_payoff_extra_payment(self, auth_client, seed_user, db, seed_periods):
        """POST payoff with extra → results fragment."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        assert b"Months Saved" in resp.data

    def test_payoff_target_date(self, auth_client, seed_user, db, seed_periods):
        """POST payoff with target date → results fragment with payment data."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200
        # The payoff results template renders payment calculation data.
        assert b"$" in resp.data, (
            "Payoff results should contain dollar-formatted payment data"
        )

    def test_payoff_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid input → error."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/payoff",
            data={"mode": "invalid_mode"},
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data


class TestPayoffSlider:
    """Tests for the payoff calculator slider (U1)."""

    def test_dashboard_has_extra_payment_slider(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage dashboard renders a range slider for extra payment."""
        from app.models.mortgage_params import MortgageParams

        mortgage_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        account = Account(
            user_id=seed_user["user"].id,
            account_type_id=mortgage_type.id,
            name="Home Mortgage",
            current_anchor_balance=Decimal("250000.00"),
        )
        db.session.add(account)
        db.session.flush()

        params = MortgageParams(
            account_id=account.id,
            original_principal=Decimal("300000.00"),
            current_principal=Decimal("250000.00"),
            interest_rate=Decimal("0.06500"),
            term_months=360,
            origination_date=date(2023, 1, 1),
            payment_day=1,
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/mortgage")
        assert resp.status_code == 200
        assert b'data-slider-group="payoff"' in resp.data
        assert b'type="range"' in resp.data
        assert b'chart_slider.js' in resp.data


class TestMortgageNegativePaths:
    """Negative-path and boundary tests for mortgage routes."""

    def test_escrow_add_missing_name(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST without name field returns 400 and creates nothing."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={"annual_amount": "1200.00"},
        )
        assert resp.status_code == 400
        assert b"Please correct" in resp.data

        count = db.session.query(EscrowComponent).filter_by(
            account_id=acct.id,
        ).count()
        assert count == 0

    def test_escrow_add_negative_amount(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST with negative annual_amount is rejected by Range(min=0)."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={"name": "Tax", "annual_amount": "-1200.00"},
        )
        assert resp.status_code == 400
        assert b"Please correct" in resp.data

        count = db.session.query(EscrowComponent).filter_by(
            account_id=acct.id,
        ).count()
        assert count == 0

    def test_rate_change_missing_date(self, auth_client, seed_user, db, seed_periods):
        """Rate change POST without effective_date returns 400 schema error."""
        acct = _create_mortgage_account(seed_user, db.session)
        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        orig_rate = params.interest_rate

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/rate",
            data={"interest_rate": "5.5"},
        )
        assert resp.status_code == 400
        assert b"Please correct" in resp.data

        # No rate history created, params unchanged.
        history_count = db.session.query(MortgageRateHistory).filter_by(
            account_id=acct.id,
        ).count()
        assert history_count == 0
        db.session.expire_all()
        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        assert params.interest_rate == orig_rate

    def test_rate_change_rate_zero(self, auth_client, seed_user, db, seed_periods):
        """Rate change with 0% interest is valid (ARM reset to 0%)."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/rate",
            data={"interest_rate": "0", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 200

        db.session.expire_all()
        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        assert params.interest_rate == Decimal("0.00000")

        entry = db.session.query(MortgageRateHistory).filter_by(
            account_id=acct.id,
        ).first()
        assert entry is not None
        assert entry.interest_rate == Decimal("0.00000")

    def test_params_update_nonexistent_account(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST params to nonexistent account redirects with flash."""
        resp = auth_client.post(
            "/accounts/999999/mortgage/params",
            data={
                "current_principal": "200000.00",
                "interest_rate": "6.0",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")
        # Follow redirect to verify flash message.
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Mortgage account not found." in resp2.data

    def test_rate_change_nonexistent_account(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Rate change POST to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/mortgage/rate",
            data={"interest_rate": "5.5", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404
        assert b"Account not found" in resp.data

    def test_escrow_nonexistent_account(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Escrow POST to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/mortgage/escrow",
            data={"name": "Tax", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 404
        assert b"Account not found" in resp.data

    def test_params_update_wrong_account_type(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST mortgage params to a checking account redirects with type error."""
        checking_acct = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking_acct.id}/mortgage/params",
            data={
                "current_principal": "200000.00",
                "interest_rate": "6.0",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")
        # _load_mortgage_account returns (None, None) for wrong type,
        # so route flashes "Mortgage account not found."
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Mortgage account not found." in resp2.data

    def test_rate_change_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """Rate change POST to another user's mortgage returns 404 with no side effects."""
        other_acct = _create_other_mortgage(second_user, db.session)
        resp = auth_client.post(
            f"/accounts/{other_acct.id}/mortgage/rate",
            data={"interest_rate": "9.0", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404

        # Verify no rate history was created.
        history_count = db.session.query(MortgageRateHistory).filter_by(
            account_id=other_acct.id,
        ).count()
        assert history_count == 0

        # Verify params unchanged.
        db.session.expire_all()
        params = db.session.query(MortgageParams).filter_by(
            account_id=other_acct.id,
        ).one()
        assert params.interest_rate == Decimal("0.05000")

    def test_escrow_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """Escrow POST to another user's mortgage returns 404 with no side effects."""
        other_acct = _create_other_mortgage(second_user, db.session)
        resp = auth_client.post(
            f"/accounts/{other_acct.id}/mortgage/escrow",
            data={"name": "Stolen Escrow", "annual_amount": "9999.00"},
        )
        assert resp.status_code == 404

        # Verify no escrow component was created.
        count = db.session.query(EscrowComponent).filter_by(
            account_id=other_acct.id,
        ).count()
        assert count == 0


class TestCreateMortgageAccount:
    """Test creating a mortgage account redirects correctly."""

    def test_create_mortgage_account(self, auth_client, seed_user, db, seed_periods):
        """Creating mortgage account type redirects to mortgage dashboard."""
        mortgage_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": "New Mortgage",
                "account_type_id": str(mortgage_type.id),
                "anchor_balance": "200000",
            },
        )
        assert resp.status_code == 302
        assert "/mortgage" in resp.headers.get("Location", "")
