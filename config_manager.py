from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from app.forms import (
    IncomeCategoryForm,
    FrequencyForm,
    RecurringScheduleForm,
    ScheduleTypeForm,
)
from models import db, IncomeCategory, Frequency, RecurringSchedule, ScheduleType, User
from functools import wraps
from datetime import date

config_bp = Blueprint("config", __name__, url_prefix="/config")


# Helper function to require login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


# Helper function to check if user is admin
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


# Income Categories Management
@config_bp.route("/categories")
@login_required
def categories():
    categories = IncomeCategory.query.all()
    return render_template("config/categories.html", categories=categories)


@config_bp.route("/categories/add", methods=["GET", "POST"])
@login_required
def add_category():
    form = IncomeCategoryForm()

    if form.validate_on_submit():
        category = IncomeCategory(
            name=form.name.data, description=form.description.data
        )
        db.session.add(category)
        db.session.commit()

        flash("Income category added successfully.", "success")
        return redirect(url_for("config.categories"))

    return render_template("config/edit_category.html", form=form, is_edit=False)


@config_bp.route("/categories/edit/<int:category_id>", methods=["GET", "POST"])
@login_required
def edit_category(category_id):
    category = IncomeCategory.query.get_or_404(category_id)
    form = IncomeCategoryForm(obj=category)

    if form.validate_on_submit():
        category.name = form.name.data
        category.description = form.description.data
        db.session.commit()

        flash("Income category updated successfully.", "success")
        return redirect(url_for("config.categories"))

    return render_template(
        "config/edit_category.html", form=form, is_edit=True, category=category
    )


