"""Tests for the home-equity producer (``app.services.home_equity_service``).

``compute_home_equity`` is pure arithmetic over already-resolved inputs;
``resolve_home_equity`` gathers a Property's market value and the resolved
balances of the loans it secures.  Equity = market value - secured debt;
LTV = secured debt / market value.
"""

from datetime import date
from decimal import Decimal

from app.models.asset_appreciation_params import AssetAppreciationParams  # noqa: F401
from app.models.ref import AccountType
from app.services import account_service, home_equity_service
from app.services.home_equity_service import HomeEquity, compute_home_equity
from tests._test_helpers import create_loan_account


class TestComputeHomeEquity:
    """The pure equity + LTV arithmetic."""

    def test_single_loan(self):
        """$400k home, $250k mortgage -> $150k equity, 62.50% LTV."""
        result = compute_home_equity(
            Decimal("400000.00"), [Decimal("250000.00")],
        )
        # equity = 400000 - 250000 = 150000; ltv = 250000/400000 = 0.6250
        assert result == HomeEquity(
            market_value=Decimal("400000.00"),
            total_debt=Decimal("250000.00"),
            equity=Decimal("150000.00"),
            ltv=Decimal("0.6250"),
        )

    def test_two_loans_sum(self):
        """A mortgage and a HELOC both secured by the home sum into debt."""
        result = compute_home_equity(
            Decimal("400000.00"),
            [Decimal("250000.00"), Decimal("30000.00")],
        )
        # total_debt = 280000; equity = 120000; ltv = 280000/400000 = 0.7000
        assert result.total_debt == Decimal("280000.00")
        assert result.equity == Decimal("120000.00")
        assert result.ltv == Decimal("0.7000")

    def test_negative_equity_underwater(self):
        """Debt exceeding value yields negative equity and LTV above 1."""
        result = compute_home_equity(
            Decimal("200000.00"), [Decimal("250000.00")],
        )
        # equity = 200000 - 250000 = -50000; ltv = 250000/200000 = 1.2500
        assert result.equity == Decimal("-50000.00")
        assert result.ltv == Decimal("1.2500")

    def test_zero_market_value_ltv_none(self):
        """LTV is undefined (None) when the market value is zero."""
        result = compute_home_equity(Decimal("0.00"), [Decimal("100.00")])
        # equity = 0 - 100 = -100; ltv = None (division undefined)
        assert result.equity == Decimal("-100.00")
        assert result.ltv is None

    def test_no_loans_all_equity(self):
        """An unencumbered property is all equity at 0% LTV."""
        result = compute_home_equity(Decimal("400000.00"), [])
        # total_debt = 0; equity = 400000; ltv = 0/400000 = 0.0000
        assert result.total_debt == Decimal("0")
        assert result.equity == Decimal("400000.00")
        assert result.ltv == Decimal("0.0000")


class TestResolveHomeEquity:
    """Gathering a Property's equity from its secured loans (DB-backed)."""

    def test_equity_from_linked_loan(self, app, db, seed_user, seed_periods_today):
        """Equity nets the property value against the linked loan's resolved balance."""
        with app.app_context():
            property_type = (
                db.session.query(AccountType).filter_by(name="Property").one()
            )
            prop = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=property_type.id,
                    name="House",
                    anchor_balance=Decimal("400000.00"),
                    anchor_period_id=seed_periods_today[0].id,
                ),
            )
            db.session.add(prop)
            db.session.flush()
            # An unpaid loan resolves to its origination anchor (250000).
            loan = create_loan_account(
                seed_user, db.session, name="Mtg",
                principal=Decimal("250000.00"), rate=Decimal("0.05000"),
                term=360,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()

            scenario = seed_user["scenario"]
            equity = home_equity_service.resolve_home_equity(
                prop, scenario.id, date.today(),
            )
            # market value 400000; the unpaid loan resolves to its 250000
            # anchor; equity = 400000 - 250000 = 150000; ltv = 0.6250.
            assert equity.market_value == Decimal("400000.00")
            assert equity.total_debt == Decimal("250000.00")
            assert equity.equity == Decimal("150000.00")
            assert equity.ltv == Decimal("0.6250")

    def test_unlinked_property_is_all_equity(self, app, db, seed_user, seed_periods_today):
        """A property with no secured loans reports its full value as equity."""
        with app.app_context():
            property_type = (
                db.session.query(AccountType).filter_by(name="Property").one()
            )
            prop = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=property_type.id,
                    name="Paid-off House",
                    anchor_balance=Decimal("300000.00"),
                    anchor_period_id=seed_periods_today[0].id,
                ),
            )
            db.session.commit()
            equity = home_equity_service.resolve_home_equity(
                prop, seed_user["scenario"].id, date.today(),
            )
            # No secured loans -> total_debt 0, equity = market value.
            assert equity.total_debt == Decimal("0")
            assert equity.equity == Decimal("300000.00")
