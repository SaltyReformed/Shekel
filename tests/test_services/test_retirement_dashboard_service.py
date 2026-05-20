"""
Shekel Budget App -- Retirement Dashboard Service Tests

Unit tests for the retirement_dashboard_service module, verifying that
the extracted gap analysis and projection logic produces correct
financial computations independently of the Flask route layer.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.investment_params import InvestmentParams
from app.models.pension_profile import PensionProfile
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.user import UserSettings
from app.services import (
    account_service,
    balance_resolver,
    pay_period_service,
    retirement_dashboard_service,
)


class TestComputeGapData:
    """Tests for the top-level compute_gap_data orchestrator."""

    def test_returns_expected_keys(self, app, db, seed_user, seed_periods):
        """Return dict contains all template context keys."""
        with app.app_context():
            result = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            expected_keys = {
                "gap_analysis", "chart_data", "pension_benefit",
                "retirement_account_projections", "settings",
                "salary_profiles", "pensions",
            }
            assert set(result.keys()) == expected_keys

    def test_user_with_no_accounts_returns_safe_defaults(
        self, app, db, seed_user, seed_periods
    ):
        """User with no retirement accounts gets zero projections."""
        with app.app_context():
            result = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            assert result["retirement_account_projections"] == []
            assert result["pension_benefit"] is None

    def test_user_with_no_salary_profile(self, app, db, seed_user, seed_periods):
        """User with no salary profile still returns valid structure."""
        with app.app_context():
            result = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            assert result["gap_analysis"] is not None
            assert result["salary_profiles"] == []

    def test_pensions_list_populated(self, app, db, seed_user, seed_periods):
        """Active pensions are included in the pensions list."""
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Main",
                annual_salary=Decimal("80000"),
                pay_periods_per_year=26,
                state_code="NC",
                is_active=True,
            )
            db.session.add(profile)
            db.session.flush()

            pension = PensionProfile(
                user_id=seed_user["user"].id,
                salary_profile_id=profile.id,
                name="State Pension",
                benefit_multiplier=Decimal("0.01750"),
                consecutive_high_years=4,
                hire_date=date(2010, 1, 1),
                planned_retirement_date=date(2050, 1, 1),
                is_active=True,
            )
            db.session.add(pension)
            db.session.commit()

            result = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            assert len(result["pensions"]) == 1
            assert result["pension_benefit"] is not None


class TestComputeSliderDefaults:
    """Tests for the slider default computation.

    Post-C-45 (F-100 / F-101): the returned ``current_swr`` and
    ``current_return`` keys carry :class:`~decimal.Decimal` percentages
    quantised to ``Decimal("0.01")``.  Earlier versions returned
    ``float`` and the dashboard template's ``"%.2f"|format(...)`` masked
    the precision drift; these tests pin the new Decimal contract.
    """

    def test_default_swr_uses_user_setting_as_decimal(
        self, app, db, seed_user, seed_periods,
    ):
        """``current_swr`` is a Decimal scaled from the user's stored SWR.

        ``seed_user`` constructs ``UserSettings`` with the model-level
        default ``safe_withdrawal_rate = Decimal("0.0400")``, so the
        slider default should round-trip to ``Decimal("4.00")``.
        Arithmetic: 0.0400 * 100 = 4.00.  Asserts exact equality to
        catch any future regression that re-introduces a float cast
        (which would have produced 3.9999... or 4.000000000001 instead).
        """
        with app.app_context():
            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            assert isinstance(slider["current_swr"], Decimal)
            assert slider["current_swr"] == Decimal("4.00")

    def test_default_return_when_no_accounts(self, app, db, seed_user, seed_periods):
        """``current_return`` falls back to Decimal('7.00') with no accounts.

        ``seed_user`` does not seed any retirement or investment
        accounts, so the balance-weighted average has no inputs to
        weight; the function must return the module-level
        ``_DEFAULT_RETURN_PCT`` (S&P 500 long-run real return baseline).
        Asserts type as well as value to keep the Decimal contract
        pinned (F-100 fix).
        """
        with app.app_context():
            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            assert isinstance(slider["current_return"], Decimal)
            assert slider["current_return"] == Decimal("7.00")

    def test_default_swr_when_settings_none(self, app, db, seed_user, seed_periods):
        """``current_swr`` falls back to Decimal('4.00') when settings is None.

        ``compute_slider_defaults`` accepts the dict returned by
        ``compute_gap_data``; that dict carries ``settings = None``
        only when the user has no ``UserSettings`` row.  Splicing a
        ``settings=None`` dict in-place verifies the fallback branch
        without having to delete + recreate the seeded settings row
        (which would also need to keep the rest of ``data`` intact).
        Asserts the result is the unaltered ``_DEFAULT_SWR_PCT``
        constant (Decimal('4.00'), Trinity Study baseline).
        """
        with app.app_context():
            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            data["settings"] = None
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            assert isinstance(slider["current_swr"], Decimal)
            assert slider["current_swr"] == Decimal("4.00")

    def test_zero_swr_round_trips_as_decimal_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """An explicit Decimal('0') SWR survives the round-trip as Decimal('0.00').

        Storing ``safe_withdrawal_rate = Decimal("0")`` is semantically
        distinct from ``None`` (the F-077 / C-24 CHECK constraint
        permits both NULL and zero; zero means "explicit zero rate,"
        NULL means "use the default").  This test pins the boundary:
        the function must NOT collapse a stored zero to
        ``_DEFAULT_SWR_PCT``.  Arithmetic: 0.0000 * 100 = 0.0000,
        quantised to Decimal('0.00').
        """
        with app.app_context():
            settings = (
                db.session.query(UserSettings)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            settings.safe_withdrawal_rate = Decimal("0")
            db.session.commit()

            data = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            slider = retirement_dashboard_service.compute_slider_defaults(data)
            assert isinstance(slider["current_swr"], Decimal)
            assert slider["current_swr"] == Decimal("0.00")


# ── C8: retirement projection uses the canonical entries-aware producer ─
#
# Pre-Commit-8 ``_project_retirement_accounts`` built per-account
# transaction queries with no ``selectinload(Transaction.entries)``
# and called ``balance_calculator.calculate_balances`` directly.  When
# a retirement / investment account had a Projected envelope expense
# with cleared debit entries -- unusual but a valid configuration that
# the contract must handle uniformly -- the silent-degrade seam
# (closed at the math layer by Commit 5) was the only safety net.
# Commit 8 / R-1 routes this through ``balance_resolver.balances_for``
# so the per-account ``current_balance`` input to the gap calculation
# matches the grid and /investment dashboard byte-for-byte.


def _add_envelope_expense_with_cleared_entries_ret(
    db_session, *, user_id, account, scenario_id, period, category_id,
    estimated, cleared_amounts,
):
    """Create a Projected envelope expense with cleared debit entries.

    Same shape as the helper used in the C8 year-end / investment
    tests; copied here so this file stays standalone.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    template = TransactionTemplate(
        user_id=user_id,
        account_id=account.id,
        category_id=category_id,
        transaction_type_id=expense_type_id,
        name="Retirement-side expense",
        default_amount=estimated,
        is_envelope=True,
    )
    db_session.add(template)
    db_session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=scenario_id,
        account_id=account.id,
        status_id=projected_id,
        name="Retirement-side expense",
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


