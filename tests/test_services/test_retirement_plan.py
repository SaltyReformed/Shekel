"""
Shekel Budget App -- Retirement Plan Producer Tests (C2-f2d-2)

The step made "the retirement picture at a candidate plan" ONE producer where
there were two, and the two claims that carries are graded here:

* **One derivation per plan.**  Asking for the same
  :class:`~app.services.retirement_plan.PlanPoint` twice returns the SAME
  object, so the readiness hero and the lever card's baseline cannot state
  different figures for one plan.  Asserted as IDENTITY, because equality is
  what the old two-derivation shape already satisfied right up until the day
  it did not.
* **One blended return.**  The rate the assumptions rail displays, the rate the
  readiness chart reverses under, and the rate the contribution lever divides
  the shortfall by are one value.  Its arithmetic is pinned HERE rather than in
  ``tests/manual/verify_retirement_render.py``, because on the developer's own
  data every projecting account carries the SAME assumed return -- so the
  weighting, the zero arms and the two-decimal round-trip are all invisible to
  that harness (measured 2026-08-16: removing the quantization moved zero
  lines).
"""

from datetime import date
from decimal import Decimal

from app.models.pension_profile import PensionProfile
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import retirement_levers, retirement_readiness
from app.services.balance_at import BalanceContext
from app.services.retirement_plan import (
    STORED_PLAN,
    PlanPoint,
    _stored_blend_percent,
    load_retirement_inputs,
    picture_at,
)
from app.utils.dates import add_months


class _FakeAccount:
    """An account the blended-return fold can key on: an id and nothing else."""

    def __init__(self, account_id):
        self.id = account_id


class _FakeParams:
    """An ``InvestmentParams`` stand-in carrying only the stored return."""

    def __init__(self, assumed_annual_return):
        self.assumed_annual_return = assumed_annual_return


def _projection(account_id, balance):
    """A per-account projection dict carrying only the blend's two inputs."""
    return {
        "account": _FakeAccount(account_id),
        "current_balance": Decimal(balance),
    }


# ── The blended return, on the axes the production data cannot vary ──


class TestStoredBlendPercent:
    """The balance-weighted average of each account's STORED return.

    Every figure below is hand-computed and stated in the docstring, and every
    case varies an axis the developer's own data holds constant.
    """

    def test_weights_by_balance(self):
        """Two accounts blend by balance, not by count.

        $100,000 at 4% and $300,000 at 8%: the count average would be 6.00%,
        the balance-weighted one is
        (100000*0.04 + 300000*0.08) / 400000 = 28000 / 400000 = 0.07 -> 7.00%.
        The two differ, which is what makes this case a control on the
        weighting rather than on the loop.
        """
        assert _stored_blend_percent(
            [_projection(1, "100000.00"), _projection(2, "300000.00")],
            {1: _FakeParams(Decimal("0.04000")),
             2: _FakeParams(Decimal("0.08000"))},
        ) == Decimal("7.00")

    def test_a_zero_rate_account_still_carries_its_weight(self):
        """A 0% sleeve dilutes the blend; it is not skipped (E-12).

        $100,000 at 0.00% and $100,000 at 7.00% blend to
        (0 + 7000) / 200000 = 0.035 -> 3.50%.  A truthiness check on the rate
        drops the first account entirely and reports 7.00% -- twice the true
        blend, and the exact defect CRIT-04 recorded.
        """
        assert _stored_blend_percent(
            [_projection(1, "100000.00"), _projection(2, "100000.00")],
            {1: _FakeParams(Decimal("0.00000")),
             2: _FakeParams(Decimal("0.07000"))},
        ) == Decimal("3.50")

    def test_a_zero_balance_account_contributes_no_weight(self):
        """A $0 account is real and weighs nothing; it does not skew the blend.

        $0 at 20% beside $50,000 at 6% blends to
        (0 + 3000) / 50000 = 0.06 -> 6.00%: the empty account's rate cannot
        pull the average, and its presence cannot make the result undefined.
        """
        assert _stored_blend_percent(
            [_projection(1, "0.00"), _projection(2, "50000.00")],
            {1: _FakeParams(Decimal("0.20000")),
             2: _FakeParams(Decimal("0.06000"))},
        ) == Decimal("6.00")

    def test_an_account_with_no_params_row_is_skipped(self):
        """No stored rate means no known rate: skipped from BOTH sums.

        $100,000 with no params row beside $100,000 at 6% blends to
        6000 / 100000 = 0.06 -> 6.00%, not 3.00%: an unknown rate must not be
        read as a zero one, because that would report a balanced portfolio as
        half its true growth assumption.  A params row with a NULL rate takes
        the same arm, which the second mapping below states.
        """
        blend_inputs = [_projection(1, "100000.00"), _projection(2, "100000.00")]
        assert _stored_blend_percent(
            blend_inputs, {2: _FakeParams(Decimal("0.06000"))},
        ) == Decimal("6.00")
        assert _stored_blend_percent(
            blend_inputs,
            {1: _FakeParams(None), 2: _FakeParams(Decimal("0.06000"))},
        ) == Decimal("6.00")

    def test_no_weight_at_all_falls_back_to_seven_percent(self):
        """Nothing to blend -> the documented 7.00% default, not a division.

        Both arms of "no weight": no accounts at all, and accounts whose
        balances sum to zero.  The second is the one that would raise on a
        naive implementation.
        """
        assert _stored_blend_percent([], {}) == Decimal("7.00")
        assert _stored_blend_percent(
            [_projection(1, "0.00")], {1: _FakeParams(Decimal("0.09000"))},
        ) == Decimal("7.00")

    def test_the_blend_is_quantized_to_the_two_decimals_shown(self):
        """The rate the solver divides by is the rate the rail displays.

        $100,000 at 5% and $200,000 at 10% blend to
        (5000 + 20000) / 300000 = 0.08333... -> 8.333...% , which quantizes to
        8.33%.  Two decimals is not cosmetic here: the assumptions rail renders
        ``"%.2f"``, and the contribution lever divides the shortfall by an
        annuity factor built from this rate, so an unquantized blend would
        solve "contribute $X per period" at a return the page never stated.
        """
        assert _stored_blend_percent(
            [_projection(1, "100000.00"), _projection(2, "200000.00")],
            {1: _FakeParams(Decimal("0.05000")),
             2: _FakeParams(Decimal("0.10000"))},
        ) == Decimal("8.33")


