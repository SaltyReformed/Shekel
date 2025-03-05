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
from dateutil.relativedelta import relativedelta
from app.forms import (
    ExpenseCategoryForm,
    RecurringExpenseForm,
    ExpensePaymentForm,
    OneTimeExpenseForm,
    ExpenseFilterForm,
)
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
    expenses = (
        Expense.query.filter_by(user_id=user_id)
        .order_by(Expense.scheduled_date.desc())
        .all()
    )
    categories = ExpenseCategory.query.all()

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


# Route to add a one-time expense using a form
@expense_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_expense():
    user_id = session.get("user_id")
    form = OneTimeExpenseForm()

    # Populate form choices
    accounts = Account.query.filter_by(user_id=user_id).all()
    categories = ExpenseCategory.query.all()
    form.category_id.choices = [(0, "-- Select Category --")] + [
        (c.id, c.name) for c in categories
    ]
    form.account_id.choices = [(0, "-- Select Account --")] + [
        (a.id, a.account_name) for a in accounts
    ]

    if form.validate_on_submit():
        description = form.description.data
        amount = form.amount.data
        expense_date = form.expense_date.data
        category_id = form.category_id.data if form.category_id.data != 0 else None
        account_id = form.account_id.data if form.account_id.data != 0 else None
        notes = form.notes.data
        is_paid = form.is_paid.data

        # Ensure schedule type exists for expenses
        schedule_type = ScheduleType.query.filter_by(name="expense").first()
        if not schedule_type:
            schedule_type = ScheduleType(name="expense", description="Expense")
            db.session.add(schedule_type)
            db.session.commit()

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

        # Use expense ID in transaction description for easier tracking
        transaction_description = f"Expense {expense.id}: {description}"

        if is_paid and account_id:
            account = Account.query.get(account_id)
            payment = ExpensePayment(
                expense_id=expense.id,
                account_id=account_id,
                payment_date=expense_date,
                amount=amount,
            )
            db.session.add(payment)
            transaction = Transaction(
                account_id=account_id,
                transaction_date=expense_date,
                amount=amount,
                description=transaction_description,
                transaction_type="withdrawal",
            )
            db.session.add(transaction)
            account.balance -= amount
            db.session.commit()

        flash("Expense added successfully.", "success")
        return redirect(url_for("expense.overview"))

    # Pass is_edit=False to the template to indicate we're adding a new expense
    return render_template(
        "expenses/expense_form.html",  # Use the new combined template
        form=form,
        accounts=accounts,
        categories=categories,
        is_edit=False,  # We're in "add" mode
    )


