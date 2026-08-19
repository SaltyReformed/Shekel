"""
Shekel Budget App -- Investment Projection Input Calculator

Pure function that computes all inputs needed for growth_engine.project_balance()
from raw deduction, contribution, and investment params data.

Used by both the investment detail route and the savings dashboard to avoid
duplicating contribution/employer/YTD calculation logic.

Contributions are derived from shadow income transactions (transfer_id IS NOT
NULL) in the investment/retirement account.  The caller queries these
transactions and passes them in; this module has no database access.

**They arrive PRICED, as :class:`PricedContribution` records rather than ORM
rows** (plan step X-au-c2, a developer ruling of 2026-08-12).  Four readers here
used to ask each row for its ``effective_amount`` and screen it with
``status_contributes_to_balance`` -- a model property that cannot answer for a
row whose amount is DERIVED, since such a row stores no figure and resolving one
needs a database this module deliberately does not have.  Valuing at the
BOUNDARY instead (``projection_inputs.load_shadow_income_contributions_*``)
resolves the whole row set ONCE, drops the rows that contribute nothing, and
retires all four copies of the status screen with them.  What is left here is
arithmetic over plain data, which is what the paragraph above always claimed.

**They arrive DATED too, since plan step C2-f2c**, and for the same reason one
tier down.  A contribution's pay period was carried here as an id, so the three
readers that needed to know WHEN it landed took the owner's whole period list
as an argument and looked the payday up in it -- a join table threaded through
a public signature to answer a question the loader can answer once, where the
session is.  ``calculate_investment_inputs`` and
:func:`build_contribution_timeline` are the readers; neither takes a period id
now, and the period list left the first of them outright.  It also ended a
shape collision this module could not have absorbed otherwise: it is shared by
``/retirement``, which holds ORM rows spelling that key ``id``, and by
``/investment``, which since C2-f2c holds
:class:`~app.services.pay_calendar.DerivedPeriod`\\ s spelling it ``period_id``.
"""

from collections import namedtuple
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.enums import CalcMethodEnum, EmployerContributionTypeEnum
from app.services.growth_engine import ContributionRecord
from app.services.pay_calendar import PayCadence
from app.utils.deduction_cap import cap_period_amount
from app.utils.money import ZERO, round_money


@dataclass(frozen=True)
class PricedContribution:
    """ONE shadow contribution, already valued, screened and DATED.

    The boundary record this module consumes in place of a
    :class:`~app.models.transaction.Transaction` (plan step X-au-c2).  It
    carries exactly the four facts the readers here need, so a row's amount is
    resolved once by the loader that has a session rather than four times by
    functions that do not.

    **Non-contributing rows are ABSENT rather than zero, and that is load
    bearing rather than tidy.**  :func:`_average_transfer_contribution` divides
    by the number of DISTINCT paydays it sees, so a Cancelled contribution
    carried as ``$0.00`` would enlarge that denominator and quietly reduce the
    average -- where the ``status_contributes_to_balance`` screen it replaces
    dropped the row before the count.  The loader applies that screen and omits
    what fails it.

    **It carries the PAYDAY rather than the ``pay_period_id`` since plan step
    C2-f2c**, and that is what let the period LIST leave this module's public
    surface.  Every reader here bucketed on the id and then needed a lookup
    table to find out WHEN that period was: the YTD windows built a set of ids
    filtered by each period's ``start_date``, and the timeline built an
    id-keyed map to stamp each record with one.  So the period list was an
    argument three functions took to answer one question the loader can answer
    once, where the session is -- the same move plan step X-au-c2 made for the
    amount and the status.  It also ended a shape collision: this module is
    shared with ``/retirement``, which holds ORM rows spelling that key ``id``,
    while ``/investment`` now holds
    :class:`~app.services.pay_calendar.DerivedPeriod`\\ s spelling it
    ``period_id``.

    A payday identifies a period as exactly as the id does: paydays are unique
    per owner (``uq_pay_periods_user_start``) and every batch the loader builds
    is scoped to one.

    Attributes:
        account_id: The investment / retirement account the contribution
            landed in.  Read by the cross-account consumers, which load one
            batch and partition it per account.
        payday: The ``start_date`` of the pay period the contribution belongs
            to -- the day every average, YTD sum and timeline record here dates
            it at.  It is a PAYDAY rather than a posting date on purpose: the
            growth engine matches a contribution to a period by that period's
            opening day.
        amount: What the row CONTRIBUTES
            (:func:`app.services.cash_ledger.contributions_by_id`): the entered
            ``actual_amount`` where a human read one off a statement, else the
            row's resolved amount.
        is_confirmed: Whether the contribution actually happened
            (``status.is_settled``), as opposed to being still projected.  The
            growth engine's :class:`~app.services.growth_engine.ContributionRecord`
            takes it verbatim.
    """

    account_id: int
    payday: date
    amount: Decimal
    is_confirmed: bool


