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
from app.services.loan_resolution import ResolvedLoan
from app.services.resolution_context import BalanceContext

from ._inputs import _require_scenario

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
        is_paid_off: Whether the loan owes nothing: it has ORIGINATED, its
            LEDGER-confirmed balance is ``<= 0``, and it has at least one
            confirmed payment.  The confirmed-payment guard keeps a brand-new loan
            with a degenerate zero anchor from reading as retired.
        is_originated: Whether the loan EXISTS yet -- whether its
            ``origination_date`` has arrived by the read pass's ``as_of``.

            The seam publishes this because ``balance_at`` correctly answers
            ``$0.00`` for a loan that has not been borrowed yet, and a consumer
            that reads a zero balance as "this debt is gone" then reports the
            opposite of the truth.  Three did: the dashboard's debt track counted
            an unclosed mortgage's whole principal as REPAID (66.67% paid, on a
            borrower who had paid nothing), the property equity chart dropped a
            mortgage closing in 26 days and drew ten years of debt-free equity, and
            the year-end panel reported -$198,049.28 of principal "paid".  A zero
            balance means "owes nothing"; it does NOT mean "has no debt ahead of
            it", and only this flag separates the two.
    """

    monthly_payment: Decimal
    current_rate: Decimal
    payoff_date: date | None
    is_paid_off: bool
    is_originated: bool


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
        is_paid_off=_is_paid_off(resolved, ctx.as_of),
        is_originated=_is_originated(resolved, ctx.as_of),
    )


def _is_originated(resolved: ResolvedLoan, as_of: date) -> bool:
    """Return whether the loan has come into existence by *as_of*.

    THE one definition of "does this loan exist yet", shared by
    :attr:`LoanFigures.is_originated` and :func:`_is_paid_off` so the seam cannot
    answer it two ways.  See :attr:`LoanFigures.is_originated` for why the seam
    publishes it at all.

    Args:
        resolved: The loan's
            :class:`~app.services.loan_resolution.ResolvedLoan`.
        as_of: The read pass's as-of.

    Returns:
        ``True`` when the loan's ``origination_date`` has arrived.
    """
    return resolved.params.origination_date <= as_of


def _is_paid_off(resolved: ResolvedLoan, as_of: date) -> bool:
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

    **A loan that has not ORIGINATED is not paid off; it has not been taken out.**
    That guard is not defensive -- it is load-bearing, and this function is where
    the origination fix would otherwise have CREATED a bug.  ``current_balance``
    is now correctly ``0.00`` for a loan configured before it closes, and a
    settled transfer INTO such a loan is constructible through the ordinary
    ``transfer_service`` (a down payment, an earnest deposit), which satisfies the
    confirmed-payment guard below.  Without this check an unclosed mortgage would
    render RETIRED: badged paid off, dropped from the debt card's total, and gone
    from the Horizon's liabilities.  The confirmed-payment guard alone was assumed
    to cover this and does not.

    Args:
        resolved: The loan's
            :class:`~app.services.loan_resolution.ResolvedLoan`.
        as_of: The read pass's as-of, against which the loan's origination is
            tested.

    Returns:
        ``True`` when the loan has originated, the ledger says nothing is owed,
        and at least one payment is confirmed.
    """
    if not _is_originated(resolved, as_of):
        return False
    if not any(p.is_confirmed for p in resolved.context.payments):
        return False
    return resolved.state.current_balance <= ZERO_MONEY


def loan_ledger_domain(
    account: Account, ctx: BalanceContext,
) -> "LoanLedgerDomain | None":
    """Return where a loan's confirmed ledger begins, and what balance it opens at.

    The seam's view of the ledger's DOMAIN -- its lower edge.  A caller measuring a
    CHANGE in a loan's balance across a window (the year-end summary's
    principal-paid) must clamp the window to start here: before this date
    :func:`balance_at` reports ``$0.00``, and that zero means "no record", not "no
    debt".  Subtracting a real balance from that fabricated zero is what made the
    year-end summary report the borrower ADDING $188,753 of debt they had actually
    been paying down.

    **Loan-only, and guarded.**  Returns ``None`` for anything that is not a
    configured loan, exactly as :func:`loan_figures` does.  The guard is
    load-bearing rather than defensive: the underlying reader keys on an
    OPENING-kind posting, and EVERY account carries one (a non-loan's is its
    ``account_opening``), so without the guard a Checking account would resolve
    happily and hand back its asset balance SIGN-INVERTED as an ``opening_balance``
    -- a negative "owed" for a positive asset, from the one seam every consumer is
    told to call.

    Args:
        account: The loan :class:`~app.models.account.Account`.
        ctx: The read pass's :class:`BalanceContext` (supplies the scenario).

    Returns:
        The loan's
        :class:`~app.services.loan_posting_service.LoanLedgerDomain` (its
        ``start_date``, ``opening_date`` and ``opening_balance``), or ``None`` when
        the account is not a configured loan, or is one whose ledger was never
        opened.
    """
    # Pylint: ``import-outside-toplevel`` -- the lazy-seam import pattern this
    # package uses for the loan ledger, so the static import graph carries no
    # ``balance_at -> loan_posting_service`` cycle at module load.
    # pylint: disable=import-outside-toplevel
    from app.services.loan_posting_service import confirmed_loan_ledger_domain

    _require_scenario(ctx)
    if ctx.loan(account) is None:
        return None
    return confirmed_loan_ledger_domain(account.id, ctx.scenario.id)
