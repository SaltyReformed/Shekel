"""
Shekel Budget App -- Year-End Summary: income/tax and mortgage interest.

Section 1 (W-2-style income and tax totals) and Section 2 (mortgage
interest paid during the year, for Schedule A).
"""

from decimal import Decimal

from app.models.salary_profile import SalaryProfile
from app.services import loan_loaders, paycheck_calculator
from app.services.loan_posting_service import confirmed_loan_interest_in_year
from app.services.net_worth_kernel import DebtSchedule
from app.services.tax_config_service import load_tax_configs_for_year

ZERO = Decimal("0")


def _compute_income_tax(
    user_id: int,
    year: int,
    periods: list,
    salary_profiles: list,
) -> dict:
    """Aggregate W-2-style income and tax totals for the year.

    Calls the paycheck calculator for each active salary profile
    across all pay periods in the year, then sums the results.

    Pre-tax and post-tax deductions are grouped by deduction name
    so each deduction type (e.g. 401k, HSA) shows its annual total.

    Args:
        user_id: User ID for loading tax configs.
        year: Calendar year for tax config lookup.
        periods: Pay periods with start_date in the target year.
        salary_profiles: Active SalaryProfile objects with loaded
            raises and deductions.

    Returns:
        dict with gross_wages, federal_tax, state_tax,
        social_security_tax, medicare_tax, pretax_deductions,
        posttax_deductions, total_pretax, total_posttax,
        net_pay_total.  mortgage_interest_total is added by the
        caller after computing Section 2.
    """
    if not periods or not salary_profiles:
        return _empty_income_tax()

    # Accumulate totals across all profiles and periods.
    totals = {k: ZERO for k in (
        "gross", "federal", "state", "ss", "medicare", "net",
    )}
    pretax_by_name: dict[str, Decimal] = {}
    posttax_by_name: dict[str, Decimal] = {}

    for profile in salary_profiles:
        breakdowns = _compute_profile_breakdowns(
            user_id, year, profile, periods,
        )
        for bd in breakdowns:
            totals["gross"] += bd.earnings.gross_biweekly
            totals["federal"] += bd.taxes.federal
            totals["state"] += bd.taxes.state
            totals["ss"] += bd.taxes.social_security
            totals["medicare"] += bd.taxes.medicare
            totals["net"] += bd.earnings.net_pay

            for ded in bd.deductions.pre_tax:
                pretax_by_name[ded.name] = (
                    pretax_by_name.get(ded.name, ZERO) + ded.amount
                )
            for ded in bd.deductions.post_tax:
                posttax_by_name[ded.name] = (
                    posttax_by_name.get(ded.name, ZERO) + ded.amount
                )

    return _assemble_income_result(
        totals, pretax_by_name, posttax_by_name,
    )


def _compute_profile_breakdowns(
    user_id: int, year: int, profile: SalaryProfile, periods: list,
) -> list:
    """Run the paycheck calculator for one profile across all periods.

    Loads tax configs for the target year (current-year fallback when the
    target year has no configs) via the shared ``load_tax_configs_for_year``
    SSOT (DH-#30).

    Args:
        user_id: User ID for tax config lookup.
        year: Target calendar year.
        profile: SalaryProfile with loaded raises and deductions.
        periods: Pay periods in the target year.

    Returns:
        List of PaycheckBreakdown from project_salary.
    """
    # Single target year: every period in ``periods`` is in ``year``, so a
    # single config set is correct.  The per-year + current-year fallback
    # rule is owned by load_tax_configs_for_year, the SSOT shared with the
    # recurrence engine and the salary projection (DH-#30).
    tax_configs = load_tax_configs_for_year(user_id, profile, year)

    return paycheck_calculator.project_salary(
        profile, periods, tax_configs,
    )


def _assemble_income_result(
    totals: dict, pretax_by_name: dict, posttax_by_name: dict,
) -> dict:
    """Build the income_tax section dict from accumulated totals.

    Args:
        totals: dict mapping short keys to Decimal sums.
        pretax_by_name: deduction name -> annual total.
        posttax_by_name: deduction name -> annual total.

    Returns:
        Fully structured income_tax section dict.
    """
    pretax_list = [
        {"name": k, "annual_total": v}
        for k, v in sorted(pretax_by_name.items())
    ]
    posttax_list = [
        {"name": k, "annual_total": v}
        for k, v in sorted(posttax_by_name.items())
    ]

    return {
        "gross_wages": totals["gross"],
        "federal_tax": totals["federal"],
        "state_tax": totals["state"],
        "social_security_tax": totals["ss"],
        "medicare_tax": totals["medicare"],
        "pretax_deductions": pretax_list,
        "posttax_deductions": posttax_list,
        "total_pretax": sum(
            (d["annual_total"] for d in pretax_list), ZERO,
        ),
        "total_posttax": sum(
            (d["annual_total"] for d in posttax_list), ZERO,
        ),
        "net_pay_total": totals["net"],
    }