@config_bp.route("/categories/delete/<int:category_id>", methods=["POST"])
@login_required
def delete_category(category_id):
    category = IncomeCategory.query.get_or_404(category_id)

    try:
        db.session.delete(category)
        db.session.commit()
        flash("Income category deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash("Cannot delete this category because it is in use.", "danger")

    return redirect(url_for("config.categories"))


# Frequencies Management
@config_bp.route("/frequencies")
@login_required
def frequencies():
    frequencies = Frequency.query.all()
    return render_template("config/frequencies.html", frequencies=frequencies)


@config_bp.route("/frequencies/add", methods=["GET", "POST"])
@login_required
def add_frequency():
    form = FrequencyForm()

    if form.validate_on_submit():
        frequency = Frequency(name=form.name.data, description=form.description.data)
        db.session.add(frequency)
        db.session.commit()

        flash("Frequency added successfully.", "success")
        return redirect(url_for("config.frequencies"))

    return render_template("config/edit_frequency.html", form=form, is_edit=False)


@config_bp.route("/frequencies/edit/<int:frequency_id>", methods=["GET", "POST"])
@login_required
def edit_frequency(frequency_id):
    frequency = Frequency.query.get_or_404(frequency_id)
    form = FrequencyForm(obj=frequency)

    if form.validate_on_submit():
        frequency.name = form.name.data
        frequency.description = form.description.data
        db.session.commit()

        flash("Frequency updated successfully.", "success")
        return redirect(url_for("config.frequencies"))

    return render_template(
        "config/edit_frequency.html", form=form, is_edit=True, frequency=frequency
    )


@config_bp.route("/frequencies/delete/<int:frequency_id>", methods=["POST"])
@login_required
def delete_frequency(frequency_id):
    frequency = Frequency.query.get_or_404(frequency_id)

    try:
        db.session.delete(frequency)
        db.session.commit()
        flash("Frequency deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash("Cannot delete this frequency because it is in use.", "danger")

    return redirect(url_for("config.frequencies"))


# Schedule Types Management
@config_bp.route("/schedule-types")
@admin_required
def schedule_types():
    types = ScheduleType.query.all()
    return render_template("config/schedule_types.html", types=types)


@config_bp.route("/schedule-types/add", methods=["GET", "POST"])
@admin_required
def add_schedule_type():
    form = ScheduleTypeForm()

    if form.validate_on_submit():
        schedule_type = ScheduleType(
            name=form.name.data, description=form.description.data
        )
        db.session.add(schedule_type)
        db.session.commit()

        flash("Schedule type added successfully.", "success")
        return redirect(url_for("config.schedule_types"))

    return render_template("config/edit_schedule_type.html", form=form, is_edit=False)


@config_bp.route("/schedule-types/edit/<int:type_id>", methods=["GET", "POST"])
@admin_required
def edit_schedule_type(type_id):
    schedule_type = ScheduleType.query.get_or_404(type_id)
    form = ScheduleTypeForm(obj=schedule_type)

    if form.validate_on_submit():
        schedule_type.name = form.name.data
        schedule_type.description = form.description.data
        db.session.commit()

        flash("Schedule type updated successfully.", "success")
        return redirect(url_for("config.schedule_types"))

    return render_template(
        "config/edit_schedule_type.html", form=form, is_edit=True, type=schedule_type
    )


@config_bp.route("/schedule-types/delete/<int:type_id>", methods=["POST"])
@admin_required
def delete_schedule_type(type_id):
    schedule_type = ScheduleType.query.get_or_404(type_id)

    try:
        db.session.delete(schedule_type)
        db.session.commit()
        flash("Schedule type deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash("Cannot delete this type because it is in use.", "danger")

    return redirect(url_for("config.schedule_types"))


# Recurring Schedules Management
@config_bp.route("/recurring-schedules")
@login_required
def recurring_schedules():
    user_id = session.get("user_id")
    schedules = RecurringSchedule.query.filter_by(user_id=user_id).all()
    today = date.today()
    return render_template(
        "config/recurring_schedules.html", schedules=schedules, today=today
    )


@config_bp.route("/recurring-schedules/add", methods=["GET", "POST"])
@login_required
def add_recurring_schedule():
    form = RecurringScheduleForm()

    # Get all frequencies for the dropdown
    frequencies = Frequency.query.all()
    form.frequency_id.choices = [(f.id, f.name) for f in frequencies]

    # Get all schedule types for the dropdown
    types = ScheduleType.query.all()
    form.type_id.choices = [(t.id, t.name) for t in types]

    if form.validate_on_submit():
        user_id = session.get("user_id")
        schedule = RecurringSchedule(
            user_id=user_id,
            description=form.description.data,
            frequency_id=form.frequency_id.data,
            interval=form.interval.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            amount=form.amount.data,
            type_id=form.type_id.data,
        )
        db.session.add(schedule)
        db.session.commit()

        flash("Recurring schedule added successfully.", "success")
        return redirect(url_for("config.recurring_schedules"))

    return render_template(
        "config/edit_recurring_schedule.html", form=form, is_edit=False
    )


@config_bp.route("/recurring-schedules/edit/<int:schedule_id>", methods=["GET", "POST"])
@login_required
def edit_recurring_schedule(schedule_id):
    user_id = session.get("user_id")
    schedule = RecurringSchedule.query.filter_by(
        id=schedule_id, user_id=user_id
    ).first_or_404()

    form = RecurringScheduleForm(obj=schedule)

    # Get all frequencies for the dropdown
    frequencies = Frequency.query.all()
    form.frequency_id.choices = [(f.id, f.name) for f in frequencies]

    # Get all schedule types for the dropdown
    types = ScheduleType.query.all()
    form.type_id.choices = [(t.id, t.name) for t in types]

    if form.validate_on_submit():
        schedule.description = form.description.data
        schedule.frequency_id = form.frequency_id.data
        schedule.interval = form.interval.data
        schedule.start_date = form.start_date.data
        schedule.end_date = form.end_date.data
        schedule.amount = form.amount.data
        schedule.type_id = form.type_id.data

        db.session.commit()

        flash("Recurring schedule updated successfully.", "success")
        return redirect(url_for("config.recurring_schedules"))

    return render_template(
        "config/edit_recurring_schedule.html",
        form=form,
        is_edit=True,
        schedule=schedule,
    )


@config_bp.route("/recurring-schedules/delete/<int:schedule_id>", methods=["POST"])
@login_required
def delete_recurring_schedule(schedule_id):
    user_id = session.get("user_id")
    schedule = RecurringSchedule.query.filter_by(
        id=schedule_id, user_id=user_id
    ).first_or_404()

    try:
        db.session.delete(schedule)
        db.session.commit()
        flash("Recurring schedule deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash("Cannot delete this schedule because it is in use.", "danger")

    return redirect(url_for("config.recurring_schedules"))
