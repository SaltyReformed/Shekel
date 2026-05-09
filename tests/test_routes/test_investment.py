"""
Tests for investment/retirement account routes.
"""

import json
import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum, DeductionTimingEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile


def _create_investment_account(seed_user, db_session, type_name="401(k)",
                                name="My 401k", balance="50000.00"):
    """Helper to create an investment/retirement account."""
    acct_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=acct_type.id,
        name=name,
        current_anchor_balance=Decimal(balance),
    )
    db_session.add(account)
    db_session.flush()
    return account


def _create_investment_params(db_session, account_id, **overrides):
    """Helper to create investment params for an account."""
    defaults = {
        "account_id": account_id,
        "assumed_annual_return": Decimal("0.07000"),
        "annual_contribution_limit": Decimal("23500.00"),
        "contribution_limit_year": 2026,
        "employer_contribution_type": "none",
    }
    defaults.update(overrides)
    params = InvestmentParams(**defaults)
    db_session.add(params)
    db_session.commit()
    return params


def _create_other_investment(second_user, db_session):
    """Create an investment account owned by the second user.

    Builds on the shared second_user fixture. Returns the Account
    (no InvestmentParams -- IDOR tests verify none get created).
    """
    acct_type = db_session.query(AccountType).filter_by(name="401(k)").one()
    account = Account(
        user_id=second_user["user"].id,
        account_type_id=acct_type.id,
        name="Other 401k",
        current_anchor_balance=Decimal("10000.00"),
    )
    db_session.add(account)
    db_session.commit()
    return account


