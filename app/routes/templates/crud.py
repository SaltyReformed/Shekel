"""
Shekel Budget App -- Recurring route package: recurring-transaction CRUD.

Create, edit, update, archive, unarchive and hard-delete a recurring
TRANSACTION definition (a :class:`~app.models.transaction_template.TransactionTemplate`)
and the recurrence rule it carries, plus the kind-agnostic recurrence-preview
fragment endpoint both template forms point at.  Updating a template triggers
recurrence regeneration.

The unified Recurring LIST page is the sibling module
:mod:`app.routes.templates.surface`: it spans both template kinds, so it is not
part of this one.
"""

import logging
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.category import Category
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.ref import Status, TransactionType
from app import ref_cache
from app.enums import TxnTypeEnum
from app.utils import archive_helpers
from app.schemas.validation import TemplateCreateSchema, TemplateUpdateSchema
from app.services import (
    account_service,
    category_service,
    pay_period_service,
    recurrence_engine,
    template_amount_service,
)
from app.services.generation_schedule import GenerationSchedule
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.recurrence import pattern_choices
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.balance_predicates import is_projected_clause
from app.routes._commit_helpers import (
    StaleConflictContext,
    commit_or_handle_stale,
)
from app.routes._amount_version_actions import (
    AmountVersionAction,
    withdraw_amount_version,
)
from app.routes._recurrence_preview import recurrence_preview_fragment
from app.routes._recurrence_conflict_chooser import (
    PreEditTemplateState,
    RecurrenceConflictKind,
    regenerate_or_conflict_chooser,
)
from app.routes._recurrence_form_helpers import (
    STALE_ACTION_MESSAGE,
    STALE_EDITING_MESSAGE,
    RecurrenceFormContext,
    build_recurrence_rule_from_form,
    edit_form_pattern_choices,
    handle_stale_form_conflict,
    resolve_recurrence_rule_for_update,
)
from app.routes._form_errors import validate_form_or_redirect
from app.routes._redirect_target import RedirectTarget
from app.routes.templates._bp import templates_bp

logger = logging.getLogger(__name__)


# Field allowlist for the template update route: which submitted form
# fields may be written back to the template via setattr.
#
# Scoped to exactly the keys ``TemplateUpdateSchema`` can deserialize.
# ``is_active`` and ``sort_order`` are deliberately absent: neither is a
# field on the Template schema chain, so ``_update_schema.load`` (with
# ``unknown = EXCLUDE``) can never surface them here.  ``is_active`` is
# owned by the dedicated archive / unarchive routes, which pair the flag
# flip with the projected-transaction soft-delete this route does not
# perform -- allowlisting it here would invite a future schema field to
# silently archive a template without that cleanup.
#
# ``default_amount`` is absent for the same shape of reason since plan step
# X-au-a: the amount is no longer a bare column but a dated SERIES, and
# ``template_amount_service.set_amount`` is the one door that moves the scalar
# and the series together.  A setattr here would move one without the other.
_TEMPLATE_UPDATE_FIELDS = {
    "name", "category_id", "transaction_type_id",
    "account_id", "is_envelope", "companion_visible",
}

_create_schema = TemplateCreateSchema()
_update_schema = TemplateUpdateSchema()

# Where this kind's amount-history withdrawal reports back to; the act itself is
# shared with the transfer-template twin (plan step X-au-a).
_AMOUNT_VERSION_ACTION = AmountVersionAction(
    logger=logger,
    edit_endpoint="templates.edit_template",
    noun="recurring transaction",
)

# Query-param hint the Recurring surface's "New" picker passes so the
# creation form pre-selects the right transaction type.  "income" selects
# the income type; any other value (including the default Expense picker
# entry and a hand-crafted request) falls back to expense, the most common
# recurring definition.
_NEW_TYPE_INCOME = "income"

