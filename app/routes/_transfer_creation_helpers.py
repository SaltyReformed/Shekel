"""
Shekel Budget App -- Recurring-Transfer Creation Route Helpers

Shared building blocks for the routes that spin up a recurring
TransferTemplate and seed its Transfer instances:

* :func:`app.routes.investment.create_contribution_transfer` -- a
  biweekly contribution transfer into an investment / retirement
  account.
* :func:`app.routes.loan.payment_transfer.create_payment_transfer` -- a monthly P&I +
  escrow payment transfer into a debt account.
* :func:`app.routes.transfers.templates.create_transfer_template` /
  :func:`app.routes.transfers.templates.unarchive_transfer_template` -- the
  generic transfer-template create / restore paths.

Those routes were near-forks: the investment and loan creators ran a
byte-identical validate -> verify-source-account -> build-rule ->
build-template -> flush -> generate -> commit skeleton, diverging only
in the amount derivation, the recurrence pattern, the template name,
and the user-facing copy.  The four helpers here capture the shared
steps so each route keeps only its genuinely-distinct middle.

Route-layer module (leading underscore = route-internal) rather than a
service because every helper consumes Flask globals (``request``,
``flash``, ``redirect``, ``url_for``, ``current_user`` -- the redirect /
url_for pair via :class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask.  None of
these helpers create or mutate transfer shadow transactions directly --
shadow atomicity stays inside ``transfer_recurrence.generate_for_template``
and ``transfer_service`` -- so the transfer invariants are unaffected by
routing a call through this module.
"""
import logging
from datetime import date
from decimal import Decimal
from typing import Any

from flask import Response, abort, flash, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account import Account
from app.models.recurrence_rule import RecurrenceRule
from app.models.transfer_template import TransferTemplate
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import (
    RECURRENCE_END_BOUND_KEY,
    RECURRENCE_NEEDS_A_START,
    RECURRENCE_NOMINAL_DAY_KEY,
    RECURRENCE_STARTS_ON_KEY,
    end_bound_before_start_message,
)
from app.services import loan_loaders, loan_recurrence_sync, transfer_recurrence
from app.services.generation_schedule import GenerationSchedule
from app.services.scenario_resolver import require_baseline_scenario
from app.utils.auth_helpers import get_or_404

logger = logging.getLogger(__name__)


# Canonical name-collision flash for the partial-unique transfer-template
# name index.  Shared so the wording stays identical across every flush
# site (coding-standards DRY); the create-template path overrides it with
# its own non-"recurring" wording.
TRANSFER_NAME_DUP_MESSAGE: str = (
    "A recurring transfer with that name already exists."
)

# Shared validation-failure flash for the contribution / payment transfer
# forms.  Byte-identical between the investment and loan creators
# pre-extraction.
_TRANSFER_VALIDATION_FLASH: str = "Please correct the errors and try again."


def validate_and_resolve_source_account(
    schema: Any,
    *,
    dest_account_id: int,
    redirect: RedirectTarget,
) -> tuple[Account, dict[str, Any]] | Response:
    """Validate a transfer form and resolve + check its source account.

    Shared head of
    :func:`app.routes.investment.create_contribution_transfer` and
    :func:`app.routes.loan.payment_transfer.create_payment_transfer`.  Runs the four
    pre-conditions both routes enforce before building anything:

    1. The submitted form validates against ``schema``.
    2. The ``source_account_id`` it carries resolves to a row owned by
       the current user (``get_or_404`` -> ``abort(404)`` for both
       not-found and not-yours, per the security response rule).
    3. The source account is active.
    4. The source account is not the destination account.

    Args:
        schema: An instantiated Marshmallow schema exposing
            ``source_account_id`` and an optional ``amount`` (the
            investment / loan transfer schemas).  Validated and loaded
            against ``request.form``.
        dest_account_id: The destination account id from the route URL,
            compared against the submitted source to reject self-transfers.
        redirect: Where to redirect on any recoverable validation
            failure -- invalid form, inactive source, or self-transfer
            (each route's own dashboard).

    Returns:
        * ``(source_account, data)`` -- the owned, active source
          :class:`Account` and the loaded payload, when every check
          passes.
        * :class:`Response` -- a Flask redirect to ``redirect`` for a
          recoverable failure (invalid form, inactive source,
          self-transfer); the caller returns it directly.

    Raises:
        werkzeug.exceptions.NotFound: via ``abort(404)`` when the source
            account does not exist or is not owned by the current user.
    """
    errors = schema.validate(request.form)
    if errors:
        flash(_TRANSFER_VALIDATION_FLASH, "danger")
        return redirect.to_response()

    data = schema.load(request.form)
    source_account_id = data["source_account_id"]

    source_account = get_or_404(Account, source_account_id)
    if source_account is None:
        abort(404)

    if not source_account.is_active:
        flash("Source account is inactive.", "danger")
        return redirect.to_response()

    if source_account_id == dest_account_id:
        flash("Source and destination accounts must be different.", "danger")
        return redirect.to_response()

    return source_account, data


