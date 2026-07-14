"""Balance-at-T seam -- a property's SECURED-DEBT series, assembled inside the seam.

The seam's fifth shape.

The property equity chart draws a debt line: for each loan a physical asset
secures, its owed balance per calendar month, from origination to payoff.  That
line IS a balance-at-T series, so by the seam's own charter it belongs here --
but it was assembled in the ROUTE (``app/routes/accounts/detail.py``), which
resolved each loan through the read pass's context and held the whole
:class:`~app.services.loan_resolution.ResolvedLoan` to do it.  A route holding a
``ResolvedLoan`` is a route ONE attribute read (``state.current_balance``) away
from putting an unfenced loan balance on a screen, and the W9906 fence -- which
binds on function names -- is structurally incapable of noticing.  Moving the
assembly here is what lets the fence bind: the property route now calls one seam
entry and never sees a resolver bundle at all.

(The loan DETAIL route is NOT yet on this footing.  ``app/routes/loan/`` still
resolves through ``resolve_loan_seeded`` and renders ``state.current_balance``
directly -- a balance-at-T produced outside the seam, which for a loan whose
genesis ledger is missing answers from the money-blind anchor replay while the
seam RAISES.  That is the next commit's work; it is not fixed here, and this
module does not claim it is.)

**The series carries NO balance, and that is the point.**  ``SecuredLoanSeries``
used to carry ``current_balance``, for exactly one purpose: re-deriving an
``is_retired`` test (``is_originated and current_balance <= 0``) that the ROUTE
also carried its own copy of.  Two copies of one rule is precisely how both came
to drop a mortgage that closes next month -- it owes ``$0.00`` today, which is
true, and it is not remotely retired.  So the seam now answers that question
ONCE (:attr:`~app.services.balance_at.LoanFigures.is_retired`) and hands the
answer over as a boolean; the series needs no balance to apply it, and cannot
render a wrong one even by accident -- the same close-by-construction move
:class:`LoanFigures` and
:func:`~app.services.net_worth_kernel.debt_schedule_rows` make.

**It is ``is_retired``, NOT ``is_paid_off``, and the difference is $197,049.32.**
``is_paid_off`` is ``is_retired`` plus "the ledger shows a confirmed payment" -- a
BADGING guard that stops a degenerate ``$0``-anchor loan being congratulated.  A
mortgage paid off by a LUMP SUM recorded as a balance true-up has no payment rows,
so it reads ``is_paid_off=False`` while owing ``$0.00``.  Charting on that
predicate charts it -- and because a zero-balance loan has an EMPTY resolved
schedule, the back-projection clip below admits its ENTIRE contractual walk, so
the chart drew a 30-year mortgage worth $197,049.32 of debt beside an equity hero
correctly reporting ``$0.00``.  Measured, not theorised.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.
"""

from dataclasses import dataclass

from app.models.account import Account
from app.services.amortization_engine import AmortizationRow
from app.services.loan_loaders import load_rate_changes
from app.services.loan_resolution import contractual_schedule_from_origination
from app.services.resolution_context import BalanceContext

from ._loan_figures import loan_figures


