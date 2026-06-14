"""
Shekel Budget App -- Pay-Period Template Population

Fills a set of pay periods with each active template's recurring rows --
transactions AND transfers -- in one pass.  This is the orchestrator the
extend and regenerate operations run after creating new, empty periods.

It lives in its own module rather than inside either recurrence engine
because it must call BOTH: the transaction engine
(``recurrence_engine.generate_for_template``) and the transfer engine
(``transfer_recurrence.generate_for_template``).  The transfer engine
already imports the transaction engine, so co-locating this orchestrator
with either one would create an import cycle; a neutral module that
imports both (and is imported by neither) keeps the graph acyclic.

Flask-isolated: takes and returns plain data, flushes via the engines,
never commits.
"""

from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import transfer_recurrence
from app.services.recurrence_engine import generate_for_template
from app.services.scenario_resolver import get_baseline_scenario


def populate_periods_from_active_templates(user_id, periods, effective_from=None):
    """Generate recurring transactions AND transfers into a set of periods.

    The repopulation step extend and regenerate run after creating new,
    empty periods.  ``generate_pay_periods`` creates blank periods and
    does NOT call the recurrence engine, so a freshly-appended period has
    none of its rent / paychecks / recurring transfers until this runs.
    This re-runs BOTH engines -- transactions and transfers, so a new
    period never silently misses a recurring transfer -- over the
    specific ``periods``, into the user's baseline scenario (multi-scenario
    repopulation is reserved for later).

    Both engines' shared ``should_skip_period`` skips any period that
    already holds a template-linked row, so this is safe to re-run: a
    retried extend / top-up creates nothing and cannot violate the
    ``(template, period, scenario)`` unique partial index.

    Args:
        user_id: The owning user's id.
        periods: The PayPeriod objects to populate (ordered by index).
            An empty list is a no-op.
        effective_from: Boundary date forwarded to each engine; defaults
            to the first period's ``start_date`` so generation is scoped
            to exactly these periods regardless of a rule's own start
            period.

    Returns:
        The number of template-linked records created (transactions plus
        transfers; a transfer counts once, not its two shadow rows).
    """
    if not periods:
        return 0

    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return 0

    boundary = (
        effective_from if effective_from is not None else periods[0].start_date
    )

    created = 0
    txn_templates = (
        db.session.query(TransactionTemplate)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    for template in txn_templates:
        created += len(generate_for_template(
            template, periods, scenario.id, effective_from=boundary,
        ))

    transfer_templates = (
        db.session.query(TransferTemplate)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    for template in transfer_templates:
        created += len(transfer_recurrence.generate_for_template(
            template, periods, scenario.id, effective_from=boundary,
        ))

    return created
