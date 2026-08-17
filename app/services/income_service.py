"""
Shekel Budget App -- Income Service (F-20 / MED-06 / F-032).

Single source of truth for the raise-aware per-period gross income
quantity that every income-derived dashboard surface needs.  Wraps
:func:`paycheck_calculator.calculate_paycheck` so the engine's
:attr:`~app.services.paycheck_calculator.Earnings.gross_biweekly`
(``breakdown.earnings.gross_biweekly``) is the canonical value -- never the off-engine
``Decimal(str(profile.annual_salary)) / pay_periods_per_year``
recompute that silently dropped any applicable
:class:`~app.models.salary_raise.SalaryRaise` row pre-Commit-17.

Pre-fix, six call sites read the off-engine quantity:

- ``savings_dashboard_service._data._load_account_params``
- ``year_end_summary_service._load_salary_gross_biweekly``
- ``retirement_plan.picture_at`` (projected-salary path)
- ``retirement_projection.load_projection_batch``
- ``investment_dashboard_service._context._load_projection_context``

For users with applicable raises, those quantities drifted from the
paycheck engine's per-period gross by the raise factor -- the audit's
F-032 worked example: a $104,000 base with a 3% recurring raise showed
``$4,000.00`` per period off-engine vs ``$4,120.00`` from the engine,
which then under-stated the employer-match cap basis, the retirement
gap denominator, and the year-end employer / investment-growth totals
by the same factor.  Routing every consumer through this one helper
means the corrected income figure shows up uniformly.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol.  All inputs are plain data
(user id, optional scenario id, optional ``as_of`` date); the return
value is a :class:`~decimal.Decimal`.
"""

import logging
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.services import paycheck_calculator
from app.services.pay_calendar import PeriodWindow, calendar_for
from app.services.tax_config_service import (
    load_tax_configs_for_periods,
    load_tax_configs_for_year,
)
from app.utils.balance_predicates import is_projected

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


def get_current_gross_biweekly(
    user_id: int,
    *,
    scenario_id: int | None = None,
    as_of: date | None = None,
) -> Decimal:
    """Return the raise-aware per-period gross for the user's active salary profile.

    The canonical raise-aware income producer (F-20 / MED-06 / F-032):
    every dashboard surface that needs the current gross per pay period
    routes through this helper so the value cannot disagree with the
    paycheck engine for the same period.  Internally loads the user's
    active :class:`SalaryProfile`, resolves the pay period containing
    ``as_of`` (default: today), and invokes
    :func:`paycheck_calculator.calculate_paycheck` so any applicable
    :class:`~app.models.salary_raise.SalaryRaise` row is folded into
    the post-raise annual salary -- which the engine then divides by
    ``pay_periods_per_year`` and reconciles per
    :func:`paycheck_calculator._gross_biweekly_for_period`.

    Returning a single Decimal (rather than the full
    :class:`~app.services.paycheck_calculator.PaycheckBreakdown`)
    matches the producer shape every consumer wants: a snapshot
    "current per-period gross" value that downstream code feeds into
    investment / retirement / employer-match math.  Callers that
    already hold a :class:`PaycheckBreakdown` for the same period
    should read ``breakdown.earnings.gross_biweekly`` directly rather than
    re-invoking this helper (avoids re-querying tax configs and
    re-running the engine for an identical result).

    Args:
        user_id: ID of the user whose active salary profile to load.
        scenario_id: Optional scenario filter.  When provided, only a
            ``SalaryProfile`` whose ``scenario_id`` matches is
            returned -- year-end consumers pass this to scope to the
            same scenario they aggregate against.  When ``None``, the
            filter is omitted and the user's first ``is_active=True``
            profile across all scenarios is used (the historical
            savings / retirement / investment dashboard behavior).
        as_of: Optional date for which to compute the gross.  Defaults
            to today.  The period covering it is resolved off the owner's
            DERIVED calendar
            (:meth:`~app.services.pay_calendar.PayCalendar.period_containing`).

    Returns:
        The paycheck engine's
        :attr:`~app.services.paycheck_calculator.Earnings.gross_biweekly`
        for the resolved profile + period.  Returns ``Decimal("0")``
        when the user has no active salary profile or no pay period
        covers ``as_of`` -- both pre-fix call sites returned
        ``Decimal("0")`` for the missing-profile branch, so the
        substitute preserves the contract.

    Raises:
        PayCalendarError: The owner's paydays do not define a calendar (plan
            findings **P8** / **P35**, owned by pay-calendar plan step C4).
            Reached only for an owner who HAS an active salary profile, since
            the profile lookup runs first.

    **It derives the calendar rather than being handed one** (pay-calendar plan
    step C2-f2d-3).  All three callers hold a
    :class:`~app.services.balance_at.BalanceContext` whose ``calendar()`` memo
    would answer both questions for free, and two of them do not even pass
    ``as_of``, so this is one of the doors ledger row **P56** counts.  Taking
    the pass is ``C2-f3``'s move; the two reads here are the two this function
    already made.
    """
    query = (
        db.session.query(SalaryProfile)
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.is_active.is_(True),
        )
    )
    if scenario_id is not None:
        query = query.filter(SalaryProfile.scenario_id == scenario_id)
    profile = query.first()
    if profile is None:
        return ZERO

    as_of_date = as_of or date.today()
    # ONE derivation answers both questions the engine needs -- which paycheck
    # covers ``as_of``, and the owner's whole schedule for the cumulative and
    # reconciliation context -- where the two SQL readers this replaced could
    # answer from two different reads (pay-calendar plan step C2-f2d-3).
    calendar = calendar_for(user_id)
    current_period = calendar.period_containing(as_of_date)
    if current_period is None:
        return ZERO

    all_periods = calendar.saved()
    # Resolved for the RESOLVED PERIOD's own tax year rather than the clock's,
    # which is the key ``live_projected_net`` below already uses for every
    # period it prices -- so a caller reading one period and a caller reading
    # the horizon cannot resolve the same period against different rules.
    tax_configs = load_tax_configs_for_year(
        user_id, profile, current_period.start_date.year,
    )
    breakdown = paycheck_calculator.calculate_paycheck(
        profile, current_period, all_periods, tax_configs,
    )
    return breakdown.earnings.gross_biweekly


