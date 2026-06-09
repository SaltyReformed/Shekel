"""
Tests for the shared projection-inputs helpers (F-22 / Commit 18).

Two flavours of test:

- Equivalence lock (C18-1): the new
  :func:`build_investment_projection_inputs` returns the same
  :class:`InvestmentInputs` as the previous inline
  :func:`calculate_investment_inputs` kwargs splat that lived in the
  three dashboard services.  If a future change drifts the helper
  away from the engine call, this test fails loud.

- Query-builder shape: the deduction-loader helpers return rows that
  match the filter contract the four consumers depend on (active
  profile, active deduction, target_account_id membership).  Uses
  the live test DB.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import (
    CalcMethodEnum,
    DeductionTimingEnum,
    EmployerContributionTypeEnum,
)
from app.extensions import db
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.services.investment_projection import (
    InvestmentInputs,
    calculate_investment_inputs,
)
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_account,
    load_active_deductions_for_accounts,
)


def _flat_id():
    return ref_cache.calc_method_id(CalcMethodEnum.FLAT)


@dataclass
class _FakeDeduction:
    amount: Decimal
    calc_method_id: int
    annual_salary: Decimal
    pay_periods_per_year: int


@dataclass
class _FakeStatus:
    excludes_from_balance: bool = False
    is_settled: bool = False


@dataclass
class _FakeContribution:
    estimated_amount: Decimal
    pay_period_id: int
    status: _FakeStatus = field(default_factory=_FakeStatus)


@dataclass
class _FakePeriod:
    id: int
    start_date: date
    period_index: int


@dataclass
class _FakeInvestmentParams:
    assumed_annual_return: Decimal
    annual_contribution_limit: Decimal
    employer_contribution_type_id: int
    employer_flat_percentage: Decimal = Decimal("0")
    employer_match_percentage: Decimal = Decimal("0")
    employer_match_cap_percentage: Decimal = Decimal("0")


class TestBuildInvestmentProjectionInputsEquivalence:
    """C18-1: lock the helper to the engine's exact result.

    One flat deduction + one settled contribution + employer flat
    percentage produces a fully-populated :class:`InvestmentInputs`.
    Both call paths (direct ``calculate_investment_inputs`` kwargs
    splat AND the new ``build_investment_projection_inputs`` wrapper)
    are exercised on the SAME input objects; their outputs must be
    byte-identical.

    Hand-computed expectations (gross = 100000 / 26 = 3846.15;
    per-period deduction = 500.00; contribution per period =
    400 / 2 = 200.00; total per period = 700.00; YTD over both
    contribution periods = 200 + 200 = 400.00; employer flat 5%
    of gross 3846.15 = 192.31 -- carried as flat_percentage in
    employer_params, not directly computed by this helper):
    """

    @staticmethod
    def _fixture_inputs():
        params = _FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            ),
            employer_flat_percentage=Decimal("0.05"),
        )
        deductions = [
            _FakeDeduction(
                amount=Decimal("500.00"),
                calc_method_id=_flat_id(),
                annual_salary=Decimal("100000"),
                pay_periods_per_year=26,
            ),
        ]
        periods = [
            _FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
            _FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1),
        ]
        contributions = [
            _FakeContribution(
                estimated_amount=Decimal("200"), pay_period_id=1,
                status=_FakeStatus(is_settled=True),
            ),
            _FakeContribution(
                estimated_amount=Decimal("200"), pay_period_id=2,
                status=_FakeStatus(is_settled=True),
            ),
        ]
        return params, deductions, contributions, periods

    def test_helper_matches_inline_kwargs_splat(self):
        """build_investment_projection_inputs == calculate_investment_inputs kwargs splat.

        The pre-Commit-18 call shape (direct kwargs splat) and the
        post-Commit-18 helper invocation must produce identical
        :class:`InvestmentInputs` for the same input objects.
        """
        params, deductions, contributions, periods = self._fixture_inputs()
        gross_biweekly = Decimal("3846.15")  # 100000/26 quantised; matches deduction gross

        # The pre-Commit-18 inline kwargs splat that lived in each
        # dashboard service.  Reproduced here exactly so a future
        # divergence between the wrapper and the engine surfaces.
        inline_result = calculate_investment_inputs(
            investment_params=params,
            deductions=deductions,
            all_contributions=contributions,
            all_periods=periods,
            current_period=periods[1],
            salary_gross_biweekly=gross_biweekly,
        )

        helper_result = build_investment_projection_inputs(
            params, deductions, contributions, periods, periods[1], gross_biweekly,
        )

        assert isinstance(helper_result, InvestmentInputs)
        assert helper_result.periodic_contribution == inline_result.periodic_contribution
        assert helper_result.employer_params == inline_result.employer_params
        assert (
            helper_result.annual_contribution_limit
            == inline_result.annual_contribution_limit
        )
        assert helper_result.ytd_contributions == inline_result.ytd_contributions
        assert helper_result.gross_biweekly == inline_result.gross_biweekly

    def test_helper_returns_expected_decimal_values(self):
        """Hand-computed Decimal arithmetic locks the fixture's expected values.

        - periodic_contribution = 500.00 (deduction) + (200+200)/2 (avg)
          = 500.00 + 200.00 = 700.00
        - ytd_contributions = 200 + 200 = 400 (both contributions in
          2026 up to current_period=periods[1])
        - annual_contribution_limit = 23500
        - employer_params.flat_percentage = Decimal("0.05")
        - employer_params.gross_biweekly = 3846.15
        """
        params, deductions, contributions, periods = self._fixture_inputs()
        gross_biweekly = Decimal("3846.15")
        result = build_investment_projection_inputs(
            params, deductions, contributions, periods, periods[1], gross_biweekly,
        )
        assert result.periodic_contribution == Decimal("700.00")
        assert result.ytd_contributions == Decimal("400")
        assert result.annual_contribution_limit == Decimal("23500")
        assert result.employer_params is not None
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        # gross_biweekly here is the deduction-derived gross (100000/26)
        # which the engine populates from the deduction record itself,
        # not the salary_gross_biweekly kwarg (that is only the fallback
        # when no deductions are provided).
        assert result.employer_params["gross_biweekly"] == Decimal("3846.15")


def _seed_deductions_fixture(app, db, seed_user, seed_second_user):
    """Seed two investment accounts + active and inactive deductions.

    Acct A has one active deduction (worth 500).  Acct B has one
    active deduction (worth 250).  Other user has an active
    deduction on their own account.  An additional inactive
    deduction on Acct A and a deduction on an inactive salary
    profile must NOT appear in the loader's results.

    Returns a dict of ids the caller's assertions consume.  Must run
    inside an active ``app.app_context()`` so the inserts share the
    test's session.
    """
    from app.enums import AcctTypeEnum
    from app.models.account import Account
    from app.models.ref import FilingStatus

    user_id = seed_user["user"].id
    other_user_id = seed_second_user["user"].id
    scenario_id = seed_user["scenario"].id
    other_scenario_id = seed_second_user["scenario"].id
    bootstrap_period_id = seed_user["bootstrap_period"].id
    other_bootstrap_id = seed_second_user["bootstrap_period"].id

    retire_type_id = ref_cache.acct_type_id(AcctTypeEnum.K401)
    flat_id = ref_cache.calc_method_id(CalcMethodEnum.FLAT)
    timing_id = ref_cache.deduction_timing_id(
        DeductionTimingEnum.PRE_TAX,
    )
    filing_status_id = (
        db.session.query(FilingStatus).filter_by(name="single").one().id
    )

    acct_a = Account(
        user_id=user_id, name="Acct A", account_type_id=retire_type_id,
        current_anchor_balance=Decimal("0.00"),
        current_anchor_period_id=bootstrap_period_id,
    )
    acct_b = Account(
        user_id=user_id, name="Acct B", account_type_id=retire_type_id,
        current_anchor_balance=Decimal("0.00"),
        current_anchor_period_id=bootstrap_period_id,
    )
    other_acct = Account(
        user_id=other_user_id, name="Other Acct",
        account_type_id=retire_type_id,
        current_anchor_balance=Decimal("0.00"),
        current_anchor_period_id=other_bootstrap_id,
    )
    db.session.add_all([acct_a, acct_b, other_acct])
    db.session.flush()

    active_profile = SalaryProfile(
        user_id=user_id, scenario_id=scenario_id,
        name="Active", annual_salary=Decimal("100000"),
        pay_periods_per_year=26, state_code="NC",
        filing_status_id=filing_status_id, is_active=True,
    )
    inactive_profile = SalaryProfile(
        user_id=user_id, scenario_id=scenario_id,
        name="Inactive", annual_salary=Decimal("80000"),
        pay_periods_per_year=26, state_code="NC",
        filing_status_id=filing_status_id, is_active=False,
    )
    other_profile = SalaryProfile(
        user_id=other_user_id, scenario_id=other_scenario_id,
        name="Other", annual_salary=Decimal("100000"),
        pay_periods_per_year=26, state_code="NC",
        filing_status_id=filing_status_id, is_active=True,
    )
    db.session.add_all([active_profile, inactive_profile, other_profile])
    db.session.flush()

    active_a = PaycheckDeduction(
        salary_profile_id=active_profile.id, target_account_id=acct_a.id,
        name="A", amount=Decimal("500"), calc_method_id=flat_id,
        deduction_timing_id=timing_id, is_active=True,
    )
    active_b = PaycheckDeduction(
        salary_profile_id=active_profile.id, target_account_id=acct_b.id,
        name="B", amount=Decimal("250"), calc_method_id=flat_id,
        deduction_timing_id=timing_id, is_active=True,
    )
    inactive_dedn = PaycheckDeduction(
        salary_profile_id=active_profile.id, target_account_id=acct_a.id,
        name="A-inactive", amount=Decimal("999"), calc_method_id=flat_id,
        deduction_timing_id=timing_id, is_active=False,
    )
    inactive_profile_dedn = PaycheckDeduction(
        salary_profile_id=inactive_profile.id,
        target_account_id=acct_a.id, name="A-inactive-profile",
        amount=Decimal("888"), calc_method_id=flat_id,
        deduction_timing_id=timing_id, is_active=True,
    )
    other_user_dedn = PaycheckDeduction(
        salary_profile_id=other_profile.id,
        target_account_id=other_acct.id, name="Other",
        amount=Decimal("777"), calc_method_id=flat_id,
        deduction_timing_id=timing_id, is_active=True,
    )
    db.session.add_all([
        active_a, active_b, inactive_dedn,
        inactive_profile_dedn, other_user_dedn,
    ])
    db.session.flush()
    return {
        "user_id": user_id,
        "other_user_id": other_user_id,
        "acct_a_id": acct_a.id,
        "acct_b_id": acct_b.id,
        "other_acct_id": other_acct.id,
        "bootstrap_period_id": bootstrap_period_id,
    }


class TestLoadActiveDeductionsHelpers:
    """Query-shape tests for the new deduction loader helpers.

    Uses the live test DB with a small fixture seeded inside each
    test's ``app.app_context()`` so the inserts share the test's
    session: two investment accounts owned by one user, one active
    deduction per account, one inactive deduction (must be
    excluded), one deduction on an inactive salary profile (must be
    excluded), and one deduction owned by another user (must be
    excluded).
    """

    def test_single_account_loader_returns_only_active_owned_rows(
        self, app, db, seed_user, seed_second_user,
    ):
        """Single-account loader filters by user, active profile, active deduction."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_account(
                ctx["user_id"], ctx["acct_a_id"],
            )
            amounts = sorted(d.amount for d in result)
            # Only the active deduction on the active profile.
            # 999 (inactive) and 888 (inactive profile) excluded.
            assert amounts == [Decimal("500")]

    def test_single_account_loader_rejects_other_user(
        self, app, db, seed_user, seed_second_user,
    ):
        """Single-account loader does not bleed across users."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_account(
                ctx["user_id"], ctx["other_acct_id"],
            )
            assert result == []

    def test_batch_loader_groups_by_target_account_id(
        self, app, db, seed_user, seed_second_user,
    ):
        """Batch loader returns dict keyed by target_account_id."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_accounts(
                ctx["user_id"], [ctx["acct_a_id"], ctx["acct_b_id"]],
            )
            assert set(result.keys()) == {ctx["acct_a_id"], ctx["acct_b_id"]}
            # Acct A: only the active 500 deduction (999 + 888 filtered).
            assert (
                [d.amount for d in result[ctx["acct_a_id"]]]
                == [Decimal("500")]
            )
            # Acct B: only the active 250 deduction.
            assert (
                [d.amount for d in result[ctx["acct_b_id"]]]
                == [Decimal("250")]
            )

    def test_batch_loader_empty_account_ids(
        self, app, db, seed_user, seed_second_user,
    ):
        """Batch loader returns {} for empty account_ids without an IN () query."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_accounts(
                ctx["user_id"], [],
            )
            assert result == {}

    def test_batch_loader_omits_accounts_with_no_deductions(
        self, app, db, seed_user, seed_second_user,
    ):
        """Accounts without active deductions are absent from the dict."""
        from app.enums import AcctTypeEnum
        from app.models.account import Account
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            retire_type_id = ref_cache.acct_type_id(AcctTypeEnum.K401)
            bare_acct = Account(
                user_id=ctx["user_id"], name="Bare",
                account_type_id=retire_type_id,
                current_anchor_balance=Decimal("0.00"),
                current_anchor_period_id=ctx["bootstrap_period_id"],
            )
            db.session.add(bare_acct)
            db.session.flush()
            result = load_active_deductions_for_accounts(
                ctx["user_id"],
                [ctx["acct_a_id"], bare_acct.id],
            )
            assert ctx["acct_a_id"] in result
            assert bare_acct.id not in result
