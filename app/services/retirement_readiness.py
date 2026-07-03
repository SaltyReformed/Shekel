"""
Shekel Budget App -- Retirement Readiness Producer (P1c)

Template-ready plain data for the Fable 5 retirement rebuild's direction-D
page: the readiness hero (net-frame funded verdict), the savings
flight-path chart ("your path" vs "needed to retire", BOTH stated in the
after-tax frame so the chart never disagrees with the after-tax verdict
beside it -- Gate A ruling 2), the countdown facts, and the per-account
contribution facts.

Split out of :mod:`app.services.retirement_dashboard_service` (which owns
``compute_gap_data`` / ``compute_slider_defaults`` and stayed at the
1000-line ceiling before this rebuild) so the readiness feature is one
cohesive module, mirroring the
:mod:`app.services.year_end_summary_service` package precedent.

The CURRENT ``/retirement`` page does NOT consume this producer -- it is
wired in P3 -- so nothing here rewires the existing route or templates.
The net-frame figures reuse the one
:func:`app.services.retirement_gap_calculator.calculate_gap` formula, and
the blended chart return reuses ``compute_slider_defaults``, so no gap /
return math is duplicated.

No Flask imports.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.services import growth_engine, retirement_gap_calculator
from app.services.retirement_dashboard_service import (
    compute_gap_data,
    compute_slider_defaults,
)
from app.utils.money import round_money

# Percentage scaler: ``compute_slider_defaults`` returns the blended
# return as a percent (10.50); the reverse projection wants the fraction.
_PCT_SCALE = Decimal("100")

# Four-decimal quantum for the funded ratio (after-tax projected /
# required).  Preserves the tenth-of-a-percent the direction-D hero shows
# ("56.5% funded") while keeping the encoded value bounded.
_RATIO_QUANTUM = Decimal("0.0001")

# One-decimal quantum for the years-to-retirement countdown ("19.9 years")
# and the day divisor matching
# ``pension_calculator._calculate_years_of_service``.
_YEARS_QUANTUM = Decimal("0.1")
_DAYS_PER_YEAR = Decimal("365.25")

# Maximum plotted points per readiness-chart series (a 20-year horizon is
# ~520 synthetic periods; each series downsamples to at most this many,
# first and last always kept, the SAME index set applied to both).
_MAX_CHART_POINTS = 48


def compute_readiness_data(user_id):
    """Assemble the direction-D readiness data for the retirement page (P1c).

    Net frame (Gate A ruling 2).  Everything is stated after the estimated
    retirement tax so the verdict compares like-for-like: the income
    target is the net final-year monthly path; the pension is shown gross
    AND net; the monthly gap is net income target minus net pension; the
    required savings follow that net gap at the SWR; projected savings are
    reported pre-tax AND after-tax (the existing traditional/Roth split);
    ``funded_ratio`` is after-tax projected / required (guarded against a
    zero requirement -- the "no savings needed" state, not a division).

    Fork F1 (ratified).  A missing ``estimated_retirement_tax_rate`` is
    treated as an explicit ``Decimal("0")`` rate with a ``tax_rate_missing``
    flag the assumptions panel surfaces -- never a truthiness fallback and
    never skipping the after-tax block.

    Args:
        user_id: The user's integer ID.

    Returns:
        dict with the net-frame figures, the ``tax_rate_missing`` flag, the
        two downsampled string-Decimal chart series under ``chart``, the
        countdown facts (``periods_remaining``, ``years_remaining``,
        ``retirement_date``), and the per-account contribution facts under
        ``account_contributions``.
    """
    data = compute_gap_data(user_id)
    projections = data["retirement_account_projections"]
    net, tax_rate_missing, effective_tax_rate = _net_frame(data)
    funded_ratio, no_savings_needed = funded_ratio_state(net)
    synthetic_periods = _synthetic_periods(data["planned_retirement_date"])

    return {
        "income_target_net_monthly": net.pre_retirement_net_monthly,
        "pension_gross_monthly": net.monthly_pension_income,
        "pension_net_monthly": net.after_tax_monthly_pension,
        "monthly_gap_net": net.monthly_income_gap,
        "required_savings": net.required_retirement_savings,
        "projected_savings_pretax": net.projected_total_savings,
        "projected_savings_after_tax": net.after_tax_projected_savings,
        "funded_ratio": funded_ratio,
        "no_savings_needed": no_savings_needed,
        "surplus_or_shortfall_after_tax": net.after_tax_surplus_or_shortfall,
        "estimated_tax_rate": effective_tax_rate,
        "tax_rate_missing": tax_rate_missing,
        "safe_withdrawal_rate": data["swr"],
        "chart": _build_readiness_chart(
            data, projections, synthetic_periods,
            net.required_retirement_savings, effective_tax_rate,
        ),
        "account_contributions": [
            {
                "account": proj["account"],
                "employee_per_period": proj["employee_per_period"],
                "employer_per_period": proj["employer_per_period"],
                "none_linked": proj["none_linked"],
            }
            for proj in projections
        ],
        **_build_countdown(data["planned_retirement_date"], synthetic_periods),
    }


def _net_frame(data):
    """Re-run the gap in the explicit net frame (ruling 2 + fork F1).

    Reuses the one
    :func:`~app.services.retirement_gap_calculator.calculate_gap` formula
    at the explicit (possibly 0%) tax rate so the after-tax pension /
    projected / surplus fields are ALWAYS populated -- ``calculate_gap``
    otherwise leaves them ``None`` when the rate is unset.

    Args:
        data: The dict returned by ``compute_gap_data`` (carries the
            projections, the gross-pension gap analysis, the net biweekly,
            the SWR, and the stored estimated tax rate).

    Returns:
        ``(net_analysis, tax_rate_missing, effective_tax_rate)``.
    """
    stored_tax_rate = data["estimated_tax_rate"]
    tax_rate_missing = stored_tax_rate is None
    effective_tax_rate = (
        stored_tax_rate if stored_tax_rate is not None else Decimal("0")
    )
    net = retirement_gap_calculator.calculate_gap(
        net_biweekly_pay=data["gap_net_biweekly"],
        monthly_pension_income=data["gap_analysis"].monthly_pension_income,
        retirement_account_projections=data["retirement_account_projections"],
        safe_withdrawal_rate=data["swr"],
        estimated_tax_rate=effective_tax_rate,
    )
    return net, tax_rate_missing, effective_tax_rate


def funded_ratio_state(net):
    """Compute the after-tax funded ratio, guarding a zero requirement.

    Args:
        net: The net-frame :class:`RetirementGapAnalysis`.

    Returns:
        ``(funded_ratio, no_savings_needed)``: the ratio (after-tax
        projected / required, quantized) with ``no_savings_needed`` False;
        or ``(None, True)`` when the requirement is zero (the pension fully
        covers the gap -- reported as a distinct state, not a division).
    """
    required = net.required_retirement_savings
    if required == Decimal("0"):
        return None, True
    return (
        (net.after_tax_projected_savings / required).quantize(_RATIO_QUANTUM),
        False,
    )


def _synthetic_periods(planned_retirement_date):
    """Return the biweekly synthetic periods from today to retirement.

    Matches the exact ``generate_projection_periods`` call the account
    projection used, so the readiness chart's per-period rows align.

    Args:
        planned_retirement_date: The resolved retirement date, or ``None``.

    Returns:
        The list of synthetic periods (empty when there is no horizon).
    """
    if planned_retirement_date is None:
        return []
    return growth_engine.generate_projection_periods(
        start_date=date.today(), end_date=planned_retirement_date,
    )


def _build_countdown(planned_retirement_date, synthetic_periods):
    """Build the countdown facts for the readiness header.

    Args:
        planned_retirement_date: The resolved retirement date, or ``None``.
        synthetic_periods: The biweekly synthetic periods from today to the
            retirement date (their count is the remaining-paychecks fact).

    Returns:
        dict with ``periods_remaining`` (int biweekly paychecks left),
        ``years_remaining`` (Decimal to one decimal place, clamped at 0),
        and ``retirement_date`` (the date, or ``None``).
    """
    if planned_retirement_date is None:
        return {
            "periods_remaining": 0,
            "years_remaining": Decimal("0.0"),
            "retirement_date": None,
        }
    days = (planned_retirement_date - date.today()).days
    years_remaining = (
        max(Decimal(days), Decimal("0")) / _DAYS_PER_YEAR
    ).quantize(_YEARS_QUANTUM, rounding=ROUND_HALF_UP)
    return {
        "periods_remaining": len(synthetic_periods),
        "years_remaining": years_remaining,
        "retirement_date": planned_retirement_date,
    }


def _build_readiness_chart(
    data, projections, synthetic_periods, required_target, effective_tax_rate,
):
    """Build the two downsampled string-Decimal chart series (after-tax frame).

    BOTH series are stated in the after-tax frame so the chart agrees with
    the after-tax funded verdict beside it (Gate A ruling 2: net-primary; a
    figure and its caption never disagree).  "your path" is the summed
    per-account projected balance at each synthetic period with the
    estimated retirement tax applied to the traditional portion
    (:func:`_build_your_path`); "needed path" is
    :func:`~app.services.growth_engine.reverse_project_balance` from the
    net-frame required target back to today under the blended return
    (reused from ``compute_slider_defaults`` so it matches the accounts
    table's return) and the aggregate current contribution schedule.  Both
    series are downsampled with the SAME index set
    (:func:`_downsample_indices`, first and last always kept) so they plot
    on one axis, and each value is encoded as a string Decimal for the
    template's ``data-*`` attributes.

    Args:
        data: The dict returned by ``compute_gap_data`` (for the blended
            return via ``compute_slider_defaults``).
        projections: The per-account projection dicts (each carrying
            ``projection_rows``, ``is_traditional``, and the contribution
            facts).
        synthetic_periods: The biweekly periods from today to retirement
            (empty when there is no horizon).
        required_target: The net-frame required savings figure the needed
            path reverse-projects from.
        effective_tax_rate: The explicit (possibly F1 zero) estimated
            retirement tax rate applied to the traditional portion of
            "your path".

    Returns:
        dict with ``your_path`` / ``needed_path`` (lists of string
        Decimals) and ``dates`` (ISO end-date of each plotted period);
        all empty when there is no horizon.
    """
    if not synthetic_periods:
        return {"your_path": [], "needed_path": [], "dates": []}

    blended_return = compute_slider_defaults(data)["current_return"] / _PCT_SCALE
    your_path = _build_your_path(
        projections, synthetic_periods, effective_tax_rate,
    )
    needed_path = _build_needed_path(
        required_target, projections, synthetic_periods, blended_return,
    )
    indices = _downsample_indices(len(synthetic_periods))
    return {
        "your_path": [str(your_path[i]) for i in indices],
        "needed_path": [str(needed_path[i]) for i in indices],
        "dates": [synthetic_periods[i].end_date.isoformat() for i in indices],
    }


def _build_your_path(projections, synthetic_periods, effective_tax_rate):
    """Sum the after-tax per-period projected balances across the horizon.

    At each point ``after_tax(t) = traditional_sum(t) * (1 - rate) +
    roth_sum(t)`` -- the same traditional/Roth split and operation order as
    :func:`app.services.retirement_gap_calculator._after_tax_projected_savings`
    (one ``round_money`` after combining), so the series endpoint equals
    the after-tax projected total byte-for-byte and therefore matches the
    funded ratio's numerator.  With the F1 explicit-zero rate the after-tax
    value equals the pre-tax sum, so untaxed data plots unchanged.

    A projecting account contributes its per-period end balance from
    ``projection_rows`` (aligned 1:1 with *synthetic_periods*); a
    non-projecting account (no params / no rows) contributes its flat
    current balance at every period.

    Args:
        projections: The per-account projection dicts (``is_traditional``
            selects the taxed bucket).
        synthetic_periods: The biweekly periods from today to retirement.
        effective_tax_rate: The explicit (possibly zero) fractional tax
            rate applied to the traditional bucket.

    Returns:
        list[Decimal]: the after-tax portfolio balance at each period.
    """
    count = len(synthetic_periods)
    traditional = [Decimal("0")] * count
    roth = [Decimal("0")] * count
    for proj in projections:
        bucket = traditional if proj["is_traditional"] else roth
        rows = proj["projection_rows"]
        if len(rows) == count:
            for i in range(count):
                bucket[i] += rows[i].end_balance
        else:
            # Non-projecting account: flat current balance across the axis.
            current = proj["current_balance"]
            for i in range(count):
                bucket[i] += current
    keep_fraction = 1 - effective_tax_rate
    return [
        round_money(traditional[i] * keep_fraction + roth[i])
        for i in range(count)
    ]


def _build_needed_path(
    required_target, projections, synthetic_periods, blended_return,
):
    """Reverse-project the required savings target back to today.

    Runs :func:`~app.services.growth_engine.reverse_project_balance` from
    the required target (the balance at the end of the last period) under
    the blended return and the aggregate current per-period contribution
    (the sum of every account's employee + employer per-period amount,
    folded into one stream because reverse projection takes a single scalar
    contribution).  The result is a reference "what you would need to hold"
    trajectory whose end equals the target.

    Args:
        required_target: The net-frame required savings figure.
        projections: The per-account projection dicts (for the aggregate
            contribution).
        synthetic_periods: The biweekly periods from today to retirement.
        blended_return: The blended annual return fraction.

    Returns:
        list[Decimal]: the needed portfolio balance at each period (zeros
        when the target is non-positive -- the pension already covers the
        gap, so no savings trajectory is required).
    """
    count = len(synthetic_periods)
    if required_target <= Decimal("0"):
        return [Decimal("0")] * count
    aggregate_contribution = sum(
        (proj["employee_per_period"] + proj["employer_per_period"]
         for proj in projections),
        Decimal("0"),
    )
    reversed_proj = growth_engine.reverse_project_balance(
        anchor_balance=required_target,
        assumed_annual_return=blended_return,
        periods=synthetic_periods,
        periodic_contribution=aggregate_contribution,
    )
    if len(reversed_proj) != count:
        return [Decimal("0")] * count
    return [row.end_balance for row in reversed_proj]


def _downsample_indices(count, max_points=_MAX_CHART_POINTS):
    """Even-spaced index set for downsampling a series to <= *max_points*.

    Always includes the first (0) and last (``count - 1``) index; the
    interior indices are evenly spaced with round-half-up positioning and
    de-duplicated (so the result never exceeds *max_points* and preserves
    order).  Decimal arithmetic keeps the spacing float-free.

    Args:
        count: Length of the series to sample.
        max_points: Maximum number of sampled indices.

    Returns:
        list[int]: the sampled indices into a length-*count* series.
    """
    if count <= 0:
        return []
    if count <= max_points:
        return list(range(count))
    step = Decimal(count - 1) / Decimal(max_points - 1)
    result = []
    for point in range(max_points):
        index = int(
            (step * point).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        if index not in result:
            result.append(index)
    return result
