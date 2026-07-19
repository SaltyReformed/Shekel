"""Balance-at-T seam -- a loan's interest / principal PAID in a calendar year.

Plan steps **C3c** and **C6c** (``docs/audits/balance_architecture/README.md``).
The seam's paid-in-year figures, all folded from the loan's SOURCE events (the
running-balance walk :func:`app.services.loan_ledger.walk_loan_ledger`, sampled
through the read pass's memoized
:meth:`~app.services.resolution_context.BalanceContext.loan_walk`) so a figure and
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
* :func:`loan_interest_in_year` (step **C3c**) -- the tax year's WHOLE
  mortgage-interest figure (Schedule A): the SETTLED interest above PLUS the
  interest still to be paid this year.  It replaced the ledger-reader-plus-schedule
  HYBRID that lived in ``tax_report_service``, closing B-6 (the Taxes tab no longer
  prints interest for a loan the seam values a different way).

**Two clocks, deliberately.**  The interest figure is a TAX figure, so it counts a
payment in the year the user PAID it on their WALL CLOCK
(:func:`app.utils.dates.to_display_civil_date`, the L9 rule) -- which diverges from
the balance ledger's UTC ``entry_date`` clock across the New Year (a settle at 8:05
PM EST Dec 31 is stored 01:05 UTC Jan 1, deductible in the OLD year).  This is why
interest-in-year is NOT ``positions().cum_interest`` keyed on the fold's UTC
visible date, and why it lives in its own function rather than on the balance
producer: the balance is a storage-clock quantity, the deduction a wall-clock one.

**One row per installment (the merge).**  The settled side (fold) and the projected
side (schedule) must not both count the same installment.  They can overlap for an
EARLY-settled payment -- settled before its pay period begins, so it is in the fold
yet its schedule row is still ``is_confirmed=False`` -- which double-counts its
interest (measured: +$489.97).  The merge key is the set of due (year, month) slots
the settled payments occupy, derived from the SAME fold walk the interest comes
from (so the two cannot disagree on which payments are settled): a schedule row
whose slot is already settled is the fold payment, not a second projected
installment.  This is the settled-slot de-dup relocated here from
``tax_report_service._loan_year_interest``; the ``not is_confirmed`` guard the
hybrid also carried is subsumed by reading only the schedule's unconfirmed rows.
The de-dup survives until step **C6** replaces schedule-row projection with payment
RECORDS (D1), at which point there is one record per installment by construction
and no slot to de-dup.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections.abc import Callable
from decimal import Decimal

from app.models.account import Account
from app.services.loan_ledger import LoanLedgerWalk, LoanPaymentSplit
from app.services.loan_loaders import loan_payment_due_date
from app.services.resolution_context import BalanceContext
from app.utils.dates import to_display_civil_date

from ._inputs import _require_scenario

_ZERO_MONEY = Decimal("0.00")


def loan_interest_paid_in_year(
    account: Account, ctx: BalanceContext, year: int,
) -> Decimal:
    """Return the interest *account*'s SETTLED payments actually paid in *year*.

    The loan-detail "Interest paid, YTD" chip (step **C6c**): the interest side of
    each settled payment's real split (:func:`app.services.loan_ledger.walk_loan_ledger`),
    attributed to the DISPLAY-timezone civil YEAR of its paid date
    (:func:`_paid_year`, the L9 tax basis) -- the interest actually PAID, and
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
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
            -- its scenario scopes the walk, memoized so the balance hero and both
            YTD chips fold the loan once.
        year: The calendar year to sum interest paid within (the DISPLAY-tz civil
            year the chip is keyed to).

    Returns:
        The interest paid during *year* as a cent-quantized ``Decimal``.

    Raises:
        ValueError: When ``ctx.scenario`` is None (guard a nullable baseline
            first).
    """
    _require_scenario(ctx)
    return _settled_sum_in_year(
        ctx.loan_walk(account), year, lambda split: split.interest,
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
    year (:func:`_paid_year`).  Sharing :func:`_settled_sum_in_year` with the
    interest chip is what keeps the two chips describing one set of payments.

    Folded from the loan's SOURCE events, it replaces the posting reader
    ``confirmed_loan_principal_in_year`` the chip read before (unmoved by B2 / plan
    E1).  TOTAL, never ``None`` -- see :func:`loan_interest_paid_in_year`.

    Args:
        account: The loan account whose paid principal to sum (the caller owns the
            ownership check).
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
            (its scenario scopes the memoized walk).
        year: The calendar year to sum principal paid within (the DISPLAY-tz civil
            year).

    Returns:
        The principal paid during *year* as a cent-quantized ``Decimal``.

    Raises:
        ValueError: When ``ctx.scenario`` is None (guard a nullable baseline
            first).
    """
    _require_scenario(ctx)
    return _settled_sum_in_year(
        ctx.loan_walk(account), year, lambda split: split.principal,
    )


def _settled_sum_in_year(
    walk: LoanLedgerWalk,
    year: int,
    part: Callable[[LoanPaymentSplit], Decimal],
) -> Decimal:
    """Sum a settled-payment split PART attributed to the display-tz paid *year*.

    The shared paid-in-year core of :func:`loan_interest_paid_in_year`,
    :func:`loan_principal_paid_in_year`, and the SETTLED half of
    :func:`loan_interest_in_year`: it sums *part* (``split.interest`` or
    ``split.principal``) over the walk's settled payment splits whose payment was
    PAID in *year* on the display clock (:func:`_paid_year`).  One derivation, so
    the interest chip, the principal chip, and the Schedule-A figure can never
    disagree on WHICH payments a year contains.

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`.
        year: The DISPLAY-tz civil year to sum within.
        part: The split field to sum -- ``lambda split: split.interest`` or
            ``lambda split: split.principal``.

    Returns:
        The cent-quantized sum of *part* over the payments paid in *year*
        (``0.00`` when none).
    """
    return sum(
        (
            part(split)
            for split in walk.payment_splits
            if _paid_year(split.income_shadow) == year
        ),
        _ZERO_MONEY,
    )


