"""Balance-at-T seam -- a loan's interest PAID in a calendar year (fold + schedule).

Plan step **C3c** (``docs/audits/balance_architecture/README.md``).  The ONE
producer of a loan's mortgage-interest figure for a tax year, unified onto the
same total loan producer the balance derives from: the event FOLD
(:func:`app.services.loan_ledger.walk_loan_ledger`) for the interest a settled
payment ACTUALLY paid, and the resolver schedule for the interest still to come.
It replaces the ledger-reader-plus-schedule HYBRID that lived in
``tax_report_service`` -- two producers (the posting reader
:func:`~app.services.loan_posting_service.confirmed_loan_interest_in_year` and the
schedule) glued with two exclusions -- with one, closing B-6 (the Taxes tab no
longer prints interest for a loan the seam values a different way).

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

from decimal import Decimal

from app.models.account import Account
from app.services.loan_ledger import walk_loan_ledger
from app.services.loan_loaders import loan_payment_due_date
from app.services.resolution_context import BalanceContext
from app.utils.dates import to_display_civil_date

from ._inputs import _require_scenario

_ZERO_MONEY = Decimal("0.00")


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

    walk = walk_loan_ledger(account.id, ctx.scenario.id)
    payment_day = resolved.params.payment_day

    settled_interest = sum(
        (
            split.interest
            for split in walk.payment_splits
            if _paid_year(split.income_shadow) == year
        ),
        _ZERO_MONEY,
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
    the SAME attribution
    :func:`app.services.loan_posting_service.confirmed_loan_interest_in_year` makes,
    so the fold-based figure and the posting reader agree on WHICH year a payment
    lands in -- they differ only in reading the fold's split rather than the posted
    net.

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