@dataclass(frozen=True)
class SecuredLoanSeries:
    """One secured loan's already-resolved rows for the property equity chart.

    A pure ROW bundle plus the seam's retired predicate.  It carries no balance
    (see the module docstring): the chart derives its per-month debt line from the
    rows, and the only question it asked a balance for -- "is this loan done?" --
    is now answered once, by the seam, and handed over as
    :attr:`is_paid_off`.

    ``back_projection`` and ``schedule`` together span the loan's
    origination-to-payoff months.  NOT necessarily one row per calendar month:
    real data has rowless months (a payment can fold into the following month), so
    the producer forward-fills them
    (:func:`app.services.property_equity_chart._dense_month_balances`) rather than
    reading a gap as ``$0.00``.

    Attributes:
        account_id: The loan account this series belongs to.  The seam returns one
            entry per CONFIGURED secured loan and skips the rest, so a bare list
            cannot be correlated back to the property's ``secured_loans`` by
            position -- the series has to say which loan it is.  It is also what
            the planned ledger-backed debt line needs
            (``followup``: the confirmed tier should read
            :func:`app.services.loan_posting_service.confirmed_loan_balance_map`,
            which is keyed by account).
        back_projection: The contractual-from-origination rows for the months
            BEFORE the resolved schedule begins (a mid-life-imported loan's
            pre-tracking-start estimate;
            :func:`app.services.loan_resolution.contractual_schedule_from_origination`,
            clipped to ``payment_date < schedule[0].payment_date``).  Empty for a
            loan whose schedule already starts at origination.  Rendered as the
            ``estimated`` tier.
        schedule: The loan's resolved schedule (confirmed history + committed
            forward): ``is_confirmed`` rows are recorded history (``confirmed``
            tier), the rest are the committed projection (``projected`` tier).
        is_retired: The seam's
            :attr:`~app.services.balance_at.LoanFigures.is_retired` -- the loan has
            ORIGINATED and its LEDGER-confirmed balance is ``<= 0``.  THE single
            definition of "drop this loan from the chart", answered once by the
            seam so the route that used to pack this series and the producer that
            charts it cannot answer it two ways.

            A loan that has not been BORROWED yet also owes ``$0.00`` and is
            emphatically NOT retired: its whole debt line is still ahead of it.
            The origination half of the test is what keeps a mortgage closing in
            26 days on the chart instead of drawing ten years of debt-free equity.

            NOT ``is_paid_off`` -- see the module docstring; that predicate's
            confirmed-payment guard is about badging, and charting on it drew
            $197,049.32 of debt a borrower did not owe.
    """

    account_id: int
    back_projection: list[AmortizationRow]
    schedule: list[AmortizationRow]
    is_retired: bool


def secured_loan_series(
    property_account: Account, ctx: BalanceContext,
) -> list[SecuredLoanSeries]:
    """Pack each loan a Property secures into its equity-chart rows.

    For every loan in ``property_account.secured_loans``, builds its pre-tracking
    contractual back-projection (clipped to the months before the resolved
    schedule begins) and packs it with the resolved schedule and the seam's
    retired predicate.  Every figure comes from the read pass's ONE memoized
    resolution, so the chart cannot disagree with the equity hero beside it or
    with the /savings debt card.

    A linked account that is not a configured loan (no ``LoanParams``) is skipped,
    exactly as it is for the equity hero
    (:func:`app.services.home_equity_service.resolve_home_equity`), and no scenario
    guard runs here for the same reason: this entry reads no balance, and a
    Property with no configured secured loan must still render its chart for a user
    with no baseline scenario (as it did before the assembly moved into the seam).
    A Property that DOES carry a configured loan raises from the equity hero's
    :func:`app.services.balance_at.balance_at` first -- the property route resolves
    the hero before the chart -- so the fail-loud is unchanged, not weakened.

    Every loan is packed, INCLUDING a retired one; the chart applies
    :attr:`SecuredLoanSeries.is_retired` to drop it.  Deciding it here instead
    would put a second copy of that rule beside the producer's, which is the exact
    duplication that made both drop an unclosed mortgage.  The cost is one
    back-projection walk for a retired loan; the benefit is that the rule has one
    home.

    Args:
        property_account: The Property :class:`~app.models.account.Account`; its
            ``secured_loans`` backref lists the liabilities it secures.
        ctx: The read pass's
            :class:`~app.services.resolution_context.BalanceContext`.

    Returns:
        One :class:`SecuredLoanSeries` per configured secured loan.
    """
    series: list[SecuredLoanSeries] = []
    for loan in property_account.secured_loans:
        figures = loan_figures(loan, ctx)
        if figures is None:
            continue                       # not a configured loan: no debt leg
        resolved = ctx.resolved_loan(loan)
        schedule = resolved.state.schedule
        tracking_start = schedule[0].payment_date if schedule else None
        full_contractual = contractual_schedule_from_origination(
            resolved.params, load_rate_changes(resolved.params.account_id),
        )
        series.append(SecuredLoanSeries(
            account_id=loan.id,
            back_projection=[
                row for row in full_contractual
                if tracking_start is None or row.payment_date < tracking_start
            ],
            schedule=schedule,
            is_retired=figures.is_retired,
        ))
    return series
