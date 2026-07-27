"""Tests for the Property (appreciating physical-asset) projection.

Covers the classifier (Property -> APPRECIATING) and the net-worth kernel's
appreciation balance map (compound forward, flat-carry backward).

Net-worth NETTING (a home against its mortgage) is not covered here -- see
the tombstone at the foot of this file.
"""

from decimal import Decimal

from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.ref import AccountType
from app.services.balance_at import BalanceContext
from app.services import account_service, growth_engine, pay_period_service, savings_dashboard_service
from app.services.balance_at import _kernel as net_worth_kernel
from app.services.balance_at._asset_contributions import (
    ContributionInputs,
)
from tests._test_helpers import (
    restamp_opening_assertion,
    settle_instant_on,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)


def _make_property(db, seed_user, periods, anchor_period, balance, rate=None):
    """Create a Property account, optionally with an appreciation rate.

    **The opening assertion is stamped at the anchor period's first day**, the
    N-77 / N-65 pin the shared ``make_appreciating_account`` helper already
    carries.  ``account_service.create_account`` stamps
    ``AccountAnchorHistory.created_at`` with ``db.func.now()`` -- the DATABASE
    clock, which the suite's frozen today does not reach -- while these fixtures
    seed their pay periods relative to that frozen today.  The opening therefore
    lands MONTHS after the whole seeded horizon, and since plan step X-g2b a
    modelled asset accrues only forward of its LATEST assertion (ruling R-Y),
    an unpinned Property would earn nothing at any period and this file's
    appreciation assertions would all read the flat market value.  That is a
    state production cannot reach: a real opening is stamped when the account is
    created, inside its own anchor period.
    """
    property_type = (
        db.session.query(AccountType).filter_by(name="Property").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=property_type.id,
            name="House",
            anchor_balance=balance,
            anchor_period_id=anchor_period.id,
        ),
    )
    db.session.add(acct)
    db.session.flush()
    if rate is not None:
        db.session.add(AssetAppreciationParams(
            account_id=acct.id, annual_appreciation_rate=rate,
        ))
    restamp_opening_assertion(
        db.session, acct, settle_instant_on(anchor_period.start_date),
    )
    db.session.commit()
    return acct


class TestClassify:
    """The flag-driven classifier routes a Property to APPRECIATING."""

    def test_property_classifies_appreciating(self, app, db, seed_user, seed_periods_today):
        """A Property (has_appreciation=True) classifies as APPRECIATING.

        Checked before INVESTMENT, so the ``has_parameters=True`` Property is
        never mistaken for an investment account.
        """
        with app.app_context():
            acct = _make_property(
                db, seed_user, seed_periods_today, seed_periods_today[0],
                Decimal("400000.00"), rate=Decimal("0.03000"),
            )
            assert classify_account(acct) is AccountProjectionKind.APPRECIATING


class TestAppreciationBalanceMap:
    """The net-worth kernel projects appreciation forward, flat backward."""

    def test_compound_forward_flat_backward(self, app, db, seed_user, seed_periods_today):
        """Pre-anchor holds flat; the ANCHOR period and everything after compound.

        **The anchor period moved at plan step X-g2b (ruling R-Y).**  It used to
        hold flat with the pre-anchor ones, because the shipped producer split
        its periods on ``period_index > anchor_idx`` and served the anchor period
        from the flat cash base -- so a Property earned nothing at all in the
        period it was valued in, and lost that period again every time the user
        re-asserted its value.  The assertion's OWN day accrues now, so with the
        opening pinned to the anchor period's first day the whole 14 days do:
        ``400000 * ((1.03 ** (14/365)) - 1) = $453.76``, hand-computed and
        pinned below.

        Pre-anchor periods still hold flat, and that is a RULING rather than an
        oversight (R-S): a manually-asserted point-in-time market value has no
        historical basis to compound backward from.
        """
        with app.app_context():
            all_periods = sorted(
                seed_periods_today, key=lambda p: p.period_index,
            )
            anchor = all_periods[4]  # mid-list: real pre- and post-anchor periods
            acct = _make_property(
                db, seed_user, all_periods, anchor,
                Decimal("400000.00"), rate=Decimal("0.03000"),
            )
            balances = net_worth_kernel.build_account_balance_map(
                acct, BalanceContext.build(seed_user["user"].id), all_periods,
                ContributionInputs.absent(),
            )

            # Pre-anchor periods hold flat at the user-set value: a
            # manually-set valuation is not back-cast (ruling R-S).
            for period in all_periods:
                if period.period_index < anchor.period_index:
                    assert balances[period.id] == Decimal("400000.00")

            # The ANCHOR period earns its own 14 days, hand-computed above.
            assert balances[anchor.id] == Decimal("400453.76")

            # Post-anchor periods compound forward -- strictly increasing.
            post = [p for p in all_periods if p.period_index > anchor.period_index]
            assert post  # the anchor is mid-list, so post-anchor periods exist
            prev = balances[anchor.id]
            for period in post:
                assert balances[period.id] > prev
                prev = balances[period.id]

            # The growth itself is the SHARED curve, not a second model: seeded
            # at the anchor period's own end balance, the growth engine's
            # per-period projection tracks the daily replay to within a cent at
            # every post-anchor period.  (Not equality -- the grain differs by
            # ruling R-T, and X-g1 measured that difference at at most $0.05
            # across the three real investment accounts.  Equality here would
            # be asserting the replay IS the producer it replaced.)
            expected = {
                pb.period_id: pb.end_balance
                for pb in growth_engine.project_balance(
                    current_balance=balances[anchor.id],
                    assumed_annual_return=Decimal("0.03000"),
                    periods=post,
                )
            }
            for period in post:
                assert abs(
                    balances[period.id] - expected[period.id],
                ) <= Decimal("0.01"), f"period {period.period_index} diverged"

    def test_zero_rate_is_flat(self, app, db, seed_user, seed_periods_today):
        """A Property with a 0% rate carries its value flat at every period."""
        with app.app_context():
            all_periods = sorted(
                seed_periods_today, key=lambda p: p.period_index,
            )
            acct = _make_property(
                db, seed_user, all_periods, all_periods[0],
                Decimal("400000.00"), rate=Decimal("0.00000"),
            )
            balances = net_worth_kernel.build_account_balance_map(
                acct, BalanceContext.build(seed_user["user"].id), all_periods,
                ContributionInputs.absent(),
            )
            # rate 0 -> no growth; every period equals the anchor value.
            for period in all_periods:
                assert balances[period.id] == Decimal("400000.00")

    def test_no_params_flat_carries(self, app, db, seed_user, seed_periods_today):
        """A Property with no appreciation params row flat-carries its value.

        The create flow seeds a zero-rate row, but the kernel must still
        degrade gracefully (flat carry) if the row is absent.
        """
        with app.app_context():
            all_periods = sorted(
                seed_periods_today, key=lambda p: p.period_index,
            )
            acct = _make_property(
                db, seed_user, all_periods, all_periods[0],
                Decimal("400000.00"), rate=None,  # no params row
            )
            balances = net_worth_kernel.build_account_balance_map(
                acct, BalanceContext.build(seed_user["user"].id), all_periods,
                ContributionInputs.absent(),
            )
            for period in all_periods:
                assert balances[period.id] == Decimal("400000.00")


