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

from collections import OrderedDict
from datetime import date
from decimal import Decimal
from enum import Enum

from app.enums import AcctTypeEnum


class AccountProjectionKind(Enum):
    """Which projection engine drives an account's period balances.

    The order reflects the canonical precedence the dual dispatchers
    expressed inconsistently pre-Commit-28 (S6-03):

    1. :data:`AMORTIZING` -- loan amortization engine
       (:func:`app.services.rate_period_engine.replay_schedule`
       + :func:`app.services.amortization_engine.project_forward`, fed
       by :func:`app.services.loan_resolver.resolve_loan` via the
       :func:`app.services.loan_resolver.compute_payoff_scenarios`
       composer).
    2. :data:`INTEREST` -- interest projection layered over the
       balance calculator
       (:func:`app.services.balance_calculator.calculate_balances_with_interest`).
    3. :data:`APPRECIATING` -- the growth engine run as pure compound
       appreciation with no contributions
       (:func:`app.services.growth_engine.project_balance`, fed by
       :class:`~app.models.asset_appreciation_params.AssetAppreciationParams`).
       A physical asset (Property) whose user-set market value compounds
       forward; pre-anchor periods hold the value flat (a manually-set
       valuation is not back-cast).  Checked BEFORE :data:`INVESTMENT`
       because a Property carries ``has_parameters=True`` too.
    4. :data:`INVESTMENT` -- growth engine
       (:func:`app.services.growth_engine.project_balance`).
    5. :data:`PLAIN` -- the generic entries-aware producer
       (:func:`app.services.balance_resolver.balances_for`).
    """

    AMORTIZING = "amortizing"
    INTEREST = "interest"
    APPRECIATING = "appreciating"
    INVESTMENT = "investment"
    PLAIN = "plain"


