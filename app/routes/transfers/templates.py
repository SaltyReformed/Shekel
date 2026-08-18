"""
Shekel Budget App -- Transfer route package: template management.

CRUD for recurring transfer templates: list, create, edit, update, archive,
unarchive, and hard-delete, plus the update-acceptance gate and the
regenerate-and-commit step.  Every URL and endpoint name is preserved verbatim
from the pre-split ``app/routes/transfers.py``.

What happens to the ``budget.transfers`` ROWS a template stands for --
materializing them on create, and carrying an edit onto a non-repeating
template's single Transfer -- is the sibling module
:mod:`app.routes.transfers._instances`, split out at plan step R2e-3 when this
one reached the 1,000-line module cap.
"""

import logging
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.extensions import db
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.account import Account
from app.models.ref import Status
from app.utils import archive_helpers
from app.services import (
    account_service,
    category_service,
    loan_loaders,
    pay_period_service,
    template_amount_service,
    transfer_recurrence,
    transfer_service,
)
from app.utils.balance_predicates import is_projected_clause
from app.routes._commit_helpers import (
    STALE_ACTION_MESSAGE,
    STALE_EDITING_MESSAGE,
    StaleConflictContext,
    commit_or_handle_stale,
    handle_stale_conflict,
    handle_stale_form_conflict,
)
from app.routes._amount_version_actions import (
    AmountVersionAction,
    withdraw_amount_version,
)
from app.services.cash_ledger import resolve_transfer_amount
from app.routes._recurrence_conflict_chooser import (
    PreEditTemplateState,
    RecurrenceConflictKind,
    regenerate_or_conflict_chooser,
)
from app.routes._recurrence_form_helpers import (
    RecurrenceFormContext,
    build_recurrence_rule_for_create,
    resolve_recurrence_rule_for_update,
)
from app.routes._recurrence_form_render import recurrence_form_state
from app.routes._form_errors import load_form_or_redirect
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import RECURRENCE_END_BOUND_KEY
from app.routes._transfer_creation_helpers import (
    flush_template_or_namedup_redirect,
    generate_transfers_for_all_periods,
    settle_first_occurrence,
)
from app.routes.transfers._bp import transfers_bp
from app.routes.transfers._instances import (
    NON_REPEATING_ACCOUNTS_ARE_FIXED,
    materialize_initial_transfers,
    non_repeating_live_transfers,
    propagate_to_non_repeating_transfers,
)
from app.routes.transfers._helpers import (
    _create_schema,
    _update_schema,
    _user_owns,
)

logger = logging.getLogger(__name__)

# Field allowlist for the transfer-template update route: which submitted
# form fields may be written back to the template via setattr.
#
# ``default_amount`` is deliberately absent since plan step X-au-a: the amount
# is a dated SERIES as well as a column, and
# ``template_amount_service.set_amount`` is the one door that moves both
# together.  A setattr here would move one without the other.
_TEMPLATE_UPDATE_FIELDS = {
    "name", "from_account_id", "to_account_id",
    "category_id", "is_active", "sort_order",
}

# Where this kind's amount-history withdrawal reports back to; the act itself is
# shared with the transaction-template twin (plan step X-au-a).
_AMOUNT_VERSION_ACTION = AmountVersionAction(
    logger=logger,
    edit_endpoint="transfers.edit_transfer_template",
    noun="recurring transfer",
)




@transfers_bp.route("/transfers")
@login_required
@require_owner
def list_transfer_templates():
    """Redirect the retired /transfers list to the unified Recurring surface.

    Transfer templates are now listed and managed alongside recurring
    income and expenses on the unified ``/templates`` (Recurring) surface
    (Loop B).  This URL is kept as a redirect so old bookmarks -- and the
    transfer create/update routes' post-save redirects, which still target
    this endpoint -- land on the surface that replaced the standalone list.
    """
    return redirect(url_for("templates.list_templates"))