class TestSavingsDashboardProjection:
    """The savings dashboard projects a Property without error."""

    def test_property_horizons_and_no_setup_badge(self, app, db, seed_user, seed_periods_today):
        """A past-anchored Property tile reports its model-from-anchor value, no badge.

        The Property is anchored at ``seed_periods_today[0]`` -- three periods
        before today's period (period 4 per the fixture) -- with a 3% rate, so
        the Level 1 ``balance_at`` seam compounds its $400,000 market value
        forward to today.  The tile's ``current_balance`` therefore ADOPTS the
        model-from-anchor value the net-worth trend and year-end summary
        already report (developer-authorized; the pre-seam tile showed the
        flat market value here).  It equals the canonical net-worth kernel's
        appreciation map at the current period and is strictly above the flat
        $400,000 anchor, and the configured params row fires no setup badge.
        """
        with app.app_context():
            acct = _make_property(
                db, seed_user, seed_periods_today, seed_periods_today[0],
                Decimal("400000.00"), rate=Decimal("0.03000"),
            )
            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            entry = next(
                ad for ad in data["account_data"]
                if ad["account"].id == acct.id
            )
            # The tile adopts the model-from-anchor value: the canonical
            # net-worth kernel's appreciation map at the current period (read
            # the SAME way the dashboard does, via get_current_period, rather
            # than a fixture-index guess).  Cross-checked against the kernel
            # producer (not a pinned magic number) and asserted strictly above
            # the flat $400,000 the pre-seam tile showed, so the appreciation
            # is provably applied.  gross/deductions are irrelevant on the
            # appreciation path, so a 0 gross reproduces the tile's map exactly.
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            modeled_map = net_worth_kernel.build_account_balance_map(
                acct, BalanceContext.build(seed_user["user"].id),
                seed_periods_today,
                ContributionInputs.absent(),
            )
            assert entry["current_balance"] == modeled_map[current_period.id]
            assert entry["current_balance"] > Decimal("400000.00")
            assert isinstance(entry["projected"], dict)
            # The params row exists, so no "needs setup" affordance fires --
            # the regression the classifier fix guards against.
            assert entry["needs_setup"] is False


# ``TestNetWorthNetting`` stood here.  It asserted that a Property nets
# against its mortgage, but it did so by calling the kernel's
# ``sum_net_worth_at_period`` with two hand-built dicts -- a function with no
# production caller, so the test exercised no path a screen renders and could
# not have caught a netting regression.  Both were deleted.
#
# The live netting is covered on the real path in
# ``test_savings_dashboard_service.py``, by tests that build real accounts and
# read the cockpit producer: ``test_assets_minus_liabilities`` (a real
# mortgage against real assets).  The ``abs`` on a negatively-stored
# liability has a SEPARATE control at each of its two reduction sites:
#   * hero            -- test_a_negative_balance_liability_still_adds_its_magnitude
#   * per-period band -- test_series_liability_band_holds_a_negative_balance_magnitude
# None of them uses a Property specifically; the netting rule is keyed on the
# liability flag, not on the asset's kind.  Deliberately NOT cited:
# ``test_net_equals_assets_minus_liabilities_each_point`` -- the series appends
# ``assets - liabilities`` and those same two values, so that assertion is a
# tautology and pins nothing.
