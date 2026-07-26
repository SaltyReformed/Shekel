"""Balance-at-T seam -- modelled INTEREST accrual over a folded base.

The interest-bearing counterpart of :mod:`app.services.balance_at._investment`:
that module models an INVESTMENT's growth and an APPRECIATING asset's
appreciation on top of their cash bases, and this one models an INTEREST
account's accrual on top of its folded cash balance.  One statement of "where
modelled interest begins and how it compounds", shared by the two surfaces that
show it -- the kind-correct balance map (through
:mod:`app.services.balance_at._kernel`) and the budget grid's read-only
"Interest" row (:mod:`app.services.balance_at._grid`) -- so a rendered accrual
and the balance it lifts cannot come from two walks.

**It layers, it does not project** (plan step X-c2b2).  The base balances come
from the cash fold (:func:`app.services.balance_at._cash_fold.cash_period_balances`),
which is the whole cutover: the accrual used to be seeded off the
``current_anchor_balance`` CACHE column and carried forward over still-Projected
rows only, so it compounded on a balance that ignored every row settled since
the last assertion.  On the real Money Market that was ``$2,000.00`` of settled
money the base never saw, and the grid rendered the gap as ``$2,007.01`` of
INTEREST EARNED (finding N-49) until both halves moved together.  Because both
halves now derive from ONE fold over ONE period list, the grid's accrual row IS
the map this module returns rather than the difference of two independently
computed balance maps (finding N-52).

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.interest_params import InterestParams
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import resolve_anchor
from app.services.interest_projection import calculate_interest

from ._context import BalanceContext


def accrual_params(account: Account) -> "InterestParams | None":
    """Return *account*'s interest params if it models an accrual, else ``None``.

    "Does this account earn modelled interest?" asked ONCE.  Both readers that
    layer an accrual -- the kind-correct balance map
    (:func:`app.services.balance_at._kernel.base_account_balance_map`) and the
    grid's column set (:func:`app.services.balance_at.grid_balance_view`) --
    ask it here rather than each spelling out the kind test and the params
    presence, because two spellings of one predicate is how a screen ends up
    accruing where a sibling screen does not (plan Section 8's "a DRY refactor
    of a PREDICATE can move money" -- these two provably answer the same
    question, so they share the rule rather than mirroring it).

    An INTEREST-kinded account with NO params row models nothing: it is a HYSA
    the user has not configured, and inventing a rate for it would put interest
    on a screen the account has never earned.

    Args:
        account: The account to test.  The ``hasattr`` covers a non-ORM test
            fake with no ``interest_params`` attribute at all, which the
            balance paths have always tolerated.

    Returns:
        The account's :class:`~app.models.interest_params.InterestParams`, or
        ``None`` when it models no accrual.
    """
    if classify_account(account) is not AccountProjectionKind.INTEREST:
        return None
    if not hasattr(account, "interest_params"):
        return None
    return account.interest_params or None


def layer_account_interest(
    account: Account,
    ctx: BalanceContext,
    periods: list,
    base_balances: "OrderedDict[int, Decimal]",
    interest_params: InterestParams,
) -> "tuple[OrderedDict[int, Decimal], dict[int, Decimal]]":
    """Layer *account*'s modelled accrual onto its folded *base_balances*.

    The ONE place ruling R-L's window is stated, which is why both readers come
    through here rather than each resolving the account's assertion date: the
    balance a screen renders and the interest figure beside it are the same walk
    (plan finding N-47 -- the account-detail "Interest, next 12 mo" chip is this
    map, summed).

    **Where modelled interest begins (ruling R-L, plan step X-c2a).**  The
    accrual window opens at the account's LATEST balance assertion -- the UTC
    civil day of the newest
    :class:`~app.models.account.AccountAnchorHistory` row, read through the dated
    source of truth :func:`~app.services.cash_ledger.resolve_anchor` -- not at
    the anchor PERIOD's start, which precedes it by up to 13 days and modelled
    interest across days the assertion already contains.  Everything at or before
    that assertion is a bank FACT the user typed in.

    **This is now the ONLY cash path that fails loud on a missing assertion
    history, and that asymmetry is deliberate** (plan step X-c2b2).
    ``resolve_anchor`` raises for an account with the anchor COLUMNS set and
    no history row -- the trap against a caller that built an ``Account``
    without the factory.  Every other cash kind used to share it through
    ``balances_for``; they now read the FOLD, which is TOTAL and answers such
    an account from a zero seed rather than raising (the totality rule the
    whole arc turns on -- a partial producer is what forces every caller to
    compose it with a fallback).  Interest cannot follow: an accrual needs a
    DATE to open its window on, and there is no honest window without an
    assertion.  So the fold answers "what is the balance" for a
    history-less account and this refuses to model interest on it.
    Migration ``cfb15e782f86`` and the account factory make the state
    unreachable either way.

    Also note the CLOCK: the assertion's UTC civil day, not its display day.
    A balance is dated in UTC everywhere in this arc (the cash walk's
    ``dated_deltas``, the posting writer's ``to_utc_civil_date``); only the
    TAX figure keys on the display year (plan step C3c).  Interest is a
    balance concern, so it takes the balance clock -- two clocks inside one
    figure is where plan step C6c-ii's double-count came from.

    Args:
        account: The interest-bearing account; its latest assertion opens the
            window.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the anchor resolution).
        periods: The pay periods to walk, ordered by ``period_index``.
        base_balances: The account's NO-interest balances per period id -- the
            cash fold sampled at each period's end.  A period absent from it is
            skipped (see :func:`_layer_interest`); the fold is total over the
            periods it is given, so today nothing is.
        interest_params: The account's
            :class:`~app.models.interest_params.InterestParams` (APY +
            compounding frequency).

    Returns:
        ``(balances, interest_by_period)`` -- the period_id -> ``Decimal``
        end-balance map with interest layered in, and the period_id ->
        ``Decimal`` interest earned in each period.
    """
    return _layer_interest(
        base_balances,
        periods,
        interest_params,
        resolve_anchor(account, ctx.scenario.id).as_of_date,
    )


def _layer_interest(
    base_balances: "OrderedDict[int, Decimal]",
    periods: list,
    interest_params: InterestParams,
    accrual_start: date,
) -> "tuple[OrderedDict[int, Decimal], dict[int, Decimal]]":
    """Layer per-period interest on top of pre-computed base balances.

    Re-walks the periods in order, compounding interest forward: each
    period's interest is computed on its base balance plus the interest
    accrued in prior periods, then folded into the running balance.  So

        balance[p] - balance[p-1] == base[p] - base[p-1] + interest[p]

    which is the ``+ interest[p]`` term of the grid's identity (ruling R-K):
    what the transaction rows on screen cannot explain about the balance change
    is exactly the accrual, by construction.

    **A period accrues only over the days it holds the ASSERTED balance**
    (ruling R-L, plan step X-c2a): the accrual window is
    ``[max(period.start_date, accrual_start) .. period.end_date]``, so a
    period entirely after the assertion accrues in full, the assertion's own
    period accrues from the day it was asserted, and a period that ended
    before the assertion accrues nothing.  Everything at or before that
    assertion is a bank FACT the user typed in, and modelling interest across
    those days adds money the assertion already contains.  Before this rule
    accrual began at the anchor PERIOD's start, which can be up to 13 days
    early: measured on the real Fidelity Savings (``$5,363.56`` at 3.29% APY,
    asserted 2026-04-06 inside the 03-26..04-08 period), ``$6.77`` over 14 days
    where the honest window earns ``$1.45`` over 3.

    That one ``max`` is the whole rule and it needs no branch:
    :func:`~app.services.interest_projection.calculate_interest` returns zero
    for an inverted window (``period_start >= period_end``), so a period ending
    before *accrual_start* falls out arithmetically rather than through a guard
    a later reader could mistake for a special case.  Such a period keeps its
    place in BOTH returned maps, carrying its base balance and a zero accrual
    -- dropping it would put a hole in a column the caller is projecting.  That
    matters more since the base became a fold: every period the caller asks
    about now has a base balance, including the pre-assertion past, and every
    one of those accrues exactly nothing.

    Args:
        base_balances: period_id -> ``Decimal`` end balance, the no-interest
            balances the account's cash fold sampled at each period's end.
        periods: List of PayPeriod objects, ordered by period_index.
        interest_params: Object with .apy (Decimal) and
            .compounding_frequency_id (int).
        accrual_start: ``datetime.date`` -- the first day interest may accrue
            on, the UTC civil day of the account's LATEST balance assertion
            (the caller reads it off the dated ``AccountAnchorHistory`` source
            of truth).  It is NOT assumed to fall inside any particular
            period: the ``max`` above is total over every relationship between
            it and a period's span.

    Returns:
        (balances, interest_by_period) where balances is an OrderedDict
        period_id -> Decimal end balance with interest layered in, and
        interest_by_period maps period_id -> Decimal interest earned.
    """
    apy = interest_params.apy  # Already Decimal from Numeric(7,5) column.
    compounding_id = interest_params.compounding_frequency_id

    # Re-walk periods, layering interest on top of the base balances.
    balances = OrderedDict()
    interest_by_period = {}
    running_balance = None
    interest_cumulative = Decimal("0.00")

    for period in periods:
        if period.id not in base_balances:
            continue

        base_bal = base_balances[period.id]
        # Add cumulative interest from prior periods.
        running_balance = base_bal + interest_cumulative

        # Calculate interest for this period.  Pay periods carry an
        # INCLUSIVE end_date (a 14-calendar-day period runs
        # start .. start + 13), but calculate_interest treats period_end as
        # the EXCLUSIVE right boundary of a half-open [start, end) window
        # (its (period_end - period_start).days convention, verified by its
        # unit tests).  Pass end_date + 1 day -- the true exclusive boundary,
        # equal to the next period's start_date -- so the money accrues over
        # all 14 calendar days it is held, not 13.  Counting only 13 days
        # understated a HYSA's yield by ~1 day in 14 (~7%), the interest-path
        # twin of the growth_engine day-count defect.
        #
        # The left boundary is the LATER of the period's start and the
        # account's latest assertion (ruling R-L): a day at or before that
        # assertion is a bank fact, not a day to model.  An entirely
        # pre-assertion period inverts the window and earns zero without a
        # branch (see the docstring).
        interest = calculate_interest(
            balance=running_balance,
            apy=apy,
            compounding_frequency_id=compounding_id,
            period_start=max(period.start_date, accrual_start),
            period_end=period.end_date + timedelta(days=1),
        )
        interest_cumulative += interest
        running_balance += interest
        interest_by_period[period.id] = interest
        balances[period.id] = running_balance

    return balances, interest_by_period
