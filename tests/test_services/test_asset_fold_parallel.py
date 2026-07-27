"""X-g1: the modelled replay run in PARALLEL with the three shipping bases.

Plan step X-g1 (``docs/audits/balance_architecture/README.md``).  The companion
to ``test_asset_fold.py``: that file grades the replay on HAND-COMPUTED figures,
this one runs it beside the producers plan step X-g2 will replace and
CLASSIFIES every divergence.

**Equality is NOT the pass condition here, and that is the point.**  The
shipping producers are wrong about exactly the cases the replay exists to fix,
so a test demanding equality would pin the defect.  What this file asserts is
that every difference falls into one of three NAMED classes and behaves the way
that class predicts:

* **the reverse projection** -- a period BEFORE the account's latest assertion
  reads a growth curve today and the replay reads the assertion the user
  actually made (findings N-43 / N-74, `$6,315.57` of contradicted history at
  one period on real data);
* **the anchor period** -- ``_investment`` splits on ``period_index >
  anchor_idx``, so the period holding the latest assertion earns NOTHING today
  while ruling R-Y accrues it from the assertion's own day forward;
* **the grain** -- the accrual is per-DAY rather than per-PERIOD (ruling R-T),
  which moves cents rather than dollars and is measured here.

And the file's strongest single result is an EQUALITY, on the shape that
isolates the grain from the other two: with the assertion on the anchor
period's last day and no contribution feed, the daily replay tracks
``growth_engine.project_balance`` -- run per PERIOD from the replay's own
anchor-period balance -- to within a CENT on every post-anchor period
(:class:`TestTheGrainIsARegroupingNotADifferentModel`).  That is what shows the
replay is the same model read at a finer grain rather than a second opinion --
the clean-shape half plan step X-b's own parallel run used, applied here.  The
reference is the pure math engine ruling R-U KEEPS, seeded so that the OTHER
two classes cannot contribute to the number being compared.

No sampling: the every-day class walks all 140 days of the seeded horizon
(a 14-day sample once scored perfect while wrong by $178,103.41 on 22% of days).
"""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.services import growth_engine
from app.services.balance_at import (
    _asset_fold,
    _cash_fold,
    _interest,
    _investment,
)
from app.services.balance_at._asset_contributions import ContributionInputs
from app.services.balance_at._context import BalanceContext
from app.services.projection_inputs import load_investment_params_for_accounts
from tests._test_helpers import (
    create_hysa_account,
    make_appreciating_account,
    make_investment_account,
    override_anchor,
    restamp_opening_assertion,
    settle_instant_on,
)

_LATE_AS_OF = date(2026, 12, 31)
_ZERO = Decimal("0")
_CENT = Decimal("0.01")


def _ctx(seed_user):
    """Return a read-pass context for the seed user, pinned late."""
    return BalanceContext.build(seed_user["user"].id, as_of=_LATE_AS_OF)


def _params_for(account):
    """Return the account's ``InvestmentParams`` through the shared loader."""
    return load_investment_params_for_accounts([account])[account.id]


def _replay(account, ctx, periods, params=None):
    """Return the modelled per-period columns, with no contribution feed."""
    return _asset_fold.asset_period_view(
        account, ctx, periods, ContributionInputs(investment_params=params),
    )


