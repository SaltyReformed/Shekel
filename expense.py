from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    flash,
    request,
    session,
    jsonify,
)
from functools import wraps
from datetime import date, datetime, timedelta
import decimal
import re

from models import (
    db,
    Account,
    Expense,
    ExpenseCategory,
    ExpensePayment,
    RecurringSchedule,
    ScheduleType,
    Transaction,
    Frequency,
)

# Creating the blueprint
expense_bp = Blueprint("expense", __name__, url_prefix="/expenses")


# Helper function to require login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


# Expense overview page
@expense_bp.route("/")
@login_required
def overview():
    user_id = session.get("user_id")

    # Get expenses
    expenses = (
        Expense.query.filter_by(user_id=user_id)
        .order_by(Expense.scheduled_date.desc())
        .all()
    )

    # Get expense categories
    categories = ExpenseCategory.query.all()

    # Calculate monthly and yearly totals
    current_month = datetime.now().month
    current_year = datetime.now().year
    start_of_month = date(current_year, current_month, 1)

    if current_month == 12:
        end_of_month = date(current_year + 1, 1, 1) - timedelta(days=1)
    else:
        end_of_month = date(current_year, current_month + 1, 1) - timedelta(days=1)

    month_expenses = (
        db.session.query(db.func.sum(Expense.amount))
        .filter(
            Expense.user_id == user_id,
            Expense.scheduled_date >= start_of_month,
            Expense.scheduled_date <= end_of_month,
        )
        .scalar()
        or 0
    )

    year_expenses = (
        db.session.query(db.func.sum(Expense.amount))
        .filter(
            Expense.user_id == user_id,
            Expense.scheduled_date >= date(current_year, 1, 1),
            Expense.scheduled_date <= date(current_year, 12, 31),
        )
        .scalar()
        or 0
    )

    # Get recurring expenses
    recurring_expenses = (
        db.session.query(RecurringSchedule)
        .join(ScheduleType, ScheduleType.id == RecurringSchedule.type_id)
        .filter(ScheduleType.name == "expense")
        .filter(RecurringSchedule.user_id == user_id)
        .all()
    )

    return render_template(
        "expenses/overview.html",
        expenses=expenses,
        categories=categories,
        month_expenses=month_expenses,
        year_expenses=year_expenses,
        recurring_expenses=recurring_expenses,
    )


# Route to add a one-time expense
@expense_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_expense():
    user_id = session.get("user_id")

    # Get accounts and categories for the form
    accounts = Account.query.filter_by(user_id=user_id).all()
    categories = ExpenseCategory.query.all()

    if request.method == "POST":
        description = request.form.get("description")
        amount = request.form.get("amount")
        expense_date = request.form.get("expense_date")
        category_id = request.form.get("category_id") or None
        account_id = request.form.get("account_id") or None
        notes = request.form.get("notes", "")
        is_paid = "is_paid" in request.form

        # Validate inputs
        if not description or not amount or not expense_date:
            flash("Please fill out all required fields.", "danger")
            return render_template(
                "expenses/add_expense.html", accounts=accounts, categories=categories
            )

        try:
            amount = decimal.Decimal(amount)
            expense_date = datetime.strptime(expense_date, "%Y-%m-%d").date()
        except (ValueError, decimal.InvalidOperation):
            flash("Invalid amount or date format.", "danger")
            return render_template(
                "expenses/add_expense.html", accounts=accounts, categories=categories
            )

        # Get expense schedule type
        schedule_type = ScheduleType.query.filter_by(name="expense").first()
        if not schedule_type:
            schedule_type = ScheduleType(name="expense", description="Expense")
            db.session.add(schedule_type)
            db.session.commit()

        # Create a one-time expense (no recurring schedule)
        expense = Expense(
            user_id=user_id,
            scheduled_date=expense_date,
            category_id=category_id,
            amount=amount,
            description=description,
            paid=is_paid,
            notes=notes,
        )

        db.session.add(expense)
        db.session.commit()

        # If paid and account selected, create payment and transaction
        if is_paid and account_id:
            account = Account.query.get(account_id)

            # Create expense payment
            payment = ExpensePayment(
                expense_id=expense.id,
                account_id=account_id,
                payment_date=expense_date,
                amount=amount,
            )

            db.session.add(payment)

            # Create transaction in the account
            transaction = Transaction(
                account_id=account_id,
                transaction_date=expense_date,
                amount=amount,
                description=f"Expense: {description}",
                transaction_type="withdrawal",
            )

            db.session.add(transaction)

            # Update account balance
            account.balance -= amount

            db.session.commit()

        flash("Expense added successfully.", "success")
        return redirect(url_for("expense.overview"))

    return render_template(
        "expenses/add_expense.html", accounts=accounts, categories=categories
    )


