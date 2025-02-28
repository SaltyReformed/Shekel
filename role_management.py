from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import db, Role, User
from functools import wraps
from user_management import admin_required

role_bp = Blueprint("role", __name__, url_prefix="/roles")


@role_bp.route("/")
@admin_required
def list_roles():
    """List all available roles"""
    roles = Role.query.all()
    return render_template("roles/list.html", roles=roles)


@role_bp.route("/add", methods=["GET", "POST"])
@admin_required
def add_role():
    """Add a new role"""
    if request.method == "POST":
        name = request.form.get("name").upper()
        description = request.form.get("description")

        # Check if role already exists
        existing_role = Role.query.filter_by(name=name).first()
        if existing_role:
            flash(f"Role '{name}' already exists.", "danger")
            return redirect(url_for("role.list_roles"))

        new_role = Role(name=name, description=description)
        db.session.add(new_role)

        try:
            db.session.commit()
            flash(f"Role '{name}' created successfully.", "success")
            return redirect(url_for("role.list_roles"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating role: {str(e)}", "danger")
            return redirect(url_for("role.list_roles"))

    return render_template("roles/add_role.html")


@role_bp.route("/edit/<int:role_id>", methods=["GET", "POST"])
@admin_required
def edit_role(role_id):
    """Edit an existing role"""
    role = Role.query.get_or_404(role_id)

    if request.method == "POST":
        role.name = request.form.get("name").upper()
        role.description = request.form.get("description")

        try:
            db.session.commit()
            flash(f"Role '{role.name}' updated successfully.", "success")
            return redirect(url_for("role.list_roles"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating role: {str(e)}", "danger")
            return redirect(url_for("role.list_roles"))

    return render_template("roles/edit_role.html", role=role)


@role_bp.route("/delete/<int:role_id>", methods=["POST"])
@admin_required
def delete_role(role_id):
    """Delete a role"""
    role = Role.query.get_or_404(role_id)

    # Prevent deleting roles with existing users
    users_with_role = User.query.filter_by(role_id=role_id).count()
    if users_with_role > 0:
        flash(
            f"Cannot delete role. {users_with_role} users are assigned to this role.",
            "danger",
        )
        return redirect(url_for("role.list_roles"))

    # Prevent deleting default roles
    default_roles = ["ADMIN", "USER"]
    if role.name.upper() in default_roles:
        flash(f"Cannot delete default role '{role.name}'.", "danger")
        return redirect(url_for("role.list_roles"))

    try:
        db.session.delete(role)
        db.session.commit()
        flash(f"Role '{role.name}' deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting role: {str(e)}", "danger")

    return redirect(url_for("role.list_roles"))