class TestTheGrainIsARegroupingNotADifferentModel:
    """The clean shape: assertion on the anchor period's LAST day, no feed.

    On it the two other divergence classes cannot fire -- there is no period
    before the latest assertion to reverse-project, and the anchor period has
    exactly ONE day inside ruling R-Y's window.  What is left is the grain, and
    the arithmetic says it is a re-grouping: ``period_return_rate`` over a
    14-day span is ``(1 + annual) ** (14/365) - 1``, which is the 14th power of
    the one-day rate the replay applies, so the two compound the SAME curve and
    differ only where cent-rounding lands.
    """

    def test_the_daily_grain_compounds_the_same_curve_as_the_period_rate(
        self, db, seed_user, seed_periods,
    ):
        """Nine post-anchor periods, never more than a cent from the engine.

        $20,000 at 7%, asserted on period 0's LAST day (2026-01-15).  The
        reference is :func:`~app.services.growth_engine.project_balance`
        itself -- the pure math engine ruling R-U KEEPS -- run per PERIOD from
        the replay's own anchor-period balance, so the two share a seed, a rate
        and a period list and the only variable left is the grain.  That is the
        honest isolation: comparing against ``build_investment_balance_map``
        instead would fold in ruling R-Y's dropped anchor-period day
        (measured $3.71 here, pinned by the test below), which is a different
        class.

        Measured on this shape: eight of the nine periods agree EXACTLY and one
        is a cent apart, which is the arithmetic saying what it should --
        ``period_return_rate`` over a 14-day span is ``(1 + annual) **
        (14/365) - 1``, the 14th power of the one-day rate the replay applies,
        so the two compound the same curve and differ only where cent-rounding
        lands.
        """
        account = make_investment_account(
            seed_user, db.session, seed_periods[0], Decimal("20000.00"),
        )
        restamp_opening_assertion(
            db.session, account,
            settle_instant_on(seed_periods[0].end_date),
        )
        db.session.commit()
        ctx = _ctx(seed_user)
        params = _params_for(account)

        replayed = _replay(account, ctx, seed_periods, params)
        engine = growth_engine.project_balance(
            current_balance=replayed[seed_periods[0].id].balance,
            assumed_annual_return=params.assumed_annual_return,
            periods=seed_periods[1:],
        )
        assert len(engine) == 9
        for row in engine:
            gap = replayed[row.period_id].balance - row.end_balance
            assert abs(gap) <= _CENT, (
                f"period {row.period_id} diverged by {gap}, which is more "
                "than cent-rounding: the grain is supposed to be a "
                "re-grouping of the same curve"
            )

    def test_the_anchor_periods_one_day_is_the_only_structural_gap(
        self, db, seed_user, seed_periods,
    ):
        """On that shape the anchor period differs by exactly one day's accrual.

        Ruling R-Y in its smallest form: the shipping map serves the anchor
        period from the flat cash base, so it reads $20,000.00, while the
        replay accrues the assertion's OWN day -- ``round(20000 * ((1.07 **
        (1/365)) - 1)) = $3.71``, hand-computed.  Every wider window is the same
        rule with more days in it (up to a full period on the real Roth IRA,
        $105.26).
        """
        account = make_investment_account(
            seed_user, db.session, seed_periods[0], Decimal("20000.00"),
        )
        restamp_opening_assertion(
            db.session, account,
            settle_instant_on(seed_periods[0].end_date),
        )
        db.session.commit()
        ctx = _ctx(seed_user)
        params = _params_for(account)

        shipped = _investment.build_investment_balance_map(
            account, params, ctx.scenario, seed_periods, [], _ZERO,
        )
        column = _replay(account, ctx, seed_periods, params)[
            seed_periods[0].id
        ]
        assert shipped[seed_periods[0].id] == Decimal("20000.00")
        assert column.accrual == Decimal("3.71")
        assert column.balance - shipped[seed_periods[0].id] == column.accrual


