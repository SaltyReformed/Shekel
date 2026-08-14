"""
Shekel Budget App -- Retirement Lever Solvers (P2a / P2b)

The two "close the gap" levers for the Fable 5 retirement rebuild's
direction-D page:

* **Contribution lever (P2a).**  The additional per-period contribution
  that closes the after-tax shortfall by the retirement date -- closed
  form, no iteration: shortfall divided by the annuity factor of the
  remaining paychecks at the blended return
  (:func:`_annuity_factor`).  Ratified fork F2: the new money is treated
  as Roth-basis (untaxed at withdrawal), so it solves against the
  AFTER-TAX shortfall and its horizon value is added to the after-tax
  projection dollar-for-dollar.  If the solution exceeds the aggregate
  per-period contribution-limit headroom the producer flags it honestly
  with the numbers -- it never silently caps.

* **Retire-later lever (P2b).**  The smallest whole-month offset (binary
  search, capped at +180 months) at which a FULL recomputation of the
  readiness picture -- the merit-horizon salary path, the pension years
  of service and high-salary average, the growth horizon, and the
  required target (BOTH sides of the gap move with the date) -- reaches
  funded >= 100%.  Probes reuse one loaded input batch
  (:func:`app.services.retirement_projection.load_projection_batch`);
  only the date-dependent parts recompute per probe.

``compute_lever_data`` is the producer the P2c lever fragment endpoint
renders: it returns both levers' solved defaults plus the outcome facts
at caller-supplied stepper values.

All functions accept plain data and return plain data.  No Flask imports.
"""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from app.services import growth_engine, retirement_gap_calculator
from app.services.pay_calendar import PayCadence, PeriodWindow
from app.services.retirement_dashboard_service import (
    GapInputs,
    compute_gap_net_biweekly,
    compute_pension_summary,
    compute_slider_defaults,
    load_gap_inputs,
    resolve_estimated_tax_rate,
    resolve_planned_retirement_date,
    resolve_swr_fraction,
)
from app.services.retirement_projection import (
    build_employer_salary_basis,
    build_projection_context,
    load_projection_batch,
    project_accounts_with_batch,
    resolve_projection_axis,
)
from app.services.retirement_readiness import funded_ratio_state
from app.utils.dates import add_months
from app.utils.money import ZERO, round_money

# Retire-later search cap: the largest month offset the P2b binary search
# probes (the audit's ratified bound).  A plan not funded even at +180
# months is reported as the honest ``not_within_cap`` state.
_MAX_DELAY_MONTHS = 180

# Percentage scaler for ``compute_slider_defaults``' percent-form blended
# return (10.50 -> 0.1050).
_PCT_SCALE = Decimal("100")

# Funded means the quantized funded ratio reaches at least this value.
_FULLY_FUNDED = Decimal("1")


@dataclass(frozen=True)
class _ProbeInputs:
    """Loaded-once inputs shared by every retire-later probe.

    Built by :func:`_load_probe_inputs`.  Everything here is
    date-independent: a probe at month offset ``m`` recomputes only the
    salary path, the pension benefit, the employer salary basis, the
    projection axis, and the per-account engine walk.

    Attributes:
        gap: The :class:`GapInputs` bundle (settings, pensions, salary
            profiles, current pay, merit horizon).
        base_date: The stored plan's resolved retirement date, or ``None``
            when neither a pension nor the settings supply one.
        ctx: The projection context at the stored plan (probes derive
            horizon-shifted copies via :func:`dataclasses.replace`).
        batch: The date-independent projection batch (deductions,
            contributions, params, balances) loaded once.
        swr: The resolved fractional safe-withdrawal rate.
        effective_tax_rate: The explicit (possibly F1 zero) estimated
            retirement tax rate.
        tax_rate_missing: True when the stored rate is unset (fork F1).
    """

    gap: GapInputs
    base_date: date | None
    ctx: object
    batch: object
    swr: Decimal
    effective_tax_rate: Decimal
    tax_rate_missing: bool


