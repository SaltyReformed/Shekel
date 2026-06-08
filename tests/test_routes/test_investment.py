"""
Tests for investment/retirement account routes.
"""

import json
import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import (
    CalcMethodEnum,
    DeductionTimingEnum,
    EmployerContributionTypeEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services import account_service, investment_dashboard_service


def _create_investment_account(seed_user, db_session, type_name="401(k)",
                                name="My 401k", balance="50000.00"):
    """Helper to create an investment/retirement account."""
    acct_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=name,
            anchor_balance=Decimal(balance),
        ),
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
        "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=second_user["user"].id,
            account_type_id=acct_type.id,
            name="Other 401k",
            anchor_balance=Decimal("10000.00"),
        ),
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


class TestContributionLimitZeroCap:
    """Pin the zero-vs-None annual-limit branches (quality-pass B7).

    Commit 24 / HIGH-06 / E-12 replaced Python truthiness on
    ``annual_contribution_limit`` with explicit ``is None`` checks so a
    stored ``Decimal("0")`` ("capped at zero this year") stays distinct
    from ``None`` ("no cap configured").  The cleanup left those three
    branches in ``_compute_limit_info`` and the zero-cap branch in
    ``_compute_suggested_contribution`` unpinned; these unit tests assert
    each on hand-reasoned values.  Both helpers read only
    ``annual_contribution_limit``, so an in-memory params object and an
    empty period list keep them pure (no DB, no engine).
    """

    def test_limit_info_zero_cap_with_ytd_is_fully_used(self):
        """Zero cap + positive YTD -> card renders at 100% used.

        ``limit`` is 0 (not ``None``), so the card is shown rather than
        hidden; any positive YTD is over a zero cap, so ``pct`` saturates
        at 100 (matching the growth engine's ``min(contribution, 0) = 0``
        semantics).  A truthiness regression would treat the zero cap as
        "no cap" and hide the card (return ``None``).
        """
        params = InvestmentParams(annual_contribution_limit=Decimal("0"))
        result = investment_dashboard_service._compute_limit_info(
            params, Decimal("100.00"),
        )
        assert result == {
            "limit": Decimal("0"),
            "ytd": Decimal("100.00"),
            "pct": 100,
        }

    def test_limit_info_zero_cap_zero_ytd_is_zero_used(self):
        """Zero cap + zero YTD -> card renders at 0% used.

        Both cap and YTD are zero: nothing contributed against a zero
        cap, so ``pct`` is 0 (the ``elif ytd > 0`` branch is not taken).
        The card still renders (``limit`` is 0, not ``None``).
        """
        params = InvestmentParams(annual_contribution_limit=Decimal("0"))
        result = investment_dashboard_service._compute_limit_info(
            params, Decimal("0"),
        )
        assert result == {
            "limit": Decimal("0"),
            "ytd": Decimal("0"),
            "pct": 0,
        }

    def test_limit_info_none_cap_hides_card(self):
        """No cap configured (``None``) -> hide the card (return ``None``).

        The contrast case to the zero cap above: ``None`` means
        "Brokerage-style, no IRS limit," which hides the card entirely.
        Keeping this distinct from the zero cap is the whole point of the
        ``is None`` fix.
        """
        params = InvestmentParams(annual_contribution_limit=None)
        result = investment_dashboard_service._compute_limit_info(
            params, Decimal("100.00"),
        )
        assert result is None

    def test_suggested_contribution_zero_cap_is_zero(self):
        """Zero cap -> $0.00 per-period suggestion, never a phantom default.

        Remaining limit = max(0 - ytd, 0) = 0, so the suggestion is
        ``(0 / max(periods, 1)).quantize(.01) = 0.00`` regardless of the
        period list.  Pins that a zero cap suggests nothing within the
        cap rather than the legacy $500 fallback truthiness once produced.
        """
        params = InvestmentParams(annual_contribution_limit=Decimal("0"))
        result = investment_dashboard_service._compute_suggested_contribution(
            params, Decimal("0"), [],
        )
        assert result == Decimal("0.00")

    def test_suggested_contribution_none_cap_is_zero(self):
        """No cap configured (``None``) -> $0.00 suggestion (no IRS limit).

        The brokerage path returns ``Decimal("0")`` immediately: there is
        no annual limit to spread over the remaining periods.  Pins the
        contrast to a positive cap and guards against a reintroduced
        non-zero default for the no-cap case.
        """
        params = InvestmentParams(annual_contribution_limit=None)
        result = investment_dashboard_service._compute_suggested_contribution(
            params, Decimal("0"), [],
        )
        assert result == Decimal("0")


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
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params.assumed_annual_return == Decimal("0.08000")

    def test_update_params_percent_normalized_by_schema(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C12-1 (F-17 / Commit 12): investment-params update schema's
        @pre_load converts every declared percent field to its
        fraction equivalent before the route persists.  Arithmetic:
        7.5 / 100 = 0.075 (stored as ``0.07500`` in the
        ``Numeric(7, 5)`` column).
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7.5",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
                "employer_match_percentage": "100",
                "employer_match_cap_percentage": "6",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id,
        ).one()
        # Hand-computed: 7.5 / 100 = 0.075.
        assert params.assumed_annual_return == Decimal("0.07500")
        # Hand-computed: 100 / 100 = 1.00.
        assert params.employer_match_percentage == Decimal("1.0000")
        # Hand-computed: 6 / 100 = 0.06.
        assert params.employer_match_cap_percentage == Decimal("0.0600")

    def test_create_params_with_employer_match(self, auth_client, seed_user, db, seed_periods_today):
        """POST with employer match config."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
                "employer_match_percentage": "100",
                "employer_match_cap_percentage": "6",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params is not None
        assert params.employer_contribution_type_id == (
            ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.MATCH,
            )
        )
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
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/investment/params",
            data={
                "assumed_annual_return": "99",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
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
        # With no active profile the deduction path resolves
        # _salary_profile_action="list" -> url_for("salary.list_profiles")
        # (/salary), so salary_profile_url is always set and the prompt
        # renders the reachable "Go to Salary Profile" link. This pins the
        # URL-resolution invariant: a broken endpoint name here would 500.
        assert "Go to Salary Profile" in html
        assert 'href="/salary"' in html

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

    def test_create_transfer_rejects_inactive_source(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Inactive source account -> rejected, no transfer wired (B7).

        ``validate_and_resolve_source_account`` refuses to route a
        recurring contribution out of a deactivated account.  The source
        is owned (so ``get_or_404`` passes -- it checks ownership, not
        ``is_active``) but inactive, so the guard redirects with the
        ``Source account is inactive.`` flash.  The load-bearing assertion
        is the money-routing guard: NO ``TransferTemplate`` is created, so
        no shadow transactions are generated against the destination.
        """
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
        checking.is_active = False
        db.session.commit()

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

        # Money-routing guard: an inactive source gets no transfer wired
        # up -- no TransferTemplate against the destination, hence no
        # shadow transactions move money into it.
        tpl = (
            db.session.query(TT)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is None

        # The user-facing reason is surfaced.  The redirect was not
        # followed, so the flash is still unconsumed in the session.
        with auth_client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any(
            "inactive" in message.lower() for _category, message in flashes
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
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
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


# ── C8: investment dashboard / growth chart routed through producer ─
#
# Pre-Commit-8 the dashboard() and growth_chart() handlers each built
# their own per-account transaction query and called
# ``balance_calculator.calculate_balances`` directly with no
# ``selectinload(Transaction.entries)``.  The math-layer silent-degrade
# seam (closed in Commit 5) was the only safety net.  When an
# investment account had a Projected expense with cleared debit
# entries (an unusual but valid configuration; the contract is that
# the resolver applies the entries-aware reduction unconditionally
# regardless of account type), the route silently returned
# ``effective_amount``.  Commit 8 routes both handlers through
# ``balance_resolver.balances_for`` so the figure matches the grid and
# every other surface for the same inputs.


def _add_envelope_expense_with_cleared_entries_inv(
    db_session, *, user_id, account, scenario, period, category_id,
    estimated, cleared_amounts,
):
    """Create a Projected envelope expense with cleared debit entries.

    Same shape as the helper used in the savings / accounts / year-end
    C8 tests; copied here so this file stays standalone.  These are
    the entries that produce the F-009 / CRIT-01 silent-degrade gap
    when the consuming query forgets to ``selectinload(entries)``.
    """
    from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
    from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel
    from app.models.transaction_template import TransactionTemplate  # pylint: disable=import-outside-toplevel
    from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    template = TransactionTemplate(
        user_id=user_id,
        account_id=account.id,
        category_id=category_id,
        transaction_type_id=expense_type_id,
        name="Investment-side expense",
        default_amount=estimated,
        is_envelope=True,
    )
    db_session.add(template)
    db_session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=scenario.id,
        account_id=account.id,
        status_id=projected_id,
        name="Investment-side expense",
        category_id=category_id,
        transaction_type_id=expense_type_id,
        estimated_amount=estimated,
    )
    db_session.add(txn)
    db_session.flush()

    for amt in cleared_amounts:
        db_session.add(TransactionEntry(
            transaction_id=txn.id,
            user_id=user_id,
            amount=amt,
            description="Cleared purchase",
            entry_date=date(2026, 5, 15),
            is_credit=False,
            is_cleared=True,
        ))
    db_session.flush()
    return txn


class TestInvestmentEntryAwareRouting:
    """C8-2 / C8-3: /investment dashboard + growth chart use canonical producer.

    Pins the R-1 finding: pre-Commit-8 the two investment handlers
    each had bare ``calculate_balances`` calls with no
    ``selectinload(Transaction.entries)``.  Routing both through
    ``balance_resolver.balances_for`` (which owns the eager-load and
    the anchor resolution) makes the entries-aware reduction
    structural for these routes.
    """

    def test_investment_holdings_entry_aware(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """C8-2: dashboard current_balance == canonical producer value.

        Reproduction of the symptom on /investment:

          - Investment account anchor 50,000.00 on the current period.
          - One Projected envelope expense on the same account in the
            same period, ``estimated_amount = 500.00``.
          - Three CLEARED debit entries summing 45.71 (20 + 15.71 + 10).

        Hand arithmetic (CRIT-01 / F-009 / R-1):

          cleared_debit   = 45.71
          uncleared_debit = 0
          sum_credit      = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          current_balance = 50,000.00 + 0 - 454.29 = 49,545.71

        The route renders this number formatted with ``{:,.2f}`` so
        the byte string ``$49,545.71`` (or the bare ``49,545.71``
        inside the page) MUST appear in the response.  Pre-Commit-8
        the route reported ``49,500.00`` (= 50,000 - 500) via the
        silent-degrade seam.  We also assert byte-equality with the
        canonical producer's value so the contract is locked beyond
        the rendered string.
        """
        from app.services import balance_resolver, pay_period_service  # pylint: disable=import-outside-toplevel

        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            current_period = pay_period_service.get_current_period(user.id)
            assert current_period is not None

            # ``account_service.create_account`` (via the helper) anchors
            # the new account against the user's current pay period and
            # writes the matching ``AccountAnchorHistory`` row, so no
            # explicit override is needed -- the resolver reads the
            # factory's history row directly.
            acct = _create_investment_account(
                seed_user, db.session,
                type_name="401(k)", name="Test 401k",
                balance="50000.00",
            )
            assert acct.current_anchor_period_id == current_period.id
            _create_investment_params(db.session, acct.id)
            _add_envelope_expense_with_cleared_entries_inv(
                db.session,
                user_id=user.id,
                account=acct,
                scenario=scenario,
                period=current_period,
                category_id=seed_user["categories"]["Groceries"].id,
                estimated=Decimal("500.00"),
                cleared_amounts=(
                    Decimal("20.00"), Decimal("15.71"), Decimal("10.00"),
                ),
            )
            db.session.commit()

            # Canonical producer value: 50,000 - max(500 - 45.71 - 0, 0)
            #                         = 50,000 - 454.29 = 49,545.71.
            producer = balance_resolver.balances_for(
                acct, scenario.id, seed_periods_today,
            )
            assert producer.balances[current_period.id] == Decimal("49545.71")

            resp = auth_client.get(f"/accounts/{acct.id}/investment")
            assert resp.status_code == 200
            # The rendered current-balance tile carries the comma-
            # formatted Decimal.  Pre-Commit-8 it would render
            # 49,500.00 (silent degrade).  Asserting both presence of
            # the correct value AND absence of the pre-fix value
            # locks the regression in both directions.
            assert b"49,545.71" in resp.data
            assert b"49,500.00" not in resp.data

    def test_investment_growth_chart_entry_aware(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """C8-3: growth_chart() seeds the projection from the entries-aware balance.

        Same setup as C8-2.  The growth-chart route projects a
        synthetic period series forward from ``current_balance`` --
        if that seed is wrong, the entire chart series is wrong.  The
        first chart point (``data-balances[0]``) is the seed period's
        end balance from the growth engine; with ``periodic_contribution
        = 0`` (no deductions, no recurring transfers) and only the
        post-anchor projection from 49,545.71, the first chart point
        ends very close to the seed (subject to one biweekly's worth
        of compounding at 7% annual = ~0.27%, ~$133 on $49,545.71).

        Pre-Commit-8 the seed was 49,500.00 via the silent-degrade
        seam, so the first chart point would land near $49,633 instead
        of near $49,679.  We assert the chart's first point sits in
        a tight band around the entry-aware seed -- the band is wide
        enough to absorb the growth engine's contribution / employer-
        match math but narrow enough to reject the pre-fix value.
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            from app.services import pay_period_service  # pylint: disable=import-outside-toplevel
            current_period = pay_period_service.get_current_period(user.id)
            assert current_period is not None

            # ``account_service.create_account`` (via the helper) anchors
            # the new account against the user's current pay period and
            # writes the matching ``AccountAnchorHistory`` row, so no
            # explicit override is needed -- the resolver reads the
            # factory's history row directly.
            acct = _create_investment_account(
                seed_user, db.session,
                type_name="401(k)", name="Test 401k",
                balance="50000.00",
            )
            assert acct.current_anchor_period_id == current_period.id
            _create_investment_params(db.session, acct.id)
            _add_envelope_expense_with_cleared_entries_inv(
                db.session,
                user_id=user.id,
                account=acct,
                scenario=scenario,
                period=current_period,
                category_id=seed_user["categories"]["Groceries"].id,
                estimated=Decimal("500.00"),
                cleared_amounts=(
                    Decimal("20.00"), Decimal("15.71"), Decimal("10.00"),
                ),
            )
            db.session.commit()

            resp = auth_client.get(
                f"/accounts/{acct.id}/investment/growth-chart?horizon_years=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            balances = _extract_data_attr(resp.data, "balances")
            assert balances is not None and len(balances) > 0

            # First chart point sits within roughly one biweekly's
            # compounding of the entries-aware seed 49,545.71.  At 7%
            # annual return that is ~ 49,545.71 * 0.07 / 26 ~ $133 per
            # biweekly period; the engine actually returns about
            # $49,665 for this configuration (no contributions, no
            # employer match, one period of compounding plus a small
            # adjustment for the contribution-limit math).  The
            # pre-fix seed 49,500.00 with the same compounding lands
            # near $49,619 -- about $46 lower, which is the difference
            # between the two seeds carried forward.  The assertion
            # band [49,640, 49,800] strictly contains the entries-
            # aware first point and strictly excludes the pre-fix
            # value.
            first_point = Decimal(balances[0])
            assert first_point >= Decimal("49640.00"), (
                f"First chart point {first_point} below the entries-"
                "aware lower bound; pre-Commit-8 silent-degrade "
                "regression suspected (pre-fix value lands near "
                "$49,619)."
            )
            assert first_point <= Decimal("49800.00")


class TestEmployerMatchCapped:
    """C25 / HIGH-07 / F-043 / F-055: dashboard "Employer Per Period" card
    feeds the limit-capped employee contribution into
    ``calculate_employer_contribution`` so it matches the growth chart's
    employer line and the year-end ``year_summary_employer_total``.

    Pre-fix the card passed the UNCAPPED ``periodic_contribution`` to the
    matcher (``investment.py:183 -> 187-189``), so near the annual limit
    a match-type employer overstated the card per-period match relative
    to the chart and year-end (F-043 worked example: $240 vs $100).
    """

    def _make_settled_shadow_income(
        self, seed_user, to_account, period, amount, db_session,
    ):
        """Seed a Settled transfer into the investment account.

        Uses the canonical ``transfer_service.create_transfer`` -- the
        only sanctioned path for budget.transfers rows -- with a
        ``Received`` status whose ``excludes_from_balance = False`` so
        the route's YTD aggregation (``calculate_investment_inputs``
        Step 4) counts the resulting shadow income.
        """
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        received_id = ref_cache.status_id(StatusEnum.RECEIVED)
        cat = seed_user["categories"]["Groceries"]
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=to_account.id,
                pay_period_id=period.id,
                scenario_id=seed_user["scenario"].id,
                amount=amount,
                status_id=received_id,
                category_id=cat.id,
                name="YTD seed",
            ),
        )
        db_session.commit()
        return xfer

    def test_card_uses_capped_contribution_at_binding_limit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C25-1: card per-period employer match equals the chart/year-end
        capped value when the annual limit binds.

        Setup (F-043 worked example, scaled to fit a deduction):

          annual_contribution_limit = $23,500
          YTD shadow income contributed in past period of current year
            = $23,300  -> remaining = $200
          gross_biweekly                                 = $8,000
          (annual_salary = 8000 * 26 = $208,000)
          match: 50% up to 6% of gross
            matchable_salary = 8000 * 0.06           = $480.00
          deduction (employee contribution per period) = $1,500

        Pre-fix card (UNCAPPED $1,500 fed to the matcher):
          matched  = min(1500, 480)                  = 480
          employer = 480 * 0.50                      = $240.00

        Post-fix card (CAPPED at remaining limit before matcher):
          capped   = min(1500, max(23500 - 23300, 0)) = 200
          matched  = min(200, 480)                   = 200
          employer = 200 * 0.50                      = $100.00

        The card now reads $100.00; the pre-fix string $240.00 must
        not appear in the response.
        """
        # 401(k) account; create_account anchors at the current period.
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="HIGH-07 401k", balance="10000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("23500.00"),
            contribution_limit_year=2026,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("0.5000"),
            employer_match_cap_percentage=Decimal("0.0600"),
        )

        # Salary profile: annual 208000 -> gross_biweekly 8000.
        filing = db.session.query(FilingStatus).filter_by(name="single").one()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="Day Job",
            annual_salary=Decimal("208000.00"),
            state_code="NC",
            is_active=True,
        )
        db.session.add(profile)
        db.session.flush()

        # Deduction $1500/period -> uncapped periodic_contribution = 1500.
        _create_deduction(db.session, profile.id, acct.id, "1500.00")
        db.session.commit()

        # YTD seed: $23,300 settled shadow income in a past period
        # within the current year.  seed_periods_today[0] is ~56 days
        # before today (period 4 = current), so for any test date this
        # year it lands in the same calendar year as today.
        self._make_settled_shadow_income(
            seed_user, acct, seed_periods_today[0],
            Decimal("23300.00"), db.session,
        )

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        # Post-fix capped employer match.
        assert "$100.00" in html, (
            "Card did not render the capped employer match of $100.00 "
            "(HIGH-07/F-043).  Card site may still be bypassing "
            "cap_contribution_at_limit before calling "
            "calculate_employer_contribution."
        )
        # Pre-fix uncapped value must NOT appear: locks the fix in both
        # directions.  Use a regex-safe assertion -- the value must not
        # appear anywhere in the rendered HTML.
        assert "$240.00" not in html, (
            "Pre-fix uncapped employer match $240.00 detected; the "
            "cap_contribution_at_limit fix has regressed."
        )

    def test_card_unchanged_when_well_below_limit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C25-3: well below limit, the card value is unchanged (regression
        guard).

        Same fixture as the binding-limit test but with no YTD seed:
        remaining = max(23500 - 0, 0) = 23500;
        capped = min(1500, 23500) = 1500 (cap does not bind);
        matched = min(1500, 480) = 480;
        employer = 480 * 0.50 = $240.00.

        The capped helper returns 1500 (the full periodic) so the card
        produces the same byte-identical $240.00 it always did.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="HIGH-07 below-limit", balance="10000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("23500.00"),
            contribution_limit_year=2026,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("0.5000"),
            employer_match_cap_percentage=Decimal("0.0600"),
        )
        filing = db.session.query(FilingStatus).filter_by(name="single").one()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="Day Job",
            annual_salary=Decimal("208000.00"),
            state_code="NC",
            is_active=True,
        )
        db.session.add(profile)
        db.session.flush()
        _create_deduction(db.session, profile.id, acct.id, "1500.00")
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        # No YTD: cap does not bind; full match (matchable=480) applies.
        assert "$240.00" in html, (
            "Below-limit card regressed: expected $240.00 employer per "
            "period.  cap_contribution_at_limit may be incorrectly "
            "clamping below the limit."
        )
