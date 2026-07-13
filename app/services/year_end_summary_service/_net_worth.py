"""
Shekel Budget App -- Year-End Summary: net worth and debt progress.

Section 5 (net worth at 12 monthly endpoints) and Section 6 (principal
paid per debt account during the year).
"""

import calendar
from datetime import date
from decimal import Decimal

from app.services import balance_at, net_worth_account_data
from app.services.resolution_context import BalanceContext
# Net-worth sum re-exported from the shared kernel (Loop B Phase 1): the
# private alias is preserved so the section helpers below and the
# year-end net-worth tests keep calling ``_sum_net_worth_at_period``
# unchanged while the one definition lives in the kernel.
from app.services.net_worth_kernel import (
    DebtSchedule,
    sum_net_worth_at_period as _sum_net_worth_at_period,
)
from app.services.year_end_summary_service._periods import (
    _find_period_before_date,
    _get_month_end_periods,
)
from app.services.year_end_summary_service._types import _YearContext

ZERO = Decimal("0")


def _compute_net_worth(
    accounts: list,
    year_ctx: _YearContext,
) -> dict:
    """Compute net worth at 12 monthly endpoints for the year.

    For each month, finds the last pay period ending in or before the
    month's last day, then sums all account balances at that period.
    Liability accounts contribute negative values.

    The per-account balances come from the :mod:`app.services.balance_at`
    seam (:func:`_build_account_data`), so this section reads the same
    per-kind math -- the balance calculator for checking/savings, the
    interest calculator for HYSA-type accounts, the amortization schedule
    for loans, and the growth engine for investments -- as the savings
    cockpit's net-worth trend and the cross-page balance oracle.

    Args:
        accounts: All active user accounts.
        year_ctx: The target year plus its scenario and the full ordered
            period list (used for anchor-based projection).

    Returns:
        dict with monthly_values (list of 12 {month, month_name,
        balance}), jan1, dec31, delta.
    """
    all_periods = year_ctx.all_periods
    year = year_ctx.year
    if not accounts or not all_periods:
        return _empty_net_worth()

    month_end_periods = _get_month_end_periods(year, all_periods)
    account_data = _build_account_data(
        accounts, year_ctx.balance_ctx, all_periods,
    )

    jan1_period = _find_period_before_date(date(year, 1, 1), all_periods)
    jan1_nw = (
        _sum_net_worth_at_period(jan1_period.id, account_data)
        if jan1_period else ZERO
    )

    monthly_values = _compute_monthly_values(
        month_end_periods, account_data, jan1_nw,
    )

    dec31_nw = monthly_values[11]["balance"]
    return {
        "monthly_values": monthly_values,
        "jan1": jan1_nw,
        "dec31": dec31_nw,
        "delta": dec31_nw - jan1_nw,
    }


def _build_account_data(
    accounts: list,
    balance_ctx: BalanceContext,
    all_periods: list,
) -> list[dict]:
    """Build balance maps for all accounts with liability flags.

    Asks the :mod:`app.services.balance_at` seam for every account's dense
    per-period balance map (the seam owns the input assembly -- debt
    schedules, investment params, deductions, engine gross -- so this
    section no longer pre-assembles them), then pairs each with its
    liability flag through the shared
    :func:`app.services.net_worth_account_data.to_net_worth_account_data`
    adapter -- the SAME builder the savings cockpit's net-worth region
    uses, so the two surfaces assemble net-worth account data one way.
    Accounts with no anchor period are omitted by the seam, matching the
    prior ``balances is None`` skip.

    Args:
        accounts: All active user accounts.
        balance_ctx: The read pass's ``BalanceContext``.
        all_periods: All user pay periods (the dense domain; the seam
            builds over ALL periods so the entries-aware resolver has its
            anchor seed).

    Returns:
        List of ``{account_id, balances, is_liability}`` dicts (the
        net-worth reducers read only ``balances`` / ``is_liability``).
    """
    balance_maps = balance_at.build_maps(accounts, balance_ctx, all_periods)
    return net_worth_account_data.to_net_worth_account_data(
        accounts, balance_maps,
    )


def _compute_monthly_values(
    month_end_periods: dict, account_data: list, initial: Decimal,
) -> list[dict]:
    """Compute net worth at 12 monthly endpoints.

    Carries forward the last known balance for months without a
    matching period.

    Args:
        month_end_periods: month number -> last PayPeriod mapping.
        account_data: Balance maps from _build_account_data.
        initial: Net worth at the start of the year (Jan 1).

    Returns:
        List of 12 dicts with month, month_name, balance.
    """
    values = []
    last_known = initial
    for month in range(1, 13):
        if month in month_end_periods:
            last_known = _sum_net_worth_at_period(
                month_end_periods[month].id, account_data,
            )
        values.append({
            "month": month,
            "month_name": calendar.month_name[month],
            "balance": last_known,
        })
    return values