@dataclass(frozen=True)
class _ProbeResult:
    """One candidate retirement date's readiness picture.

    **``month_offset`` was DELETED at plan step C2-e** (developer, 2026-08-14),
    which is what keeps this record at seven fields and off a
    ``too-many-instance-attributes`` suppression now that it carries the axis.
    It was written by :func:`_probe` and read nowhere in ``app/``: the memo is
    keyed on the offset by its CALLER (:func:`compute_lever_data`'s
    ``probe_cache``), and both levers read :attr:`retirement_date` instead.  A
    field whose only consumer was a test is a field with no consumer -- the same
    ruling that deleted the milestone dicts' machine ``kind`` at plan step X-s1.

    Attributes:
        retirement_date: The shifted retirement date.
        required: The net-frame required savings at that date.
        after_tax_projected: The after-tax projected savings at that date.
        funded_ratio: after-tax projected / required (quantized), or
            ``None`` when the requirement is zero.
        no_savings_needed: True when the requirement is zero (the pension
            fully covers the shifted gap).
        projections: The per-account projection dicts the probe produced
            (the baseline probe's list feeds the blended return and the
            headroom facts).
        axis: The :class:`~app.services.pay_calendar.PeriodWindow` this probe
            projected over -- the owner's paychecks from the read pass's clock
            to :attr:`retirement_date`.  **Carried rather than rebuilt** (plan
            step C2-e): the contribution lever's annuity factor is a fold over
            exactly the periods the baseline probe used, and it used to
            RE-ISSUE the axis producer with the same two arguments and trust
            the two calls to agree.  An annuity factor over a different axis
            than the shortfall it divides solves for a per-period contribution
            that does not close the gap.
    """

    retirement_date: date
    required: Decimal
    after_tax_projected: Decimal
    funded_ratio: Decimal | None
    no_savings_needed: bool
    projections: list
    axis: PeriodWindow


def compute_lever_data(user_id, contribution_override=None, months_override=None):
    """Compute both levers' solved defaults and stepper outcomes (P2a/P2b).

    Args:
        user_id: The user's integer ID.
        contribution_override: Optional Decimal per-period extra
            contribution from the stepper; ``None`` displays the solved
            default.
        months_override: Optional int month offset from the stepper
            (0-180); ``None`` displays the solved default.

    Returns:
        dict with ``no_horizon`` (True short-circuits everything else when
        no retirement date exists), ``tax_rate_missing``, the ``baseline``
        readiness facts, and the ``contribution`` / ``retire_later`` lever
        dicts (see :func:`_contribution_lever` /
        :func:`_retire_later_lever`).
    """
    inputs = _load_probe_inputs(user_id)
    if inputs.base_date is None:
        # No pension date and no settings date: there is no horizon to
        # solve against.  P3 renders this as the page's empty state.
        return {
            "no_horizon": True,
            "tax_rate_missing": inputs.tax_rate_missing,
        }

    probe_cache: dict[int, _ProbeResult] = {}

    def probe_at(month_offset):
        """Memoized probe so the search and the outcome reuse results."""
        if month_offset not in probe_cache:
            probe_cache[month_offset] = _probe(inputs, month_offset)
        return probe_cache[month_offset]

    baseline = probe_at(0)
    return {
        "no_horizon": False,
        "tax_rate_missing": inputs.tax_rate_missing,
        "baseline": {
            "funded_ratio": baseline.funded_ratio,
            "no_savings_needed": baseline.no_savings_needed,
            "required_savings": baseline.required,
            "projected_after_tax": baseline.after_tax_projected,
            "retirement_date": inputs.base_date,
        },
        "contribution": _contribution_lever(
            inputs, baseline, contribution_override,
        ),
        "retire_later": _retire_later_lever(probe_at, months_override),
    }


# ── Loading and probing ──────────────────────────────────────────


def _load_probe_inputs(user_id):
    """Load every date-independent lever input exactly once.

    Args:
        user_id: The user's integer ID.

    Returns:
        A :class:`_ProbeInputs` bundle.  ``ctx`` / ``batch`` are built at
        the stored plan's resolved date; probes shift the context per
        candidate date without re-querying.
    """
    gap = load_gap_inputs(user_id)
    base_date = resolve_planned_retirement_date(gap.pensions, gap.settings)
    ctx = build_projection_context(
        user_id,
        gap.pay.all_periods,
        gap.pay.current_period,
        base_date,
        None,
        build_employer_salary_basis(
            gap.salary_profiles, base_date, gap.merit_horizon_years,
        ),
    )
    stored_tax_rate = resolve_estimated_tax_rate(gap.settings)
    return _ProbeInputs(
        gap=gap,
        base_date=base_date,
        ctx=ctx,
        batch=load_projection_batch(ctx),
        swr=resolve_swr_fraction(gap.settings),
        effective_tax_rate=(
            stored_tax_rate if stored_tax_rate is not None else Decimal("0")
        ),
        tax_rate_missing=stored_tax_rate is None,
    )


