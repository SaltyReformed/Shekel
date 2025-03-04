from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps
import re

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func

from app.forms import ExpenseForm, RecurringExpenseForm
from models import (
    Account,
    Expense,
    ExpenseCategory,
    ExpensePayment,
    Frequency,
    RecurringSchedule,
    ScheduleType,
    Transaction,
    User,
    db,
)

expense_bp = Blueprint("expense", __name__, url_prefix="/expense")


# Helper function to require login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


# Route to display expense overview
@expense_bp.route("/")
@login_required
def overview():
    user_id = session.get("user_id")

    # Get expense categories
    categories = ExpenseCategory.query.all()

    # Get upcoming expenses
    upcoming_expenses = (
        Expense.query.filter_by(user_id=user_id, paid=False)
        .order_by(Expense.scheduled_date)
        .limit(10)
        .all()
    )

    # Get recent paid expenses
    recent_expenses = (
        Expense.query.filter_by(user_id=user_id, paid=True)
        .order_by(Expense.scheduled_date.desc())
        .limit(10)
        .all()
    )

    # Get recurring expense schedules
    recurring_schedules = (
        db.session.query(RecurringSchedule)
        .join(ScheduleType, ScheduleType.id == RecurringSchedule.type_id)
        .filter(ScheduleType.name == "expense")
        .filter(RecurringSchedule.user_id == user_id)
        .all()
    )

    # Calculate summary stats
    current_month = datetime.now().month
    current_year = datetime.now().year
    start_of_month = date(current_year, current_month, 1)
    if current_month == 12:
        end_of_month = date(current_year + 1, 1, 1) - timedelta(days=1)
    else:
        end_of_month = date(current_year, current_month + 1, 1) - timedelta(days=1)

    month_expenses = (
        db.session.query(func.sum(Expense.amount))
        .filter(
            Expense.user_id == user_id,
            Expense.scheduled_date >= start_of_month,
            Expense.scheduled_date <= end_of_month,
            Expense.paid == True,
        )
        .scalar()
        or 0
    )

    year_expenses = (
        db.session.query(func.sum(Expense.amount))
        .filter(
            Expense.user_id == user_id,
            Expense.scheduled_date >= date(current_year, 1, 1),
            Expense.scheduled_date <= date(current_year, 12, 31),
            Expense.paid == True,
        )
        .scalar()
        or 0
    )

    # Group expenses by category for the current month
    expenses_by_category = (
        db.session.query(ExpenseCategory.name, func.sum(Expense.amount).label("total"))
        .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .filter(
            Expense.user_id == user_id,
            Expense.scheduled_date >= start_of_month,
            Expense.scheduled_date <= end_of_month,
            Expense.paid == True,
        )
        .group_by(ExpenseCategory.name)
        .all()
    )

    return render_template(
        "expense/overview.html",
        upcoming_expenses=upcoming_expenses,
        recent_expenses=recent_expenses,
        recurring_schedules=recurring_schedules,
        month_expenses=month_expenses,
        year_expenses=year_expenses,
        expenses_by_category=expenses_by_category,
        categories=categories,
    )


# Route to add a one-time expense
@expense_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_expense():
    form = ExpenseForm()
    user_id = session.get("user_id")

    # Populate category dropdown
    categories = ExpenseCategory.query.all()
    form.category_id.choices = [(c.id, c.name) for c in categories]
    form.category_id.choices.insert(0, (0, "-- Select Category --"))

    # Populate account dropdown
    accounts = Account.query.filter_by(user_id=user_id).all()
    form.account_id.choices = [(a.id, a.account_name) for a in accounts]

    if form.validate_on_submit():
        # Create expense record
        expense = Expense(
            user_id=user_id,
            scheduled_date=form.expense_date.data,
            category_id=form.category_id.data if form.category_id.data != 0 else None,
            amount=form.amount.data,
            description=form.description.data,
            paid=form.is_paid.data,
            recurring_schedule_id=None,  # Not a recurring expense
        )

        db.session.add(expense)
        db.session.commit()

        # If expense is marked as paid, create expense payment and transaction
        if form.is_paid.data:
            # Create expense payment record
            payment = ExpensePayment(
                expense_id=expense.id,
                account_id=form.account_id.data,
                payment_date=form.expense_date.data,
                amount=form.amount.data,
            )
            db.session.add(payment)

            # Create transaction record
            transaction = Transaction(
                account_id=form.account_id.data,
                transaction_date=form.expense_date.data,
                amount=form.amount.data,
                description=f"Expense: {form.description.data}",
                transaction_type="withdrawal",
            )
            db.session.add(transaction)

            # Update account balance
            account = Account.query.get(form.account_id.data)
            account.balance -= form.amount.data

            db.session.commit()

        flash("Expense added successfully.", "success")
        return redirect(url_for("expense.overview"))

    return render_template("expense/add_expense.html", form=form)


