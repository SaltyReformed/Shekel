"""
Shekel Budget App -- Account Projection Dispatcher (MED-01 / S6-03 / S6-04)

One flag-driven classification of which projection engine drives an
account's period balances, consumed by every dashboard that branches
on account type.  Collapses the two divergent branch ladders that
lived in :func:`savings_dashboard_service._compute_account_projections`
and :func:`year_end_summary_service._get_account_balance_map`
(S6-03 in ``docs/audits/financial_calculations/06_dry_solid.md``)
into a single classifier whose flag-driven order is the project's
canonical answer to "which engine for this account?".  Also centralises
the payroll-deduction funding decision that previously lived as a
hardcoded enum-frozenset literal in ``app/routes/investment.py`` (S6-04;
see :func:`is_payroll_deduction_funded` for the replacement).

Pure functions over Account / AccountType / int.  No Flask imports
(the service-boundary rule from ``CLAUDE.md``).  Caller-supplied
``ref_cache_module`` keeps this module free of circular import
worries with :mod:`app.ref_cache` while preserving the IDs-for-logic
standard (``docs/coding-standards.md:174-178``).
"""

from enum import Enum

from app.enums import AcctTypeEnum


class AccountProjectionKind(Enum):
    """Which projection engine drives an account's period balances.

    The order reflects the canonical precedence the dual dispatchers
    expressed inconsistently pre-Commit-28 (S6-03):

    1. :data:`AMORTIZING` -- loan amortization engine
       (:func:`app.services.amortization_engine.generate_schedule`,
       fed by :func:`app.services.loan_resolver.resolve_loan`).
    2. :data:`INTEREST` -- interest projection layered over the
       balance calculator
       (:func:`app.services.balance_calculator.calculate_balances_with_interest`).
    3. :data:`INVESTMENT` -- growth engine
       (:func:`app.services.growth_engine.project_balance`).
    4. :data:`PLAIN` -- the generic entries-aware producer
       (:func:`app.services.balance_resolver.balances_for`).
    """

    AMORTIZING = "amortizing"
    INTEREST = "interest"
    INVESTMENT = "investment"
    PLAIN = "plain"


def classify_account(account) -> AccountProjectionKind:
    """Return the :class:`AccountProjectionKind` for *account*.

    Branches solely on the boolean columns on the linked
    :class:`~app.models.ref.AccountType`
    (``has_amortization`` / ``has_interest`` / ``has_parameters``):
    no enum-name comparisons, no name strings -- consistent with the
    IDs-for-logic standard.  An account with no ``account_type``
    (degenerate / partially loaded) classifies as :data:`PLAIN`
    so the canonical balance resolver still produces a sensible
    output rather than the caller raising on ``None.has_amortization``.

    The order matters: an :class:`~app.models.ref.AccountType` for
    which both ``has_amortization`` and ``has_interest`` are True
    (no such row exists in the seed catalog today, but the schema
    permits it) classifies as :data:`AMORTIZING` because the
    amortization engine consumes the schedule and the interest
    calculator's layered interest accrual is irrelevant for a
    liability balance.

    Args:
        account: An :class:`~app.models.account.Account` with its
            ``account_type`` relationship eager-loaded (the consumer
            is expected to ``joinedload`` it; the classifier does
            not issue queries).

    Returns:
        The :class:`AccountProjectionKind` for this account.
    """
    acct_type = account.account_type
    if acct_type is None:
        return AccountProjectionKind.PLAIN
    if acct_type.has_amortization:
        return AccountProjectionKind.AMORTIZING
    if acct_type.has_interest:
        return AccountProjectionKind.INTEREST
    if acct_type.has_parameters:
        return AccountProjectionKind.INVESTMENT
    return AccountProjectionKind.PLAIN


# Payroll-deduction-funded account types.  The schema does not
# currently carry a metadata flag for "this account type is funded
# by employer payroll deduction" (S6-04 in ``06_dry_solid.md``
# records this as report-only -- the audit explicitly does not invent
# the flag).  The enum tuple is the single source of this decision:
# when a new employer-sponsored type is added (403(b), Roth 403(b),
# TSP, SIMPLE IRA), extend this tuple or, ideally, replace the helper
# with a schema flag introduced by a follow-up migration.  Either
# change touches one site.
_PAYROLL_DEDUCTION_FUNDED_TYPES = (
    AcctTypeEnum.K401,
    AcctTypeEnum.ROTH_401K,
)


def is_payroll_deduction_funded(
    account_type_id: int,
    ref_cache_module,
) -> bool:
    """Return True iff *account_type_id* designates a payroll-funded type.

    Used by the investment dashboard to choose between the
    employer-sponsored-plan prompt (link to the salary profile's
    deductions tab) and the individual-contribution prompt (create a
    recurring transfer).  Pre-Commit-28 the decision lived as a
    hardcoded enum-frozenset literal in
    ``app/routes/investment.py:60`` enumerating K401 and ROTH_401K;
    centralising it here closes the OCP smell S6-04 names (a new
    payroll-funded type required editing the route, not just adding
    a seed row).

    Args:
        account_type_id: The account's ``account_type_id``.
        ref_cache_module: The :mod:`app.ref_cache` module
            (parameter-injected to avoid an import cycle between
            this service and the cache layer -- both are imported by
            multiple route layers).

    Returns:
        True when the type is in the project's payroll-deduction
        catalog, False otherwise.
    """
    funded_ids = {
        ref_cache_module.acct_type_id(t)
        for t in _PAYROLL_DEDUCTION_FUNDED_TYPES
    }
    return account_type_id in funded_ids
