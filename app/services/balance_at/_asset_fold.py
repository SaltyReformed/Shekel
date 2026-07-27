"""Balance-at-T seam -- the MODELLED fold: a cash fold plus a modelled return.

Plan step **X-g1** (``docs/audits/balance_architecture/README.md`` Section 3.2).
"A modelled asset is an event stream", and the stream is the CASH one with two
more event kinds on it::

    AssetEvent = (effective_date, kind, payload)

    kind = ASSERTION    balance := asserted_value      (AccountAnchorHistory, EVERY row)
         | ACTUAL       balance += settled_cash_leg    (settled rows)
         | PLANNED      balance += reservation         (still-projected, clamped, R-G)
         | CONTRIBUTION balance += modelled_rate       (payroll deductions + employer)
         | ACCRUAL      balance += balance * rate      (modelled return, DAILY, R-T)

The first three are exactly :mod:`app.services.balance_at._cash_fold`'s three
tiers, taken whole through :func:`~app.services.balance_at._cash_fold.assemble`
rather than re-derived -- which is the structural claim this module exists to
make: an INTEREST account, an INVESTMENT and a Property are not three questions,
they are the cash fold plus a rate, and :mod:`._interest` already said so in its
own words before there was a shape that could express it.

**ACCRUAL is the only MULTIPLICATIVE kind, and that is the whole difference.**
Its delta is a function of the running balance at its own instant, so resolving
it must be sequential (:func:`_resolve`).  Between two events the balance is
constant, so ONE pass over the merged, date-ordered event list resolves every
accrual on the horizon into ordinary dated deltas -- after which
:func:`~app.services.balance_at._fold.sample_cumulative`, the sampler this
module shares with the LOAN fold and the cash fold, is unchanged.  Generalising
that sampler to be balance-dependent would have put this step's blast radius on
the loan side for no gain.

**What it replaces, and why the replacement is not a preference** (plan step
X-g2 wires it; this module ships ADDITIVE and unwired).  Today a modelled
account's map is three producers merged by a preference order
(``_investment._merge_balance_sources``): a forward growth projection, the
anchor-forward cash base, and a REVERSE growth projection.  Measured on the
prod-shape clone, that merge renders **$6,315.57** of net-worth history that
contradicts the user's own recorded balance assertions -- the three modelled
accounts carry 15 of them and the map reads only the LATEST, re-deriving every
earlier period from a model (findings N-43 / N-74).  A fold has no join, so it
has no join rule to get wrong: every ASSERTION is replayed as a reset, which is
what makes the earlier periods read the numbers the user typed in.

**Four rules decide where the modelled tiers start and what they are worth.**

* **Ruling R-L, generalised at ruling R-Y.**  ACCRUAL exists only on days at or
  after the LATEST balance assertion, and the assertion's OWN day accrues.
  Everything at or before it is a bank fact the user typed in, and modelling
  across those days adds money the assertion already contains.  This is what
  ``_interest`` has done since plan step X-c2a; ruling R-Y extends it to
  INVESTMENT and APPRECIATING, which today skip the anchor PERIOD entirely and
  so silently drop up to a full period of return (measured: Roth +$105.26, Trad
  IRA +$44.95, Empower +$76.59, Property +$170.11 at the anchor period).
* **Ruling R-S.**  There is no backward direction.  Before the FIRST assertion
  the balance is ruling R-I's back-projection over the records it already
  contains -- the cash fold's own answer, inherited here for free -- and the
  reverse growth projection leaves the balance path entirely.
* **Ruling R-T.**  ACCRUAL is DAILY.  A step exists for every day, so a sampled
  date never falls inside an unresolved span and the answer never depends on
  which OTHER dates were asked for.
* **Ruling R-X.**  A day's accrual is computed at FULL precision and credited in
  whole cents, carrying the sub-cent remainder
  (:func:`_resolve`).  Every emitted step is therefore an exact cent -- so the
  grid identity stays exact by construction -- while the cumulative accrual at
  every date equals ``round(exact)``.  Rounding each day independently instead
  would make a small balance accrue nothing at all, forever: 0.45 cents a day on
  a $50 HYSA at 3.29% APY rounds to zero every day, and a $20 holding at 10.5%
  would grow $3.65 a year against a true $2.00.

**Ruling R-R partitions a contribution by SOURCE**, which is what makes the two
feeds disjoint by construction rather than by a de-dup rule: a recorded transfer
HAS a transaction row, so it is already an ACTUAL / PLANNED event; a payroll
deduction never has one, so it is a modelled CONTRIBUTION event.  This module
therefore never reads ``investment_projection._average_transfer_contribution``,
which folds both feeds into one scalar.  The recorded feed is still READ here --
for the annual-limit accounting and as the employer match's base (ruling R-R
consequence (a)) -- but it is never added a second time.

**TOTAL over every date and every account, like the folds it extends.**  An
account that models no return (an INTEREST account whose params row is absent,
an INVESTMENT with no ``InvestmentParams``) IS its cash fold; a date before every
event reads the seed; a future date answers.  The one place it fails loud is a
modelled account with NO assertion history at all, which has no honest window to
open an accrual on -- the same deliberate asymmetry
:func:`~app.services.cash_ledger.resolve_anchor` already enforces for the
interest path.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.services import growth_engine, pay_period_service
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.interest_projection import accrued_interest
from app.services.investment_projection import (
    adapt_deductions,
    deduction_contribution_per_period,
    employer_contribution_params,
)
from app.services.loan_loaders import query_shadow_income
from app.utils.money import round_money

from . import _cash_fold, _interest
from ._context import BalanceContext
from ._fold import sample_cumulative

_ZERO = Decimal("0")
_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)

# ``period_return_rate`` reads only a period's INCLUSIVE calendar-day span, and
# that span is 1 for every single day of every year -- it carries no leap-day or
# month-length switch (unlike the interest day count, which carries both).  So a
# compound return's per-day rate is date-independent and is resolved ONCE, off a
# span whose endpoints are a placeholder rather than a real day.
_ONE_DAY_SPAN = growth_engine.SyntheticPeriod(
    id=0, start_date=date.min, end_date=date.min,
)


@dataclass(frozen=True)
class _InterestAccrual:
    """One day of an INTEREST account's modelled accrual.

    The rate is NOT date-independent: the daily divisor switches to 366 for a
    window containing Feb 29, the monthly divisor is the calendar month's own
    length, and the quarterly divisor is the quarter's -- so each day resolves
    its own.

    That is also what the daily grain BUYS on the monthly and quarterly
    frequencies, and it is measurable: a 14-day pay period straddling a month
    boundary prices every one of its days against the FIRST month's length,
    because ``calculate_interest`` reads ``monthrange(period_start)`` once for
    the whole window.  On $10,000 at 3.29% APY over 2026-01-29 .. 2026-02-11 --
    three January days and eleven February ones -- that is **$12.38** against a
    day-by-day **$13.42**, so one period is short by **$1.04** (8.4%).  It is
    the same class as the "13 days of a 14-day period" note the interest path
    already carries, on the two frequencies that note did not reach; the real
    Money Market compounds MONTHLY.

    Attributes:
        apy: The account's annual percentage yield.
        compounding_frequency_id: ``ref.compounding_frequencies.id``.
    """

    apy: Decimal
    compounding_frequency_id: int

    def one_day(self, day: date, balance: Decimal) -> Decimal:
        """Return the UNROUNDED interest *balance* accrues on *day*.

        Args:
            day: The calendar day accruing.
            balance: The balance held on it.

        Returns:
            The full-precision ``Decimal`` accrual; ``0`` for a non-positive
            balance or a non-positive APY (the shared rule's own guard).
        """
        return accrued_interest(
            balance=balance,
            apy=self.apy,
            compounding_frequency_id=self.compounding_frequency_id,
            period_start=day,
            period_end=day + _ONE_DAY,
        )


@dataclass(frozen=True)
class _CompoundAccrual:
    """One day of an INVESTMENT's growth or an APPRECIATING asset's appreciation.

    Built by :func:`_compound_accrual`, which resolves the per-day rate once
    through the shared :func:`~app.services.growth_engine.period_return_rate` so
    this module states no growth formula of its own.

    Attributes:
        daily_rate: The compound rate for ONE calendar day,
            ``(1 + annual) ** (1 / 365) - 1``.  Negative for a depreciating
            asset, which the schema permits (``annual_appreciation_rate > -1``).
    """

    daily_rate: Decimal

    def one_day(self, _day: date, balance: Decimal) -> Decimal:
        """Return the UNROUNDED growth *balance* accrues in one day.

        The leading underscore on the day parameter is the point: a compound
        rate is date-INDEPENDENT (see :data:`_ONE_DAY_SPAN`), where an interest
        rate is not.  The parameter is still taken so this and
        :meth:`_InterestAccrual.one_day` answer one question through one
        signature, which is what lets :func:`_resolve` carry no branch on the
        account's kind.

        Args:
            balance: The balance held on the day.

        Returns:
            The full-precision ``Decimal`` accrual; ``0`` for a non-positive
            balance, matching the interest rule's own guard, so a modelled
            return can never drive a balance further below zero.
        """
        if balance <= _ZERO:
            return _ZERO
        return balance * self.daily_rate


@dataclass(frozen=True)
class _AccrualWindow:
    """When an account's modelled return runs, and at what rate.

    One optional object rather than three separately-optional fields: an
    account either models a return over a window or it does not, and splitting
    that into a nullable rule beside a nullable start day is how a reader ends
    up checking one and not the other.  ``None`` in place of the whole window is
    the "models nothing" state -- an unconfigured HYSA, an ``InvestmentParams``-
    less brokerage, a Property whose rate is unset -- and the fold is then the
    cash fold unchanged.

    Attributes:
        rule: The per-day accrual rule (:class:`_InterestAccrual` or
            :class:`_CompoundAccrual`).
        start: The first day the return may accrue on -- the LATEST balance
            assertion's UTC civil day, INCLUSIVE (ruling R-L as sharpened at
            plan step X-c2a: the assertion's own day accrues; ruling R-Y
            extends that from INTEREST to all three modelled kinds).
        end: The last day to resolve -- the caller's furthest requested date.
            Sampling BEYOND it would read a balance that had stopped accruing,
            so every entry derives it from its own request rather than from a
            horizon constant.
    """

    rule: "_InterestAccrual | _CompoundAccrual"
    start: date
    end: date

    def days(self) -> list[date]:
        """Return every day this window accrues on, ascending.

        Returns:
            The inclusive day list ``[start .. end]``; empty when the window is
            inverted (a read valued entirely before the account's latest
            assertion, which accrues nothing).
        """
        accruing: list[date] = []
        day = self.start
        while day <= self.end:
            accruing.append(day)
            day += _ONE_DAY
        return accruing

    def accrues_on(self, day: date) -> bool:
        """Return whether *day* falls inside this window.

        Args:
            day: The calendar day to test.

        Returns:
            ``True`` when ``start <= day <= end``.
        """
        return self.start <= day <= self.end


@dataclass(frozen=True)
class _ContributionPlan:
    """What an INVESTMENT account's modelled contributions are made of.

    A cohesive assembly record (:func:`_contribution_plan`): the modelled
    per-period employee amount, the employer configuration, the annual limit,
    and the RECORDED contributions per pay period -- which are read for the
    limit and the match base and never contributed again (ruling R-R).

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


