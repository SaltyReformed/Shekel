"""
Shekel Budget App -- Retirement Dashboard Service Tests

Unit tests for the retirement dashboard's LOADER and RESOLVERS, and for the
figures the retirement picture publishes from them -- verifying that the gap
analysis and projection logic produce correct financial computations
independently of the Flask route layer.

Every seeded case goes through :func:`_picture`, which runs the ROUTE's own
sequence: load the render's inputs once, then derive the picture at a plan
point (plan step C2-f2d-2, which made that the ONE producer).  The figures
these cases assert on did not move -- only the surface they are read from.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    EmployerContributionTypeEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.investment_params import InvestmentParams
from app.models.pension_profile import PensionProfile
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.user import UserSettings
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCadence, calendar_for
from app.services import (
    account_service,
    balance_at,
    cash_ledger,
    growth_engine,
    paycheck_calculator,
    retirement_dashboard_service,
    retirement_plan,
    retirement_projection,
)
from app.services.retirement_plan import load_retirement_inputs, picture_at
from tests._test_helpers import (
    all_periods,
    current_pay_period,
    derived_span,
    last_covered_day,
    make_investment_account,
    mark_purchase_settled,
    open_books_before_the_first_assertion,
)
from app.models.amount_ownership import AmountOwnership


def _picture(user_id, as_of=None):
    """The retirement picture a /retirement render derives at *point*.

    The route's own two steps, in one place: build the render's inputs from its
    read pass, derive the picture.  Spelling those out per case would let a
    test drift from what the route does, and two spellings of one sequence is
    the class of defect this arc removes.

    Args:
        user_id: The owner to render for.
        as_of: The read pass's pinned day, or ``None`` for the pass's own
            default. Supplied by the cases that grade what the RENDER threads
            into its producers, which is a different question from what those
            producers do with a day handed to them directly.

    Returns:
        The :class:`~app.services.retirement_plan.RetirementPicture`.
    """
    inputs = load_retirement_inputs(BalanceContext.build(user_id, as_of=as_of))
    return picture_at(inputs, inputs.stored_plan)


class TestThePicturesPublishedSurface:
    """What one retirement picture carries, and where each fact lives.

    It replaced a fourteen-key dict at plan step C2-f2d-2, and WHERE a fact
    sits is the assertion worth making: the point-independent ones (settings,
    pensions, salary profiles, the cadence, the tax rate) are reachable through
    ``inputs`` and are the SAME on every picture of one render, while the
    point-dependent ones (the axis, the projections, the pension summary, the
    net analysis) belong to the picture.  A fact filed on the wrong side is a
    fact that would be recomputed per point or shared across points wrongly.
    """

    def test_the_picture_carries_the_point_dependent_facts(
        self, app, db, seed_user, seed_periods,
    ):
        """Every figure a render publishes is reachable from one object."""
        with app.app_context():
            picture = _picture(seed_user["user"].id)
            # Point-dependent: derived per plan.
            assert picture.point == picture.inputs.stored_plan
            assert picture.net is not None
            assert picture.pension is not None
            assert picture.axis is not None
            assert picture.projections == []
            # Derived, never stored beside their own inputs: the rate the
            # picture reports IS the rate its own analysis was solved at, so
            # there is no second copy that could drift from it.
            assert picture.safe_withdrawal_rate is (
                picture.net.safe_withdrawal_rate
            )
            # NOT asserted against ``funded_ratio_state(picture.net)``: the
            # property IS that call, so the comparison would be a tautology.
            # Assert the RELATIONSHIP instead -- the ratio the picture reports
            # is its own projected over its own required.
            ratio, no_savings_needed = picture.funded_state
            if picture.net.required_retirement_savings == Decimal("0"):
                assert (ratio, no_savings_needed) == (None, True)
            else:
                assert no_savings_needed is False
                assert ratio == (
                    picture.net.after_tax_projected_savings
                    / picture.net.required_retirement_savings
                ).quantize(Decimal("0.0001"))
            # Point-INDEPENDENT: on the inputs, shared by every point.
            assert picture.as_of == picture.inputs.balance_ctx.as_of
            assert picture.pay_cadence is picture.inputs.gap.pay_cadence
            assert picture.inputs.tax_rate_missing is True

    def test_user_with_no_accounts_returns_safe_defaults(
        self, app, db, seed_user, seed_periods
    ):
        """User with no retirement accounts gets zero projections."""
        with app.app_context():
            picture = _picture(seed_user["user"].id)
            assert picture.projections == []
            # No qualifying pension -> no per-pension derivation entries.
            assert picture.pension.per_pension == []

    def test_user_with_no_salary_profile(self, app, db, seed_user, seed_periods):
        """User with no salary profile still returns a valid analysis."""
        with app.app_context():
            picture = _picture(seed_user["user"].id)
            assert picture.net is not None
            assert picture.inputs.gap.salary_profiles == []

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

            picture = _picture(seed_user["user"].id)
            assert len(picture.inputs.gap.pensions) == 1
            # One qualifying pension -> one per-pension derivation entry
            # carrying its computed benefit (D6 contract).
            assert len(picture.pension.per_pension) == 1
            assert picture.pension.per_pension[0]["benefit"] is not None


#: The read pass's day these pure-unit gap cases pin.  ``compute_gap_net_biweekly``
#: takes it since pay-calendar plan step C2-f2e; every case below supplies a
#: series explicitly, so it reaches no projection and only the SIGNATURE
#: depends on it -- which is why one literal serves all three.
_AS_OF = date(2026, 3, 20)


def _gap_inputs(profile, pay, cadence_days=14):
    """Wrap a profile and a pay snapshot in the bundle the producer takes.

    ``compute_gap_net_biweekly`` reads the owner's salary profiles and the
    current-pay snapshot off its
    :class:`~app.services.retirement_dashboard_service.GapInputs` since
    pay-calendar plan step C2-f2e, so the pure-unit cases build the bundle the
    one production caller builds rather than handing the two values in loose.
    Three fields are inert: the producer reads neither the settings, the
    pensions, nor the bundle's STORED merit horizon (the argument carries the
    plan point's).  **The pay cadence is NOT one of them since plan step
    R-F16**, which is what divides the projected final-year salary into a
    paycheck -- it read a ``pay_periods_per_year`` column off the profile
    until then, and this docstring said the field was inert.

    Args:
        profile: The owner's primary :class:`SalaryProfile`.
        pay: The ``_CurrentPay`` snapshot.
        cadence_days: Days between the owner's paydays.  Stated rather than
            fixed so a case can vary the one axis a 14-day fixture cannot
            see: at 26 the derived count equals the constant it replaced.

    Returns:
        The :class:`GapInputs` bundle.
    """
    return retirement_dashboard_service.GapInputs(
        settings=None,
        pensions=[],
        salary_profiles=[profile],
        pay=pay,
        merit_horizon_years=5,
        pay_cadence=PayCadence(cadence_days=cadence_days),
    )


class TestComputeGapNetBiweekly:
    """Pin the gap-comparison net-biweekly scaling (quality-pass B7).

    ``compute_gap_net_biweekly`` scales the projected final-year gross
    biweekly by the current effective take-home rate (net / gross) so the
    gap calculator compares retirement income against a raise-adjusted
    pre-retirement take-home figure rather than today's pay.  The cleanup
    (ce65229) reshaped the inputs into the ``_CurrentPay`` snapshot but
    left the scaling arithmetic itself unpinned; these tests assert the
    formula and its two early-return guards on hand-computed values,
    independent of the tax engine that produces the real net / gross.

    Supplying ``salary_by_year`` directly keeps the helper pure (no DB,
    no ref_cache, no paycheck engine) so the asserted numbers depend only
    on the scaling math under test.
    """


    def test_scales_final_gross_by_current_take_home_rate(self):
        """Final-year gross is scaled by today's net/gross take-home rate.

        Inputs chosen so every step is exact and hand-checkable:

          effective take-home rate = 2000.00 / 2500.00 = 0.80
          final-year gross biweekly = 131,000.00 / 26
                                    = 5038.4615...  -> 5038.46 (quantize .01)
          gap net biweekly = 5038.46 * 0.80
                           = 4030.768  -> 4030.77 (quantize .01)

        Exact equality catches a regression in either quantize step or in
        the rate denominator -- the engine ``gross_biweekly`` is reused so
        the rate stays raise-aware (the pre-Commit-17 ``annual / periods``
        recompute silently dropped any applicable raise).
        """
        profile = SalaryProfile()
        pay = retirement_dashboard_service._CurrentPay(
            net_biweekly=Decimal("2000.00"),
            current_breakdown=paycheck_calculator.PaycheckBreakdown(
                period=paycheck_calculator.PeriodInfo(period_id=1),
                earnings=paycheck_calculator.Earnings(
                    annual_salary=Decimal("65000.00"),
                    gross_biweekly=Decimal("2500.00"),
                ),
            ),
        )
        salary_by_year = [
            (2026, Decimal("120000.00")),
            (2055, Decimal("131000.00")),
        ]
        # merit_horizon_years is inert here: salary_by_year is supplied,
        # so the helper never recomputes it (the horizon only affects the
        # internal project_salaries_by_year call on the None branch).
        result = retirement_dashboard_service.compute_gap_net_biweekly(
            _gap_inputs(profile, pay), date(2055, 1, 1), salary_by_year, 5,
            _AS_OF,
        )
        assert result == Decimal("4030.77")

    def test_returns_current_net_when_no_retirement_horizon(self):
        """No planned retirement date -> current net biweekly, unscaled.

        The first guard returns ``pay.net_biweekly`` verbatim when any of
        salary profile / horizon / positive current pay is missing.  A
        ``None`` horizon must not scale (and must not raise), so the gap
        calculator falls back to comparing against today's take-home.
        """
        profile = SalaryProfile()
        pay = retirement_dashboard_service._CurrentPay(
            net_biweekly=Decimal("1800.00"),
            current_breakdown=None,
        )
        result = retirement_dashboard_service.compute_gap_net_biweekly(
            _gap_inputs(profile, pay), None,
            [(2026, Decimal("120000.00"))], 5, _AS_OF,
        )
        assert result == Decimal("1800.00")

    def test_returns_current_net_when_current_gross_is_zero(self):
        """No current breakdown -> gross 0.00 -> unscaled, no divide-by-zero.

        Past the first guard (profile + horizon + positive net all
        present) the rate denominator is ``current_breakdown.earnings.
        gross_biweekly``; a missing breakdown resolves it to 0.00.  The
        helper must return the current net biweekly rather than divide by
        zero, so a no-current-period user still gets a defined comparison.
        """
        profile = SalaryProfile()
        pay = retirement_dashboard_service._CurrentPay(
            net_biweekly=Decimal("1500.00"),
            current_breakdown=None,
        )
        result = retirement_dashboard_service.compute_gap_net_biweekly(
            _gap_inputs(profile, pay), date(2055, 1, 1),
            [(2055, Decimal("131000.00"))], 5, _AS_OF,
        )
        assert result == Decimal("1500.00")


class TestTheRenderDayOpensTheSalaryPath:
    """Every salary path on ``/retirement`` starts at the READ PASS's year.

    The three producers that open one -- :func:`compute_pension_summary`,
    :func:`compute_gap_net_biweekly` and
    :func:`~app.services.retirement_projection.build_employer_salary_basis` --
    each called ``date.today().year`` for themselves until pay-calendar plan
    step **C2-f2e**, which is ledger row **P55**.  They run once per PLAN POINT
    and the retire-later lever probes about ten, so one render read the clock
    about thirteen times; because the reads are ``.year`` they diverge only
    across a NEW YEAR, and then the verdict card projects its path from year N
    while the lever card beside it projects from N+1.

    Each case asserts the path MOVES with the supplied day rather than sitting
    where the frozen fixture clock is.  Asserting "it starts this year" would
    pass on both trees, every day but one.

    **Measured on the merge base ``5ab457b7``**: the same pension, projected
    through ``compute_pension_summary`` with the suite's own ``freeze_today``
    set to 2026-12-31 and then to 2027-01-01, gave
    ``[2026, 2027, 2028, 2029, 2030]`` and ``[2027, 2028, 2029, 2030]`` -- a
    salary path one year shorter for no reason but the moment the process ran.
    (The first attempt at that probe used ``time_machine.travel`` and PASSED:
    these modules bind ``date`` at import, which is what
    ``tests._test_helpers.freeze_today`` exists to reach and what a bare
    traveller does not.)
    """

    def _profile(self):
        """A raise-free profile, so the projected series is flat and exact."""
        return SalaryProfile(annual_salary=Decimal("100000.00"))

    def test_the_pension_path_opens_at_the_pass_year(self):
        """``compute_pension_summary`` projects from the pass's year.

        A pension retiring 2030-06-30, projected from a pass pinned to 2027
        and again from one pinned to 2028: the series opens on the pass's year
        both times and is one year shorter the second time.
        """
        pension = PensionProfile(
            planned_retirement_date=date(2030, 6, 30),
            salary_profile=self._profile(),
            benefit_multiplier=Decimal("0.02"),
            consecutive_high_years=3,
            hire_date=date(2010, 1, 1),
        )

        early = retirement_dashboard_service.compute_pension_summary(
            [pension], 5, date(2027, 3, 20),
        )
        late = retirement_dashboard_service.compute_pension_summary(
            [pension], 5, date(2028, 3, 20),
        )

        assert [year for year, _ in early.salary_by_year] == [
            2027, 2028, 2029, 2030,
        ]
        assert [year for year, _ in late.salary_by_year] == [
            2028, 2029, 2030,
        ]

    def test_the_gap_path_opens_at_the_pass_year(self):
        """``compute_gap_net_biweekly``'s recompute branch uses the pass's year.

        The branch runs when no pension supplied a series -- an owner with a
        retirement date in SETTINGS and no pension profile, which is reachable
        and is why the recompute exists.  A raise-free $100,000.00 profile
        projects flat, so the FIGURE is the same either way; what has to differ
        is the series the projection walked, and the observable difference is
        the horizon guard: a pass pinned PAST the retirement date projects an
        empty series and the producer returns the current net unchanged.
        """
        pay = retirement_dashboard_service._CurrentPay(
            net_biweekly=Decimal("2000.00"),
            current_breakdown=paycheck_calculator.PaycheckBreakdown(
                period=paycheck_calculator.PeriodInfo(period_id=1),
                earnings=paycheck_calculator.Earnings(
                    annual_salary=Decimal("100000.00"),
                    gross_biweekly=Decimal("2500.00"),
                ),
            ),
        )
        gap = _gap_inputs(self._profile(), pay)

        # Pass pinned BEFORE the horizon: the path is walked and the final-year
        # gross ($100,000.00 / 26 = $3,846.15) is scaled by the take-home rate
        # (2000 / 2500 = 0.80) -> $3,076.92.
        before = retirement_dashboard_service.compute_gap_net_biweekly(
            gap, date(2030, 6, 30), None, 5, date(2027, 3, 20),
        )
        assert before == Decimal("3076.92")

        # Pass pinned AFTER it: no year to project, so the producer falls back
        # to the current net.  A producer reading its own clock would answer
        # the line above for both.
        after = retirement_dashboard_service.compute_gap_net_biweekly(
            gap, date(2030, 6, 30), None, 5, date(2032, 3, 20),
        )
        assert after == Decimal("2000.00")

    def test_a_weekly_owners_gap_divides_by_52(self):
        """THE CADENCE AXIS: the projected paycheck follows the owner's rhythm.

        Input: the same $100,000 profile and 0.80 take-home rate, on a 7-day
        cadence.
        Expected: ``($100,000 / 52) x 0.80 = $1,923.08 x 0.80 = $1,538.46``,
        half the biweekly answer above.
        Why: every other case here is biweekly, where the derived count and
        the ``pay_periods_per_year`` column plan step R-F16 deleted both read
        26 -- so none of them can tell the two apart. This is the case that
        fails if the divisor stops being the owner's cadence.
        """
        pay = retirement_dashboard_service._CurrentPay(
            net_biweekly=Decimal("2000.00"),
            current_breakdown=paycheck_calculator.PaycheckBreakdown(
                period=paycheck_calculator.PeriodInfo(period_id=1),
                earnings=paycheck_calculator.Earnings(
                    annual_salary=Decimal("100000.00"),
                    gross_biweekly=Decimal("2500.00"),
                ),
            ),
        )
        gap = _gap_inputs(self._profile(), pay, cadence_days=7)

        # $100,000 / 52 = $1,923.0769 -> $1,923.08; x 0.80 -> $1,538.464 ->
        # $1,538.46.
        assert retirement_dashboard_service.compute_gap_net_biweekly(
            gap, date(2030, 6, 30), None, 5, date(2027, 3, 20),
        ) == Decimal("1538.46")

    def test_the_employer_basis_opens_at_the_pass_year(self):
        """``build_employer_salary_basis`` projects from the pass's year.

        A pass pinned past the horizon leaves no year to project, so the
        resolver is ``None`` and ``growth_engine`` falls back to the constant
        employer gross -- the documented no-horizon behavior.  Pinned before
        it, the resolver exists.
        """
        profile = self._profile()

        cadence = PayCadence(cadence_days=14)

        assert retirement_projection.build_employer_salary_basis(
            [profile], date(2030, 6, 30), 5, date(2027, 3, 20), cadence,
        ) is not None
        assert retirement_projection.build_employer_salary_basis(
            [profile], date(2030, 6, 30), 5, date(2032, 3, 20), cadence,
        ) is None

    def test_the_employer_basis_divides_by_the_OWNERS_paychecks(self):
        """THE CADENCE AXIS for the employer-contribution base.

        Input: the same raise-free $100,000 profile, resolved at 14 days and
        again at 7.
        Expected: $3,846.15 and $1,923.08 -- the same salary over 26 and over
        52 paychecks.
        Why: the resolver feeds ``growth_engine``'s percentage-of-gross
        employer match for the WHOLE projection horizon, so a count that is
        not the owner's compounds. It read a ``pay_periods_per_year`` column
        until plan step R-F16 and its only test was biweekly, where that
        column and the derived count agree.
        """
        profile = self._profile()

        class _Period:  # the one attribute the resolver reads
            start_date = date(2027, 3, 20)

        period = _Period()

        biweekly = retirement_projection.build_employer_salary_basis(
            [profile], date(2030, 6, 30), 5, date(2027, 3, 20),
            PayCadence(cadence_days=14),
        )
        weekly = retirement_projection.build_employer_salary_basis(
            [profile], date(2030, 6, 30), 5, date(2027, 3, 20),
            PayCadence(cadence_days=7),
        )

        assert biweekly(period) == Decimal("3846.15")
        assert weekly(period) == Decimal("1923.08")


    def test_the_RENDER_threads_its_own_day_into_the_salary_path(
        self, app, db, seed_user, seed_periods,
    ):
        """A /retirement RENDER opens its salary path at the pass's year.

        **The three cases above cannot see the line this step actually
        changed** (found by C2-f2e's adversarial design review, 2026-08-18).
        They call each producer directly with a literal day, so they grade what
        a producer DOES with a day it is handed -- while the fix is one
        assignment, ``as_of = inputs.balance_ctx.as_of`` in
        ``retirement_plan._derive_picture``, which is the only place any of the
        three is called from in ``app/``. A regression writing ``date.today()``
        there would leave all three green, and
        ``TestOneReadPassPerRender`` counts passes rather than clocks.

        This case goes through the route's own two steps
        (:func:`_picture`) at two pinned days in DIFFERENT years and requires
        the pension's projected salary path to open on each. It is what makes
        the "this render is single-clock" claim in ``app/routes/retirement.py``
        and ``retirement_readiness`` a graded one.
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Main",
                annual_salary=Decimal("80000"),
                state_code="NC",
                is_active=True,
            )
            db.session.add(profile)
            db.session.flush()
            db.session.add(PensionProfile(
                user_id=seed_user["user"].id,
                salary_profile_id=profile.id,
                name="State Pension",
                benefit_multiplier=Decimal("0.01750"),
                consecutive_high_years=4,
                hire_date=date(2010, 1, 1),
                planned_retirement_date=date(2030, 6, 30),
                is_active=True,
            ))
            db.session.commit()

            early = _picture(seed_user["user"].id, as_of=date(2027, 3, 20))
            late = _picture(seed_user["user"].id, as_of=date(2028, 3, 20))

            # The premise, asserted rather than assumed: a path was projected
            # at all, so the years below are the projection's and not an empty
            # list's.
            assert early.pension.salary_by_year
            assert [year for year, _ in early.pension.salary_by_year] == [
                2027, 2028, 2029, 2030,
            ]
            assert [year for year, _ in late.pension.salary_by_year] == [
                2028, 2029, 2030,
            ]