@dataclass(frozen=True)
class ShadowContributions:
    """A batch of priced contributions, and WHICH accounts had any at all.

    Two facts that must travel together, because screening the first destroys
    the second and one consumer needs each (plan step X-au-c2).

    **The second field exists because an adversarial review caught the screen
    silently answering a different question.**  ``retirement_projection``'s
    ``none_linked`` is a PRESENCE test -- *is anything linked to fund this
    account?* -- and it read the loader's list length.  Before the screen moved
    to the boundary that list carried Cancelled and Credit rows, so an account
    whose contributions were all cancelled reported ``you $0.00 / employer
    $0.00``; screening them out at the loader would have flipped it to the
    "nothing linked yet" call-to-action, telling the owner to link a
    contribution that already exists.

    Separating them is the correct design rather than a compatibility shim:
    *what does this account receive* and *is anything wired up to it* are
    different questions, and conflating them is exactly what let one change
    answer the second while only meaning to change the first.

    Attributes:
        records: The contributions that COUNT -- screened by
            ``status_contributes_to_balance`` and priced through the amount
            model.  Cancelled and Credit rows are absent (see
            :class:`PricedContribution` on why absent rather than zero).
        linked_account_ids: Every account id that had a contribution shadow in
            the window, WHATEVER its status.  A cancelled contribution is still
            a link.
    """

    records: list[PricedContribution]
    linked_account_ids: frozenset[int]


@dataclass
class InvestmentInputs:
    """All inputs needed for growth_engine.project_balance().

    ``ytd_contributions`` and ``ytd_contributions_seed`` are two YTD views
    of the same contribution stream that differ only on the current period
    (deep-quality-hunt #10):

    * ``ytd_contributions`` -- contributions this calendar year *through*
      the current period (``<=``).  This is the displayed limit-card value.
    * ``ytd_contributions_seed`` -- contributions this calendar year
      *strictly before* the current period (``<``).  This is the
      ``ytd_contributions_start`` handed to the growth engine, whose own
      per-period walk then applies and counts the current period's
      contribution against the limit.  Seeding the through-current value
      instead would charge the current period against the annual limit
      twice.  The two views converge at the engine's current-period row.
    """
    periodic_contribution: Decimal
    employer_params: Optional[dict]
    annual_contribution_limit: Optional[Decimal]
    ytd_contributions: Decimal
    ytd_contributions_seed: Decimal
    gross_biweekly: Decimal


AdaptedDeduction = namedtuple(
    "AdaptedDeduction",
    ["amount", "calc_method_id", "periods_per_year", "annual_cap"],
)