def build_recurring_transfer_template(
    *,
    source_account: Account,
    dest_account: Account,
    rule: RecurrenceRule,
    name: str,
    default_amount: Decimal,
) -> TransferTemplate:
    """Construct + session-add a recurring :class:`TransferTemplate`.

    Shared template-construction step of the investment and loan
    transfer creators.  Builds the row from the resolved accounts and a
    pre-flushed recurrence ``rule``, adds it to the session, and returns
    it; the caller flushes (via
    :func:`flush_template_or_namedup_redirect`) so name-collision
    handling stays at the route layer.

    Loan-payment settings are intentionally NOT set here: an investment
    contribution and every generic transfer get NO
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row, so every
    reader defaults them to non-derive (decision B).  The loan-payment creator
    -- the only caller that needs it -- attaches ``template.settings`` itself on
    the returned row before the flush, keeping that loan-only concern at the
    loan call site.

    Args:
        source_account: The owned, active funding account
            (``from_account``).
        dest_account: The investment / loan destination account
            (``to_account``).
        rule: The already-added-and-flushed :class:`RecurrenceRule`
            whose ``id`` links the template.
        name: Display name for the template.
        default_amount: Per-period transfer amount (Decimal).

    Returns:
        The added (not yet flushed) :class:`TransferTemplate`, with no
        loan-payment settings row (the caller attaches one only for a loan
        payment).
    """
    template = TransferTemplate(
        user_id=current_user.id,
        from_account_id=source_account.id,
        to_account_id=dest_account.id,
        recurrence_rule_id=rule.id,
        name=name,
        default_amount=default_amount,
    )
    db.session.add(template)
    return template


def settle_first_occurrence(
    data: dict[str, Any], *, redirect: RedirectTarget,
) -> Response | None:
    """Derive a loan payment's first occurrence, or refuse a missing one.

    **The CREATE-form half of the rule the EDIT form states as
    ``LOAN_PAYMENT_BOUND_IS_DERIVED``** (plan step R7c-b, developer ruling
    2026-08-15).  A recurring loan payment's first occurrence is the loan's
    first contractual installment; the app writes it, so the form's control is
    locked and posts nothing, and this is what fills the gap the lock leaves.

    **It DERIVES before the rule is built rather than after**, which is the
    whole of what that ruling changed.  ``bind_rule_to_loan`` still runs later
    and is now a no-op for this path; before this, the generic transfer form
    authored a rule from the date the USER typed and had it silently replaced a
    few lines on -- the shape
    :func:`~app.services.loan_recurrence_sync.loan_cadence_start`'s own
    docstring records as worse than duplication.

    **The refusal is here rather than in the schema, for the reason the update
    path's is in the route**: whether a start is required depends on the
    DESTINATION -- a loan derives one, anything else must state one -- and a
    schema never learns which accounts are loans.  So
    ``TransferTemplateCreateSchema`` carries
    ``recurrence_start_is_required = False`` and this states the rule with the
    schema's own message, exactly as
    ``_recurrence_form_helpers.resolve_recurrence_rule_for_update`` does.

    A submission naming NO cadence authors no rule and is left alone: "does
    not repeat" needs no first occurrence.

    Args:
        data: The validated payload, mutated in place.  Its ``to_account_id``
            must already be ownership-checked -- this reads the destination's
            loan parameters, so an unchecked id would be an IDOR.
        redirect: Where to send the user when the submission is refused.

    Returns:
        * ``None`` -- ``data`` now carries a first occurrence, or names no
          cadence at all; the caller continues.
        * :class:`Response` -- the refusal redirect, returned verbatim -- an
          unstated first occurrence for a non-loan destination, or a closing
          bound below the one just derived for a loan
          (:func:`_refuse_bound_before_derived_start`).
    """
    if data.get("recurrence_unit") is None:
        return None
    params = loan_loaders.load_loan_params(data.get("to_account_id"))
    if params is not None:
        cadence_start = loan_recurrence_sync.loan_cadence_start(
            data["recurrence_unit"], params,
        )
        data[RECURRENCE_STARTS_ON_KEY] = cadence_start.starts_on
        data[RECURRENCE_NOMINAL_DAY_KEY] = cadence_start.nominal_day
        return _refuse_bound_before_derived_start(
            data, cadence_start.starts_on, redirect=redirect,
        )
    if data.get(RECURRENCE_STARTS_ON_KEY) is not None:
        return None
    for message in RECURRENCE_NEEDS_A_START[RECURRENCE_STARTS_ON_KEY]:
        flash(message, "danger")
    return redirect.to_response()


