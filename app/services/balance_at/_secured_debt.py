"""Balance-at-T seam -- a property's SECURED-DEBT series, assembled inside the seam.

The seam's fifth shape.

The property equity chart draws a debt line: for each loan a physical asset
secures, its owed balance per calendar month, from origination to payoff.  That
line IS a balance-at-T series, so by the seam's own charter it belongs here --
and since plan step C5 it is the FOLD (:func:`~app.services.balance_at.positions`),
not a walk of the resolver's contractual schedule rows.

**The recorded and projected months are the fold (finding B-2).**  Before C5 the
chart read each month's debt off a schedule row's ``remaining_balance`` -- the
resolver's CONTRACTUAL amortization, which advances one scheduled installment
whether or not the borrower paid it -- and it disagreed with the equity hero
(which reads the fold through :func:`~app.services.balance_at.balance_at`) on eight
of thirteen loan shapes, by up to $299,701.35.  Now every month at or before today
reads :func:`positions` folded over the loan's SOURCE events (what the borrower
actually paid), and every month after today reads the SAME forward projection the
scalar and the liability band read, so the chart's balance and the hero's balance
come from the ONE producer and cannot disagree.  Its ``today``-month point samples
:func:`positions` at ``ctx.as_of`` itself, so it equals the hero's balance to the
cent and the chart reconciles AT today, not merely at the last fully-confirmed
month.

**The pre-tracking ESTIMATED tier is the deliberate exception (ruling D2).**  A
mid-life-imported loan has no payment record before its tracking-start assertion,
so the fold holds the origination principal FLAT across those months (a balance
does not move without a recorded event).  That flat plateau is not what the loan
owed while it amortized unseen, so those months instead carry the CONTRACTUAL
back-projection (:func:`~app.services.loan_resolution.contractual_schedule_from_origination`,
rendered as the visually-distinct ``estimated`` tier) -- a prediction that fills a
gap in the record, never overwriting one.  The honest step where the estimate meets
the recorded opening is kept, not smoothed.

**The empty-schedule clip is gone (finding FU-8).**  The pre-C5 back-projection
clip keyed on the schedule: an EMPTY schedule made ``tracking_start`` ``None`` and
admitted the loan's ENTIRE contractual walk as ``estimated`` -- a 30-year mortgage's
worth of phantom debt beside an equity hero reporting ``$0.00``.  Now an empty
schedule draws NO back-projection, and the loan's real balance comes from the fold,
which answers ``$0.00`` after payoff rather than a contractual walk nobody owes.

**The series carries a tiered per-month debt MAP, and no scalar balance.**  It used
to carry ``current_balance`` for one purpose: re-deriving an ``is_retired`` test
(``is_originated and current_balance <= 0``) the ROUTE also carried its own copy of.
Two copies of one rule is precisely how both came to drop a mortgage that closes next
month -- it owes ``$0.00`` today, which is true, and it is not remotely retired.  So
the seam now answers that question ONCE
(:attr:`~app.services.balance_at.LoanFigures.is_retired`) and hands the answer over as
a boolean; the map is the chart's actual payload, not a scalar a route could render
raw.

**It is ``is_retired``, NOT ``is_paid_off``, and the difference is $197,049.32.**
``is_paid_off`` is ``is_retired`` plus "the ledger shows a confirmed payment" -- a
BADGING guard that stops a degenerate ``$0``-anchor loan being congratulated.  A
mortgage paid off by a LUMP SUM recorded as a balance true-up has no payment rows, so
it reads ``is_paid_off=False`` while owing ``$0.00``.  Charting on that predicate
charts it; the chart drops it on ``is_retired`` instead.  (The fold would in any case
answer ``$0.00`` for its whole post-payoff span, so the phantom walk the pre-C5
empty-schedule clip drew for such a loan cannot recur even if it slipped past the
drop.)

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.services.loan_loaders import load_rate_changes
from app.services.loan_resolution import (
    ResolvedLoan,
    contractual_schedule_from_origination,
)
from app.utils.dates import add_months, months_between

from ._context import BalanceContext
from ._loan_figures import LoanFigures, loan_figures
from ._plan import memoized_plan
from ._positions import positions, window_sample_date

# ``debt_tier`` values: the per-month confidence of the summed debt line, so the
# renderer can style the pre-tracking contractual estimate apart from recorded
# history and forward projection.  Defined here, beside the assembly that assigns
# them (each month's tier is decided when its balance is sourced -- estimated
# months from the back-projection, the rest from the fold split on ``today``), and
# re-exported by :mod:`app.services.property_equity_chart` for the producer that
# merges them across loans and the route that flags the estimated tier.
TIER_ESTIMATED = "estimated"
TIER_CONFIRMED = "confirmed"
TIER_PROJECTED = "projected"


@dataclass(frozen=True)
class SecuredLoanSeries:
    """One secured loan's fold-derived per-month debt line for the equity chart.

    A pure balance-at-T bundle plus the seam's retired predicate.  It carries no
    scalar balance (see the module docstring): the only question the chart ever
    asked a balance for -- "is this loan done?" -- is now answered once, by the
    seam, and handed over as :attr:`is_retired`.

    Attributes:
        account_id: The loan account this series belongs to.  The seam returns one
            entry per CONFIGURED secured loan and skips the rest, so the series has
            to say which loan it is (a bare list cannot be correlated back to the
            property's ``secured_loans`` by position).
        month_balances: The loan's owed balance per calendar month, keyed by
            ``(year, month)`` and tagged with its confidence tier, spanning the
            loan's origination month through the end of its debt line
            (:func:`_debt_span_upper` -- its DERIVED payoff, today for a retired
            loan, the plan's last installment for one that never clears).
            Each entry is ``(balance, tier)``: months before the tracking-start
            assertion carry the contractual back-projection (:data:`TIER_ESTIMATED`);
            months at or before today carry the fold of the loan's actual payments
            (:data:`TIER_CONFIRMED`); months after today carry the forward
            projection (:data:`TIER_PROJECTED`).  A month outside this span is
            absent -- the loan owes ``$0.00`` before it originates and after it pays
            off, and the producer reads that absence as zero, so a younger loan
            never adds a balance to a month before it existed and a paid-off loan
            drops out cleanly.
        is_retired: The seam's
            :attr:`~app.services.balance_at.LoanFigures.is_retired` -- the loan has
            ORIGINATED and its ledger-confirmed balance is ``<= 0``.  THE single
            definition of "drop this loan from the chart", answered once by the seam
            so the route that used to pack this series and the producer that charts
            it cannot answer it two ways.

            A loan that has not been BORROWED yet also owes ``$0.00`` and is
            emphatically NOT retired: its whole debt line is still ahead of it.  The
            origination half of the test is what keeps a mortgage closing in 26 days
            on the chart instead of drawing ten years of debt-free equity.

            NOT ``is_paid_off`` -- see the module docstring; that predicate's
            confirmed-payment guard is about badging, and charting on it drew
            $197,049.32 of debt a borrower did not owe.
    """

    account_id: int
    month_balances: dict[tuple[int, int], tuple[Decimal, str]]
    is_retired: bool


def _back_projection_by_month(
    resolved: ResolvedLoan,
) -> dict[tuple[int, int], Decimal]:
    """Return the pre-tracking contractual balance per calendar month (ruling D2).

    The ``estimated`` tier: a mid-life-imported loan's resolved schedule opens at
    its tracking-start assertion, so the origination-to-tracking-start months have
    no payment record and the fold holds the origination principal flat across
    them.  This supplies the honest contractual estimate for those months instead
    -- the
    :func:`~app.services.loan_resolution.contractual_schedule_from_origination`
    balance (amortized from the origination terms on the SAME monthly grid the
    resolved schedule uses), clipped to the months strictly before the tracking
    start and keyed by calendar month.

    Empty ``{}`` in the two cases with no pre-tracking gap to estimate:

    * an IN-APP loan, whose resolved schedule already begins at origination (the
      contractual grid's first row equals the schedule's first row, so nothing is
      strictly before it); and
    * a loan with an EMPTY resolved schedule (a retired loan the producer drops, or
      a degenerate zero-remaining-term one) -- drawing NO back-projection rather
      than, as the pre-C5 clip did, admitting the loan's ENTIRE contractual walk
      because ``tracking_start`` was ``None`` (finding FU-8, closed here).

    Args:
        resolved: The loan's :class:`~app.services.loan_resolution.ResolvedLoan`.

    Returns:
        ``{(year, month): contractual balance}`` for the pre-tracking months, or
        ``{}`` when there is no pre-tracking gap.
    """
    schedule = resolved.state.schedule
    if not schedule:
        return {}
    tracking_start = schedule[0].payment_date
    return {
        (row.payment_date.year, row.payment_date.month): row.remaining_balance
        for row in contractual_schedule_from_origination(
            resolved.params, load_rate_changes(resolved.params.account_id),
        )
        if row.payment_date < tracking_start
    }


def _debt_span_months(origination: date, upper: date) -> list[date]:
    """First-of-month dates from *origination*'s month through *upper*'s month.

    The contiguous calendar span the loan can owe over: its origination month
    through the later of its debt-line end and today (:func:`_debt_span_upper`
    decides the former).  At least one month, since *upper* is never before
    *origination*.
    """
    first = date(origination.year, origination.month, 1)
    return [
        add_months(first, offset)
        for offset in range(months_between(origination, upper) + 1)
    ]


def _debt_span_upper(
    loan: Account, figures: LoanFigures, ctx: BalanceContext,
) -> date:
    """Return the last date the loan's debt line runs to -- never before *as_of*.

    The chart's axis ends where the debt ends, and since plan step C8 the payoff
    is DERIVED (:attr:`~app.services.balance_at.LoanFigures.payoff_date`, the date
    the balance folds to zero) rather than read off the resolver's schedule walk.
    A derived payoff can be ``None``, which is TWO different states, so this
    splits them rather than collapsing both onto one fallback:

    * **RETIRED** (``payoff is None`` and the loan owes nothing): the debt line is
      history -- there is no future debt to draw -- so the span ends at *as_of*.
      That is what the retired loan produced before C8 too (its schedule's last
      row is a past date, which ``max(payoff, as_of)`` resolved to *as_of*), and
      the chart drops the loan on :attr:`SecuredLoanSeries.is_retired` regardless;
      ending here keeps a retired 30-year mortgage from folding 360 dead months.
    * **A real payoff**: ``max(payoff, as_of)`` -- the loan's own end, or today for
      one that pays off imminently.
    * **``None`` and NOT retired**: the loan does not clear within its plan
      (negative amortization, or an underpayment too severe for the
      post-contractual extension).  It still owes money for every month ahead, so
      the span runs to the last installment the PLAN models
      (:meth:`~app.services.balance_at.BalanceContext.loan_plan`, already
      memoized -- no extra derivation).  Falling back to *as_of* here would draw
      NO forward debt for a loan that never stops owing, and the property's
      appreciation line would carry on beside it -- future equity overstated by
      the whole balance, the finding-B-2 shape this producer exists to close.

    Args:
        loan: The secured loan account.
        figures: Its seam :class:`~app.services.balance_at.LoanFigures` (the
            derived payoff and the retired predicate), resolved once by the
            caller.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The span's last date, never earlier than ``ctx.as_of``.
    """
    if figures.payoff_date is not None:
        return max(figures.payoff_date, ctx.as_of)
    if figures.is_retired:
        return ctx.as_of
    plan = memoized_plan(loan, ctx)
    if not plan:
        # Reachable, and not a degenerate: a loan whose whole TERM has already
        # matured while it still owes (a balloon, or a long-delinquent loan).
        # Every contractual installment is in the past, and so is the ESTIMATED
        # tail's extension, so the plan synthesizes nothing -- the model has no
        # future payment to draw.  (``original_principal > 0`` and
        # ``term_months > 0`` are DB check constraints, so an EMPTY contractual
        # schedule is not the case being handled here.)
        return ctx.as_of
    return max(max(payment.due_date for payment in plan), ctx.as_of)


def _sample_date_for(month_first: date, as_of: date) -> date:
    """Return the date to value a month at -- the begun/future window rule.

    Derives the month's end (its last day) and applies the ONE begun/future
    sampling rule (:func:`~app.services.balance_at._positions.window_sample_date`):
    a begun month reads ``min(month end, as_of)`` -- its end for a fully-past month,
    *as_of* for the CURRENT month, so today's point equals the equity hero's balance
    and the chart reconciles at today -- and a future month reads its month end.
    Shared with the per-period map so the two cannot drift on that boundary.
    """
    month_end = add_months(month_first, 1) - timedelta(days=1)
    return window_sample_date(month_first, month_end, as_of)


def _loan_month_balances(
    loan: Account,
    resolved: ResolvedLoan,
    figures: LoanFigures,
    ctx: BalanceContext,
) -> dict[tuple[int, int], tuple[Decimal, str]]:
    """Fold one loan into its tiered per-calendar-month debt line.

    :func:`positions` is sampled in ONE call over every month's representative date
    (:func:`_sample_date_for`), so the past months share a single fold walk of the
    ledger (the future months still project per month).  The pre-tracking months
    then take the contractual back-projection instead
    (:func:`_back_projection_by_month`, the ``estimated`` tier); every remaining
    month is ``confirmed`` when it is begun (the fold of recorded payments) and
    ``projected`` when it is ahead (the forward projection).

    The loan's ORIGINATION month is one such begun, non-estimated month: its
    contractual back-projection grid opens a month LATER (at the first payment), so
    the origination month itself has no estimate and reads the fold's recorded
    opening principal, tagged ``confirmed``.  For a mid-life import that puts one
    ``confirmed`` opening point just before the ``estimated`` back-projection run --
    deliberately: the origination anchor is a hard recorded fact, not a contractual
    guess, and tagging a known opening ``estimated`` would misrepresent it.

    Args:
        loan: The secured loan :class:`~app.models.account.Account`.
        resolved: Its :class:`~app.services.loan_resolution.ResolvedLoan` from the
            read pass's one memoized resolution.
        figures: Its seam :class:`~app.services.balance_at.LoanFigures`, resolved
            once by the caller and threaded in so the span's end
            (:func:`_debt_span_upper`) reads the SAME derived payoff the loan card
            and the /savings chip show -- the chart's axis cannot end at a
            different month from the payoff rendered beside it.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`; its ``as_of``
            is the fold/projection boundary that :func:`positions` also splits on.

    Returns:
        ``{(year, month): (balance, tier)}`` across the loan's whole debt span --
        origination month through :func:`_debt_span_upper`.
    """
    as_of = ctx.as_of
    month_firsts = _debt_span_months(
        resolved.params.origination_date, _debt_span_upper(loan, figures, ctx),
    )
    sample_on = {
        (month_first.year, month_first.month): _sample_date_for(month_first, as_of)
        for month_first in month_firsts
    }
    valued = positions(loan, ctx, list(sample_on.values()))
    estimated = _back_projection_by_month(resolved)

    result: dict[tuple[int, int], tuple[Decimal, str]] = {}
    for month_first in month_firsts:
        key = (month_first.year, month_first.month)
        if key in estimated:
            result[key] = (estimated[key], TIER_ESTIMATED)
        else:
            tier = TIER_CONFIRMED if month_first <= as_of else TIER_PROJECTED
            result[key] = (valued[sample_on[key]], tier)
    return result


def secured_loan_series(
    property_account: Account, ctx: BalanceContext,
) -> list[SecuredLoanSeries]:
    """Fold each loan a Property secures into its equity-chart debt line.

    For every loan in ``property_account.secured_loans``, folds its owed balance
    per calendar month (:func:`_loan_month_balances`) and packs it with the seam's
    retired predicate.  Every figure comes from the read pass's ONE memoized
    resolution, so the chart cannot disagree with the equity hero beside it or with
    the /savings debt card.

    A linked account that is not a configured loan (no ``LoanParams``) is skipped,
    exactly as it is for the equity hero
    (:func:`app.services.home_equity_service.resolve_home_equity`).  A Property that
    carries no configured secured loan therefore folds nothing and still renders its
    chart for a user with no baseline scenario; a Property that DOES carry a
    configured loan is valued through :func:`positions`, whose ``_require_scenario``
    guard raises for a scenario-less read -- but the property route resolves the
    equity hero (:func:`app.services.balance_at.balance_at`) BEFORE the chart, so
    that fail-loud fires there first and the ordering, not this entry, owns it (as
    it did before the assembly moved into the seam).

    Every loan is folded, INCLUDING a retired one; the chart applies
    :attr:`SecuredLoanSeries.is_retired` to drop it.  Deciding it here instead would
    put a second copy of that rule beside the producer's, which is the exact
    duplication that made both drop an unclosed mortgage.  The cost is one memoized
    fold for a retired loan; the benefit is that the rule has one home.

    Args:
        property_account: The Property :class:`~app.models.account.Account`; its
            ``secured_loans`` backref lists the liabilities it secures.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        One :class:`SecuredLoanSeries` per configured secured loan.
    """
    series: list[SecuredLoanSeries] = []
    for loan in property_account.secured_loans:
        figures = loan_figures(loan, ctx)
        if figures is None:
            continue                       # not a configured loan: no debt leg
        resolved = ctx.resolved_loan(loan)
        series.append(SecuredLoanSeries(
            account_id=loan.id,
            month_balances=_loan_month_balances(loan, resolved, figures, ctx),
            is_retired=figures.is_retired,
        ))
    return series