def adapt_deductions(
    raw_deductions: list, cadence: PayCadence,
) -> list[AdaptedDeduction]:
    """Adapt PaycheckDeduction ORM objects for calculate_investment_inputs().

    Extracts the fields needed from each deduction into lightweight
    namedtuples with no ORM dependency.  This decouples the projection logic
    from the database layer and consolidates the adaptation pattern previously
    duplicated across year_end_summary_service, savings_dashboard_service, and
    retirement_dashboard_service.

    **It stopped carrying the parent profile's salary at plan step R-F16, and
    that was a MONEY fix rather than a tidy-up.**  Each row used to carry
    ``annual_salary`` and ``pay_periods_per_year`` so
    :func:`_compute_deduction_per_period` could recompute a gross as
    ``annual_salary / pay_periods_per_year`` -- which is precisely the
    "off-engine recompute that silently dropped any applicable SalaryRaise"
    that F-20 / MED-06 / F-032 replaced everywhere else, still live here.  The
    caller's raise-aware gross was consulted only as a FALLBACK when no
    deduction supplied one, so an account WITH a deduction sized its employer
    match off the raise-blind figure: measured at ``$3,525.96`` against a true
    ``$3,631.74`` on the developer's own profile, understating a 5% employer
    contribution by ``$137.51`` a year.  The gross is now an argument
    resolved ONCE by the caller (:func:`deduction_contribution_per_period`,
    :func:`build_contribution_timeline`) and never recomputed here.

    ``periods_per_year`` stays on the row because
    :func:`_annual_cap_averaged` spreads a calendar-year cap across the year's
    paychecks and genuinely needs the count.  It is stamped from the ONE
    cadence this call is given, so every row carries the same number by
    construction -- a copy of one fact, not a second source of it.

    Args:
        raw_deductions: List of PaycheckDeduction ORM objects.
        cadence: The owner's :class:`~app.services.pay_calendar.PayCadence`.
            Required: how many paychecks a year the cap is spread over is a
            fact about the OWNER, and defaulting it to biweekly would spread a
            weekly-paid owner's cap over half the paychecks they receive.

    Returns:
        List of AdaptedDeduction namedtuples ready for
        calculate_investment_inputs() or build_contribution_timeline().
    """
    periods_per_year = cadence.periods_per_year
    return [
        AdaptedDeduction(
            amount=ded.amount,
            calc_method_id=ded.calc_method_id,
            periods_per_year=periods_per_year,
            annual_cap=ded.annual_cap,
        )
        for ded in raw_deductions
    ]


def _compute_deduction_per_period(deduction, gross_biweekly, pct_id):
    """Compute the per-period contribution amount from a single deduction.

    Handles flat-dollar and percentage-of-salary calculation methods.
    Shared by calculate_investment_inputs() and build_contribution_timeline()
    to keep the deduction amount logic in one place (DRY).

    Args:
        deduction:      Object with .amount and .calc_method_id.
        gross_biweekly: The owner's RAISE-AWARE gross per pay period, which a
                        percentage deduction takes its percentage of.  Taken
                        rather than recomputed since plan step R-F16 -- see
                        :func:`adapt_deductions` for what the recompute cost.
        pct_id:         The ref ID for the PERCENTAGE calculation method.

    Returns:
        The per-period contribution amount (Decimal).
    """
    amt = Decimal(str(deduction.amount))
    if deduction.calc_method_id == pct_id:
        amt = round_money(gross_biweekly * amt)
    return amt


def _annual_cap_averaged(per_period_amount, deduction):
    """Per-period amount evenly throttled to the deduction's annual cap.

    The periodic contribution is the growth engine's fallback for periods with
    no dated ``ContributionRecord`` -- in practice the projected long-horizon
    chart, whose generated dates never match a real period.  A capped deduction
    must not contribute more than ``annual_cap`` per calendar year there either,
    so the per-period amount is the cap spread evenly across the year:
    ``min(amount * ppy, annual_cap) / ppy``.  This even-spread is the
    long-horizon analogue of the front-loaded per-period timeline
    (:func:`_deduction_contribution_records`): both hold the annual total at the
    cap and differ only in WITHIN-year timing, which a multi-year projection
    does not surface.  ``annual_cap`` is read via ``getattr`` so a minimal
    deduction-like fake (no cap field) is treated as uncapped.

    Args:
        per_period_amount: Decimal uncapped per-period contribution.
        deduction:         The deduction-like object (.periods_per_year,
                           optionally .annual_cap).

    Returns:
        The capped per-period amount (Decimal); unchanged when uncapped.
    """
    annual_cap = getattr(deduction, "annual_cap", None)
    if annual_cap is None:
        return per_period_amount
    pay_per_year = deduction.periods_per_year
    annual_capped = min(per_period_amount * pay_per_year, Decimal(str(annual_cap)))
    return round_money(annual_capped / pay_per_year)