def loan_interest_in_year(
    account: Account, ctx: BalanceContext, year: int,
) -> Decimal:
    """Return *account*'s mortgage interest PAID during *year* -- fold + schedule.

    The Schedule A / debt-interest figure for one loan and one tax year, from the
    same total producer the balance derives from (see the module docstring):

    * **SETTLED (past) interest -- the FOLD.**  Each settled payment's ACTUAL
      accrued interest (:attr:`~app.services.loan_ledger.LoanPaymentSplit.interest`,
      the interest the payment's real cash paid on the reset-aware running balance --
      correct even for an off-schedule extra / short payment, where the schedule's
      replayed figure is not), attributed to the DISPLAY-timezone civil YEAR of its
      paid date (:func:`app.utils.dates.to_display_civil_date`, the L9 tax basis).
      This reads the loan's SOURCE events, not the posting cache, so a loan the
      posting reader cannot value (no genesis opening posting) is still valued from
      its facts -- closing B-6 -- rather than falling back to the schedule.
    * **PROJECTED (future) interest -- the schedule.**  Each still-unconfirmed
      resolver row whose ``payment_date`` falls in *year*, EXCLUDING any due
      (year, month) slot a settled payment already occupies (the merge; see the
      module docstring).

    **Loan-only, and total.**  A non-configured account (no
    :class:`~app.models.loan_params.LoanParams`) has no fold and no schedule, so it
    contributes ``0.00`` -- matching the pre-C3c hybrid, where such an account was
    simply absent from the debt-schedule dict.  (The caller
    :func:`app.services.tax_report_service._build_schedule_a` selects only MORTGAGE
    accounts, but the figure is well defined for any loan.)

    Args:
        account: The loan account whose paid interest to sum (the caller owns the
            ownership check and the mortgage-kind selection).
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
            -- its scenario scopes the fold, and its memoized resolution supplies
            the schedule and the loan's ``payment_day`` (one resolution, no
            re-walk).
        year: The calendar / tax year to sum interest paid within.

    Returns:
        The loan's interest paid during *year* as a ``Decimal`` (``0.00`` for a
        non-configured account, or a configured loan that paid no interest in the
        year and has no projected row in it).

    Raises:
        ValueError: When ``scenario`` is None (callers that resolve a nullable
            baseline must guard first).
    """
    _require_scenario(ctx)
    resolved = ctx.resolved_loan(account)
    if resolved is None:
        return _ZERO_MONEY

    walk = ctx.loan_walk(account)
    payment_day = resolved.params.payment_day

    settled_interest = _settled_sum_in_year(
        walk, year, lambda split: split.interest,
    )
    settled_slots = {
        _due_slot(split.income_shadow, payment_day)
        for split in walk.payment_splits
    }
    projected_interest = sum(
        (
            row.interest
            for row in resolved.state.schedule
            if not row.is_confirmed
            and row.payment_date.year == year
            and (row.payment_date.year, row.payment_date.month) not in settled_slots
        ),
        _ZERO_MONEY,
    )
    return settled_interest + projected_interest


def _paid_year(shadow) -> int:
    """Return the DISPLAY-timezone civil YEAR a settled payment was paid in.

    The tax attribution rule (L9): mortgage interest deducts in the year the user
    PAID it on their wall clock, so a payment's interest belongs to the display-tz
    civil year of its ``paid_at`` (falling back to its pay-period start when
    ``paid_at`` is NULL, the same fallback the posting entry dating uses).  This is
    the SAME attribution the posting ledger stamps each interest / principal leg's
    ``entry_date`` with, so the fold-based figure and the posted legs it projects
    agree on WHICH year a payment lands in -- they differ only in reading the fold's
    split rather than the posted net.

    Args:
        shadow: The settled loan-side income shadow (its ``pay_period`` is
            eager-loaded by :func:`~app.services.loan_loaders.settled_income_shadows`).

    Returns:
        The calendar year the payment was paid in, on the display-tz clock.
    """
    return to_display_civil_date(
        shadow.paid_at, shadow.pay_period.start_date,
    ).year


def _due_slot(shadow, payment_day: int) -> tuple[int, int]:
    """Return the ``(year, month)`` installment slot a settled payment satisfies.

    The merge key that keeps a settled payment and its schedule row from both
    counting: the payment's contractual due (year, month), via the project's single
    due-date derivation (:func:`app.services.loan_loaders.loan_payment_due_date`).
    Keyed by month rather than exact date so the exclusion still matches a schedule
    row whose display date the resolver's biweekly-collision redistribution nudged
    within the month (the approximation the retired
    ``loan_loaders.load_settled_payment_due_months`` documented; the settled
    payment's OWN due month never shifts, only display rows do).

    Args:
        shadow: The settled loan-side income shadow.
        payment_day: The loan's contractual day-of-month due day (used only by the
            due-date fallback for a shadow with no stored ``due_date``).

    Returns:
        The ``(year, month)`` of the installment this payment satisfies.
    """
    due = loan_payment_due_date(shadow, payment_day)
    return (due.year, due.month)
