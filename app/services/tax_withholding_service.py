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

Full-year engine context (how the remainder is computed):

The partition above decides WHICH paydays are modeled; the projection
itself runs ``project_salary`` over the ENTIRE year's period list and then
sums only the remainder periods' breakdowns.  The paycheck engine derives
its year-cumulative state from the period list it is handed: cumulative
wages drive the Social Security wage-base cap and the Medicare surtax
threshold, and the month grouping drives 3rd-paycheck detection and
monthly-capped deductions (which feed pre-tax and therefore federal
withholding).  Projecting over only the remainder would restart all of
that at zero mid-year -- a high earner who already crossed the SS cap in
the measured half would be re-charged SS across the remainder.  Full-list
projection matches how the year-end summary projects the same year.

Residual approximation, stated honestly: the remainder's cumulative
context comes from the MODELED elapsed periods (the engine replays the
year from the profile), not from the checkpoint's measured ``ytd_gross``.
When the stub's actual gross differs from the modeled gross, the
cap-crossing period can shift by that difference.  Exact when the two
coincide, and strictly better than restarting the year at zero.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from app.services import paycheck_calculator
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


def compute_withholding_to_date(
    user_id: int, profile, year: int, periods: list,
) -> WithholdingToDate:
    """Compute withholding-to-date = measured checkpoint + modeled remainder.

    Anchors on the profile's latest in-year checkpoint (its five YTD
    figures are the MEASURED side) and models only the periods the stub
    does not cover (``start_date > checkpoint.as_of_date``) through
    ``paycheck_calculator.project_salary``, which applies the profile's
    active calibration automatically.  With no checkpoint the measured side
    is zero and the whole ``periods`` list is modeled.  The projection runs
    over the FULL year's period list so the engine's year-cumulative state
    (SS wage-base cap, Medicare surtax, monthly deduction caps) is intact;
    only the remainder periods' breakdowns are summed (see the module
    docstring's "Full-year engine context").

    The projection uses the same per-year tax configs SSOT
    (:func:`load_tax_configs_for_year`) and the same ``project_salary``
    path the paycheck engine and the year-end summary use, so this module
    re-implements no tax arithmetic.

    Args:
        user_id: The owning user's id (tax configs are per-user).
        profile: The :class:`~app.models.salary_profile.SalaryProfile`,
            with its ``raises``, ``deductions``, and ``calibration``
            relationships available (read by ``project_salary``).
        year: The tax year; every period in *periods* is expected to fall
            in it (the caller loads the year's periods).
        periods: The tax year's :class:`~app.services.pay_calendar.DerivedPeriod`
            list.  PASSED IN (this module performs no pay-period query);
            an empty list yields an all-zero modeled remainder.

    Returns:
        The populated :class:`WithholdingToDate` (totals + measured /
        projected split + the source checkpoint).
    """
    checkpoint = latest_checkpoint(profile.id, year)
    measured = _measured_components(checkpoint)
    projected = _project_remainder(user_id, profile, year, periods, checkpoint)

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


def _project_remainder(
    user_id: int,
    profile,
    year: int,
    periods: list,
    checkpoint: YtdTaxCheckpoint | None,
) -> WithholdingComponents:
    """Project the year with full context and sum the remainder's withholding.

    Partitions *periods* by the module's rule (a period is modeled when its
    payday ``start_date`` is STRICTLY after the checkpoint date; every
    period when *checkpoint* is ``None``), then delegates to
    ``paycheck_calculator.project_salary`` over the FULL *periods* list --
    NOT just the remainder -- so the engine's year-cumulative state is
    intact: cumulative wages for the SS wage-base cap and the Medicare
    surtax threshold, and month grouping for 3rd-paycheck detection and
    monthly-capped deductions (see the module docstring's "Full-year
    engine context").  Only the remainder periods' breakdowns are summed.

    The filter keys on ``PaycheckBreakdown.period.period_id``:
    ``project_salary`` returns exactly one breakdown per passed period,
    each stamped with its own period's id via :class:`PeriodInfo`, so the
    breakdown-to-period pairing is the engine's own contract rather than
    positional zip order.

    An empty remainder short-circuits to all zeros before any projection
    (nothing to model, and ``project_salary`` is not free on a 26-period
    year).

    Args:
        user_id: The owning user's id (per-user tax configs).
        profile: The salary profile to project (calibration-aware via
            ``profile.calibration``).
        year: The tax year whose configs to load.
        periods: The tax year's FULL pay-period list (may be empty).
        checkpoint: The measured checkpoint, or ``None`` (fully modeled).

    Returns:
        The summed modeled remainder as a :class:`WithholdingComponents`.
    """
    remainder_ids = {
        p.period_id for p in periods
        if checkpoint is None or p.start_date > checkpoint.as_of_date
    }
    if not remainder_ids:
        return _ZERO_COMPONENTS

    tax_configs = load_tax_configs_for_year(user_id, profile, year)
    breakdowns = paycheck_calculator.project_salary(
        profile, periods, tax_configs,
        calibration=profile.calibration,
    )
    remainder = [
        bd for bd in breakdowns if bd.period.period_id in remainder_ids
    ]
    return WithholdingComponents(
        gross=sum((bd.earnings.gross_biweekly for bd in remainder), ZERO),
        federal=sum((bd.taxes.federal for bd in remainder), ZERO),
        state=sum((bd.taxes.state for bd in remainder), ZERO),
        social_security=sum(
            (bd.taxes.social_security for bd in remainder), ZERO,
        ),
        medicare=sum((bd.taxes.medicare for bd in remainder), ZERO),
    )
