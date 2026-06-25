"""
Shekel Budget App -- Home-equity producer.

Computes a physical asset's equity (market value minus the balances of the
liabilities it secures) and its loan-to-value ratio, for the Property
detail page now and the Net Worth Cockpit equity card after the rebuild.

This module forks NO math.  The market value is the Property's user-set
anchor balance and each securing loan's balance is the resolver-derived
``LoanState.current_balance`` -- the same figures the debt card and the
net-worth liability column read -- so the equity number can never disagree
with the loan surfaces.  Equity itself is plain presentation arithmetic
over those canonical inputs; the emergent net-worth sum in
:mod:`app.services.net_worth_kernel` is untouched.

Boundary discipline (``CLAUDE.md``: services are isolated from Flask): no
Flask imports.  All money is :class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services.loan_payment_service import resolve_account_loan

ZERO = Decimal("0")
# LTV is a display ratio (debt / value), not a money amount; four-place
# rounding keeps a clean percentage (0.6250 -> 62.50%) without inheriting a
# repeating-decimal tail from the division.
_LTV_QUANT = Decimal("0.0001")


@dataclass(frozen=True)
class HomeEquity:
    """A physical asset's equity snapshot.

    Attributes:
        market_value: The asset's user-set market value (its anchor
            balance).
        total_debt: Sum of the resolved current balances of the
            liabilities secured by the asset.
        equity: ``market_value - total_debt``.  Negative when the asset is
            underwater (debt exceeds value); a numeric comparison, never a
            name-string, decides how the UI styles it.
        ltv: Loan-to-value ratio (``total_debt / market_value``) as a
            decimal fraction rounded to four places, or ``None`` when the
            market value is zero (the ratio is undefined).
    """

    market_value: Decimal
    total_debt: Decimal
    equity: Decimal
    ltv: Decimal | None


def compute_home_equity(
    market_value: Decimal, secured_loan_balances: list[Decimal],
) -> HomeEquity:
    """Combine a market value and its securing loan balances into equity.

    Pure arithmetic over already-resolved inputs -- the caller is
    responsible for sourcing ``market_value`` and each balance from the
    canonical producers (the anchor balance and the loan resolver), so this
    function never queries or re-resolves.

    Args:
        market_value: The asset's market value.
        secured_loan_balances: The current balances of the liabilities
            secured by the asset (empty when none are linked).

    Returns:
        A :class:`HomeEquity` snapshot.
    """
    total_debt = ZERO
    for balance in secured_loan_balances:
        total_debt += balance
    equity = market_value - total_debt
    ltv = (
        (total_debt / market_value).quantize(_LTV_QUANT, rounding=ROUND_HALF_UP)
        if market_value > ZERO
        else None
    )
    return HomeEquity(
        market_value=market_value,
        total_debt=total_debt,
        equity=equity,
        ltv=ltv,
    )


def resolve_home_equity(
    property_account, scenario_id: int | None, as_of: date,
) -> HomeEquity:
    """Resolve a Property account's equity from its secured loans.

    Market value is the Property's ``current_anchor_balance`` (the user's
    last-set valuation, the honest "as of today" figure -- the appreciation
    projection is a forward estimate, not a known present value).  Each
    loan in ``property_account.secured_loans`` is resolved through
    :func:`app.services.loan_payment_service.resolve_account_loan`, so its
    contribution is the same ``LoanState.current_balance`` the debt card
    shows.  A linked account with no ``LoanParams`` row (not a configured
    loan) contributes nothing.

    Args:
        property_account: The Property :class:`~app.models.account.Account`
            (its ``secured_loans`` backref lists the liabilities it
            secures).
        scenario_id: The baseline scenario id for payment history, or
            ``None`` when the user has no scenario yet (loans then resolve
            from their anchor with no payment history).
        as_of: The as-of date for the loan resolver.

    Returns:
        A :class:`HomeEquity` snapshot for the Property.
    """
    market_value = property_account.current_anchor_balance or ZERO
    balances: list[Decimal] = []
    for loan in property_account.secured_loans:
        resolved = resolve_account_loan(loan.id, scenario_id, as_of)
        if resolved is None:
            continue
        _params, state = resolved
        balances.append(state.current_balance)
    return compute_home_equity(market_value, balances)
