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

from dataclasses import dataclass
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
    PricedContribution,
    build_contribution_timeline,
    calculate_investment_inputs,
)
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_account,
    load_active_deductions_for_accounts,
    load_investment_params_for_accounts,
    load_shadow_income_contributions_for_account,
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
class _FakePeriod:
    """The one thing ``calculate_investment_inputs`` reads off a period.

    A ``start_date`` and nothing else since plan step C2-f2c, which is the
    whole surface that function has left: the period LIST it took to translate
    a contribution's ``pay_period_id`` into a payday is gone, because the
    loader dates each contribution at the boundary.  Both real period types --
    :class:`~app.models.pay_period.PayPeriod` and
    :class:`~app.services.pay_calendar.DerivedPeriod` -- satisfy exactly this.
    """

    start_date: date


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
            _FakePeriod(start_date=date(2026, 1, 2)),
            _FakePeriod(start_date=date(2026, 1, 16)),
        ]
        contributions = [
            PricedContribution(
                account_id=1, payday=periods[0].start_date,
                amount=Decimal("200"), is_confirmed=True,
            ),
            PricedContribution(
                account_id=1, payday=periods[1].start_date,
                amount=Decimal("200"), is_confirmed=True,
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
            current_period=periods[1],
            salary_gross_biweekly=gross_biweekly,
        )

        helper_result = build_investment_projection_inputs(
            params, deductions, contributions, periods[1], gross_biweekly,
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
            params, deductions, contributions, periods[1], gross_biweekly,
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
    )
    acct_b = Account(
        user_id=user_id, name="Acct B", account_type_id=retire_type_id,
    )
    other_acct = Account(
        user_id=other_user_id, name="Other Acct",
        account_type_id=retire_type_id,
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
            )
            db.session.add(bare_acct)
            db.session.flush()
            result = load_active_deductions_for_accounts(
                ctx["user_id"],
                [ctx["acct_a_id"], bare_acct.id],
            )
            assert ctx["acct_a_id"] in result
            assert bare_acct.id not in result


class TestLoadInvestmentParamsForAccounts:
    """Query-shape tests for the investment-params batch loader.

    Level 1 balance-seam prep (DRY): this loader is the single home for
    the "which accounts get an :class:`InvestmentParams` row?" decision,
    scoped by the canonical classifier rather than by elimination.  Uses
    the live test DB with a mixed account set seeded inside the test's
    ``app.app_context()`` so the inserts share the test's session.
    """

    def test_returns_only_investment_accounts_with_a_params_row(
        self, app, db, seed_user,
    ):
        """Map holds exactly the INVESTMENT account that has a params row.

        Locks the classifier predicate
        (``classify_account(a) is AccountProjectionKind.INVESTMENT``):
        the loader must scope membership by the classifier, NOT by "does
        this account happen to have an InvestmentParams row?".  Mixed
        input set, all owned by ``seed_user``:

        - a 401(k) (classifier -> INVESTMENT) WITH an
          :class:`InvestmentParams` row -> present, keyed by account_id.
        - a Checking account (classifier -> PLAIN) that ALSO has an
          InvestmentParams row -> still excluded, because the classifier
          filters it out before the query runs.  If the predicate were
          dropped, the widened query would find this row and leak it
          into the result, failing the keys assertion below -- that is
          why the excluded account is deliberately given a row.
        - a second 401(k) (INVESTMENT) with NO params row -> absent,
          because the query finds no matching row for it.
        """
        from app.enums import AcctTypeEnum
        from app.models.account import Account
        from app.models.investment_params import InvestmentParams
        with app.app_context():
            user_id = seed_user["user"].id
            k401_type_id = ref_cache.acct_type_id(AcctTypeEnum.K401)
            checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)

            inv_with_params = Account(
                user_id=user_id, name="401k With Params",
                account_type_id=k401_type_id,
            )
            checking = Account(
                user_id=user_id, name="Everyday Checking",
                account_type_id=checking_type_id,
            )
            inv_without_params = Account(
                user_id=user_id, name="401k No Params",
                account_type_id=k401_type_id,
            )
            db.session.add_all(
                [inv_with_params, checking, inv_without_params],
            )
            db.session.flush()

            none_type_id = ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.NONE,
            )
            # Seed a params row on BOTH the INVESTMENT account and the
            # classifier-excluded Checking account.  The checking row is
            # the predicate trip-wire: only the classifier filter keeps
            # it out of the result, so dropping that filter would widen
            # the query, leak the checking row in, and fail the keys
            # assertion below.
            db.session.add_all([
                InvestmentParams(
                    account_id=inv_with_params.id,
                    assumed_annual_return=Decimal("0.07000"),
                    employer_contribution_type_id=none_type_id,
                ),
                InvestmentParams(
                    account_id=checking.id,
                    assumed_annual_return=Decimal("0.07000"),
                    employer_contribution_type_id=none_type_id,
                ),
            ])
            db.session.flush()

            result = load_investment_params_for_accounts(
                [inv_with_params, checking, inv_without_params],
            )

            # Exactly the investment-with-params account, keyed by id;
            # the Checking account is filtered by the classifier (despite
            # having a row) and the params-less 401(k) has no row.
            assert set(result.keys()) == {inv_with_params.id}
            assert result[inv_with_params.id].account_id == inv_with_params.id
            assert (
                result[inv_with_params.id].assumed_annual_return
                == Decimal("0.07000")
            )

    def test_empty_accounts_returns_empty_dict(self, app):
        """Empty input returns {} without issuing an IN () query."""
        with app.app_context():
            assert load_investment_params_for_accounts([]) == {}