def _probe(inputs, month_offset):
    """Recompute the FULL readiness picture at plan date + *month_offset*.

    Both sides of the gap move with the date: the merit-horizon salary
    path extends (:func:`compute_pension_summary` shifts each pension's
    date, growing the years of service and the high-salary window), the
    income target re-derives from the longer salary path
    (:func:`compute_gap_net_biweekly`), the employer salary basis and the
    growth horizon extends, and the per-account projections
    re-run over the longer axis -- all against the ONE loaded batch.

    Args:
        inputs: The loaded-once :class:`_ProbeInputs` (``base_date`` is
            non-None; the caller guards the no-horizon case).
        month_offset: Whole months added to the stored plan (>= 0).

    Returns:
        A :class:`_ProbeResult` for the shifted date.
    """
    date_m = add_months(inputs.base_date, month_offset)
    pension = compute_pension_summary(
        inputs.gap.pensions, inputs.gap.merit_horizon_years, month_offset,
    )
    ctx_m = replace(
        inputs.ctx,
        planned_retirement_date=date_m,
        employer_salary_basis=build_employer_salary_basis(
            inputs.gap.salary_profiles, date_m,
            inputs.gap.merit_horizon_years,
        ),
    )
    axis = resolve_projection_axis(ctx_m, inputs.batch.balance_ctx)
    projections = project_accounts_with_batch(ctx_m, inputs.batch, axis)
    net = retirement_gap_calculator.calculate_gap(
        net_biweekly_pay=compute_gap_net_biweekly(
            inputs.gap.salary_profiles, date_m, inputs.gap.pay,
            pension.salary_by_year, inputs.gap.merit_horizon_years,
        ),
        pay_cadence=inputs.gap.pay_cadence,
        monthly_pension_income=pension.monthly_income,
        retirement_account_projections=projections,
        safe_withdrawal_rate=inputs.swr,
        estimated_tax_rate=inputs.effective_tax_rate,
    )
    funded_ratio, no_savings_needed = funded_ratio_state(net)
    return _ProbeResult(
        retirement_date=date_m,
        required=net.required_retirement_savings,
        after_tax_projected=net.after_tax_projected_savings,
        funded_ratio=funded_ratio,
        no_savings_needed=no_savings_needed,
        projections=projections,
        axis=axis,
    )


def _is_funded(probe):
    """Return True when a probe's plan is fully funded.

    Funded means the requirement is zero (the pension covers the whole
    gap) or the quantized funded ratio reaches 100%.

    Args:
        probe: A :class:`_ProbeResult`.

    Returns:
        bool.
    """
    return probe.no_savings_needed or probe.funded_ratio >= _FULLY_FUNDED


# ── P2a: contribution lever ──────────────────────────────────────


def _annuity_factor(periods, annual_return):
    """Horizon value of a $1-per-period contribution stream.

    ``AF = sum over periods p of prod(1 + r_q for q > p)`` -- each
    period's contribution is applied at the period END (matching
    ``growth_engine._project_one_period``: growth accrues on the opening
    balance BEFORE the contribution lands), so a contribution in period
    ``p`` compounds through periods ``p+1..n`` only, and the last
    period's dollar arrives uncompounded (factor 1).  Per-period rates
    come from :func:`app.services.growth_engine.span_return_rate` --
    the engine's own inclusive-day-count formula -- so the closed form
    cannot drift from an engine replay beyond per-period penny rounding.

    Args:
        periods: The :class:`~app.services.pay_calendar.PeriodWindow` to the
            horizon -- the SAME window the probe whose shortfall this divides
            projected over.
        annual_return: The blended annual return fraction.

    Returns:
        Decimal: the annuity factor (``ZERO`` for an empty axis).
    """
    compound_to_horizon = Decimal("1")
    factor = ZERO
    for period in reversed(periods):
        factor += compound_to_horizon
        compound_to_horizon *= (
            1 + growth_engine.span_return_rate(
                annual_return, period.start_date, period.end_date,
            )
        )
    return factor


