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

    The shared primitive behind :func:`_projected_owed_at`, and therefore behind
    the forward projection :func:`forward_balance_at_date` -- the one place a loan
    balance is read off a schedule (the per-period forward map that once shared it
    retired at plan step C3b3, replaced by the seam's positions()-based map).

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


ZERO_MONEY = Decimal("0.00")


def _forward_rows(schedule: list) -> list:
    """Return the schedule's UNCONFIRMED rows, chronological.

    The row set the forward projection :func:`forward_balance_at_date` projects
    over.  It was extracted as a shared primitive when a per-period forward map
    projected over the same rows, so the two could not drift on which rows count
    as "still to come" -- the structural lesson of the scalar/map divergence
    recorded at
    ``docs/audits/balance_architecture/implementation_plan_fail_loud_ledger_authority.md``
    Section 2a.  That map retired at plan step C3b3 (the seam's
    positions()-based map replaced it); this stays the forward projection's one
    definition of the unconfirmed row set.

    Args:
        schedule: The resolver's :class:`AmortizationRow` list (confirmed
            history rows plus committed forward rows).

    Returns:
        The unconfirmed rows, sorted ascending by ``payment_date``.
    """
    return sorted(
        (row for row in schedule if not row.is_confirmed),
        key=lambda row: row.payment_date,
    )


def _projected_owed_at(
    forward_rows: list,
    target: date,
    seed: Decimal,
    owed_from: date,
) -> Decimal:
    """Return the projected balance at *target* -- ``0.00`` before *owed_from*.

    THE one place the origination rule is expressed:

        **A loan owes nothing before it originates.**

    The forward projection :func:`forward_balance_at_date` routes through this,
    and so does the per-period map that samples it, so neither can answer the
    origination question differently (see :func:`_forward_rows`).

    *owed_from* is the loan's ``origination_date``.

    **Why this cannot move a live loan's number, stated precisely.**  For a loan
    that has ORIGINATED, this guard never fires.  The seam's
    :func:`app.services.balance_at.positions` routes a date to the forward
    projection only when it is AFTER ``ctx.as_of`` -- a past date reads the fold
    -- and for an originated loan ``ctx.as_of >= owed_from``, so every date this
    sees satisfies ``target > ctx.as_of >= owed_from``.  The guard therefore only
    CHANGES an answer for a loan configured BEFORE it closes (a mortgage closing
    next month), whose whole timeline ``positions`` routes forward because none of
    it has happened yet: a pre-origination date then correctly reports ``0.00``
    where the pre-fix code reported the loan's full principal at every pay period
    back to the beginning of the user's history.

    Args:
        forward_rows: The unconfirmed rows from :func:`_forward_rows`.
        target: The date to value the loan at.
        seed: The balance in effect before the first row -- the loan's
            ledger-confirmed present, or (for a loan that has not originated) the
            balance it will OPEN at.  See
            :attr:`~app.services.net_worth_kernel.DebtSchedule.projection_seed`.
        owed_from: The loan's ``origination_date``.  Before it the loan does not
            exist and owes ``0.00``.

    Returns:
        The projected ``Decimal`` balance owed at *target*.
    """
    if target < owed_from:
        return ZERO_MONEY
    return balance_from_schedule_at_date(forward_rows, target, seed)


def forward_balance_at_date(
    schedule: list,
    target: date,
    current_balance: Decimal,
    owed_from: date,
) -> Decimal:
    """Return a loan's PROJECTED balance at a future date.

    *current_balance* -- the confirmed present, which the read switch seeds from
    the genesis ledger -- reduced by the scheduled payments still TO COME by
    *target*.  ``0.00`` before *owed_from* (the loan's origination): it does not
    exist yet, so it owes nothing (:func:`_projected_owed_at`).

    Walks ONLY the schedule's UNCONFIRMED rows, and that exclusion is
    the whole point:

    * A confirmed row's paydown is ALREADY inside *current_balance* (the ledger
      summed it), so the row is not a future event.  Its ``remaining_balance`` is
      a HISTORICAL balance, and reading it for a future date reports whatever the
      loan owed back then.
    * The confirmed rows are also an INCOMPLETE record of the past.  They are
      payment rows only -- a balance true-up is a ledger event with no schedule
      row -- so a loan trued-up after its last payment has a last confirmed row
      whose balance is stale by the true-up.  Walking it reported a balance the
      loan does not owe (a real $3.94 divergence on production data), while the
      ledger, which books the true-up, was right.
    * A confirmed row's ``payment_date`` is its INSTALLMENT date, which for an
      early- or late-settled payment need not sit on the same side of *target*
      as the cash did.

    The past therefore belongs to the ledger
    (:func:`app.services.loan_posting_service.confirmed_loan_balance_at`) and the
    future to this projection; no consumer should derive one from the other.  An
    OVERDUE payment (unconfirmed, already past due) stays in the walk, preserving
    the project's due-basis treatment of it -- a known defect, recorded as FU-7
    (it pays down installments that were never paid; see the plan).

    Args:
        schedule: The resolver's :class:`AmortizationRow` list (confirmed
            history rows plus committed forward rows).
        target: The future date to value the loan at.
        current_balance: The projection's SEED -- the balance in effect before
            the first unconfirmed row.  For a loan that has originated this is
            its ledger-confirmed balance today; for one that has not, the balance
            it will OPEN at (the two differ, which is why the seam names the field
            :attr:`~app.services.net_worth_kernel.DebtSchedule.projection_seed`
            rather than "current balance").
        owed_from: The loan's ``origination_date``.  Before it the loan does not
            exist and owes ``0.00`` (:func:`_projected_owed_at`).

    Returns:
        The projected ``Decimal`` balance owed at *target*.
    """
    return _projected_owed_at(
        _forward_rows(schedule), target, current_balance, owed_from,
    )


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
