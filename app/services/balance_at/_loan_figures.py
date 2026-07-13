"""Balance-at-T seam -- a loan's RICH figures, with the balance deliberately absent.

The seam's fourth shape.  A loan tile wants more than a balance: the monthly
payment, the current rate, the payoff date, and whether the loan is retired.
Those are rich projection detail, not a balance-at-T, and the seam has always
been happy for a consumer to hold them.

What it must NOT hand out is the BALANCE.  ``LoanState.current_balance`` is a
balance-at-today, and the W9906 fence binds on function NAMES -- it cannot see
an attribute read.  So for as long as consumers held a ``LoanState``, the loan's
displayed balance reached the screen without passing the seam: the /savings loan
tile, the net-worth hero that reduces over it, the debt card, the Horizon's
index-0 liability point, and the property-equity card's mortgage leg were ALL
produced outside the one tested place, and the fence was structurally incapable
of noticing.  They agreed with the seam only because both paths happened to
bottom out in the same genesis ledger -- agreement by luck, not by construction,
which is the exact failure signature of the whole balance-bug family
(``docs/audits/balance_architecture/``).

:class:`LoanFigures` closes that by CONSTRUCTION rather than by policing: it
carries no balance, so a consumer holding one cannot render a wrong balance even
by accident.  A consumer that wants a loan's balance has exactly one way to get
it -- :func:`~app.services.balance_at.balance_at` -- which is the point.  This is
the same "do not hand ``current_balance`` to out-of-cluster callers" move that
:func:`~app.services.net_worth_kernel.debt_schedule_rows` makes for the
``DebtSchedule`` bundle (``followup_debt_schedule_attribute_fence.md``).

``is_paid_off`` lives here, not in a consumer, for the same reason: it is a
LEDGER-derived predicate over the loan's confirmed balance, so it belongs beside
the balance rules rather than in a dashboard module that would have to reach for
the balance to compute it.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services.resolution_context import BalanceContext

ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class LoanFigures:
    """A loan's rich resolver figures -- deliberately WITHOUT its balance.

    Every field is projection detail a loan tile renders beside the balance; the
    balance itself is not here, and its absence is the point (see the module
    docstring).

    Attributes:
        monthly_payment: The loan's P&I payment as of the context's ``as_of``
            (the governing rate period's level payment).
        current_rate: The annual interest rate in effect on ``as_of``, as a
            decimal fraction -- the resolver-derived source of truth that
            replaced the retired ``LoanParams.interest_rate`` column.
        payoff_date: The last payment date in the committed (plan-aware)
            schedule -- the month the loan reaches zero.
        is_paid_off: Whether the loan owes nothing: its LEDGER-confirmed balance
            is ``<= 0`` AND it has at least one confirmed payment.  The
            confirmed-payment guard keeps a brand-new loan with a degenerate zero
            anchor from reading as retired.
    """

    monthly_payment: Decimal
    current_rate: Decimal
    payoff_date: date | None
    is_paid_off: bool


def loan_figures(
    account: Account, ctx: BalanceContext,
) -> LoanFigures | None:
    """Return *account*'s rich loan figures, or ``None`` if it is not a loan.

    Reads the read pass's ONE memoized resolution
    (:meth:`~app.services.resolution_context.BalanceContext.loan`), so these
    figures and the balance the same consumer reads from
    :func:`~app.services.balance_at.balance_at` come from the SAME resolution --
    identical by construction, not by two producers agreeing.

    Args:
        account: The account to read.  A non-loan (no ``LoanParams``) returns
            ``None``; the caller renders its non-loan tile.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`.

    Returns:
        The :class:`LoanFigures`, or ``None`` when *account* is not a configured
        loan.
    """
    resolved = ctx.loan(account)
    if resolved is None:
        return None
    state = resolved.state
    return LoanFigures(
        monthly_payment=state.monthly_payment,
        current_rate=state.current_rate,
        payoff_date=state.payoff_date,
        is_paid_off=_is_paid_off(resolved),
    )


def _is_paid_off(resolved) -> bool:
    """Return whether the loan owes nothing -- read from the genesis ledger.

    ``resolved.state.current_balance`` is the ledger-confirmed balance (the read
    switch seeds it from ``confirmed_loan_balance_at``), so this asks the ONE
    producer that books what each payment actually paid.

    This replaced a ``resolve_loan(inputs, date.max)`` probe that could not have
    consulted the ledger even in principle -- ``confirmed_loan_view`` returns
    ``None`` for any ``as_of`` after today -- and so answered from the
    pre-read-switch anchor replay, which is BLIND TO MONEY: it advances one
    SCHEDULED step per confirmed payment and discards the cash.  A loan retired
    by one lump-sum payment read as still-owing (no badge, still active debt on
    the Horizon), and a loan paid SHORT could read as retired and VANISH from the
    debt card's total.  Both are regression-tested
    (``followup_redundant_loan_resolution.md``).

    Args:
        resolved: The loan's
            :class:`~app.services.loan_resolution.ResolvedLoan`.

    Returns:
        ``True`` when the ledger says nothing is owed and at least one payment is
        confirmed.
    """
    if not any(p.is_confirmed for p in resolved.context.payments):
        return False
    return resolved.state.current_balance <= ZERO_MONEY
