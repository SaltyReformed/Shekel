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

    def test_c31_jn01_jn02_chart_remaining_server_computed(
        self, app, db, seed_user, seed_periods,
    ):
        """C31 (JN-01/JN-02) -- chart_remaining is server-computed.

        The retirement-gap chart's "Gap" bar previously computed
        ``max(0, preRetirement - (pension + investment))`` in JS
        (``retirement_gap_chart.js``).  After Commit 31 the server
        ships ``chart_remaining`` as a string Decimal so the client
        only renders.  The other three legs (pension, investment,
        pre_retirement) are still emitted alongside it; the lock
        verifies all four are present and the relationship holds.
        """
        with app.app_context():
            result = retirement_dashboard_service.compute_gap_data(
                seed_user["user"].id
            )
            chart = result["chart_data"]
            for key in (
                "pension", "investment_income", "gap",
                "pre_retirement", "chart_remaining",
            ):
                assert key in chart
                # Each value is a string-encoded Decimal so the
                # template can drop it verbatim into a data-* attr.
                assert isinstance(chart[key], str)
            # Relationship: chart_remaining = max(0, pre_retirement -
            # (pension + investment_income)) reconstructed from the
            # other emitted values.  This guards the JN-02 audit note
            # that this is intentionally a different concept from
            # ``gap`` (post-pension, before investments).
            pre_retirement = Decimal(chart["pre_retirement"])
            pension = Decimal(chart["pension"])
            investment = Decimal(chart["investment_income"])
            expected_remaining = max(
                Decimal("0.00"), pre_retirement - pension - investment,
            )
            assert Decimal(chart["chart_remaining"]) == expected_remaining

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


# ── C20: retirement zero-is-a-value, not "missing" (CRIT-04) ─────────
#
# Pre-Commit-20 ``retirement_dashboard_service`` resolved the SWR
# with truthiness (``or "0.04"``) in ``compute_gap_data`` while
# ``compute_slider_defaults`` used ``is None``; an explicit
# ``safe_withdrawal_rate = Decimal("0.0000")`` displayed 0.00% on
# the slider but drove the projection at 4% -- phantom $4,000/mo of
# retirement income on a $1.2M balance the slider said was zero.
# Separately, ``if params and params.assumed_annual_return:`` truthiness
# dropped any zero-return account from the balance-weighted average
# (two $100k accounts at 0% and 7% reported 7.00% instead of the true
# blended 3.50%).  Commit 20 routes both sites through one
# ``_resolve_swr_fraction`` helper and replaces the weighted-return
# truthiness with explicit ``is not None`` so zero stays zero.
# See: CRIT-04, F-042, PA-04, PA-05; coding-standard E-12 ("0 vs None").


def _seed_active_salary_profile(db_session, user, scenario):
    """Attach an active salary profile so ``compute_gap_data`` reaches
    the ``_project_retirement_accounts`` path.

    The retirement dashboard's gap computation short-circuits without
    one (the net-biweekly path is gated on a salary profile being
    present), so the C20 fixtures must guarantee the projection code
    actually runs for the bug repro.
    """
    filing = db_session.query(FilingStatus).first()
    profile = SalaryProfile(
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing.id,
        name="C20 Day Job",
        annual_salary=Decimal("80000.00"),
        pay_periods_per_year=26,
        state_code="NC",
        is_active=True,
    )
    db_session.add(profile)
    db_session.flush()
    return profile


def _make_retirement_account(user, name, anchor_balance):
    """Create a 401(k) retirement account with a dated anchor.

    ``account_service.create_account`` writes the matching
    ``AccountAnchorHistory`` row so ``balance_resolver`` reads a
    consistent dated source of truth; the C20 tests then assert
    against the resolved balance, not the raw column.
    """
    inv_type = (
        db.session.query(AccountType)
        .filter_by(name="401(k)").one()
    )
    return account_service.create_account(
        user_id=user.id,
        account_type_id=inv_type.id,
        name=name,
        anchor_balance=anchor_balance,
    )