def deduction_contribution_per_period(deductions, salary_gross_biweekly):
    """Sum the per-period contribution from paycheck deductions.

    Each deduction's per-period amount is throttled to its calendar-year
    ``annual_cap`` via :func:`_annual_cap_averaged` (deep-hunt #2) before
    summing, so this fallback average respects the same cap the per-period
    timeline enforces.

    **The DEDUCTION half of a contribution feed, on its own.**  Public
    because the balance seam's modelled asset fold
    (``balance_at._asset_fold``) needs exactly this and NOT the transfer
    half: plan ruling R-R partitions a contribution by SOURCE, so a
    recorded transfer is an ACTUAL / PLANNED event in the fold (it has a
    transaction row) while a payroll deduction is a modelled
    CONTRIBUTION event (it never has one).  Mixing the two into one
    scalar -- which is what :func:`calculate_investment_inputs` does by
    adding :func:`_average_transfer_contribution` to this -- is precisely
    what makes them indistinguishable, so the replay reads this half
    directly rather than the sum.

    Args:
        deductions:            List of deduction-like objects with
                               .amount, .calc_method_id, .periods_per_year,
                               and optionally .annual_cap.
        salary_gross_biweekly: The owner's RAISE-AWARE engine gross per pay
                               period (Decimal or None) -- the basis a
                               percentage deduction takes its percentage of,
                               and the figure the employer match is sized on.
                               ``None`` and ZERO both mean the owner has no
                               resolvable current paycheck, which is what
                               ``income_service.get_current_gross_biweekly``
                               answers for an owner with no active profile or
                               no period covering the day; a percentage
                               deduction then contributes nothing, because
                               there is no paycheck to take a percentage of.

    Returns:
        Tuple of (periodic_contribution: Decimal, gross_biweekly: Decimal).

    **The gross is the caller's, not a recompute, since plan step R-F16.**  It
    was consulted only as a fallback for the no-deduction case while an
    account WITH a deduction used ``annual_salary / pay_periods_per_year``
    off the row -- raise-blind, and the basis of the employer match.  The two
    diverge by every raise the owner has taken.  It also means one gross for
    the owner rather than whichever deduction happened to be iterated last.
    """
    pct_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
    gross_biweekly = (
        ZERO if salary_gross_biweekly is None
        else Decimal(str(salary_gross_biweekly))
    )
    periodic_contribution = sum(
        (
            _annual_cap_averaged(
                _compute_deduction_per_period(ded, gross_biweekly, pct_id), ded,
            )
            for ded in deductions
        ),
        ZERO,
    )
    return periodic_contribution, gross_biweekly


def _average_transfer_contribution(all_contributions):
    """Average per-period contribution from priced shadow contributions.

    ``all_contributions`` are :class:`PricedContribution` records already
    filtered to one account by the caller.  Cancelled / Credit rows never reach
    here: the loader that priced them applied the
    ``status_contributes_to_balance`` screen and omitted what failed it, so this
    module holds no copy of that rule (plan step X-au-c2).  The screen still
    shares ONE definition with the SQL filters in ``year_end_summary_service`` /
    ``savings_dashboard_service`` -- it just lives at the boundary now.

    Contributions are summed on the record's :attr:`~PricedContribution.amount`
    -- the realized actual when a shadow is settled, else what the row's amount
    RESOLVES to -- which is the same figure the per-period timeline reads off
    the same records, so this average and the YTD/limit accounting cannot
    disagree with the engine on a settled transfer whose actual differs from its
    estimate (deep-quality-hunt #11).

    Args:
        all_contributions: List of :class:`PricedContribution` records.

    Returns:
        The per-period average contribution (Decimal), or ZERO when no
        contributions exist.
    """
    if not all_contributions:
        return ZERO

    total_contrib = sum(c.amount for c in all_contributions)
    # DISTINCT PAYDAYS, which is distinct periods: a payday is unique per owner
    # (``uq_pay_periods_user_start``) and one batch is one owner's.  It read
    # ``pay_period_id`` until plan step C2-f2c moved the period key onto the
    # record's own date; the denominator is the same set either way.
    num_periods_with_contrib = len(
        set(c.payday for c in all_contributions)
    )
    if num_periods_with_contrib > 0:
        return round_money(total_contrib / num_periods_with_contrib)
    return ZERO


