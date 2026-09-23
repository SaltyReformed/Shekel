"""
Shekel Budget App -- Savings Dashboard: what an account's tile shows on a day.

The ONE home of the /savings tile's valuation rule (plan step
credit_card:CC-5-5d, ruling **R-CC88**, ledger row **CC-371**).  A tile does
not show every account at the same instant:

* a CONFIGURED LOAN's tile shows what the seam folds for the DAY itself; and
* every other account's tile shows its column for the pay period containing
  the day -- the balance at that period's END, which the grid reads too, so a
  purchase or a payment already planned later in the period is inside it.

Both were true before this module existed, but the rule was stated nowhere:
the loan arm read :func:`~app.services.balance_at.balance_at` at the pass's
day and every other arm indexed its dense period map at the current period,
in two functions of :mod:`._projections`, while the archived list read the DAY
for every kind.  So an archived card with a purchase planned later in the
current period showed what it owed today where the same card's live tile had
shown what it would owe at the period's end -- measured at CC-5-5d on a Credit
Card holding ``-1,000.00`` with a ``$200.00`` purchase planned inside the
period: tile ``-1,200.00``, the day ``-1,000.00``.  Ruling R-CC88 then asked a
debt goal to read "the same figure as its tile" for its start, its current
figure and its save check -- a THIRD reader of the rule -- so the rule moved
here and every reader calls it.

No Flask imports.
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.liability_sign import owed


def is_configured_loan(account: Account, ctx: BalanceContext) -> bool:
    """Return whether the seam resolves *account* as a CONFIGURED loan.

    :func:`app.services.balance_at.loan_figures`, the seam's loan test, asked
    for an account that has no projection in hand.  A live projection builds
    its :attr:`~._types.AccountProjection.loan` from the same call
    (:func:`._projections._compute_loan_account`), but only for an account
    with a terms row on an amortizing type.  ``loan_figures`` answers ``None``
    for an account with no terms row, so the two differ only for a terms row
    on a NON-amortizing type.  Loan setup refuses one; a re-type can still
    leave one behind (an Auto Loan with terms re-typed to a card while the
    owner has no baseline scenario, so it holds no postings) -- they agree by
    that argument, not by one spelling.  Asking the seam's own
    ``configured_loan`` here would make it one rule; that needs a new export
    from :mod:`app.services.balance_at`, and is reported rather than done.
    On a goal's account the goal door also refuses a loan type without terms
    (ruling R-CC93).  It reads the pass's
    ONE memoized loan resolution, so asking it again for a loan the render
    already resolved costs no second resolution.

    Args:
        account: The account.
        ctx: The read pass.

    Returns:
        ``True`` when the account's tile is valued as a loan's (on the day).
    """
    return balance_at.loan_figures(account, ctx) is not None


def tile_balance_on(
    account: Account,
    ctx: BalanceContext,
    day: date,
    *,
    is_loan: bool | None = None,
    balances: OrderedDict[int, Decimal] | None = None,
) -> Decimal:
    """Return the HELD balance *account*'s /savings tile shows when "today" is *day*.

    The tile's rule, stated once: a configured loan is valued AT *day*; every
    other account reads its dense period map at the pay period containing
    *day* (the balance at that period's end), or the seam's scalar at *day*
    itself when no saved period contains it -- before the owner's first
    payday, or past their horizon.  The live tile reads it at the pass's day;
    a debt goal at the day it was set and at the pass's day (ruling R-CC88);
    the archived list at the pass's day (ledger row CC-371).

    Args:
        account: The account to value.
        ctx: The read pass.  Its calendar decides which period contains *day*.
        day: The day the tile is being read on.
        is_loan: Whether the seam resolves *account* as a CONFIGURED loan.
            A live projection has already asked
            (:attr:`~._types.AccountProjection.loan`) and passes its answer;
            ``None`` asks :func:`is_configured_loan` here -- the archived list,
            an archived debt's goal and the goal door hold no projection.
        balances: The account's dense ``period_id -> balance`` map
            (:func:`app.services.balance_at.balance_map`), when the caller holds
            one -- a live projection built it anyway.  ``None`` builds it here,
            for a non-loan, the one time it is needed.  A loan never reads it.

    Returns:
        The balance the account HOLDS, as its tile values it: negative when it
        owes.

    Raises:
        KeyError: When a saved period contains *day* and the map has no column
            for it -- a seam or period-list defect, never a display state.  The
            map is INDEXED, not ``.get``-defaulted (plan step X-v2, ruling R-CA,
            carried here from the tile's former ``_current_balance_from_map``):
            the seam builds a column for every reported period, and answering a
            missing one with ``None`` or ``$0.00`` renders a real account as one
            the app has no figure for.
        BaselineMissingError: From the seam, when the pass has no baseline
            scenario (one application-level handler answers it).
        PayCalendarError: From the seam or the calendar, when the owner's
            paydays cannot define one.
    """
    if is_loan is None:
        is_loan = is_configured_loan(account, ctx)
    if is_loan:
        return balance_at.balance_at(account, ctx, day)
    period = ctx.calendar().period_containing(day)
    if period is None:
        return balance_at.balance_at(account, ctx, day)
    if balances is None:
        balances = balance_at.balance_map(account, ctx)
    return balances[period.period_id]


def first_tile_day_owing_at_most(
    account: Account,
    ctx: BalanceContext,
    owed_at_most: Decimal,
    *,
    is_loan: bool,
    balances: OrderedDict[int, Decimal] | None,
) -> date | None:
    """Return the first FUTURE day *account*'s tile shows it owing at most *owed_at_most*.

    A debt goal's projected date (ruling R-CC73, read by the tile's rule under
    R-CC88): the day the goal and its tile would both first show the debt
    under its target.  The caller asks only while the debt still owes MORE
    than the target at the pass's day, so the answer lies ahead of it.

    * **A configured loan** is valued on the day, so its day is the VISIBLE
      date of the first projected installment that leaves the balance at or
      under the target -- :func:`app.services.balance_at.first_installment_at_most`
      over the loan page's own forward walk
      (:func:`app.services.balance_at.loan_installments`), the date the fold
      the tile reads moves the balance on.  The loan page's payoff is the SAME
      installment at ``$0.00`` read on its DUE date
      (:func:`app.services.balance_at.installments_payoff`); the two dates
      differ only for an overdue installment still projected, which the fold
      moves to the day after the pass's (ruling D1) -- so a ``$0.00`` goal
      names that day, where the payoff chip names the contract's month.
    * **Any other account** is valued at its period's end, so its tile first
      shows the crossing on the FIRST day of the first later period whose
      column owes at most the target.  Only a saved period can cross: past the
      owner's horizon the fold holds no planned row to move the balance.

    Args:
        account: The debt account.
        ctx: The read pass; its day is "now".
        owed_at_most: The target, as an amount OWED (``>= 0``).
        is_loan: As for :func:`tile_balance_on`, but REQUIRED: its one caller
            already holds the answer.
        balances: As for :func:`tile_balance_on`, and required likewise:
            ``None`` builds the map here for a non-loan.

    Returns:
        The first day the tile shows the account owing at most the target, or
        ``None`` when nothing the owner has planned gets it there.
    """
    if is_loan:
        installment = balance_at.first_installment_at_most(
            balance_at.loan_installments(account, ctx), owed_at_most,
        )
        return None if installment is None else installment.visible_on
    if balances is None:
        balances = balance_at.balance_map(account, ctx)
    for period in ctx.reported_periods():
        if (
            period.start_date > ctx.as_of
            and owed(balances[period.period_id]) <= owed_at_most
        ):
            return period.start_date
    return None