class TestEveryDivergenceIsOneOfThreeClasses:
    """A multi-assertion investment: each period classified, none unexplained.

    The real Roth IRA's shape -- six assertions, the map reading only the last
    -- compressed into the seeded calendar.  Every period is put in exactly one
    class and the class's own prediction is asserted, which is what "every
    divergence explained and signed off" means as a test rather than as prose.
    """

    @staticmethod
    def _account(seed_user, seed_periods):
        """A 401(k) asserted four times, its cache kept in step each time."""
        account = make_investment_account(
            seed_user, db.session, seed_periods[0], Decimal("22909.02"),
        )
        restamp_opening_assertion(
            db.session, account,
            settle_instant_on(seed_periods[0].start_date),
        )
        for index, balance in (
            (1, "23851.08"), (2, "24605.20"), (3, "25959.47"),
        ):
            override_anchor(
                db.session, account, seed_periods[index],
                Decimal(balance), notes=f"assertion {index}",
            )
        db.session.commit()
        return account

    def test_a_pre_assertion_period_reads_the_users_own_number(
        self, seed_user, seed_periods,
    ):
        """Class 1: the shipping map models over recorded facts; the replay does not.

        Periods 0-2 each hold an assertion of their own, and the replay reads
        it back verbatim ($22,909.02 / $23,851.08 / $24,605.20).  The shipping
        map reverse-projects all three from the LATEST assertion, so each one
        differs -- which is finding N-74's mechanism, and the reason the pass
        condition here is a classification rather than equality.
        """
        account = self._account(seed_user, seed_periods)
        ctx = _ctx(seed_user)
        params = _params_for(account)

        shipped = _investment.build_investment_balance_map(
            account, params, ctx.scenario, seed_periods, [], _ZERO,
        )
        replayed = _replay(account, ctx, seed_periods, params)
        asserted = {
            seed_periods[0].id: Decimal("22909.02"),
            seed_periods[1].id: Decimal("23851.08"),
            seed_periods[2].id: Decimal("24605.20"),
        }
        for period_id, balance in asserted.items():
            assert replayed[period_id].balance == balance
            assert replayed[period_id].accrual == Decimal("0.00")
            assert shipped[period_id] != balance, (
                "the shipping map is supposed to contradict the assertion "
                "here -- if it no longer does, this class has been closed "
                "elsewhere and the classification needs re-deriving"
            )

    def test_the_anchor_period_differs_by_its_own_accrual(
        self, seed_user, seed_periods,
    ):
        """Class 2: the period holding the latest assertion earns nothing today.

        The shipping map serves it from the flat cash base ($25,959.47, the
        asserted value); the replay accrues from the assertion's own day to the
        period's end.  The gap IS the replay's reported accrual, exactly.
        """
        account = self._account(seed_user, seed_periods)
        ctx = _ctx(seed_user)
        params = _params_for(account)

        shipped = _investment.build_investment_balance_map(
            account, params, ctx.scenario, seed_periods, [], _ZERO,
        )
        column = _replay(account, ctx, seed_periods, params)[
            seed_periods[3].id
        ]
        assert shipped[seed_periods[3].id] == Decimal("25959.47")
        assert column.accrual == Decimal("67.46")
        assert column.balance - shipped[seed_periods[3].id] == column.accrual

    def test_every_later_period_carries_that_lead_and_never_loses_it(
        self, seed_user, seed_periods,
    ):
        """Class 3: post-anchor periods are the class-2 lead, compounding.

        Each later period is ahead by at least the anchor period's dropped
        accrual, and the gap never shrinks -- both producers compound the same
        curve from bases that differ by that lead, so the difference can only
        grow.  A gap that shrank would mean the two models had diverged rather
        than merely started apart.
        """
        account = self._account(seed_user, seed_periods)
        ctx = _ctx(seed_user)
        params = _params_for(account)

        shipped = _investment.build_investment_balance_map(
            account, params, ctx.scenario, seed_periods, [], _ZERO,
        )
        replayed = _replay(account, ctx, seed_periods, params)
        lead = replayed[seed_periods[3].id].accrual
        previous = lead
        for period in seed_periods[4:]:
            gap = replayed[period.id].balance - shipped[period.id]
            assert gap >= previous - _CENT, (
                f"period {period.period_index} lost ground: {gap} < {previous}"
            )
            previous = gap