def _is_tracking_on_non_expense(data, template=None):
    """Check whether tracking is being set on a non-expense template.

    Defense-in-depth fallback for the cross-field schema validator
    ``validate_envelope_only_on_expense``.  The schema validator catches
    the bug whenever both ``is_envelope`` and ``transaction_type_id``
    appear in the deserialized payload (the normal HTML form path); this
    helper closes the gap on partial updates that omit one field by
    falling back to the existing template's stored value.

    Args:
        data: Deserialized form data from Marshmallow schema.
        template: Existing TransactionTemplate (for updates) or None (for creates).

    Returns:
        True if the combination is invalid (tracking on non-expense), False otherwise.
    """
    track = data.get(
        "is_envelope",
        getattr(template, "is_envelope", False),
    )
    if not track:
        return False
    type_id = data.get(
        "transaction_type_id",
        getattr(template, "transaction_type_id", None),
    )
    return type_id != ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


def _validate_template_form(data, on_invalid, template=None):
    """Validate submitted template data against ownership and tracking rules.

    Shared by :func:`create_template` and :func:`update_template`, whose
    create-vs-update difference is only that the create schema makes
    ``account_id`` / ``category_id`` required (always present) while the
    update schema makes them optional -- so guarding each check with
    ``in data`` is correct for both paths.  Checks, in order:

      1. ``account_id`` (when present) names an Account the user owns.
      2. ``account_id`` (when present) is NOT an amortizing loan -- a
         template on a loan would have the recurrence engine generate raw
         transactions onto it (finding N-11 / ruling D4; the create routes
         refuse the same shape via
         :func:`app.routes.transactions.create._reject_transaction_on_loan`).
      3. ``category_id`` (when present) names a Category the user owns.
      4. The resulting envelope-tracking state is expense-only
         (:func:`_is_tracking_on_non_expense`).

    Args:
        data: Deserialized form data (post ``schema.load``).
        on_invalid: Redirect destination for the first failed check.
        template: Existing TransactionTemplate for an update, or ``None``
            for a create, so the tracking check can fall back to the
            stored value on a partial update.

    Returns:
        A redirect ``Response`` for the first failed check, or ``None``
        when every check passes.
    """
    if "account_id" in data:
        acct = db.session.get(Account, data["account_id"])
        if not acct or acct.user_id != current_user.id:
            flash("Invalid account.", "danger")
            return on_invalid.to_response()
        if classify_account(acct) is AccountProjectionKind.AMORTIZING:
            # N-11 / ruling D4: a loan's balance is ledger-derived, not a
            # transaction sum.  A template targeting a loan would have the
            # recurrence engine generate raw transactions onto the loan
            # account (``recurrence_engine`` copies ``template.account_id``),
            # posting a bare cash leg the fold cannot see -- the same shape
            # the create routes refuse (``_reject_transaction_on_loan``) and
            # the transfer service forbids for a transfer out of a loan (R6).
            flash(
                "A loan's balance is not a transaction sum, so a template "
                "cannot target a loan account. Record loan payments as "
                "transfers.",
                "danger",
            )
            return on_invalid.to_response()
    if "category_id" in data:
        cat = db.session.get(Category, data["category_id"])
        if not cat or cat.user_id != current_user.id:
            flash("Invalid category.", "danger")
            return on_invalid.to_response()
    if _is_tracking_on_non_expense(data, template):
        flash("Purchase tracking is only available for expense templates.", "danger")
        return on_invalid.to_response()
    return None


def _apply_fields_and_propagate_rename(template, data):
    """Apply allowlisted field updates, propagating a rename to instances.

    Writes every :data:`_TEMPLATE_UPDATE_FIELDS` key present in *data* onto
    *template*, then propagates a changed name to EVERY existing Transaction
    generated from this template -- including soft-deleted ones.

    The rename propagation is load-bearing: ``regenerate_for_template``
    only deletes/recreates non-override rows on or after ``effective_from``,
    so historic rows, overrides, and settled rows would otherwise keep the
    old label and desync every view that renders ``txn.name`` directly
    (calendar CSV export, calendar, companion card, edit form header).
    Soft-deleted rows are renamed too, so a row later restored -- by the
    recurrence-conflict chooser's "use" action or by carry-forward --
    surfaces with the current name rather than a stale one.  The partial
    unique index on transactions covers ``(template_id, pay_period_id,
    scenario_id)`` only, so a bulk name update cannot trip a constraint.
    Template ownership is verified by the caller, so ``template_id`` alone
    scopes the update to the current user.
    """
    old_name = template.name
    for field, value in data.items():
        if field in _TEMPLATE_UPDATE_FIELDS:
            setattr(template, field, value)

    if template.name != old_name:
        db.session.query(Transaction).filter(
            Transaction.template_id == template.id,
        ).update({"name": template.name}, synchronize_session="fetch")


