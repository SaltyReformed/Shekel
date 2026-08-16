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
  funded >= 100%.

**This module SOLVES; it no longer computes the picture it solves against**
(plan step C2-f2d-2, ledger row **P57**).  It held ``_ProbeInputs``, a second
loader, and ``_probe``, a second implementation of the retirement picture --
and on a default ``/retirement`` load its month-0 probe recomputed, from its
own 46 queries, the picture the readiness hero had already drawn.  A probe is
now :func:`~app.services.retirement_plan.picture_at` at a
:class:`~app.services.retirement_plan.PlanPoint` whose month offset varies,
so the baseline the levers compare against IS the object the hero rendered.

``compute_lever_data`` is the producer the P2c lever fragment endpoint
renders: it returns both levers' solved defaults plus the outcome facts
at caller-supplied stepper values.

All functions accept plain data and return plain data.  No Flask imports.
"""

from dataclasses import replace
from decimal import Decimal

from app.services import growth_engine
from app.services.pay_calendar import PayCadence
from app.services.retirement_gap_calculator import funded_ratio_for
from app.services.retirement_plan import STORED_PLAN, picture_at
from app.utils.money import ZERO, round_money

# Retire-later search cap: the largest month offset the P2b binary search
# probes (the audit's ratified bound).  A plan not funded even at +180
# months is reported as the honest ``not_within_cap`` state.
_MAX_DELAY_MONTHS = 180


def compute_lever_data(
    inputs, point=STORED_PLAN,
    contribution_override=None, months_override=None,
):
    """Compute both levers' solved defaults and stepper outcomes (P2a/P2b).

    **It runs on the render's own loaded inputs and derives the picture through
    the page's one producer** (plan step C2-f2d-2).  The ``/retirement`` route
    renders this beside the readiness verdict, and this module used to load
    every input again and recompute that verdict's picture as its month-0
    probe: 86 of the render's 179 queries on a production clone, and -- the
    part that is not about speed -- a second derivation of a displayed figure,
    agreeing with the first until one of them changed.

    Args:
        inputs: The render's
            :class:`~app.services.retirement_plan.RetirementInputs`, built once
            by the route.
        point: The :class:`~app.services.retirement_plan.PlanPoint` these
            levers solve FROM -- the assumptions the page is currently showing.
            Its ``month_offset`` is REPLACED per probe -- the retire-later
            lever owns that axis -- while the three what-if overrides ride
            through unchanged, so a lever solved while a slider is held is
            solved against the picture beside it rather than against the
            stored one.
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
    if inputs.base_date is None:
        # No pension date and no settings date: there is no horizon to
        # solve against.  P3 renders this as the page's empty state.  The
        # picture is not derived at all here, so a horizon-less owner pays
        # for none of the projection.
        return {
            "no_horizon": True,
            "tax_rate_missing": inputs.tax_rate_missing,
        }

    def probe_at(month_offset):
        """The picture at *point* delayed by *month_offset* whole months.

        Memoized by :func:`~app.services.retirement_plan.picture_at` on the
        render's inputs rather than in a cache of this function's own, which
        is what lets the search, the displayed outcome AND the readiness hero
        share one derivation of the month-0 picture.
        """
        return picture_at(inputs, replace(point, month_offset=month_offset))

    baseline = probe_at(0)
    funded_ratio, no_savings_needed = baseline.funded_state
    return {
        "no_horizon": False,
        "tax_rate_missing": inputs.tax_rate_missing,
        "baseline": {
            "funded_ratio": funded_ratio,
            "no_savings_needed": no_savings_needed,
            "required_savings": baseline.net.required_retirement_savings,
            "projected_after_tax": baseline.net.after_tax_projected_savings,
            "retirement_date": baseline.retirement_date,
        },
        "contribution": _contribution_lever(
            baseline, contribution_override,
        ),
        "retire_later": _retire_later_lever(probe_at, months_override),
    }


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