def _compute_mortgage_interest(
    year: int,
    debt_schedules: dict[int, DebtSchedule],
    scenario_id: int,
) -> Decimal:
    """Sum mortgage/loan interest PAID during the calendar year.

    Schedule A (itemized deductions) reports the interest a loan's payments
    actually paid during the year, so accuracy is critical.  Per loan this is a
    HYBRID of ledger-actual and schedule-projected interest
    (:func:`_loan_year_interest`): the ACTUAL interest of confirmed (settled)
    payments comes from the genesis ledger -- correct even for an off-schedule
    (extra / short) payment, where the amortization schedule's replayed figure is
    not -- while the PROJECTED interest of the year's not-yet-confirmed payments
    comes from the schedule.  A loan the ledger does not own (no genesis opening
    posting) falls back to the schedule alone, unchanged from before the read
    switch.

    Args:
        year: Calendar year to sum interest for.
        debt_schedules: loan account_id ->
            :class:`~app.services.net_worth_kernel.DebtSchedule` mapping
            from _generate_debt_schedules().
        scenario_id: The budget scenario the schedules were generated in; scopes
            the ledger read to the same scenario.

    Returns:
        Total interest paid across all loan accounts in the year.
    """
    return sum(
        (
            _loan_year_interest(loan_account_id, debt, scenario_id, year)
            for loan_account_id, debt in debt_schedules.items()
        ),
        ZERO,
    )


def _loan_year_interest(
    loan_account_id: int,
    debt: DebtSchedule,
    scenario_id: int,
    year: int,
) -> Decimal:
    """Return one loan's interest PAID during *year* (ledger-actual + projected).

    The per-loan hybrid behind :func:`_compute_mortgage_interest`:

    * the ACTUAL interest of confirmed payments comes from the genesis ledger
      (:func:`app.services.loan_posting_service.confirmed_loan_interest_in_year`),
      attributed to each payment's civil paid date -- the tax-correct basis, and
      correct for off-schedule payments where the schedule's replayed interest is
      not; PLUS
    * the schedule's PROJECTED interest for the year's genuinely projected rows:
      not replay-confirmed (``not row.is_confirmed``) AND not occupying a due
      slot a SETTLED payment already holds
      (:func:`app.services.loan_loaders.load_settled_payment_due_months` -- an
      early-settled payment's interest is already in the ledger term, so its
      still-``is_confirmed=False`` schedule row must not count again).

    When the loan has no genesis opening posting (an un-backfilled loan, or one
    the ledger does not own) the reader returns ``None`` and this sums the FULL
    schedule (confirmed history + projection) by ``payment_date`` -- byte-identical
    to the pre-read-switch behaviour.

    Args:
        loan_account_id: The loan account whose paid interest to compute.
        debt: The loan's :class:`~app.services.net_worth_kernel.DebtSchedule`
            (its amortization schedule).
        scenario_id: The budget scenario to scope the ledger read to.
        year: The calendar year to sum interest for.

    Returns:
        The loan's interest paid during *year* as a ``Decimal``.
    """
    confirmed = confirmed_loan_interest_in_year(
        loan_account_id, scenario_id, year,
    )
    if confirmed is None:
        # No genesis authority: sum the FULL schedule (confirmed history +
        # projection), byte-identical to the pre-read-switch behaviour.
        return sum(
            (
                row.interest for row in debt.schedule
                if row.payment_date.year == year
            ),
            ZERO,
        )
    # Ledger-actual (confirmed) + schedule-projected.  The projected term
    # excludes a row when EITHER the replay confirmed it (``is_confirmed`` --
    # its interest is the ledger's actual figure) OR its due slot is occupied
    # by a SETTLED payment the replay has not confirmed yet: an early-settled
    # payment (settled before its pay period begins) already posted its actual
    # interest at its paid date, while its schedule row stays
    # ``is_confirmed=False`` -- counting that row too would double-count the
    # slot.  The partition rule is "a slot is projected iff no settled payment
    # occupies it" (the 2026-07-02 adversarial review's R1 companion fix).
    settled_due_months = loan_loaders.load_settled_payment_due_months(
        loan_account_id, scenario_id,
    )
    projected = sum(
        (
            row.interest for row in debt.schedule
            if not row.is_confirmed
            and row.payment_date.year == year
            and (row.payment_date.year, row.payment_date.month)
            not in settled_due_months
        ),
        ZERO,
    )
    return confirmed + projected


def _empty_income_tax() -> dict:
    """Return an income/tax section with all zeros."""
    return {
        "gross_wages": ZERO,
        "federal_tax": ZERO,
        "state_tax": ZERO,
        "social_security_tax": ZERO,
        "medicare_tax": ZERO,
        "pretax_deductions": [],
        "posttax_deductions": [],
        "total_pretax": ZERO,
        "total_posttax": ZERO,
        "net_pay_total": ZERO,
        "mortgage_interest_total": ZERO,
    }