@dataclass(frozen=True)
class AssetPeriodFigures:
    """One pay period's modelled column: the balance and what moved it.

    The per-period output of :func:`asset_period_view`.  For every period and
    every modelled kind, in terms of :attr:`balance`::

        balance(p.end) - balance(p.start - 1 day)
            == <the cash period view's net + reconciliation>
               + accrual + contribution

    (the boundary form, so the FIRST period is covered too -- it has no
    predecessor to subtract), and it holds BY CONSTRUCTION rather than as an
    invariant a test polices: all four terms are readings of ONE resolved step
    list, and every step is a whole cent (ruling R-X).  Plan step X-g3 renders
    the last two as ruling R-W's "Growth" row.

    Attributes:
        balance: The modelled end-of-period balance.
        accrual: The modelled return credited inside this period's span.
        contribution: The modelled contribution credited inside it.
        balance_without_accrual: The same balance with the ACCRUAL events
            filtered out -- the pre-growth SEED a forward chart compounds FROM
            (ruling R-U).  It is the balance MINUS the cumulative accrual rather
            than a second resolve, and the two are equal because no contribution
            amount depends on the balance: the deduction amount, the annual cap
            and the employer match are all balance-independent recurrences.
    """

    balance: Decimal
    accrual: Decimal
    contribution: Decimal
    balance_without_accrual: Decimal