class SalaryPricing:
    """What the owner's active salary profiles pay, resolved per profile ASKED.

    **The DERIVATION half of the salary amount rule, split from its per-row
    lookup at plan step X-au-c2b.**  Everything expensive behind a paycheck's
    live figure -- the owner's whole pay-period set, each profile's tax configs
    resolved per period YEAR, and ``paycheck_calculator.project_salary`` run over
    the complete set so the biweekly rounding residue reconciles against the
    annual figure -- depends on ``(user_id, scenario_id)`` and on NOTHING about
    which rows a caller happens to have loaded.  Keying it that way is what lets
    one read pass resolve it ONCE however many row sets ask
    (:class:`~app.services.cash_ledger.AmountBasis`).

    It was a ``{transaction_id: Decimal}`` map built per row set until that step,
    and the two consequences are why this type exists.  A request that loaded two
    row sets ran the paycheck engine twice (findings **N-268**, **N-269**), and a
    row outside the set it was built over had no answer -- which forced the basis
    to carry the set as a membership guard so the miss could be REFUSED rather
    than read as "this row has no live figure".  Keyed on the definition and the
    period instead, there is no membership question left to get wrong: the pair
    IS the paycheck's identity.

    **Laziness is in TWO stages, and an adversarial review of this step's own
    build is why.**  The first draft resolved one map for every active profile
    on first read, gated only by ``is_income and template_id is not None`` -- a
    test that cannot tell a paycheck template from any other recurring income.
    Both halves of that were a regression against the row-set producer it
    replaced, which filtered its profile query by the CANDIDATE rows' templates
    and returned ``{}`` without projecting anything:

      * a recurring *Interest* or *Dividend* income row -- templated, projected,
        not a paycheck -- forced the whole paycheck engine, where the old path
        paid one indexed query and stopped; and
      * an owner with TWO active profiles paid ``project_salary`` twice on a
        pass whose rows named one of them.

    So the PROFILE LOOKUP is memoized separately from the PROJECTIONS: asking
    about a template no profile names costs one indexed query and no engine run,
    and a profile is projected only when a row actually asks about it.  Both
    memos live for the pass, so the sharing this class exists for is unchanged.
    """

    def __init__(self, user_id: int, scenario_id: int) -> None:
        """Pin the owner and scenario; resolve nothing yet.

        Args:
            user_id: The owner whose active profiles price these rows.
            scenario_id: The scenario to resolve profiles against.
        """
        self._user_id = user_id
        self._scenario_id = scenario_id
        self._profiles: "dict[int, SalaryProfile] | None" = None
        self._periods: PeriodWindow | None = None
        self._net_by_profile: "dict[int, dict[int, Decimal]]" = {}

    def net_for(
        self, template_id: int, pay_period_id: int,
    ) -> "Decimal | None":
        """Return what the profile driving *template_id* pays for that period.

        Args:
            template_id: The recurring definition the row was generated from.
            pay_period_id: The period the row is funded in.

        Returns:
            The live net pay, or ``None`` when no ACTIVE profile in this
            scenario names that template, or when the profile's projection does
            not reach that period.  Both are the refusals amount rule 2 raises
            rather than substituting a stored figure.
        """
        profile = self._profile_by_template().get(template_id)
        if profile is None:
            return None
        return self._net_by_period(profile).get(pay_period_id)

    def _profile_by_template(self) -> "dict[int, SalaryProfile]":
        """Return ``{template_id: profile}`` for this owner and scenario.

        One indexed query, memoized -- the CHEAP stage, so a row on a template
        no profile names is answered without projecting anything.

        **The query is ORDERED, and it was not before.**  Two active profiles
        naming ONE template in one scenario is expressible (nothing constrains
        it) and this map keeps the last writer.  Unordered, that was whichever
        row the planner reached first, so one owner could be priced two ways
        across two requests; ordering by id makes the collision resolve the same
        way every time.  The collision itself is finding **N-294**, reported
        rather than fixed here: which profile SHOULD win is a question for the
        salary arc, and answering it inside a reader refactor would be an
        unreviewed ruling.

        Returns:
            ``{template_id: SalaryProfile}``; empty for an owner with no active
            profile in this scenario.
        """
        if self._profiles is None:
            profiles = (
                db.session.query(SalaryProfile)
                .filter(
                    SalaryProfile.user_id == self._user_id,
                    SalaryProfile.scenario_id == self._scenario_id,
                    SalaryProfile.is_active.is_(True),
                )
                .order_by(SalaryProfile.id)
                .all()
            )
            self._profiles = {p.template_id: p for p in profiles}
        return self._profiles

    def _net_by_period(self, profile) -> dict[int, Decimal]:
        """Return ``{pay_period_id: net pay}`` for one profile, projecting once.

        The EXPENSIVE stage, memoized per profile.  Runs
        :func:`paycheck_calculator.project_salary` over the owner's full
        pay-period set -- required, not an optimisation: the biweekly residue
        reconciliation anchors against the complete annual figure, exactly as
        the salary projection page does.  Tax configs resolve PER period year
        (DH-#30), the same per-year resolution the recurrence engine uses to
        GENERATE the stored amount, so the live figure and the generated one
        cannot disagree for want of a bracket set.

        Args:
            profile: The active :class:`SalaryProfile` to project.

        Returns:
            ``{pay_period_id: net pay}`` for every period the projection covers.
        """
        if profile.id not in self._net_by_profile:
            if self._periods is None:
                # The owner's saved schedule off the DERIVED calendar
                # (pay-calendar plan step C2-f2d-3), so the periods this
                # projection reconciles against carry the ends the whole payday
                # set dictates rather than the stored ``end_date`` column plan
                # step C4 drops.  A second derivation of a value the read pass
                # holding this basis already memoizes -- recorded rather than
                # threaded, because handing the basis a calendar is
                # ``balance:X-i1``'s input tier and this is a reader move.
                self._periods = calendar_for(self._user_id).saved()
            configs_by_year = load_tax_configs_for_periods(
                self._user_id, profile, self._periods,
            )
            breakdowns = paycheck_calculator.project_salary(
                profile, self._periods, configs_by_year=configs_by_year,
                calibration=profile.calibration,
            )
            self._net_by_profile[profile.id] = {
                bd.period.period_id: bd.earnings.net_pay for bd in breakdowns
            }
        return self._net_by_profile[profile.id]