class TestSwrResolverConsistency:
    """CRIT-04 / F-042 / PA-04 / PA-05: SWR resolution is unified.

    Pre-fix, ``compute_gap_data`` and ``compute_slider_defaults``
    resolved the same ``UserSettings.safe_withdrawal_rate`` column
    under two different rules (truthiness ``or "0.04"`` vs.  ``is
    None``); an explicit ``Decimal("0.0000")`` stored SWR therefore
    displayed 0.00% on the slider but drove the projection at 4%.
    These tests pin the corrected behaviour: both surfaces read the
    SWR through the single ``_resolve_swr_fraction`` helper, and an
    explicit zero is a real zero on both surfaces.
    """

    def test_explicit_zero_swr_no_phantom_income(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C20-1: explicit zero SWR shows $0.00 income AND 0.00% slider.

        Reproduction of the CRIT-04 phantom-income failure mode:

          - ``safe_withdrawal_rate`` stored as ``Decimal("0")`` (the
            user explicitly entered 0%; the column's CHECK admits 0).
          - One retirement account with a $1,200,000 anchor balance,
            no ``InvestmentParams`` (so ``_project_retirement_accounts``
            skips the growth simulation and ``projected_balance`` ==
            ``current_balance`` == 1,200,000).
          - Active salary profile so the gap-projection path runs.

        Hand arithmetic (CRIT-04 / F-042 / PA-04):

          gap_result.projected_total_savings = 1,200,000.00
          swr (resolver) = Decimal("0") (was Decimal("0.04") pre-fix)
          chart investment_income
              = (1,200,000 * 0 / 12).quantize(0.01)
              = 0.00
          slider current_swr
              = (Decimal("0") * 100).quantize(0.01)
              = 0.00

        Pre-fix the slider rendered 0.00% but the chart rendered
        ``str((1,200,000 * 0.04 / 12).quantize(0.01)) = "4000.00"``
        -- the phantom $4,000/mo the audit cited.  All three numbers
        (resolver swr, chart income, slider %) MUST agree on zero.
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]

            settings = (
                db.session.query(UserSettings)
                .filter_by(user_id=user.id)
                .one()
            )
            settings.safe_withdrawal_rate = Decimal("0")

            _seed_active_salary_profile(db.session, user, scenario)
            _make_retirement_account(
                user, "C20-1 401k", Decimal("1200000.00"),
            )
            db.session.commit()

            data = retirement_dashboard_service.compute_gap_data(user.id)
            slider = retirement_dashboard_service.compute_slider_defaults(data)

            assert data["gap_analysis"].safe_withdrawal_rate == Decimal("0"), (
                "Resolver fed truthiness fallback into the gap "
                "calculator (CRIT-04)."
            )
            assert data["gap_analysis"].projected_total_savings == Decimal(
                "1200000.00"
            )
            # 1,200,000 * 0 / 12 = 0.00, not the pre-fix 4,000.00.
            assert data["chart_data"]["investment_income"] == "0.00", (
                "Phantom retirement income from truthiness fallback "
                "(CRIT-04 / F-042)."
            )
            assert slider["current_swr"] == Decimal("0.00")

    def test_none_swr_uses_default_on_both_surfaces(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C20-2: ``None`` SWR -> default applies to slider AND gap.

        Splices ``safe_withdrawal_rate = None`` (the column is
        nullable; NULL is the documented "use default" sentinel,
        distinct from an explicit stored zero).  Both surfaces must
        fall back to ``_DEFAULT_SWR_PCT`` (4% / 0.04 fractional)
        through the shared resolver.

        Hand arithmetic (CRIT-04 default-fallback):

          resolver = _DEFAULT_SWR_PCT / _PCT_SCALE
                   = Decimal("4.00") / Decimal("100")
                   = Decimal("0.04")
          slider   = (0.04 * 100).quantize(0.01) = 4.00
          gap_analysis.safe_withdrawal_rate = 0.04 (passed through)
        """
        with app.app_context():
            user = seed_user["user"]
            settings = (
                db.session.query(UserSettings)
                .filter_by(user_id=user.id)
                .one()
            )
            settings.safe_withdrawal_rate = None
            db.session.commit()

            data = retirement_dashboard_service.compute_gap_data(user.id)
            slider = retirement_dashboard_service.compute_slider_defaults(data)

            assert data["gap_analysis"].safe_withdrawal_rate == Decimal("0.04")
            assert slider["current_swr"] == Decimal("4.00")


class TestWeightedReturnZeroIsAValue:
    """CRIT-04 / F-042 / PA-04: zero return contributes, ``None`` skips.

    Pre-fix ``compute_slider_defaults`` used ``if params and
    params.assumed_annual_return:`` -- truthiness on a Decimal -- so
    a stable-value / cash sleeve at exactly 0.00% return was
    silently dropped from the weighted-average denominator.  Post-
    fix the gate is ``params is not None and
    params.assumed_annual_return is not None``: a zero rate is a real
    rate (counts), a missing ``InvestmentParams`` row is still
    "missing" (skipped).
    """

    def test_zero_return_account_in_weighted_avg(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C20-3: two $100k accounts at 0% and 7% blend to 3.50%.

        Hand arithmetic (CRIT-04 / F-042 / PA-04):

          weighted_return = 100,000 * 0.00000 + 100,000 * 0.07000
                          = 0 + 7,000
                          = 7,000
          total_balance   = 100,000 + 100,000 = 200,000
          current_return  = (7,000 / 200,000) * 100
                          = 0.035 * 100
                          = Decimal("3.50")

        Pre-fix the zero-return account was dropped from both numerator
        and denominator, yielding (7,000 / 100,000) * 100 = 7.00 -- the
        7.00% the audit cited for a portfolio whose true blended return
        is 3.50%.
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            _seed_active_salary_profile(db.session, user, scenario)

            acct_zero = _make_retirement_account(
                user, "C20-3 zero", Decimal("100000.00"),
            )
            acct_seven = _make_retirement_account(
                user, "C20-3 seven", Decimal("100000.00"),
            )
            db.session.add(InvestmentParams(
                account_id=acct_zero.id,
                assumed_annual_return=Decimal("0.00000"),
                employer_contribution_type="none",
            ))
            db.session.add(InvestmentParams(
                account_id=acct_seven.id,
                assumed_annual_return=Decimal("0.07000"),
                employer_contribution_type="none",
            ))
            db.session.commit()

            data = retirement_dashboard_service.compute_gap_data(user.id)
            slider = retirement_dashboard_service.compute_slider_defaults(data)

            # 100,000*0 + 100,000*0.07 = 7,000; 7,000 / 200,000 * 100 = 3.50.
            assert slider["current_return"] == Decimal("3.50"), (
                "Zero-return account dropped from weighted-return "
                "denominator (CRIT-04 / F-042 / PA-04)."
            )

    def test_c4_1_zero_balance_account_included_at_weight_zero(
        self, app, db, seed_user, seed_periods_today,
    ):
        """F-11: zero-balance account is included in the loop at weight 0.

        Pins the upstream ``proj.get("current_balance", ...)`` contract
        that Commit 4 / F-11 unlocked.  Pre-fix the trailing ``or
        Decimal("0")`` was truthiness on a Decimal, so a real zero
        balance was indistinguishable from a missing key.  The new
        explicit ``is None`` guard preserves a real zero (contributes
        weight 0 to the denominator) and only fires when the upstream
        contract drifts to return ``None``.

        Setup:

          - Account A: $0.00 anchor, ``InvestmentParams`` with
            ``assumed_annual_return = Decimal("0.07000")`` (zero
            balance, non-zero rate).
          - Account B: $100,000.00 anchor, ``InvestmentParams`` with
            ``assumed_annual_return = Decimal("0.05000")``.

        Hand arithmetic (F-11):

          weighted_return = 0 * 0.07000 + 100,000 * 0.05000
                          = 0 + 5,000
                          = 5,000
          total_balance   = 0 + 100,000 = 100,000
          current_return  = (5,000 / 100,000) * 100
                          = 0.05 * 100
                          = Decimal("5.00")

        If a future refactor causes ``proj.get("current_balance", ...)``
        to skip the zero-balance account, ``total_balance`` would
        collapse to ``$100,000`` with a numerator of ``$5,000`` -- still
        ``5.00`` accidentally.  The stronger lock is in
        ``test_c4_1_zero_balance_account_increments_total_balance``
        below, which asserts the zero-balance account contributes its
        ``$0.00`` weight to the loop (i.e. the loop iterated it).
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            _seed_active_salary_profile(db.session, user, scenario)

            acct_zero = _make_retirement_account(
                user, "F-11 zero-bal", Decimal("0.00"),
            )
            acct_funded = _make_retirement_account(
                user, "F-11 funded", Decimal("100000.00"),
            )
            db.session.add(InvestmentParams(
                account_id=acct_zero.id,
                assumed_annual_return=Decimal("0.07000"),
                employer_contribution_type="none",
            ))
            db.session.add(InvestmentParams(
                account_id=acct_funded.id,
                assumed_annual_return=Decimal("0.05000"),
                employer_contribution_type="none",
            ))
            db.session.commit()

            data = retirement_dashboard_service.compute_gap_data(user.id)
            slider = retirement_dashboard_service.compute_slider_defaults(data)

            # (0*0.07 + 100,000*0.05) / (0 + 100,000) * 100 = 5.00.
            assert slider["current_return"] == Decimal("5.00"), (
                "Zero-balance account was skipped by the truthiness "
                "guard the F-11 fix removed (or upstream proj.get "
                "contract drifted)."
            )

    def test_c4_1_zero_balance_account_increments_total_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """F-11: ``compute_gap_data`` exposes both projections so the
        upstream ``proj`` dict carries ``current_balance = Decimal("0")``
        for a real zero-balance account.

        This is the strict version of the F-11 contract: the loop in
        ``compute_slider_defaults`` consumes ``proj["current_balance"]``
        (via ``proj.get("current_balance", acct.current_anchor_balance)``)
        and the producer ``_project_retirement_accounts`` must therefore
        emit a Decimal-zero (not omit the account, not emit ``None``).
        If a future refactor drops the zero-balance account from the
        projections list, the truthiness regression returns to the same
        latent hazard the F-11 fix removed.
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            _seed_active_salary_profile(db.session, user, scenario)

            acct_zero = _make_retirement_account(
                user, "F-11 contract zero", Decimal("0.00"),
            )
            db.session.add(InvestmentParams(
                account_id=acct_zero.id,
                assumed_annual_return=Decimal("0.07000"),
                employer_contribution_type="none",
            ))
            db.session.commit()

            data = retirement_dashboard_service.compute_gap_data(user.id)
            projections = data["retirement_account_projections"]
            target = next(
                p for p in projections if p["account"].id == acct_zero.id
            )
            assert target["current_balance"] == Decimal("0.00"), (
                "Upstream proj.get contract drifted: a real zero-balance "
                "retirement account must emit Decimal('0.00'), not None "
                "or a missing key (F-11)."
            )
            assert isinstance(target["current_balance"], Decimal)

    def test_none_params_excluded_zero_return_included(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C20-4: an account with no ``InvestmentParams`` row is "missing"
        and skipped; an account with an explicit zero return contributes.

        Setup:

          - Account A: $50,000 anchor, NO ``InvestmentParams`` row
            (``params is None`` -- the genuine "missing" case).
          - Account B: $100,000 anchor, ``InvestmentParams`` with
            ``assumed_annual_return = Decimal("0.00000")`` (explicit
            zero, not "missing").

        Hand arithmetic (CRIT-04 / E-12):

          A is skipped (params is None).
          B contributes: weighted = 100,000 * 0 = 0;
                         denom    = 100,000.
          current_return = (0 / 100,000) * 100 = Decimal("0.00")

        Pre-fix B was ALSO skipped (truthiness on a zero Decimal), so
        ``total_balance`` was zero and the default 7.00% fallback ran
        -- the audit-cited misbehaviour.  Post-fix only A is missing.
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            _seed_active_salary_profile(db.session, user, scenario)

            _make_retirement_account(user, "C20-4 A", Decimal("50000.00"))
            acct_b = _make_retirement_account(
                user, "C20-4 B", Decimal("100000.00"),
            )
            db.session.add(InvestmentParams(
                account_id=acct_b.id,
                assumed_annual_return=Decimal("0.00000"),
                employer_contribution_type="none",
            ))
            db.session.commit()

            data = retirement_dashboard_service.compute_gap_data(user.id)
            slider = retirement_dashboard_service.compute_slider_defaults(data)

            # B's explicit zero contributes; A's missing params is
            # skipped.  Weighted = 0; denom = 100,000 -> 0.00%.
            assert slider["current_return"] == Decimal("0.00")


class TestSwrResolverSingleDefinition:
    """C20-5: source-text gate against re-introducing the bug.

    The defect was structural -- two truthiness expressions in the
    same module that disagreed with each other.  This test scans the
    source for the offending patterns so a future edit cannot
    silently regress to truthiness on a financial value.
    """

    def test_no_truthiness_on_financial_values(self):
        """No ``or "0.04"`` and no ``and X:`` truthiness on financial
        Decimal columns survives in executable code.

        Scans :mod:`app.services.retirement_dashboard_service` line by
        line; skips comments and docstring lines (their references
        documenting the historical pattern are intentional).  A failure
        names the surviving expression so the diagnostic is concrete.
        """
        import inspect  # pylint: disable=import-outside-toplevel
        source = inspect.getsource(retirement_dashboard_service)
        forbidden = (
            'or "0.04"',
            "or 0.04",
            "and params.assumed_annual_return:",
        )
        offending = []
        in_block_doc = False
        for lineno, raw in enumerate(source.splitlines(), start=1):
            stripped = raw.lstrip()
            # Strip block docstrings and comments so the gate only
            # inspects executable lines.  Counts triple-quote
            # openings/closings on each line to track state.
            triple = stripped.count('"""') + stripped.count("'''")
            line_was_in_doc = in_block_doc
            if triple % 2 == 1:
                in_block_doc = not in_block_doc
            if line_was_in_doc or in_block_doc:
                continue
            if stripped.startswith("#"):
                continue
            # Strip the inline comment suffix so a forbidden literal
            # appearing only in a trailing ``# ...`` does not trip.
            code = raw.split("#", 1)[0]
            # Strip string literals (a docstring that opens and closes
            # on the same line, or a normal string) so historical
            # references inside quotes are not flagged.
            code_no_strings = code
            for quote in ('"""', "'''", '"', "'"):
                while quote in code_no_strings:
                    start = code_no_strings.find(quote)
                    end = code_no_strings.find(quote, start + len(quote))
                    if end == -1:
                        break
                    code_no_strings = (
                        code_no_strings[:start]
                        + code_no_strings[end + len(quote):]
                    )
            for pattern in forbidden:
                if pattern in code_no_strings:
                    offending.append((lineno, pattern, raw.rstrip()))
        assert not offending, (
            "Truthiness on financial values re-introduced (CRIT-04 / "
            "E-12):\n"
            + "\n".join(f"  line {n}: {p!r} in {r}" for n, p, r in offending)
        )