@transfers_bp.route("/transfers/new", methods=["GET"])
@login_required
@require_owner
def new_transfer_template():
    """Display the transfer template creation form."""
    accounts = account_service.list_active_accounts(current_user.id)
    categories = category_service.list_active_categories(current_user.id)
    periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)

    # Pre-fill account selection from query params (for quick-action links).
    prefill_from = request.args.get("from_account", type=int)
    prefill_to = request.args.get("to_account", type=int)

    return render_template(
        "transfers/form.html",
        template=None,
        accounts=accounts,
        categories=categories,
        # One value for every recurrence control (see the transaction-template
        # twin).  A CREATE form locks nothing on the SERVER -- there is no
        # template yet to ask ``owns_validity_window`` about -- but this form
        # offers every active account as a destination, so the definition it
        # is about to create may be a loan payment.  Which accounts those are
        # rides to the browser below and ``recurrence_form.js`` locks the
        # "Starts on" row when one is chosen; the derivation itself is the
        # route's (``settle_first_occurrence``), so the lock is an affordance
        # rather than the enforcement.
        recurrence=recurrence_form_state(None),
        loan_account_ids=loan_loaders.load_loan_account_ids_for_user(
            current_user.id,
        ),
        periods=periods,
        current_period=current_period,
        prefill_from=prefill_from,
        prefill_to=prefill_to,
        # A template that does not exist yet has no amount history; passed so
        # the shared form never references an undefined value.
        amount_history_rows=[],
        amount_today=None,
        amount_version_delete_endpoint="transfers.delete_amount_version",
    )