# ── Seeded scenario: one picture per plan ────────────────────────


def _seed_plan(db, seed_user, *, balance, annual_return, months_out=240):
    """Seed a salary profile, a pension, a retirement date and a 401(k).

    Mirrors ``tests/test_services/test_retirement_levers._seed_scenario`` --
    deliberately, because these tests assert relationships BETWEEN the lever
    producer and the readiness producer and must run on the shape both were
    written against.

    Args:
        db: The test database handle.
        seed_user: The ``seed_user`` fixture dict.
        balance: The 401(k)'s opening anchor balance (Decimal).
        annual_return: Its stored assumed annual return (Decimal).
        months_out: Months from today to the planned retirement date.

    Returns:
        The created 401(k) :class:`~app.models.account.Account`.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import EmployerContributionTypeEnum
    from app.models.investment_params import InvestmentParams
    from app.services import account_service

    user = seed_user["user"]
    filing = db.session.query(FilingStatus).first()
    retirement_date = add_months(date.today(), months_out)

    profile = SalaryProfile(
        user_id=user.id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=filing.id,
        name="Day Job",
        annual_salary=Decimal("80000.00"),
        pay_periods_per_year=26,
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    db.session.flush()

    settings = (
        db.session.query(UserSettings).filter_by(user_id=user.id).one()
    )
    settings.planned_retirement_date = retirement_date
    db.session.add(PensionProfile(
        user_id=user.id,
        salary_profile_id=profile.id,
        name="State Pension",
        benefit_multiplier=Decimal("0.01500"),
        consecutive_high_years=4,
        hire_date=date(2010, 1, 1),
        planned_retirement_date=retirement_date,
        is_active=True,
    ))

    inv_type = db.session.query(AccountType).filter_by(name="401(k)").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=inv_type.id,
            name="401k",
            anchor_balance=balance,
        ),
    )
    db.session.flush()
    db.session.add(InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=annual_return,
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.NONE
        ),
    ))
    db.session.commit()
    return acct


class TestOnePicturePerPlan:
    """One plan point, one derivation -- asserted as identity, not equality.

    **Equality is what the shape this step replaced already satisfied.**  Before
    it, the readiness verdict and the lever card's month-0 probe were two
    independent computations that agreed on the developer's data (funded ratio
    ``0.7463``, required ``$1,120,707.00``, projected ``$836,398.65``) and would
    have gone on agreeing until one of them was edited.  A test asserting they
    are EQUAL passes in both worlds and so grades nothing about the change; a
    test asserting they are the same OBJECT passes only in this one.
    """

    def test_the_same_point_is_derived_once(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two asks for one plan point return the identical object."""
        with app.app_context():
            _seed_plan(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            assert picture_at(inputs, STORED_PLAN) is picture_at(
                inputs, STORED_PLAN,
            )
            delayed = PlanPoint(month_offset=24)
            assert picture_at(inputs, delayed) is picture_at(inputs, delayed)

    def test_different_points_are_different_pictures(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The memo distinguishes every field of the point.

        The failure this guards is the one the C2-f2d-1 seed memo already paid
        for: a key that omits a term of its own value hands one caller another
        caller's answer.  Each point below differs from the stored plan in
        exactly ONE field, so a key that drops that field returns the stored
        plan's picture and the assertion fails on the field that was dropped.
        """
        with app.app_context():
            _seed_plan(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            stored = picture_at(inputs, STORED_PLAN)
            for varied in (
                PlanPoint(month_offset=24),
                PlanPoint(swr_override=Decimal("0.0300")),
                PlanPoint(return_rate_override=Decimal("0.02000")),
                PlanPoint(merit_horizon_override=1),
            ):
                assert picture_at(inputs, varied) is not stored

    def test_the_lever_baseline_is_the_readiness_picture(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The page's two cards read ONE derivation of the stored plan.

        The route derives the picture, shapes the readiness verdict from it and
        hands the same loaded inputs to the lever solver; the solver's month-0
        probe must therefore be a memo HIT rather than a second derivation.
        Asserted by identity through the memo, which is the only way to tell a
        shared answer from two equal ones.
        """
        with app.app_context():
            _seed_plan(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            page_picture = picture_at(inputs, STORED_PLAN)
            retirement_readiness.readiness_from_picture(page_picture)
            retirement_levers.compute_lever_data(inputs)
            assert inputs.picture_memo[STORED_PLAN] is page_picture

    def test_a_uniform_return_override_is_the_blend(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A what-if return replaces the blend rather than being averaged in.

        Every account's weight carries the override, so the balance-weighted
        average of the overridden rates IS the override -- and the chart's
        needed path, the accounts table and the contribution lever's annuity
        factor must all run in that one frame.  Read off the picture, which is
        where the two arms meet.
        """
        with app.app_context():
            _seed_plan(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            assert picture_at(inputs, STORED_PLAN).blended_return == Decimal(
                "0.105",
            )
            override = Decimal("0.02000")
            assert picture_at(
                inputs, PlanPoint(return_rate_override=override),
            ).blended_return == override
