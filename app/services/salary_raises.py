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

**Every caller passes real ``SalaryRaise`` rows as of plan step salary:S3-c.**
The ``raises`` argument was duck-typed so the pension projector could
extrapolate over FABRICATED values (deep-hunt #83) -- it built a
``TerminatedRaise`` per row to carry a terminal year invented from
``auth.user_settings.merit_raise_horizon_years``.  Ruling **R-SAL11** made
that year a stored column on the row, so the fabrication, the value class it
produced and the setting it read are all deleted; the reads below are plain
attribute access on a row rather than defended lookups.
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

    **APPLICATIONS are ordered by the date each one lands on**, not by the
    raise they belong to.  Raise application is
    non-commutative (``(salary + flat) * pct`` != ``salary * pct +
    flat``), so the order is the answer, and until this step the order was
    wrong whenever an owner held a flat raise and a percentage raise at
    once: the raises were sorted, then EACH raise's whole run of yearly
    applications was applied before the next raise began.  A recurring flat
    ``$1,500`` COLA therefore contributed all of its additions up front and
    a recurring 4% merit raise then multiplied the lot -- including the
    COLA dollars that arrive in later years, which that percentage had not
    been earned on.

    The size of that is small inside the owner's saved pay calendar, which
    is why it stood: on a 3%-plus-flat pair it is ``$62.40`` by the second
    year.  It grows without bound over a projection.  The two-phase split
    ``pension_calculator.project_salaries_by_year`` used to apply its merit
    horizon happened to BOUND the error past the cutoff by re-basing on the
    cutoff salary, so the defect surfaced when that split was examined for
    removal; it was never a property of the horizon.

    Within a single date a flat raise still applies before a percentage one
    (M-01; deep-hunt #12 added the method tie-break the original M-01 fix
    specified but omitted, leaving same-date ties resolved by DB row
    order), and the number of times each raise applies is unchanged.  For
    an owner whose raises are all percentages the result is therefore
    identical to the previous rule, multiplication being commutative --
    which is every raise on the developer's own profile.

    A raise applies if:
    - Its effective_year is on or before ``as_of``'s year (recurring
      raises compound once per year from ``effective_year`` onward)
    - Its effective_month is on or before ``as_of``'s month (for that year)
    - It has not TERMINATED first -- see ``terminal_year`` below.  A
      terminated raise still applies; it stops accruing FURTHER
      applications after its last believed year.

    Args:
        base_salary: The pre-raise annual salary -- a Decimal, or any
            value ``Decimal(str(...))`` accepts.
        raises: An iterable of :class:`~app.models.salary_raise.SalaryRaise`
            rows, each exposing ``effective_year``, ``effective_month``,
            ``is_recurring``, ``percentage``, ``flat_amount`` and
            ``terminal_year`` -- the last year the raise is believed to
            happen, ``None`` meaning indefinitely.  A falsy/empty *raises*
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

    # Sorting by (year, month, method) puts every application in the order
    # the money actually arrived.  The list is of APPLICATIONS, not of
    # raises, which is the whole of this rule -- see this
    # function's docstring for what it corrects.  ``sorted`` is stable and
    # the key excludes the raise object, so two applications on one date
    # with one method keep their input order and nothing compares a
    # ``SalaryRaise`` against another.
    applications = sorted(
        _applications(raises, period_year, period_month),
        key=lambda a: a[:3],
    )

    for _, _, _, raise_obj in applications:
        salary = _apply_single_raise(salary, raise_obj)

    return round_money(salary)