@templates_bp.route("/templates/new", methods=["GET"])
@login_required
@require_owner
def new_template():
    """Display the template creation form.

    The Recurring surface's "New" picker offers Expense / Income / Transfer;
    the Income entry links here with ``?type=income`` so the form lands with
    the income type pre-selected (expense and income share this form).  Any
    other ``type`` value falls back to expense.
    """
    categories = category_service.list_active_categories(current_user.id)
    accounts = account_service.list_active_accounts(current_user.id)
    txn_types = db.session.query(TransactionType).all()
    periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)

    if request.args.get("type") == _NEW_TYPE_INCOME:
        default_txn_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    else:
        default_txn_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    return render_template(
        "templates/form.html",
        template=None,
        categories=categories,
        accounts=accounts,
        pattern_choices=pattern_choices(),
        txn_types=txn_types,
        periods=periods,
        current_period=current_period,
        default_txn_type_id=default_txn_type_id,
        # A template that does not exist yet has no amount history; passed so
        # the shared form never references an undefined value.
        amount_history_rows=[],
        amount_today=None,
        amount_version_delete_endpoint="templates.delete_amount_version",
    )


@templates_bp.route("/templates", methods=["POST"])
@login_required
@require_owner
def create_template():
    """Create a new transaction template with optional recurrence rule."""
    invalid_payload = validate_form_or_redirect(
        _create_schema, RedirectTarget("templates.new_template"),
    )
    if invalid_payload is not None:
        return invalid_payload

    data = _create_schema.load(request.form)

    # Validate account/category ownership + expense-only tracking.
    invalid = _validate_template_form(
        data, on_invalid=RedirectTarget("templates.new_template"),
    )
    if invalid is not None:
        return invalid

    # The pop + ``build_recurrence_rule_from_form`` call below is the
    # shared create-form preamble; ``transfers.create_transfer_template``
    # runs the byte-identical sequence.  The rule-building logic itself is
    # already DRY in the F-24 helper; only the call site repeats, and it
    # cannot be hoisted into a further wrapper because the transfers side
    # reuses ``start_period_id`` afterward (its one-time-transfer branch)
    # while this route does not -- a wrapper that popped it internally
    # would have to thread it back out (coding-standards rule 13).
    # Pylint: ``duplicate-code`` -- one-sided disable; only this call site
    # repeats the transfers-side create-form preamble, and it cannot be
    # hoisted further (see plan.md Phase 2 notes).
    # pylint: disable=duplicate-code
    # Extract start_period_id and end_date before creating the rule.
    start_period_id = data.pop("start_period_id", None)
    end_date = data.pop("end_date", None)

    # Create the recurrence rule if a pattern was specified.  The
    # F-24 helper pops every recurrence-related key from ``data`` so
    # the TransactionTemplate constructor below does not receive
    # stray kwargs; it returns a flushed RecurrenceRule, ``None``
    # when no pattern was selected, or a Flask redirect Response
    # for the invalid-pattern / invalid-start-period validation
    # failures (caller returns the redirect verbatim).
    rule_or_redirect = build_recurrence_rule_from_form(
        data,
        user_id=current_user.id,
        start_period_id=start_period_id,
        ctx=RecurrenceFormContext(
            end_date_value=end_date,
            redirect=RedirectTarget("templates.new_template"),
            include_due_day_of_month=True,
        ),
    )
    if isinstance(rule_or_redirect, Response):
        return rule_or_redirect
    rule = rule_or_redirect
    # pylint: enable=duplicate-code

    # Create the template.
    template = TransactionTemplate(
        user_id=current_user.id,
        recurrence_rule_id=rule.id if rule else None,
        **data,
    )
    db.session.add(template)
    db.session.flush()

    # Open the amount's dated series at today (plan step X-au-a).  The
    # constructor above also carries the figure because the column is NOT NULL;
    # this call is what makes the SERIES exist, and plan step X-au-e removes the
    # redundancy by removing the column.  A template created today generates
    # rows into historical pay periods too, and those resolve by the series
    # holding flat before its earliest version
    # (``template_amount_service.amount_as_of``).
    template_amount_service.set_amount(
        template, template.default_amount, effective_on=display_today(),
    )

    # Auto-generate transactions from the rule into future periods.
    if rule:
        scenario = get_baseline_scenario(current_user.id)
        if scenario:
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(current_user.id),
                scenario.id,
            )

    db.session.commit()
    flash(
        f"Recurring transaction '{template.name}' created. "
        "View it on the Budget grid.",
        "success",
    )
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/edit", methods=["GET"])
@login_required
@require_owner
def edit_template(template_id):
    """Display the template edit form."""
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    categories = category_service.list_active_categories(current_user.id)
    accounts = account_service.list_active_accounts(current_user.id)
    txn_types = db.session.query(TransactionType).all()

    return render_template(
        "templates/form.html",
        template=template,
        categories=categories,
        accounts=accounts,
        # The EDIT picker, not the create one: a rule whose stored pattern the
        # application no longer models must stay selected, or the browser picks
        # the first option for the user -- the empty "Does not repeat" entry,
        # whose save DELETES the rule (R2e-1).
        pattern_choices=edit_form_pattern_choices(template),
        txn_types=txn_types,
        periods=[],
        current_period=None,
        # Unused when editing (the form reads the template's own type), but
        # passed so the shared template never references an undefined value.
        default_txn_type_id=None,
        # The amount's dated history (plan step X-au-a), precomputed into
        # display rows.  Empty for a salary-linked template, whose amount the
        # paycheck calculator derives and which therefore has no series at all.
        amount_history_rows=template_amount_service.build_amount_history(
            template, display_today(),
        ),
        # What the definition costs NOW, which is not the stored column whenever
        # a rise is SCHEDULED; the form's date input defaults to today, so the
        # two have to be the same question.
        amount_today=template_amount_service.current_amount(
            template, display_today(),
        ),
        amount_version_delete_endpoint="templates.delete_amount_version",
    )