@dataclass(frozen=True)
class _ModelledFold:
    """One account's resolved modelled step list, plus what each tier contributed.

    The output of :func:`_assemble`.  :attr:`steps` is what
    :func:`~app.services.balance_at._fold.sample_cumulative` reads; the two maps
    beside it are the same deltas kept apart so a reader can report WHY a
    balance moved without re-deriving it.

    Attributes:
        seed: The balance before every step (the cash fold's ruling R-I seed).
        steps: The resolved dated deltas, ASCENDING by date -- the cash tiers,
            the contributions and the accruals merged into one running total.
        accrual_by_day: day -> the cent-quantized accrual credited on it.  Days
            crediting nothing are absent.
        contribution_by_day: day -> the modelled contribution landing on it.
    """

    seed: Decimal
    steps: "list[tuple[date, Decimal]]"
    accrual_by_day: "dict[date, Decimal]"
    contribution_by_day: "dict[date, Decimal]"


def _compound_accrual(annual_rate) -> _CompoundAccrual:
    """Return the per-day compound accrual for *annual_rate*.

    Args:
        annual_rate: The configured annual return / appreciation rate.

    Returns:
        The :class:`_CompoundAccrual` whose ``daily_rate`` is the shared growth
        formula evaluated over a one-day span.
    """
    return _CompoundAccrual(
        daily_rate=growth_engine.period_return_rate(
            Decimal(str(annual_rate)), _ONE_DAY_SPAN,
        ),
    )