def _settle_create_references(data, start_period_id):
    """Refuse, or settle, everything the create payload REFERS to.

    One step rather than three consecutive guards, and the reason is the route
    rather than the count: each of these asks whether a submitted reference is
    the current user's to use, and the last one READS the destination it has
    just checked.  Splitting them across the route left three
    ``return redirect`` arms in a function whose own docstring records the
    single-return FK loop it already grew for the same reason -- and plan step
    R7c-b's fourth arm pushed it past pylint's ``too-many-return-statements``.
    Decomposing is this project's answer to that count, never a disable.

    Three things, in the one order they can be asked in:

    1. **Every user-scoped FK is the owner's** (commit C-27 / F-043).  A
       single-return loop so a future FK adds a row rather than an arm; the
       message-per-FK detail rides on the label.
    2. **The pay period a NON-REPEATING transfer lands in** is the owner's too.
       Owner-checked at the route since plan step R7b-4: the check used to live
       inside ``build_recurrence_rule_from_form``, because the same ``<select>``
       was ALSO the recurrence's "First paycheck" and a cross-user period would
       have shifted this owner's generation timing.  The recurrence takes a
       DATE now, so the field has one job and one owner.  Guarded on presence
       rather than folded into the loop, because it is OPTIONAL: a repeating
       transfer submits no period at all and ``_user_owns`` reads ``None`` as
       "no row".  Checked unconditionally when present, so a crafted POST
       pairing a foreign period with a repeating cadence is refused rather than
       ignored; ``_materialize_one_time_transfer`` re-checks as defence in
       depth.
    3. **A loan destination's first occurrence is DERIVED**, and it must be
       settled before the rule is built so nothing is authored that
       ``bind_rule_to_loan`` then replaces (plan step R7c-b, developer ruling
       2026-08-15).  It runs LAST because it reads the destination's loan
       parameters, which step 1 has just proved are the owner's -- reading them
       first would be an IDOR.

    Args:
        data: The validated payload, mutated in place by step 3.
        start_period_id: The submitted pay period, already popped from *data*
            by the caller, or ``None``.

    Returns:
        * ``None`` -- every reference checks out and *data* is ready to build.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    for model, pk, label in (
        (Account, data.get("from_account_id"), "source account"),
        (Account, data.get("to_account_id"), "destination account"),
        (Category, data.get("category_id"), "category"),
    ):
        if not _user_owns(model, pk):
            flash(f"Invalid {label}.", "danger")
            return redirect(url_for("transfers.new_transfer_template"))

    if start_period_id is not None and not _user_owns(PayPeriod, start_period_id):
        flash("Invalid start period.", "danger")
        return redirect(url_for("transfers.new_transfer_template"))

    return settle_first_occurrence(
        data, redirect=RedirectTarget("transfers.new_transfer_template"),
    )


@transfers_bp.route("/transfers", methods=["POST"])
@login_required
@require_owner
def create_transfer_template():
    """Create a new transfer template with optional recurrence rule.

    Route-boundary FK ownership checks (commit C-27 / F-043 of the
    2026-04-15 security remediation plan): every user-scoped FK
    accepted from the form -- ``from_account_id``, ``to_account_id``,
    ``category_id``, and the optional ``start_period_id`` -- is verified
    against ``current_user.id`` before the row is persisted, by
    :func:`_settle_create_references`.  That helper also settles a LOAN
    destination's derived first occurrence, which is why the three steps are
    one call: it reads the destination it has just proved is the owner's.  The
    follow-up branch (:func:`_materialize_one_time_transfer`) re-fetches the
    period and verifies ownership a second time, so a malicious
    ``start_period_id`` cannot leak into the transfer service.  The flash +
    redirect UX matches the existing template-form pattern; the security
    response rule (404 for both not-found and not-yours) is preserved
    indirectly by re-rendering the same form page rather than confirming
    whether the FK exists for someone else.
    """
    payload = load_form_or_redirect(
        _create_schema, RedirectTarget("transfers.new_transfer_template"),
    )
    if isinstance(payload, Response):
        return payload
    data = payload

    start_period_id = data.pop("start_period_id", None)
    refusal = _settle_create_references(data, start_period_id)
    if refusal is not None:
        return refusal

    # Create the recurrence rule via the F-24 preamble, or NO rule when the
    # form says "Does not repeat".  ``rule is None`` is the one-time transfer
    # since plan step R2e-3 -- the same shape a one-time transaction template
    # has always had -- and it is the create form's DEFAULT selection.
    #
    # This dereferenced ``rule.id`` unguarded until R2e-3, on a comment
    # claiming ``recurrence_pattern`` was ``required`` on
    # ``TransferTemplateCreateSchema``.  It is not: the field is
    # ``allow_none``, so any POST omitting or emptying it reached
    # ``AttributeError: 'NoneType' object has no attribute 'id'`` -- a 500
    # (defect **D13**), measured on both the absent and the empty spelling.
    rule = build_recurrence_rule_for_create(
        data,
        user_id=current_user.id,
        redirect=RedirectTarget("transfers.new_transfer_template"),
        include_due_day_of_month=False,
    )

    template = TransferTemplate(
        user_id=current_user.id,
        recurrence_rule_id=rule.id if rule is not None else None,
        **data,
    )
    db.session.add(template)

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget("transfers.list_transfer_templates"),
        name_dup_message="A transfer with that name already exists.",
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # Open the amount's dated series at today (plan step X-au-a).  The
    # constructor above also carries the figure because the column is NOT NULL;
    # this call is what makes the SERIES exist, and plan step X-au-e removes the
    # redundancy by removing the column.
    template_amount_service.set_amount(
        template, template.default_amount, effective_on=display_today(),
    )

    # Create the initial transfer instance(s) for the new template: a single
    # Transfer when it does not repeat, or a recurrence-engine fan-out when it
    # does.  Returns a redirect Response on a missing / invalid period or a
    # service rejection, which is propagated verbatim.
    materialize_redirect = materialize_initial_transfers(
        template, rule, start_period_id,
    )
    if materialize_redirect is not None:
        return materialize_redirect

    db.session.commit()
    flash(f"Transfer '{template.name}' created.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/edit", methods=["GET"])
@login_required
@require_owner
def edit_transfer_template(template_id):
    """Display the transfer template edit form."""
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    accounts = account_service.list_active_accounts(current_user.id)
    categories = category_service.list_active_categories(current_user.id)

    return render_template(
        "transfers/form.html",
        template=template,
        accounts=accounts,
        categories=categories,
        # The EDIT controls' starting state: see ``templates.edit_template``.
        # Since plan step R2e-3 this form offers the same empty "Does not
        # repeat" option the transaction form does, and it is FIRST -- so a
        # cadence left unselected would default to the DESTRUCTIVE clear, not
        # to a wrong cadence.  ``edit_form_cadence`` is what selects it.
        recurrence=recurrence_form_state(template),
        # A LOAN PAYMENT's stop is the loan's projected payoff, rewritten by
        # ``loan_recurrence_sync`` on every payoff-affecting edit -- so the
        # control renders disabled and states where the value comes from,
        # rather than accepting one the next loan edit discards.
        periods=[],
        current_period=None,
        # The amount's dated history (plan step X-au-a), precomputed into
        # display rows.  Empty for a DERIVE-mode loan payment, whose
        # ``default_amount`` is a P&I + escrow snapshot and which therefore has
        # no series at all.
        amount_history_rows=template_amount_service.build_amount_history(
            template, display_today(),
        ),
        # What the definition costs NOW; see the transaction twin.
        amount_today=template_amount_service.current_amount(
            template, display_today(),
        ),
        amount_version_delete_endpoint="transfers.delete_amount_version",
    )


# The transfer-template kind for the shared regenerate-or-chooser flow.
# Mutations route through transfer_recurrence (shadow-safe resolve).
_TRANSFER_TEMPLATE_KIND = RecurrenceConflictKind(
    model=Transfer,
    # A transfer's amount rule (plan step X-au-c2b): rule 5's own entry, which
    # needs no basis -- a parent transfer is priced by its own figure or by its
    # definition's series, never by a live producer.
    resolve_amount=resolve_transfer_amount,
    regenerate_fn=transfer_recurrence.regenerate_for_template,
    resolve_fn=transfer_recurrence.resolve_conflicts,
    update_endpoint="transfers.update_transfer_template",
)


@transfers_bp.route("/transfers/<int:template_id>", methods=["POST"])
@login_required
@require_owner
def update_transfer_template(template_id):
    """Update a transfer template and regenerate future transfers.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent transfer-template edit that races past
    the form-side check -- is caught and converted to the same
    flash + redirect.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    payload = load_form_or_redirect(
        _update_schema,
        RedirectTarget(
            "transfers.edit_transfer_template", {"template_id": template_id},
        ),
    )
    if isinstance(payload, Response):
        return payload
    data = payload

    # Stale-form check (commit C-18 / F-010).  Routed through the
    # F-26 helper so the pre-flush optimistic-locking guard shares a
    # single implementation with the parallel transaction-template
    # update route.
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != template.version_id:
        return handle_stale_form_conflict(
            StaleConflictContext(
                logger=logger,
                log_label="update_transfer_template",
                log_id=template_id,
                flash_message=STALE_EDITING_MESSAGE.format(
                    noun="recurring transfer",
                ),
                redirect=RedirectTarget(
                    "transfers.edit_transfer_template",
                    {"template_id": template_id},
                ),
            ),
            submitted=submitted_version,
            current=template.version_id,
        )

    effective_from = data.pop("effective_from", display_today())
    # Dropped, not read: an EDIT never re-materialises the one-time Transfer
    # this field places, so the only thing a submitted value could do here is
    # reach the field-update loop as a stray kwarg.  It is still on THIS
    # schema (plan step R7b-4 moved it there from the shared recurrence
    # mixin), so a submission can still carry it and the pop is still needed.
    data.pop("start_period_id", None)
    # The closing bound, composed by the schema's ``@post_load`` into ONE
    # value under the mode key.  ABSENT when the form stated no bound --
    # a disabled control, or a partial update -- which the helpers read as
    # "leave the stored one alone" (plan step R7b-3).
    end_bound = data.pop(RECURRENCE_END_BOUND_KEY, None)

    # The template's before-image, captured BEFORE anything overwrites it
    # (plan step R2e-1).  ``had_recurrence_rule`` is what lets the
    # regeneration below tell "the user just cleared the recurrence" -- which
    # must sweep the instances the deleted rule generated -- from "this
    # template never recurred", which must not: a RULE-LESS transfer
    # template's single Transfer is an ordinary auto-generated row, so a
    # rename would otherwise delete it -- which is exactly what a
    # ``Once``-ruled transfer suffered until plan step R2e-3 made it
    # rule-less (defect D16).
    before = PreEditTemplateState(
        amount=template.default_amount,
        had_recurrence_rule=template.recurrence_rule_id is not None,
    )

    # Re-point, rebuild, or clear the recurrence rule from the update payload
    # (F-24).  The helper dispatches the existing-rule (mutate in place)
    # vs no-existing-rule (build + link) branches and pops every
    # recurrence key from ``data``.  ``include_due_day_of_month=False``
    # because the transfer-template schemas do not expose the field.
    redirect_response = resolve_recurrence_rule_for_update(
        template,
        data,
        ctx=RecurrenceFormContext(
            end_bound=end_bound,
            redirect=RedirectTarget(
                "transfers.edit_transfer_template",
                {"template_id": template_id},
            ),
            include_due_day_of_month=False,
        ),
    )
    if redirect_response is not None:
        return redirect_response

    # Every reason this update may be refused, asked once and BEFORE the
    # field loop below writes anything: route-boundary FK ownership (commit
    # C-27 / F-043) and, for a template that does not repeat, an account
    # change its already-created Transfer could not follow (plan step R2e-3).
    refusal = _reject_transfer_template_update(template, data, before)
    if refusal is not None:
        flash(refusal, "danger")
        return redirect(url_for(
            "transfers.edit_transfer_template", template_id=template_id,
        ))

    for field, value in data.items():
        if field in _TEMPLATE_UPDATE_FIELDS:
            setattr(template, field, value)

    # State the amount through its one write door, which moves the scalar and
    # the dated series together (plan step X-au-a).  ``effective_from`` is the
    # form's "Amount effective from" date, which also bounds the regeneration
    # below -- ONE value, applied by two different predicates (the series reads
    # a row's DUE date, the sweep its pay PERIOD's end); finding **N-247** holds
    # that seam and X-au-e dissolves it.  Absent from a partial update means the
    # amount was not restated.
    if "default_amount" in data:
        template_amount_service.set_amount(
            template, data["default_amount"], effective_on=effective_from,
        )

    # Flush template changes first so name-uniqueness violations are caught
    # before regeneration dirties the session with transfer deletes/creates.
    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget(
            "transfers.edit_transfer_template",
            {"template_id": template_id},
        ),
    )
    if namedup_redirect is not None:
        return namedup_redirect

    return _regenerate_and_commit_template(
        template, before, effective_from, template_id,
    )