# The transaction-template kind for the shared regenerate-or-chooser flow:
# how to regenerate, resolve, load, and re-edit an expense / income row.
_TXN_TEMPLATE_KIND = RecurrenceConflictKind(
    model=Transaction,
    amount_attr="estimated_amount",
    regenerate_fn=recurrence_engine.regenerate_for_template,
    resolve_fn=recurrence_engine.resolve_conflicts,
    update_endpoint="templates.update_template",
)


@templates_bp.route("/templates/<int:template_id>", methods=["POST"])
@login_required
@require_owner
def update_template(template_id):
    """Update a template and regenerate future transactions.

    Uses POST with _method=PUT for HTML form compatibility.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent edit that races past the form-side check
    -- is caught and converted to the same flash + redirect.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    invalid_payload = validate_form_or_redirect(
        _update_schema,
        RedirectTarget("templates.edit_template", {"template_id": template_id}),
    )
    if invalid_payload is not None:
        return invalid_payload

    # The load / version-guard / pop / resolve preamble below is the
    # standard parallel-CRUD update shape it shares with
    # ``transfers.update_transfer_template``.  Its substantive steps are
    # already DRY: the optimistic-lock guard (``handle_stale_form_conflict``)
    # and the recurrence-rule resolution (``resolve_recurrence_rule_for_update``)
    # live in the shared F-24 helper module.  What remains duplicated is only
    # the ORDER in which this route invokes those helpers; folding that call
    # sequence into a further helper would couple two separate template
    # domains (transaction-template envelope tracking + name propagation vs
    # transfer-template name-uniqueness + shadow invariants) behind awkward
    # multi-value returns for no real gain (coding-standards rule 13).
    # Pylint: ``duplicate-code`` -- one-sided disable; only the call sequence
    # is shared with ``transfers.update_transfer_template`` per the R0801
    # mechanics in ``docs/audits/pylint-cleanup/plan.md`` (Phase 2 working
    # notes).
    # pylint: disable=duplicate-code
    data = _update_schema.load(request.form)

    # Stale-form check (commit C-18 / F-010).  Routed through the
    # F-26 helper so the pre-flush optimistic-locking guard shares a
    # single implementation with the parallel transfer-template
    # update route.
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != template.version_id:
        return handle_stale_form_conflict(
            StaleConflictContext(
                logger=logger,
                log_label="update_template",
                log_id=template_id,
                flash_message=STALE_EDITING_MESSAGE.format(
                    noun="recurring transaction",
                ),
                redirect=RedirectTarget(
                    "templates.edit_template",
                    {"template_id": template_id},
                ),
            ),
            submitted=submitted_version,
            current=template.version_id,
        )

    effective_from = data.pop("effective_from", display_today())

    # Remove start_period_id from update data (set once at creation).
    data.pop("start_period_id", None)
    end_date = data.pop("end_date", None)

    # The template's before-image, captured BEFORE anything overwrites it
    # (plan step R2e-1).  ``had_recurrence_rule`` is what lets the
    # regeneration below tell "the user just cleared the recurrence" -- which
    # must sweep the instances the deleted rule generated -- from "this
    # template never recurred", which must not.
    before = PreEditTemplateState(
        amount=template.default_amount,
        had_recurrence_rule=template.recurrence_rule_id is not None,
    )

    # Re-point, rebuild, or clear the recurrence rule from the update payload
    # (F-24).  The helper dispatches the existing-rule (mutate in place)
    # vs no-existing-rule (build + link) branches and pops every
    # recurrence key from ``data`` so the field-update loop below sees
    # none.
    redirect_response = resolve_recurrence_rule_for_update(
        template,
        data,
        ctx=RecurrenceFormContext(
            end_date_value=end_date,
            redirect=RedirectTarget(
                "templates.edit_template",
                {"template_id": template_id},
            ),
            include_due_day_of_month=True,
        ),
    )
    if redirect_response is not None:
        return redirect_response
    # pylint: enable=duplicate-code

    # Validate account/category ownership + expense-only tracking on the
    # resulting state.  Shared with create_template via _validate_template_form.
    invalid = _validate_template_form(
        data,
        on_invalid=RedirectTarget(
            "templates.edit_template", {"template_id": template_id},
        ),
        template=template,
    )
    if invalid is not None:
        return invalid

    # State the amount through its one write door, which moves the scalar and
    # the dated series together (plan step X-au-a).  ``effective_from`` is the
    # form's "Amount effective from" date, which also bounds the regeneration
    # below -- ONE value, though the two apply it with different predicates: the
    # series answers by a row's own DUE date and the sweep selects by its pay
    # PERIOD's end, so an edit can rewrite a row whose due date precedes the
    # date it states (finding **N-247**, owned by X-au-e, which deletes the
    # sweep's amount arm and dissolves it).  Absent from a partial update means
    # the amount was not restated, and the series is untouched.
    #
    # **BEFORE the field loop, because that loop can FLUSH.**  A rename issues a
    # bulk UPDATE over this template's instances, which autoflushes whatever is
    # dirty; stating the amount afterwards would leave a second dirty write for
    # the commit and bump the optimistic-lock counter twice for one edit.
    if "default_amount" in data:
        template_amount_service.set_amount(
            template, data["default_amount"], effective_on=effective_from,
        )

    # Apply allowlisted field updates, propagating any rename to existing
    # instances (see _apply_fields_and_propagate_rename for the rationale).
    _apply_fields_and_propagate_rename(template, data)

    # Regenerate future transactions, diverting to the conflict chooser when
    # an amount change would overwrite hand-edited upcoming instances (the
    # chooser rolls the pending edit back; its Apply re-runs this same edit).
    diverted = regenerate_or_conflict_chooser(
        template, before, effective_from, _TXN_TEMPLATE_KIND,
        amount_drives_instances=not template_amount_service.is_salary_linked_template(
            template,
        ),
    )

    # The chooser short-circuits (its pending edit is already rolled back);
    # otherwise commit the edit, subject to the stale-version guard.
    response = diverted or commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="update_template",
        log_id=template_id,
        flash_message=STALE_EDITING_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect=RedirectTarget(
            "templates.edit_template",
            {"template_id": template_id},
        ),
    ))
    if response is not None:
        return response
    # An edit that ended the recurrence deleted this template's upcoming
    # projected rows; "updated." alone would report a destructive change as a
    # routine one.  Mirrors the archive route, which already names what it
    # removed.
    if before.had_recurrence_rule and template.recurrence_rule_id is None:
        flash(
            f"'{template.name}' no longer repeats. Its upcoming projected "
            "entries were removed; settled and hand-edited ones were kept.",
            "success",
        )
    else:
        flash(f"Recurring transaction '{template.name}' updated.", "success")
    return redirect(url_for("templates.list_templates"))


@templates_bp.route(
    "/templates/<int:template_id>/amount-versions/<int:version_id>/delete",
    methods=["POST"],
)
@login_required
@require_owner
def delete_amount_version(template_id, version_id):
    """Withdraw one entry from a template's amount history.

    The correction path for a price stamped against the wrong DATE: restating
    the amount writes a version at the date it names and leaves the mis-dated
    one standing, so removing it is a separate act.  The EARLIEST entry is
    refused by the service -- it is what every date before the series answers
    from -- and the way to move it is to state the amount at the right date
    first, which makes the old one no longer earliest.

    Ownership is the ``get_or_404`` on the TEMPLATE: the shared action looks the
    version up inside that template's own collection, so a ``version_id``
    belonging to another user's template is simply not found and the refusal is
    indistinguishable from "no such entry" (the security response rule).  The
    act itself is shared with the transfer-template twin
    (:func:`app.routes._amount_version_actions.withdraw_amount_version`).
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)
    return withdraw_amount_version(template, version_id, _AMOUNT_VERSION_ACTION)