def _modelled_return(
    account: Account, investment_params: "InvestmentParams | None",
) -> "_InterestAccrual | _CompoundAccrual | None":
    """Return *account*'s modelled per-day return, or ``None`` if it models none.

    "Does this account model a return, and at what rate?" asked ONCE, for all
    three modelled kinds -- the generalisation of
    :func:`app.services.balance_at._interest.accrual_params`, which this
    delegates to for INTEREST so the two cannot answer that kind differently.

    An account whose parameters are absent models NOTHING and is its cash fold:
    an INTEREST-kinded account with no params row is an HYSA the user has not
    configured, an INVESTMENT with no ``InvestmentParams`` is the state
    ``build_account_balance_map`` already falls through on, and a Property with
    no appreciation row is one whose rate is not set.  Inventing a rate for any
    of them would put growth on a screen the account has never earned.

    Args:
        account: The account to test.  Its ``account_type`` drives the
            classifier.
        investment_params: The account's
            :class:`~app.models.investment_params.InvestmentParams`, or ``None``
            -- supplied by the caller's batch-loaded bundle rather than
            re-queried here, exactly as the kernel's dispatcher receives it.

    Returns:
        The per-day accrual rule, or ``None``.
    """
    kind = classify_account(account)
    if kind is AccountProjectionKind.INTEREST:
        interest_params = _interest.accrual_params(account)
        return None if interest_params is None else _InterestAccrual(
            apy=interest_params.apy,
            compounding_frequency_id=interest_params.compounding_frequency_id,
        )
    if kind is AccountProjectionKind.INVESTMENT:
        return None if investment_params is None else _compound_accrual(
            investment_params.assumed_annual_return,
        )
    if kind is AccountProjectionKind.APPRECIATING:
        appreciation_params = account.asset_appreciation_params
        return None if appreciation_params is None else _compound_accrual(
            appreciation_params.annual_appreciation_rate,
        )
    return None


