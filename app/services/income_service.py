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
- ``retirement_dashboard_service.compute_gap_data`` (projected-salary path)
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
from app.services import pay_period_service, paycheck_calculator
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
            to today.  Passed to
            :func:`pay_period_service.get_current_period` for period
            resolution.

    Returns:
        The paycheck engine's
        :attr:`~app.services.paycheck_calculator.Earnings.gross_biweekly`
        for the resolved profile + period.  Returns ``Decimal("0")``
        when the user has no active salary profile or no pay period
        covers ``as_of`` -- both pre-fix call sites returned
        ``Decimal("0")`` for the missing-profile branch, so the
        substitute preserves the contract.
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
    current_period = pay_period_service.get_current_period(
        user_id, as_of=as_of_date,
    )
    if current_period is None:
        return ZERO

    all_periods = pay_period_service.get_all_periods(user_id)
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
    """What the owner's active salary profiles pay, per template and period.

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

    **The derivation is LAZY**, so a read pass whose rows hold no paycheck pays
    nothing: :func:`salary_net_for` answers ``None`` from the row's own columns
    before it touches :attr:`net_by_template_period`.  That is the "no query
    when there are no candidates" property the row-set producer had, kept rather
    than traded away for the sharing.
    """

    def __init__(self, user_id: int, scenario_id: int) -> None:
        """Pin the owner and scenario; resolve nothing yet.

        Args:
            user_id: The owner whose active profiles price these rows.
            scenario_id: The scenario to resolve profiles against.
        """
        self._user_id = user_id
        self._scenario_id = scenario_id
        self._net: "dict[tuple[int, int], Decimal] | None" = None

    @property
    def net_by_template_period(self) -> dict[tuple[int, int], Decimal]:
        """``{(template_id, pay_period_id): net pay}``, resolved on first read.

        Covers every period each active profile's projection reaches.  A
        template named by no active profile in this scenario is absent, and so
        is a period the projection does not cover -- both are the refusals rule
        2 raises rather than substituting a stored figure.
        """
        if self._net is None:
            self._net = _resolve_salary_net(self._user_id, self._scenario_id)
        return self._net


def salary_pricing(user_id: int, scenario_id: int) -> SalaryPricing:
    """Return the read pass's :class:`SalaryPricing` for an owner and scenario.

    The named constructor the amount model calls, so no caller reaches for the
    class directly and the two pins are always supplied together.  Resolves
    nothing: the projection behind it is lazy, so a pass that prices no paycheck
    issues no query.

    Args:
        user_id: The owner whose profiles price these rows.
        scenario_id: The scenario to resolve profiles against.

    Returns:
        The unresolved :class:`SalaryPricing` handle.
    """
    return SalaryPricing(user_id, scenario_id)


def _resolve_salary_net(
    user_id: int, scenario_id: int,
) -> dict[tuple[int, int], Decimal]:
    """Resolve what every active salary profile pays, per template and period.

    The owner-and-scenario-scoped derivation behind
    :func:`salary_net_for` and :func:`live_projected_net`.  Runs
    :func:`paycheck_calculator.project_salary` once per active profile over the
    owner's full pay-period set -- required, not an optimisation: the biweekly
    residue reconciliation anchors against the complete annual figure, exactly as
    the salary projection page does.  Tax configs resolve PER period year
    (DH-#30), the same per-year resolution the recurrence engine uses to GENERATE
    the stored amount, so the live figure and the generated one cannot disagree
    for want of a bracket set.

    **It loads every active profile rather than only those a caller's rows
    name**, which is the row-set independence this split exists for.  The
    figures are identical either way -- ``project_salary`` never reads the
    caller's rows -- so the only difference is that a second row set in the same
    read pass now costs nothing.

    **The profile query is ORDERED, and it was not before.**  Two active profiles
    naming ONE template in one scenario is expressible (nothing constrains it),
    and the map this builds keeps the last writer.  Unordered, that was whichever
    row the planner reached first, so one owner could be priced two ways across
    two requests; ordering by id makes the collision resolve the same way every
    time.  The collision itself is finding **N-294**, reported rather than fixed
    here: which profile SHOULD win is a question for the salary arc, and
    answering it inside a reader refactor would be an unreviewed ruling.

    Args:
        user_id: The owner whose profiles to resolve; also scopes the
            pay-period set the projection runs over.
        scenario_id: The scenario to resolve profiles against -- a profile
            drives income only within its own scenario.

    Returns:
        ``{(template_id, pay_period_id): net pay}``.  Empty when the owner has
        no active profile in the scenario, which costs one indexed query and no
        projection.
    """
    profiles = (
        db.session.query(SalaryProfile)
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.scenario_id == scenario_id,
            SalaryProfile.is_active.is_(True),
        )
        .order_by(SalaryProfile.id)
        .all()
    )
    if not profiles:
        return {}

    all_periods = pay_period_service.get_all_periods(user_id)
    net_by_template_period: dict[tuple[int, int], Decimal] = {}
    for profile in profiles:
        configs_by_year = load_tax_configs_for_periods(
            user_id, profile, all_periods,
        )
        breakdowns = paycheck_calculator.project_salary(
            profile, all_periods, configs_by_year=configs_by_year,
            calibration=profile.calibration,
        )
        for breakdown in breakdowns:
            net_by_template_period[
                (profile.template_id, breakdown.period.period_id)
            ] = breakdown.earnings.net_pay
    return net_by_template_period


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
    return pricing.net_by_template_period.get(
        (txn.template_id, txn.pay_period_id),
    )


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