def _is_believed_in(raise_obj, year):
    """Whether *raise_obj* is believed to happen in *year*.

    **The one place this project answers that question.**  It was answered
    twice until an adversarial review of plan step salary:S3-c -- once as a
    clamp inside :func:`_applications` and once as a comparison inside
    :func:`get_raise_event`, sixty lines apart, agreeing because they were
    read together rather than because anything held them to each other.  Two
    spellings of one rule are two spellings whether or not they agree today
    (CLAUDE.md rule 14), and the money either side of a disagreement is a
    projected paycheck against the banner announcing it.

    Args:
        raise_obj: A raise exposing ``effective_year`` and ``terminal_year``.
        year: The calendar year being asked about.

    Returns:
        ``True`` when *year* falls on or after the raise's effective year and
        on or before the last year it is believed -- ``terminal_year`` of
        ``None`` meaning there is no last year.
    """
    if year < raise_obj.effective_year:
        return False
    terminal_year = raise_obj.terminal_year
    return terminal_year is None or year <= terminal_year


def _applications(raises, period_year, period_month):
    """Yield one entry per raise APPLICATION, with the date it lands on.

    The unit :func:`apply_raises` orders by.  A recurring raise contributes
    one entry per year from its effective year through the last year whose
    effective month the caller's date has reached; a one-time raise
    contributes at most one.  The counts are exactly those the per-raise
    loop this replaced produced -- what changed is that they are now
    interleaved by DATE rather than grouped by raise.

    Args:
        raises: The raise objects, as :func:`apply_raises` documents them.
        period_year: The year the salary is being evaluated at.
        period_month: The month within that year.

    Yields:
        ``(year, month, method_rank, raise_obj)`` -- *method_rank* is 0 for
        a flat raise and 1 for a percentage one, which is how the
        documented flat-before-percentage order survives inside a single
        date (M-01 / deep-hunt #12).  A raise is exactly one method
        (``ck_salary_raises_one_method``) with a positive amount, so a
        truthy ``flat_amount`` uniquely marks the flat leg.
    """
    for raise_obj in raises:
        eff_year = raise_obj.effective_year
        eff_month = raise_obj.effective_month
        method_rank = 0 if raise_obj.flat_amount else 1

        if raise_obj.is_recurring:
            # Recurring raises compound each year at the specified month.
            # The last year that has LANDED is the caller's own, unless the
            # effective month has not been reached in it yet; whether each of
            # those years is still BELIEVED is :func:`_is_believed_in`'s
            # question, asked here rather than answered a second time.
            last_year = (
                period_year if period_month >= eff_month else period_year - 1
            )
            for year in range(eff_year, last_year + 1):
                if _is_believed_in(raise_obj, year):
                    yield year, eff_month, method_rank, raise_obj
        elif (
            (period_year > eff_year)
            or (period_year == eff_year and period_month >= eff_month)
        ) and _is_believed_in(raise_obj, eff_year):
            # One-time raise: it lands once, on its own effective date, and
            # only if that date falls within the years it is believed for.
            yield eff_year, eff_month, method_rank, raise_obj


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

    **It honours ``terminal_year`` as of plan step salary:S3-c**, which is an
    obligation that step INHERITED rather than created: plan step salary:S3-a
    added the field and named this function as the walk that did not know
    about it, so a recurring raise went on badging an event every year after
    its last believed one -- a banner on a paycheck whose gross the same field
    had already stopped moving.  **Both walks go through
    :func:`_is_believed_in`**, so they agree by construction rather than by
    being read together -- a draft of this paragraph claimed the weaker thing,
    and an adversarial review of this step took that admission as the finding
    it was.
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
                and _is_believed_in(raise_obj, period_year)):
            # A recurring raise recurs at eff_month every year from
            # eff_year onward and stops after the last year it is believed,
            # matching apply_raises' application gate at BOTH ends -- so it
            # must not badge an event in a calendar year before it takes
            # effect (deep-hunt #13) or after it is over (salary:S3-c).
            is_match = True
        elif eff_year == period_year and eff_month == period_month:
            # The one-time arm, which a terminated recurring raise cannot
            # fall through into: reaching here needs ``period_year ==
            # eff_year``, and ``ck_salary_raises_terminal_year_not_before_
            # effective`` guarantees a raise is still believed in its own
            # effective year.  A one-time raise carries no terminal year at
            # all (``ck_salary_raises_terminal_year_only_on_a_recurring_
            # raise``), so there is nothing to test here.
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