def employer_contribution_params(investment_params, gross_biweekly):
    """Build the employer-contribution params dict, or None.

    Public alongside :func:`deduction_contribution_per_period` and for the
    same reason: the balance seam's modelled asset fold
    (``balance_at._asset_fold``) sizes the employer amount per pay period
    off the RESOLVED employee total for that period (plan ruling R-R
    consequence (a)), so it needs this dict without the transfer-averaged
    ``periodic_contribution`` :func:`calculate_investment_inputs` bundles
    it with.  It is the only shape
    :func:`~app.services.growth_engine.calculate_employer_contribution`
    accepts, so building it anywhere else would be a second statement of
    the same mapping.

    Args:
        investment_params: Object with ``employer_contribution_type_id``
                           and the ``employer_*_percentage`` fields.
        gross_biweekly:    Engine gross per pay period (Decimal), embedded
                           so the growth engine can size a
                           percentage-of-gross employer match.

    Returns:
        A dict describing the employer contribution, or None when the
        account has no employer contribution configured.  The dict
        carries the employer-type ref id under ``type_id`` (#38) so the
        growth engine branches on the id, not a string.
    """
    emp_type_id = getattr(investment_params, "employer_contribution_type_id", None)
    none_id = ref_cache.employer_contribution_type_id(
        EmployerContributionTypeEnum.NONE
    )
    if emp_type_id is None or emp_type_id == none_id:
        return None
    return {
        "type_id": emp_type_id,
        "flat_percentage": getattr(
            investment_params, "employer_flat_percentage", None) or ZERO,
        "match_percentage": getattr(
            investment_params, "employer_match_percentage", None) or ZERO,
        "match_cap_percentage": getattr(
            investment_params, "employer_match_cap_percentage", None) or ZERO,
        "gross_biweekly": gross_biweekly,
    }


def _ytd_contributions(all_contributions, current_period, *, inclusive):
    """Sum this calendar year's contributions up to the current period.

    ``inclusive`` controls the current period itself: ``True`` keeps it
    (``<=``, the through-current YTD shown on the limit card); ``False``
    drops it (``<``, the strictly-before seed handed to the growth engine,
    whose per-period walk then applies and counts the current period's own
    contribution against the annual limit -- seeding the through-current value
    there would charge that period twice, deep-quality-hunt #10).  ONE
    expression for both keeps them from drifting.

    Both bounds read the current period's own ``start_date``, so the year and
    the boundary are one fact rather than two: a contribution counts when its
    PAYDAY falls in that period's calendar year at or before that period's
    payday.  **It used to build a set of period IDS and match each record's
    ``pay_period_id`` against it**, which needed the owner's whole period list
    as an argument; the record carries its payday since plan step C2-f2c, so
    the list has nothing left to answer (see :class:`PricedContribution`).  The
    two select identically -- every record the loader returns belongs to a
    period in that list, because the list is what scoped the query.

    Every record here has already passed the boundary's
    ``status_contributes_to_balance`` screen, so this sums what it is given.
    :attr:`~PricedContribution.amount` -- the realized actual when a shadow is
    settled, else what its amount resolves to -- is the ONE answer to what a row
    contributes, so this YTD/limit accounting agrees with the per-period
    timeline (:func:`build_contribution_timeline`, reading the same records)
    once a transfer shadow is settled with an actual that differs from its
    estimate (deep-quality-hunt #11).  Summing ``estimated_amount`` here
    previously let the cap/limit math read a different dollar than the engine
    actually applied; the prior "F-027 S18 contract-safe" rationale assumed a
    shadow's ``actual_amount`` is always ``None``, which is untrue once a settle
    sets it (the ``Transfer`` parent has no ``actual_amount`` column, so a
    settled actual lives only on the shadows).

    Args:
        all_contributions: :class:`PricedContribution` records for one account.
        current_period:    The current period object -- anything carrying a
                           ``start_date`` -- or None.
        inclusive:         Keyword-only; include the current period or not.

    Returns:
        The contribution total (Decimal); ZERO when ``current_period`` is None,
        the state in which there is no year and no boundary to ask about.
    """
    if current_period is None:
        return ZERO
    boundary = current_period.start_date
    return sum(
        (
            c.amount for c in all_contributions
            if c.payday.year == boundary.year
            and (c.payday <= boundary if inclusive else c.payday < boundary)
        ),
        ZERO,
    )


