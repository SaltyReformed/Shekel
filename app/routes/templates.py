"""
Shekel Budget App — Template Management Routes

CRUD pages for transaction templates and their recurrence rules.
Updating a template triggers recurrence regeneration.
"""

import logging
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.category import Category
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.ref import RecurrencePattern, TransactionType
from app.schemas.validation import TemplateCreateSchema, TemplateUpdateSchema
from app.services import recurrence_engine, pay_period_service
from app.exceptions import RecurrenceConflict

logger = logging.getLogger(__name__)

templates_bp = Blueprint("templates", __name__)

_create_schema = TemplateCreateSchema()
_update_schema = TemplateUpdateSchema()


@templates_bp.route("/templates")
@login_required
def list_templates():
    """List all transaction templates for the current user."""
    templates = (
        db.session.query(TransactionTemplate)
        .filter_by(user_id=current_user.id)
        .order_by(TransactionTemplate.sort_order, TransactionTemplate.name)
        .all()
    )
    return render_template("templates/list.html", templates=templates)


@templates_bp.route("/templates/new", methods=["GET"])
@login_required
def new_template():
    """Display the template creation form."""
    categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    patterns = db.session.query(RecurrencePattern).all()
    txn_types = db.session.query(TransactionType).all()

    return render_template(
        "templates/form.html",
        template=None,
        categories=categories,
        accounts=accounts,
        patterns=patterns,
        txn_types=txn_types,
    )


@templates_bp.route("/templates", methods=["POST"])
@login_required
def create_template():
    """Create a new transaction template with optional recurrence rule."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash(f"Validation error: {errors}", "danger")
        return redirect(url_for("templates.new_template"))

    data = _create_schema.load(request.form)

    # Create the recurrence rule if a pattern was specified.
    rule = None
    pattern_name = data.pop("recurrence_pattern", None)
    if pattern_name:
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        rule = RecurrenceRule(
            user_id=current_user.id,
            pattern_id=pattern.id,
            interval_n=data.pop("interval_n", 1),
            offset_periods=data.pop("offset_periods", 0),
            day_of_month=data.pop("day_of_month", None),
            month_of_year=data.pop("month_of_year", None),
        )
        db.session.add(rule)
        db.session.flush()
    else:
        # Remove recurrence-related keys if no pattern.
        for key in ("interval_n", "offset_periods", "day_of_month", "month_of_year"):
            data.pop(key, None)

    # Create the template.
    template = TransactionTemplate(
        user_id=current_user.id,
        recurrence_rule_id=rule.id if rule else None,
        **data,
    )
    db.session.add(template)
    db.session.flush()

    # Auto-generate transactions from the rule into future periods.
    if rule:
        scenario = (
            db.session.query(Scenario)
            .filter_by(user_id=current_user.id, is_baseline=True)
            .first()
        )
        if scenario:
            periods = pay_period_service.get_all_periods(current_user.id)
            recurrence_engine.generate_for_template(
                template, periods, scenario.id,
                effective_from=date.today(),
            )

    db.session.commit()
    flash(f"Template '{template.name}' created.", "success")
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/edit", methods=["GET"])
@login_required
def edit_template(template_id):
    """Display the template edit form."""
    template = db.session.get(TransactionTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Template not found.", "danger")
        return redirect(url_for("templates.list_templates"))

    categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    patterns = db.session.query(RecurrencePattern).all()
    txn_types = db.session.query(TransactionType).all()

    return render_template(
        "templates/form.html",
        template=template,
        categories=categories,
        accounts=accounts,
        patterns=patterns,
        txn_types=txn_types,
    )


@templates_bp.route("/templates/<int:template_id>", methods=["POST"])
@login_required
def update_template(template_id):
    """Update a template and regenerate future transactions.

    Uses POST with _method=PUT for HTML form compatibility.
    """
    template = db.session.get(TransactionTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Template not found.", "danger")
        return redirect(url_for("templates.list_templates"))

    errors = _update_schema.validate(request.form)
    if errors:
        flash(f"Validation error: {errors}", "danger")
        return redirect(url_for("templates.edit_template", template_id=template_id))

    data = _update_schema.load(request.form)
    effective_from = data.pop("effective_from", date.today())

    # Update recurrence rule if pattern changed.
    pattern_name = data.pop("recurrence_pattern", None)
    if pattern_name:
        pattern = db.session.query(RecurrencePattern).filter_by(name=pattern_name).one()
        if template.recurrence_rule:
            template.recurrence_rule.pattern_id = pattern.id
            template.recurrence_rule.interval_n = data.pop("interval_n", 1)
            template.recurrence_rule.offset_periods = data.pop("offset_periods", 0)
            template.recurrence_rule.day_of_month = data.pop("day_of_month", None)
            template.recurrence_rule.month_of_year = data.pop("month_of_year", None)
        else:
            rule = RecurrenceRule(
                user_id=current_user.id,
                pattern_id=pattern.id,
                interval_n=data.pop("interval_n", 1),
                offset_periods=data.pop("offset_periods", 0),
                day_of_month=data.pop("day_of_month", None),
                month_of_year=data.pop("month_of_year", None),
            )
            db.session.add(rule)
            db.session.flush()
            template.recurrence_rule_id = rule.id
    else:
        for key in ("interval_n", "offset_periods", "day_of_month", "month_of_year"):
            data.pop(key, None)

    # Apply remaining field updates to the template.
    for field, value in data.items():
        if hasattr(template, field):
            setattr(template, field, value)

    # Regenerate future transactions.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    if scenario and template.recurrence_rule:
        periods = pay_period_service.get_all_periods(current_user.id)
        try:
            recurrence_engine.regenerate_for_template(
                template, periods, scenario.id, effective_from=effective_from,
            )
        except RecurrenceConflict as conflict:
            # Store conflict info in session for the resolution page.
            # For Phase 1, auto-keep overrides (simplest approach).
            logger.warning(
                "Recurrence conflict for template %d: %d overridden, %d deleted",
                template.id, len(conflict.overridden), len(conflict.deleted),
            )
            flash(
                f"Note: {len(conflict.overridden)} overridden and "
                f"{len(conflict.deleted)} deleted entries were kept as-is.",
                "warning",
            )

    db.session.commit()
    flash(f"Template '{template.name}' updated.", "success")
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/delete", methods=["POST"])
@login_required
def delete_template(template_id):
    """Deactivate a template (stops future generation, keeps history)."""
    template = db.session.get(TransactionTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Template not found.", "danger")
        return redirect(url_for("templates.list_templates"))

    template.is_active = False
    db.session.commit()

    flash(f"Template '{template.name}' deactivated.", "info")
    return redirect(url_for("templates.list_templates"))