# Route to edit an expense
@expense_bp.route("/edit/<int:expense_id>", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    form = ExpenseForm()

    # Populate category dropdown
    categories = ExpenseCategory.query.all()
    form.category_id.choices = [(c.id, c.name) for c in categories]
    form.category_id.choices.insert(0, (0, "-- Select Category --"))

    # Populate account dropdown
    accounts = Account.query.filter_by(user_id=user_id).all()
    form.account_id.choices = [(a.id, a.account_name) for a in accounts]

    # Get expense payment if exists
    payment = ExpensePayment.query.filter_by(expense_id=expense.id).first()

    if request.method == "GET":
        form.description.data = expense.description
        form.amount.data = expense.amount
        form.expense_date.data = expense.scheduled_date
        form.category_id.data = expense.category_id or 0
        form.is_paid.data = expense.paid
        form.notes.data = expense.notes if hasattr(expense, "notes") else ""

        if payment:
            form.account_id.data = payment.account_id

    if form.validate_on_submit():
        # Check if payment status is changing
        was_paid = expense.paid
        will_be_paid = form.is_paid.data

        # Update expense record
        expense.description = form.description.data
        expense.amount = form.amount.data
        expense.scheduled_date = form.expense_date.data
        expense.category_id = (
            form.category_id.data if form.category_id.data != 0 else None
        )
        expense.paid = form.is_paid.data
        if hasattr(expense, "notes"):
            expense.notes = form.notes.data

        # Handle payment status changes
        if not was_paid and will_be_paid:
            # Expense is being marked as paid

            # Create expense payment record
            payment = ExpensePayment(
                expense_id=expense.id,
                account_id=form.account_id.data,
                payment_date=form.expense_date.data,
                amount=form.amount.data,
            )
            db.session.add(payment)

            # Create transaction record
            transaction = Transaction(
                account_id=form.account_id.data,
                transaction_date=form.expense_date.data,
                amount=form.amount.data,
                description=f"Expense: {form.description.data}",
                transaction_type="withdrawal",
            )
            db.session.add(transaction)

            # Update account balance
            account = Account.query.get(form.account_id.data)
            account.balance -= form.amount.data

        elif was_paid and not will_be_paid:
            # Expense is being unmarked as paid

            # Find and delete transaction
            transaction = Transaction.query.filter_by(
                account_id=payment.account_id,
                transaction_date=payment.payment_date,
                amount=payment.amount,
                transaction_type="withdrawal",
            ).first()

            if transaction:
                db.session.delete(transaction)

            # Restore account balance
            account = Account.query.get(payment.account_id)
            account.balance += payment.amount

            # Delete payment record
            db.session.delete(payment)

        elif was_paid and will_be_paid and payment:
            # Expense remains paid but details might have changed

            # Check if account changed
            old_account_id = payment.account_id
            new_account_id = form.account_id.data

            if old_account_id != new_account_id or payment.amount != form.amount.data:
                # Restore old account balance
                old_account = Account.query.get(old_account_id)
                old_account.balance += payment.amount

                # Update payment details
                payment.account_id = new_account_id
                payment.payment_date = form.expense_date.data
                payment.amount = form.amount.data

                # Update transaction or create new one
                transaction = Transaction.query.filter_by(
                    account_id=old_account_id,
                    transaction_date=expense.scheduled_date,
                    amount=expense.amount,
                    transaction_type="withdrawal",
                ).first()

                if transaction:
                    # Update existing transaction
                    transaction.account_id = new_account_id
                    transaction.transaction_date = form.expense_date.data
                    transaction.amount = form.amount.data
                    transaction.description = f"Expense: {form.description.data}"
                else:
                    # Create new transaction
                    transaction = Transaction(
                        account_id=new_account_id,
                        transaction_date=form.expense_date.data,
                        amount=form.amount.data,
                        description=f"Expense: {form.description.data}",
                        transaction_type="withdrawal",
                    )
                    db.session.add(transaction)

                # Update new account balance
                new_account = Account.query.get(new_account_id)
                new_account.balance -= form.amount.data

        db.session.commit()
        flash("Expense updated successfully.", "success")
        return redirect(url_for("expense.manage_expenses"))

    return render_template(
        "expense/edit_expense.html",
        form=form,
        expense=expense,
        is_recurring=expense.recurring_schedule_id is not None,
    )


# Route to delete an expense
@expense_bp.route("/delete/<int:expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    try:
        # If the expense is paid, need to reverse the payment
        if expense.paid:
            # Get the payment record
            payment = ExpensePayment.query.filter_by(expense_id=expense.id).first()

            if payment:
                # Find and delete transaction
                transaction = Transaction.query.filter_by(
                    account_id=payment.account_id,
                    transaction_date=payment.payment_date,
                    amount=payment.amount,
                    transaction_type="withdrawal",
                ).first()

                if transaction:
                    db.session.delete(transaction)

                # Restore account balance
                account = Account.query.get(payment.account_id)
                account.balance += payment.amount

                # Delete payment record
                db.session.delete(payment)

        # Delete the expense
        db.session.delete(expense)
        db.session.commit()

        flash("Expense deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting expense: {str(e)}", "danger")

    return redirect(url_for("expense.manage_expenses"))


# Route to manage recurring expenses
@expense_bp.route("/recurring", methods=["GET", "POST"])
@login_required
def recurring_expense():
    form = RecurringExpenseForm()
    user_id = session.get("user_id")

    # Populate category dropdown
    categories = ExpenseCategory.query.all()
    form.category_id.choices = [(c.id, c.name) for c in categories]
    form.category_id.choices.insert(0, (0, "-- Select Category --"))

    # Populate frequency dropdown
    frequencies = Frequency.query.all()
    form.frequency_id.choices = [(f.id, f.name) for f in frequencies]

    # Populate account dropdown
    accounts = Account.query.filter_by(user_id=user_id).all()
    form.account_id.choices = [(a.id, a.account_name) for a in accounts]

    if form.validate_on_submit():
        # Get expense schedule type
        schedule_type = ScheduleType.query.filter_by(name="expense").first()
        if not schedule_type:
            schedule_type = ScheduleType(name="expense", description="Expense")
            db.session.add(schedule_type)
            db.session.commit()

        # Create recurring schedule
        schedule = RecurringSchedule(
            user_id=user_id,
            type_id=schedule_type.id,
            description=form.description.data,
            frequency_id=form.frequency_id.data,
            interval=form.interval.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            amount=form.amount.data,
        )

        db.session.add(schedule)
        db.session.commit()

        # Generate first set of expense instances
        generate_expenses_from_schedule(schedule.id, num_periods=form.num_periods.data)

        flash("Recurring expense created successfully.", "success")
        return redirect(url_for("expense.overview"))

    return render_template("expense/recurring_expense.html", form=form)


# Route to edit a recurring expense schedule
@expense_bp.route("/recurring/edit/<int:schedule_id>", methods=["GET", "POST"])
@login_required
def edit_recurring_expense(schedule_id):
    user_id = session.get("user_id")
    schedule = RecurringSchedule.query.filter_by(
        id=schedule_id, user_id=user_id
    ).first_or_404()

    form = RecurringExpenseForm()

    # Populate category dropdown
    categories = ExpenseCategory.query.all()
    form.category_id.choices = [(c.id, c.name) for c in categories]
    form.category_id.choices.insert(0, (0, "-- Select Category --"))

    # Populate frequency dropdown
    frequencies = Frequency.query.all()
    form.frequency_id.choices = [(f.id, f.name) for f in frequencies]

    # Populate account dropdown
    accounts = Account.query.filter_by(user_id=user_id).all()
    form.account_id.choices = [(a.id, a.account_name) for a in accounts]

    # Get a sample expense to get category_id and account_id
    sample_expense = Expense.query.filter_by(recurring_schedule_id=schedule.id).first()

    if request.method == "GET":
        form.description.data = schedule.description
        form.frequency_id.data = schedule.frequency_id
        form.interval.data = schedule.interval
        form.start_date.data = schedule.start_date
        form.end_date.data = schedule.end_date
        form.amount.data = schedule.amount
        form.num_periods.data = 3  # Default to generating 3 periods

        if sample_expense:
            form.category_id.data = sample_expense.category_id or 0

            # Find payment for this expense
            payment = ExpensePayment.query.filter_by(
                expense_id=sample_expense.id
            ).first()
            if payment:
                form.account_id.data = payment.account_id

    if form.validate_on_submit():
        # Update schedule
        schedule.description = form.description.data
        schedule.frequency_id = form.frequency_id.data
        schedule.interval = form.interval.data
        schedule.start_date = form.start_date.data
        schedule.end_date = form.end_date.data
        schedule.amount = form.amount.data

        db.session.commit()

        # Update category on all future expenses
        if sample_expense and sample_expense.category_id != form.category_id.data:
            future_expenses = Expense.query.filter(
                Expense.recurring_schedule_id == schedule.id,
                Expense.scheduled_date >= date.today(),
                Expense.paid == False,
            ).all()

            for expense in future_expenses:
                expense.category_id = (
                    form.category_id.data if form.category_id.data != 0 else None
                )

        # Generate additional expense instances if requested
        if form.generate_new.data:
            generate_expenses_from_schedule(
                schedule.id, num_periods=form.num_periods.data
            )

        flash("Recurring expense updated successfully.", "success")
        return redirect(url_for("expense.manage_recurring"))

    return render_template(
        "expense/edit_recurring_expense.html", form=form, schedule=schedule
    )


# Route to delete a recurring expense schedule
@expense_bp.route("/recurring/delete/<int:schedule_id>", methods=["POST"])
@login_required
def delete_recurring_expense(schedule_id):
    user_id = session.get("user_id")
    schedule = RecurringSchedule.query.filter_by(
        id=schedule_id, user_id=user_id
    ).first_or_404()

    # Check if we should delete associated expenses
    delete_expenses = request.form.get("delete_expenses") == "1"

    try:
        if delete_expenses:
            # Find all expenses for this schedule
            expenses = Expense.query.filter_by(recurring_schedule_id=schedule_id).all()

            for expense in expenses:
                # If the expense is paid, we need to handle account balance changes
                if expense.paid:
                    payment = ExpensePayment.query.filter_by(
                        expense_id=expense.id
                    ).first()
                    if payment:
                        # Find and delete transaction
                        transaction = Transaction.query.filter_by(
                            account_id=payment.account_id,
                            transaction_date=payment.payment_date,
                            amount=payment.amount,
                            transaction_type="withdrawal",
                        ).first()

                        if transaction:
                            db.session.delete(transaction)

                        # Restore account balance
                        account = Account.query.get(payment.account_id)
                        account.balance += payment.amount

                        # Delete payment
                        db.session.delete(payment)

                # Delete the expense
                db.session.delete(expense)

        # Delete the schedule
        db.session.delete(schedule)
        db.session.commit()

        if delete_expenses:
            flash(
                "Recurring expense and all associated expenses deleted successfully.",
                "success",
            )
        else:
            flash(
                "Recurring expense schedule deleted successfully. Existing expenses were preserved.",
                "success",
            )
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting recurring expense: {str(e)}", "danger")

    return redirect(url_for("expense.manage_recurring"))


# Route to manage all expenses
@expense_bp.route("/manage", methods=["GET"])
@login_required
def manage_expenses():
    user_id = session.get("user_id")

    # Get filter parameters
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    category_id = request.args.get("category_id")
    status = request.args.get("status")

    # Base query
    query = Expense.query.filter_by(user_id=user_id)

    # Apply filters
    if start_date:
        query = query.filter(
            Expense.scheduled_date >= datetime.strptime(start_date, "%Y-%m-%d").date()
        )

    if end_date:
        query = query.filter(
            Expense.scheduled_date <= datetime.strptime(end_date, "%Y-%m-%d").date()
        )

    if category_id and category_id != "0":
        query = query.filter(Expense.category_id == category_id)

    if status == "paid":
        query = query.filter(Expense.paid == True)
    elif status == "unpaid":
        query = query.filter(Expense.paid == False)

    # Get results
    expenses = query.order_by(Expense.scheduled_date.desc()).all()

    # Get categories for filter dropdown
    categories = ExpenseCategory.query.all()

    # Group expenses by month for easier display
    expenses_by_month = {}
    for expense in expenses:
        month_key = expense.scheduled_date.strftime("%Y-%m")
        if month_key not in expenses_by_month:
            expenses_by_month[month_key] = {
                "month_name": expense.scheduled_date.strftime("%B %Y"),
                "expenses": [],
                "total": 0,
            }
        expenses_by_month[month_key]["expenses"].append(expense)
        if expense.paid:
            expenses_by_month[month_key]["total"] += expense.amount

    # Sort months in reverse order (newest first)
    sorted_months = sorted(expenses_by_month.keys(), reverse=True)

    return render_template(
        "expense/manage_expenses.html",
        expenses_by_month=expenses_by_month,
        sorted_months=sorted_months,
        categories=categories,
    )


# Route to manage recurring expense schedules
@expense_bp.route("/recurring/manage", methods=["GET"])
@login_required
def manage_recurring():
    user_id = session.get("user_id")

    # Get recurring expense schedules
    schedules = (
        db.session.query(RecurringSchedule)
        .join(ScheduleType, ScheduleType.id == RecurringSchedule.type_id)
        .filter(ScheduleType.name == "expense")
        .filter(RecurringSchedule.user_id == user_id)
        .all()
    )

    # For each schedule, get the next upcoming expense
    for schedule in schedules:
        next_expense = (
            Expense.query.filter_by(
                recurring_schedule_id=schedule.id,
                paid=False,
            )
            .filter(Expense.scheduled_date >= date.today())
            .order_by(Expense.scheduled_date)
            .first()
        )

        schedule.next_expense = next_expense

    return render_template(
        "expense/manage_recurring.html",
        schedules=schedules,
    )


# Route to mark an expense as paid
@expense_bp.route("/mark-paid/<int:expense_id>", methods=["POST"])
@login_required
def mark_expense_paid(expense_id):
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    # Check if already paid
    if expense.paid:
        flash("Expense is already marked as paid.", "info")
        return redirect(url_for("expense.manage_expenses"))

    # Get account to debit
    account_id = request.form.get("account_id")
    if not account_id:
        flash("Please select an account to pay this expense from.", "danger")
        return redirect(url_for("expense.edit_expense", expense_id=expense.id))

    account = Account.query.filter_by(id=account_id, user_id=user_id).first()
    if not account:
        flash("Invalid account selected.", "danger")
        return redirect(url_for("expense.edit_expense", expense_id=expense.id))

    try:
        # Mark expense as paid
        expense.paid = True

        # Create expense payment record
        payment = ExpensePayment(
            expense_id=expense.id,
            account_id=account.id,
            payment_date=date.today(),
            amount=expense.amount,
        )
        db.session.add(payment)

        # Create transaction record
        transaction = Transaction(
            account_id=account.id,
            transaction_date=date.today(),
            amount=expense.amount,
            description=f"Expense: {expense.description}",
            transaction_type="withdrawal",
        )
        db.session.add(transaction)

        # Update account balance
        account.balance -= expense.amount

        db.session.commit()

        flash("Expense marked as paid successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error marking expense as paid: {str(e)}", "danger")

    return redirect(url_for("expense.manage_expenses"))


# Route to manage expense categories
@expense_bp.route("/categories", methods=["GET"])
@login_required
def categories():
    categories = ExpenseCategory.query.all()
    return render_template("expense/categories.html", categories=categories)


# Route to add expense category
@expense_bp.route("/categories/add", methods=["GET", "POST"])
@login_required
def add_category():
    from app.forms import CategoryForm

    form = CategoryForm()

    if form.validate_on_submit():
        category = ExpenseCategory(
            name=form.name.data,
            description=form.description.data,
        )

        db.session.add(category)
        db.session.commit()

        flash(f"Category '{form.name.data}' created successfully.", "success")
        return redirect(url_for("expense.categories"))

    return render_template("expense/edit_category.html", form=form, is_edit=False)


# Route to edit expense category
@expense_bp.route("/categories/edit/<int:category_id>", methods=["GET", "POST"])
@login_required
def edit_category(category_id):
    from app.forms import CategoryForm

    category = ExpenseCategory.query.get_or_404(category_id)
    form = CategoryForm()

    if request.method == "GET":
        form.name.data = category.name
        form.description.data = category.description

    if form.validate_on_submit():
        category.name = form.name.data
        category.description = form.description.data

        db.session.commit()

        flash(f"Category '{form.name.data}' updated successfully.", "success")
        return redirect(url_for("expense.categories"))

    return render_template("expense/edit_category.html", form=form, is_edit=True)


# Route to delete expense category
@expense_bp.route("/categories/delete/<int:category_id>", methods=["POST"])
@login_required
def delete_category(category_id):
    category = ExpenseCategory.query.get_or_404(category_id)

    try:
        db.session.delete(category)
        db.session.commit()

        flash(f"Category '{category.name}' deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(
            f"Error deleting category: {str(e)}. It may be in use by expenses.",
            "danger",
        )

    return redirect(url_for("expense.categories"))


# Helper function to generate expenses from a recurring schedule
def generate_expenses_from_schedule(
    schedule_id, start_date=None, end_date=None, num_periods=6
):
    """
    Generates projected expenses for a recurring schedule

    Args:
        schedule_id: The ID of the recurring schedule
        start_date: Optional start date to begin generating expenses
        end_date: Optional end date to stop generating expenses
        num_periods: Number of expense instances to generate
    """
    schedule = RecurringSchedule.query.get_or_404(schedule_id)
    user_id = schedule.user_id

    # If no start date provided, use the schedule's start date or today
    if not start_date:
        start_date = schedule.start_date

        # If we already have expenses for this schedule, start from the last one
        last_expense = (
            Expense.query.filter_by(recurring_schedule_id=schedule_id)
            .order_by(Expense.scheduled_date.desc())
            .first()
        )

        if last_expense:
            # Calculate the next date based on frequency
            frequency = Frequency.query.get(schedule.frequency_id)
            if frequency.name == "weekly":
                start_date = last_expense.scheduled_date + timedelta(
                    days=7 * schedule.interval
                )
            elif frequency.name == "biweekly":
                start_date = last_expense.scheduled_date + timedelta(
                    days=14 * schedule.interval
                )
            elif frequency.name == "semimonthly":
                # Simplified approach for semi-monthly
                start_date = last_expense.scheduled_date + timedelta(
                    days=15 * schedule.interval
                )
            elif frequency.name == "monthly":
                # Simplified approach for monthly
                start_date = last_expense.scheduled_date + timedelta(
                    days=30 * schedule.interval
                )
            else:
                # Default to biweekly if unknown frequency
                start_date = last_expense.scheduled_date + timedelta(
                    days=14 * schedule.interval
                )

    # Use schedule's end date if not provided
    if not end_date and schedule.end_date:
        end_date = schedule.end_date

    # Get frequency information
    frequency = Frequency.query.get(schedule.frequency_id)

    # Calculate time delta between expenses based on frequency
    if frequency.name == "weekly":
        delta = timedelta(days=7 * schedule.interval)
    elif frequency.name == "biweekly":
        delta = timedelta(days=14 * schedule.interval)
    elif frequency.name == "semimonthly":
        # Simplified approach for semi-monthly
        delta = timedelta(days=15 * schedule.interval)
    elif frequency.name == "monthly":
        # Simplified approach for monthly
        delta = timedelta(days=30 * schedule.interval)
    else:
        # Default to biweekly if unknown frequency
        delta = timedelta(days=14 * schedule.interval)

    # Generate expenses
    expenses = []
    current_date = start_date

    # Find a sample expense to get the category_id
    sample_expense = Expense.query.filter_by(recurring_schedule_id=schedule_id).first()

    category_id = None
    if sample_expense:
        category_id = sample_expense.category_id

    for i in range(num_periods):
        # Stop if we've reached the end date
        if end_date and current_date > end_date:
            break

        # Check if an expense already exists on this date
        existing = Expense.query.filter_by(
            recurring_schedule_id=schedule_id,
            scheduled_date=current_date,
        ).first()

        if not existing:
            # Create the expense
            expense = Expense(
                user_id=user_id,
                scheduled_date=current_date,
                category_id=category_id,
                amount=schedule.amount,
                description=schedule.description,
                recurring_schedule_id=schedule_id,
                paid=False,
            )

            expenses.append(expense)
            db.session.add(expense)

        # Increment the date for the next expense
        current_date += delta

    db.session.commit()
    return expenses