def calculate_investment_inputs(
    investment_params,
    deductions,
    all_contributions,
    current_period,
    salary_gross_biweekly=None,
):
    """Compute projection inputs for an investment account.

    **It stopped taking the owner's period LIST at plan step C2-f2c.**  The
    list served the two YTD windows alone, as a lookup from a contribution's
    ``pay_period_id`` to that period's payday; the loader that prices a
    contribution now dates it too, so there is no lookup left to do and one
    argument fewer for a caller to get wrong.  That also retired this
    function's ``too-many-arguments`` disable rather than re-justifying it.

    Args:
        investment_params:     Object with employer fields and
                               ``annual_contribution_limit``.
        deductions:            List of deduction-like objects with
                               .amount, .calc_method_id, .periods_per_year,
                               and optionally .annual_cap.
        all_contributions:     List of :class:`PricedContribution` records
                               for this account -- shadow-income rows already
                               valued, screened and dated at the boundary.
        current_period:        The current period object -- anything carrying a
                               ``start_date``, which both
                               :class:`~app.models.pay_period.PayPeriod` and
                               :class:`~app.services.pay_calendar.DerivedPeriod`
                               do -- or None.
        salary_gross_biweekly: The owner's RAISE-AWARE engine gross per pay
                               period (Decimal or None).  Since plan step
                               R-F16 it is the ONLY gross in this path -- the
                               basis a percentage deduction takes its
                               percentage of and the figure the employer match
                               is sized on -- rather than a fallback behind an
                               off-engine recompute.

    Returns:
        InvestmentInputs dataclass.
    """
    periodic_contribution, gross_biweekly = deduction_contribution_per_period(
        deductions, salary_gross_biweekly,
    )
    periodic_contribution += _average_transfer_contribution(all_contributions)

    return InvestmentInputs(
        periodic_contribution=periodic_contribution,
        employer_params=employer_contribution_params(
            investment_params, gross_biweekly,
        ),
        annual_contribution_limit=getattr(
            investment_params, "annual_contribution_limit", None),
        ytd_contributions=_ytd_contributions(
            all_contributions, current_period, inclusive=True),
        ytd_contributions_seed=_ytd_contributions(
            all_contributions, current_period, inclusive=False),
        gross_biweekly=gross_biweekly,
    )


def _deduction_contribution_records(
    deductions, periods, gross_biweekly, pct_id, as_of,
):
    """Per-period deduction ContributionRecords, each clamped to its annual cap.

    Deductions contribute the same raw amount every period; each is clamped to
    its own calendar-year ``annual_cap`` (deep-hunt #2) through the shared
    ``cap_period_amount`` so this timeline agrees with the net-pay path.  Cap
    state is tracked per deduction and resets at each year boundary, mirroring
    the growth engine's own year reset.

    A record is emitted for every covered period -- even a fully-capped $0 --
    so the growth engine applies the capped amount rather than the
    periodic-average fallback a missing record would trigger.  ``annual_cap`` is
    read via ``getattr`` so a minimal deduction-like fake (no cap field) is
    treated as uncapped.

    Args:
        deductions:     Deduction-like objects (see build_contribution_timeline).
        periods:        Period objects with .start_date.
        gross_biweekly: The owner's raise-aware gross per pay period, which a
                        percentage deduction takes its percentage of (plan step
                        R-F16).
        pct_id:         The ref ID for the PERCENTAGE calculation method.
        as_of:          The read pass's clock -- the day splitting confirmed
                        (past) from projected periods.

    Returns:
        list[ContributionRecord] in period-start-date order; empty when no
        deduction contributes a positive amount.
    """
    deduction_raws = [
        (_compute_deduction_per_period(d, gross_biweekly, pct_id),
         getattr(d, "annual_cap", None))
        for d in deductions
    ]
    if not any(raw > ZERO for raw, _ in deduction_raws):
        return []

    # (year, raw_cumulative) per deduction; None until its first period.
    cap_state = [None] * len(deduction_raws)
    records = []
    for period in sorted(periods, key=lambda p: p.start_date):
        period_total, cap_state = _period_capped_total(
            deduction_raws, cap_state, period.start_date.year,
        )
        records.append(ContributionRecord(
            contribution_date=period.start_date,
            amount=period_total,
            # Past periods are confirmed (the deduction was taken from the
            # paycheck); future periods are projected.
            is_confirmed=period.start_date < as_of,
        ))
    return records


