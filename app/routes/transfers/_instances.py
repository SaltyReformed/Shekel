"""
Shekel Budget App -- Transfer route package: a template's Transfer INSTANCES

What happens to the ``budget.transfers`` rows a :class:`TransferTemplate`
stands for -- creating them when the template is created, and carrying an edit
onto them afterwards.  The sibling module :mod:`app.routes.transfers.templates`
owns the template itself: its CRUD routes, its form payload, and its recurrence
rule.

The seam is the one :mod:`app.routes._recurrence_form_helpers` and
:mod:`app.routes._recurrence_conflict_chooser` already draw one layer up --
"what the definition IS" versus "what happens to the rows it produced" -- and
it was cut here for the same reason: plan step R2e-3 pushed
``transfers/templates.py`` past the 1,000-line module cap.

**Both halves exist because a NON-REPEATING transfer is not a non-repeating
transaction.**  A transaction template with no recurrence rule generates
nothing and waits for the user to add rows by hand, so there is nothing to
create and nothing to propagate.  A transfer template with no rule still moves
money exactly once: this module materialises that single Transfer (with its two
shadow transactions, atomically) and then keeps it equal to the definition,
because the regeneration path that does that job for every recurring template
deliberately skips a rule-less one -- which is what stops a rename from
destroying it (defect D16).

Route-layer module (leading underscore = route-internal) rather than a service
because it consumes Flask ``flash`` / ``redirect`` / ``url_for`` and
``current_user``; ``CLAUDE.md::Architecture`` keeps services isolated from
Flask globals.
"""

from flask import flash, redirect, url_for
from flask_login import current_user

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import (
    NotFoundError,
    ValidationError as ShekelValidationError,
)
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transfer import Transfer
from app.services import loan_recurrence_sync, transfer_service
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.balance_predicates import is_projected_clause
from app.routes._transfer_creation_helpers import (
    generate_transfers_for_all_periods,
)


ONE_TIME_TRANSFER_NEEDS_PERIOD: str = (
    "A transfer that does not repeat has to land in one pay period. Pick the "
    "pay period it belongs to, or choose a pattern to repeat it."
)
"""Refusal shown when a non-repeating transfer names no pay period."""


NON_REPEATING_ACCOUNTS_ARE_FIXED: str = (
    "This transfer does not repeat, so the transfer it created already "
    "exists and cannot be moved between accounts. Delete it and create a "
    "new one, or choose a pattern to make it repeat."
)
"""Refusal shown when an account change cannot reach the Transfer it names."""


def _rollback_and_refuse(message):
    """Roll the pending create back, flash *message*, return to the form.

    The single refusal shape every branch of :func:`materialize_initial_transfers`
    shares.  The template and any rule are already FLUSHED by the time
    materialization runs, so a refusal has to roll back or it leaves a
    transfer definition behind that generated nothing.

    Args:
        message: The flash text explaining what was refused.

    Returns:
        A redirect ``Response`` to the create form.
    """
    db.session.rollback()
    flash(message, "danger")
    return redirect(url_for("transfers.new_transfer_template"))