@transfers_bp.route(
    "/transfers/<int:template_id>/amount-versions/<int:version_id>/delete",
    methods=["POST"],
)
@login_required
@require_owner
def delete_amount_version(template_id, version_id):
    """Withdraw one entry from a transfer template's amount history.

    The correction path for a price stamped against the wrong DATE: restating
    the amount writes a version at the date it names and leaves the mis-dated
    one standing, so removing it is a separate act.  The EARLIEST entry is
    refused by the service -- it is what every date before the series answers
    from.

    Ownership is the ``get_or_404`` on the TEMPLATE; the act itself is shared
    with the transaction-template twin
    (:func:`app.routes._amount_version_actions.withdraw_amount_version`).
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)
    return withdraw_amount_version(template, version_id, _AMOUNT_VERSION_ACTION)


@transfers_bp.route("/transfers/<int:template_id>/archive", methods=["POST"])
@login_required
@require_owner
def archive_transfer_template(template_id):
    """Archive a transfer template (stops future generation, keeps history).

    Soft-deletes projected transfers and their shadow transactions via
    the transfer service to maintain the three-level cascade:
    template archival -> transfer soft-delete -> shadow soft-delete.

    Optimistic locking (commit C-18 / F-010): the template's
    ``version_id`` is enforced by SQLAlchemy on the
    ``is_active = False`` flush; a concurrent edit raises
    ``StaleDataError`` which the handler converts into a flash +
    redirect so the user retries against fresh state.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    template.is_active = False

    # Find projected, non-deleted transfers to soft-delete.  Routed
    # through the centralized ``is_projected_clause`` (D6-09 / MED-02)
    # parameterised on ``Transfer`` so the rule "what does a
    # Projected filter look like in SQL" is shared with the
    # Transaction filter sites.
    transfers_to_delete = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
            Transfer.is_deleted.is_(False),
        )
        .all()
    )

    # Route each through the service to ensure shadows are soft-deleted.
    for xfer in transfers_to_delete:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="archive_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect=RedirectTarget("transfers.list_transfer_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(
        f"Recurring transfer '{template.name}' archived. "
        f"{len(transfers_to_delete)} projected transfer(s) removed.",
        "info",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/unarchive", methods=["POST"])
@login_required
@require_owner
def unarchive_transfer_template(template_id):
    """Unarchive a transfer template.

    Restores soft-deleted transfers and their shadow transactions.

    Optimistic locking: see :func:`archive_transfer_template`.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    template.is_active = True

    # Find soft-deleted projected transfers to restore.  Routed
    # through ``is_projected_clause(Transfer)`` (D6-09 / MED-02);
    # see ``archive_transfer_template`` above.
    transfers_to_restore = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
            Transfer.is_deleted.is_(True),
        )
        .all()
    )

    # Restore transfers and shadows via the service so all mutations
    # flow through the single enforcement point (design doc section 4.1).
    for xfer in transfers_to_restore:
        transfer_service.restore_transfer(xfer.id, current_user.id)

    restored_count = len(transfers_to_restore)

    if template.recurrence_rule:
        generate_transfers_for_all_periods(template, effective_from=date.today())

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="unarchive_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect=RedirectTarget("transfers.list_transfer_templates"),
    ))
    if conflict is not None:
        return conflict
    flash(
        f"Recurring transfer '{template.name}' unarchived. "
        f"{restored_count} projected transfer(s) restored.",
        "success",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/hard-delete", methods=["POST"])
@login_required
@require_owner
def hard_delete_transfer_template(template_id):
    """Permanently delete a transfer template if it has no payment history.

    Maintains all five transfer invariants from CLAUDE.md:
      1. Two linked shadows per transfer -- CASCADE on Transaction.transfer_id
         removes both shadows when the parent Transfer is hard-deleted via
         transfer_service.delete_transfer(soft=False).
      2. No orphaned shadows -- shadows are removed atomically with their
         parent transfer through the service's CASCADE verification.
      3. Amount/status/period parity -- not applicable; entire records are
         removed, not mutated.
      4. All mutations through the transfer service -- every transfer
         deletion is routed through transfer_service.delete_transfer().
      5. Balance calculator queries only budget.transactions -- after
         deletion, shadow transactions no longer exist in the table.

    Two-path logic:
      - History exists (Paid/Settled transfers): permanent deletion is
        blocked.  Template is archived instead (if not already) and the
        user is warned.
      - No history: linked transfers are hard-deleted through the
        transfer service (which CASCADE-deletes shadows), then the
        template itself is permanently removed.

    Defense in depth (F-14): the bulk delete is constrained to non-
    settled transfers via the semantic ``Status.is_settled`` boolean,
    mirroring the ``templates.py::hard_delete_template`` shape added
    after CRIT-05.  Even if the guard predicate above regresses, is
    bypassed, or races a concurrent mark-done that lands between the
    guard check and the loop, settled transfers (Paid, Received,
    Settled) and their two-shadow pairs cannot be physically destroyed
    by this route.  Survivors retain their ``transfer_template_id``;
    the column's FK is ``ON DELETE SET NULL`` so they become detached
    settled history when the parent template is removed.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    if archive_helpers.transfer_template_has_paid_history(template.id):
        flash(
            f"'{template.name}' has payment history and cannot be permanently "
            "deleted. It has been archived instead.",
            "warning",
        )
        if template.is_active:
            template.is_active = False
            # Soft-delete projected transfers via the service (same as
            # archive_transfer_template) to maintain shadow invariants.
            # Routed through ``is_projected_clause(Transfer)``
            # (D6-09 / MED-02); see ``archive_transfer_template`` above.
            transfers_to_delete = (
                db.session.query(Transfer)
                .filter(
                    Transfer.transfer_template_id == template.id,
                    is_projected_clause(Transfer),
                    Transfer.is_deleted.is_(False),
                )
                .all()
            )
            for xfer in transfers_to_delete:
                transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)
            conflict = commit_or_handle_stale(StaleConflictContext(
                logger=logger,
                log_label="hard_delete_transfer_template archive-fallback",
                log_id=template_id,
                flash_message=STALE_ACTION_MESSAGE.format(
                    noun="recurring transfer",
                ),
                redirect=RedirectTarget("transfers.list_transfer_templates"),
            ))
            if conflict is not None:
                return conflict
        return redirect(url_for("transfers.list_transfer_templates"))

    # No history -- safe to permanently delete linked transfers through
    # the transfer service so that shadow transactions are CASCADE-
    # deleted (invariants 1, 2, 4).  ``transfer_service.delete_transfer``
    # flushes but does not commit, so all deletions are atomic within a
    # single DB transaction.
    #
    # Defense in depth (F-14 / commit C-21 mirror): the bulk delete is
    # additionally constrained to ``Status.is_settled = False`` rows via
    # the semantic ``Status.is_settled`` boolean -- the same shape
    # ``templates.py::hard_delete_template`` applies after CRIT-05.
    # Even if ``transfer_template_has_paid_history`` regresses, is
    # bypassed, or races a concurrent mark-done that lands between the
    # guard check and the loop below, settled transfers (Paid,
    # Received, Settled) and their two-shadow pairs cannot be
    # physically destroyed by this route.  Survivors retain their
    # ``transfer_template_id``; the column's FK is ``ON DELETE SET
    # NULL`` (see ``app/models/transfer.py``) so they become detached
    # settled history when the parent template is removed below.
    template_name = template.name
    settled_status_ids = db.session.query(Status.id).filter(
        Status.is_settled.is_(True)
    ).scalar_subquery()
    deletable_transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            Transfer.status_id.notin_(settled_status_ids),
        )
        .all()
    )
    for xfer in deletable_transfers:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)

    db.session.delete(template)
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="hard_delete_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect=RedirectTarget("transfers.list_transfer_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(f"Recurring transfer '{template_name}' permanently deleted.", "info")
    return redirect(url_for("transfers.list_transfer_templates"))