def _refuse_bound_before_derived_start(
    data: dict[str, Any], starts_on: date, *, redirect: RedirectTarget,
) -> Response | None:
    """Refuse a stated closing bound that precedes the DERIVED first occurrence.

    **The comparison the schema could not make**, and leaving it out was an
    unhandled 500.  ``RecurrenceFormFieldsMixin.build_end_bound`` runs
    ``require_end_bound_after_start`` at load time, which early-returns when
    ``starts_on`` is absent -- and absent is exactly what the loan branch above
    produces, because the form's "Starts on" control is locked and posts
    nothing.  So any past "Ends on" passed every validator and the pair reached
    the write door with the derived start beside it, generating a rule that
    names no occurrence at all.  The create form does not lock the "Ends"
    control (the server cannot know the destination at render), so the form
    invites exactly this.

    Worded through
    :func:`~app.schemas.validation.end_bound_before_start_message`, the same
    sentence both other doors use, so a user who states an impossible window
    reads one refusal wherever they state it.

    Args:
        data: The validated payload.  Read for the composed closing bound,
            which the create preamble pops later; not mutated.
        starts_on: The first occurrence just derived from the loan's contract.
        redirect: Where to send the user when the submission is refused.

    Returns:
        * ``None`` -- the submission states no bound, or one at or after the
          derived start.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    bound = data.get(RECURRENCE_END_BOUND_KEY)
    if bound is None:
        return None
    end_date = bound.columns().end_date
    if end_date is None or end_date >= starts_on:
        return None
    flash(end_bound_before_start_message(end_date, starts_on), "danger")
    return redirect.to_response()


def flush_template_or_namedup_redirect(
    *,
    redirect: RedirectTarget,
    name_dup_message: str = TRANSFER_NAME_DUP_MESSAGE,
) -> Response | None:
    """Flush the session, translating a name-collision into flash+redirect.

    Wraps the
    ``try: db.session.flush() except IntegrityError`` idiom the transfer
    creators / updaters share, where the partial-unique index on the
    template name surfaces a concurrent or duplicate name as an
    :class:`IntegrityError`.  Rolls back and converts it into the
    canonical "name already exists" flash + redirect rather than a 500.

    Args:
        redirect: Where to redirect on collision.
        name_dup_message: Flash text for the collision; defaults to
            :data:`TRANSFER_NAME_DUP_MESSAGE`.  The generic create-
            template path passes its own non-"recurring" wording.

    Returns:
        * ``None`` -- the flush succeeded; the caller continues.
        * :class:`Response` -- the collision redirect; the caller
          returns it directly.
    """
    try:
        db.session.flush()
        return None
    except IntegrityError:
        db.session.rollback()
        flash(name_dup_message, "warning")
        return redirect.to_response()


def generate_transfers_for_all_periods(
    template: TransferTemplate,
    *,
    effective_from=None,
) -> None:
    """Seed a template's Transfer instances across the user's pay periods.

    The shared ``resolve baseline scenario -> load the owner's schedule ->
    transfer_recurrence.generate_for_template`` idiom used by the
    investment / loan / transfers create paths (and the unarchive
    restore path).  Shadow-transaction atomicity is owned by
    ``generate_for_template``; this helper only orchestrates its inputs.

    **It REQUIRES the baseline scenario (ruling R-BW), and the silent no-op it
    replaces was ledger row F-9.**  Every caller is a CREATE that reports
    success to the user afterwards, so "generate nothing and return normally"
    told them a recurring transfer existed that did not -- the same outcome the
    adjacent missing-period branch is written to refuse
    (``transfers/_instances.ONE_TIME_TRANSFER_NEEDS_PERIOD``).  The raise is
    answered by the one application-level handler, which rolls the pending
    create back and renders the repair.

    Args:
        template: The flushed :class:`TransferTemplate` whose recurrence
            rule drives generation.
        effective_from: Optional lower bound passed through to
            ``generate_for_template``; ``None`` (the default) generates
            across every period, matching the create paths, while the
            unarchive path passes ``date.today()`` to fill only forward.

    Raises:
        BaselineMissingError: When the owner has no baseline scenario, so
            there is nothing to generate INTO.  Unreachable through any door
            today -- registration writes one and nothing deletes one -- which
            is why this changes no live behaviour.
    """
    scenario = require_baseline_scenario(current_user.id)
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_user(current_user.id),
        scenario.id,
        effective_from=effective_from,
    )


__all__ = [
    "settle_first_occurrence",
    "TRANSFER_NAME_DUP_MESSAGE",
    "validate_and_resolve_source_account",
    "build_recurring_transfer_template",
    "flush_template_or_namedup_redirect",
    "generate_transfers_for_all_periods",
]