def _materialize_one_time_transfer(template, start_period_id):
    """Create the single Transfer a NON-REPEATING transfer template stands for.

    A transfer that does not repeat still moves money exactly once, so unlike
    a rule-less transaction template -- which generates nothing and waits for
    the user to add rows by hand -- this materializes one Transfer in the
    chosen period, through ``transfer_service`` so its two shadow
    transactions are created atomically (Transfer Invariants 1, 2 and 4).

    Re-fetches ``start_period_id`` and re-verifies ownership so a tampered
    period id cannot leak into the transfer service -- defence in depth behind
    :func:`~app.routes._recurrence_form_helpers.build_recurrence_rule_from_form`'s
    probe, which plan step R2e-3 moved ahead of that helper's no-pattern early
    return so this path is owner-checked before anything is written at all.

    An ABSENT period is refused here rather than at the route, so the two
    "this transfer has nowhere to land" answers sit together.  Both refusals
    roll back: this runs after the template is flushed.

    **The due date is the CHOSEN period's start** -- the period named by
    *start_period_id*, which is not necessarily the one the transfer sits in
    later.  Before plan step R2e-3 this ran ``compute_due_date`` against the
    template's ``Once`` RULE, which returned exactly that: a form-authored
    ``Once`` rule carries no ``day_of_month`` (verified: all four live ``Once``
    rules have it NULL), and ``compute_due_date`` falls back to the period's
    start when there is none.  Verified against both live ``Once`` transfers on
    production -- id 154 due 2026-04-23 and id 409 due 2026-07-30, each equal
    to its CHOSEN period's start.  Transfer 409 has since been moved to an
    earlier period and its ``due_date`` no longer matches the period it sits
    in, which is why the property is stated against the chosen period and not
    the current one.

    It differed only when a day LEAKED in -- the day input is hidden for a
    non-repeating pattern but a hidden input still submits, so switching the
    picker away from Monthly left the typed day on the payload and silently
    re-dated the transfer.  With no rule there is nothing to read it from.

    Args:
        template: The persisted (flushed) TransferTemplate, with no rule.
        start_period_id: The submitted start-period id.

    Returns:
        A redirect ``Response`` on an invalid period or a service rejection
        (e.g. a loan as the source account) -- the caller returns it verbatim;
        ``None`` on success so the caller proceeds to commit.
    """
    if not start_period_id:
        return _rollback_and_refuse(ONE_TIME_TRANSFER_NEEDS_PERIOD)

    period = db.session.get(PayPeriod, start_period_id)
    if not period or period.user_id != current_user.id:
        return _rollback_and_refuse("Invalid pay period for one-time transfer.")

    scenario = get_baseline_scenario(current_user.id)
    if scenario is None:
        return None

    try:
        transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=current_user.id,
                from_account_id=template.from_account_id,
                to_account_id=template.to_account_id,
                pay_period_id=period.id,
                scenario_id=scenario.id,
                amount=template.default_amount,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=template.category_id,
                name=template.name,
                transfer_template_id=template.id,
                due_date=period.start_date,
            ),
        )
    except (NotFoundError, ShekelValidationError) as exc:
        return _rollback_and_refuse(f"Could not create transfer: {exc}")
    return None


def materialize_initial_transfers(template, rule, start_period_id):
    """Create the initial transfer instance(s) for a freshly built template.

    Two shapes, and ``rule`` is what tells them apart since plan step R2e-3:

    * ``rule is None`` -- the template does not repeat, so exactly one
      Transfer lands in the chosen period (:func:`_materialize_one_time_transfer`).
      This used to be the ``Once`` PATTERN, whose rule is what made a rename
      destroy the transfer: regeneration swept the row and the pattern's own
      suppression guard generated nothing back (defect **D16**, measured
      1 transfer + 2 shadows -> 0 + 0).  A rule-less template is skipped by
      that sweep's gate entirely, which is what closes it.
    * a rule -- hand the template to the recurrence engine to fan out across
      every period.

    Args:
        template: The persisted (flushed) TransferTemplate.
        rule: The template's RecurrenceRule, or ``None`` when it does not
            repeat.
        start_period_id: The submitted start-period id.  Required by the
            rule-less path, which refuses without it; ignored for a recurring
            rule, which fans out across the whole schedule.

    Returns:
        A redirect ``Response`` when either path hits an invalid period or the
        service rejects the transfer (e.g. a loan as the source account) -- the
        caller returns it verbatim; ``None`` on success so the caller proceeds
        to commit.
    """
    if rule is None:
        return _materialize_one_time_transfer(template, start_period_id)

    # Bound the rule to the destination loan's life BEFORE anything generates
    # (plan step C9a).  The transfer form offers every active account as a
    # destination, so a loan payment can be set up here as readily as on the
    # loan page -- but this path builds its rule from the FORM, so nothing has
    # ever given it the loan's ``start_date``.  Unbounded, it generated an
    # installment into every materialized pay period, including those preceding
    # origination: measured 3 pre-origination payments on a mortgage closing
    # 2026-04-15, each a phantom cash debit and an erased payment once settled.
    # A no-op for every non-loan destination, so no kind check is needed here.
    #
    # Below the rule-less return rather than above it, and that is a FIX: a
    # loan's recurring payment is found by ``recurrence_rule_id IS NOT NULL``
    # (``recurring_transfer_query``), so a ``Once`` transfer into a loan used
    # to be bound here and could then be returned as that loan's standing
    # payment.  A one-time transfer is not a cadence and no longer binds one.
    loan_recurrence_sync.bind_rule_to_loan(rule, template.to_account_id)

    # Recurring transfer: delegate to the recurrence engine.  Wrap in the
    # SAME guard the one-time branch uses: the recurrence engine fans out
    # through ``create_transfer``, which rejects a loan as the source account
    # (a transfer OUT of an amortizing loan), so an unhandled rejection here
    # would 500 on a clean, user-reachable action (the transfer form offers
    # every active account as a source).
    try:
        generate_transfers_for_all_periods(template)
    except (NotFoundError, ShekelValidationError) as exc:
        return _rollback_and_refuse(f"Could not create transfer: {exc}")
    return None