# Route to edit an expense
@expense_bp.route("/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    # Get accounts and categories for the form
    accounts = Account.query.filter_by(user_id=user_id).all()
    categories = ExpenseCategory.query.all()

    # Get payment info if any
    payment = ExpensePayment.query.filter_by(expense_id=expense_id).first()
    account_id = payment.account_id if payment else None

    if request.method == "POST":
        description = request.form.get("description")
        amount = request.form.get("amount")
        expense_date = request.form.get("expense_date")
        category_id = request.form.get("category_id") or None
        new_account_id = request.form.get("account_id") or None
        notes = request.form.get("notes", "")
        is_paid = "is_paid" in request.form

        # Validate inputs
        if not description or not amount or not expense_date:
            flash("Please fill out all required fields.", "danger")
            return render_template(
                "expenses/edit_expense.html",
                expense=expense,
                accounts=accounts,
                categories=categories,
                selected_account_id=account_id,
            )

        try:
            new_amount = decimal.Decimal(amount)
            new_date = datetime.strptime(expense_date, "%Y-%m-%d").date()
        except (ValueError, decimal.InvalidOperation):
            flash("Invalid amount or date format.", "danger")
            return render_template(
                "expenses/edit_expense.html",
                expense=expense,
                accounts=accounts,
                categories=categories,
                selected_account_id=account_id,
            )

        # Handle payment status change and account updates
        had_payment = payment is not None
        old_amount = expense.amount

        # Update expense details
        expense.description = description
        expense.amount = new_amount
        expense.scheduled_date = new_date
        expense.category_id = category_id
        expense.notes = notes
        expense.paid = is_paid

        # Handle payment/account changes
        if is_paid:
            if not had_payment:
                # New payment
                if new_account_id:
                    account = Account.query.get(new_account_id)

                    # Create payment
                    new_payment = ExpensePayment(
                        expense_id=expense.id,
                        account_id=new_account_id,
                        payment_date=new_date,
                        amount=new_amount,
                    )

                    db.session.add(new_payment)

                    # Create transaction
                    transaction = Transaction(
                        account_id=new_account_id,
                        transaction_date=new_date,
                        amount=new_amount,
                        description=f"Expense: {description}",
                        transaction_type="withdrawal",
                    )

                    db.session.add(transaction)

                    # Update account balance
                    account.balance -= new_amount
            else:
                # Existing payment to update
                if new_account_id != account_id:
                    # Account changed
                    if account_id:
                        # Restore old account balance
                        old_account = Account.query.get(account_id)
                        old_account.balance += old_amount

                    if new_account_id:
                        # Update new account balance
                        new_account = Account.query.get(new_account_id)
                        new_account.balance -= new_amount

                        # Update payment
                        payment.account_id = new_account_id
                else:
                    # Same account, possibly different amount
                    if account_id and new_amount != old_amount:
                        account = Account.query.get(account_id)
                        account.balance += old_amount  # Restore old amount
                        account.balance -= new_amount  # Deduct new amount

                # Update payment details
                if payment:
                    payment.payment_date = new_date
                    payment.amount = new_amount

                    # Update related transaction if exists
                    transaction = Transaction.query.filter_by(
                        account_id=payment.account_id,
                        transaction_date=payment.payment_date,
                        amount=old_amount,
                        transaction_type="withdrawal",
                    ).first()

                    if transaction:
                        transaction.transaction_date = new_date
                        transaction.amount = new_amount
                        transaction.description = f"Expense: {description}"
        else:
            # Not paid anymore
            if had_payment:
                # Restore account balance
                if account_id:
                    account = Account.query.get(account_id)
                    account.balance += old_amount

                # Find and delete related transaction
                transaction = Transaction.query.filter_by(
                    account_id=account_id,
                    transaction_date=payment.payment_date,
                    amount=old_amount,
                    transaction_type="withdrawal",
                ).first()

                if transaction:
                    db.session.delete(transaction)

                # Delete payment
                db.session.delete(payment)

        db.session.commit()
        flash("Expense updated successfully.", "success")
        return redirect(url_for("expense.overview"))

    return render_template(
        "expenses/edit_expense.html",
        expense=expense,
        accounts=accounts,
        categories=categories,
        selected_account_id=account_id,
    )


# Route to delete an expense
@expense_bp.route("/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_expense(expense_id):
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    # Check if this expense has payments
    payment = ExpensePayment.query.filter_by(expense_id=expense_id).first()

    try:
        if payment:
            # Restore account balance if it was paid
            if expense.paid:
                account = Account.query.get(payment.account_id)
                account.balance += payment.amount

            # Find and delete related transaction
            transaction = Transaction.query.filter_by(
                account_id=payment.account_id,
                transaction_date=payment.payment_date,
                amount=payment.amount,
                transaction_type="withdrawal",
            ).first()

            if transaction:
                db.session.delete(transaction)

            # Delete payment
            db.session.delete(payment)

        # Delete expense
        db.session.delete(expense)
        db.session.commit()

        flash("Expense deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting expense: {str(e)}", "danger")

    return redirect(url_for("expense.overview"))


# Route to mark an expense as paid
@expense_bp.route("/<int:expense_id>/pay", methods=["POST"])
@login_required
def mark_expense_paid(expense_id):
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    # Check if already marked as paid
    if expense.paid:
        flash("Expense is already marked as paid.", "info")
        return redirect(url_for("expense.overview"))

    # Get account to pay from
    account_id = request.form.get("account_id")
    if not account_id:
        flash("Please select an account to pay from.", "danger")
        return redirect(url_for("expense.edit_expense", expense_id=expense_id))

    account = Account.query.get(account_id)
    if not account:
        flash("Selected account not found.", "danger")
        return redirect(url_for("expense.edit_expense", expense_id=expense_id))

    try:
        # Create payment record
        payment = ExpensePayment(
            expense_id=expense.id,
            account_id=account_id,
            payment_date=date.today(),
            amount=expense.amount,
        )
        db.session.add(payment)

        # Create transaction
        transaction = Transaction(
            account_id=account_id,
            transaction_date=date.today(),
            amount=expense.amount,
            description=f"Expense: {expense.description}",
            transaction_type="withdrawal",
        )
        db.session.add(transaction)

        # Update account balance
        account.balance -= expense.amount

        # Mark expense as paid
        expense.paid = True

        db.session.commit()

        flash("Expense marked as paid successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error marking expense as paid: {str(e)}", "danger")

    return redirect(url_for("expense.overview"))


# Route to add a recurring expense
@expense_bp.route("/recurring/add", methods=["GET", "POST"])
@login_required
def add_recurring_expense():
    user_id = session.get("user_id")

    # Get frequencies, accounts and categories for the form
    frequencies = Frequency.query.all()
    accounts = Account.query.filter_by(user_id=user_id).all()
    categories = ExpenseCategory.query.all()

    if request.method == "POST":
        description = request.form.get("description")
        amount = request.form.get("amount")
        frequency_id = request.form.get("frequency_id")
        interval = request.form.get("interval", 1)
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date", "")
        category_id = request.form.get("category_id") or None
        account_id = request.form.get("account_id") or None
        auto_pay = "auto_pay" in request.form

        # Validate inputs
        if not description or not amount or not frequency_id or not start_date:
            flash("Please fill out all required fields.", "danger")
            return render_template(
                "expenses/add_recurring_expense.html",
                frequencies=frequencies,
                accounts=accounts,
                categories=categories,
            )

        try:
            amount = decimal.Decimal(amount)
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_date = (
                datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
            )
            interval = int(interval)
            if interval < 1:
                interval = 1
        except (ValueError, decimal.InvalidOperation):
            flash("Invalid input values.", "danger")
            return render_template(
                "expenses/add_recurring_expense.html",
                frequencies=frequencies,
                accounts=accounts,
                categories=categories,
            )

        # Get expense schedule type
        schedule_type = ScheduleType.query.filter_by(name="expense").first()
        if not schedule_type:
            schedule_type = ScheduleType(name="expense", description="Expense")
            db.session.add(schedule_type)
            db.session.commit()

        # Create recurring expense schedule
        schedule = RecurringSchedule(
            user_id=user_id,
            type_id=schedule_type.id,
            description=description,
            frequency_id=frequency_id,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            amount=amount,
        )

        db.session.add(schedule)
        db.session.commit()

        # Generate future expenses based on the schedule
        generate_recurring_expenses(user_id, schedule.id, auto_pay=auto_pay)

        flash(
            "Recurring expense added successfully with future occurrences.", "success"
        )
        return redirect(url_for("expense.overview"))

    return render_template(
        "expenses/add_recurring_expense.html",
        frequencies=frequencies,
        accounts=accounts,
        categories=categories,
    )


# Helper function to generate recurring expenses
def generate_recurring_expenses(user_id, schedule_id, num_periods=6, auto_pay=False):
    """
    Generates projected expenses for a recurring schedule

    Args:
        user_id: The user ID
        schedule_id: The recurring schedule ID
        num_periods: Number of expenses to generate
        auto_pay: Whether to automatically mark expenses as paid
    """
    schedule = RecurringSchedule.query.get_or_404(schedule_id)
    frequency = Frequency.query.get_or_404(schedule.frequency_id)

    # Find latest generated expense for this schedule
    latest_expense = (
        Expense.query.filter_by(recurring_schedule_id=schedule_id, user_id=user_id)
        .order_by(Expense.scheduled_date.desc())
        .first()
    )

    # Determine start date for new expenses
    if latest_expense:
        start_date = latest_expense.scheduled_date
    else:
        start_date = schedule.start_date

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
    elif frequency.name == "quarterly":
        delta = timedelta(days=91 * schedule.interval)
    elif frequency.name == "annually":
        delta = timedelta(days=365 * schedule.interval)
    else:
        # Default to biweekly if unknown frequency
        delta = timedelta(days=14 * schedule.interval)

    # Generate expenses
    expenses_created = 0
    current_date = start_date + delta  # Start with the next occurrence

    for i in range(num_periods):
        # Stop if we've reached the end date
        if schedule.end_date and current_date > schedule.end_date:
            break

        # Skip if expense already exists for this date
        existing_expense = Expense.query.filter_by(
            recurring_schedule_id=schedule_id,
            scheduled_date=current_date,
            user_id=user_id,
        ).first()

        if not existing_expense:
            # Create the expense
            expense = Expense(
                user_id=user_id,
                scheduled_date=current_date,
                amount=schedule.amount,
                description=schedule.description,
                recurring_schedule_id=schedule_id,
                paid=False,
            )

            db.session.add(expense)
            expenses_created += 1

            # If auto-pay is enabled and there's an account, create payment
            if auto_pay:
                # You might want to add a default account to the schedule
                # For now, we'll leave it unpaid
                pass

        # Increment the date for the next expense
        current_date += delta

    db.session.commit()
    return expenses_created