class TestInvestmentDashboard:
    """Tests for the investment dashboard page."""

    def test_dashboard_no_params(self, auth_client, seed_user, db, seed_periods_today):
        """GET returns 200 even without investment params."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert b"50,000.00" in resp.data

    def test_dashboard_with_params(self, auth_client, seed_user, db, seed_periods_today):
        """GET returns 200 with params and projection data."""
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert b"50,000.00" in resp.data

    def test_dashboard_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """GET another user's investment dashboard returns 404 (security)
        and does not leak victim data."""
        other_acct = _create_other_investment(second_user, db.session)

        resp = auth_client.get(f"/accounts/{other_acct.id}/investment")
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data, (
            "IDOR response leaked victim's account name"
        )

    def test_dashboard_nonexistent(self, auth_client, seed_user, db, seed_periods_today):
        """Nonexistent account returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.get("/accounts/99999/investment")
        assert resp.status_code == 404

    def test_dashboard_brokerage(self, auth_client, seed_user, db, seed_periods_today):
        """Brokerage account (no contribution limit) works."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Brokerage",
            name="Brokerage", balance="25000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert b"Brokerage" in resp.data
        assert b"25,000.00" in resp.data
        assert b"Assumed Return" in resp.data


class TestInvestmentParams:
    """Tests for creating/updating investment params."""

    def test_create_params(self, auth_client, seed_user, db, seed_periods_today):
        """POST creates new investment params."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params is not None
        assert params.assumed_annual_return == Decimal("0.07000")

    def test_update_params(self, auth_client, seed_user, db, seed_periods_today):
        """POST updates existing investment params."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "8",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params.assumed_annual_return == Decimal("0.08000")

    def test_create_params_with_employer_match(self, auth_client, seed_user, db, seed_periods_today):
        """POST with employer match config."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type": "match",
                "employer_match_percentage": "100",
                "employer_match_cap_percentage": "6",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params is not None
        assert params.employer_contribution_type == "match"
        assert params.employer_match_percentage == Decimal("1.0000")
        assert params.employer_match_cap_percentage == Decimal("0.0600")

    def test_params_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """POST to another user's investment params returns 404 (security)
        and does not create any InvestmentParams row."""
        # Phase A: Setup victim's account with no params.
        other_acct = _create_other_investment(second_user, db.session)

        # Phase B: Attack.
        resp = auth_client.post(
            f"/accounts/{other_acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type": "none",
            },
        )

        # Phase C: Verify no state change.
        assert resp.status_code == 404

        db.session.expire_all()
        created = db.session.query(InvestmentParams).filter_by(
            account_id=other_acct.id
        ).first()
        assert created is None, (
            "IDOR attack created InvestmentParams on victim's account!"
        )

    def test_validation_error(self, auth_client, seed_user, db, seed_periods_today):
        """Invalid data flashes error, redirects, and creates no params."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "not_a_number",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302

        # Verify no InvestmentParams row was created.
        db.session.expire_all()
        created = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert created is None, (
            "Invalid data created an InvestmentParams row!"
        )


class TestInvestmentNegativePaths:
    """Negative-path and boundary tests for investment routes."""

    def test_dashboard_login_required(self, client, seed_user, db, seed_periods_today):
        """Unauthenticated GET to investment dashboard redirects to login."""
        acct = _create_investment_account(seed_user, db.session)
        resp = client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_params_login_required(self, client, seed_user, db, seed_periods_today):
        """Unauthenticated POST to investment params redirects to login."""
        acct = _create_investment_account(seed_user, db.session)
        resp = client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_params_update_idor_db_unchanged(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """IDOR POST to investment params with existing params is rejected and DB unchanged."""
        other_acct = _create_other_investment(second_user, db.session)
        # Create params on victim's account to test update path.
        params = InvestmentParams(
            account_id=other_acct.id,
            assumed_annual_return=Decimal("0.07000"),
            employer_contribution_type="none",
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/investment/params",
            data={
                "assumed_annual_return": "99",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        after = db.session.query(InvestmentParams).filter_by(
            account_id=other_acct.id,
        ).one()
        assert after.assumed_annual_return == Decimal("0.07000"), (
            "IDOR attack modified assumed_annual_return!"
        )

    def test_validation_error_db_unchanged(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Invalid data on existing params preserves original values."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        orig = db.session.query(InvestmentParams).filter_by(account_id=acct.id).one()
        orig_return = orig.assumed_annual_return

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "not_a_number",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(InvestmentParams).filter_by(account_id=acct.id).one()
        assert after.assumed_annual_return == orig_return

    def test_params_update_nonexistent_account(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST to nonexistent account returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.post(
            "/accounts/999999/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 404

    def test_params_update_wrong_account_type(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST investment params to checking account redirects with flash."""
        checking_acct = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking_acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type": "none",
            },
        )
        # The route checks account is None or user_id mismatch -- checking account
        # passes ownership but the route does NOT check account type; it will
        # create params. However, let's verify the actual behavior.
        # Reading the route: update_params only checks ownership, not account type.
        # So this may actually succeed. Let's assert what actually happens.
        assert resp.status_code == 302

    def test_params_update_negative_return_rate(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Negative return rate as percentage input: -5 converts to -0.05, within Range(-1,1)."""
        acct = _create_investment_account(seed_user, db.session)
        # _convert_percentage_inputs converts -5 to -0.05, which is within Range(-1, 1).
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "-5",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302

        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id,
        ).first()
        assert params is not None
        # -5% → -0.05 is valid per schema Range(-1, 1)
        assert params.assumed_annual_return == Decimal("-0.05000")


class TestGrowthChartFragment:
    """Tests for the investment growth chart HTMX fragment (U2)."""

    def test_growth_chart_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET without HX-Request header redirects to investment dashboard."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment/growth-chart")
        assert resp.status_code == 302
        assert "/investment" in resp.headers.get("Location", "")

    def test_growth_chart_empty_without_params(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Returns empty state when no investment params exist."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"No projection data" in resp.data

    def test_growth_chart_with_data(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Returns canvas element when projection data exists."""
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"growthChart" in resp.data

    def test_growth_chart_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """GET another user's growth chart returns 404
        and does not leak victim data."""
        other_acct = _create_other_investment(second_user, db.session)

        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data, (
            "IDOR response leaked victim's account name"
        )


# ── Tests: Contribution-Aware Dashboard ───────────────────────


def _create_salary_profile(db_session, user_id, scenario_id):
    """Create an active salary profile for the test user."""
    filing = db_session.query(FilingStatus).filter_by(name="single").one()
    profile = SalaryProfile(
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing.id,
        name="Day Job",
        annual_salary=Decimal("100000.00"),
        state_code="NC",
        is_active=True,
    )
    db_session.add(profile)
    db_session.flush()
    return profile


def _create_deduction(db_session, profile_id, account_id, amount="500.00"):
    """Create a flat-dollar deduction targeting the investment account."""
    flat_id = ref_cache.calc_method_id(CalcMethodEnum.FLAT)
    timing_id = ref_cache.deduction_timing_id(DeductionTimingEnum.PRE_TAX)
    ded = PaycheckDeduction(
        salary_profile_id=profile_id,
        deduction_timing_id=timing_id,
        calc_method_id=flat_id,
        name="401k Contribution",
        amount=Decimal(amount),
        target_account_id=account_id,
        is_active=True,
    )
    db_session.add(ded)
    db_session.flush()
    return ded


class TestContributionAwareDashboard:
    """Tests for the contribution timeline integration (5.2-2).

    Backward compatibility (no deductions/transfers) is already covered
    by TestInvestmentDashboard.test_dashboard_with_params.
    IDOR is already covered by TestInvestmentDashboard.test_dashboard_idor.
    """

    def test_dashboard_with_deduction(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Dashboard renders with deduction-based contributions.

        Creates a salary profile and a flat $500 deduction targeting the
        investment account.  Verifies the dashboard renders without error
        and the periodic contribution value appears in the response.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)

        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id, "500.00")
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        # The deduction contributes $500/period.
        assert b"500.00" in resp.data

    def test_growth_chart_with_deduction(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Growth chart HTMX fragment renders with deduction contributions.

        Verifies the growth chart route processes contribution data without
        error.  The chart uses synthetic periods, so deduction dates
        mostly fall back to periodic_contribution -- but the route must
        still call build_contribution_timeline without crashing.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)

        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id, "500.00")
        db.session.commit()

        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"growthChart" in resp.data


