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

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.services import growth_engine
from app.services.cash_ledger import (
    AmountBasis,
    ReconciledThrough,
    contributions_by_id,
)
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
from app.services.pay_calendar import DerivedPeriod, PeriodWindow

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
    basis: AmountBasis, account_id: int,
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
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis`, carrying the owner
            and the scenario the rows live in.
        account_id: The account receiving the contributions.

    Returns:
        ``{pay_period_id: total}`` over what each row CONTRIBUTES -- the
        realized actual for a settled shadow, else its resolved amount.  ``{}``
        for an account with none.
    """
    rows = query_shadow_income(account_id, basis.scenario_id).all()
    contributions = contributions_by_id(rows, basis)
    totals: dict[int, Decimal] = {}
    for txn in rows:
        totals[txn.pay_period_id] = (
            totals.get(txn.pay_period_id, _ZERO) + contributions[txn.id]
        )
    return totals


def _plan_for(
    account: Account, basis: AmountBasis, inputs: ContributionInputs,
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
        basis: The read pass's amount basis, carrying the scenario the recorded
            contributions live in and the derivations pricing them.
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
        recorded_by_period=_recorded_contributions(basis, account.id),
    )


def contribution_events(
    account: Account,
    basis: AmountBasis,
    inputs: ContributionInputs,
    reconciled_through: ReconciledThrough,
    periods: PeriodWindow,
) -> "list[tuple[date, Decimal]]":
    """Return *account*'s modelled CONTRIBUTION events, dated.

    The tier's ONE entry, so the replay asks "what does this account's payroll
    put in, and when" and never has to know that answering it needs the
    recorded-contribution feed or the annual limit's year boundary.

    Empty -- with no query issued -- for every account that cannot have a
    modelled feed: any kind but INVESTMENT (an INTEREST account's payroll does
    not fund it, and a Property's certainly does not), and an INVESTMENT with
    no :class:`~app.models.investment_params.InvestmentParams` to carry the
    limit and the employer configuration.

    **The pay periods are the READ PASS'S, handed in** (plan step **C2-f2a**,
    ledger row **P37**).  This function issued its own
    ``pay_period_service.get_all_periods`` until then -- a SECOND read of the
    owner's schedule inside a pass that already holds it, and the last reader
    of that module anywhere under :mod:`app.services.balance_at`.  Its ONE
    caller, :func:`._asset_fold.resolve`, now derives the window from the
    pass's own :class:`~app.services.pay_calendar.PayCalendar` -- the same
    derivation every other per-period seam entry reports over, so the
    contribution tier and the cash columns beside it cannot be resolved
    against two readings of one schedule, and no caller states a window at all.

    **The explicit sort went with the query, and the ORDER did not become an
    assumption -- it became a type.**  This door used to sort by ``start_date``
    because ``get_all_periods`` orders by the stored ``period_index``, an
    equation nothing in the schema enforces and one
    :meth:`~app.services.pay_calendar.PayCalendar.filing_period` measures
    parting company with date order on 800 of 872 probed days.  A
    :class:`~app.services.pay_calendar.PeriodWindow` sorts at construction and
    is frozen, so payday order is a property of the value rather than a step
    at this door that a second door could forget.  :func:`_dated_events` stays
    ORDER-SENSITIVE by design -- a calendar-year limit is an accumulation --
    which is why it takes the looser sequence type and this entry takes the
    window.

    Args:
        account: The account to resolve.  Its ``account_type`` drives the
            classifier and its ``user_id`` scopes the RECORDED feed -- which
            is the pass owner's too, since plan step C2-f2a took the AXIS off
            the context's calendar rather than off this account (the seam's
            standing contract makes them one owner).
        basis: The read pass's amount basis, carrying the scenario the
            recorded contributions live in and the derivations pricing them.
        inputs: The account's :class:`ContributionInputs`.
        reconciled_through: The account's coverage boundary -- the assertion
            :func:`_dated_events` admits an event strictly after (ruling R-Z).
        periods: The owner's SAVED pay periods, as
            :meth:`~app.services.pay_calendar.PayCalendar.saved` yields them.
            The WHOLE schedule, never a slice: the year-boundary reset and the
            limit accounting are wrong over one (see :func:`_dated_events`).
            That is a precondition no window type can carry, which is why
            :func:`._asset_fold.resolve` takes the CALENDAR and derives this
            rather than accepting one.

    Returns:
        ``[(payday, amount), ...]`` in payday order, one entry per period that
        contributes a non-zero amount.  ``[]`` when the account models none.
    """
    if (classify_account(account) is not AccountProjectionKind.INVESTMENT
            or inputs.investment_params is None):
        return []
    plan = _plan_for(account, basis, inputs)
    if plan is None:
        return []
    return _dated_events(plan, periods, reconciled_through)


def _dated_events(
    plan: _ContributionPlan,
    periods: "Iterable[DerivedPeriod]",
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
            order on 800 of 872 probed days; C2-c made the caller sort, and
            plan step **C2-f2a** replaced the sort with the ordered type --
            :func:`contribution_events` takes a
            :class:`~app.services.pay_calendar.PeriodWindow`, which sorts at
            construction.  **This parameter stays the looser sequence, and
            that is deliberate**: the walk below is order-SENSITIVE, and a
            control that shows it answering differently for the same plan in a
            different order is what proves the door's guarantee is
            load-bearing: only a window can express it, and only a list can
            violate it.  The whole schedule rather than a slice, too -- the
            year-boundary reset and the limit accounting are wrong over one.
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

        recorded = plan.recorded_by_period.get(period.period_id, _ZERO)
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