# Route to edit an expense with improved payment update logic
@expense_bp.route("/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()
    accounts = Account.query.filter_by(user_id=user_id).all()
    categories = ExpenseCategory.query.all()

    # Use the OneTimeExpenseForm pre-populated with expense data
    form = OneTimeExpenseForm(obj=expense)

    # Set dropdown choices
    form.category_id.choices = [(0, "-- Select Category --")] + [
        (c.id, c.name) for c in categories
    ]
    form.account_id.choices = [(0, "-- Select Account --")] + [
        (a.id, a.account_name) for a in accounts
    ]

    # Retrieve existing payment (if any) and store its old details
    payment = ExpensePayment.query.filter_by(expense_id=expense_id).first()
    old_payment = None
    if payment:
        old_payment = {
            "account_id": payment.account_id,
            "amount": expense.amount,
            "payment_date": payment.payment_date,
        }
        # Set the account_id in the form
        form.account_id.data = payment.account_id

    if form.validate_on_submit():
        description = form.description.data
        new_amount = form.amount.data
        new_date = form.expense_date.data
        category_id = form.category_id.data if form.category_id.data != 0 else None
        new_account_id = form.account_id.data if form.account_id.data != 0 else None
        notes = form.notes.data
        is_paid = form.is_paid.data

        transaction_description = f"Expense {expense.id}: {description}"

        # Update expense details
        expense.description = description
        expense.amount = new_amount
        expense.scheduled_date = new_date
        expense.category_id = category_id
        expense.notes = notes
        expense.paid = is_paid

        # Fetch the related transaction record using the description pattern
        transaction = Transaction.query.filter(
            Transaction.description.like(f"Expense {expense.id}:%"),
            Transaction.transaction_type == "withdrawal",
        ).first()

        if is_paid:
            if not payment:
                # Create a new payment record if none exists
                if new_account_id:
                    account = Account.query.get(new_account_id)
                    new_payment = ExpensePayment(
                        expense_id=expense.id,
                        account_id=new_account_id,
                        payment_date=new_date,
                        amount=new_amount,
                    )
                    db.session.add(new_payment)
                    new_transaction = Transaction(
                        account_id=new_account_id,
                        transaction_date=new_date,
                        amount=new_amount,
                        description=transaction_description,
                        transaction_type="withdrawal",
                    )
                    db.session.add(new_transaction)
                    account.balance -= new_amount
            else:
                # Payment already exists – update accordingly
                if new_account_id != payment.account_id:
                    # Restore the old account balance
                    if payment.account_id:
                        old_account = Account.query.get(payment.account_id)
                        old_account.balance += old_payment["amount"]
                    # Deduct the new amount from the new account
                    if new_account_id:
                        new_account = Account.query.get(new_account_id)
                        new_account.balance -= new_amount
                    payment.account_id = new_account_id
                    if transaction:
                        transaction.account_id = new_account_id
                else:
                    # Same account; adjust for the difference
                    diff = new_amount - old_payment["amount"]
                    if diff != 0:
                        account = Account.query.get(new_account_id)
                        account.balance -= diff

                payment.payment_date = new_date
                payment.amount = new_amount
                if transaction:
                    transaction.transaction_date = new_date
                    transaction.amount = new_amount
                    transaction.description = transaction_description
        else:
            # Expense is no longer marked as paid
            if payment:
                account = Account.query.get(payment.account_id)
                account.balance += old_payment["amount"]
                if transaction:
                    db.session.delete(transaction)
                db.session.delete(payment)

        db.session.commit()
        flash("Expense updated successfully.", "success")
        return redirect(url_for("expense.overview"))

    # Pass is_edit=True to the template to indicate we're editing
    return render_template(
        "expenses/expense_form.html",  # Use the new combined template
        expense=expense,
        form=form,
        accounts=accounts,
        categories=categories,
        is_edit=True,  # We're in "edit" mode
    )


# Route to delete an expense
@expense_bp.route("/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_expense(expense_id):
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()
    payment = ExpensePayment.query.filter_by(expense_id=expense_id).first()

    try:
        if payment:
            if expense.paid:
                account = Account.query.get(payment.account_id)
                account.balance += payment.amount
            transaction = Transaction.query.filter(
                Transaction.description.like(f"Expense {expense.id}:%"),
                Transaction.transaction_type == "withdrawal",
            ).first()
            if transaction:
                db.session.delete(transaction)
            db.session.delete(payment)
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

    # Get account_id from POST or use default
    account_id = request.form.get("account_id")

    # If no account specified, try to get default from recurring schedule
    if not account_id and expense.recurring_schedule_id:
        schedule = RecurringSchedule.query.get(expense.recurring_schedule_id)
        if schedule and schedule.default_account_id:
            account_id = schedule.default_account_id

    if not account_id:
        flash("Please select an account to pay from.", "danger")
        return redirect(url_for("expense.edit_expense", expense_id=expense_id))

    if expense.paid:
        flash("Expense is already marked as paid.", "info")
        return redirect(url_for("expense.overview"))

    # account_id = request.form.get("account_id")
    # if not account_id:
    #     flash("Please select an account to pay from.", "danger")
    #     return redirect(url_for("expense.edit_expense", expense_id=expense_id))

    # account = Account.query.get(account_id)
    # if not account:
    #     flash("Selected account not found.", "danger")
    #     return redirect(url_for("expense.edit_expense", expense_id=expense_id))

    try:
        transaction_description = f"Expense {expense.id}: {expense.description}"
        payment = ExpensePayment(
            expense_id=expense.id,
            account_id=account_id,
            payment_date=date.today(),
            amount=expense.amount,
        )
        db.session.add(payment)
        transaction = Transaction(
            account_id=account_id,
            transaction_date=date.today(),
            amount=expense.amount,
            description=transaction_description,
            transaction_type="withdrawal",
        )
        db.session.add(transaction)
        account.balance -= expense.amount
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
    form = RecurringExpenseForm()

    # Load necessary data for dropdowns
    frequencies = Frequency.query.all()
    accounts = Account.query.filter_by(user_id=user_id).all()
    categories = ExpenseCategory.query.all()

    # Set form choices
    form.frequency_id.choices = [(f.id, f.name) for f in frequencies]
    form.category_id.choices = [(0, "-- Select Category --")] + [
        (c.id, c.name) for c in categories
    ]
    form.account_id.choices = [(0, "-- Select Account --")] + [
        (a.id, a.account_name) for a in accounts
    ]

    if request.method == "POST" and form.validate_on_submit():
        # Get form data
        description = form.description.data
        amount = form.amount.data
        frequency_id = form.frequency_id.data
        interval = form.interval.data or 1
        start_date = form.start_date.data
        end_date = form.end_date.data if form.end_date.data else None

        # Get category and account IDs, defaulting to None if not selected
        category_id = form.category_id.data if form.category_id.data != 0 else None
        account_id = form.account_id.data if form.account_id.data != 0 else None

        # Auto-pay setting if it exists
        auto_pay = form.auto_pay.data if hasattr(form, "auto_pay") else False

        # Get or create the expense schedule type
        schedule_type = ScheduleType.query.filter_by(name="expense").first()
        if not schedule_type:
            schedule_type = ScheduleType(name="expense", description="Expense")
            db.session.add(schedule_type)
            db.session.commit()

        # Create the schedule with all properties explicitly set
        schedule = RecurringSchedule(
            user_id=user_id,
            type_id=schedule_type.id,
            description=description,
            frequency_id=frequency_id,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            amount=amount,
            category_type="expense",  # Setting directly for expense
            category_id=category_id,
            default_account_id=account_id,
        )

        # Debug output
        print(f"Creating expense: category_id={category_id}, account_id={account_id}")

        db.session.add(schedule)
        db.session.commit()

        # Debug verification
        print(
            f"Saved schedule: ID={schedule.id}, category_id={schedule.category_id}, account_id={schedule.default_account_id}"
        )

        # Generate recurring expenses for future dates
        if "generate_expenses" in request.form:
            generate_recurring_expenses(
                user_id,
                schedule.id,
                auto_pay=auto_pay,
                category_id=category_id,
                account_id=account_id,
            )

        flash(
            "Recurring expense added successfully with future occurrences.", "success"
        )
        return redirect(url_for("expense.recurring_expenses"))

    # For GET requests, render the template with is_edit=False
    return render_template(
        "expenses/recurring_expense_form.html",  # New combined template name
        form=form,
        categories=categories,
        accounts=accounts,
        today=date.today(),
        is_edit=False,  # Indicate we're in "add" mode
    )


# Helper function to generate recurring expenses using relativedelta for accurate date math.
def generate_recurring_expenses(
    user_id,
    schedule_id,
    num_periods=6,
    auto_pay=False,
    category_id=None,
    account_id=None,
):
    schedule = RecurringSchedule.query.get_or_404(schedule_id)
    frequency = Frequency.query.get_or_404(schedule.frequency_id)

    latest_expense = (
        Expense.query.filter_by(recurring_schedule_id=schedule_id, user_id=user_id)
        .order_by(Expense.scheduled_date.desc())
        .first()
    )
    start_date = (
        latest_expense.scheduled_date if latest_expense else schedule.start_date
    )

    if frequency.name.lower() == "weekly":
        delta = relativedelta(weeks=schedule.interval)
    elif frequency.name.lower() == "biweekly":
        delta = relativedelta(weeks=2 * schedule.interval)
    elif frequency.name.lower() == "semimonthly":
        delta = timedelta(days=15 * schedule.interval)
    elif frequency.name.lower() == "monthly":
        delta = relativedelta(months=schedule.interval)
    elif frequency.name.lower() == "quarterly":
        delta = relativedelta(months=3 * schedule.interval)
    elif frequency.name.lower() == "annually":
        delta = relativedelta(years=schedule.interval)
    else:
        delta = relativedelta(weeks=2 * schedule.interval)  # Default

    expenses_created = 0
    current_date = start_date + delta

    for i in range(num_periods):
        if schedule.end_date and current_date > schedule.end_date:
            break

        existing_expense = Expense.query.filter_by(
            recurring_schedule_id=schedule_id,
            scheduled_date=current_date,
            user_id=user_id,
        ).first()

        if not existing_expense:
            expense = Expense(
                user_id=user_id,
                scheduled_date=current_date,
                amount=schedule.amount,
                description=schedule.description,
                recurring_schedule_id=schedule_id,
                paid=False,
            )

            # Set category if appropriate
            if schedule.category_type == "expense" and schedule.category_id:
                expense.category_id = schedule.category_id

            db.session.add(expense)
            expenses_created += 1

            if auto_pay:
                # Placeholder for auto-pay logic (if an account is associated, implement payment creation)
                pass

        if isinstance(delta, relativedelta):
            current_date += delta
        else:
            current_date = current_date + delta

    db.session.commit()
    return expenses_created


# Route to view all expenses with filtering options
@expense_bp.route("/all")
@login_required
def all_expenses():
    user_id = session.get("user_id")
    category_id = request.args.get("category_id", type=int)
    is_paid = request.args.get("is_paid")
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")

    # Use join to eagerly load the expense_category relationship
    query = Expense.query.filter_by(user_id=user_id).join(
        ExpenseCategory, Expense.category_id == ExpenseCategory.id, isouter=True
    )

    if category_id:
        query = query.filter_by(category_id=category_id)
    if is_paid == "paid":
        query = query.filter_by(paid=True)
    elif is_paid == "unpaid":
        query = query.filter_by(paid=False)
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            query = query.filter(Expense.scheduled_date >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Expense.scheduled_date <= end_date)
        except ValueError:
            pass

    expenses = query.order_by(Expense.scheduled_date.desc()).all()

    expenses_by_month = {}
    for expense in expenses:
        month_key = expense.scheduled_date.strftime("%Y-%m")
        if month_key not in expenses_by_month:
            expenses_by_month[month_key] = {
                "month_name": expense.scheduled_date.strftime("%B %Y"),
                "expenses": [],
                "total": decimal.Decimal(0),
            }
        expenses_by_month[month_key]["expenses"].append(expense)
        expenses_by_month[month_key]["total"] += expense.amount

    sorted_months = sorted(
        expenses_by_month.keys(), reverse=False
    )  # Changed to reverse=False to show oldest first
    categories = ExpenseCategory.query.all()
    accounts = Account.query.filter_by(user_id=user_id).all()

    return render_template(
        "expenses/all.html",
        expenses_by_month=expenses_by_month,
        sorted_months=sorted_months,
        categories=categories,
        accounts=accounts,
    )


# Route to view recurring expenses
@expense_bp.route("/recurring")
@login_required
def recurring_expenses():
    user_id = session.get("user_id")
    status = request.args.get("status")
    category_id = request.args.get("category", type=int)
    schedule_type = ScheduleType.query.filter_by(name="expense").first()

    if not schedule_type:
        return render_template(
            "expenses/recurring.html", recurring_expenses=[], categories=[]
        )

    query = RecurringSchedule.query.filter_by(user_id=user_id, type_id=schedule_type.id)

    if status == "active":
        query = query.filter(
            db.or_(
                RecurringSchedule.end_date == None,
                RecurringSchedule.end_date >= date.today(),
            )
        )
    elif status == "inactive":
        query = query.filter(
            RecurringSchedule.end_date != None,
            RecurringSchedule.end_date < date.today(),
        )

    recurring_expenses = query.all()

    # Calculate next_due_date for each recurring expense
    today = date.today()
    for schedule in recurring_expenses:
        # Find the most recent expense for this schedule
        latest_expense = (
            Expense.query.filter_by(recurring_schedule_id=schedule.id)
            .order_by(Expense.scheduled_date.desc())
            .first()
        )

        # Get the category for this schedule
        if latest_expense and latest_expense.category_id:
            category = ExpenseCategory.query.get(latest_expense.category_id)
            schedule.category = category
        else:
            # Create a dummy category if none is found
            dummy_category = type("obj", (object,), {"name": "Uncategorized"})
            # Create or store the category as an attribute instead of trying to set the property
            if not hasattr(schedule, 'category') or schedule.category is None:
                dummy_category = type("obj", (object,), {"name": "Uncategorized"})
                # Add as a regular attribute, not trying to use the property
                setattr(schedule, '_category', dummy_category)
                # Create a temporary method to access this attribute
                schedule.get_category_name = lambda: getattr(schedule, '_category').name

        # Calculate the next due date based on frequency
        if latest_expense:
            last_date = latest_expense.scheduled_date
        else:
            last_date = schedule.start_date

        # Use the same logic from your generate_recurring_expenses function
        from dateutil.relativedelta import relativedelta

        if hasattr(schedule, "frequency") and schedule.frequency:
            freq_name = schedule.frequency.name.lower()
            if freq_name == "weekly":
                delta = timedelta(days=7 * schedule.interval)
            elif freq_name == "biweekly":
                delta = timedelta(days=14 * schedule.interval)
            elif freq_name == "semimonthly":
                delta = timedelta(days=15 * schedule.interval)
            elif freq_name == "monthly":
                delta = relativedelta(months=schedule.interval)
            elif freq_name == "quarterly":
                delta = relativedelta(months=3 * schedule.interval)
            elif freq_name == "annually":
                delta = relativedelta(years=schedule.interval)
            else:
                delta = timedelta(days=30 * schedule.interval)  # Default

            # Calculate next due date
            next_due = last_date + delta

            # If the calculated date is in the past, keep adding the interval until we get a future date
            while next_due < today:
                next_due += delta

            # Add this as an attribute to the schedule object
            schedule.next_due_date = next_due
        else:
            # If there's no frequency, just use the start date
            schedule.next_due_date = schedule.start_date

    categories = ExpenseCategory.query.all()

    return render_template(
        "expenses/recurring.html",
        recurring_expenses=recurring_expenses,
        categories=categories,
    )


# Route to edit a recurring expense schedule
@expense_bp.route("/recurring/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def edit_recurring_expense(expense_id):
    user_id = session.get("user_id")
    schedule = RecurringSchedule.query.filter_by(
        id=expense_id, user_id=user_id
    ).first_or_404()
    expense_type = ScheduleType.query.filter_by(name="expense").first()
    if schedule.type_id != expense_type.id:
        flash("Invalid recurring expense", "danger")
        return redirect(url_for("expense.recurring_expenses"))

    # Load the form
    frequencies = Frequency.query.all()
    accounts = Account.query.filter_by(user_id=user_id).all()
    categories = ExpenseCategory.query.all()
    form = RecurringExpenseForm()

    # Set form choices
    form.frequency_id.choices = [(f.id, f.name) for f in frequencies]
    form.category_id.choices = [(0, "-- Select Category --")] + [
        (c.id, c.name) for c in categories
    ]
    form.account_id.choices = [(0, "-- Select Account --")] + [
        (a.id, a.account_name) for a in accounts
    ]

    # When loading the form, populate it with existing data
    if request.method == "GET":
        form.description.data = schedule.description
        form.amount.data = schedule.amount
        form.frequency_id.data = schedule.frequency_id
        form.interval.data = schedule.interval
        form.start_date.data = schedule.start_date
        form.end_date.data = schedule.end_date

        # Set category and account
        if schedule.category_type == "expense" and schedule.category_id:
            form.category_id.data = schedule.category_id

        if schedule.default_account_id:
            form.account_id.data = schedule.default_account_id

        # Handle notes if they exist
        if hasattr(schedule, "notes") and schedule.notes:
            form.notes.data = schedule.notes

    if form.validate_on_submit():
        # Update schedule with form data
        schedule.description = form.description.data
        schedule.amount = form.amount.data
        schedule.frequency_id = form.frequency_id.data
        schedule.interval = form.interval.data
        schedule.start_date = form.start_date.data
        schedule.end_date = form.end_date.data

        # Properly update category and account
        category_id = form.category_id.data if form.category_id.data != 0 else None
        account_id = form.account_id.data if form.account_id.data != 0 else None

        # Always set these explicitly
        schedule.category_type = "expense"
        schedule.category_id = category_id
        schedule.default_account_id = account_id

        # Debug output to server logs
        print(f"Updating schedule: category_id={category_id}, account_id={account_id}")

        # Handle notes if they exist on the model
        if hasattr(schedule, "notes"):
            schedule.notes = form.notes.data

        # Save changes
        db.session.commit()

        # Verify the data was saved
        print(
            f"Updated schedule: ID={schedule.id}, category_id={schedule.category_id}, account_id={schedule.default_account_id}"
        )

        # Generate future occurrences
        if "generate_expenses" in request.form:
            generate_recurring_expenses(
                user_id,
                schedule.id,
                auto_pay=form.auto_pay.data if hasattr(form, "auto_pay") else False,
                category_id=category_id,
                account_id=account_id,
            )

        flash("Recurring expense updated successfully", "success")
        return redirect(url_for("expense.recurring_expenses"))

    # Render the template with is_edit=True
    return render_template(
        "expenses/recurring_expense_form.html",  # New combined template name
        form=form,
        schedule=schedule,
        today=date.today(),
        is_edit=True,  # Indicate we're in "edit" mode
    )


# Route to delete a recurring expense and optionally its associated expenses
@expense_bp.route("/recurring/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_recurring_expense(expense_id):
    user_id = session.get("user_id")
    schedule = RecurringSchedule.query.filter_by(
        id=expense_id, user_id=user_id
    ).first_or_404()
    try:
        delete_expenses = request.form.get("delete_expenses") == "1"
        if delete_expenses:
            expenses = Expense.query.filter_by(recurring_schedule_id=expense_id).all()
            for expense in expenses:
                if expense.paid:
                    payment = ExpensePayment.query.filter_by(
                        expense_id=expense.id
                    ).first()
                    if payment:
                        account = Account.query.get(payment.account_id)
                        if account:
                            account.balance += payment.amount
                        db.session.delete(payment)
                db.session.delete(expense)
        schedule_desc = schedule.description
        db.session.delete(schedule)
        db.session.commit()
        flash(f"Recurring expense '{schedule_desc}' deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting recurring expense: {str(e)}", "danger")
    return redirect(url_for("expense.recurring_expenses"))


# Route to mark multiple expenses as paid in one operation
@expense_bp.route("/batch/pay", methods=["POST"])
@login_required
def batch_pay_expenses():
    user_id = session.get("user_id")
    expense_ids = request.form.getlist("expense_ids[]")
    account_id = request.form.get("account_id")
    if not expense_ids or not account_id:
        flash("No expenses selected or no account specified", "danger")
        return redirect(url_for("expense.all_expenses"))
    account = Account.query.get(account_id)
    if not account:
        flash("Invalid account selected", "danger")
        return redirect(url_for("expense.all_expenses"))

    success_count = 0
    total_amount = decimal.Decimal(0)

    for expense_id in expense_ids:
        expense = Expense.query.filter_by(
            id=expense_id, user_id=user_id, paid=False
        ).first()
        if expense:
            payment = ExpensePayment(
                expense_id=expense.id,
                account_id=account_id,
                payment_date=date.today(),
                amount=expense.amount,
            )
            db.session.add(payment)
            transaction_description = f"Expense {expense.id}: {expense.description}"
            transaction = Transaction(
                account_id=account_id,
                transaction_date=date.today(),
                amount=expense.amount,
                description=transaction_description,
                transaction_type="withdrawal",
            )
            db.session.add(transaction)
            account.balance -= expense.amount
            expense.paid = True
            success_count += 1
            total_amount += expense.amount

    db.session.commit()
    if success_count > 0:
        flash(
            f"Successfully paid {success_count} expenses totaling ${total_amount:.2f}",
            "success",
        )
    else:
        flash("No eligible expenses were found to pay", "warning")
    return redirect(url_for("expense.all_expenses"))