def salary_pricing(user_id: int, scenario_id: int) -> SalaryPricing:
    """Return the read pass's :class:`SalaryPricing` for an owner and scenario.

    The named constructor the amount model calls, so no caller reaches for the
    class directly and the two pins are always supplied together.  Resolves
    nothing: both stages behind it are lazy, so a pass that prices no paycheck
    issues no query.

    Args:
        user_id: The owner whose profiles price these rows.
        scenario_id: The scenario to resolve profiles against.

    Returns:
        The unresolved :class:`SalaryPricing` handle.
    """
    return SalaryPricing(user_id, scenario_id)


def salary_net_for(txn, pricing: SalaryPricing) -> "Decimal | None":
    """Return what the salary profile pays for *txn*'s period, or ``None``.

    **The PRICING lookup -- amount rule 2's whole body** (ruling **R-FI**), split
    from the read-time repair below at plan step X-au-c2b.  It asks only what a
    paycheck IS worth, so it reads nothing about whether the row still counts:
    not ``is_projected``, not ``is_override``, not ``is_deleted``.  That is
    finding **N-262**'s rule applied one tier down -- those three say whether a
    row COUNTS and who last touched it, never who prices it -- and it is why a
    Cancelled paycheck resolves like any other instead of refusing for a reason
    that has nothing to do with pricing.

    ``is_income`` IS read, and it is a pricing fact rather than a status one: a
    salary profile states a NET PAY, so an expense row on a salary-linked
    template has no figure here to find.  ``amount_rule`` still places such a
    row under rule 2 (it classifies by the definition, which is salary-linked),
    so this returning ``None`` is what turns it into that rule's refusal.

    Args:
        txn: The row being priced.  ``is_income``, ``template_id`` and
            ``pay_period_id`` are read.
        pricing: The read pass's :class:`SalaryPricing`.

    Returns:
        The live net pay for the row's period, or ``None`` when no active
        profile in this scenario names its template, when the projection does
        not cover its period, or when it is not an income row.
    """
    if not txn.is_income or txn.template_id is None:
        return None
    return pricing.net_for(txn.template_id, txn.pay_period_id)