class TestTheInterestPathDivergesOnlyByTheGrain:
    """INTEREST already accrues from the assertion, so only the grain is left.

    Plan step X-c2a shipped ruling R-L for this kind, and the base has been the
    cash fold since X-c2b2 -- so the replay and ``base_account_balance_map``
    share their window AND their base, and every remaining cent is the daily
    grain plus ruling R-X's carry.  Measured on the real accounts over 840 days,
    that is $0.04 on the Fidelity Savings and $1.64 on the Money Market.
    """

    def test_the_replay_tracks_the_layered_accrual_within_pennies(
        self, db, seed_user, seed_periods,
    ):
        """Ten periods on a $10,000 HYSA: never more than a few cents apart.

        Measured on this shape: seven periods agree EXACTLY and three sit one
        cent below, so the bound is one cent and there is no DIRECTION to
        assert -- ruling R-X's carry tracks ``round(exact)``, which lands on
        either side of the per-period layer's own rounding.  On the real
        accounts over 840 days the two rules end $0.04 apart on the Fidelity
        Savings and $1.64 apart on the Money Market, the second being larger
        because that account's balance MOVES: the per-period layer accrues a
        whole period on its END balance while the replay accrues each day on
        the balance actually held.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)

        # The incumbent is COMPOSED here rather than called through the kernel:
        # plan step X-g2b deleted ``base_account_balance_map`` with the per-kind
        # ladder (ruling R-AD), and ``_interest.layer_account_interest`` over the
        # cash fold is exactly what that function was.  Building it in the test
        # is what keeps this a parallel run against the producer the replay
        # replaced, rather than a comparison with the replay itself.
        shipped, _ = _interest.layer_account_interest(
            account, ctx, seed_periods,
            _cash_fold.cash_period_balances(
                account, ctx.scenario.id, ctx.as_of, seed_periods,
            ),
            _interest.accrual_params(account),
        )
        replayed = _replay(account, ctx, seed_periods)
        for period in seed_periods:
            gap = replayed[period.id].balance - shipped[period.id]
            assert abs(gap) <= _CENT, (
                f"period {period.period_index} diverged by {gap}"
            )


class TestTheAppreciationPathDivergesOnlyByTheAnchorPeriod:
    """A Property flat-carries its pre-anchor periods today, and so does the fold.

    ``build_appreciation_balance_map`` already declines to back-cast ("a
    manually-asserted point-in-time market value has no historical basis to
    compound backward from"), which is ruling R-S's rule for the other two
    kinds -- so this kind's ONLY structural divergence is the anchor period.
    """

    def test_pre_anchor_periods_agree_and_the_anchor_period_accrues(
        self, db, seed_user, seed_periods,
    ):
        """$100,000 at 3%, asserted on period 3's first day.

        Periods 0-2 flat-carry $100,000.00 under BOTH producers -- the one place
        the shipping modelled map and the replay already agree about the past.
        Period 3 accrues its own 14 days ($113.44, hand-computed at $8.10 a day
        with an $8.11 on the fourteenth) where the shipping map reads the flat
        value.
        """
        account = make_appreciating_account(
            seed_user, db.session, seed_periods[0], Decimal("100000.00"),
            Decimal("0.03000"),
        )
        # The factory now pins its own opening assertion to the anchor period's
        # first day (finding N-77, closed at plan step X-g2a), so the local
        # restamp this test carried is gone.  What it was compensating for is
        # exactly what the helper now owns: an unpinned wall-clock opening is
        # the LATEST assertion, lands past the seeded horizon, and leaves the
        # account accruing nothing anywhere.
        override_anchor(
            db.session, account, seed_periods[3], Decimal("100000.00"),
            notes="market value",
        )
        db.session.commit()
        ctx = _ctx(seed_user)

        shipped = _investment.build_appreciation_balance_map(
            account, ctx.scenario, seed_periods,
        )
        replayed = _replay(account, ctx, seed_periods)
        for period in seed_periods[:3]:
            assert shipped[period.id] == Decimal("100000.00")
            assert replayed[period.id].balance == Decimal("100000.00")
        assert shipped[seed_periods[3].id] == Decimal("100000.00")
        assert replayed[seed_periods[3].id].accrual == Decimal("113.44")


class TestTheTwoGrainsAreOneRunningTotal:
    """Every day of the horizon, and the period map read off the same steps.

    The internal half of the parallel run: a scalar at a date, a daily series
    and the per-period map must be three readings of ONE resolved step list, not
    three producers a test keeps in step.  Walked over all 140 days of the
    seeded calendar -- never sampled.
    """

    def test_every_day_of_the_horizon_agrees_with_the_period_map(
        self, db, seed_user, seed_periods,
    ):
        """The scalar at each period's end_date IS that period's column.

        Asserted for every period, and the day-by-day series is walked whole in
        between so a divergence anywhere inside a period would have to survive
        both checks.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)
        first, last = seed_periods[0].start_date, seed_periods[-1].end_date
        horizon = [
            first + timedelta(days=offset)
            for offset in range((last - first).days + 1)
        ]

        daily = _asset_fold.fold_asset_balances(
            account, ctx, horizon, ContributionInputs(),
        )
        columns = _replay(account, ctx, seed_periods)
        assert len(daily) == 140
        for period in seed_periods:
            assert daily[period.end_date] == columns[period.id].balance

    def test_the_balance_never_goes_backwards_on_a_pure_accrual_shape(
        self, db, seed_user, seed_periods,
    ):
        """With nothing but accrual, every one of the 140 days is >= the last.

        The cheapest whole-horizon sanity the resolving pass can fail: a carry
        that credited a negative step, or an ordering slip that applied a day's
        accrual before its own base, shows up here on the day it happens rather
        than at a period boundary.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)
        first, last = seed_periods[0].start_date, seed_periods[-1].end_date
        horizon = [
            first + timedelta(days=offset)
            for offset in range((last - first).days + 1)
        ]

        daily = _asset_fold.fold_asset_balances(
            account, ctx, horizon, ContributionInputs(),
        )
        previous = Decimal("10000.00")
        for day in horizon:
            assert daily[day] >= previous, f"balance fell on {day}"
            previous = daily[day]