def _compute_debt_progress(
    year: int,
    debt_accounts: list,
    debt_schedules: dict[int, DebtSchedule],
    balance_ctx: BalanceContext,
) -> list[dict]:
    """Compute principal paid for each debt account during the year.

    Principal paid = the loan balance at Dec 31 of the PRIOR year (the
    starting balance, before any payment in the target year) minus the
    balance at Dec 31 of the target year.  Both balances come from the
    :mod:`app.services.balance_at` seam
    (:func:`app.services.balance_at.balance_at`) -- the single
    date-precise loan-balance accessor, walking the loan's resolver
    schedule to the exact date and falling back to the resolver's current
    balance before the first upcoming payment (NOT the original principal).
    Reading through the seam keeps this section, the savings cockpit loan
    card, and the net-worth liability column on ONE loan-balance
    derivation so they cannot drift.

    ``debt_schedules`` is consulted ONLY as the membership gate: a loan
    with no resolved schedule (no ``LoanParams`` row) or an empty schedule
    (paid off / fully resolved) has no meaningful principal-paid figure and
    is skipped, exactly as before.  For each surviving loan the seam
    re-resolves the schedule to read its balance; that re-resolution is the
    deliberate, correctness-neutral cost of the seam owning the balance
    read -- it calls the same resolver that built ``debt_schedules``, on the
    same clock, so the figures are identical.

    **The opening window is CLAMPED to where the ledger's knowledge begins.**
    ``principal_paid`` is a CHANGE across a window, so its opening balance must be
    a date the confirmed ledger can actually answer.  Before a loan's opening the
    balance readers return ``$0.00``, and that zero means "we have no record", NOT
    "you owed nothing" (:func:`loan_posting_service.confirmed_loan_balance_at`'s
    caveat).  Subtracting a real year-end balance from that fabricated zero is how
    this section came to report the borrower ADDING the entire balance of every
    mid-life-imported loan: on real data the Mortgage read
    ``jan1=0.00, dec31=175,870.41, principal_paid=-175,870.41`` -- a $175k debt
    INCREASE, for a year in which principal was steadily paid down.

    So the window opens at ``max(Dec-31-prior-year, the ledger's start)``.  For a
    loan tracked from origination that is the plain Dec-31 date and nothing changes.
    For a mid-life import it is the ``tracking_start``, and the figure honestly
    becomes "principal paid since we began tracking this loan" -- which
    ``tracked_from`` reports, so the surface can say so rather than implying a full
    calendar year.  A loan whose ledger begins AFTER the target year is skipped: it
    did not exist, as far as any record goes, and inventing a $0 opening for it is
    the same mistake in a different costume.

    Args:
        year: Target calendar year.
        debt_accounts: Accounts with has_amortization=True.
        debt_schedules: account_id -> that loan's ``list`` of
            :class:`~app.services.amortization_engine.AmortizationRow` (the
            orchestrator passes ``net_worth_kernel.debt_schedule_rows``); the
            membership gate only -- an empty list means no meaningful figure.
        balance_ctx: The read pass's ``BalanceContext`` (scopes the seam's
            loan resolution and pins its as-of).

    Returns:
        List of dicts: [{account_name, account_id, jan1_balance,
        dec31_balance, principal_paid, tracked_from}].  ``tracked_from`` is
        ``None`` when the ledger covers the whole year, else the CIVIL DATE the
        loan's opening balance was asserted on -- the instant ``jan1_balance`` is
        as-of, and the date to show a user.  (Deliberately not the ledger's
        ``start_date``, a pay-period start that can sit days earlier and on which
        the readers report a different balance -- see
        :class:`~app.services.loan_posting_service.LoanLedgerDomain`.)
    """
    if not debt_accounts:
        return []

    year_open = date(year - 1, 12, 31)
    year_close = date(year, 12, 31)

    result = []
    for account in debt_accounts:
        schedule_rows = debt_schedules.get(account.id)
        if not schedule_rows:
            continue

        # Clamp the opening read to a window the ledger can answer (see above).
        domain = balance_at.loan_ledger_domain(account, balance_ctx)
        if domain is not None and domain.start_date > year_close:
            # The loan's record begins after this year entirely: no progress to
            # report, and a $0 opening would fabricate one.
            continue

        if domain is not None and domain.start_date > year_open:
            # The ledger opens mid-year (a mid-life import).  The window's opening
            # balance is the ledger's OPENING balance -- not the balance read AT
            # the start date, which would already net away any payment sharing the
            # opening's pay period and hide principal paid inside the window.
            # The CIVIL date the opening balance was asserted on -- NOT
            # ``start_date``, which is a pay-period start and can sit days earlier,
            # on which the readers report a different (post-payment) balance.
            # Publishing that pair would show a figure the seam contradicts.
            tracked_from = domain.opening_date
            jan1_bal = domain.opening_balance
        else:
            # The ledger covers the whole year: read the balance at the end of the
            # prior year, BEFORE any payment in the target year.
            #
            # At the exact boundary (ledger start == year_open) this branch reads
            # ``balance_at(year_open)``, which nets away any payment sharing the
            # opening's visibility date, while one day later the clamp above does
            # not.  The asymmetry is benign: the netted payment lands in the PRIOR
            # year's window instead, so no principal is lost or double-counted
            # across consecutive years.
            tracked_from = None
            jan1_bal = balance_at.balance_at(account, balance_ctx, year_open)

        dec31_bal = balance_at.balance_at(account, balance_ctx, year_close)
        principal_paid = jan1_bal - dec31_bal

        result.append({
            "account_name": account.name,
            "account_id": account.id,
            "jan1_balance": jan1_bal,
            "dec31_balance": dec31_bal,
            "principal_paid": principal_paid,
            "tracked_from": tracked_from,
        })

    return result


def _empty_net_worth() -> dict:
    """Return a net worth section with empty monthly values."""
    monthly_values = [
        {"month": m, "month_name": calendar.month_name[m], "balance": ZERO}
        for m in range(1, 13)
    ]
    return {
        "monthly_values": monthly_values,
        "jan1": ZERO,
        "dec31": ZERO,
        "delta": ZERO,
    }