def _reject_transfer_template_update(template, data, before):
    """Return why this update must be refused, or ``None`` to proceed.

    Two rules, asked together so the route has ONE refusal branch rather than
    one per rule (which would push it past pylint's ``too-many-returns``):

    * every user-scoped FK in the payload is owned by this user
      (:func:`_first_unowned_template_fk`);
    * a template that neither has nor had a recurrence rule may not change
      its source or destination ACCOUNT while the Transfer it already created
      is still live.

    **Why the second rule exists.**  A non-repeating template does not
    regenerate -- that is what stops a rename from destroying its single
    Transfer (defect D16) -- so an edit reaches that Transfer only through
    :func:`propagate_to_non_repeating_transfers`, and the shadow-safe door
    it uses (``transfer_service.update_transfer``) accepts amount, name and
    category but NOT the two account columns: a shadow's ``account_id`` is
    derived from them when the pair is created, and moving it is a different
    operation from updating it.  Rather than let the template claim accounts
    its own Transfer does not use, the change is refused and the user is told
    what to do instead.  Scoped to a LIVE Transfer, because the rule is about
    that row: a template with none (one whose recurrence was cleared, say)
    has nothing to disagree with and is re-pointed freely.

    Args:
        template: The ``TransferTemplate`` being updated, still holding its
            PRE-edit field values -- the caller's ``setattr`` loop runs after
            this returns, which is what makes the comparison below meaningful.
        data: The loaded update payload.
        before: The template's pre-edit state
            (:class:`~app.routes._recurrence_conflict_chooser.PreEditTemplateState`).

    Returns:
        The refusal message to flash, or ``None`` when the update may proceed.
    """
    unowned = _first_unowned_template_fk(data)
    if unowned is not None:
        return f"Invalid {unowned}."

    if before.had_recurrence_rule or template.recurrence_rule is not None:
        return None
    moved = any(
        field in data
        and data[field] is not None
        and data[field] != getattr(template, field)
        for field in ("from_account_id", "to_account_id")
    )
    if moved and non_repeating_live_transfers(template):
        return NON_REPEATING_ACCOUNTS_ARE_FIXED
    return None