def _blended_return(settings, projections):
    """The balance-weighted blended annual return fraction.

    Reuses :func:`~app.services.retirement_dashboard_service
    .compute_slider_defaults` -- the same definition the readiness chart's
    needed-path uses -- scaled from percent to fraction.

    Args:
        settings: The user's :class:`UserSettings`, or ``None``.
        projections: The baseline per-account projection dicts.

    Returns:
        Decimal fraction (e.g. ``0.105`` for 10.5%).
    """
    slider = compute_slider_defaults({
        "settings": settings,
        "retirement_account_projections": projections,
    })
    return slider["current_return"] / _PCT_SCALE


def _headroom_per_period(
    projections: list[dict], pay_cadence: PayCadence,
) -> Decimal | None:
    """Aggregate per-period contribution-limit headroom across accounts.

    For each account with a finite ``annual_contribution_limit``, the
    per-period room is ``limit / paychecks per year - current employee
    per-period`` (floored at zero); the aggregate is their sum.  An account
    with no known limit (no params row, or an uncapped account such as a
    brokerage) makes the aggregate unbounded -- reported as ``None`` so
    the solver never flags a solution that unlimited account could absorb.

    **The divisor is the OWNER's paycheck count since plan step R7a-2a**, where
    it was a hardcoded 26.  It has to match the cadence the contributions
    themselves are made at: a weekly-paid owner making 52 contributions a year
    was told each could be ``limit / 26``, which is twice the room they have,
    so the solver would propose a contribution that overshoots the annual cap.

    Args:
        projections: The per-account projection dicts (each carrying
            ``annual_contribution_limit`` and ``employee_per_period``).
        pay_cadence: The owner's
            :class:`~app.services.pay_calendar.PayCadence` -- how many
            contributions a year the annual limit is spread over.

    Returns:
        Decimal per-period headroom, or ``None`` when unbounded.
    """
    total = ZERO
    for proj in projections:
        limit = proj["annual_contribution_limit"]
        if limit is None:
            return None
        room = pay_cadence.annual_to_per_paycheck(Decimal(str(limit))) - (
            proj["employee_per_period"]
        )
        total += max(room, ZERO)
    return round_money(total)


def _contribution_outcome(baseline, annuity_factor, amount):
    """Outcome facts for an extra *amount* per period, Roth-basis (F2).

    The stream's horizon value is ``amount * AF``; Roth-basis dollars land
    untaxed, so it adds to the AFTER-TAX projection dollar-for-dollar and
    the funded ratio re-derives against the unchanged requirement.

    Args:
        baseline: The month-0 :class:`_ProbeResult`.
        annuity_factor: The horizon annuity factor.
        amount: The extra per-period contribution (Decimal >= 0).

    Returns:
        dict with ``projected_after_tax``, ``funded_ratio``,
        ``no_savings_needed``, and ``surplus_or_shortfall`` at *amount*.
    """
    projected = baseline.after_tax_projected + round_money(
        amount * annuity_factor,
    )
    if baseline.required == ZERO:
        funded_ratio, no_savings_needed = None, True
    else:
        funded_ratio = (projected / baseline.required).quantize(
            Decimal("0.0001"),
        )
        no_savings_needed = False
    return {
        "projected_after_tax": projected,
        "funded_ratio": funded_ratio,
        "no_savings_needed": no_savings_needed,
        "surplus_or_shortfall": projected - baseline.required,
    }