class TestTheDisplayedRates:
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
        """The active SWR is the user's stored rate, as a fraction.

        ``seed_user`` constructs ``UserSettings`` with the model-level default
        ``safe_withdrawal_rate = Decimal("0.0400")``, and the picture reports
        the rate its own analysis was solved at: ``Decimal("0.04")``.

        **The percent-scaled ``current_swr`` this asserted on is GONE** (plan
        step C2-f2d-2): ``compute_slider_defaults`` published it and no
        template, route or producer ever read it, so it was a published figure
        with no consumer.  The rule it protected is unchanged and is asserted
        here at the surface that survives -- exact equality, which is what
        catches a float cast re-appearing (3.9999... or 4.000000000001).
        """
        with app.app_context():
            picture = _picture(seed_user["user"].id)
            assert isinstance(picture.safe_withdrawal_rate, Decimal)
            assert picture.safe_withdrawal_rate == Decimal("0.04")

    def test_default_return_when_no_accounts(self, app, db, seed_user, seed_periods):
        """The blended return falls back to 7% with nothing to weight.

        ``seed_user`` seeds no retirement or investment accounts, so the
        balance-weighted average has no inputs; the picture must report the
        documented ``_DEFAULT_RETURN_PCT`` baseline (the S&P 500 long-run real
        return), as the fraction the growth math takes: ``Decimal("0.07")``.
        Type is asserted as well as value to keep the Decimal contract pinned
        (F-100 fix).
        """
        with app.app_context():
            picture = _picture(seed_user["user"].id)
            assert isinstance(picture.blended_return, Decimal)
            assert picture.blended_return == Decimal("0.07")

    def test_default_swr_when_settings_none(self, app, db, seed_user, seed_periods):
        """The SWR falls back to 4% when the user has no settings row.

        Asserted against the resolver directly, because ``settings is None``
        is the only way to reach this arm and a seeded user always HAS a
        settings row: the pre-C2-f2d-2 test spliced ``settings = None`` into a
        producer's returned dict, which the typed record no longer permits and
        which was never a state the producer could actually be in.  Testing the
        resolver is testing the branch rather than a dict edit.
        """
        with app.app_context():
            assert retirement_dashboard_service.resolve_swr_fraction(
                None,
            ) == Decimal("0.04")

    def test_zero_swr_round_trips_as_decimal_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """An explicit Decimal('0') SWR survives as a real zero.

        Storing ``safe_withdrawal_rate = Decimal("0")`` is semantically
        distinct from ``None`` (the F-077 / C-24 CHECK constraint permits both
        NULL and zero; zero means "explicit zero rate," NULL means "use the
        default").  This pins the boundary: the rate must NOT collapse to the
        4% default.
        """
        with app.app_context():
            settings = (
                db.session.query(UserSettings)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            settings.safe_withdrawal_rate = Decimal("0")
            db.session.commit()

            picture = _picture(seed_user["user"].id)
            assert isinstance(picture.safe_withdrawal_rate, Decimal)
            assert picture.safe_withdrawal_rate == Decimal("0")


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


def _add_envelope_expense_with_settled_entries_ret(
    db_session, *, user_id, account, scenario_id, period, category_id,
    estimated, settled_amounts,
):
    """Create a Projected envelope expense with already-posted debit entries.

    Same shape as the helper used in the C8 year-end / investment
    tests; copied here so this file stays standalone.

    **Each purchase is dated on the account's own latest asserted day and
    routed through ``mark_purchase_settled``** (plan step S1-c, ruling
    R-DH (d), finding N-132 / R8).  It carried a hardcoded ``2026-05-15`` and a
    stored ``is_cleared`` flag, which claimed the purchases were inside an
    anchor the account asserts for the frozen today (2026-03-20) -- two months
    EARLIER, a state production cannot reach.  Deriving the day from the
    account's assertion also removes the fixture's calendar dependence: it
    holds whatever day the suite runs on, which is the ``.claude/rules/testing``
    property N-131 and N-132 are both about.
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
        amount_ownership=AmountOwnership.own(estimated),
    )
    db_session.add(txn)
    db_session.flush()

    observed_on = cash_ledger.reconciled_through(account.id).observed_day
    for amt in settled_amounts:
        entry = TransactionEntry(
            transaction_id=txn.id, account_id=txn.account_id,
            user_id=user_id,
            amount=amt,
            description="Confirmed purchase",
            purchased_on=observed_on,
            is_credit=False,
        )
        db_session.add(entry)
        db_session.flush()
        mark_purchase_settled(db_session, account, entry)
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

        Hand arithmetic (CRIT-01 / F-009 / R-1) for the CASH BASIS:

          cleared_debit   = 45.71
          uncleared_debit = 0
          sum_credit      = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          cash basis      = 50,000.00 + 0 - 454.29 = 49,545.71

        Pre-Commit-8 the projection's ``current_balance`` was
        50,000 - 500 = 49,500.00 via the silent-degrade seam.

        **The projection is no longer EQUAL to that basis, and this test used
        to assert it was.**  Ruling R-Y (plan step X-g2b) gives the anchor
        period its own accrual, so an INVESTMENT account is worth more than the
        number last typed into it from the day after it was typed -- the same
        divergence ``TestInvestmentCrossPageEquality`` and
        ``TestPropertyCrossPageEquality`` already state for their kinds.  On a
        clean $50,000 anchor at 7%/yr, measured: ``$0.00`` the day before the
        assertion, ``+$9.27`` the day of it, and ``+$92.77`` at the period end
        the per-period map answers at.

        It passed before only because the anchor row's ``created_at`` came from
        the DATABASE clock, which ``freeze_today`` did not reach -- so the
        assertion was stamped four months PAST the end of its own seeded window
        and the replay had nothing inside the window to accrue from (finding
        N-65).  With the database clock frozen too, the anchor is dated inside
        its own period and the tile carries the accrual it should.

        So the basis is asserted as the hand-computed figure it has always
        been, and the projection is asserted against the SEAM's own modelled
        map -- strictly above the basis, which is what would fail if a surface
        fell back to the cash producer.
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            current_period = current_pay_period(user.id)
            assert current_period is not None

            # Active salary profile so the gap path is reachable.
            filing = db.session.query(FilingStatus).first()
            db.session.add(SalaryProfile(
                user_id=user.id,
                scenario_id=scenario.id,
                filing_status_id=filing.id,
                name="Day Job",
                annual_salary=Decimal("80000.00"),
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
                account_service.AccountSpec(
                    user_id=user.id,
                    account_type_id=inv_type.id,
                    name="C8 401k",
                    anchor_balance=Decimal("50000.00"),
                ),
            )
            db.session.flush()
            # **Its books open before the entries below** (plan step X-f3c-2b,
            # ruling **R-HG**).  ``create_account`` opens them on the
            # assertion's own day and those entries settle on that same day, so
            # they are inside the $50,000.00 that was declared.  Moves no
            # figure here -- the assertion, which is what every number in the
            # docstring is computed from, does not move.
            open_books_before_the_first_assertion(db.session, acct)

            db.session.add(InvestmentParams(
                account_id=acct.id,
                assumed_annual_return=Decimal("0.07000"),
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            ))

            _add_envelope_expense_with_settled_entries_ret(
                db.session,
                user_id=user.id,
                account=acct,
                scenario_id=scenario.id,
                period=current_period,
                category_id=seed_user["categories"]["Groceries"].id,
                estimated=Decimal("500.00"),
                settled_amounts=(
                    Decimal("20.00"), Decimal("15.71"), Decimal("10.00"),
                ),
            )
            db.session.commit()

            # The entries-aware CASH BASIS the modelled balance is computed
            # ON: 50,000 - max(500 - 45.71 - 0, 0) = 49,545.71.  Read through
            # the seam's cash-flow view at plan step X-g4b, which deleted the
            # anchor-forward producer this asserted against; that view is the
            # same fold the modelled replay folds beneath its accrual, so the
            # basis under test is genuinely the one the tile is built on.
            ctx = BalanceContext.build(user.id)
            basis = balance_at.cash_balance_map(
                acct, ctx,
            )
            # CRIT-01 / F-009 / R-1: 50000 - max(500 - 45.71, 0)
            #                      = 50000 - 454.29 = 49,545.71.
            # Pre-Commit-8 this was 49,500.00.
            assert basis[current_period.id] == Decimal("49545.71")

            target = next(
                p for p in _picture(user.id).projections
                if p["account"].id == acct.id
            )
            # The projection reads the MODELLED map, which folds ruling R-Y's
            # anchor-period accrual over the cash basis above.  Read once from
            # the seam so this cannot drift from the producer it locks, exactly
            # as the per-kind cross-page classes do.
            modelled = balance_at.balance_map(acct, ctx)
            assert target["current_balance"] == modelled[current_period.id]
            # And the accrual is really there: a surface that fell back to the
            # cash producer would land ON the basis, not above it.
            assert target["current_balance"] > basis[current_period.id]
            # PINNED to the figure, not only to the producer.  The two
            # assertions above are a WIRING identity and an inequality: both
            # survive a wrong RATE, which plan step X-h's adversarial review
            # demonstrated by injecting a 10x return and watching this test
            # stay green.  49,545.71 accruing 7%/yr from the 2026-03-20
            # assertion to the current period's end (2026-03-29, 10 days --
            # $9.27/day at this balance) is 49,637.72.
            assert target["current_balance"] == Decimal("49637.72")


class TestRetirementAnchorInPastModeledHeadlineDatedSeed:
    """Anchor-in-past: modelled headline, and a seed read the day before.

    The displayed per-account ``current_balance`` is the model-from-anchor
    value (so it agrees with the /savings net-worth tile and the /investment
    dashboard).  The forward growth projection seeds from the MODELLED balance
    on the day BEFORE its window opens (plan step X-g2b, rulings R-AB / R-AE).

    **The seed's basis changed and the reason it changed is the point.**  It
    used to be the flat CASH carry, with the current period's own contribution
    subtracted back out, because the window opened INSIDE the period the seed
    was read at the end of -- so something had to be removed or the engine
    re-applied it.  Read the seed a day before the window and the two are
    disjoint: nothing it holds can be re-applied, and nothing it has earned is
    thrown away.  Seeding from the flat cash basis instead now DROPS every cent
    the account earned between its anchor and today, which is the error this
    class newly locks in the other direction.

    Every other retirement test anchors at the CURRENT period, where the three
    candidate seeds nearly coincide, so the divergence is invisible to them.
    """

    def test_displayed_modeled_projection_seeds_cash(
        self, app, db, seed_user, seed_periods_today,
    ):
        """current_balance == modelled; the projection seeds the day before.

        A 401(k) opening at V0 = $100,000, 7% return, no contributions,
        anchored at the FIRST seeded period while today falls in period 4 --
        so the anchor is in the past and the model-from-anchor map compounds
        V0 forward to today, STRICTLY ABOVE the flat $100,000 cash carry.

        Two locks, both falsifiable because the modeled value diverges from
        V0:

          * ``current_balance`` equals the seam's model-from-anchor value at
            today (``balance_at.balance_map[current_period]``) -- the
            DISPLAYED headline is modeled, not the flat cash carry.
          * ``projected_balance`` equals the growth engine re-run from the
            MODELLED balance on the day before the window opens, and equals
            NEITHER the run seeded from the flat cash V0 (which would discard
            the anchor-to-today growth) NOR the run seeded from the modelled
            value at the current period's END (which would compound that
            period twice).  Three seeds, three different answers, so the
            equality is falsifiable in both directions.

        ``seed_user`` sets no retirement horizon (``planned_retirement_date``
        is None, no pensions), so the projection runs over the real
        current-period-forward run; with no deductions / employer match the
        engine inputs are its defaults, so the reconstruction is exact.
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            v0 = Decimal("100000.00")
            past_anchor = seed_periods_today[0]
            acct = make_investment_account(
                seed_user, db.session, past_anchor, v0, name="Past 401k",
            )

            owner_periods = all_periods(user.id)
            current_period = current_pay_period(user.id)
            assert current_period is not None
            assert derived_span(past_anchor).period_index < derived_span(current_period).period_index, (
                "fixture regressed: the anchor must be strictly before today"
            )

            # The seam's model-from-anchor value at today (the figure the
            # rerouted headline now reads), strictly above the flat carry.
            bctx = BalanceContext.build(seed_user["user"].id)
            modeled = balance_at.balance_map(
                acct, bctx,
            )[current_period.id]
            assert modeled > v0, (
                f"modeled {modeled!r} did not compound above the flat anchor "
                f"{v0!r}; the divergence this lock needs is absent"
            )

            # _project_one_account runs project_balance over the real
            # current-period-forward run (no retirement horizon -> no
            # synthetic periods); with no contributions / employer match the
            # other engine kwargs are their defaults.  Seeding from V0 (cash)
            # and from the modeled value give DIFFERENT end balances, so the
            # equality below is non-tautological.
            # A ``PeriodWindow`` off the owner's own calendar, which is what
            # ``growth_engine.project_balance`` is typed for and what
            # ``_project_one_account`` hands it.  It was a list of ORM rows
            # filtered by ordinal, and that only ran while the row still
            # carried an ``end_date`` for the engine to read -- the duck type
            # plan step ``pay_calendar:C4-c`` removed the second inhabitant of.
            forward_periods = calendar_for(user.id).window(
                derived_span(current_period).period_index, len(owner_periods),
            )
            def _run(seed):
                return growth_engine.project_balance(
                    current_balance=seed,
                    assumed_annual_return=Decimal("0.07000"),
                    periods=forward_periods,
                )[-1].end_balance

            # The ruled seed: the modelled balance the day BEFORE the window.
            dated_seed = balance_at.balance_at(
                acct, bctx,
                forward_periods[0].start_date - timedelta(days=1),
            )
            expected_dated = _run(dated_seed)
            # The two errors it sits between: the retired flat cash carry
            # (drops the anchor-to-today growth) and the current period's END
            # (compounds that period twice).
            expected_cash = _run(v0)
            expected_end = _run(modeled)
            assert len({expected_dated, expected_cash, expected_end}) == 3, (
                "the three candidate seeds must diverge for a "
                "non-tautological lock"
            )
            assert expected_cash < expected_dated < expected_end

            target = next(
                p for p in _picture(user.id).projections
                if p["account"].id == acct.id
            )

            # Displayed headline is the modelled value.
            assert target["current_balance"] == modeled
            # The forward projection continues that curve from the day before
            # its own window -- neither restarting from the flat basis nor
            # re-growing the period the window opens in.
            assert target["projected_balance"] == expected_dated
            assert target["projected_balance"] != expected_cash
            assert target["projected_balance"] != expected_end


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
# ``resolve_swr_fraction`` helper and replaces the weighted-return
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
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=inv_type.id,
            name=name,
            anchor_balance=anchor_balance,
        ),
    )