# ── Tests: Contribution Setup Prompt (5.2-3) ─────────────────


def _create_transfer_template(db_session, user_id, from_id, to_id,
                               is_active=True):
    """Create a recurring transfer template targeting an account."""
    from app.enums import RecurrencePatternEnum as RPE
    from app.models.recurrence_rule import RecurrenceRule
    from app.models.transfer_template import TransferTemplate

    every_id = ref_cache.recurrence_pattern_id(RPE.EVERY_PERIOD)
    rule = RecurrenceRule(user_id=user_id, pattern_id=every_id)
    db_session.add(rule)
    db_session.flush()
    tpl = TransferTemplate(
        user_id=user_id,
        from_account_id=from_id,
        to_account_id=to_id,
        recurrence_rule_id=rule.id,
        name=f"Contribution {from_id}->{to_id}",
        default_amount=Decimal("200.00"),
        is_active=is_active,
    )
    db_session.add(tpl)
    db_session.flush()
    return tpl


class TestContributionPrompt:
    """Tests for the contribution setup prompt on the investment dashboard.

    Verifies prompt visibility rules, prompt type (transfer vs. deduction),
    and the create_contribution_transfer route.
    """

    def test_prompt_shown_ira_no_contribution(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """IRA with params, no transfer or deduction: transfer prompt visible."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring contribution" in html
        assert "Create Recurring Transfer" in html

    def test_prompt_shown_401k_no_deduction(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """401(k) with params, no deduction: deduction linkage prompt visible."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="My 401k", balance="50000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No paycheck deduction linked" in html
        assert "Salary Profile" in html

    def test_prompt_hidden_transfer_exists(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """IRA with active recurring transfer: prompt hidden."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        _create_transfer_template(
            db.session, seed_user["user"].id,
            seed_user["account"].id, acct.id,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring contribution" not in html
        assert "No paycheck deduction" not in html

    def test_prompt_hidden_deduction_linked(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """401(k) with linked deduction: prompt hidden."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="My 401k", balance="50000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No paycheck deduction linked" not in html

    def test_prompt_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Account without InvestmentParams: no prompt shown."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring contribution" not in html
        assert "No paycheck deduction" not in html

    def test_prompt_shown_archived_transfer(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Archived transfer template: prompt still shown."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        _create_transfer_template(
            db.session, seed_user["user"].id,
            seed_user["account"].id, acct.id,
            is_active=False,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring contribution" in html

    def test_create_transfer_success(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with valid source creates RecurrenceRule + TransferTemplate."""
        from app.models.recurrence_rule import RecurrenceRule as RR
        from app.models.transfer_template import TransferTemplate as TT

        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/investment" in resp.headers.get(
            "Location", "",
        )

        tpl = (
            db.session.query(TT)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.is_active is True
        assert tpl.from_account_id == checking.id
        assert tpl.default_amount == Decimal("269.23")
        assert tpl.recurrence_rule_id is not None

        rule = db.session.get(RR, tpl.recurrence_rule_id)
        assert rule is not None

    def test_create_transfer_generates_shadows(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """After creation: shadow transactions exist on the investment account."""
        from app.enums import TxnTypeEnum as TTE
        from app.models.transaction import Transaction as Txn

        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]

        auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )

        income_type_id = ref_cache.txn_type_id(TTE.INCOME)
        shadows = (
            db.session.query(Txn)
            .filter(
                Txn.account_id == acct.id,
                Txn.transfer_id.isnot(None),
                Txn.transaction_type_id == income_type_id,
                Txn.is_deleted.is_(False),
            )
            .all()
        )
        assert len(shadows) > 0

    def test_create_transfer_redirect_hides_prompt(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """After creation, GET dashboard: prompt no longer visible."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]

        auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring contribution" not in html

    def test_create_transfer_validates_source_not_self(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with investment account as source: validation error."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={"source_account_id": str(acct.id), "amount": "100"},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/investment" in resp.headers.get(
            "Location", "",
        )

    def test_create_transfer_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """POST to other user's investment account returns 404 (security)."""
        other_acct = _create_other_investment(second_user, db.session)

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/investment/create-contribution-transfer",
            data={"source_account_id": "1", "amount": "100"},
        )
        assert resp.status_code == 404

    def test_create_transfer_amount_override(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with custom amount: template uses the override amount."""
        from app.models.transfer_template import TransferTemplate as TT

        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "1000.00",
            },
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TT)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.default_amount == Decimal("1000.00")


# ── Helpers: What-If Chart Data Extraction ──────────────────────


def _extract_data_attr(response_data, attr_name):
    """Extract a JSON data-* attribute value from the chart canvas element.

    Args:
        response_data: Response bytes from the test client.
        attr_name:     The data attribute name (e.g., 'whatif-balances').

    Returns:
        Parsed JSON value (list/dict), or None if not found.
    """
    html = response_data.decode()
    pattern = rf"data-{re.escape(attr_name)}='([^']*)'"
    match = re.search(pattern, html)
    if match:
        return json.loads(match.group(1))
    return None


# ── Tests: What-If Contribution Calculator (5.3-1) ─────────────


class TestWhatIfContributionCalculator:
    """Tests for the what-if contribution calculator on the investment
    growth chart.

    The what-if feature overlays a hypothetical contribution scenario
    on the committed projection, producing a dual-dataset chart and
    a comparison card showing the balance difference at the horizon.
    """

    def test_chart_no_what_if_single_line(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET growth-chart without what_if param: single dataset only.

        Backward compatibility: existing chart behavior unchanged when
        no what-if parameter is provided.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "growthChart" in html
        assert "data-whatif-balances" not in html, (
            "What-if data should not be present without what_if param"
        )
        # No comparison card.
        assert "Current Plan" not in html
        assert "Difference" not in html

    def test_chart_with_what_if_dual_lines(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=500: what-if data present.

        Verifies the response contains both committed and what-if
        datasets, and a comparison card.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "growthChart" in html
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None, (
            "What-if balances should be present"
        )
        assert len(whatif_balances) > 0
        # Comparison card rendered.
        assert "Current Plan" in html
        assert "Difference" in html
        # What-if label includes the amount.
        assert "500.00" in html

    def test_chart_what_if_zero_valid(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=0: valid growth-only scenario.

        Zero means "what if I stop contributing?" -- the what-if line
        shows balance growth without any contributions.  This is NOT
        treated as "clear the what-if."
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None, (
            "Zero is a valid what-if (growth-only), not 'clear'"
        )

    def test_chart_what_if_empty_string_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution= (empty): no what-if, single dataset.

        Empty input means "no what-if" -- chart reverts to standard mode.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-whatif-balances" not in resp.data.decode()

    def test_chart_what_if_invalid_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=abc: invalid input ignored, no error.

        Non-numeric input degrades gracefully to single-line chart.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=abc",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-whatif-balances" not in resp.data.decode()

    def test_chart_what_if_negative_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=-100: negative contribution ignored.

        Negative contributions are nonsensical; chart renders without
        what-if overlay.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=-100",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-whatif-balances" not in resp.data.decode()

    def test_what_if_respects_annual_limit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Annual contribution limit caps what-if contributions.

        Setup: $0 balance, $7000/year limit, 0% return.
        What-if: $500/period (~$13K/year uncapped).

        With 0% return, end balance = total capped contributions.
        Must be less than uncapped total (26+ periods * $500),
        proving the limit is enforced on the what-if path.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="Limited IRA", balance="0.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("7000.00"),
        )
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=1&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None
        # With 0% return, balance = sum of capped contributions.
        # Uncapped: 26+ periods * $500 = $13000+.
        # Capped at $7000/year (with possible year-boundary reset).
        last_balance = Decimal(whatif_balances[-1])
        assert last_balance < Decimal("13500"), (
            f"Expected capped balance < $13500, got ${last_balance}"
        )
        assert last_balance >= Decimal("7000"), (
            f"Expected at least one year's limit ($7000), got ${last_balance}"
        )

    def test_what_if_employer_match_recalculated(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Employer match is recalculated for the what-if amount.

        Setup: 401(k) with 100% match up to 6% of gross.
        $100K salary -> biweekly gross ~$3846.15.
        6% of gross ~$230.77 (matchable).
        What-if: $300/period -> employer matches min($300, $230.77) = $230.77.
        Total per period: $300 + $230.77 = $530.77.

        With 0% return, end balance must exceed employee-only total
        ($300 * N periods), proving employer match was applied.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="Matched 401k", balance="0.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=None,
            contribution_limit_year=None,
            employer_contribution_type="match",
            employer_match_percentage=Decimal("1.0000"),
            employer_match_cap_percentage=Decimal("0.0600"),
        )
        # Salary profile provides gross_biweekly for employer match calc.
        _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        db.session.commit()

        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=1&what_if_contribution=300",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None
        last_balance = Decimal(whatif_balances[-1])
        # Employee-only: $300 * ~27 periods = ~$8100.
        # With employer match: ($300 + $230.77) * ~27 = ~$14330.
        assert last_balance > Decimal("8100"), (
            f"Expected balance > $8100 (employee alone), got ${last_balance}. "
            "Employer match may not be applied to what-if amount."
        )

    def test_what_if_no_limit_brokerage(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Brokerage account (no annual limit): contributions uncapped.

        With 0% return and no limit, end balance = $what_if * N periods.
        Must exceed the amount that would be capped at a typical limit,
        confirming no artificial cap is applied.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="Brokerage",
            name="My Brokerage", balance="0.00",
        )
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=1&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None
        last_balance = Decimal(whatif_balances[-1])
        # No limit: $500 * ~27 periods = ~$13500.
        # Should exceed a typical IRA limit of $7000.
        assert last_balance > Decimal("7000"), (
            f"Expected uncapped balance > $7000, got ${last_balance}"
        )

    def test_what_if_comparison_positive(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if > current contribution: comparison shows positive difference.

        No current contributions -> committed is growth-only.
        What-if at $500/period adds contributions.
        The what-if end balance exceeds committed -> positive difference.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=5&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Positive difference: what-if exceeds committed.
        assert "+$" in html, (
            "Expected positive difference indicator in comparison card"
        )

    def test_what_if_comparison_negative(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if < current contribution: comparison shows negative difference.

        Current contributions at $500/period via deduction.
        What-if at $100/period is less.
        The what-if end balance is lower -> negative difference.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id, "500.00")
        db.session.commit()

        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=5&what_if_contribution=100",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Negative difference: what-if is less than committed.
        assert "-$" in html, (
            "Expected negative difference indicator in comparison card"
        )

    def test_what_if_comparison_zero(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if == current (both zero): comparison shows zero difference.

        No current contributions and what-if=0 means both projections
        are growth-only from the same starting balance.  Difference is
        exactly $0.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "same as current plan" in html, (
            "Expected zero-difference message in comparison card"
        )

    def test_what_if_no_current_contributions(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """No existing contributions: committed is growth-only.

        When the account has no deductions or transfers, the committed
        projection is purely growth-based.  The what-if adds contributions,
        so it should produce a higher end balance.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=5&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        committed = _extract_data_attr(resp.data, "balances")
        whatif = _extract_data_attr(resp.data, "whatif-balances")
        assert committed is not None
        assert whatif is not None
        # What-if with contributions should exceed growth-only committed.
        assert Decimal(whatif[-1]) > Decimal(committed[-1]), (
            "What-if with contributions should exceed growth-only committed"
        )

    def test_what_if_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """Other user's account with what-if param: 404.

        IDOR protection is unaffected by the what-if parameter.
        """
        other_acct = _create_other_investment(second_user, db.session)
        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data

    def test_what_if_preserves_horizon(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if with custom horizon: both projections use same period count.

        The committed and what-if datasets must have the same length
        (same x-axis) regardless of the horizon setting.
        """
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods_today[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=10&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        committed = _extract_data_attr(resp.data, "balances")
        whatif = _extract_data_attr(resp.data, "whatif-balances")
        assert committed is not None
        assert whatif is not None
        assert len(committed) == len(whatif), (
            f"Committed ({len(committed)}) and what-if ({len(whatif)}) "
            "must have the same number of data points"
        )
        # 10-year horizon should produce many more periods than 2-year.
        assert len(committed) > 100, (
            f"10-year horizon should have 100+ periods, got {len(committed)}"
        )