def _contribution_lever(inputs, baseline, contribution_override):
    """Assemble the contribution lever's solved default and outcome (P2a).

    Closed form: ``solved = round(after-tax shortfall / AF)`` where the
    shortfall is ``required - after-tax projected`` (fork F2: Roth-basis
    money closes an after-tax gap) and AF is the annuity factor of the
    remaining paychecks at the blended return.  A non-positive
    shortfall is the ``already_funded`` state (solved amount 0.00); a
    POSITIVE shortfall with a zero annuity factor -- the planned date is
    today or past, so no paycheck remains for new money to land in -- is
    the honest ``past_horizon`` state (M1: collapsing it into
    already_funded contradicted the hero's shortfall verdict).  The
    headroom facts are attached honestly: ``exceeds_headroom`` compares
    the DISPLAYED amount against the aggregate per-period limit headroom
    and never caps the number.

    Args:
        inputs: The loaded-once :class:`_ProbeInputs`.
        baseline: The month-0 probe.
        contribution_override: Optional stepper amount; ``None`` displays
            the solved default.

    Returns:
        dict with ``state`` (``solved`` / ``already_funded`` /
        ``past_horizon``), ``solved_amount`` (``None`` for
        ``past_horizon`` -- no per-period solution exists), ``amount``
        (the displayed stepper value; ``None`` only in the unsolvable
        no-override case), the outcome facts at that amount,
        ``headroom_per_period`` (``None`` = unbounded), and
        ``exceeds_headroom``.
    """
    annuity_factor = _annuity_factor(
        baseline.axis,
        _blended_return(inputs.gap.settings, baseline.projections),
    )
    shortfall = baseline.required - baseline.after_tax_projected
    if shortfall <= ZERO:
        state = "already_funded"
        solved_amount = Decimal("0.00")
    elif annuity_factor == ZERO:
        # M1: there IS a shortfall but zero periods remain -- typically a
        # pension-sourced planned date that has aged into the past (the
        # settings schema rejects new past dates, but stored data ages).
        # No per-period amount can exist; report the state, never a lie.
        state = "past_horizon"
        solved_amount = None
    else:
        state = "solved"
        solved_amount = round_money(shortfall / annuity_factor)

    amount = (
        contribution_override
        if contribution_override is not None
        else solved_amount
    )
    headroom = _headroom_per_period(
        baseline.projections, inputs.gap.pay_cadence,
    )
    return {
        "state": state,
        "solved_amount": solved_amount,
        "amount": amount,
        # A None amount (past_horizon, no override) evaluates the outcome
        # at $0 extra: with no periods the annuity factor is zero anyway,
        # so the facts are the baseline picture.
        **_contribution_outcome(
            baseline, annuity_factor,
            amount if amount is not None else Decimal("0"),
        ),
        "headroom_per_period": headroom,
        "exceeds_headroom": (
            amount is not None
            and headroom is not None
            and amount > headroom
        ),
    }


# ── P2b: retire-later lever ──────────────────────────────────────


def _retire_later_lever(probe_at, months_override):
    """Assemble the retire-later lever's solved default and outcome (P2b).

    Binary search for the smallest whole-month offset at which
    :func:`_is_funded` holds, capped at :data:`_MAX_DELAY_MONTHS`.  The
    search assumes funded-ness is monotone in the delay (each extra month
    adds contributions, growth, and pension service on one side of the
    gap faster than the salary-grown target moves on the other -- the
    empirical shape of every scenario the suite pins); the two endpoint
    probes bracket the answer before bisecting.  Degenerate states are
    surfaced, never clamped: ``already_funded`` (offset 0) and
    ``not_within_cap`` (not funded even at +180, reported with the
    at-cap facts).

    Args:
        probe_at: The memoized ``month_offset -> _ProbeResult`` callable.
        months_override: Optional stepper offset; ``None`` displays the
            solved default (or the cap facts when unsolvable).

    Returns:
        dict with ``state`` (``solved`` / ``already_funded`` /
        ``not_within_cap``), ``solved_months`` (``None`` when
        unsolvable), ``months`` (the displayed offset, ``None`` only in
        the unsolvable no-override case), ``retirement_date``, and the
        funded / required / projected / surplus facts at the displayed
        offset.
    """
    baseline = probe_at(0)
    if _is_funded(baseline):
        state, solved_months = "already_funded", 0
    elif not _is_funded(probe_at(_MAX_DELAY_MONTHS)):
        state, solved_months = "not_within_cap", None
    else:
        low, high = 0, _MAX_DELAY_MONTHS
        # Invariant: not funded at ``low``, funded at ``high``.
        while high - low > 1:
            mid = (low + high) // 2
            if _is_funded(probe_at(mid)):
                high = mid
            else:
                low = mid
        state, solved_months = "solved", high

    months = months_override if months_override is not None else solved_months
    # The unsolvable no-override case displays the at-cap facts so the
    # caption can state how far even +180 months falls short.
    outcome = probe_at(months if months is not None else _MAX_DELAY_MONTHS)
    return {
        "state": state,
        "solved_months": solved_months,
        "months": months,
        "retirement_date": outcome.retirement_date,
        "funded_ratio": outcome.funded_ratio,
        "no_savings_needed": outcome.no_savings_needed,
        "required_savings": outcome.required,
        "projected_after_tax": outcome.after_tax_projected,
        "surplus_or_shortfall": (
            outcome.after_tax_projected - outcome.required
        ),
    }