def _contribution_outcome(net, annuity_factor, amount):
    """Outcome facts for an extra *amount* per period, Roth-basis (F2).

    The stream's horizon value is ``amount * AF``; Roth-basis dollars land
    untaxed, so it adds to the AFTER-TAX projection dollar-for-dollar and
    the funded ratio re-derives against the unchanged requirement.

    It takes the ANALYSIS rather than the whole picture, because those two
    figures are every input it has: handing it a picture to read a required
    and a projected off would be stamp coupling, and it would force any test
    of this arithmetic to construct a projection it never looks at.

    Args:
        net: The month-0 picture's net-frame
            :class:`~app.services.retirement_gap_calculator.RetirementGapAnalysis`.
        annuity_factor: The horizon annuity factor.
        amount: The extra per-period contribution (Decimal >= 0).

    Returns:
        dict with ``projected_after_tax``, ``funded_ratio``,
        ``no_savings_needed``, and ``surplus_or_shortfall`` at *amount*.
    """
    required = net.required_retirement_savings
    projected = net.after_tax_projected_savings + round_money(
        amount * annuity_factor,
    )
    funded_ratio, no_savings_needed = funded_ratio_for(projected, required)
    return {
        "projected_after_tax": projected,
        "funded_ratio": funded_ratio,
        "no_savings_needed": no_savings_needed,
        "surplus_or_shortfall": projected - required,
    }


def _contribution_lever(baseline, contribution_override):
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
        baseline: The month-0
            :class:`~app.services.retirement_plan.RetirementPicture` -- its
            axis, its shortfall, its blended return and the owner's cadence
            are every input this lever has.
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
    # The annuity factor folds over exactly the window this picture projected
    # over, at exactly the return it grew at -- both read off the picture
    # rather than rebuilt beside it (plan steps C2-e and C2-f2d-2).  An annuity
    # factor over a different axis, or at a different rate, than the shortfall
    # it divides solves for a per-period contribution that does not close the
    # gap.
    annuity_factor = _annuity_factor(baseline.axis, baseline.blended_return)
    shortfall = (
        baseline.net.required_retirement_savings
        - baseline.net.after_tax_projected_savings
    )
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
        baseline.projections, baseline.pay_cadence,
    )
    return {
        "state": state,
        "solved_amount": solved_amount,
        "amount": amount,
        # A None amount (past_horizon, no override) evaluates the outcome
        # at $0 extra: with no periods the annuity factor is zero anyway,
        # so the facts are the baseline picture.
        **_contribution_outcome(
            baseline.net, annuity_factor,
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

    Binary search for the smallest whole-month offset at which the picture is
    :attr:`~app.services.retirement_plan.RetirementPicture.is_funded`, capped
    at :data:`_MAX_DELAY_MONTHS`.  The
    search assumes funded-ness is monotone in the delay (each extra month
    adds contributions, growth, and pension service on one side of the
    gap faster than the salary-grown target moves on the other -- the
    empirical shape of every scenario the suite pins); the two endpoint
    probes bracket the answer before bisecting.  Degenerate states are
    surfaced, never clamped: ``already_funded`` (offset 0) and
    ``not_within_cap`` (not funded even at +180, reported with the
    at-cap facts).

    Args:
        probe_at: The ``month_offset -> RetirementPicture`` callable, memoized
            on the render's inputs so a repeated offset costs nothing and the
            month-0 probe is the readiness hero's own picture.
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
    if probe_at(0).is_funded:
        state, solved_months = "already_funded", 0
    elif not probe_at(_MAX_DELAY_MONTHS).is_funded:
        state, solved_months = "not_within_cap", None
    else:
        low, high = 0, _MAX_DELAY_MONTHS
        # Invariant: not funded at ``low``, funded at ``high``.
        while high - low > 1:
            mid = (low + high) // 2
            if probe_at(mid).is_funded:
                high = mid
            else:
                low = mid
        state, solved_months = "solved", high

    months = months_override if months_override is not None else solved_months
    # The unsolvable no-override case displays the at-cap facts so the
    # caption can state how far even +180 months falls short.
    outcome = probe_at(months if months is not None else _MAX_DELAY_MONTHS)
    funded_ratio, no_savings_needed = outcome.funded_state
    required = outcome.net.required_retirement_savings
    projected = outcome.net.after_tax_projected_savings
    return {
        "state": state,
        "solved_months": solved_months,
        "months": months,
        "retirement_date": outcome.retirement_date,
        "funded_ratio": funded_ratio,
        "no_savings_needed": no_savings_needed,
        "required_savings": required,
        "projected_after_tax": projected,
        "surplus_or_shortfall": projected - required,
    }