def _period_capped_total(deduction_raws, cap_state, period_year):
    """Return ONE period's capped deduction total, and the advanced cap state.

    The per-period half of :func:`_deduction_contribution_records`, split out
    so each function answers one question: this one is "what does this period
    contribute, and what does that leave the running cap at", the caller's is
    "which periods get a record".  Each deduction's clamp is
    ``cap_period_amount`` -- the same one the net-pay path applies -- against
    its own calendar-year cumulative, which RESETS when the year changes
    because a state stamped with a different year reads as no cumulative at
    all.

    Returns a NEW state list rather than mutating the caller's: the caller
    rebinds it each period, so the running total is threaded rather than
    hidden in a shared mutable the two functions would both have to remember
    the rules for.

    Args:
        deduction_raws: ``(raw_amount, annual_cap)`` per deduction, in a fixed
            order the state list is indexed by.
        cap_state: ``(year, raw_cumulative)`` per deduction, or ``None`` for a
            deduction that has not contributed yet.
        period_year: The calendar year of the period being valued.

    Returns:
        ``(period_total, advanced_state)`` -- the capped sum across every
        deduction, and the state to value the next period against.
    """
    total = ZERO
    advanced = list(cap_state)
    for i, (raw, annual_cap) in enumerate(deduction_raws):
        if raw <= ZERO:
            continue
        prior = cap_state[i]
        cumulative_before = (
            prior[1] if prior is not None and prior[0] == period_year
            else ZERO
        )
        total += cap_period_amount(raw, cumulative_before, annual_cap)
        advanced[i] = (period_year, cumulative_before + raw)
    return total, advanced


def build_contribution_timeline(
    deductions,
    contribution_transactions,
    periods,
    gross_biweekly,
    as_of,
):
    """Build ContributionRecords from deductions and shadow transactions.

    Combines two contribution paths into a unified per-period timeline
    for the growth engine:

    Path 1 -- Paycheck deductions: The same raw amount every period, each
    clamped to its own calendar-year ``annual_cap`` (deep-hunt #2) so this
    timeline agrees with the net-pay path.  Confirmation is date-based (past
    period = confirmed) because there is no per-period transaction record for
    deductions.

    Path 2 -- Transfer-based contributions: Per-record amounts from the priced
    shadow contributions.  Confirmation is status-based
    (:attr:`PricedContribution.is_confirmed`, resolved from
    ``status.is_settled`` at the boundary) -- factual from the transaction
    workflow.

    The growth engine handles same-date aggregation (summing amounts,
    conservative is_confirmed rule) via its lookup dict.

    **It reads no clock and needs no period IDENTITY since plan step C2-f2c.**
    The confirmation split took ``date.today()``, so a render that straddled
    midnight could date this timeline one day and the pass around it another;
    it takes the read pass's own ``as_of`` now, which is what every other
    producer on that render already runs on.  And path 2 resolved each
    contribution's date through an id-keyed map of *periods*, which forced this
    function to know how the caller's period type spells its primary key --
    ``id`` on an ORM row, ``period_id`` on a
    :class:`~app.services.pay_calendar.DerivedPeriod`.  A contribution carries
    its own payday now, so the only thing read off a period here is its
    ``start_date`` and both types serve.

    Args:
        deductions:                 List of deduction-like objects with
                                    .amount, .calc_method_id, and optionally
                                    .annual_cap (the calendar-year ceiling;
                                    absent = uncapped).
        contribution_transactions:  List of :class:`PricedContribution`
                                    records -- shadow-income rows already
                                    valued, screened and dated at the boundary.
        periods:                    The timeline's DOMAIN: period objects with
                                    a ``start_date``, one record emitted per
                                    period for the deduction path and any
                                    contribution outside them dropped.
        gross_biweekly:             The owner's raise-aware gross per pay
                                    period, which a percentage deduction takes
                                    its percentage of.  Taken rather than
                                    recomputed off the deduction's parent
                                    profile since plan step R-F16.
        as_of:                      The read pass's clock; a period opening
                                    strictly before it is confirmed.

    Returns:
        list[ContributionRecord] sorted by contribution_date.  Empty
        list if no deductions and no qualifying contributions exist.
    """
    records = []
    pct_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)

    # Path 1: Paycheck deductions -- same raw amount every period, each
    # clamped to its own calendar-year cap.
    records.extend(
        _deduction_contribution_records(
            deductions, periods, gross_biweekly, pct_id, as_of,
        )
    )

    # Path 2: Transfer-based contributions -- per-transaction amounts.
    paydays = {p.start_date for p in periods}
    for contribution in contribution_transactions:
        if contribution.payday not in paydays:
            # Contribution in a period outside this timeline's domain.
            continue
        records.append(ContributionRecord(
            contribution_date=contribution.payday,
            amount=contribution.amount,
            # Transfer-based: determined by the row's settlement status
            # (Paid/Settled=True, Projected=False), resolved at the boundary.
            is_confirmed=contribution.is_confirmed,
        ))

    records.sort(key=lambda r: r.contribution_date)
    return records