def classify_account(account) -> AccountProjectionKind:
    """Return the :class:`AccountProjectionKind` for *account*.

    Branches solely on the boolean columns on the linked
    :class:`~app.models.ref.AccountType`
    (``has_amortization`` / ``has_interest`` / ``has_appreciation`` /
    ``has_parameters``): no enum-name comparisons, no name strings --
    consistent with the IDs-for-logic standard.  ``has_appreciation`` is
    checked before ``has_parameters`` so a parameterised physical asset
    (Property) classifies as :data:`APPRECIATING`, not :data:`INVESTMENT`.
    An account with no ``account_type``
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
    if acct_type.has_appreciation:
        return AccountProjectionKind.APPRECIATING
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


def balance_from_schedule_at_date(
    sorted_schedule: list,
    target: date,
    current_balance: Decimal,
) -> Decimal:
    """Remaining balance after the last scheduled payment on or before *target*.

    Walks *sorted_schedule* (chronological by ``payment_date``) and returns
    the ``remaining_balance`` of the latest row whose ``payment_date`` is on
    or before *target*.  When no row qualifies -- *target* precedes the
    schedule's first payment -- returns *current_balance*.

    *current_balance* (the loan's resolver-derived balance as of today), NOT
    its original principal, is the correct pre-schedule value: the resolver
    builds a TODAY-forward schedule from the current balance, so a date before
    the first upcoming payment is simply at today's balance.  Reporting the
    origination amount there made the loan leap down from its original
    principal the moment the first payment landed -- a phantom liability drop,
    and net-worth jump, of (original principal - current balance).

    The shared primitive behind both :func:`compute_loan_period_balance_map`
    (per-period) and the year-end debt-progress section (at Jan 1 / Dec 31),
    so the two cannot drift on how a loan balance is read from a schedule.

    Args:
        sorted_schedule: Non-empty ``AmortizationRow`` list sorted ascending
            by ``payment_date``.
        target: The date to read the balance at.
        current_balance: The loan's resolver-derived current balance, used
            when *target* precedes the first scheduled payment.

    Returns:
        The ``Decimal`` remaining balance at *target*.
    """
    balance = current_balance
    for row in sorted_schedule:
        if row.payment_date <= target:
            balance = row.remaining_balance
        else:
            break
    return balance


def compute_loan_period_balance_map(
    schedule: list,
    periods: list,
    current_balance: Decimal,
) -> "OrderedDict[int, Decimal]":
    """Map an amortization schedule to per-period remaining balances.

    For each ``PayPeriod`` in *periods*, returns the
    :attr:`~app.services.amortization_engine.AmortizationRow.remaining_balance`
    from the last schedule row whose ``payment_date`` is on or before
    ``period.end_date``.  Periods before the schedule's first payment -- and
    every period when the schedule is empty -- return *current_balance*.

    *current_balance* is the loan's resolver-derived balance as of today
    (:attr:`~app.services.loan_resolver.LoanState.current_balance`), NOT its
    original principal.  The resolver's schedule is TODAY-forward, so a period
    before the first upcoming payment is at today's balance; reporting the
    origination amount there made the loan leap down to its real balance when
    the first payment landed -- a phantom liability drop / net-worth jump of
    (original principal - current balance).  An empty schedule (a paid-off or
    fully-resolved loan with no remaining rows) likewise sits at its current
    balance at every period.

    Period-end-keyed is the project's canonical loan-balance derivation as of
    F-21 / Commit 19 of ``remediation_follow_up_plan.md`` -- it answers "what
    does the borrower owe AFTER the payment due in this period?"  The savings
    cockpit and the year-end net-worth / debt-progress sections all route
    through this producer and the shared
    :func:`balance_from_schedule_at_date`, so their loan balances cannot drift.

    Args:
        schedule: List of
            :class:`~app.services.amortization_engine.AmortizationRow`
            sorted chronologically.  An empty schedule returns
            *current_balance* for every period.
        periods: List of :class:`~app.models.pay_period.PayPeriod`
            objects.  Order does not matter; the map keys by
            ``period.id``.
        current_balance: The loan's resolver-derived current balance, the
            pre-first-payment and empty-schedule fallback.

    Returns:
        ``OrderedDict`` mapping ``period.id`` to ``Decimal``
        remaining balance.
    """
    balances: "OrderedDict[int, Decimal]" = OrderedDict()

    if not schedule:
        for period in periods:
            balances[period.id] = current_balance
        return balances

    # Defensive sort -- the resolver emits chronological schedules,
    # but a future caller might assemble one differently.
    sorted_schedule = sorted(schedule, key=lambda r: r.payment_date)

    for period in periods:
        balances[period.id] = balance_from_schedule_at_date(
            sorted_schedule, period.end_date, current_balance,
        )

    return balances


def find_period_containing_date(periods: list, target: date):
    """Return the pay period whose interval contains *target*.

    A period "contains" *target* when
    ``period.start_date <= target <= period.end_date``.  When no
    period contains *target* (the date falls in a gap or beyond the
    user's generated horizon), falls back to the latest period whose
    ``end_date`` is on or before *target*; if none exists either,
    returns ``None``.

    The fallback is the same shape the year-end summary's
    :func:`_find_period_on_or_before_date` uses -- it preserves the
    period-end-keyed semantic when a target date sits just past the
    last generated period (the user's last known balance at the
    horizon is the natural answer).

    Args:
        periods: List of :class:`~app.models.pay_period.PayPeriod`
            objects.
        target: The date to locate.

    Returns:
        The matching :class:`~app.models.pay_period.PayPeriod`, or
        ``None`` when no period precedes *target*.
    """
    containing = None
    fallback = None
    for period in periods:
        if period.start_date <= target <= period.end_date:
            if containing is None or period.period_index > containing.period_index:
                containing = period
        elif period.end_date < target:
            if fallback is None or period.period_index > fallback.period_index:
                fallback = period
    return containing if containing is not None else fallback