def _first_unowned_template_fk(data):
    """Return the label of the first submitted FK the user does not own, else None.

    Route-boundary FK ownership for the transfer-template update payload
    (commit C-27 / F-043).  Each user-scoped FK is verified only when present
    in the partial-update ``data`` (the loaded dict carries only keys the user
    submitted -- BaseSchema's EXCLUDE meta drops stray form fields).
    ``category_id`` accepts ``None`` per the schema; ``None`` clears the
    category and skips the probe.

    Args:
        data: The loaded TransferTemplateUpdateSchema output (partial update).

    Returns:
        The human-readable label ("source account", "destination account" or
        "category") of the first FK that is present, non-``None``, and not
        owned by ``current_user``; ``None`` when every present FK is owned.
    """
    for field, model, label in (
        ("from_account_id", Account, "source account"),
        ("to_account_id", Account, "destination account"),
        ("category_id", Category, "category"),
    ):
        if field not in data:
            continue
        value = data[field]
        if value is None:
            continue
        if not _user_owns(model, value):
            return label
    return None


def _regenerate_and_commit_template(
    template, before, effective_from, template_id,
):
    """Regenerate a transfer template's future transfers, then commit.

    Re-runs ``transfer_recurrence.regenerate_for_template`` against the
    baseline scenario, diverting to the recurrence-conflict chooser when an
    amount change would overwrite hand-edited upcoming transfers, then
    commits.  Optimistic-lock and name-uniqueness failures at flush time are
    converted to the same flash + redirect the form-side guards produce, so a
    concurrent edit never surfaces as a 500.

    Args:
        template: The TransferTemplate whose field changes are already staged
            in the session.
        before: The template's pre-edit state
            (:class:`~app.routes._recurrence_form_helpers.PreEditTemplateState`)
            -- its amount gates the chooser and its ``had_recurrence_rule``
            gates the sweep; see :func:`regenerate_or_conflict_chooser`.
        effective_from: Date from which regeneration applies.
        template_id: The template's id, used for redirect kwargs and logging.

    Returns:
        A ``Response`` -- the chooser, or the edit form on a stale-data or
        name-duplicate conflict, or a redirect to the template list on
        success.
    """
    # A template that neither has nor had a rule does not regenerate at all
    # (the gate below returns before touching a row -- that is what closes
    # defect D16), so its already-created Transfer is reached HERE or nowhere.
    if not before.had_recurrence_rule and template.recurrence_rule is None:
        refused = propagate_to_non_repeating_transfers(template)
        if refused is not None:
            return refused

    # Regenerate future transfers, diverting to the conflict chooser when an
    # amount change would overwrite hand-edited upcoming instances.
    diverted = regenerate_or_conflict_chooser(
        template, before, effective_from, _TRANSFER_TEMPLATE_KIND,
        amount_drives_instances=True,
    )
    # The chooser short-circuits (its pending edit is already rolled back).
    if diverted is not None:
        return diverted

    try:
        db.session.commit()
    except StaleDataError:
        return handle_stale_conflict(StaleConflictContext(
            logger=logger,
            log_label="update_transfer_template",
            log_id=template_id,
            flash_message=STALE_EDITING_MESSAGE.format(
                noun="recurring transfer",
            ),
            redirect=RedirectTarget(
                "transfers.edit_transfer_template",
                {"template_id": template_id},
            ),
        ))
    except IntegrityError:
        db.session.rollback()
        flash("A recurring transfer with that name already exists.", "warning")
        return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))
    # An edit that ended the recurrence deleted this template's upcoming
    # projected transfers (and their shadow pairs); "updated." alone would
    # report a destructive change as a routine one.
    if before.had_recurrence_rule and template.recurrence_rule_id is None:
        flash(
            f"'{template.name}' no longer repeats. Its upcoming projected "
            "transfers were removed; settled and hand-edited ones were kept.",
            "success",
        )
    else:
        flash(f"Recurring transfer '{template.name}' updated.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))
