"""
Shekel Budget App -- Home-equity producer.

Computes a physical asset's equity (market value minus the balances of the
liabilities it secures) and its loan-to-value ratio, for the Property
detail page now and the Net Worth Cockpit equity card after the rebuild.

This module forks NO math.  The market value is the Property's user-set
anchor balance and each securing loan's balance comes from the balance-at seam
(:func:`app.services.balance_at.balance_at`) -- the same producer the debt card
and the net-worth liability column read -- so the equity number can never
disagree with the loan surfaces.  Equity itself is plain presentation arithmetic
over those canonical inputs; the emergent net-worth sum in
:mod:`app.services.balance_at._kernel` is untouched.

Boundary discipline (``CLAUDE.md``: services are isolated from Flask): no
Flask imports.  All money is :class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services import balance_at, cash_ledger
from app.services.balance_at import BalanceContext

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
    property_account, ctx: BalanceContext,
) -> HomeEquity:
    """Resolve a Property account's equity from its secured loans.

    Market value is the Property's latest balance ASSERTION
    (:func:`app.services.cash_ledger.resolve_anchor`) -- the user's last-set
    valuation, the honest "as of today" figure, since the appreciation
    projection is a forward estimate and not a known present value.  It read
    the ``current_anchor_balance`` cache column until plan step X-f1c3a
    (finding N-83's CACHE half); the figure is identical and the ``or ZERO``
    reducer beside it is gone with the column, since an asserted ``$0.00`` is a
    real valuation and was never "missing".  Each loan in
    ``property_account.secured_loans`` is valued through the balance-at seam off
    the read pass's :class:`~app.services.balance_at.BalanceContext`, so
    its contribution is the SAME figure the debt card and the net-worth liability
    column read -- one resolution, not a parallel one that has to agree.  A linked
    account with no ``LoanParams`` row (not a configured loan) contributes
    nothing; :func:`app.services.balance_at.loan_figures` is the configured-loan
    test, since it is already a seam entry and already returns ``None`` for a
    non-loan.  (It was ``ctx.loan(...) is None``, which handed this module a whole
    ``ResolvedLoan`` -- and therefore an unfenced ``current_balance`` -- to answer
    a yes/no question.)

    This module used to call :func:`resolve_account_loan` itself, which made the
    cockpit's equity card an independent, unfenced re-resolution of the mortgage
    -- one of the eleven a single ``/savings`` render ran, and the one the
    redundancy audit missed entirely.

    Args:
        property_account: The Property :class:`~app.models.account.Account`
            (its ``secured_loans`` backref lists the liabilities it
            secures).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the payment history; its ``as_of`` is the
            resolver's now).

    Returns:
        A :class:`HomeEquity` snapshot for the Property.

    Raises:
        ValueError: When *ctx* has no baseline scenario AND the Property secures at
            least one CONFIGURED loan -- :func:`app.services.balance_at.balance_at`
            runs the seam's ``_require_scenario`` guard, and raises.  (The
            configured-loan test above does not: ``loan_figures`` carries no such
            guard, so a Property with no configured secured loan still resolves to
            an all-market-value ``HomeEquity`` for a user with no baseline.)  An
            earlier version of this docstring claimed each loan "still resolves ...
            exactly as before" with no baseline; that has been false since
            ``7b7c909b``, when the balance moved to the seam.
    """
    market_value = cash_ledger.resolve_anchor(property_account).balance
    balances: list[Decimal] = []
    for loan in property_account.secured_loans:
        if balance_at.loan_figures(loan, ctx) is None:
            continue                       # not a configured loan: no debt leg
        balances.append(balance_at.balance_at(loan, ctx, ctx.as_of))
    return compute_home_equity(market_value, balances)