class TestRetirementProjectionEntryAware:
    """C8-4: per-account current balance routed through canonical producer.

    Pins the R-1 finding for the retirement dashboard's gap-analysis
    inputs: the ``acct_balance_map`` is now built via
    ``balance_resolver.balances_for`` so the per-account
    ``current_balance`` in ``retirement_account_projections`` cannot
    disagree with the grid or /investment dashboard for the same
    inputs.
    """

    def test_retirement_projection_entry_aware(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C8-4: ``current_balance`` in projections == canonical producer value.

        Reproduction:

          - Retirement 401(k) account anchor 50,000.00 on the current
            pay period (created via ``account_service.create_account``
            which writes the matching ``AccountAnchorHistory`` row).
          - One Projected envelope expense on the same account in the
            same period, ``estimated_amount = 500.00``.
          - Three CLEARED debit entries summing 45.71 (20 + 15.71 + 10).
          - InvestmentParams set so the account is loaded into the
            retirement-types filter.
          - Active salary profile so ``compute_gap_data`` reaches the
            ``_project_retirement_accounts`` path that this commit
            touches.

        Hand arithmetic (CRIT-01 / F-009 / R-1):

          cleared_debit   = 45.71
          uncleared_debit = 0
          sum_credit      = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          current_balance = 50,000.00 + 0 - 454.29 = 49,545.71

        Pre-Commit-8 the projection's ``current_balance`` was
        50,000 - 500 = 49,500.00 via the silent-degrade seam.  Both
        the canonical producer value AND the projection's
        ``current_balance`` MUST equal Decimal("49545.71").
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            current_period = pay_period_service.get_current_period(user.id)
            assert current_period is not None

            # Active salary profile so the gap path is reachable.
            filing = db.session.query(FilingStatus).first()
            db.session.add(SalaryProfile(
                user_id=user.id,
                scenario_id=scenario.id,
                filing_status_id=filing.id,
                name="Day Job",
                annual_salary=Decimal("80000.00"),
                pay_periods_per_year=26,
                state_code="NC",
                is_active=True,
            ))

            inv_type = (
                db.session.query(AccountType)
                .filter_by(name="401(k)").one()
            )
            # ``account_service.create_account`` anchors against the
            # current pay period and writes the matching
            # ``AccountAnchorHistory`` row, so the resolver reads a
            # consistent dated source of truth without an explicit
            # override.
            acct = account_service.create_account(
                user_id=user.id,
                account_type_id=inv_type.id,
                name="C8 401k",
                anchor_balance=Decimal("50000.00"),
            )
            db.session.flush()
            assert acct.current_anchor_period_id == current_period.id

            db.session.add(InvestmentParams(
                account_id=acct.id,
                assumed_annual_return=Decimal("0.07000"),
                employer_contribution_type="none",
            ))

            _add_envelope_expense_with_cleared_entries_ret(
                db.session,
                user_id=user.id,
                account=acct,
                scenario_id=scenario.id,
                period=current_period,
                category_id=seed_user["categories"]["Groceries"].id,
                estimated=Decimal("500.00"),
                cleared_amounts=(
                    Decimal("20.00"), Decimal("15.71"), Decimal("10.00"),
                ),
            )
            db.session.commit()

            # Canonical producer value (the contract Commit 8 locks).
            producer = balance_resolver.balances_for(
                acct, scenario.id, seed_periods_today,
            )
            assert producer.balances[current_period.id] == Decimal("49545.71")

            result = retirement_dashboard_service.compute_gap_data(user.id)
            projections = result["retirement_account_projections"]
            target = next(
                p for p in projections if p["account"].id == acct.id
            )
            # CRIT-01 / F-009 / R-1: 50000 - max(500 - 45.71, 0)
            #                      = 50000 - 454.29 = 49,545.71.
            # Pre-Commit-8 this was 49,500.00.
            assert target["current_balance"] == Decimal("49545.71")
            assert target["current_balance"] == producer.balances[
                current_period.id
            ]
