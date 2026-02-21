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
    """Display all categories grouped by group_name."""
    categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id)
        .order_by(Category.sort_order, Category.group_name, Category.item_name)
        .all()
    )

    # Group categories by group_name for display.
    grouped = {}
    for cat in categories:
        grouped.setdefault(cat.group_name, []).append(cat)

    return render_template("categories/list.html", grouped=grouped)


@categories_bp.route("/categories", methods=["POST"])
@login_required
def create_category():
    """Create a new category."""
    errors = _create_schema.validate(request.form)
    if errors:
        if request.headers.get("HX-Request"):
            return jsonify(errors=errors), 400
        flash(f"Validation error: {errors}", "danger")
        return redirect(url_for("categories.list_categories"))

    data = _create_schema.load(request.form)

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
        return redirect(url_for("categories.list_categories"))

    category = Category(user_id=current_user.id, **data)
    db.session.add(category)
    db.session.commit()

    logger.info("Created category: %s", category.display_name)

    if request.headers.get("HX-Request"):
        return render_template("categories/_category_row.html", category=category)

    flash(f"Category '{category.display_name}' created.", "success")
    return redirect(url_for("categories.list_categories"))


@categories_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def delete_category(category_id):
    """Delete a category (only if not in use by any template or transaction)."""
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("categories.list_categories"))

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
        return redirect(url_for("categories.list_categories"))

    db.session.delete(category)
    db.session.commit()
    flash(f"Category '{category.display_name}' deleted.", "info")
    return redirect(url_for("categories.list_categories"))
