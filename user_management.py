from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import db, User, Role
from werkzeug.security import generate_password_hash
from functools import wraps
import re

user_bp = Blueprint("user", __name__, url_prefix="/users")


# Helper function to require admin login
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "danger")
            return redirect(url_for("auth.login"))

        user = User.query.get(session["user_id"])
        if not user or not user.role or user.role.name != "ADMIN":
            flash("You do not have permission to access this page.", "danger")
            return redirect(url_for("home"))

        return f(*args, **kwargs)

    return decorated_function


def validate_password(password):
    """
    Validate that password meets the requirements:
    - At least 16 characters
    - Contains uppercase
    - Contains lowercase
    - Contains numbers
    - Contains symbols

    Returns (is_valid, message) tuple
    """
    # Check password strength
    if len(password) < 16:
        return False, "Password must be at least 16 characters long"

    if not re.search(r"[A-Z]", password):
        return False, "Password must include at least one uppercase letter"

    if not re.search(r"[a-z]", password):
        return False, "Password must include at least one lowercase letter"

    if not re.search(r"[0-9]", password):
        return False, "Password must include at least one number"

    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must include at least one symbol"

    return True, "Password meets requirements"


# List all users
@user_bp.route("/")
@admin_required
def list_users():
    users = User.query.all()
    roles = Role.query.all()
    return render_template("users/list.html", users=users, roles=roles)


# Create a new user
@user_bp.route("/add", methods=["POST"])
@admin_required
def add_user():
    username = request.form.get("username")
    first_name = request.form.get("first_name", "")
    last_name = request.form.get("last_name", "")
    email = request.form.get("email", "")
    password = request.form.get("password")
    role_id = request.form.get("role_id")

    # Validate input
    if not username or not password:
        flash("Username and password are required", "danger")
        return redirect(url_for("user.list_users"))

    # Check if user already exists
    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        flash("Username already exists", "danger")
        return redirect(url_for("user.list_users"))

    # Validate password
    is_valid, message = validate_password(password)
    if not is_valid:
        flash(message, "danger")
        return redirect(url_for("user.list_users"))

    # Create new user
    new_user = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=email,
        password_hash=generate_password_hash(password),
        role_id=role_id,
    )

    db.session.add(new_user)
    db.session.commit()

    flash(f"User {username} created successfully", "success")
    return redirect(url_for("user.list_users"))


# Update a user
@user_bp.route("/edit/<int:user_id>", methods=["POST"])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    # Get form data
    first_name = request.form.get("first_name", "")
    last_name = request.form.get("last_name", "")
    email = request.form.get("email", "")
    role_id = request.form.get("role_id")
    password = request.form.get("password")

    # Update user's basic info
    user.first_name = first_name
    user.last_name = last_name
    user.email = email
    user.role_id = role_id

    # Only update password if a new one was provided
    if password:
        # Validate password
        is_valid, message = validate_password(password)
        if not is_valid:
            flash(message, "danger")
            return redirect(url_for("user.list_users"))

        user.password_hash = generate_password_hash(password)

    db.session.commit()

    flash(f"User {user.username} updated successfully", "success")
    return redirect(url_for("user.list_users"))


# Delete a user
@user_bp.route("/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    # Prevent deleting yourself
    if user.id == session.get("user_id"):
        flash("You cannot delete your own account", "danger")
        return redirect(url_for("user.list_users"))

    db.session.delete(user)
    db.session.commit()

    flash(f"User {user.username} deleted successfully", "success")
    return redirect(url_for("user.list_users"))
