"""
Shekel Budget App -- Category Routes

CRUD for the flat two-level category system (group + item name).
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required

from app.utils.auth_helpers import require_owner

from app.extensions import db
from app.models.category import Category
from app.schemas.validation import CategoryCreateSchema, CategoryEditSchema
from app.utils import archive_helpers

logger = logging.getLogger(__name__)

categories_bp = Blueprint("categories", __name__)

_create_schema = CategoryCreateSchema()
_edit_schema = CategoryEditSchema()


@categories_bp.route("/categories")
@login_required
@require_owner
def list_categories():
    """Redirect to settings dashboard categories section."""
    return redirect(url_for("settings.show", section="categories"))


@categories_bp.route("/categories", methods=["POST"])
@login_required
@require_owner
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
        # Fresh group list for the edit-form dropdown in the HTMX partial.
        group_names = sorted(
            row[0] for row in
            db.session.query(Category.group_name)
            .filter_by(user_id=current_user.id)
            .distinct()
        )
        return render_template(
            "categories/_category_row.html",
            category=category,
            group_names=group_names,
        )

    flash(f"Category '{category.display_name}' created.", "success")
    return redirect(url_for("settings.show", section="categories"))


@categories_bp.route("/categories/<int:category_id>/edit", methods=["POST"])
@login_required
@require_owner
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


@categories_bp.route("/categories/<int:category_id>/archive", methods=["POST"])
@login_required
@require_owner
def archive_category(category_id):
    """Archive a category (hide from active views, preserve data)."""
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    category.is_active = False
    db.session.commit()
    flash(f"Category '{category.display_name}' archived.", "info")
    return redirect(url_for("settings.show", section="categories"))


@categories_bp.route("/categories/<int:category_id>/unarchive", methods=["POST"])
@login_required
@require_owner
def unarchive_category(category_id):
    """Unarchive a category (return to active views)."""
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    category.is_active = True
    db.session.commit()
    flash(f"Category '{category.display_name}' unarchived.", "success")
    return redirect(url_for("settings.show", section="categories"))


@categories_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
@require_owner
def delete_category(category_id):
    """Permanently delete a category, or archive if in use.

    Uses archive_helpers.category_has_usage() to check whether any
    templates or transactions reference this category for the current
    user.  If in use, the category is archived instead of deleted.
    If not in use, it is permanently removed.
    """
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    if archive_helpers.category_has_usage(category_id, current_user.id):
        flash(
            f"'{category.display_name}' is in use and cannot be permanently "
            "deleted. It has been archived instead.",
            "warning",
        )
        if category.is_active:
            category.is_active = False
            db.session.commit()
        return redirect(url_for("settings.show", section="categories"))

    # No usage -- safe to permanently delete.
    db.session.delete(category)
    db.session.commit()
    flash(f"Category '{category.display_name}' permanently deleted.", "info")
    return redirect(url_for("settings.show", section="categories"))