def live_projected_net(txn, pricing: SalaryPricing) -> "Decimal | None":
    """Return the live net that SUPERSEDES *txn*'s stored figure, or ``None``.

    The read-time repair, and the half of the old batch producer that reads a
    row's status.  A stored ``Transaction.estimated_amount`` is a cache of this
    derivation (finding **N-224**), so every balance and display surface shows
    the recompute rather than the column -- which is what keeps the grid from
    disagreeing with the salary page after a profile, calibration, or
    financial-calc CODE change that fired no regeneration.

    Only a row that is ALL of:

      * income with a template, and priced by an active profile in this
        scenario for its own period (:func:`salary_net_for`);
      * Projected (:func:`~app.utils.balance_predicates.is_projected` --
        Received / Settled income carries a realized ``actual_amount`` that is a
        historical fact, never a recomputable projection);
      * NOT user-overridden (``is_override`` -- a manual amount the user
        deliberately set is respected, mirroring the recurrence engine),

    has a live figure.  Every other row answers ``None`` and keeps its stored
    one, so the repair stays dormant for non-salary income, overridden rows and
    expenses.

    **This gate is the repair's, NOT the pricing rule's**, and separating them is
    plan step X-au-c2b.  Ruling R-FI deletes the repair outright -- plan steps
    X-au-d and X-au-g declare these rows DERIVED, after which the resolver
    answers them from :func:`salary_net_for` and there is no stored figure left
    to supersede.  Until then the two coexist and only this one reads status.

    Args:
        txn: The row to ask about.
        pricing: The read pass's :class:`SalaryPricing`.

    Returns:
        The live net pay when the repair applies to this row, else ``None``.
    """
    if not is_projected(txn) or txn.is_override:
        return None
    return salary_net_for(txn, pricing)