class TestSwrResolverConsistency:
    """CRIT-04 / F-042 / PA-04 / PA-05: SWR resolution is unified.

    Pre-fix, ``compute_gap_data`` and ``compute_slider_defaults``
    resolved the same ``UserSettings.safe_withdrawal_rate`` column
    under two different rules (truthiness ``or "0.04"`` vs.  ``is
    None``); an explicit ``Decimal("0.0000")`` stored SWR therefore
    displayed 0.00% on the slider but drove the projection at 4%.
    These tests pin the corrected behaviour: both surfaces read the
    SWR through the single ``resolve_swr_fraction`` helper, and an
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
          meter withdrawals (P3c: the income meter superseded the
          retired gap chart as the SWR-income surface)
              = round(1,200,000 * 0 / 12)
              = 0.00
          slider current_swr
              = (Decimal("0") * 100).quantize(0.01)
              = 0.00

        Pre-fix the slider rendered 0.00% but the income surface rendered
        (1,200,000 * 0.04 / 12).quantize(0.01) = 4,000.00 -- the phantom
        $4,000/mo the audit cited.  All three numbers (resolver swr,
        withdrawal income, slider %) MUST agree on zero.
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

            picture = _picture(user.id)

            assert picture.safe_withdrawal_rate == Decimal("0"), (
                "Resolver fed truthiness fallback into the gap "
                "calculator (CRIT-04)."
            )
            assert picture.net.projected_total_savings == Decimal(
                "1200000.00"
            )
            # 1,200,000 * 0 / 12 = 0.00, not the pre-fix 4,000.00.
            from app.services import retirement_readiness

            meter = retirement_readiness.readiness_from_picture(
                picture,
            )["income_meter"]
            assert meter["withdrawals_net_monthly"] == Decimal("0.00"), (
                "Phantom retirement income from truthiness fallback "
                "(CRIT-04 / F-042)."
            )

    def test_none_swr_uses_the_documented_default(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C20-2: a ``None`` SWR falls back to the documented default.

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

            assert _picture(user.id).safe_withdrawal_rate == Decimal("0.04")


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
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            ))
            db.session.add(InvestmentParams(
                account_id=acct_seven.id,
                assumed_annual_return=Decimal("0.07000"),
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            ))
            db.session.commit()

            # 100,000*0 + 100,000*0.07 = 7,000; 7,000 / 200,000 = 0.035.
            assert _picture(user.id).blended_return == Decimal("0.035"), (
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
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            ))
            db.session.add(InvestmentParams(
                account_id=acct_funded.id,
                assumed_annual_return=Decimal("0.05000"),
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            ))
            db.session.commit()

            # (0*0.07 + 100,000*0.05) / (0 + 100,000) = 0.05.
            assert _picture(user.id).blended_return == Decimal("0.05"), (
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
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            ))
            db.session.commit()

            target = next(
                p for p in _picture(user.id).projections
                if p["account"].id == acct_zero.id
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
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            ))
            db.session.commit()

            # B's explicit zero contributes; A's missing params is
            # skipped.  Weighted = 0; denom = 100,000 -> 0.
            assert _picture(user.id).blended_return == Decimal("0")


class TestSwrResolverSingleDefinition:
    """C20-5: source-text gate against re-introducing the bug.

    The defect was structural -- two truthiness expressions that disagreed
    with each other.  This test scans the source for the offending patterns so
    a future edit cannot silently regress to truthiness on a financial value.

    **It scans BOTH modules that now hold those expressions**, and that is the
    point of this note.  When plan step C2-f2d-2 moved the blended-return fold
    out of ``retirement_dashboard_service`` into
    :mod:`app.services.retirement_plan`, a scan of the first module alone would
    have kept passing while grading a file the pattern had left -- a firing
    control turned into a tautology by a module split, with nothing red.  A
    gate that names its subjects has to be re-pointed when a subject moves.
    """

    def test_no_truthiness_on_financial_values(self):
        """No ``or "0.04"`` and no ``and X:`` truthiness on financial
        Decimal columns survives in executable code.

        Scans the retirement loader / resolver module AND the picture producer
        line by line; skips comments and docstring lines (their references
        documenting the historical pattern are intentional).  A failure names
        the surviving expression so the diagnostic is concrete.
        """
        import inspect  # pylint: disable=import-outside-toplevel
        source = "\n".join(
            inspect.getsource(module)
            for module in (retirement_dashboard_service, retirement_plan)
        )
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


class TestTheProjectionAxisIsTheOwnersOwnCalendar:
    """Plan step **C2-e**, ledger rows **P7**, **P17**, **P20**, **P22**.

    ``retirement_projection`` built its axis with
    ``growth_engine.generate_projection_periods`` -- a fabricated period list
    with ids numbered from 1 in the same integer namespace as real
    ``budget.pay_periods.id`` (P17) and a ``cadence_days=14`` default that not
    one of its six call sites overrode (P20).  The growth engine applies
    ``periodic_contribution`` -- a per-PAYCHECK figure -- once per axis period,
    so a monthly-paid owner was credited ``365/14`` contributions a year.

    Measured through the real engine, that is **+83%** over twenty years:
    ``$1,300,344.92`` shown against a true ``$711,385.70`` on a $50,000 seed at
    7% with $1,000 a paycheck.  It cost ``$0.00`` on production, whose cadence
    is 14 -- and ``ck_pay_schedule_cadence_range`` permits 1..365, so a
    monthly-paid owner is an ordinary user rather than a contrived one.
    """

    @staticmethod
    def _axis_for(user_id, horizon):
        """Return the axis the retirement projection would run over."""
        # pylint: disable=import-outside-toplevel
        from app.services import retirement_projection
        # The pass carries the owner's calendar, so the two period arguments
        # this took went with pay-calendar plan step C2-f2d-3.
        ctx = retirement_projection.build_projection_context(
            BalanceContext.build(user_id), horizon, None, None,
        )
        return retirement_projection.resolve_projection_axis(ctx)

    def test_a_monthly_owner_gets_monthly_paychecks_not_biweekly_ones(
        self, app, db, seed_user,
    ):  # pylint: disable=unused-argument
        """The P20 count, at the axis rather than in dollars.

        A monthly schedule bracketing today, and a horizon a year out.  Every
        axis period spans exactly 30 days -- the owner's own cadence, saved and
        projected alike -- so a year holds 12 paychecks and the engine applies
        12 contributions.  The deleted producer put 26 periods in the same span
        whatever the schedule said.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from app.services import pay_period_write
            user_id = seed_user["user"].id
            as_of = BalanceContext.build(user_id).as_of
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=as_of - timedelta(days=150),
                num_periods=10,
                cadence_days=30,
            )
            db.session.commit()

            axis = self._axis_for(user_id, as_of + timedelta(days=360))
            assert axis[0].start_date <= as_of <= axis[0].end_date
            for period in axis:
                assert (period.end_date - period.start_date).days + 1 == 30, (
                    "an axis period is not one of this owner's paychecks"
                )
            starts = [period.start_date for period in axis]
            assert all(
                (later - earlier).days == 30
                for earlier, later in zip(starts, starts[1:])
            )
            # A 360-day horizon at a 30-day cadence: 12 whole paychecks plus
            # the part-elapsed one the axis opens on.  The firing control is
            # the count a hardcoded biweekly rhythm would have produced.
            assert len(axis) == 13
            assert len(axis) < 26

    def test_the_axis_projects_past_the_saved_schedule_at_that_cadence(
        self, app, db, seed_user,
    ):  # pylint: disable=unused-argument
        """Ledger row **P7**: the calendar ANSWERS past the last payday.

        Past the schedule the periods are projections -- no ``period_id``, so
        nothing can mistake one for a row a foreign key points at -- and they
        continue the saved ordinal sequence, which is the key the projection's
        consumers index on (row **P21**).
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from app.services import pay_period_write
            user_id = seed_user["user"].id
            as_of = BalanceContext.build(user_id).as_of
            saved = pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=as_of - timedelta(days=60),
                num_periods=4,
                cadence_days=30,
            )
            db.session.commit()
            last_saved_end = saved[-1].start_date + timedelta(days=29)

            axis = self._axis_for(user_id, last_saved_end + timedelta(days=200))
            head = [p for p in axis if p.start_date <= last_saved_end]
            tail = [p for p in axis if p.start_date > last_saved_end]

            assert head and tail
            assert all(period.period_id is not None for period in head)
            assert all(period.period_id is None for period in tail)
            # The ordinals run unbroken ACROSS the boundary, which is what
            # makes them a usable map key where the id is not.
            ordinals = [period.period_index for period in axis]
            assert ordinals == list(range(ordinals[0], ordinals[0] + len(axis)))
            assert len(set(ordinals)) == len(axis)

    def test_with_no_retirement_date_the_axis_runs_to_the_saved_horizon(
        self, app, db, seed_user, seed_periods,
    ):
        """The two arms collapsed into one expression at plan step C2-e.

        The no-horizon arm used to walk the REAL pay periods from the current
        one while the horizon arm built a synthetic list -- two answers to
        "when is this owner's next paycheck", only one of which read the
        schedule.  With no retirement date the axis simply ends at the last day
        the saved schedule covers, and every period in it is saved.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            axis = self._axis_for(user_id, None)
            # ``axis`` holds ``DerivedPeriod`` values, which carry the span
            # already; only the ORM row on the right needs the calendar.
            assert axis[-1].end_date == last_covered_day(seed_periods[-1])
            assert all(period.period_id is not None for period in axis)

    def test_a_retirement_date_already_past_gives_an_EMPTY_axis(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The lever page's ``past_horizon`` state, and it must not raise.

        A stored plan date ages; the settings schema refuses a new past date
        but not an old one that has become past.  No paycheck remains for new
        money to land in, so the axis is empty and the annuity factor is zero
        -- which is the state the contribution lever reports rather than
        solving for.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            assert len(self._axis_for(user_id, date(2020, 1, 1))) == 0