def _latest_assertion_day(
    account: Account, walk: "_cash_fold.CashLedgerWalk",
) -> date:
    """Return the UTC civil day of *account*'s LATEST balance assertion.

    The day ruling R-L's window opens on, read off the WALK the fold was already
    built from rather than through a second
    :func:`~app.services.cash_ledger.resolve_anchor` query.  The two are the same
    row by construction -- both order the account's
    :class:`~app.models.account.AccountAnchorHistory` rows by ``(created_at,
    id)`` and take the last -- which is what makes ruling R-L "one line of the
    event builder" rather than a rule each modelled layer restates.

    **It fails loud for an account with no assertion history, and that asymmetry
    is deliberate.**  The cash fold answers such an account from a zero seed
    (the totality rule the whole arc turns on); a modelled layer cannot, because
    an accrual needs a DATE to open its window on and there is no honest window
    without an assertion.  It is the same refusal
    :func:`~app.services.cash_ledger.resolve_anchor` makes, and the same
    unreachable state: migration ``cfb15e782f86`` and
    ``account_service.create_account`` guarantee every account an opening row.

    Args:
        account: The account, named in the failure.
        walk: Its :class:`~app.services.cash_ledger.CashLedgerWalk`.

    Returns:
        The assertion's UTC calendar date.

    Raises:
        RuntimeError: When the account carries no assertion at all.
    """
    if not walk.anchor_corrections:
        raise RuntimeError(
            f"_asset_fold: account id={account.id} models a return but has "
            "zero AccountAnchorHistory rows, so there is no assertion to open "
            "its accrual window on.  Migration cfb15e782f86 plus "
            "account_service.create_account make this state unreachable; "
            "investigate any code path that constructed the Account row "
            "without routing through the canonical factory."
        )
    return walk.anchor_corrections[-1].visible_on


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


