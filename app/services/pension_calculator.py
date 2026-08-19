"""
Shekel Budget App -- Pension Calculator Service

Pure function service that calculates defined-benefit pension income
based on years of service, salary projection, and a benefit multiplier.

All functions are pure (no DB access) -- data is passed in as arguments.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app import ref_cache
from app.enums import RaiseTypeEnum
from app.services.salary_raises import apply_raises
from app.utils.money import round_money

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


@dataclass
class PensionBenefit:
    """Result of a pension benefit calculation."""
    years_of_service: Decimal
    high_salary_average: Decimal
    annual_benefit: Decimal
    monthly_benefit: Decimal
    high_salary_years: list = field(default_factory=list)  # [(year, salary)]


@dataclass(frozen=True)
class _HorizonRaise:
    """A recurring cola-type raise re-anchored to the post-cutoff horizon.

    The minimal raise-like value
    :func:`app.services.salary_raises.apply_raises` consumes: that
    function reads only ``effective_year``, ``effective_month``,
    ``is_recurring``, ``percentage``, and ``flat_amount`` (never
    ``raise_type`` -- that is display-only, read by the badge helper).
    Used by :func:`project_salaries_by_year` to compound post-cutoff cola
    raises forward from the cutoff salary without re-applying the
    occurrences already baked into that base.  Passing a plain raise-like
    object is the supported contract for ``apply_raises`` (deep-hunt #83);
    it is not reaching into a private symbol.
    """

    effective_year: int
    effective_month: int
    is_recurring: bool
    percentage: Decimal | None
    flat_amount: Decimal | None


def calculate_benefit(benefit_multiplier, consecutive_high_years,
                      hire_date, planned_retirement_date,
                      salary_by_year):
    """Calculate the projected pension benefit.

    Args:
        benefit_multiplier:      Decimal per-year multiplier (e.g. 0.0185 for 1.85%).
        consecutive_high_years:  int -- number of consecutive highest salary years to average.
        hire_date:               date -- employment start date.
        planned_retirement_date: date -- planned retirement date.
        salary_by_year:          list of (year, annual_salary) tuples, sorted by year.

    Returns:
        PensionBenefit dataclass.
    """
    benefit_multiplier = Decimal(str(benefit_multiplier))
    years_of_service = _calculate_years_of_service(hire_date, planned_retirement_date)

    if not salary_by_year:
        return PensionBenefit(
            years_of_service=years_of_service,
            high_salary_average=ZERO,
            annual_benefit=ZERO,
            monthly_benefit=ZERO,
        )

    high_avg, high_years = _compute_high_salary_average(
        salary_by_year, consecutive_high_years
    )

    annual_benefit = round_money(
        benefit_multiplier * years_of_service * high_avg
    )

    monthly_benefit = round_money(annual_benefit / 12)

    return PensionBenefit(
        years_of_service=years_of_service,
        high_salary_average=high_avg,
        annual_benefit=annual_benefit,
        monthly_benefit=monthly_benefit,
        high_salary_years=high_years,
    )


def project_salaries_by_year(
    annual_salary, raises, start_year, end_year, merit_horizon_years,
):
    """Project annual salary for each year in a range, honoring the merit horizon.

    Delegates each year's salary to the shared
    :func:`app.services.salary_raises.apply_raises` so pension
    projections and the paycheck pipeline apply the identical raise rule
    (sort order, recurring compounding, one-time gating).

    Merit horizon (Gate A ruling 3 / fork F4).  Let ``cutoff = start_year
    + merit_horizon_years`` -- the caller passes the current year as
    ``start_year``, so this is "current year + N".

    * Through the cutoff year (inclusive) every raise applies exactly as
      the paycheck pipeline would.
    * After the cutoff, only ``cola``-type recurring raises keep
      compounding, and they compound forward from the CUTOFF salary --
      the merit-type and custom-type effect earned through the cutoff
      persists in that base, but no further merit/custom raises are
      applied.  This is implemented by re-anchoring the recurring cola
      raises to ``cutoff + 1`` (:func:`_reanchor_recurring_cola`) so a
      single ``apply_raises`` call over the cutoff salary compounds ONLY
      their post-cutoff occurrences (their pre-cutoff history is already
      in the base).

    Raise-type discrimination is by ``raise_type_id`` via the ref cache,
    never the display ``name`` string.

    Known split-at-cutoff artifact (review L4, documented not changed):
    with MIXED flat + percentage recurring COLAs the horizon is not
    invariant even though both raises extrapolate -- ``apply_raises``
    applies flats before percents (M-01), so compounding in two phases
    (through the cutoff, then from the cutoff base) interleaves the flat
    additions with the percentage compounding differently than one
    continuous pass (pinned:
    ``test_mixed_flat_and_percentage_colas_pinned_not_horizon_invariant``).
    A one-time cola raise dated after the cutoff is dropped by design
    (only RECURRING colas extrapolate); that rule awaits explicit
    developer confirmation before any change.

    Args:
        annual_salary:      Decimal base salary.
        raises:             list of raise objects with .percentage,
                            .flat_amount, .effective_month,
                            .effective_year, .is_recurring, .raise_type_id.
        start_year:         int first year to project (the current year, by
                            caller convention -- both call sites pass
                            ``date.today().year``).
        end_year:           int last year to project (inclusive).
        merit_horizon_years: int >= 0 number of years from ``start_year``
                            (inclusive) that merit-type and custom-type
                            raises keep applying before they stop.

    Returns:
        list of (year, Decimal salary) tuples.
    """
    owned_raises = raises or []
    cutoff_year = start_year + merit_horizon_years

    # The cutoff salary carries the full raise effect (merit + custom +
    # cola) earned through the cutoff year; post-cutoff years compound only
    # the recurring cola raises forward from it.  Re-anchoring those cola
    # raises to ``cutoff_year + 1`` makes ``apply_raises`` count exactly the
    # post-cutoff occurrences (its within-year application gate reaches
    # every effective month at the December-1 evaluation).
    cutoff_salary = apply_raises(
        annual_salary, owned_raises, date(cutoff_year, 12, 1),
    )
    cola_after_cutoff = _reanchor_recurring_cola(owned_raises, cutoff_year + 1)

    result = []
    for year in range(start_year, end_year + 1):
        # Evaluate each year's salary as of December 1 so every raise
        # effective during that year (recurring or one-time) is applied.
        if year <= cutoff_year:
            salary = apply_raises(
                annual_salary, owned_raises, date(year, 12, 1),
            )
        else:
            salary = apply_raises(
                cutoff_salary, cola_after_cutoff, date(year, 12, 1),
            )
        result.append((year, salary))
    return result


def project_profile_salaries(profile, start_year, end_year, merit_horizon_years):
    """Project a salary profile's annual salaries to the retirement horizon.

    Thin convenience over :func:`project_salaries_by_year` that marshals a
    :class:`~app.models.salary_profile.SalaryProfile` (its ``annual_salary``
    and ``raises``) into the plain-input contract.  Shared by the two
    retirement consumers that project the primary profile's salary path --
    the gap-comparison net-biweekly scaling and the P1b employer-base
    resolver -- so the ``Decimal(str(...))`` marshalling and the
    current-year start live in one place.

    Args:
        profile:             A salary profile exposing ``annual_salary`` and
                             ``raises``.
        start_year:          int first year to project (the current year).
        end_year:            int last year to project (inclusive).
        merit_horizon_years: int merit-raise horizon forwarded to
                             :func:`project_salaries_by_year`.

    Returns:
        list of (year, Decimal salary) tuples.
    """
    return project_salaries_by_year(
        Decimal(str(profile.annual_salary)),
        profile.raises,
        start_year,
        end_year,
        merit_horizon_years,
    )


def _reanchor_recurring_cola(raises, anchor_year):
    """Re-anchor recurring cola-type raises to a new effective year.

    Returns a lightweight :class:`_HorizonRaise` for every recurring
    cola-type raise, with ``effective_year`` reset to
    ``max(own effective_year, anchor_year)`` so
    :func:`app.services.salary_raises.apply_raises` compounds each
    one only from *anchor_year* forward -- and never EARLIER than the
    raise's own start (H1: a plain ``anchor_year`` reset pulled a
    future-scheduled COLA backward, applying it in years before it
    exists; a 2031-effective COLA under a 2028 cutoff must first apply
    in 2031, not 2029).  A pre-cutoff COLA's history is already in the
    cutoff base, so the anchor floor is exactly right for it.
    Merit-type raises, custom-type raises, and one-time raises are
    dropped: after the merit horizon only recurring cola raises keep
    applying.  Cola-type discrimination is by ``raise_type_id`` via the
    ref cache (never the display ``name`` string).

    Args:
        raises:      iterable of raise objects exposing ``.is_recurring``,
                     ``.raise_type_id``, ``.effective_year``,
                     ``.effective_month``, ``.percentage``, and
                     ``.flat_amount``.
        anchor_year: int earliest effective year for the re-anchored
                     raises (the first post-cutoff year).

    Returns:
        list[_HorizonRaise]: the recurring cola raises re-anchored to
        ``max(effective_year, anchor_year)``; empty when the user has no
        recurring cola raise.
    """
    cola_id = ref_cache.raise_type_id(RaiseTypeEnum.COLA)
    return [
        _HorizonRaise(
            effective_year=max(r.effective_year, anchor_year),
            effective_month=r.effective_month,
            is_recurring=True,
            percentage=r.percentage,
            flat_amount=r.flat_amount,
        )
        for r in raises
        if r.is_recurring and r.raise_type_id == cola_id
    ]


def _calculate_years_of_service(hire_date, retirement_date):
    """Calculate years of service as a Decimal."""
    if not hire_date or not retirement_date:
        return ZERO
    delta_days = (retirement_date - hire_date).days
    if delta_days < 0:
        return ZERO
    return (Decimal(str(delta_days)) / Decimal("365.25")).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )


def _compute_high_salary_average(salary_by_year, consecutive_high_years):
    """Find the consecutive window with the highest average salary.

    Args:
        salary_by_year:        list of (year, Decimal salary) sorted by year.
        consecutive_high_years: int window size.

    Returns:
        (best_avg, best_window) where best_window is the list of (year, salary) tuples.
    """
    n = len(salary_by_year)
    window_size = min(consecutive_high_years, n)

    if window_size <= 0:
        return ZERO, []

    best_avg = ZERO
    best_window = []

    for i in range(n - window_size + 1):
        window = salary_by_year[i:i + window_size]
        total = sum(Decimal(str(s)) for _, s in window)
        avg = round_money(total / window_size)
        if avg > best_avg:
            best_avg = avg
            best_window = window

    return best_avg, best_window
