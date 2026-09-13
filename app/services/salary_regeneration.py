"""
Shekel Budget App -- What a change to a salary profile does to the rows it prices.

One act: :func:`regenerate_salary_transactions`.  A salary profile is the
producer behind every paycheck row its template generated, so any write to the
profile or its line items -- a raise added, edited, ended or removed, a
deduction changed, the profile re-priced -- is followed by the SAME two moves:
the template's per-paycheck amount is re-stated at today's net, and the
projected paycheck rows from today are regenerated under the new terms.

**What those two moves keep in step, stated exactly** (an adversarial review of
plan step salary:S3-f-3 corrected a draft that claimed more).  A projected
paycheck row stores NO figure since plan step balance:X-au-d: the engine writes
a declaration and the cash ledger's rule 2 prices the row at read time from
the profile's own rows, so a raise write with no regeneration at all moves
every projected paycheck the moment it commits.  What the regeneration alone
keeps current is the template's ``default_amount`` -- vestigial for an ACTIVE
salary-linked template, but the figure ``routes/salary/profiles.delete_profile``
opens the template's price series at when the profile is archived, and the
GENERATE base the carry-forward resolver reads -- and the ROW SET, which a
cadence or window change redraws.  Two doors that write a raise leave the same
thing behind, or they are two doors with two outcomes (CLAUDE.md rule 14).

**It lived in the salary ROUTES package until plan step salary:S3-f-3**
(``routes/salary/_helpers._regenerate_salary_transactions``; ruling
**R-SAL24**), and moving it here is a correction rather than a size fix.  It
prices money and writes money rows, which is the reason
:mod:`app.services.salary_profile_service` gives for being a service and not
two lines in a route -- and S3-f-3 gave the ``/retirement`` assumptions rail a
second door that writes a raise's end year.  A door on another blueprint could
not reach a private module of the salary package (the
``shekel-private-module-import`` gate is exactly that fence), so the walk
every raise write is followed by either moved below both routes or was
spelled twice.  Rule 14 says once.

**Flask-free, which is what the move changed.**  The route-tier original built
its own read pass from ``current_user`` and FLASHED the rows the regeneration
declined to touch.  This takes the pass the route already opened
(a producer below the route takes the :class:`~app.services.balance_at
.BalanceContext` and drops ``user_id``; only a route builds one) and RETURNS
those row ids, so each route reports them in its own voice --
``flash_retained_notice`` for both today.  Nothing else moved: the paycheck is
priced through the same direct engine call (one of the sites the arch census
``tests/test_arch/test_the_calendar_wide_projection_has_one_spelling.py``
enumerates for plan step C12, which now names this file), the amount goes
through the same write door, and the regeneration reads the same day.  The
one reordering: the adapter builds the pass BEFORE the template guard below
runs, where the route-tier original guarded first -- one scenario query for a
profile without a template, a state ``create_profile`` never produces.

Boundary discipline (``CLAUDE.md`` Architecture): ORM rows and a pass in, plain
ids out.  Flushes through the engine and does not commit -- the caller owns the
unit of work, because the regeneration is one optimistic-locked transaction
with the write that caused it.
"""

import logging
from datetime import date

from sqlalchemy.exc import SQLAlchemyError

from app.exceptions import RecurrenceConflict
from app.models.salary_profile import SalaryProfile
from app.services import (
    paycheck_calculator,
    recurrence_engine,
    template_amount_service,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.payroll_basis import PayrollBasis
from app.services.tax_config_service import load_tax_configs_for_year
from app.utils.dates import display_today

logger = logging.getLogger(__name__)


def regenerate_salary_transactions(
    ctx: BalanceContext, profile: SalaryProfile,
) -> list[int]:
    """Re-state the template's amount and regenerate the profile's paycheck rows.

    The one walk every salary write is followed by (module docstring).  The
    pass pins the owner, the day and the baseline scenario, and its calendar
    serves both the paycheck recompute and the regeneration's own resolution
    (plan steps R7d-c-1 and R4b-1): the paycheck engine and the recurrence
    seam both read the CALENDAR (pay-calendar plan steps C2-f2d-3 and
    C2-f3c), so there is one value and no second read to reconcile it
    against.

    A profile with no template, or an owner with no baseline scenario, has no
    rows to regenerate and is left alone.

    Args:
        ctx: The request's read pass, built by the route.
        profile: The :class:`~app.models.salary_profile.SalaryProfile` whose
            template prices the rows.

    Returns:
        The ids of the projected rows the pass LEFT ALONE because the owner
        has purchases or notes recorded against them (plan step R10-a) --
        empty when every row took the change.  The caller tells the owner;
        a retained row is a silent no-op unless it is said out loud.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: Re-raised from the regeneration's
            flush after the profile id is logged (C-46 / F-145), so the
            route's own handler still reports it.
    """
    if not profile.template:
        return []
    if ctx.scenario is None:
        return []

    schedule = GenerationSchedule.for_pass(ctx)
    calendar = ctx.calendar()

    # Update the template's default_amount to the current net pay
    current_period = calendar.period_containing(date.today())
    if current_period:
        # The configs are resolved for the PERIOD's own tax year, not the
        # clock's: a period straddling New Year belongs to the year it starts
        # in, which is the key ``configs_by_year`` uses for every
        # other paycheck this profile computes.
        tax_configs = load_tax_configs_for_year(
            ctx.user_id, profile, current_period.start_date.year,
        )
        pay_breakdown = paycheck_calculator.calculate_paycheck(
            PayrollBasis(profile, calendar), current_period, tax_configs,
            calibration=profile.calibration,
        )
        # Through the amount's one write door (plan step X-au-a).  The profile
        # is salary-linked and active, so the door moves the column and records
        # NO version: a paycheck-calculated figure is derived, not a price
        # anybody stated.
        template_amount_service.set_amount(
            profile.template, pay_breakdown.earnings.net_pay,
            effective_on=display_today(),
        )

    # Regenerate transactions
    try:
        recurrence_engine.regenerate_for_template(
            profile.template, schedule, ctx.scenario_id,
            effective_from=date.today(),
        )
    except RecurrenceConflict as e:
        logger.warning("Recurrence conflict during salary regeneration: %s", e)
        # The override / soft-delete halves of this conflict are deliberately
        # swallowed here -- a salary regeneration preserves them and there is
        # nothing for the operator to decide -- but a retained row means this
        # pass declined to apply the profile's change to a row carrying their
        # own records, which the CALLER must say out loud (plan step R10-a,
        # adversarial review): it is returned, never dropped.
        return list(e.retained)
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): logging hook that re-raises.
        # SQLAlchemy errors from the regenerate flush get the
        # profile-id context here as well as in the calling route's
        # ``except SQLAlchemyError`` block.  Non-SQLAlchemy
        # exceptions still propagate to the caller without this
        # extra log line; the caller's logger.exception then
        # records the user-id + profile-id context.
        logger.exception("Failed to regenerate salary transactions for profile %d", profile.id)
        raise
    return []


__all__ = ["regenerate_salary_transactions"]
