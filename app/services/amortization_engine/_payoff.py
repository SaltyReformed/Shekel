"""Payoff-by-date analysis on top of the projection primitives.

The question layer of the amortization engine: "what extra monthly
payment retires this loan by a target date?".  Builds on
:mod:`._projection`'s :func:`project_forward` -- a baseline run plus a
binary search over ``extra_monthly`` -- and honors an optional
committed-plan ``monthly_override`` (F-27) so the loan resolver's
``target_date_outlook`` can answer relative to the user's recurring
payments.  Pure functions, no database access.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.utils.dates import months_between
from app.utils.money import round_money

from ._projection import (
    PeriodTerms,
    ProjectionInputs,
    advance_to_next_payment_date,
    project_forward,
)

@dataclass(frozen=True)
class PayoffRequest:
    """Everything needed to answer "what extra payment hits this date?".

    Bundles the loan's current state, contractual terms feed, and the
    target payoff date into one immutable request so
    :func:`calculate_payoff_by_date` takes a single cohesive argument.
    The function derives a :class:`ProjectionInputs` from this request
    and runs repeated projections against it; the request is the
    higher-level "loan facts plus the question" object, distinct from
    the lower-level projection primitive.

    The rate and contractual P&I for every projected month come from
    ``terms_schedule`` -- the same rate-period figures the loan card
    displays -- so the rendered ``total_monthly = monthly_payment +
    required_extra`` pairs with the card by construction (the D-2
    closure, now structural: there is no separate contractual input to
    diverge).

    Attributes:
        current_principal: Outstanding balance to project from.
            ``<= 0`` makes the function return ``Decimal("0.00")``.
        remaining_months: Months remaining on the loan.  The
            projection's loop cap and the date-delta boundary for the
            "already past / too soon" guards.
        target_date: The user's desired payoff date.
        origination_date: Anchor for the projection's first payment
            date (``origination_date + 1 month`` clamped to
            ``payment_day``).  The caller is responsible for passing
            "today's first of month" when projecting from current
            state rather than from real origination (see
            ``app.routes.loan.calculators.payoff_calculate`` target-date branch).
        payment_day: Day-of-month payments are due.
        terms_schedule: Non-empty :class:`PeriodTerms` list -- each
            month's rate and contractual P&I (the production path maps
            it from the resolver's rate periods via
            ``loan_resolver.engine_terms``; a fixed-rate caller passes
            one entry).
    """

    current_principal: Decimal
    remaining_months: int
    target_date: date
    origination_date: date
    payment_day: int
    terms_schedule: list[PeriodTerms]


def _search_extra_for_payoff(
    projection_inputs: ProjectionInputs,
    target_date: date,
    upper_bound: Decimal,
    monthly_override: dict[tuple[int, int], Decimal] | None = None,
) -> Decimal:
    """Binary-search the extra monthly payment that pays off by target_date.

    Repeats the forward projection with a candidate ``extra_monthly``,
    narrowing the bracket below one cent (the legacy convergence
    criterion).  The caller guarantees the baseline schedule pays off
    AFTER ``target_date`` within the loan's remaining months, so the
    search always has a valid bracket to converge in.

    Args:
        projection_inputs: The shared starting state every iteration
            projects from; only ``extra_monthly`` varies between calls,
            so the iterations cannot diverge in their other inputs.
        target_date: The desired payoff date the search drives toward.
        upper_bound: The initial high bracket -- the current principal
            (paying it all off immediately is the trivial upper bound).
        monthly_override: Optional ``(year, month) -> Decimal``
            planned-outlay map.  Override months replace contractual
            AND suppress the searched ``extra_monthly``, so the search
            finds the extra needed on NON-override months on top of
            the user's plan (F-27).

    Returns:
        The Decimal extra-monthly payment, rounded to cents, that
        achieves payoff at or before ``target_date``.
    """
    lo = Decimal("0.01")
    hi = upper_bound  # Upper bound: pay it all off immediately.

    for _ in range(100):  # Max iterations for convergence.
        mid = round_money((lo + hi) / 2)
        schedule = project_forward(
            projection_inputs,
            monthly_override=monthly_override,
            extra_monthly=mid,
        )
        if not schedule:
            return mid

        actual_payoff = schedule[-1].payment_date
        if actual_payoff <= target_date:
            hi = mid
        else:
            lo = mid

        if hi - lo <= Decimal("0.01"):
            break

    return hi


def required_extra_for_projection(
    projection_inputs: ProjectionInputs,
    target_date: date,
    *,
    monthly_override: dict[tuple[int, int], Decimal] | None = None,
) -> Decimal | None:
    """Required extra-monthly payment to retire a projection by a date.

    The reusable core of :func:`calculate_payoff_by_date`, factored out
    so the loan resolver's committed-plan path (F-27) can answer the
    same question from ITS replay-derived starting state with the
    planned-outlay ``monthly_override`` -- one starting state then
    drives both the committed payoff date and the additional-extra
    search, so the two figures cannot rest on diverging projections.

    Args:
        projection_inputs: The starting state every projection in the
            answer shares (baseline run and each search iteration).
            Its ``remaining_months`` is also the "target too far out"
            gate boundary.
        target_date: The desired payoff date.
        monthly_override: Optional ``(year, month) -> Decimal``
            planned-outlay map, honored by the baseline run and the
            search alike, so the returned extra is the amount needed
            ON TOP of the user's committed plan (in-window; beyond the
            plan's horizon months revert to contractual, the committed-
            scenario convention).

    Returns:
        ``None`` if ``target_date`` is in the past.  ``Decimal("0.00")``
        when no extra is required (the baseline -- contractual or
        committed-plan -- schedule already pays off by ``target_date``,
        or the loan is already paid off).  Otherwise the binary-searched
        extra-monthly payment, rounded to cents.
    """
    gate_months = projection_inputs.remaining_months

    baseline = project_forward(
        projection_inputs,
        monthly_override=monthly_override,
        extra_monthly=Decimal("0.00"),
    )
    if not baseline:
        return Decimal("0.00")

    if baseline[-1].payment_date <= target_date:
        return Decimal("0.00")

    # Months until target_date from the first payment; the inclusive
    # ``+ 1`` keeps the legacy gates firing for the same inputs.
    target_months = months_between(
        projection_inputs.starting_date, target_date,
    ) + 1

    if target_months <= 0:
        return None  # Target date is in the past.

    if target_months >= gate_months:
        return Decimal("0.00")

    return _search_extra_for_payoff(
        projection_inputs,
        target_date,
        projection_inputs.starting_balance,
        monthly_override=monthly_override,
    )


def calculate_payoff_by_date(
    request: PayoffRequest,
) -> Decimal | None:
    """Calculate required extra monthly payment to pay off by target_date.

    The raw (no committed plan) answer: builds one shared
    :class:`ProjectionInputs` from the request's loan facts -- the
    rate/P&I terms feed, the current balance, the remaining months --
    then delegates the baseline projection, gates, and binary search to
    :func:`required_extra_for_projection` (whose ``monthly_override``
    mode is the F-27 committed-plan path used by
    ``loan_resolver.target_date_outlook``).

    Args:
        request: A :class:`PayoffRequest` bundling the loan's current
            state, the rate-period terms feed, and the target payoff
            date.  See :class:`PayoffRequest` for per-field semantics.

    Returns:
        :func:`required_extra_for_projection`'s contract: ``None`` for
        a past target, ``Decimal("0.00")`` when no extra is needed,
        otherwise the binary-searched extra rounded to cents.
    """
    if request.current_principal <= 0 or request.remaining_months <= 0:
        return Decimal("0.00")

    starting_date = advance_to_next_payment_date(
        request.origination_date, request.payment_day,
    )

    # One shared starting state for the baseline run and every search
    # iteration -- they cannot diverge in their shared inputs.
    projection_inputs = ProjectionInputs(
        starting_balance=request.current_principal,
        starting_date=starting_date,
        remaining_months=request.remaining_months,
        payment_day=request.payment_day,
        terms_schedule=request.terms_schedule,
    )

    return required_extra_for_projection(
        projection_inputs,
        request.target_date,
    )
