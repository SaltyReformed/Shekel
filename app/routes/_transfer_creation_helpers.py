"""
Shekel Budget App -- Recurring-Transfer Creation Route Helpers

Shared building blocks for the routes that spin up a recurring
TransferTemplate and seed its Transfer instances:

* :func:`app.routes.investment.create_contribution_transfer` -- a
  biweekly contribution transfer into an investment / retirement
  account.
* :func:`app.routes.loan.create_payment_transfer` -- a monthly P&I +
  escrow payment transfer into a debt account.
* :func:`app.routes.transfers.create_transfer_template` /
  :func:`app.routes.transfers.unarchive_transfer_template` -- the
  generic transfer-template create / restore paths.

Those routes were near-forks: the investment and loan creators ran a
byte-identical validate -> verify-source-account -> build-rule ->
build-template -> flush -> generate -> commit skeleton, diverging only
in the amount derivation, the recurrence pattern, the template name,
and the user-facing copy.  The four helpers here capture the shared
steps so each route keeps only its genuinely-distinct middle.

Route-layer module (leading underscore = route-internal) rather than a
service because every helper consumes Flask globals (``request``,
``flash``, ``redirect``, ``url_for``, ``current_user``);
``CLAUDE.md::Architecture`` keeps services isolated from Flask.  None of
these helpers create or mutate transfer shadow transactions directly --
shadow atomicity stays inside ``transfer_recurrence.generate_for_template``
and ``transfer_service`` -- so the transfer invariants are unaffected by
routing a call through this module.
"""
import logging
from decimal import Decimal
from typing import Any

from flask import Response, abort, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account import Account
from app.models.recurrence_rule import RecurrenceRule
from app.models.transfer_template import TransferTemplate
from app.services import pay_period_service, transfer_recurrence
from app.services.scenario_resolver import get_baseline_scenario
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
    redirect_endpoint: str,
    redirect_kwargs: dict[str, Any] | None = None,
) -> tuple[Account, dict[str, Any]] | Response:
    """Validate a transfer form and resolve + check its source account.

    Shared head of
    :func:`app.routes.investment.create_contribution_transfer` and
    :func:`app.routes.loan.create_payment_transfer`.  Runs the four
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
        redirect_endpoint: Flask endpoint to redirect to on any
            validation failure (each route's own dashboard).
        redirect_kwargs: ``url_for`` kwargs for that endpoint (e.g.
            ``{"account_id": account_id}``).

    Returns:
        * ``(source_account, data)`` -- the owned, active source
          :class:`Account` and the loaded payload, when every check
          passes.
        * :class:`Response` -- a Flask redirect to ``redirect_endpoint``
          for a recoverable failure (invalid form, inactive source,
          self-transfer); the caller returns it directly.

    Raises:
        werkzeug.exceptions.NotFound: via ``abort(404)`` when the source
            account does not exist or is not owned by the current user.
    """
    redirect_kwargs = redirect_kwargs or {}
    errors = schema.validate(request.form)
    if errors:
        flash(_TRANSFER_VALIDATION_FLASH, "danger")
        return redirect(url_for(redirect_endpoint, **redirect_kwargs))

    data = schema.load(request.form)
    source_account_id = data["source_account_id"]

    source_account = get_or_404(Account, source_account_id)
    if source_account is None:
        abort(404)

    if not source_account.is_active:
        flash("Source account is inactive.", "danger")
        return redirect(url_for(redirect_endpoint, **redirect_kwargs))

    if source_account_id == dest_account_id:
        flash("Source and destination accounts must be different.", "danger")
        return redirect(url_for(redirect_endpoint, **redirect_kwargs))

    return source_account, data


def build_recurring_transfer_template(
    *,
    source_account: Account,
    dest_account: Account,
    rule: RecurrenceRule,
    name: str,
    default_amount: Decimal,
    derive_from_loan: bool = False,
) -> TransferTemplate:
    """Construct + session-add a recurring :class:`TransferTemplate`.

    Shared template-construction step of the investment and loan
    transfer creators.  Builds the row from the resolved accounts and a
    pre-flushed recurrence ``rule``, adds it to the session, and returns
    it; the caller flushes (via
    :func:`flush_template_or_namedup_redirect`) so name-collision
    handling stays at the route layer.

    Args:
        source_account: The owned, active funding account
            (``from_account``).
        dest_account: The investment / loan destination account
            (``to_account``).
        rule: The already-added-and-flushed :class:`RecurrenceRule`
            whose ``id`` links the template.
        name: Display name for the template.
        default_amount: Per-period transfer amount (Decimal).
        derive_from_loan: ``True`` only for loan-payment transfers,
            which re-derive the live cash amount from the destination
            loan on every render; ``False`` (the model default) for
            investment contributions and every other template.

    Returns:
        The added (not yet flushed) :class:`TransferTemplate`.
    """
    template = TransferTemplate(
        user_id=current_user.id,
        from_account_id=source_account.id,
        to_account_id=dest_account.id,
        recurrence_rule_id=rule.id,
        name=name,
        default_amount=default_amount,
        derive_from_loan=derive_from_loan,
    )
    db.session.add(template)
    return template


def flush_template_or_namedup_redirect(
    *,
    redirect_endpoint: str,
    redirect_kwargs: dict[str, Any] | None = None,
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
        redirect_endpoint: Flask endpoint to redirect to on collision.
        redirect_kwargs: ``url_for`` kwargs for that endpoint.
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
        return redirect(url_for(
            redirect_endpoint, **(redirect_kwargs or {}),
        ))


def generate_transfers_for_all_periods(
    template: TransferTemplate,
    *,
    effective_from=None,
) -> None:
    """Seed a template's Transfer instances across the user's pay periods.

    The shared ``resolve baseline scenario -> load all periods ->
    transfer_recurrence.generate_for_template`` idiom used by the
    investment / loan / transfers create paths (and the unarchive
    restore path).  A no-op when the user has no baseline scenario yet,
    matching the pre-extraction guard.  Shadow-transaction atomicity is
    owned by ``generate_for_template``; this helper only orchestrates
    its inputs.

    Args:
        template: The flushed :class:`TransferTemplate` whose recurrence
            rule drives generation.
        effective_from: Optional lower bound passed through to
            ``generate_for_template``; ``None`` (the default) generates
            across every period, matching the create paths, while the
            unarchive path passes ``date.today()`` to fill only forward.
    """
    scenario = get_baseline_scenario(current_user.id)
    if scenario:
        periods = pay_period_service.get_all_periods(current_user.id)
        transfer_recurrence.generate_for_template(
            template, periods, scenario.id, effective_from=effective_from,
        )


__all__ = [
    "TRANSFER_NAME_DUP_MESSAGE",
    "validate_and_resolve_source_account",
    "build_recurring_transfer_template",
    "flush_template_or_namedup_redirect",
    "generate_transfers_for_all_periods",
]
