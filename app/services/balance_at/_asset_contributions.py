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
here reads ``investment_projection._inputs._average_transfer_contribution``,
the scalar
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
    AccountPayrollFeed,
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
        feed: The account's
            :class:`~app.services.investment_projection.AccountPayrollFeed` --
            what its payroll puts in on each payday, and what gross funds its
            employer contribution, both priced by the PAYCHECK ENGINE at the
            loader (plan step **salary:R14-b**, ruling **R-SAL2**).  It
            replaced two fields: the adapted deduction rows, which this tier
            re-priced off the profile's stored annual salary and so read
            RAISE-BLIND (finding **D45**), and one ``salary_gross_biweekly``
            scalar that sized every period's employer contribution at today's
            gross.  Both were one figure standing in for a series; a payday is
            what makes either fact true, so both are keyed by one now.
    """

    investment_params: InvestmentParams | None = None
    feed: AccountPayrollFeed = field(default_factory=AccountPayrollFeed.absent)

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

    A cohesive assembly record (:func:`_plan_for`): the modelled payroll feed,
    the employer configuration, the annual limit, and the RECORDED
    contributions per pay period -- which are read for the limit and the
    match base and never contributed again (ruling R-R).

    Attributes:
        feed: The account's
            :class:`~app.services.investment_projection.AccountPayrollFeed`,
            asked per payday for the employee amount and for the gross the
            employer contribution is a percentage of.  **It was one
            ``per_period`` scalar until plan step salary:R14-b**, which is
            finding **D45**: a deduction's amount and its paycheck's gross
            both move with every raise, and one figure applied to all 63 of
            the developer's saved paydays could only be right for the periods
            that happened to share it.
        employer_params: The employer-contribution configuration
            (:func:`~app.services.investment_projection.employer_contribution_params`),
            or ``None`` when the account has none.  It no longer embeds a
            gross; the period's own comes off :attr:`feed`.
        annual_limit: The account's annual employee-contribution ceiling, or
            ``None`` for an account with no IRS limit.
        recorded_by_period: pay_period_id -> the summed CONTRIBUTION of the
            transfer-linked rows actually recorded in that period.
    """

    feed: AccountPayrollFeed
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

    ``None`` when nothing is modelled at all -- no deduction pays this account
    on any payday AND no employer contribution can be sized -- which is what
    keeps a plain IRA from paying for a period-calendar load it has no use
    for.  Note that an employer FLAT percentage models money with a zero
    employee feed (the real Empower 401(k) shape: 5% of a paycheck's own
    gross, `$181.59` rising to `$202.40` across the paydays this walk actually
    reaches -- the earlier `$176.30` paydays fall at or before the account's
    assertion and ruling **R-Z** excludes them; see :func:`_dated_events`),
    so the test is on both halves, not on the employee amount alone.

    **The employer half's test gained a second conjunct at plan step
    salary:R14-b**: a configured employer contribution models money only when
    a funding profile is KNOWN (``budget.investment_params.salary_profile_id``
    -- ruling **R-SAL5**, and the developer's 2026-09-04 ruling that an
    unknown one models nothing rather than being priced off whichever profile
    a reader resolved).  Without it this would build a plan whose every
    employer figure is ``$0.00``, which is the same money and a worse
    explanation of it.

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
    employer_params = employer_contribution_params(inputs.investment_params)
    models_employer = (
        employer_params is not None and inputs.feed.funds_employer
    )
    # PRICE here, where :func:`build_contribution_timeline`'s gate is
    # PRESENCE, and the asymmetry is deliberate: that one decides whether to
    # emit records that SUPPRESS the growth engine's fallback, so it must ask
    # whether a deduction is wired up; this one decides whether there is
    # anything to model AT ALL, and a linked deduction that prices $0.00 on
    # every payday of the window models nothing.
    if not inputs.feed.models_employee and not models_employer:
        return None
    return _ContributionPlan(
        feed=inputs.feed,
        employer_params=employer_params if models_employer else None,
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

    **Both figures are now the PERIOD's own** (plan step **salary:R14-b**,
    ruling **R-SAL2**).  The employee amount was one scalar repeated across
    every period and the employer gross was another, so this walk applied one
    paycheck's answer to a schedule the paycheck engine prices individually --
    finding **D45**.

    **Measured on the developer's own data 2026-09-04, and the WINDOW is part
    of the figure**: through ``balance_at.grid_balance_view`` -- the door the
    grid itself reads -- the Empower 401(k)'s modelled contribution over the
    63 saved paydays goes `$10,169.04` -> `$10,621.46`, **`+$452.42`**, and
    its balance at the last period `$49,960.31` -> `$50,442.34`.  Per payday
    that is a flat `$181.59` becoming `$181.59` / `$186.13` / `$191.71` /
    `$196.50` / `$202.40` as each raise lands.

    **56 of the 63 paydays pay, and the other 7 are why an earlier draft of
    this paragraph said `+$415.39` with early periods at `$176.30`.**  That
    figure summed the employer events over ALL 63 paydays with no
    ``reconciled_through``, so it included the seven at or before the
    account's balance assertion -- which ruling **R-Z**, three paragraphs up,
    excludes from this walk entirely.  Those seven are the only ones where
    R14-b models LESS than the old feed did (`$176.30` against `$181.59`), so
    counting them netted a real understatement against periods the app never
    reaches.  The window this walk covers is the owner's SAVED schedule minus
    what the assertion already contains, and it is exactly the domain the feed
    prices, so the feed's hold rule never engages here.

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
            plan.feed.employee_at(period.start_date), plan.annual_limit, ytd,
        )
        employer = growth_engine.calculate_employer_contribution(
            plan.employer_params, recorded + employee,
            plan.feed.gross_at(period.start_date),
        )
        ytd += employee
        amount = employee + employer
        if amount != _ZERO:
            events.append((period.start_date, amount))
    return events
