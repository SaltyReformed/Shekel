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
from app.services.cash_ledger import ReconciledThrough, contributions_by_id
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
    coupling the standards reject".  X-g2b is what changed that: seven readers
    hand them over there, several of which cannot have a contribution feed at
    all.  Spelling "nothing" as three separate literals at each such site is the
    argument-a-caller-can-get-wrong shape the plan's Section 8 rules a defect
    rather than a contract; :meth:`absent` makes it one token that cannot be
    half-written.

    They stay a caller's argument rather than something this module loads,
    deliberately: the seam batch-loads them ONCE per account SET
    (``_inputs._contribution_inputs_for_accounts`` -- one investment-params
    query, one deductions query, one raise-aware gross fetch), and loading them
    per account here would reinstate the N+1 that loader exists to avoid.

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

        The explicit token for "this reader cannot have contributions".  Named
        rather than spelled out so a call site that means it says so, and so a
        reader never has to decide whether an empty deduction list beside a zero
        gross was meant or forgotten.

        **Its production audience is down to ONE** -- the modelled-return
        accessor in :mod:`._kernel` -- because plan step X-g3b deleted the
        grid's kind gate and the grid now loads a REAL feed
        (:func:`._inputs._contribution_inputs_for_account`).  The constructor
        stays: it is what a reader that genuinely has no feed should say, and
        the tests that build a bundle by hand use it.

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
        recorded_by_period: pay_period_id -> the summed CONTRIBUTION of the
            transfer-linked rows actually recorded in that period.
    """

    per_period: Decimal
    employer_params: dict | None
    annual_limit: Decimal | None
    recorded_by_period: dict[int, Decimal]


def _recorded_contributions(
    user_id: int, account_id: int, scenario_id: int,
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

    **Every row is priced through the amount model** (plan step X-au-c2), which
    is what makes this feed survive the transfer cutover: a contribution shadow
    is a transfer shadow, so plan step X-au-f declares it derived and its own
    amount column goes empty.  The basis is built over the whole feed at once --
    neither live producer has a candidate among these rows (the salary half
    wants a template link, the loan half a loan-payment settings row), so it
    costs two list comprehensions and no query.

    Args:
        user_id: The owner; scopes the amount basis.
        account_id: The account receiving the contributions.
        scenario_id: The budget scenario the rows live in.

    Returns:
        ``{pay_period_id: total}`` over what each row CONTRIBUTES -- the
        realized actual for a settled shadow, else its resolved amount.  ``{}``
        for an account with none.
    """
    rows = query_shadow_income(account_id, scenario_id).all()
    contributions = contributions_by_id(user_id, scenario_id, rows)
    totals: dict[int, Decimal] = {}
    for txn in rows:
        totals[txn.pay_period_id] = (
            totals.get(txn.pay_period_id, _ZERO) + contributions[txn.id]
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
        recorded_by_period=_recorded_contributions(
            account.user_id, account.id, scenario_id,
        ),
    )


def contribution_events(
    account: Account,
    scenario_id: int,
    inputs: ContributionInputs,
    reconciled_through: ReconciledThrough,
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
        reconciled_through: The account's coverage boundary -- the assertion
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
    # Ordered by the PAYDAY, explicitly, rather than by whatever
    # ``get_all_periods`` happens to order by (the stored ``period_index``).
    # :func:`_dated_events` accumulates a calendar year's contributions in
    # iteration order and resets on a year change, so the order decides which
    # periods the annual limit caps -- and index order and date order are two
    # different things this arc exists because nothing reconciles
    # (``PayCalendar.filing_period`` measures them parting company on 800 of
    # 872 probed days).  Stating the order here makes the consumer's
    # precondition true instead of inherited.
    return _dated_events(
        plan,
        sorted(
            pay_period_service.get_all_periods(account.user_id),
            key=lambda period: period.start_date,
        ),
        reconciled_through,
    )


def _dated_events(
    plan: _ContributionPlan,
    periods: "list[PayPeriod]",
    reconciled_through: ReconciledThrough,
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
    R-Z): an event exists only when the assertion does NOT already reconcile
    the payday.  A contribution on a payday at or before the assertion is money
    the asserted balance already contains, and modelling it again double counts
    -- an over-count that looks exactly like real growth and so cannot be
    detected later.

    **That is ruling R-DH's question, so it is ruling R-DH's implementation**
    (:meth:`app.services.cash_ledger.ReconciledThrough.covers`).  It was a bare
    ``period.start_date <= accrual_start`` until the one-partition step, which
    an adversarial review caught: the same rule, the same units and the same
    inclusivity as the cash walks, reached as a loose date and therefore
    invisible to the census that step ran.  Re-rule the boundary and this feed
    would silently have stayed put while every cash consumer moved.

    The ACCRUAL rule beside it is inclusive at its own start for a reason that
    does NOT transfer and is deliberately NOT routed here: a day count has to
    tile the calendar with no gap, while a contribution is a discrete event
    that either is or is not inside the assertion.

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
        periods: The user's pay periods, CHRONOLOGICAL -- ordered by
            ``start_date``, the PAYDAY, which is the only fact in the row.
            **This read "ordered by ``period_index``" until plan step C2-c**,
            an equation nothing in the schema enforces and which
            ``PayCalendar.filing_period`` measures parting company with date
            order on 800 of 872 probed days; the caller sorts explicitly now.
            The whole calendar rather than a caller's window, too -- the
            year-boundary reset and the limit accounting are wrong over a
            slice.
        reconciled_through: The account's coverage boundary -- the latest
            assertion, as the rule that decides what it already contains.

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
        if reconciled_through.covers(period.start_date):
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
