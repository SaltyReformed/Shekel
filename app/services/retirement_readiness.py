"""
Shekel Budget App -- Retirement Readiness Shaping (P1c)

Template-ready plain data for the Fable 5 retirement rebuild's direction-D
page: the readiness hero (net-frame funded verdict), the savings
flight-path chart ("your path" vs "needed to retire", BOTH stated in the
after-tax frame so the chart never disagrees with the after-tax verdict
beside it -- Gate A ruling 2), the countdown facts, and the per-account
contribution facts.

**It COMPUTES nothing about the plan, since plan step C2-f2d-2.**  Every
figure below is shaped from one
:class:`~app.services.retirement_plan.RetirementPicture` -- the page's one
producer -- so this module holds only display shaping: rounding, meter widths,
chart downsampling and the pension footer lines.  It used to re-run the gap in
the net frame from a fourteen-key dict it read by string key across a module
boundary (``_net_frame``), which was a second call to ``calculate_gap`` over
inputs the picture already carried; the picture carries the net analysis
itself now, so there is nothing left here to get wrong.

Split out of :mod:`app.services.retirement_dashboard_service` (which stayed at
the 1000-line ceiling before this rebuild) so the readiness feature is one
cohesive module, mirroring the :mod:`app.services.year_end_summary_service`
package precedent.

No Flask imports.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.services import growth_engine
from app.services.retirement_dashboard_service import (
    resolve_retirement_date_provenance,
)
from app.services.retirement_plan import (
    STORED_PLAN,
    PlanPoint,
    RetirementInputs,
    RetirementPicture,
    picture_at,
)
from app.utils.money import MONTHS_PER_YEAR, round_money

# Percentage scaler for the income meter's segment widths: the covered shares
# of the monthly income target are computed here (templates never compute
# money) and echoed into ``data-progress-pct`` as CSS widths.
_PCT_SCALE = Decimal("100")

# One-decimal quantum for the years-to-retirement countdown ("19.9 years")
# and the day divisor matching
# ``pension_calculator._calculate_years_of_service``.
_YEARS_QUANTUM = Decimal("0.1")
_DAYS_PER_YEAR = Decimal("365.25")

# Maximum plotted points per readiness-chart series (a 20-year horizon is
# ~520 projected periods; each series downsamples to at most this many,
# first and last always kept, the SAME index set applied to both).
_MAX_CHART_POINTS = 48

# One-decimal quantum for the income meter's segment widths (percent of
# the monthly income target; display-shaping only, applied by
# progress_bar.js as a CSS width).
_METER_PCT_QUANTUM = Decimal("0.1")


def readiness_from_picture(picture: RetirementPicture) -> dict:
    """Shape the readiness dict from one retirement picture.

    **Shaping only.**  Every figure below is read off *picture* -- the page's
    one producer (:func:`app.services.retirement_plan.picture_at`) -- and the
    only arithmetic here is display arithmetic: rounding, meter widths, chart
    downsampling and the keep-fraction on the pension footer lines.

    It took ``compute_gap_data``'s fourteen-key dict until plan step C2-f2d-2
    and re-ran the gap in the net frame from it, which was a SECOND call to
    ``calculate_gap`` over inputs that call had already been given.  The
    picture carries the net analysis, so that call is gone with the dict.

    Args:
        picture: The :class:`~app.services.retirement_plan.RetirementPicture`
            to render -- the stored plan's, or a what-if's.

    Returns:
        dict with the net-frame figures, the ``tax_rate_missing`` flag, the two
        downsampled string-Decimal chart series under ``chart``, the countdown
        facts (``periods_remaining``, ``years_remaining``,
        ``retirement_date``), the per-account contribution facts under
        ``account_contributions``, the income-composition display facts under
        ``income_meter`` (:func:`_build_income_meter`), and the per-pension
        derivation lines under ``pension_lines`` (:func:`_build_pension_lines`).
    """
    net = picture.net
    projections = picture.projections
    # The tax facts are the INPUTS', not the picture's: no plan point varies
    # them, so every picture on this render is computed at the one rate.
    effective_tax_rate = picture.inputs.effective_tax_rate
    funded_ratio, no_savings_needed = picture.funded_state
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
        "tax_rate_missing": picture.inputs.tax_rate_missing,
        "safe_withdrawal_rate": picture.safe_withdrawal_rate,
        # Acceptance-drive fix 1: who owns the resolved date.  A
        # pension-owned date makes the assumptions rail's date row
        # read-only with provenance (a settings save cannot move the
        # horizon while a pension date exists).
        "date_provenance": resolve_retirement_date_provenance(
            picture.inputs.gap.pensions, picture.inputs.gap.settings,
        ),
        "chart": _build_readiness_chart(picture, effective_tax_rate),
        "income_meter": _build_income_meter(net, picture.safe_withdrawal_rate),
        "pension_lines": _build_pension_lines(
            picture.pension.per_pension, effective_tax_rate,
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
        **_build_countdown(
            picture.retirement_date, picture.axis, picture.as_of,
        ),
    }


def compute_readiness_whatif(
    inputs: RetirementInputs, point: PlanPoint = STORED_PLAN,
) -> dict:
    """Readiness at the stored settings plus the what-if deltas (P3a).

    The assumptions panel's data producer: the STORED-settings picture is
    always the baseline; when *point* differs from it the picture is recomputed
    at *point* and the panel's delta facts are derived (:func:`_whatif_deltas`
    -- funded-ratio delta in percentage points, shortfall delta in dollars).
    At :data:`~app.services.retirement_plan.STORED_PLAN` the displayed state IS
    the baseline and ``deltas`` is ``None`` (no delta chips render).

    **Both pictures come from ONE loader and ONE producer** (plan step
    C2-f2d-2, and C2-f2d-1 before it for the read pass).  The two computations
    are legitimately different -- that is what a what-if is -- but the delta
    between them must be the POINT's effect and nothing else, so every input
    they do not vary had better be the same object rather than two equal loads.
    A panel rendered across midnight used to report a day's drift as a what-if
    delta; one rendered across a NEW YEAR still can, because
    ``compute_pension_summary``, ``compute_gap_net_biweekly`` and
    ``build_employer_salary_basis`` each read ``date.today().year`` for
    themselves.  Ledger row **P55** owns that remainder.

    Args:
        inputs: The render's
            :class:`~app.services.retirement_plan.RetirementInputs`, built once
            by the route.
        point: The :class:`~app.services.retirement_plan.PlanPoint` the panel
            is displaying.  Defaults to the stored plan, in which case the
            baseline is the displayed state and no picture is derived twice --
            the memo answers the second ask.

    Returns:
        dict with ``readiness`` (the displayed -- possibly what-if -- state),
        ``baseline`` (always the stored-settings state), and ``deltas``
        (``None`` when *point* is the stored plan).
    """
    baseline_picture = picture_at(inputs, STORED_PLAN)
    baseline = readiness_from_picture(baseline_picture)
    if point == STORED_PLAN:
        return {"readiness": baseline, "baseline": baseline, "deltas": None}
    override = readiness_from_picture(picture_at(inputs, point))
    return {
        "readiness": override,
        "baseline": baseline,
        "deltas": _whatif_deltas(baseline, override),
    }


def _whatif_deltas(baseline, override):
    """Derive the panel's baseline-vs-override delta facts.

    ``funded_ratio_points`` is the funded-ratio change in percentage
    points -- ``(override - baseline) * 100`` quantized to one decimal
    (the panel chip's "funded 52% (-11.3)") -- and is ``None`` when either
    side is in the no-savings-needed state (there is no ratio to
    difference).  ``shortfall_dollars`` is the after-tax
    surplus-or-shortfall change in dollars (always defined; both sides are
    money): positive means the what-if IMPROVES the position.

    Args:
        baseline: The stored-settings readiness dict.
        override: The what-if readiness dict.

    Returns:
        dict with ``funded_ratio_points`` (Decimal | None) and
        ``shortfall_dollars`` (Decimal).
    """
    if (baseline["funded_ratio"] is not None
            and override["funded_ratio"] is not None):
        funded_ratio_points = (
            (override["funded_ratio"] - baseline["funded_ratio"])
            * Decimal("100")
        ).quantize(Decimal("0.1"))
    else:
        funded_ratio_points = None
    return {
        "funded_ratio_points": funded_ratio_points,
        "shortfall_dollars": (
            override["surplus_or_shortfall_after_tax"]
            - baseline["surplus_or_shortfall_after_tax"]
        ),
    }


def _build_income_meter(net, swr):
    """Shape the income-in-retirement card's meter facts (display only).

    Everything is derived from the net-frame gap the verdict already
    computed, so the meter can never disagree with the hero beside it:
    the monthly SWR withdrawal income is the after-tax projected savings
    at the SWR (projected * SWR / 12 -- the retired gap chart's formula,
    restated in the after-tax frame per Gate A ruling 2), the uncovered
    remainder is what neither the net pension nor those withdrawals
    reach, and the two segment percentages are the covered shares of the
    net income target (clamped so the pair never exceeds 100 even in an
    over-covered plan).  Percent widths are computed HERE because
    templates never compute money; the template only echoes them into
    ``data-progress-pct``.

    Args:
        net: The net-frame :class:`RetirementGapAnalysis`.
        swr: The active fractional safe-withdrawal rate.

    Returns:
        dict with ``withdrawals_net_monthly``, ``uncovered_monthly``,
        ``pension_pct``, and ``withdrawals_pct`` (both percentages
        quantized to one decimal; zero when there is no income target).
    """
    target = net.pre_retirement_net_monthly
    withdrawals = (
        round_money(net.after_tax_projected_savings * swr / MONTHS_PER_YEAR)
        if net.after_tax_projected_savings > 0
        else Decimal("0.00")
    )
    uncovered = max(
        Decimal("0.00"),
        target - net.after_tax_monthly_pension - withdrawals,
    )
    if target > 0:
        pension_pct = min(
            _PCT_SCALE, net.after_tax_monthly_pension / target * _PCT_SCALE,
        ).quantize(_METER_PCT_QUANTUM)
        withdrawals_pct = min(
            _PCT_SCALE - pension_pct, withdrawals / target * _PCT_SCALE,
        ).quantize(_METER_PCT_QUANTUM)
    else:
        pension_pct = Decimal("0.0")
        withdrawals_pct = Decimal("0.0")
    return {
        "withdrawals_net_monthly": withdrawals,
        "uncovered_monthly": uncovered,
        "pension_pct": pension_pct,
        "withdrawals_pct": withdrawals_pct,
    }


def _build_pension_lines(pension_benefits, effective_tax_rate):
    """Shape the per-pension derivation lines for the page footer (D6).

    One display dict per qualifying pension, straight from the benefits
    ``compute_pension_summary`` already computed (retained via
    ``PensionSummary.per_pension``), so the footer derives EVERY pension
    rather than silently showing the last one while the gap row sums all
    of them (audit finding D6).  The only arithmetic is the same
    keep-fraction the net pension figure already uses (fork F1: the
    explicit, possibly zero, estimated tax rate).

    Args:
        pension_benefits: The picture's ``pension.per_pension`` entries
            (``name``, ``benefit_multiplier``, ``consecutive_high_years``,
            ``benefit``).
        effective_tax_rate: The explicit (possibly F1 zero) fractional
            estimated retirement tax rate.

    Returns:
        list of dicts with ``name``, ``benefit_multiplier``,
        ``high_years_count``, ``years_of_service``,
        ``high_salary_average``, ``window_start`` / ``window_end`` (the
        projected high-salary window's first and last year, ``None``
        when the window is empty), ``gross_monthly``, and
        ``net_monthly``.
    """
    keep_fraction = 1 - effective_tax_rate
    lines = []
    for entry in pension_benefits:
        benefit = entry["benefit"]
        window_years = [year for year, _ in benefit.high_salary_years]
        lines.append({
            "name": entry["name"],
            "benefit_multiplier": entry["benefit_multiplier"],
            "high_years_count": entry["consecutive_high_years"],
            "years_of_service": benefit.years_of_service,
            "high_salary_average": benefit.high_salary_average,
            "window_start": min(window_years) if window_years else None,
            "window_end": max(window_years) if window_years else None,
            "gross_monthly": benefit.monthly_benefit,
            "net_monthly": round_money(benefit.monthly_benefit * keep_fraction),
        })
    return lines


def _build_countdown(planned_retirement_date, axis, as_of):
    """Build the countdown facts for the readiness header.

    **"Paychecks remaining" is the owner's own cadence** since plan step C2-e:
    the axis is their pay calendar projected forward at the cadence they
    recorded, where it used to be a hardcoded 14-day rhythm.  A monthly-paid
    owner planning a 20-year horizon was told 522 paychecks remained when 244
    do (ledger row **P20**, whose money half is the contribution count riding
    on that same axis).

    Args:
        planned_retirement_date: The resolved retirement date, or ``None``.
        axis: The picture's projection axis -- the owner's paychecks from
            the read pass's clock to the retirement date.  Its LENGTH is the
            remaining-paychecks fact.
        as_of: The read pass's clock, the same one the axis opens after, so
            "years remaining" and "paychecks remaining" are measured from one
            day rather than from two clock reads.

    Returns:
        dict with ``periods_remaining`` (int paychecks left at the owner's own
        cadence), ``years_remaining`` (Decimal to one decimal place, clamped
        at 0), and ``retirement_date`` (the date, or ``None``).
    """
    if planned_retirement_date is None:
        return {
            "periods_remaining": 0,
            "years_remaining": Decimal("0.0"),
            "retirement_date": None,
        }
    days = (planned_retirement_date - as_of).days
    years_remaining = (
        max(Decimal(days), Decimal("0")) / _DAYS_PER_YEAR
    ).quantize(_YEARS_QUANTUM, rounding=ROUND_HALF_UP)
    return {
        "periods_remaining": len(axis),
        "years_remaining": years_remaining,
        "retirement_date": planned_retirement_date,
    }


def _build_readiness_chart(picture, effective_tax_rate):
    """Build the two downsampled string-Decimal chart series (after-tax frame).

    BOTH series are stated in the after-tax frame so the chart agrees with
    the after-tax funded verdict beside it (Gate A ruling 2: net-primary; a
    figure and its caption never disagree).  "your path" is the summed
    per-account projected balance at each axis period with the
    estimated retirement tax applied to the traditional portion
    (:func:`_build_your_path`); "needed path" is
    :func:`~app.services.growth_engine.reverse_project_balance` from the
    net-frame required target back to today under the blended return and the
    aggregate current contribution schedule.  Both series are downsampled with
    the SAME index set (:func:`_downsample_indices`, first and last always
    kept) so they plot on one axis, and each value is encoded as a string
    Decimal for the template's ``data-*`` attributes.

    **The blended return is the PICTURE's** since plan step C2-f2d-2 -- the
    same rate the accounts table displays and the same one the contribution
    lever divides the shortfall by.  This site used to derive its own from a
    dict, applying the what-if override where present (a uniform override IS
    the blend, since every account's weight then carries the same rate) and
    re-querying every account's ``InvestmentParams`` where not.  Both arms are
    now :attr:`~app.services.retirement_plan.RetirementPicture.blended_return`.

    Args:
        picture: The :class:`~app.services.retirement_plan.RetirementPicture`
            being rendered -- its axis, its per-account projections, its
            required target and the return frame all of them ran in.
        effective_tax_rate: The explicit (possibly F1 zero) estimated
            retirement tax rate applied to the traditional portion of
            "your path".  Passed rather than re-read so the chart and the
            verdict above it cannot state two frames.

    Returns:
        dict with ``your_path`` / ``needed_path`` (lists of string
        Decimals) and ``dates`` (ISO end-date of each plotted period);
        all empty when there is no horizon.
    """
    axis = picture.axis
    if not axis:
        return {"your_path": [], "needed_path": [], "dates": []}

    projections = picture.projections
    your_path = _build_your_path(
        projections, axis, effective_tax_rate,
    )
    needed_path = _build_needed_path(
        picture.net.required_retirement_savings, projections, axis,
        picture.blended_return,
    )
    indices = _downsample_indices(len(axis))
    return {
        "your_path": [str(your_path[i]) for i in indices],
        "needed_path": [str(needed_path[i]) for i in indices],
        "dates": [axis[i].end_date.isoformat() for i in indices],
    }


def _build_your_path(projections, axis, effective_tax_rate):
    """Sum the after-tax per-period projected balances across the horizon.

    At each point ``after_tax(t) = traditional_sum(t) * (1 - rate) +
    roth_sum(t)`` -- the same traditional/Roth split and operation order as
    :func:`app.services.retirement_gap_calculator._after_tax_projected_savings`
    (one ``round_money`` after combining), so the series endpoint equals
    the after-tax projected total byte-for-byte and therefore matches the
    funded ratio's numerator.  With the F1 explicit-zero rate the after-tax
    value equals the pre-tax sum, so untaxed data plots unchanged.

    A projecting account contributes its per-period end balance from
    ``projection_rows`` (aligned 1:1 with *axis*); a
    non-projecting account (no params / no rows) contributes its flat
    current balance at every period.

    Args:
        projections: The per-account projection dicts (``is_traditional``
            selects the taxed bucket).
        axis: The projection axis the per-account rows were computed over.
        effective_tax_rate: The explicit (possibly zero) fractional tax
            rate applied to the traditional bucket.

    Returns:
        list[Decimal]: the after-tax portfolio balance at each period.
    """
    count = len(axis)
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
    required_target, projections, axis, blended_return,
):
    """Reverse-project the required savings target back to today.

    Runs :func:`~app.services.growth_engine.reverse_project_balance` from
    the required target (the balance at the end of the last period) under
    the blended return and the aggregate current per-period contribution
    (the sum of every account's employee + employer per-period amount,
    folded into one stream because reverse projection takes a single scalar
    contribution).  The result is a reference "what you would need to hold"
    trajectory whose end equals the target.

    Frame note (review L2, documented not changed): the reversal walks
    the AFTER-TAX required target back through the RAW pre-tax
    contribution stream (the per-period employee + employer dollars are
    contributed pre-tax, but a traditional dollar is worth ``1 - rate``
    of an after-tax dollar).  Both endpoints are exact -- the last point
    IS the target and today's point is a derived reference, not a
    balance -- but with a nonzero estimated tax rate the interior points
    sit slightly LOW (each reversal step subtracts a full pre-tax dollar
    where the after-tax frame earns only its keep-fraction).  Accepted
    as display-shaping for the reference line.

    Args:
        required_target: The net-frame required savings figure.
        projections: The per-account projection dicts (for the aggregate
            contribution).
        axis: The projection axis the per-account rows were computed over.
        blended_return: The blended annual return fraction.

    Returns:
        list[Decimal]: the needed portfolio balance at each period (zeros
        when the target is non-positive -- the pension already covers the
        gap, so no savings trajectory is required).
    """
    count = len(axis)
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
        periods=axis,
        periodic_contribution=aggregate_contribution,
    )
    # No length guard: ``reverse_project_balance`` emits exactly one row per
    # input period, and since plan step C2-e ``count`` is the length of the SAME
    # window that was handed to it.  The guard existed because the axis and the
    # rows came from two producers held equal by a comment; one producer cannot
    # disagree with itself, so the branch could no longer fire (CLAUDE.md
    # rule 1).
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
