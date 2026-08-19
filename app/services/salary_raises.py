"""How a salary RAISE changes an annual salary, and when it is an event.

Split out of :mod:`app.services.paycheck_calculator` at plan step **R-F16**,
which is the step that pushed that module past its 1000-line ceiling.  The
split is by CONCERN rather than by line count: applying raises is a rule about
``salary.salary_raises`` rows that two unrelated engines consume -- the
paycheck pipeline (:func:`~app.services.paycheck_calculator.calculate_paycheck`
/ ``project_salary``) and the pension salary projection
(:func:`app.services.pension_calculator.project_salaries_by_year`) -- and the
second of those already had to import it across the module boundary.  It
computes no paycheck and reads no cadence, so it never belonged to the
engine's own file.

Pure: plain inputs, plain outputs, no Flask, no ORM, no clock, no database.
The ``raises`` argument is an iterable of duck-typed raise objects rather than
``SalaryRaise`` rows, which is what lets the pension projector extrapolate over
fabricated ones (deep-hunt #83).
"""
from decimal import Decimal

from app.utils.money import round_money


def apply_raises(base_salary, raises, as_of):
    """Return the effective annual salary as of a date, after applying raises.

    The shared raise-application rule used by both the paycheck pipeline
    (:func:`app.services.paycheck_calculator.calculate_paycheck` /
    ``project_salary``) and the pension salary projection
    (:func:`app.services.pension_calculator.project_salaries_by_year`).
    Promoted from the former private ``_apply_raises(profile, period)`` to
    plain inputs so the pension projector no longer reaches into a private
    symbol with fabricated duck-typed objects (deep-hunt #83).

    Raises are sorted by (effective_year, effective_month, method)
    before application -- the method key sorts flat raises ahead of
    percentage raises -- so that within the same effective date a flat
    raise applies before a percentage raise.  Raise application is
    non-commutative (``(salary + flat) * pct`` != ``salary * pct +
    flat``), so this makes the result deterministic regardless of
    database query order (M-01; deep-hunt #12 added the method
    tie-break the original M-01 fix specified but omitted, leaving
    same-date ties resolved by DB row order).

    A raise applies if:
    - Its effective_year is on or before ``as_of``'s year (recurring
      raises compound once per year from ``effective_year`` onward)
    - Its effective_month is on or before ``as_of``'s month (for that year)

    Args:
        base_salary: The pre-raise annual salary -- a Decimal, or any
            value ``Decimal(str(...))`` accepts.
        raises: An iterable of raise objects, each exposing
            ``effective_year``, ``effective_month``, ``is_recurring``,
            ``percentage``, and ``flat_amount``.  A falsy/empty value
            returns ``base_salary`` unchanged (unquantized, matching the
            prior behavior).
        as_of: The :class:`datetime.date` the salary is evaluated at;
            only its ``year`` and ``month`` are consulted (day ignored).

    Returns:
        Decimal -- the post-raise annual salary, quantized to cents
        (ROUND_HALF_UP) when any raise applied.
    """
    salary = Decimal(str(base_salary))

    if not raises:
        return salary

    period_year = as_of.year
    period_month = as_of.month

    sorted_raises = sorted(
        raises,
        key=lambda r: (
            r.effective_year,
            r.effective_month,
            # Flat raises sort ahead of percentage within one effective
            # date so the documented flat-before-percentage order holds
            # regardless of DB row order (M-01 / deep-hunt #12).  A raise
            # is exactly one method (ck_salary_raises_one_method) with a
            # positive amount, so a truthy flat_amount uniquely marks the
            # flat leg.
            0 if r.flat_amount else 1,
        ),
    )

    for raise_obj in sorted_raises:
        eff_year = raise_obj.effective_year
        eff_month = raise_obj.effective_month

        if raise_obj.is_recurring:
            # Recurring raises compound each year at the specified month.
            # Count total applications: one per year from eff_year onward
            # where the effective month has been reached.
            if period_year >= eff_year:
                total_applications = period_year - eff_year
                if period_month >= eff_month:
                    total_applications += 1
                for _ in range(total_applications):
                    salary = _apply_single_raise(salary, raise_obj)
        else:
            # One-time raise: apply if we're at or past the effective date.
            if (period_year > eff_year) or (
                period_year == eff_year and period_month >= eff_month
            ):
                salary = _apply_single_raise(salary, raise_obj)

    return round_money(salary)


def _apply_single_raise(salary, raise_obj):
    """Apply a single raise (percentage or flat) to the salary."""
    if raise_obj.percentage:
        pct = Decimal(str(raise_obj.percentage))
        return salary * (1 + pct)
    if raise_obj.flat_amount:
        return salary + Decimal(str(raise_obj.flat_amount))
    return salary


def get_raise_event(profile, period):
    """Return a description of any raise event occurring in this period.

    Public because two consumers now need a period's raise event: the paycheck
    engine (:func:`app.services.paycheck_calculator.calculate_paycheck`, when
    it builds each ``PeriodInfo``) and the salary cockpit route, which compares
    the focused period's event against its
    predecessor's to collapse the raise banner to one paycheck per run
    (P-SA1) without projecting every period.  Pure over ``profile.raises``
    and ``period.start_date`` -- no breakdown, no DB, no ``float``.
    """
    if not profile.raises:
        return ""

    period_year = period.start_date.year
    period_month = period.start_date.month
    events = []

    for raise_obj in profile.raises:
        eff_month = raise_obj.effective_month
        eff_year = raise_obj.effective_year

        is_match = False
        if (raise_obj.is_recurring and period_month == eff_month
                and period_year >= eff_year):
            # A recurring raise recurs at eff_month every year from
            # eff_year onward, matching apply_raises' application gate --
            # so it must not badge an event in a calendar year before it
            # takes effect (deep-hunt #13).
            is_match = True
        elif eff_year == period_year and eff_month == period_month:
            is_match = True

        if is_match:
            raise_type = raise_obj.raise_type.name if raise_obj.raise_type else "raise"
            if raise_obj.percentage:
                pct = Decimal(str(raise_obj.percentage)) * 100
                events.append(f"{raise_type.upper()} +{pct}%")
            else:
                events.append(f"{raise_type.upper()} +${raise_obj.flat_amount:,.2f}")

    return ", ".join(events)

__all__ = ["apply_raises", "get_raise_event"]
