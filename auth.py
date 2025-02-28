from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash
from models import db, User, Role
from functools import wraps
import re

auth_bp = Blueprint("auth", __name__)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "danger")
            return redirect(url_for("auth.login"))
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


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    # If user is already logged in, redirect to dashboard
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            session["username"] = user.username
            # Store role in session if exists
            if user.role:
                session["role"] = user.role.name
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.", "danger")
    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    # Clear the user's session
    session.pop("user_id", None)
    session.pop("username", None)
    session.pop("role", None)
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    # If user is already logged in, redirect to dashboard
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        email = request.form.get("email", "")
        first_name = request.form.get("first_name", "")
        last_name = request.form.get("last_name", "")

        # Check if username already exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("Username already exists. Please choose another one.", "danger")
            return render_template("register.html")

        # Validate password
        is_valid, message = validate_password(password)
        if not is_valid:
            flash(message, "danger")
            return render_template("register.html")

        # Create a new user with default role (non-admin)
        default_role = Role.query.filter_by(name="USER").first()

        # If there's no "USER" role, create it
        if not default_role:
            default_role = Role(
                name="USER", description="Regular user with standard permissions"
            )
            db.session.add(default_role)
            db.session.commit()

        new_user = User(
            username=username,
            password_hash=generate_password_hash(password),
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=default_role,
        )

        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful! You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = User.query.get_or_404(session["user_id"])

    if request.method == "POST":
        # Update profile information
        first_name = request.form.get("first_name", "")
        last_name = request.form.get("last_name", "")
        email = request.form.get("email", "")
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")

        # Update name and email
        user.first_name = first_name
        user.last_name = last_name
        user.email = email

        # Update password if current password is correct and new password is provided
        if current_password and new_password:
            if check_password_hash(user.password_hash, current_password):
                # Validate new password
                is_valid, message = validate_password(new_password)
                if not is_valid:
                    flash(message, "danger")
                    return render_template("profile.html", user=user)

                user.password_hash = generate_password_hash(new_password)
                flash("Password updated successfully.", "success")
            else:
                flash("Current password is incorrect.", "danger")
                return render_template("profile.html", user=user)

        db.session.commit()
        flash("Profile updated successfully.", "success")
        return redirect(url_for("auth.profile"))

    return render_template("profile.html", user=user)