def non_repeating_live_transfers(template):
    """Return the Transfers a NON-REPEATING template's definition still owns.

    Projected, not hand-edited, not soft-deleted -- the same three conditions
    ``_recurrence_common.partition_regeneration_rows`` uses to decide which
    rows a recurring template's regeneration may rewrite.  A settled transfer
    is immutable history and an overridden one is a deliberate per-instance
    change; neither follows the definition, here or there.

    Args:
        template: The ``TransferTemplate``.

    Returns:
        The matching ``Transfer`` rows, newest last.  Normally exactly one --
        the row :func:`_materialize_one_time_transfer` created -- but a
        template whose recurrence was CLEARED keeps whatever survived that
        sweep, and this must be correct for both.
    """
    return (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
            Transfer.is_override.is_(False),
            Transfer.is_deleted.is_(False),
        )
        .order_by(Transfer.id)
        .all()
    )


def propagate_to_non_repeating_transfers(template):
    """Push a NON-REPEATING template's edited definition onto its Transfers.

    The counterpart of regeneration for the one shape that does not
    regenerate.  A recurring template's edit reaches its rows by deleting and
    re-creating them from the rule; a template with no rule has nothing to
    re-create from, so its already-materialised Transfer is updated IN PLACE
    instead -- through ``transfer_service.update_transfer``, the single door
    that keeps the two shadow transactions' amounts, statuses and periods
    equal to their parent's (Transfer Invariants 3 and 4).

    **Without this the template and its Transfer diverge silently.**  Measured
    before it existed, on the transfer create form's DEFAULT selection: a
    definition created at $500.00 and edited to $700.00 left the Recurring
    page reading $700.00 while the Transfer, both shadows, and therefore every
    balance the grid projects still read $500.00 -- under a plain "updated."
    flash.  The shape has existed since plan step R2e-1's clear branch; plan
    step R2e-3 is what made it reachable from the form, and so what owns it.

    Only amount, name and category propagate: those are the definition fields
    a Transfer carries, and they are exactly what the shadow-safe door
    accepts.  The two account columns cannot follow and are refused at the
    door instead (:func:`_reject_transfer_template_update`).

    Args:
        template: The updated ``TransferTemplate``, its new field values
            already applied and flushed.

    Returns:
        ``None`` on success; a redirect ``Response`` when the service refuses
        an update (rolled back, so nothing is half-applied).
    """
    for xfer in non_repeating_live_transfers(template):
        try:
            transfer_service.update_transfer(
                xfer.id, template.user_id,
                amount=template.default_amount,
                name=template.name,
                category_id=template.category_id,
            )
        except (NotFoundError, ShekelValidationError) as exc:
            db.session.rollback()
            flash(f"Could not update transfer: {exc}", "danger")
            return redirect(url_for(
                "transfers.edit_transfer_template", template_id=template.id,
            ))
    return None


__all__ = [
    "NON_REPEATING_ACCOUNTS_ARE_FIXED",
    "ONE_TIME_TRANSFER_NEEDS_PERIOD",
    "materialize_initial_transfers",
    "non_repeating_live_transfers",
    "propagate_to_non_repeating_transfers",
]