@templates_bp.route("/templates/<int:template_id>/archive", methods=["POST"])
@login_required
@require_owner
def archive_template(template_id):
    """Archive a template (stops future generation, keeps history).

    Optimistic locking (commit C-18 / F-010): the
    ``is_active = False`` flush is version-pinned by SQLAlchemy.
    A concurrent edit raises ``StaleDataError`` which the handler
    converts to a flash + redirect so the user retries against
    fresh state.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    template.is_active = False

    # Soft-delete projected transactions for this template.
    # Centralized ``is_projected_clause`` (D6-09 / MED-02) so the
    # archive-template, unarchive-template, and hard-delete-fallback
    # filters in this module share one definition.
    deleted_count = db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
        is_projected_clause(Transaction),
        Transaction.is_deleted.is_(False),
    ).update({"is_deleted": True}, synchronize_session="fetch")

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="archive_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect=RedirectTarget("templates.list_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(
        f"Recurring transaction '{template.name}' archived. "
        f"{deleted_count} projected transaction(s) removed.",
        "info",
    )
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/unarchive", methods=["POST"])
@login_required
@require_owner
def unarchive_template(template_id):
    """Unarchive a template and restore projected transactions.

    Optimistic locking: see :func:`archive_template`.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    template.is_active = True

    # Restore soft-deleted projected transactions.  Routed through
    # ``is_projected_clause`` (D6-09 / MED-02); see ``archive_template``.
    restored_count = db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
        is_projected_clause(Transaction),
        Transaction.is_deleted.is_(True),
    ).update({"is_deleted": False}, synchronize_session="fetch")

    # Regenerate to fill in any missing future periods.
    if template.recurrence_rule:
        scenario = get_baseline_scenario(current_user.id)
        if scenario:
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_user(current_user.id),
                scenario.id,
                effective_from=date.today(),
            )

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="unarchive_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect=RedirectTarget("templates.list_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(
        f"Recurring transaction '{template.name}' unarchived. "
        f"{restored_count} projected transaction(s) restored.",
        "success",
    )
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/hard-delete", methods=["POST"])
@login_required
@require_owner
def hard_delete_template(template_id):
    """Permanently delete a transaction template if it has no settled history.

    Two-path logic:
      1. If the template has any settled transaction (Paid, Received, or
         Settled -- anything with ``Status.is_settled = True``), permanent
         deletion is blocked.  The template is archived instead (if not
         already) and the user is warned.
      2. If no settled history exists, all linked NON-SETTLED transactions
         are deleted first, then the template itself is permanently
         removed.  ``Transaction.template_id`` is a FK with ON DELETE SET
         NULL, so any rows that survive the filtered delete keep their
         financial data intact with a NULL template_id rather than
         cascading away.

    Defense in depth (CRIT-05 / E-22): the bulk delete is constrained to
    non-settled rows via the semantic ``Status.is_settled`` boolean.
    Even if the guard predicate above regresses, is bypassed, or races a
    concurrent mark-done that lands between the guard check and the
    delete, settled financial history (Paid, Received, Settled) cannot
    be physically destroyed by this route.  The pre-fix code enumerated
    ``[DONE, SETTLED]`` and silently omitted RECEIVED, then bulk-deleted
    unconditionally -- the irreversible data-loss path CRIT-05 documents.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    # The paid-history-blocked branch below (flash + archive toggle) is
    # the byte-identical sibling of
    # ``transfers.hard_delete_transfer_template``; only the
    # ``*_has_paid_history`` guard name and the divergent projected-row
    # soft-delete that follows differ.  The shared part is too thin and
    # too coupled to its two parallel routes to extract without
    # indirection that removes no logic (coding-standards rule 13).
    # Pylint: ``duplicate-code`` -- one-sided disable; the paid-history-blocked
    # branch mirrors ``transfers.hard_delete_transfer_template`` but is too
    # thin and too coupled to extract (see plan.md Phase 2 notes).
    # pylint: disable=duplicate-code
    if archive_helpers.template_has_paid_history(template.id):
        flash(
            f"'{template.name}' has payment history and cannot be permanently "
            "deleted. It has been archived instead.",
            "warning",
        )
        if template.is_active:
            template.is_active = False
            # pylint: enable=duplicate-code
            # Soft-delete projected transactions (same logic as
            # archive_template).  Routed through ``is_projected_clause``
            # (D6-09 / MED-02); see ``archive_template`` above.
            db.session.query(Transaction).filter(
                Transaction.template_id == template.id,
                is_projected_clause(Transaction),
                Transaction.is_deleted.is_(False),
            ).update({"is_deleted": True}, synchronize_session="fetch")
            conflict = commit_or_handle_stale(StaleConflictContext(
                logger=logger,
                log_label="hard_delete_template archive-fallback",
                log_id=template_id,
                flash_message=STALE_ACTION_MESSAGE.format(
                    noun="recurring transaction",
                ),
                redirect=RedirectTarget("templates.list_templates"),
            ))
            if conflict is not None:
                return conflict
        return redirect(url_for("templates.list_templates"))

    # No settled history -- safe to permanently delete.  Restrict the
    # bulk delete to non-settled rows via ``Status.is_settled`` so a
    # race-window mark-done (or any future caller that bypasses the
    # guard above) cannot destroy real Paid/Received/Settled history.
    # The FK ON DELETE SET NULL on ``Transaction.template_id`` means
    # any row that survives this filter keeps its financial data with
    # a null template_id rather than being cascaded away.
    template_name = template.name
    settled_status_ids = db.session.query(Status.id).filter(
        Status.is_settled.is_(True)
    ).scalar_subquery()
    db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
        Transaction.status_id.notin_(settled_status_ids),
    ).delete(synchronize_session="fetch")

    db.session.delete(template)
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="hard_delete_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect=RedirectTarget("templates.list_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(f"Recurring transaction '{template_name}' permanently deleted.", "info")
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/preview-recurrence", methods=["GET"])
@login_required
@require_owner
def preview_recurrence():
    """HTMX partial: show the next 5 occurrences for a recurrence pattern.

    Routing only.  The fragment is built by
    :func:`app.routes._recurrence_preview.recurrence_preview_fragment`, beside
    the three helpers it composes -- the endpoint is kind-agnostic (both the
    transaction-template and transfer-template forms point at it), so its body
    does not belong in the transaction-template CRUD module.
    """
    return recurrence_preview_fragment()
