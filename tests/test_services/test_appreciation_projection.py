"""Tests for the Property (appreciating physical-asset) projection.

Covers the classifier (Property -> APPRECIATING), the net-worth kernel's
appreciation balance map (compound forward, flat-carry backward), and the
emergent net-worth netting of a home against its mortgage.
"""

from decimal import Decimal

from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.ref import AccountType
from app.services import (
    account_service,
    growth_engine,
    net_worth_kernel,
    savings_dashboard_service,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)


def _make_property(db, seed_user, periods, anchor_period, balance, rate=None):
    """Create a Property account, optionally with an appreciation rate."""
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
        """Post-anchor periods compound; the anchor and pre-anchor stay flat."""
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
                acct, seed_user["scenario"], all_periods,
                debt_schedule=None, investment_params=None,
                deductions=[], salary_gross_biweekly=Decimal("0.00"),
            )

            # Pre-anchor and anchor periods hold flat at the user-set value:
            # a manually-set valuation is not back-cast (flat-carry backward).
            for period in all_periods:
                if period.period_index <= anchor.period_index:
                    assert balances[period.id] == Decimal("400000.00")

            # Post-anchor periods compound forward -- strictly increasing.
            post = [p for p in all_periods if p.period_index > anchor.period_index]
            assert post  # the anchor is mid-list, so post-anchor periods exist
            prev = Decimal("400000.00")
            for period in post:
                assert balances[period.id] > prev
                prev = balances[period.id]

            # SSOT: the kernel delegates appreciation to the growth engine, so
            # the post-anchor values equal a direct contributions-zeroed call.
            expected = {
                pb.period_id: pb.end_balance
                for pb in growth_engine.project_balance(
                    current_balance=Decimal("400000.00"),
                    assumed_annual_return=Decimal("0.03000"),
                    periods=post,
                )
            }
            for period in post:
                assert balances[period.id] == expected[period.id]

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
                acct, seed_user["scenario"], all_periods,
                debt_schedule=None, investment_params=None,
                deductions=[], salary_gross_biweekly=Decimal("0.00"),
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
                acct, seed_user["scenario"], all_periods,
                debt_schedule=None, investment_params=None,
                deductions=[], salary_gross_biweekly=Decimal("0.00"),
            )
            for period in all_periods:
                assert balances[period.id] == Decimal("400000.00")


class TestSavingsDashboardProjection:
    """The savings dashboard projects a Property without error."""

    def test_property_horizons_and_no_setup_badge(self, app, db, seed_user, seed_periods_today):
        """A configured Property gets forward horizons and no 'needs setup' badge."""
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
            # The appreciation branch ran without error and reports the
            # market value as the current balance (appreciation shows in the
            # forward horizons; this fixture's periods do not reach the
            # 3/6/12-month marks, so ``projected`` is an empty-but-valid map).
            assert entry["current_balance"] == Decimal("400000.00")
            assert isinstance(entry["projected"], dict)
            # The params row exists, so no "needs setup" affordance fires --
            # the regression the classifier fix guards against.
            assert entry["needs_setup"] is False


class TestNetWorthNetting:
    """A Property nets against its mortgage in the emergent net-worth sum."""

    def test_property_nets_against_mortgage(self):
        """Asset adds, liability subtracts its magnitude -> equity emerges."""
        account_data = [
            {"balances": {7: Decimal("400000.00")}, "is_liability": False},  # home
            {"balances": {7: Decimal("250000.00")}, "is_liability": True},   # mortgage
        ]
        # 400000 - abs(250000) = 150000 of equity, with no special calc.
        assert net_worth_kernel.sum_net_worth_at_period(
            7, account_data,
        ) == Decimal("150000.00")