class TestShadowContributionBoundary:
    """The BOUNDARY where a shadow contribution is valued and screened.

    ``load_shadow_income_contributions_for_account(s)`` became the one place a
    contribution's dollar is decided at plan step X-au-c2: it prices the whole
    row set through :func:`app.services.cash_ledger.contributions_by_id` and
    hands :mod:`app.services.investment_projection` frozen
    :class:`~app.services.investment_projection.PricedContribution` records.

    **These four cases are deep-quality-hunt #11, moved here whole with their
    hand-computed figures** from ``test_investment_projection`` .
    ``TestEstimatedVsEffectiveAlignment``.  A transfer shadow's
    ``actual_amount`` is normally ``None``, but a settle with a manual amount
    writes a realized actual onto BOTH shadows (the ``Transfer`` parent has no
    ``actual_amount`` column).  Once that happens the realized figure diverges
    from the estimate, and every feed the projection builds -- the averaged
    periodic contribution, the YTD display, the engine seed, and the per-period
    timeline -- has to read the realized one, or the cap/limit accounting
    charges a different dollar than the growth engine actually applies.

    They live at this tier now because it is the only tier that can still fail:
    the projection module consumes a record carrying one ``amount`` field, so
    asserting the rule there would assert that $400 averages to $400.  Here the
    rule is exercised against real rows.
    """

    @staticmethod
    def _contribution_shadow(
        seed_user, db_session, period, estimated, actual=None, *,
        settled=False, cancelled=False, account=None,
    ):
        """Seed a contribution transfer into an investment account.

        Returns ``(account, income_shadow)``.  The shadow is the INCOME leg
        landing in the investment account, which is exactly what the loader's
        ``transfer_id IS NOT NULL AND transaction_type_id = Income`` filter
        selects.

        Pass ``account`` to feed an EXISTING account a second contribution --
        the multi-period cases need one account across several periods, because
        the per-account arithmetic they grade (the average's distinct-period
        denominator, the two YTD windows) is defined over one account's rows.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum
        from app.models.transaction import Transaction
        from tests._test_helpers import create_transfer, make_investment_account

        if account is None:
            account = make_investment_account(
                seed_user, db_session, period, Decimal("1000.00"),
                name=f"401k-{period.id}-{estimated}",
            )
        transfer = create_transfer(
            seed_user, db_session, seed_user["account"], account, period,
            amount=Decimal(str(estimated)),
        )
        shadow = (
            db_session.query(Transaction)
            .filter(
                Transaction.transfer_id == transfer.id,
                Transaction.account_id == account.id,
            )
            .one()
        )
        # Mutated on BOTH legs and the parent, never on the income shadow
        # alone: Transfer Invariants 3 and 4 say a transfer's shadows share
        # their amount, status and settle day, and a fixture that grades
        # against a pair the app cannot produce is grading against a state no
        # defect could ever reach (an adversarial review's finding).
        rows = [shadow] + [
            other for other in transfer.shadow_transactions
            if other.id != shadow.id
        ]
        if actual is not None:
            for row in rows:
                row.actual_amount = Decimal(str(actual))
        if settled:
            settled_id = ref_cache.status_id(StatusEnum.RECEIVED)
            for row in rows:
                row.status_id = settled_id
                row.settled_on = period.start_date
            transfer.status_id = settled_id
        if cancelled:
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
            for row in rows:
                row.status_id = cancelled_id
            transfer.status_id = cancelled_id
        db_session.flush()
        return account, shadow

    @staticmethod
    def _params(limit=None):
        return _FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=limit,
            employer_contribution_type_id=ref_cache
            .employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
        )

    def test_settled_shadow_is_priced_at_its_realized_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """estimated $500 / actual $400 -> the record carries $400.

        The rule itself, at the tier that decides it: a figure a human read off
        a statement is a fact and the row's own amount is an inference, so the
        actual wins.  Summing ``estimated_amount`` (the pre-#11 bug) would
        carry $500.
        """
        with app.app_context():
            account, _shadow = self._contribution_shadow(
                seed_user, db.session, seed_periods[0],
                estimated="500", actual="400", settled=True,
            )
            db.session.commit()

            records = load_shadow_income_contributions_for_account(
                seed_user["user"].id, seed_user["scenario"].id,
                account.id, [seed_periods[0].id],
            ).records

            assert len(records) == 1
            assert records[0].amount == Decimal("400")
            assert records[0].is_confirmed is True
            assert records[0].account_id == account.id
            assert records[0].payday == seed_periods[0].start_date

    def test_periodic_contribution_uses_the_realized_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """One settled $500-estimated / $400-actual shadow -> average $400.

        The averaged periodic contribution over one period is the realized
        $400, NOT the estimated $500.
        """
        with app.app_context():
            account, _shadow = self._contribution_shadow(
                seed_user, db.session, seed_periods[0],
                estimated="500", actual="400", settled=True,
            )
            db.session.commit()
            period = seed_periods[0]

            records = load_shadow_income_contributions_for_account(
                seed_user["user"].id, seed_user["scenario"].id,
                account.id, [period.id],
            ).records
            result = calculate_investment_inputs(
                investment_params=self._params(), deductions=[],
                all_contributions=records, current_period=period,
            )

            # effective $400 / 1 period = $400 (NOT estimated $500).
            assert result.periodic_contribution == Decimal("400")

    def test_over_contribution_actual_above_estimate_is_honored(
        self, app, db, seed_user, seed_periods,
    ):
        """estimated $400 / actual $500 -> $500, the direction the cap must catch.

        Symmetric to the under-contribution case: a settled shadow that came in
        HIGHER than planned contributes its realized $500.  Summing
        ``estimated_amount`` would under-count it at $400 and let the annual
        limit accounting miss an over-contribution.
        """
        with app.app_context():
            account, _shadow = self._contribution_shadow(
                seed_user, db.session, seed_periods[0],
                estimated="400", actual="500", settled=True,
            )
            db.session.commit()
            period = seed_periods[0]

            records = load_shadow_income_contributions_for_account(
                seed_user["user"].id, seed_user["scenario"].id,
                account.id, [period.id],
            ).records
            result = calculate_investment_inputs(
                investment_params=self._params(limit=Decimal("23500")),
                deductions=[], all_contributions=records,
                current_period=period,
            )

            assert result.periodic_contribution == Decimal("500")
            # The displayed YTD (<= current) also reflects the realized $500.
            assert result.ytd_contributions == Decimal("500")

    def test_ytd_and_seed_both_read_the_realized_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Three settled $500-estimated / $400-actual shadows across 3 periods.

        ``ytd_contributions`` (<= current) = 3 x $400 = $1,200;
        ``ytd_contributions_seed`` (< current) = 2 x $400 = $800.  Summing
        ``estimated_amount`` would give $1,500 / $1,000.
        """
        with app.app_context():
            periods = seed_periods[:3]
            account = None
            for period in periods:
                account, _shadow = self._contribution_shadow(
                    seed_user, db.session, period,
                    estimated="500", actual="400", settled=True,
                    account=account,
                )
            db.session.commit()

            records = load_shadow_income_contributions_for_account(
                seed_user["user"].id, seed_user["scenario"].id,
                account.id, [p.id for p in periods],
            ).records
            assert len(records) == 3

            result = calculate_investment_inputs(
                investment_params=self._params(limit=Decimal("23500")),
                deductions=[], all_contributions=records,
                current_period=periods[2],
            )

            assert result.ytd_contributions == Decimal("1200")
            assert result.ytd_contributions_seed == Decimal("800")

    def test_inputs_average_and_timeline_agree(
        self, app, db, seed_user, seed_periods,
    ):
        """Both feeds read the SAME record, so they cannot price the row twice.

        The whole point of #11: the timeline applies a figure per period and the
        periodic-average / YTD-seed feed must apply the same one, or the
        limit/cap accounting reads a different number than the engine does.
        Since X-au-c2 both read one ``PricedContribution.amount``, so the
        agreement is structural -- this pins it end to end anyway, because the
        two feeds are what a future edit could re-split.
        """
        with app.app_context():
            account, _shadow = self._contribution_shadow(
                seed_user, db.session, seed_periods[0],
                estimated="500", actual="400", settled=True,
            )
            db.session.commit()
            period = seed_periods[0]

            records = load_shadow_income_contributions_for_account(
                seed_user["user"].id, seed_user["scenario"].id,
                account.id, [period.id],
            ).records
            inputs = calculate_investment_inputs(
                investment_params=self._params(), deductions=[],
                all_contributions=records, current_period=period,
            )
            timeline = build_contribution_timeline(
                deductions=[], contribution_transactions=records,
                periods=[period], as_of=period.start_date,
            )

            assert inputs.periodic_contribution == Decimal("400")
            assert len(timeline) == 1
            assert timeline[0].amount == Decimal("400")
            assert inputs.periodic_contribution == timeline[0].amount

    def test_excluded_status_rows_are_dropped_not_zeroed(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled shadow is ABSENT from the records, not present at $0.00.

        The ``status_contributes_to_balance`` screen the projection module used
        to apply four times now runs once, here.  Dropping and zeroing are NOT
        interchangeable and this is the case that proves it:
        ``_average_transfer_contribution`` divides by the number of DISTINCT
        pay periods it sees, so a Cancelled contribution carried through at
        ``$0.00`` in its own period would make the denominator 2 and halve the
        average -- $400 / 2 periods = $200 against the correct $400.
        """
        with app.app_context():
            account, _shadow = self._contribution_shadow(
                seed_user, db.session, seed_periods[0],
                estimated="400", settled=True,
            )
            # The SAME account, a later period: the Cancelled row has to be
            # inside the queried set for its absence to mean anything.
            self._contribution_shadow(
                seed_user, db.session, seed_periods[1],
                estimated="999", cancelled=True, account=account,
            )
            db.session.commit()

            records = load_shadow_income_contributions_for_account(
                seed_user["user"].id, seed_user["scenario"].id,
                account.id, [seed_periods[0].id, seed_periods[1].id],
            ).records

            # The Cancelled row is gone entirely -- not present as a zero.
            assert len(records) == 1
            assert records[0].amount == Decimal("400")
            assert records[0].payday == seed_periods[0].start_date

            result = calculate_investment_inputs(
                investment_params=self._params(), deductions=[],
                all_contributions=records, current_period=seed_periods[1],
            )
            # ONE period contributed, so the average is the full $400.  A
            # zero-carrying record would have made this $200.
            assert result.periodic_contribution == Decimal("400")

    def test_projected_shadow_is_not_confirmed(
        self, app, db, seed_user, seed_periods,
    ):
        """A still-Projected shadow is priced but NOT confirmed.

        The ``status.is_settled -> ContributionRecord.is_confirmed`` mapping
        moved to this boundary at plan step X-au-c2, and an adversarial review
        found the False half had lost its only grader: converting the old
        module-level test left it asserting a field the test itself supplied.
        A loader that wrote ``is_confirmed=True`` unconditionally would pass
        every other case here, and the growth engine would treat every future
        contribution as money already in the account.
        """
        with app.app_context():
            account, _shadow = self._contribution_shadow(
                seed_user, db.session, seed_periods[0], estimated="250",
            )
            db.session.commit()

            records = load_shadow_income_contributions_for_account(
                seed_user["user"].id, seed_user["scenario"].id,
                account.id, [seed_periods[0].id],
            ).records

            assert len(records) == 1
            assert records[0].amount == Decimal("250")
            assert records[0].is_confirmed is False

    def test_cancelled_contribution_still_counts_as_LINKED(
        self, app, db, seed_user, seed_periods,
    ):
        """A screened-out row still reports its account as linked.

        The companion to
        :meth:`test_excluded_status_rows_are_dropped_not_zeroed`, and the reason
        the loader returns two facts rather than one.  ``retirement_projection``
        asks whether ANYTHING funds an account to decide between rendering
        ``$0.00`` and a "link a contribution" call-to-action; a Cancelled
        contribution counts nothing but is still a link, so screening it out of
        the records must not take the account out of that set.  An adversarial
        review caught this flipping when the screen moved here.
        """
        with app.app_context():
            account, _shadow = self._contribution_shadow(
                seed_user, db.session, seed_periods[0],
                estimated="600", cancelled=True,
            )
            db.session.commit()

            loaded = load_shadow_income_contributions_for_account(
                seed_user["user"].id, seed_user["scenario"].id,
                account.id, [seed_periods[0].id],
            )

            # Nothing COUNTS ...
            assert loaded.records == []
            # ... but something is LINKED.
            assert account.id in loaded.linked_account_ids
