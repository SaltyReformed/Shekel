"""
Shekel Budget App -- Category Routes

CRUD for the flat two-level category system (group + item name).
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required

from app.extensions import db
from app.models.category import Category
from app.schemas.validation import CategoryCreateSchema, CategoryEditSchema

logger = logging.getLogger(__name__)

categories_bp = Blueprint("categories", __name__)

_create_schema = CategoryCreateSchema()
_edit_schema = CategoryEditSchema()


@categories_bp.route("/categories")
@login_required
def list_categories():
    """Redirect to settings dashboard categories section."""
    return redirect(url_for("settings.show", section="categories"))


@categories_bp.route("/categories", methods=["POST"])
@login_required
def create_category():
    """Create a new category."""
    errors = _create_schema.validate(request.form)
    if errors:
        if request.headers.get("HX-Request"):
            return jsonify(errors=errors), 400
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    data = _create_schema.load(request.form)

    # Strip whitespace -- the schema allows it through Length(min=1).
    data["group_name"] = data["group_name"].strip()
    data["item_name"] = data["item_name"].strip()
    if not data["group_name"] or not data["item_name"]:
        if request.headers.get("HX-Request"):
            return jsonify(errors={"_schema": ["Category names cannot be blank."]}), 400
        flash("Category names cannot be blank.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    # Check for duplicates.
    existing = (
        db.session.query(Category)
        .filter_by(
            user_id=current_user.id,
            group_name=data["group_name"],
            item_name=data["item_name"],
        )
        .first()
    )
    if existing:
        flash("Category already exists.", "warning")
        return redirect(url_for("settings.show", section="categories"))

    category = Category(user_id=current_user.id, **data)
    db.session.add(category)
    db.session.commit()

    logger.info("Created category: %s", category.display_name)

    if request.headers.get("HX-Request"):
        return render_template("categories/_category_row.html", category=category)

    flash(f"Category '{category.display_name}' created.", "success")
    return redirect(url_for("settings.show", section="categories"))


@categories_bp.route("/categories/<int:category_id>/edit", methods=["POST"])
@login_required
def edit_category(category_id):
    """Edit a category item name and/or group assignment (re-parenting)."""
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    errors = _edit_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    data = _edit_schema.load(request.form)
    new_group = data["group_name"].strip()
    new_item = data["item_name"].strip()

    if not new_group or not new_item:
        flash("Category names cannot be blank.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    # Check for duplicate: another category with the same group + item
    # for this user.  PostgreSQL default collation is case-sensitive,
    # so "Auto" != "auto" -- intentional case changes are allowed.
    duplicate = (
        db.session.query(Category)
        .filter(
            Category.user_id == current_user.id,
            Category.group_name == new_group,
            Category.item_name == new_item,
            Category.id != category_id,
        )
        .first()
    )
    if duplicate:
        flash(f"Category '{new_group}: {new_item}' already exists.", "warning")
        return redirect(url_for("settings.show", section="categories"))

    old_name = category.display_name
    category.group_name = new_group
    category.item_name = new_item
    db.session.commit()

    logger.info("Edited category: %s -> %s", old_name, category.display_name)
    flash(f"Category updated to '{category.display_name}'.", "success")
    return redirect(url_for("settings.show", section="categories"))


@categories_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def delete_category(category_id):
    """Delete a category (only if not in use by any template or transaction)."""
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    # Check if the category is referenced by this user's templates or
    # active transactions.
    from app.models.transaction_template import TransactionTemplate  # pylint: disable=import-outside-toplevel
    from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel

    # Scope by user_id to prevent other users' templates from
    # blocking deletion.  See audit finding M6.
    in_use = (
        db.session.query(TransactionTemplate)
        .filter_by(category_id=category_id, user_id=current_user.id)
        .first()
    )
    if not in_use:
        # Transaction has no direct user_id -- join through PayPeriod
        # for correct ownership scoping.  Soft-deleted transactions
        # are included because the DB FK constraint would block
        # deletion regardless.
        in_use = (
            db.session.query(Transaction)
            .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
            .filter(
                PayPeriod.user_id == current_user.id,
                Transaction.category_id == category_id,
            )
            .first()
        )

    if in_use:
        flash("Cannot delete a category that is in use by templates or transactions.", "warning")
        return redirect(url_for("settings.show", section="categories"))

    db.session.delete(category)
    db.session.commit()
    flash(f"Category '{category.display_name}' deleted.", "info")
    return redirect(url_for("settings.show", section="categories"))
