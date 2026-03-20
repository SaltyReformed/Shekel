"""
Shekel Budget App — Category Routes

CRUD for the flat two-level category system (group + item name).
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required

from app.extensions import db
from app.models.category import Category
from app.schemas.validation import CategoryCreateSchema

logger = logging.getLogger(__name__)

categories_bp = Blueprint("categories", __name__)

_create_schema = CategoryCreateSchema()


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

    # Strip whitespace — the schema allows it through Length(min=1).
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


@categories_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def delete_category(category_id):
    """Delete a category (only if not in use by any template or transaction)."""
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    # Check if the category is referenced by templates or transactions.
    from app.models.transaction_template import TransactionTemplate
    from app.models.transaction import Transaction

    in_use = (
        db.session.query(TransactionTemplate)
        .filter_by(category_id=category_id)
        .first()
    ) or (
        db.session.query(Transaction)
        .filter_by(category_id=category_id)
        .first()
    )

    if in_use:
        flash("Cannot delete a category that is in use by templates or transactions.", "warning")
        return redirect(url_for("settings.show", section="categories"))

    db.session.delete(category)
    db.session.commit()
    flash(f"Category '{category.display_name}' deleted.", "info")
    return redirect(url_for("settings.show", section="categories"))
