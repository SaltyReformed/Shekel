"""
Shekel Budget App -- YTD Tax Withholding Service

The withholding-to-date half of the analytics Taxes tab (T-P2).  Where
``tax_liability_service`` (T-P1) computes what a profile OWES at filing,
this module computes what has been (and will be) WITHHELD across the tax
year, so the refund producer (T-P3) can form ``refund = withheld -
liability``.

The audit's rule (analytics_audit.md, "Tax refund estimate"):
**withholding-to-date = measured checkpoint + calibrated projection for
the remainder.**  A user reads the five year-to-date figures (gross plus
federal / state / Social Security / Medicare withholding) off a real pay
stub and saves a :class:`~app.models.ytd_tax_checkpoint.YtdTaxCheckpoint`;
the periods that stub already covers are MEASURED (taken verbatim from the
checkpoint), and only the periods after the stub date are MODELED, through
the same ``paycheck_calculator.project_salary`` path the paycheck engine
uses (so an active calibration override applies automatically).  With no
checkpoint the whole year is modeled.

This module owns the checkpoint CRUD (:func:`latest_checkpoint`,
:func:`save_checkpoint`) and the producer (:func:`compute_withholding_to_date`).
It performs no tax arithmetic of its own -- the projection is delegated to
``paycheck_calculator`` in full -- and never imports Flask (the pay periods
for the year are PASSED IN by the caller, mirroring
``tax_liability_service`` and the year-end summary).

Period-partition rule (the load-bearing edge behaviour):

* A period's ``start_date`` IS its payday.  A period is "already measured"
  -- covered by the checkpoint and excluded from the modeled remainder --
  when its payday is ON OR BEFORE the checkpoint date.  The remainder is
  therefore the periods with ``start_date > checkpoint.as_of_date``
  (strict), so:
  - a checkpoint dated exactly ON a payday INCLUDES that payday's paycheck
    in the measured figures (that period is NOT re-modeled), and
  - a checkpoint dated mid-period, after a payday that already happened,
    does NOT double-count that paycheck (the passed payday is in the stub).
* An empty period list -> an all-zero modeled remainder.
* A checkpoint whose ``as_of_date`` falls in a DIFFERENT year is ignored
  (``latest_checkpoint`` scopes to the requested year).

Year-cumulative engine context (how the remainder is computed):

The partition above decides WHICH paydays are modeled, and since plan step
**balance:X-bh-1** the projection prices exactly those.  It ran
``project_salary`` over the ENTIRE year's period list and filtered the
remainder's breakdowns back out, because the engine derived its
year-cumulative state from the period list it was handed: cumulative wages
drive the Social Security wage-base cap and the Medicare surtax threshold,
and the month grouping drives 3rd-paycheck detection and monthly-capped
deductions (which feed pre-tax and therefore federal withholding).
Projecting over only the remainder would have restarted all of that at zero
mid-year -- a high earner who already crossed the SS cap in the measured half
would have been re-charged SS across the remainder.  **That state now comes
off the owner's pay CALENDAR**, which no caller can narrow, so the same
figures come out of a projection that prices only what it sums.

Residual approximation, stated honestly: the remainder's cumulative
context comes from the MODELED elapsed paychecks (the engine replays the
year from the profile), not from the checkpoint's measured ``ytd_gross``.
When the stub's actual gross differs from the modeled gross, the
cap-crossing paycheck can shift by that difference.  Exact when the two
coincide, and strictly better than restarting the year at zero.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from app.services import paycheck_calculator
from app.services.payroll_basis import PayrollBasis
from app.services.tax_config_service import load_tax_configs_for_year

ZERO = Decimal("0")


@dataclass(frozen=True)
class WithholdingComponents:
    """The five year-to-date figures: gross plus the four withholding lines.

    The same five-field shape carried by both the MEASURED checkpoint and
    the MODELED remainder, so the T-P4 card renders either side (and their
    sum) without knowing which is which.
    """

    gross: Decimal
    federal: Decimal
    state: Decimal
    social_security: Decimal
    medicare: Decimal


# The all-zero components: a fully-modeled year's measured side, and an
# empty remainder's projected side.
_ZERO_COMPONENTS = WithholdingComponents(ZERO, ZERO, ZERO, ZERO, ZERO)


@dataclass(frozen=True)
class CheckpointFigures:
    """The user-entered figures for one YTD tax checkpoint (upsert payload).

    Bundles the six writable columns of
    :class:`~app.models.ytd_tax_checkpoint.YtdTaxCheckpoint` so
    :func:`save_checkpoint` takes a single cohesive value rather than a
    long positional argument list.  The non-negativity and
    ``component <= gross`` invariants are enforced upstream (the
    Marshmallow schema) and at the storage tier (the table's CHECK
    constraints); this dataclass carries already-validated Decimals.
    """

    as_of_date: date
    ytd_gross: Decimal
    ytd_federal: Decimal
    ytd_state: Decimal
    ytd_social_security: Decimal
    ytd_medicare: Decimal
    notes: str | None = None


@dataclass(frozen=True)
class WithholdingToDate:
    """Withholding-to-date for one profile and tax year.

    All three sides carry the same five-figure shape.  ``total`` is the
    figure the refund producer subtracts liability from (measured
    checkpoint + modeled remainder); ``measured`` and ``projected`` are
    those two sources kept apart so the T-P4 card shows the
    measured-vs-projected breakdown with no arithmetic of its own
    (``total.<line> == measured.<line> + projected.<line>`` by
    construction, for each of the five figures).

    ``measured_through`` is the checkpoint's ``as_of_date`` (the date the
    measured figures run through) or ``None`` when the year is fully
    modeled; ``checkpoint`` is the source row (or ``None``) for the card's
    "as of <date>" caption and the deferred estimate-convergence history.
    """

    total: WithholdingComponents
    measured: WithholdingComponents
    projected: WithholdingComponents
    measured_through: date | None
    checkpoint: YtdTaxCheckpoint | None


def latest_checkpoint(profile_id: int, year: int) -> YtdTaxCheckpoint | None:
    """Return the profile's latest YTD checkpoint dated within *year*.

    "Latest" is the maximum ``as_of_date`` among the profile's checkpoints
    whose ``as_of_date`` falls in the calendar *year*; the
    ``(salary_profile_id, as_of_date)`` unique constraint guarantees no tie
    on that date.  A checkpoint dated in a different year is not a candidate
    -- last year's stub says nothing about this year's withholding.

    Args:
        profile_id: The owning salary profile's id.  The caller is
            responsible for having verified the profile belongs to the
            requesting user (this is a plain data read, no Flask context).
        year: The calendar/tax year to scope the checkpoint search to.

    Returns:
        The latest in-year :class:`YtdTaxCheckpoint`, or ``None`` when the
        profile has no checkpoint dated within *year*.
    """
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    return (
        db.session.query(YtdTaxCheckpoint)
        .filter(
            YtdTaxCheckpoint.salary_profile_id == profile_id,
            YtdTaxCheckpoint.as_of_date >= year_start,
            YtdTaxCheckpoint.as_of_date <= year_end,
        )
        .order_by(YtdTaxCheckpoint.as_of_date.desc())
        .first()
    )


def save_checkpoint(
    profile_id: int, figures: CheckpointFigures,
) -> YtdTaxCheckpoint:
    """Upsert a YTD checkpoint on ``(profile_id, as_of_date)``.

    Re-entering a stub for a date that already has a checkpoint REPLACES
    that row's figures (an in-place update -- the same stub corrected);
    a new date INSERTS a new row (the history the estimate-convergence
    chart needs).  ``(salary_profile_id, as_of_date)`` is unique, so the
    lookup below finds at most one existing row.

    The row is flushed (so a subsequent read in the same request sees it
    and the DB CHECK constraints fire here rather than at commit), but NOT
    committed -- the calling route owns the transaction boundary, matching
    the calibration-confirm handler.

    Args:
        profile_id: The owning salary profile's id (ownership verified by
            the caller).
        figures: The validated :class:`CheckpointFigures` to persist.

    Returns:
        The inserted or updated :class:`YtdTaxCheckpoint` (flushed).
    """
    checkpoint = (
        db.session.query(YtdTaxCheckpoint)
        .filter_by(
            salary_profile_id=profile_id,
            as_of_date=figures.as_of_date,
        )
        .first()
    )
    if checkpoint is None:
        checkpoint = YtdTaxCheckpoint(
            salary_profile_id=profile_id,
            as_of_date=figures.as_of_date,
        )
        db.session.add(checkpoint)

    checkpoint.ytd_gross = figures.ytd_gross
    checkpoint.ytd_federal = figures.ytd_federal
    checkpoint.ytd_state = figures.ytd_state
    checkpoint.ytd_social_security = figures.ytd_social_security
    checkpoint.ytd_medicare = figures.ytd_medicare
    checkpoint.notes = figures.notes

    db.session.flush()
    return checkpoint


def year_paydays(calendar, year: int) -> tuple:
    """Return the owner's SAVED periods whose payday falls in *year*.

    **The single "which paychecks does this tax year hold" rule.**  It lived
    in ``tax_report_service`` and was passed IN here beside the calendar it
    was derived from until plan step **balance:X-bh-1**, so three arguments --
    ``periods``, ``year`` and ``calendar`` -- encoded one fact with nothing
    reconciling them.  An adversarial review of that step named it as the same
    shape the step had just deleted from the paycheck engine one layer down,
    and worse for being newly LOUD: ``_month_ordinal`` now refuses a paycheck
    its calendar cannot place, so a mismatched pair became a 500 on
    ``/analytics/taxes`` where it had been a wrong number.

    It is HERE rather than in ``tax_report_service`` because that module
    already imports this one, so the dependency stays one-way.

    Args:
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`.
        year: The calendar/tax year to scope paydays to.

    Returns:
        The year's :class:`~app.services.pay_calendar.DerivedPeriod` values as
        a tuple, possibly empty.  A tuple rather than a
        :class:`~app.services.pay_calendar.PeriodWindow`: a year slice is a
        FILTER of the calendar, and the window type is produced only by the
        calendar's own view methods.
    """
    return tuple(
        period for period in calendar.saved()
        if period.start_date.year == year
    )


def compute_withholding_to_date(
    user_id: int, profile, year: int, calendar,
) -> WithholdingToDate:
    """Compute withholding-to-date = measured checkpoint + modeled remainder.

    Anchors on the profile's latest in-year checkpoint (its five YTD
    figures are the MEASURED side) and models only the periods the stub
    does not cover (``start_date > checkpoint.as_of_date``) through
    ``paycheck_calculator.project_salary``, which applies the profile's
    active calibration automatically.  With no checkpoint the measured side
    is zero and the whole ``periods`` list is modeled.

    The projection uses the same per-year tax configs SSOT
    (:func:`load_tax_configs_for_year`) and the same ``project_salary``
    path the paycheck engine and the year-end summary use, so this module
    re-implements no tax arithmetic.

    Args:
        user_id: The owning user's id (tax configs are per-user).
        profile: The :class:`~app.models.salary_profile.SalaryProfile`, with
            its ``raises``, ``deductions``, and ``calibration`` relationships
            available (read by ``project_salary``).
        year: The tax year.  The paydays it covers are derived here from
            *calendar* through :func:`year_paydays`, rather than passed in
            beside it -- see that function for what the third argument cost.
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar` -- the paycheck
            count the engine divides the annual salary by and the payday set
            its year-cumulative state is counted over.  It was a bare
            :class:`~app.services.pay_calendar.PayCadence` until plan step
            **balance:X-bh-1**, taken beside the profile so this function
            could decide whether a projection was needed BEFORE anything
            asked for a cadence an owner may never have stated.  A calendar
            can always be built, so that ordering is no longer load-bearing:
            :attr:`~app.services.payroll_basis.PayrollBasis.periods_per_year`
            resolves the cadence on read, and an owner with no cadence has no
            payday, so they reach the all-zero remainder below without one
            ever being asked for.

    Returns:
        The populated :class:`WithholdingToDate` (totals + measured /
        projected split + the source checkpoint).
    """
    checkpoint = latest_checkpoint(profile.id, year)
    measured = _measured_components(checkpoint)
    remainder = _remainder_periods(year_paydays(calendar, year), checkpoint)
    projected = (
        _project_remainder(
            user_id, PayrollBasis(profile, calendar), year, remainder,
        )
        if remainder
        else _ZERO_COMPONENTS
    )

    total = WithholdingComponents(
        gross=measured.gross + projected.gross,
        federal=measured.federal + projected.federal,
        state=measured.state + projected.state,
        social_security=measured.social_security + projected.social_security,
        medicare=measured.medicare + projected.medicare,
    )
    return WithholdingToDate(
        total=total,
        measured=measured,
        projected=projected,
        measured_through=checkpoint.as_of_date if checkpoint is not None else None,
        checkpoint=checkpoint,
    )


def _measured_components(
    checkpoint: YtdTaxCheckpoint | None,
) -> WithholdingComponents:
    """Return the measured components from *checkpoint*, or all zeros.

    The five ``ytd_*`` columns are ``Numeric(12, 2)`` and load as
    ``Decimal``, so they feed the component sums directly.

    Args:
        checkpoint: The measured checkpoint, or ``None`` (fully-modeled
            year).

    Returns:
        The checkpoint's five figures as a :class:`WithholdingComponents`,
        or :data:`_ZERO_COMPONENTS` when there is no checkpoint.
    """
    if checkpoint is None:
        return _ZERO_COMPONENTS
    return WithholdingComponents(
        gross=checkpoint.ytd_gross,
        federal=checkpoint.ytd_federal,
        state=checkpoint.ytd_state,
        social_security=checkpoint.ytd_social_security,
        medicare=checkpoint.ytd_medicare,
    )


def _remainder_periods(
    periods: list, checkpoint: YtdTaxCheckpoint | None,
) -> tuple:
    """Return the periods a checkpoint does NOT cover.

    The module's partition rule, and its own function since plan step
    **R-F16**: a period is MODELLED when its payday ``start_date`` is STRICTLY
    after the checkpoint date, and every period is modelled when there is no
    checkpoint.  Split out of :func:`_project_remainder` so the caller can ask
    "is there anything to project?" before doing any of the work.

    **It returns the PERIODS rather than their ids** since plan step
    **balance:X-bh-1**.  The ids existed to filter breakdowns back out of a
    FULL-year projection, which the engine no longer needs: its year-cumulative
    state comes off the owner's calendar rather than off the list it is handed,
    so the remainder can simply be projected.

    Args:
        periods: The tax year's pay periods (may be empty).
        checkpoint: The measured checkpoint, or ``None``.

    Returns:
        The periods to model, in the order given.  Empty when the checkpoint
        covers the whole list, and when the list itself is empty.
    """
    return tuple(
        p for p in periods
        if checkpoint is None or p.start_date > checkpoint.as_of_date
    )


def _project_remainder(
    user_id: int,
    basis,
    year: int,
    remainder: tuple,
) -> WithholdingComponents:
    """Price the remainder's paychecks and sum their withholding.

    **It projects ONLY the remainder since plan step balance:X-bh-1**, and the
    reason it used to project the whole year is gone rather than waived.  The
    engine derived its year-cumulative state from the period list it was
    handed -- cumulative wages for the SS wage-base cap and the Medicare surtax
    threshold, and month grouping for 3rd-paycheck detection and
    monthly-capped deductions -- so pricing only the remainder would have
    restarted all of it at zero mid-year, and a high earner who had already
    crossed the SS cap in the measured half would have been re-charged SS
    across the remainder.  That state now comes off ``basis.calendar``, which
    the measured half does not narrow, so the full-year projection and its
    filter-back-out bought nothing but the work.  The figures are unchanged:
    every breakdown the filter kept is the breakdown this prices.

    **The caller decides whether to call this at all**, on
    :func:`_remainder_periods`: an empty remainder means nothing to model, and
    ``project_salary`` is not free on a 26-period year.

    Args:
        user_id: The owning user's id (per-user tax configs).
        basis: The :class:`~app.services.payroll_basis.PayrollBasis` to
            project -- the salary profile bound to its owner's pay calendar,
            calibration-aware via ``basis.profile.calibration``.
        year: The tax year whose configs to load.
        remainder: The non-empty tuple of periods to price, from
            :func:`_remainder_periods`.

    Returns:
        The summed modeled remainder as a :class:`WithholdingComponents`.
    """
    tax_configs = load_tax_configs_for_year(user_id, basis.profile, year)
    breakdowns = paycheck_calculator.project_salary(
        basis, remainder, tax_configs,
        calibration=basis.profile.calibration,
    )
    return WithholdingComponents(
        gross=sum((bd.earnings.gross_biweekly for bd in breakdowns), ZERO),
        federal=sum((bd.taxes.federal for bd in breakdowns), ZERO),
        state=sum((bd.taxes.state for bd in breakdowns), ZERO),
        social_security=sum(
            (bd.taxes.social_security for bd in breakdowns), ZERO,
        ),
        medicare=sum((bd.taxes.medicare for bd in breakdowns), ZERO),
    )
