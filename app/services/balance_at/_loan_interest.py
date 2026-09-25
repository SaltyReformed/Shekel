"""Balance-at-T seam -- a loan's interest / principal PAID in a calendar year.

Plan steps **C3c** and **C6c** (``docs/audits/balance_architecture/README.md``).
The seam's paid-in-year figures, all folded from the loan's SOURCE events (the
running-balance walk :func:`app.services.loan_ledger.walk_loan_ledger`, sampled
through the read pass's memoized
:meth:`~app.services.balance_at.BalanceContext.loan_walk`) so a figure and
the balance it describes come from the ONE total producer and cannot disagree:

* :func:`loan_interest_paid_in_year` / :func:`loan_principal_paid_in_year` (step
  **C6c**) -- the loan-detail "Interest paid, YTD" / "Principal paid, YTD" chips:
  the interest / principal a loan's SETTLED payments actually paid in a year, and
  nothing projected.  They are the fold's own splits, so they replace the posting
  readers the chips read before (``confirmed_loan_interest_in_year`` /
  ``confirmed_loan_principal_in_year``, deleted at C6c): the postings are a
  projection of this same fold (B2 / plan E1), so the figure is unmoved, and a loan
  whose posting cache is cold now folds a real figure where the reader returned
  ``None`` and hid the chip.
* :func:`loan_interest_in_year` (steps **C3c** / **C6c**) -- the tax year's WHOLE
  mortgage-interest figure (Schedule A): the SETTLED interest above PLUS the
  interest still PROJECTED to be paid in the year, both read off the loan's ONE
  timeline (:func:`~._loan_stream.loan_timeline`, plan step
  **recurrence:R16-c-1**) -- the SAME walk the loan's balance folds, so the
  deduction and the balance agree on the FUTURE as C3c made them agree on the
  PAST.  It replaced the ledger-reader-plus-schedule HYBRID that lived in
  ``tax_report_service``, closing B-6 (the Taxes tab no longer prints interest
  for a loan the seam values a different way).

**The tax clock is the WALL clock.**  The interest figure is a TAX figure, so it
counts a payment in the year the user PAID it on their wall clock -- the civil
day its money moved, which the outcome carries as ``visible_on``
(:func:`app.services.loan_ledger.payment_visible_on`, the L9 rule).  A UTC clock
diverges from it across the New Year (a settle at 8:05 PM EST Dec 31 is 01:05 UTC
Jan 1, deductible in the OLD year).  *This paragraph called the fold's visible
date that UTC clock, and said interest-in-year could not key on it; ruling
R-DH (b) moved the one clock onto the civil day, which made that false, and
since plan step balance:X-bi-6-4b the year is read off ``visible_on`` itself.*

**One record per installment, by construction.**  The settled half and the
projected half are ONE list since plan step **recurrence:R16-c-1**: the
timeline's outcomes, each either a recorded payment or a projection
(:attr:`~app.services.loan_ledger.PaymentOutcome.is_projected`), so no
installment can be counted twice and no merge key is needed.  Until that step
the two halves came from two replays -- the walk and the plan -- and this
module excluded from the plan's sum every ``(year, month)`` slot a walk payment
occupied (``_due_slot``, ``plan_interest_in_year``'s ``exclude_slots``), a
de-duplication whose rationale had been falsified once (ruling **R-DH (b)**
moved the visibility clock; finding **N-180** recorded the surviving question,
"whether the two sets can still differ for any other reason").  There are no
two sets to differ now.  What the merge key also did, wrongly, was drop a
projected payment's interest whenever an ad-hoc projected extra fell in a
settled installment's month -- a real payment's interest, discarded by a
de-dup built for a synthesis the plan no longer performs (its ESTIMATED tier
answers by occurrence identity since R16-b-2, never by month).

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections.abc import Callable
from decimal import Decimal

from app.models.account import Account
from app.services.loan_ledger import LoanLedgerWalk, PaymentOutcome

from ._context import BalanceContext
from ._inputs import _require_scenario
from ._loan_stream import loan_timeline
from ._resolution import resolved_loan

_ZERO_MONEY = Decimal("0.00")


def loan_interest_paid_in_year(
    account: Account, ctx: BalanceContext, year: int,
) -> Decimal:
    """Return the interest *account*'s SETTLED payments actually paid in *year*.

    The loan-detail "Interest paid, YTD" chip (step **C6c**): the interest side of
    each settled payment's real split (:func:`app.services.loan_ledger.walk_loan_ledger`),
    attributed to the DISPLAY-timezone civil YEAR of its paid date
    (the outcome's ``visible_on``, the L9 tax basis) -- the interest actually PAID, and
    nothing projected.  This is the SETTLED half of :func:`loan_interest_in_year`
    on its own; the two share :func:`_settled_sum_in_year`, so the chip and the
    Schedule-A figure describe one set of payments.

    Folded from the loan's SOURCE events (the read pass's memoized walk), it
    replaces the posting reader ``confirmed_loan_interest_in_year`` the chip read
    before: the postings are a projection of this fold (B2 / plan E1), so the
    figure is unmoved, and a loan whose posting cache is cold folds a real figure
    where the reader returned ``None``.

    **TOTAL, never ``None``.**  A configured loan always walks (its origination
    anchor is synthesized), so this always returns a real ``Decimal`` -- ``0.00``
    for a loan that has paid no interest in *year*, or for a non-configured account
    (an empty walk).  The loan-detail page renders only for a configured loan, so
    the chip always shows the real figure.

    Args:
        account: The loan account whose paid interest to sum (the caller owns the
            ownership check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the walk, memoized so the balance hero and both
            YTD chips fold the loan once.
        year: The calendar year to sum interest paid within (the DISPLAY-tz civil
            year the chip is keyed to).

    Returns:
        The interest paid during *year* as a cent-quantized ``Decimal``.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
    """
    _require_scenario(ctx)
    return _settled_sum_in_year(
        ctx.loan_walk(account), year, lambda outcome: outcome.interest,
    )


def loan_principal_paid_in_year(
    account: Account, ctx: BalanceContext, year: int,
) -> Decimal:
    """Return the principal *account*'s SETTLED payments actually paid in *year*.

    The loan-detail "Principal paid, YTD" chip (step **C6c**), the paid-date
    sibling of :func:`loan_interest_paid_in_year`: the principal side of each
    settled payment's real split (:func:`app.services.loan_ledger.walk_loan_ledger`
    -- extra principal included, a payoff overpayment's refund excluded, so an
    extra or short payment counts honestly), attributed on the SAME display-tz paid
    year (the outcome's ``visible_on``).  Sharing :func:`_settled_sum_in_year` with the
    interest chip is what keeps the two chips describing one set of payments.

    Folded from the loan's SOURCE events, it replaces the posting reader
    ``confirmed_loan_principal_in_year`` the chip read before (unmoved by B2 / plan
    E1).  TOTAL, never ``None`` -- see :func:`loan_interest_paid_in_year`.

    Args:
        account: The loan account whose paid principal to sum (the caller owns the
            ownership check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the memoized walk).
        year: The calendar year to sum principal paid within (the DISPLAY-tz civil
            year).

    Returns:
        The principal paid during *year* as a cent-quantized ``Decimal``.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
    """
    _require_scenario(ctx)
    return _settled_sum_in_year(
        ctx.loan_walk(account), year, lambda outcome: outcome.principal,
    )


def _settled_sum_in_year(
    walk: LoanLedgerWalk,
    year: int,
    part: Callable[[PaymentOutcome], Decimal],
) -> Decimal:
    """Sum a settled-payment split PART attributed to the display-tz paid *year*.

    The shared paid-in-year core of :func:`loan_interest_paid_in_year`,
    :func:`loan_principal_paid_in_year`, and the SETTLED half of
    :func:`loan_interest_in_year`: it sums *part* (``split.interest`` or
    ``split.principal``) over the walk's settled payment splits whose payment was
    PAID in *year* on the display clock: the outcome's ``visible_on``, the civil
    day its money moved as the stream's builder read it ONCE through
    :func:`app.services.loan_ledger.payment_visible_on`.  One derivation, so
    the interest chip, the principal chip, and the Schedule-A figure can never
    disagree on WHICH payments a year contains.

    **It re-read that day off the payment's SHADOW** (``_paid_year``, the
    shadow's ``settled_on`` through ``settled_day``) **until plan step
    balance:X-bi-6-4b**, a second read of the fact the outcome already
    carried; the two agree on every recorded payment.  A ``$0.00`` close,
    which moved no money, is attributed to the year of the installment it
    skips (ruling **R-BAL139**), where the shadow's stated settle day put it
    before.

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`.
        year: The DISPLAY-tz civil year to sum within.
        part: The split field to sum -- ``lambda outcome: outcome.interest``
            or ``lambda outcome: outcome.principal``.

    Returns:
        The cent-quantized sum of *part* over the payments paid in *year*
        (``0.00`` when none).
    """
    return sum(
        (
            part(outcome)
            for outcome in walk.settled_splits
            if outcome.visible_on.year == year
        ),
        _ZERO_MONEY,
    )


def loan_interest_in_year(
    account: Account, ctx: BalanceContext, year: int,
) -> Decimal:
    """Return *account*'s mortgage interest PAID during *year* -- fold + plan.

    The Schedule A / debt-interest figure for one loan and one tax year, from the
    same total producer the balance derives from (see the module docstring), read
    off the read pass's ONE timeline (:func:`~._loan_stream.loan_timeline`):

    * **SETTLED (past) interest.**  Each settled payment's ACTUAL accrued
      interest (its outcome's ``interest`` -- the interest the payment's real
      cash paid on the reset-aware running balance, correct even for an
      off-schedule extra / short payment, where the schedule's replayed figure is
      not), attributed to the DISPLAY-timezone civil YEAR of its paid date
      (the outcome's ``visible_on``, the L9 tax basis).  This
      reads the loan's SOURCE events, not the posting cache, so a loan the
      posting reader cannot value (no genesis opening posting) is still valued
      from its facts -- closing B-6 -- rather than falling back to the schedule.
    * **PROJECTED (future) interest.**  Each projected outcome's interest,
      attributed to the year the payment is projected to be PAID: its visible
      date (``max(due, as_of + 1d)``, ruling D1), so an overdue-but-still-
      projected payment's interest deducts in the year it is expected to clear
      rather than the closed year it was contractually due.  An overdue
      installment with NO record contributes nothing: it is absent from the plan
      entirely (finding B-9), so a delinquent loan's unpaid past does not inflate
      its deduction.  The projection folds its LIVE cash from the same running
      balance the loan's projected BALANCE reads (step **C6c**), and since plan
      step recurrence:R16-c-1 from the same REPLAY: no installment can be in
      both halves, because they are one list.

    **Loan-only, and total.**  A non-configured account (no
    :class:`~app.models.loan_params.LoanParams`) has no facts and no plan, so it
    contributes ``0.00`` -- matching the pre-C3c hybrid, where such an account was
    simply absent from the debt-schedule dict.  (The caller
    :func:`app.services.tax_report_service._build_schedule_a` selects only MORTGAGE
    accounts, but the figure is well defined for any loan.)

    Args:
        account: The loan account whose paid interest to sum (the caller owns the
            ownership check and the mortgage-kind selection).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the timeline; its ``as_of`` is the plan's
            clamp floor.
        year: The calendar / tax year to sum interest paid within.

    Returns:
        The loan's interest paid during *year* as a ``Decimal`` (``0.00`` for a
        non-configured account, or a configured loan that paid no interest in the
        year and has no projected payment landing in it).

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
    """
    _require_scenario(ctx)
    if resolved_loan(account, ctx) is None:
        # Not a configured loan (no LoanParams): it has neither facts nor a
        # plan, so it contributes 0.00 -- matching the pre-C3c hybrid, where
        # such an account was simply absent from the debt-schedule dict.
        return _ZERO_MONEY
    walk = loan_timeline(account, ctx)
    settled_interest = _settled_sum_in_year(
        walk, year, lambda outcome: outcome.interest,
    )
    projected_interest = sum(
        (
            outcome.interest
            for outcome in walk.projected_splits
            if outcome.visible_on.year == year
        ),
        _ZERO_MONEY,
    )
    return settled_interest + projected_interest