def _contribution_plan(
    account: Account,
    scenario_id: int,
    investment_params: InvestmentParams,
    deductions: list,
    salary_gross_biweekly: Decimal,
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
        investment_params: The account's
            :class:`~app.models.investment_params.InvestmentParams`.
        deductions: Its active paycheck deductions (adapted here).
        salary_gross_biweekly: The raise-aware engine gross per pay period, the
            employer-match cap basis and the fallback gross when no deduction
            supplies one.

    Returns:
        The :class:`_ContributionPlan`, or ``None``.
    """
    per_period, gross_biweekly = deduction_contribution_per_period(
        adapt_deductions(deductions), salary_gross_biweekly,
    )
    employer_params = employer_contribution_params(
        investment_params, gross_biweekly,
    )
    if per_period <= _ZERO and employer_params is None:
        return None
    return _ContributionPlan(
        per_period=per_period,
        employer_params=employer_params,
        annual_limit=investment_params.annual_contribution_limit,
        recorded_by_period=_recorded_contributions(account.id, scenario_id),
    )


def _contribution_events(
    plan: _ContributionPlan,
    periods: "list[PayPeriod]",
    accrual_start: date,
) -> "list[tuple[date, Decimal]]":
    """Resolve the plan into dated CONTRIBUTION events, one per paying period.

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


def _resolve(
    cash: _cash_fold.AssembledCashFold,
    contributions: "list[tuple[date, Decimal]]",
    window: "_AccrualWindow | None",
) -> _ModelledFold:
    """Replay the merged event stream ONCE, resolving each day's accrual in order.

    The sequential pass ACCRUAL forces and the reason it is the only kind that
    needs one: its delta is a function of the running balance at its own
    instant, so it cannot be known before the events before it are applied.
    Everything else -- the cash tiers, the contributions -- is already a dated
    delta, so the pass merges them by day, walks the days in order, and turns
    each day's accrual into an ordinary dated delta.  After it,
    :func:`~app.services.balance_at._fold.sample_cumulative` reads the result
    exactly as it reads the cash and loan folds.

    **A day accrues on the balance it ENDS holding.**  The day's cash and
    contribution steps are applied first, then the accrual is computed on the
    result and credited on the same day.  That is the shipped interest rule's
    own base (``_layer_interest`` accrues on the period's END balance) taken to
    the daily grain, where the two conventions the code carries today -- accrue
    on the period's END for interest, on its START for growth -- collapse into
    "the balance actually held on the day", with no boundary left to pick.

    **Ruling R-X's cent carry, in three lines.**  The accrual is accumulated at
    full precision in ``exact``; what is CREDITED is ``round_money(exact)``, and
    each day's step is the change in that.  So every step is a whole cent (the
    property the identity in :class:`AssetPeriodFigures` needs), the cumulative
    accrual at any date equals ``round(exact)`` (no per-day rounding bias), and a
    sub-half-cent daily accrual accumulates into a cent instead of vanishing.

    Args:
        cash: The account's assembled cash fold -- its seed and its three
            tiers' dated deltas.
        contributions: The dated CONTRIBUTION events.
        window: The account's :class:`_AccrualWindow`, or ``None`` when it
            models no return (the result is then the cash fold, unchanged).

    Returns:
        The :class:`_ModelledFold`.
    """
    by_day, contribution_by_day = _merged_day_deltas(cash, contributions)
    days = set(by_day)
    if window is not None:
        days.update(window.days())
    steps, accrual_by_day = _resolve_days(cash.seed, by_day, days, window)
    return _ModelledFold(
        seed=cash.seed,
        steps=steps,
        accrual_by_day=accrual_by_day,
        contribution_by_day=contribution_by_day,
    )


def _merged_day_deltas(
    cash: _cash_fold.AssembledCashFold,
    contributions: "list[tuple[date, Decimal]]",
) -> "tuple[dict[date, Decimal], dict[date, Decimal]]":
    """Collapse the additive tiers onto their days, keeping the contributions apart.

    Args:
        cash: The account's assembled cash fold.
        contributions: The dated CONTRIBUTION events.

    Returns:
        ``(all_by_day, contribution_by_day)`` -- every additive delta summed per
        day, and the CONTRIBUTION half of the same sum kept separately so a
        reader can report what the modelled tier put in without re-deriving it.
    """
    by_day: dict[date, Decimal] = {}
    for on_date, delta in cash.steps:
        by_day[on_date] = by_day.get(on_date, _ZERO_MONEY) + delta
    contribution_by_day: dict[date, Decimal] = {}
    for on_date, amount in contributions:
        by_day[on_date] = by_day.get(on_date, _ZERO_MONEY) + amount
        contribution_by_day[on_date] = (
            contribution_by_day.get(on_date, _ZERO_MONEY) + amount
        )
    return by_day, contribution_by_day


def _resolve_days(
    seed: Decimal,
    by_day: "dict[date, Decimal]",
    days: "set[date]",
    window: "_AccrualWindow | None",
) -> "tuple[list[tuple[date, Decimal]], dict[date, Decimal]]":
    """Walk the days in order, turning each one's accrual into a dated delta.

    The sequential half of :func:`_resolve` -- see its docstring for the
    end-of-day base and ruling R-X's cent carry, both of which live in this
    loop.

    Args:
        seed: The balance before every step.
        by_day: Every additive delta summed per day.
        days: Every day to walk -- the additive days plus the accruing ones.
        window: The account's :class:`_AccrualWindow`, or ``None``.

    Returns:
        ``(steps, accrual_by_day)`` -- the resolved dated deltas ASCENDING by
        date, and the cent-quantized accrual credited on each day that credited
        one.
    """
    running = seed
    exact = _ZERO
    credited = _ZERO_MONEY
    steps: "list[tuple[date, Decimal]]" = []
    accrual_by_day: dict[date, Decimal] = {}
    for on_date in sorted(days):
        delta = by_day.get(on_date, _ZERO_MONEY)
        running += delta
        if window is not None and window.accrues_on(on_date):
            exact += window.rule.one_day(on_date, running)
            step = round_money(exact) - credited
            if step != _ZERO_MONEY:
                credited += step
                running += step
                delta += step
                accrual_by_day[on_date] = step
        steps.append((on_date, delta))
    return steps, accrual_by_day


def _assemble(  # pylint: disable=too-many-arguments
    account: Account,
    ctx: BalanceContext,
    horizon_end: date,
    *,
    investment_params: "InvestmentParams | None",
    deductions: list,
    salary_gross_biweekly: Decimal,
) -> _ModelledFold:
    """Build and resolve *account*'s whole modelled event stream -- ONCE.

    The single assembly both entries below share, so a scalar and a period map
    of the same account are readings of ONE resolved step list rather than two
    producers a test keeps in step.

    Pylint: ``too-many-arguments`` (6/5) -- the keyword-only group is this
    account's three independent projection inputs (its investment params, its
    deductions, the engine gross-biweekly), mirroring
    :func:`~app.services.balance_at._kernel.build_account_balance_map`'s own
    signature so plan step X-g2's cutover is a call swap rather than a
    re-assembly.  They are not a cohesive named concept, and re-wrapping them in
    a bundle no other caller shares would be the stamp coupling the standards
    reject.

    Args:
        account: The account to value.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold and the contribution feed, its
            ``as_of`` is ruling R-G's clamp floor, its ``user_id`` is not read
            -- the period calendar comes off the account).
        horizon_end: The furthest date this read will be sampled at.
        investment_params: The account's ``InvestmentParams``, or ``None``.
        deductions: Its active paycheck deductions.
        salary_gross_biweekly: The raise-aware engine gross per pay period.

    Returns:
        The resolved :class:`_ModelledFold`.
    """
    cash = _cash_fold.assemble(account, ctx.scenario.id, ctx.as_of)
    accrual = _modelled_return(account, investment_params)
    if accrual is None:
        return _resolve(cash, [], None)

    window = _AccrualWindow(
        rule=accrual,
        start=_latest_assertion_day(account, cash.walk),
        end=horizon_end,
    )
    contributions: "list[tuple[date, Decimal]]" = []
    if (classify_account(account) is AccountProjectionKind.INVESTMENT
            and investment_params is not None):
        plan = _contribution_plan(
            account, ctx.scenario.id, investment_params,
            deductions, salary_gross_biweekly,
        )
        if plan is not None:
            contributions = _contribution_events(
                plan,
                pay_period_service.get_all_periods(account.user_id),
                window.start,
            )
    return _resolve(cash, contributions, window)


def fold_asset_balances(  # pylint: disable=too-many-arguments
    account: Account,
    ctx: BalanceContext,
    dates: list[date],
    *,
    investment_params: "InvestmentParams | None",
    deductions: list,
    salary_gross_biweekly: Decimal,
) -> dict[date, Decimal]:
    """Return *account*'s modelled balance at each of *dates*.

    The modelled counterpart of
    :func:`app.services.balance_at._cash_fold.fold_cash_balances`, and the
    producer that makes a modelled kind answer a DATE rather than a period: the
    three modelled kinds are period-granular today
    (``_kind_correct.balance_at`` resolves a date to its period and reads the
    map), so a whole period's growth lands on the period's FIRST day -- measured
    at period 30 on the prod-shape clone, the scalar returns the identical value
    on that period's first and last day while $328.50 of growth accrues inside
    it (finding N-71).  A daily step list has no such state to be in.

    Pylint: ``too-many-arguments`` (6/5) -- see :func:`_assemble`.

    Args:
        account: The account to value.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        dates: The dates to value the account at, in any order.  Duplicates
            collapse.
        investment_params: The account's ``InvestmentParams``, or ``None``.
        deductions: Its active paycheck deductions.
        salary_gross_biweekly: The raise-aware engine gross per pay period.

    Returns:
        ``{date: balance}`` -- one cent-quantized ``Decimal`` per distinct
        requested date.  ``{}`` for an empty *dates*.
    """
    if not dates:
        return {}
    folded = _assemble(
        account, ctx, max(dates),
        investment_params=investment_params,
        deductions=deductions,
        salary_gross_biweekly=salary_gross_biweekly,
    )
    return sample_cumulative(folded.seed, folded.steps, dates)


def asset_period_view(  # pylint: disable=too-many-arguments
    account: Account,
    ctx: BalanceContext,
    periods: "list[PayPeriod]",
    *,
    investment_params: "InvestmentParams | None",
    deductions: list,
    salary_gross_biweekly: Decimal,
) -> "OrderedDict[int, AssetPeriodFigures]":
    """Return *account*'s modelled column for each of *periods*.

    The per-period map, its accrual and contribution components, and ruling
    R-U's pre-growth seed, all sampled off ONE resolved step list.  Each period
    is valued over its OWN span -- ``(p.start_date - 1 day, p.end_date]`` -- so
    the periods need be neither contiguous nor ordered, and the first period is
    covered without a predecessor to subtract from.

    Every component is read through the shared
    :func:`~app.services.balance_at._fold.sample_cumulative`, never as a
    residual: a residual would make the identity in
    :class:`AssetPeriodFigures` arithmetically true and therefore untestable,
    and would silently absorb whatever the resolution got wrong.

    Pylint: ``too-many-arguments`` (6/5) -- see :func:`_assemble`.

    Args:
        account: The account to value.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        periods: The pay periods to value, in the caller's display order.
        investment_params: The account's ``InvestmentParams``, or ``None``.
        deductions: Its active paycheck deductions.
        salary_gross_biweekly: The raise-aware engine gross per pay period.

    Returns:
        ``OrderedDict`` period id -> :class:`AssetPeriodFigures`, in the order
        *periods* was given.  EVERY input period is present.  Empty for an empty
        *periods*.
    """
    if not periods:
        return OrderedDict()

    folded = _assemble(
        account, ctx, max(period.end_date for period in periods),
        investment_params=investment_params,
        deductions=deductions,
        salary_gross_biweekly=salary_gross_biweekly,
    )
    ends = [period.end_date for period in periods]
    boundaries = ends + [period.start_date - _ONE_DAY for period in periods]
    return _assemble_columns(
        periods,
        sample_cumulative(folded.seed, folded.steps, ends),
        sample_cumulative(
            _ZERO_MONEY, sorted(folded.accrual_by_day.items()), boundaries,
        ),
        sample_cumulative(
            _ZERO_MONEY, sorted(folded.contribution_by_day.items()), boundaries,
        ),
    )


def _assemble_columns(
    periods: "list[PayPeriod]",
    balances: dict[date, Decimal],
    accrued: dict[date, Decimal],
    contributed: dict[date, Decimal],
) -> "OrderedDict[int, AssetPeriodFigures]":
    """Read each period's column off the three sampled cumulative series.

    Args:
        periods: The pay periods to report, in display order.
        balances: The modelled running total sampled at each period's
            ``end_date``.
        accrued: The cumulative ACCRUAL sampled at each period's ``end_date``
            AND at the day before its ``start_date``.
        contributed: The cumulative CONTRIBUTION sampled at the same two
            boundaries.

    Returns:
        ``OrderedDict`` period id -> :class:`AssetPeriodFigures`.
    """
    columns: "OrderedDict[int, AssetPeriodFigures]" = OrderedDict()
    for period in periods:
        opening = period.start_date - _ONE_DAY
        columns[period.id] = AssetPeriodFigures(
            balance=balances[period.end_date],
            accrual=accrued[period.end_date] - accrued[opening],
            contribution=(
                contributed[period.end_date] - contributed[opening]
            ),
            balance_without_accrual=(
                balances[period.end_date] - accrued[period.end_date]
            ),
        )
    return columns
