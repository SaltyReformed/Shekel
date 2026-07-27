"""Balance-at-T seam -- the modelled CONTRIBUTION tier of the asset replay.

Plan step **X-g2a** (``docs/audits/balance_architecture/README.md`` Section 3.2).
Split out of :mod:`app.services.balance_at._asset_fold` at its module-size
ceiling, on the cohesion line plan step D1c drew for ``cash_ledger``: that module
answers "what is this asset worth on each day", and this one answers the one
question it has to ask a second package to resolve -- "what does this account's
payroll put IN, and when".

**Ruling R-R partitions a contribution by SOURCE**, and this module is the
modelled half of that partition.  A recorded transfer HAS a transaction row, so
it is already an ACTUAL / PLANNED event in the cash fold underneath the replay; a
payroll deduction never has one, so it is the modelled CONTRIBUTION event
:func:`contribution_events` dates.  The two feeds are therefore disjoint BY
CONSTRUCTION and there is no de-dup rule to get wrong -- which is why nothing
here reads ``investment_projection._average_transfer_contribution``, the scalar
that folds both feeds into one number and so makes them indistinguishable.  The
recorded feed IS read here, for the two things a cash delta cannot say: how much
of the calendar year's contribution limit is already consumed, and what employee
total the employer match sizes off (ruling R-R consequence (a)).

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.services import growth_engine, pay_period_service
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.investment_projection import (
    adapt_deductions,
    deduction_contribution_per_period,
    employer_contribution_params,
)
from app.services.loan_loaders import query_shadow_income

_ZERO = Decimal("0")


@dataclass(frozen=True)
class ContributionInputs:
    """One account's modelled-contribution feed, as the caller batch-loaded it.

    The three per-account projection inputs the replay needs to model a
    contribution, bundled because a cohesive named concept finally emerged for
    them (plan step X-g2a).  Plan step X-g1 deliberately did NOT bundle them --
    it had one caller, and its own docstring recorded that re-wrapping three
    independent inputs "in a bundle no other caller shares would be the stamp
    coupling the standards reject".  X-g2b is what changes that: **SEVEN readers
    hand them over there, and THREE of the seven hand nothing** -- the two
    interest accessors and the grid, none of which can have a contribution feed.
    Spelling "nothing" as three separate literals at each of those sites is the
    argument-a-caller-can-get-wrong shape the plan's Section 8 rules a defect
    rather than a contract; :meth:`absent` makes it one token that cannot be
    half-written.

    They stay a caller's argument rather than something this module loads,
    deliberately: the seam batch-loads them ONCE per account SET
    (``_inputs._assemble_inputs`` -- one investment-params query, one deductions
    query, one raise-aware gross fetch), and loading them per account here would
    reinstate the N+1 that assembly exists to avoid.

    Attributes:
        investment_params: The account's
            :class:`~app.models.investment_params.InvestmentParams`, or ``None``
            when it is not a parameterized investment account.  It also carries
            the assumed return the ACCRUAL tier reads, which is why the replay
            takes this bundle rather than the deduction feed alone.
        deductions: The account's active paycheck deductions (adapted here).
        salary_gross_biweekly: The raise-aware engine gross per pay period -- the
            employer-match cap basis, and the fallback gross when no deduction
            supplies one.
    """

    investment_params: InvestmentParams | None = None
    deductions: list = field(default_factory=list)
    salary_gross_biweekly: Decimal = _ZERO

    @classmethod
    def absent(cls) -> "ContributionInputs":
        """Return the inputs of an account that models NO contribution feed.

        The explicit token for "this reader cannot have contributions": an
        INTEREST account (no ``InvestmentParams``, so no rate to contribute
        against), a Property, and the grid's kind-blind cash-flow view.  Named
        rather than spelled out so the three call sites that mean it say so, and
        so a reader never has to decide whether an empty deduction list beside a
        zero gross was meant or forgotten.

        Returns:
            The empty :class:`ContributionInputs`.
        """
        return cls()


@dataclass(frozen=True)
class _ContributionPlan:
    """What an INVESTMENT account's modelled contributions are made of.

    A cohesive assembly record (:func:`_plan_for`): the modelled per-period
    employee amount, the employer configuration, the annual limit, and the
    RECORDED contributions per pay period -- which are read for the limit and the
    match base and never contributed again (ruling R-R).

    Attributes:
        per_period: The employee contribution one pay period's paycheck
            deductions produce, each already throttled to its own calendar-year
            cap (:func:`~app.services.investment_projection.deduction_contribution_per_period`).
        employer_params: The employer-contribution configuration
            (:func:`~app.services.investment_projection.employer_contribution_params`),
            or ``None`` when the account has none.
        annual_limit: The account's annual employee-contribution ceiling, or
            ``None`` for an account with no IRS limit.
        recorded_by_period: pay_period_id -> the ``effective_amount`` sum of the
            transfer-linked contributions actually recorded in that period.
    """

    per_period: Decimal
    employer_params: dict | None
    annual_limit: Decimal | None
    recorded_by_period: dict[int, Decimal]


def _recorded_contributions(
    account_id: int, scenario_id: int,
) -> dict[int, Decimal]:
    """Return the transfer-linked contributions recorded per pay period.

    The RECORDED half of ruling R-R's partition, loaded through the shared
    :func:`~app.services.loan_loaders.query_shadow_income` -- the app's one
    definition of "a contribution into this account" (a transfer's income-leg
    shadow, excluding soft-deleted and balance-excluded rows), which the YTD
    and limit accounting already read.

    These rows are NOT contributed by this module: they are ordinary ACTUAL /
    PLANNED events in the cash fold underneath it, which is exactly why the
    partition needs no de-dup rule.  They are read here for the two things the
    fold cannot know from a cash delta alone -- how much of the year's
    contribution limit is already consumed, and what employee total the employer
    match sizes off (ruling R-R consequence (a)).

    Unwindowed by pay period, deliberately: the limit is a CALENDAR-YEAR
    recurrence, so a windowed feed would under-count the year a projection
    starts in.  It is windowed by account and scenario, which is the whole
    domain.

    Args:
        account_id: The account receiving the contributions.
        scenario_id: The budget scenario the rows live in.

    Returns:
        ``{pay_period_id: total}`` over the rows' ``effective_amount`` -- the
        realized actual for a settled shadow, else its estimate.  ``{}`` for an
        account with none.
    """
    totals: dict[int, Decimal] = {}
    for txn in query_shadow_income(account_id, scenario_id).all():
        amount = Decimal(str(txn.effective_amount))
        totals[txn.pay_period_id] = (
            totals.get(txn.pay_period_id, _ZERO) + amount
        )
    return totals


def _plan_for(
    account: Account, scenario_id: int, inputs: ContributionInputs,
) -> "_ContributionPlan | None":
    """Assemble *account*'s modelled contribution plan, or ``None`` if it has none.

    ``None`` when nothing is modelled at all -- no deduction produces a positive
    amount AND no employer contribution is configured -- which is what keeps a
    plain IRA from paying for a period-calendar load it has no use for.  Note
    that an employer FLAT percentage models money with a zero employee feed (the
    real Empower 401(k) shape: 5% of $3,631.74 = $181.59 a period), so the test
    is on both halves, not on the employee amount alone.

    Args:
        account: The investment account.
        scenario_id: The budget scenario the recorded contributions live in.
        inputs: The account's :class:`ContributionInputs`.  Its
            ``investment_params`` is not ``None`` -- :func:`contribution_events`
            guards that.

    Returns:
        The :class:`_ContributionPlan`, or ``None``.
    """
    per_period, gross_biweekly = deduction_contribution_per_period(
        adapt_deductions(inputs.deductions), inputs.salary_gross_biweekly,
    )
    employer_params = employer_contribution_params(
        inputs.investment_params, gross_biweekly,
    )
    if per_period <= _ZERO and employer_params is None:
        return None
    return _ContributionPlan(
        per_period=per_period,
        employer_params=employer_params,
        annual_limit=inputs.investment_params.annual_contribution_limit,
        recorded_by_period=_recorded_contributions(account.id, scenario_id),
    )


def contribution_events(
    account: Account,
    scenario_id: int,
    inputs: ContributionInputs,
    accrual_start: date,
) -> "list[tuple[date, Decimal]]":
    """Return *account*'s modelled CONTRIBUTION events, dated.

    The tier's ONE entry, so the replay asks "what does this account's payroll
    put in, and when" and never has to know that answering it needs the user's
    whole pay-period calendar, the recorded-contribution feed, or the annual
    limit's year boundary.

    Empty -- with no query issued and no calendar loaded -- for every account
    that cannot have a modelled feed: any kind but INVESTMENT (an INTEREST
    account's payroll does not fund it, and a Property's certainly does not),
    and an INVESTMENT with no :class:`~app.models.investment_params.InvestmentParams`
    to carry the limit and the employer configuration.

    Args:
        account: The account to resolve.  Its ``account_type`` drives the
            classifier and its ``user_id`` the period calendar.
        scenario_id: The budget scenario the recorded contributions live in.
        inputs: The account's :class:`ContributionInputs`.
        accrual_start: The latest assertion's UTC civil day -- the boundary
            :func:`_dated_events` admits an event strictly after (ruling R-Z).

    Returns:
        ``[(payday, amount), ...]`` in period order, one entry per period that
        contributes a non-zero amount.  ``[]`` when the account models none.
    """
    if (classify_account(account) is not AccountProjectionKind.INVESTMENT
            or inputs.investment_params is None):
        return []
    plan = _plan_for(account, scenario_id, inputs)
    if plan is None:
        return []
    return _dated_events(
        plan,
        pay_period_service.get_all_periods(account.user_id),
        accrual_start,
    )


def _dated_events(
    plan: _ContributionPlan,
    periods: "list[PayPeriod]",
    accrual_start: date,
) -> "list[tuple[date, Decimal]]":
    """Resolve the plan into dated events, one per paying period.

    **A contribution lands on its pay period's ``start_date``, because that is
    the payday** -- the :class:`~app.models.pay_period.PayPeriod` model says so
    in its own docstring ("start_date (payday)"), and it is already the date
    ``investment_projection.build_contribution_timeline`` stamps on every
    ``ContributionRecord``.  So the money is in the account from the payday and
    earns a full period of return, where the growth engine adds a period's
    contribution AFTER its growth and so earns none in its own period.

    **It stops at the latest assertion, and the boundary is STRICT** (ruling
    R-Z): an event exists only when ``payday > accrual_start``.  A contribution
    on a payday at or before the assertion is money the asserted balance already
    contains, and modelling it again double counts -- an over-count that looks
    exactly like real growth and so cannot be detected later.  The ACCRUAL rule
    beside it is inclusive (``>=``) for a reason that does not transfer: a day
    count has to tile the calendar with no gap, while a contribution is a
    discrete event that either is or is not inside the assertion.

    **The annual limit is a calendar-year recurrence over BOTH feeds.**  Every
    period's RECORDED contributions consume the year's limit whether or not the
    period is modelled -- they happened, so they are never capped or dropped --
    and the modelled amount is then capped against what is left, through the
    same :func:`~app.services.growth_engine.cap_contribution_at_limit` the
    growth engine applies.  The employer amount is sized off the RESOLVED
    employee total for the period, recorded plus modelled (ruling R-R
    consequence (a)), and is not itself charged against the employee limit --
    the growth engine's own rule.

    Args:
        plan: The account's :class:`_ContributionPlan`.
        periods: The user's pay periods, CHRONOLOGICAL (ordered by
            ``period_index``), and the whole calendar rather than a caller's
            window -- the year-boundary reset and the limit accounting are
            wrong over a slice.
        accrual_start: The latest assertion's UTC civil day.

    Returns:
        ``[(payday, amount), ...]`` in period order, one entry per period that
        contributes a non-zero amount.
    """
    events: "list[tuple[date, Decimal]]" = []
    ytd = _ZERO
    prev_year: int | None = None
    for period in periods:
        period_year = period.start_date.year
        if prev_year is not None and period_year != prev_year:
            ytd = _ZERO
        prev_year = period_year

        recorded = plan.recorded_by_period.get(period.id, _ZERO)
        ytd += recorded
        if period.start_date <= accrual_start:
            continue

        employee = growth_engine.cap_contribution_at_limit(
            plan.per_period, plan.annual_limit, ytd,
        )
        employer = growth_engine.calculate_employer_contribution(
            plan.employer_params, recorded + employee,
        )
        ytd += employee
        amount = employee + employer
        if amount != _ZERO:
            events.append((period.start_date, amount))
    return events
