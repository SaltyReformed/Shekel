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
    Paycheck,
    IncomePayment,
)

expense_bp = Blueprint("expense", __name__, url_prefix="/expenses")


# ===========================================
# Helper Functions
# ===========================================


def login_required(f):
    """Helper function to require login"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


def setup_expense_form(form, user_id):
    """Set up form choices for expense forms"""
    accounts = Account.query.filter_by(user_id=user_id).all()
    categories = ExpenseCategory.query.all()
    form.category_id.choices = [(0, "-- Select Category --")] + [
        (c.id, c.name) for c in categories
    ]
    form.account_id.choices = [(0, "-- Select Account --")] + [
        (a.id, a.account_name) for a in accounts
    ]
    return form, accounts, categories


def setup_recurring_form(form, user_id):
    """Set up form choices for recurring expense forms"""
    accounts = Account.query.filter_by(user_id=user_id).all()
    categories = ExpenseCategory.query.all()
    frequencies = Frequency.query.all()

    form.frequency_id.choices = [(f.id, f.name) for f in frequencies]
    form.category_id.choices = [(0, "-- Select Category --")] + [
        (c.id, c.name) for c in categories
    ]
    form.account_id.choices = [(0, "-- Select Account --")] + [
        (a.id, a.account_name) for a in accounts
    ]

    return form, accounts, categories, frequencies


def get_expense_type():
    """Get or create expense schedule type"""
    schedule_type = ScheduleType.query.filter_by(name="expense").first()
    if not schedule_type:
        schedule_type = ScheduleType(name="expense", description="Expense")
        db.session.add(schedule_type)
        db.session.commit()
    return schedule_type


def process_expense_payment(expense, account_id, payment_date=None):
    """Process payment for an expense, create transaction, update account balance"""
    if not payment_date:
        payment_date = date.today()

    account = Account.query.get(account_id)
    if not account:
        return False, "Invalid account"

    try:
        transaction_description = f"Expense {expense.id}: {expense.description}"
        payment = ExpensePayment(
            expense_id=expense.id,
            account_id=account_id,
            payment_date=payment_date,
            amount=expense.amount,
        )
        db.session.add(payment)
        transaction = Transaction(
            account_id=account_id,
            transaction_date=payment_date,
            amount=expense.amount,
            description=transaction_description,
            transaction_type="withdrawal",
        )
        db.session.add(transaction)
        account.balance -= expense.amount
        expense.paid = True
        db.session.commit()
        return True, "Payment processed successfully"
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def reverse_expense_payment(expense):
    """Reverse a payment for an expense, undo transaction, restore account balance"""
    payment = ExpensePayment.query.filter_by(expense_id=expense.id).first()
    if not payment:
        return False, "No payment found for this expense"

    try:
        # Find related transaction
        transaction = Transaction.query.filter(
            Transaction.description.like(f"Expense {expense.id}:%"),
            Transaction.transaction_type == "withdrawal",
        ).first()

        # Restore account balance
        account = Account.query.get(payment.account_id)
        if account:
            account.balance += payment.amount

        # Delete transaction and payment
        if transaction:
            db.session.delete(transaction)
        db.session.delete(payment)
        expense.paid = False
        db.session.commit()
        return True, "Payment reversed successfully"
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def get_date_range_filter():
    """Get date range filter from request args"""
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")

    start_date = None
    end_date = None

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    return start_date, end_date


def get_current_month_range():
    """Get date range for current month"""
    current_month = datetime.now().month
    current_year = datetime.now().year
    start_of_month = date(current_year, current_month, 1)

    if current_month == 12:
        end_of_month = date(current_year + 1, 1, 1) - timedelta(days=1)
    else:
        end_of_month = date(current_year, current_month + 1, 1) - timedelta(days=1)

    return start_of_month, end_of_month


def calculate_next_due_date(schedule, last_date):
    """Calculate next due date based on frequency"""
    if not hasattr(schedule, "frequency") or not schedule.frequency:
        return schedule.start_date

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
    today = date.today()
    while next_due < today:
        if isinstance(delta, relativedelta):
            next_due += delta
        else:
            next_due = next_due + delta

    return next_due


def get_time_delta_for_frequency(frequency_name, interval=1):
    """Get appropriate time delta based on frequency name"""
    if frequency_name.lower() == "weekly":
        return relativedelta(weeks=interval)
    elif frequency_name.lower() == "biweekly":
        return relativedelta(weeks=2 * interval)
    elif frequency_name.lower() == "semimonthly":
        return timedelta(days=15 * interval)
    elif frequency_name.lower() == "monthly":
        return relativedelta(months=interval)
    elif frequency_name.lower() == "quarterly":
        return relativedelta(months=3 * interval)
    elif frequency_name.lower() == "annually":
        return relativedelta(years=interval)
    else:
        return relativedelta(weeks=2 * interval)  # Default to biweekly


def generate_recurring_expenses(
    user_id,
    schedule_id,
    num_periods=6,
    auto_pay=False,
    category_id=None,
    account_id=None,
):
    """Generate recurring expenses for future dates"""
    schedule = RecurringSchedule.query.get_or_404(schedule_id)

    if not schedule.frequency:
        return 0

    latest_expense = (
        Expense.query.filter_by(recurring_schedule_id=schedule_id, user_id=user_id)
        .order_by(Expense.scheduled_date.desc())
        .first()
    )

    start_date = (
        latest_expense.scheduled_date if latest_expense else schedule.start_date
    )
    delta = get_time_delta_for_frequency(schedule.frequency.name, schedule.interval)

    expenses_created = 0
    # Set the current_date to the start_date initially (don't skip the first instance)
    current_date = start_date

    # If we have a latest expense, then we should start after that date
    if latest_expense:
        current_date = (
            start_date + delta
            if isinstance(delta, relativedelta)
            else start_date + delta
        )

    # If schedule has an end_date, we'll generate expenses until that date
    # Otherwise, we'll generate num_periods expenses
    has_end_date = schedule.end_date is not None

    # Loop until we reach the end date or have created num_periods expenses
    while (not has_end_date and expenses_created < num_periods) or (
        has_end_date and current_date <= schedule.end_date
    ):

        # Skip if we've passed the end date
        if has_end_date and current_date > schedule.end_date:
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

            if auto_pay and account_id:
                # Auto-pay logic (create payment when generated)
                expense.paid = True
                payment = ExpensePayment(
                    expense_id=expense.id,
                    account_id=account_id,
                    payment_date=current_date,
                    amount=expense.amount,
                )
                db.session.add(payment)
                # Note: Not updating account balance for future payments

        if isinstance(delta, relativedelta):
            current_date += delta
        else:
            current_date = current_date + delta

    db.session.commit()
    return expenses_created


def prepare_expense_filter_query(user_id):
    """Prepare filtered expense query based on request args"""
    category_id = request.args.get("category_id", type=int)
    is_paid = request.args.get("is_paid")
    start_date, end_date = get_date_range_filter()

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
    if start_date:
        query = query.filter(Expense.scheduled_date >= start_date)
    if end_date:
        query = query.filter(Expense.scheduled_date <= end_date)

    return query


# ===========================================
# Route Handlers
# ===========================================


@expense_bp.route("/")
@login_required
def overview():
    """Expense overview page"""
    user_id = session.get("user_id")
    # Default to showing recent/upcoming expenses
    query = prepare_expense_filter_query(user_id)
    expenses = query.order_by(Expense.scheduled_date.desc()).limit(10).all()
    categories = ExpenseCategory.query.all()

    # Calculate monthly and yearly expense totals
    start_of_month, end_of_month = get_current_month_range()
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

    current_year = datetime.now().year
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

    # Get active recurring expenses
    schedule_type = get_expense_type()
    recurring_expenses = (
        db.session.query(RecurringSchedule)
        .filter_by(user_id=user_id, type_id=schedule_type.id)
        .all()
    )

    # Calculate next due date for each recurring expense
    today = date.today()
    for schedule in recurring_expenses:
        # Find the most recent expense for this schedule
        latest_expense = (
            Expense.query.filter_by(recurring_schedule_id=schedule.id)
            .order_by(Expense.scheduled_date.desc())
            .first()
        )

        # Get category from recurring schedule
        if schedule.category_id and schedule.category_type == "expense":
            category = ExpenseCategory.query.get(schedule.category_id)
            if category:
                setattr(schedule, "_category", category)
        else:
            # Create a dummy category if none is found
            dummy_category = type("obj", (object,), {"name": "Uncategorized"})
            setattr(schedule, "_category", dummy_category)

        # Create a temporary method to access this attribute
        schedule.get_category_name = lambda: getattr(schedule, "_category").name

        # Calculate next due date based on frequency and last expense
        last_date = (
            latest_expense.scheduled_date if latest_expense else schedule.start_date
        )
        schedule.next_date = calculate_next_due_date(schedule, last_date)

    # Calculate upcoming expenses (next 7 days)
    next_week = today + timedelta(days=7)
    upcoming_count = Expense.query.filter(
        Expense.user_id == user_id,
        Expense.paid == False,
        Expense.scheduled_date >= today,
        Expense.scheduled_date <= next_week,
    ).count()

    accounts = Account.query.filter_by(user_id=user_id).all()

    # Make sure we can access the category in the template
    for schedule in recurring_expenses:
        # Use the get_category_name method or fallback to accessing category property directly
        if hasattr(schedule, "get_category_name"):
            schedule.category_name = schedule.get_category_name()
        elif schedule.category:
            schedule.category_name = schedule.category.name
        else:
            schedule.category_name = "Uncategorized"

    return render_template(
        "expenses/overview.html",
        expenses=expenses,
        categories=categories,
        month_expenses=month_expenses,
        year_expenses=year_expenses,
        recurring_expenses=recurring_expenses,
        upcoming_count=upcoming_count,
        accounts=accounts,
        today=today,
    )


@expense_bp.route("/manage", methods=["GET", "POST"])
@expense_bp.route("/manage/<int:expense_id>", methods=["GET", "POST"])
@login_required
def manage_expense(expense_id=None):
    """Add or edit an expense based on whether expense_id is provided"""
    user_id = session.get("user_id")
    is_edit = expense_id is not None

    # Get expense if editing
    expense = None
    if is_edit:
        expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    # Setup form
    form = OneTimeExpenseForm(obj=expense if expense else None)
    form, accounts, categories = setup_expense_form(form, user_id)

    # If recurring parameter is present, redirect to recurring expense form
    if request.args.get("recurring") == "true" and request.method == "GET":
        return redirect(url_for("expense.manage_recurring_expense"))

    # Get upcoming paychecks for the paycheck selection dropdown
    # Default to next 60 days if we're adding a new expense
    start_date = date.today()
    if expense and expense.scheduled_date:
        # If editing, include paychecks from a month before the expense date
        start_date = expense.scheduled_date - timedelta(days=30)

    end_date = start_date + timedelta(days=90)  # Show paychecks for the next 90 days

    paychecks = (
        Paycheck.query.filter(
            Paycheck.user_id == user_id,
            Paycheck.scheduled_date >= start_date,
            Paycheck.scheduled_date <= end_date,
        )
        .order_by(Paycheck.scheduled_date)
        .all()
    )

    # Form handling logic
    if form.validate_on_submit():
        if not is_edit:
            # Get expense type
            schedule_type = get_expense_type()

            # Create new expense
            expense = Expense(
                user_id=user_id,
                scheduled_date=form.expense_date.data,
                category_id=(
                    form.category_id.data if form.category_id.data != 0 else None
                ),
                amount=form.amount.data,
                description=form.description.data,
                paid=form.is_paid.data,
                notes=form.notes.data,
            )
            db.session.add(expense)
            db.session.flush()  # Get ID without committing
        else:
            # Update existing expense
            expense.description = form.description.data
            expense.amount = form.amount.data
            expense.scheduled_date = form.expense_date.data
            expense.category_id = (
                form.category_id.data if form.category_id.data != 0 else None
            )
            expense.notes = form.notes.data

        # Handle paycheck assignment
        paycheck_id = request.form.get("paycheck_id")
        if paycheck_id:
            expense.paycheck_id = int(paycheck_id)
        else:
            expense.paycheck_id = None

        # Handle payment status change
        old_paid_status = expense.paid if is_edit else False
        new_paid_status = form.is_paid.data
        account_id = form.account_id.data if form.account_id.data != 0 else None

        # Handle payment status changes
        if new_paid_status != old_paid_status:
            if new_paid_status and account_id:
                success, message = process_expense_payment(
                    expense, account_id, form.expense_date.data
                )
                if not success:
                    flash(f"Error processing payment: {message}", "danger")
                    return redirect(
                        url_for(
                            "expense.manage_expense",
                            expense_id=expense.id if is_edit else None,
                        )
                    )
            elif not new_paid_status and old_paid_status:
                success, message = reverse_expense_payment(expense)
                if not success:
                    flash(f"Error reversing payment: {message}", "danger")
                    return redirect(
                        url_for("expense.manage_expense", expense_id=expense.id)
                    )

        expense.paid = new_paid_status
        db.session.commit()

        flash(f"Expense {('updated' if is_edit else 'added')} successfully.", "success")
        return redirect(url_for("expense.overview"))

    return render_template(
        "expenses/expense_form.html",
        form=form,
        expense=expense,
        accounts=accounts,
        categories=categories,
        paychecks=paychecks,
        is_edit=is_edit,
    )


@expense_bp.route("/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_expense(expense_id):
    """Delete an expense"""
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    try:
        # If the expense is paid, reverse the payment first
        if expense.paid:
            success, message = reverse_expense_payment(expense)
            if not success:
                flash(f"Error reversing payment: {message}", "danger")
                return redirect(
                    url_for("expense.manage_expense", expense_id=expense_id)
                )

        db.session.delete(expense)
        db.session.commit()
        flash("Expense deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting expense: {str(e)}", "danger")

    return redirect(url_for("expense.overview"))


@expense_bp.route("/<int:expense_id>/pay", methods=["POST"])
@login_required
def mark_expense_paid(expense_id):
    """Mark an expense as paid"""
    user_id = session.get("user_id")
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    if expense.paid:
        flash("Expense is already marked as paid.", "info")
        return redirect(url_for("expense.overview"))

    # Get account_id from POST or use default
    account_id = request.form.get("account_id")

    # If no account specified, try to get default from recurring schedule
    if not account_id and expense.recurring_schedule_id:
        schedule = RecurringSchedule.query.get(expense.recurring_schedule_id)
        if schedule and schedule.default_account_id:
            account_id = schedule.default_account_id

    if not account_id:
        flash("Please select an account to pay from.", "danger")
        return redirect(url_for("expense.manage_expense", expense_id=expense_id))

    # Process the payment
    success, message = process_expense_payment(expense, account_id)

    if success:
        flash("Expense marked as paid successfully.", "success")
    else:
        flash(f"Error marking expense as paid: {message}", "danger")

    return redirect(url_for("expense.all_expenses"))


@expense_bp.route("/batch/pay", methods=["POST"])
@login_required
def batch_pay_expenses():
    """Mark multiple expenses as paid"""
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
            success, _ = process_expense_payment(expense, account_id)
            if success:
                success_count += 1
                total_amount += expense.amount

    if success_count > 0:
        flash(
            f"Successfully paid {success_count} expenses totaling ${total_amount:.2f}",
            "success",
        )
    else:
        flash("No eligible expenses were found to pay", "warning")

    return redirect(url_for("expense.all_expenses"))


@expense_bp.route("/all")
@login_required
def all_expenses():
    """View all expenses with filtering"""
    user_id = session.get("user_id")
    query = prepare_expense_filter_query(user_id)

    # Add explicit join to RecurringSchedule to load default_account_id
    query = query.outerjoin(
        RecurringSchedule, Expense.recurring_schedule_id == RecurringSchedule.id
    )

    expenses = query.order_by(Expense.scheduled_date.desc()).all()

    # Group expenses by month
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

    sorted_months = sorted(expenses_by_month.keys(), reverse=False)
    categories = ExpenseCategory.query.all()
    accounts = Account.query.filter_by(user_id=user_id).all()

    return render_template(
        "expenses/all.html",
        expenses_by_month=expenses_by_month,
        sorted_months=sorted_months,
        categories=categories,
        accounts=accounts,
    )


@expense_bp.route("/recurring")
@login_required
def recurring_expenses():
    """View recurring expenses"""
    user_id = session.get("user_id")
    status = request.args.get("status")
    category_id = request.args.get("category", type=int)

    schedule_type = get_expense_type()

    # Build query with filters
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
        # Find most recent expense
        latest_expense = (
            Expense.query.filter_by(recurring_schedule_id=schedule.id)
            .order_by(Expense.scheduled_date.desc())
            .first()
        )

        # Get category for display
        if schedule.category_type == "expense" and schedule.category_id:
            category = ExpenseCategory.query.get(schedule.category_id)
            # Store as private attribute instead of using the property
            setattr(schedule, "_category", category)
        else:
            # Create dummy category if none found
            dummy_category = type("obj", (object,), {"name": "Uncategorized"})
            setattr(schedule, "_category", dummy_category)

        # Create a temporary method to access this attribute
        schedule.get_category_name = lambda: getattr(schedule, "_category").name

        # Calculate next due date
        last_date = (
            latest_expense.scheduled_date if latest_expense else schedule.start_date
        )
        schedule.next_due_date = calculate_next_due_date(schedule, last_date)

    categories = ExpenseCategory.query.all()
    accounts = Account.query.filter_by(user_id=user_id).all()

    # Make sure we can access the category in the template
    for schedule in recurring_expenses:
        # Use the get_category_name method or fallback to accessing category property directly
        if hasattr(schedule, "get_category_name"):
            schedule.category_name = schedule.get_category_name()
        elif schedule.category:
            schedule.category_name = schedule.category.name
        else:
            schedule.category_name = "Uncategorized"

    return render_template(
        "expenses/recurring.html",
        recurring_expenses=recurring_expenses,
        categories=categories,
        accounts=accounts,
        today=today,
    )


@expense_bp.route("/recurring/manage", methods=["GET", "POST"])
@expense_bp.route("/recurring/manage/<int:expense_id>", methods=["GET", "POST"])
@login_required
def manage_recurring_expense(expense_id=None):
    """Add or edit a recurring expense"""
    user_id = session.get("user_id")
    is_edit = expense_id is not None

    # Get schedule if editing
    schedule = None
    if is_edit:
        schedule = RecurringSchedule.query.filter_by(
            id=expense_id, user_id=user_id
        ).first_or_404()
        expense_type = get_expense_type()

        # Verify it's an expense schedule
        if schedule.type_id != expense_type.id:
            flash("Invalid recurring expense", "danger")
            return redirect(url_for("expense.recurring_expenses"))

    # Setup form
    form = RecurringExpenseForm(obj=schedule if schedule else None)
    form, accounts, categories, frequencies = setup_recurring_form(form, user_id)

    # Populate form with existing data for edit
    if request.method == "GET" and is_edit:
        form.description.data = schedule.description
        form.amount.data = schedule.amount
        form.frequency_id.data = schedule.frequency_id
        form.interval.data = schedule.interval
        form.start_date.data = schedule.start_date
        form.end_date.data = schedule.end_date

        # Set category and account if they exist
        if schedule.category_type == "expense" and schedule.category_id:
            form.category_id.data = schedule.category_id

        if schedule.default_account_id:
            form.account_id.data = schedule.default_account_id

    # Process form submission
    if form.validate_on_submit():
        if not is_edit:
            # Get expense type
            schedule_type = get_expense_type()

            # Create new schedule
            schedule = RecurringSchedule(user_id=user_id, type_id=schedule_type.id)

        # Update schedule attributes
        schedule.description = form.description.data
        schedule.amount = form.amount.data
        schedule.frequency_id = form.frequency_id.data
        schedule.interval = form.interval.data or 1
        schedule.start_date = form.start_date.data
        schedule.end_date = form.end_date.data

        # Set category and account
        category_id = form.category_id.data if form.category_id.data != 0 else None
        account_id = form.account_id.data if form.account_id.data != 0 else None

        schedule.category_type = "expense"
        schedule.category_id = category_id
        schedule.default_account_id = account_id

        # Create/update in database
        if not is_edit:
            db.session.add(schedule)
        db.session.commit()

        # Generate future occurrences if requested
        if "generate_expenses" in request.form:
            auto_pay = form.auto_pay.data if hasattr(form, "auto_pay") else False
            generate_recurring_expenses(
                user_id,
                schedule.id,
                auto_pay=auto_pay,
                category_id=category_id,
                account_id=account_id,
            )

        flash(
            f"Recurring expense {('updated' if is_edit else 'created')} successfully.",
            "success",
        )
        return redirect(url_for("expense.recurring_expenses"))

    return render_template(
        "expenses/recurring_expense_form.html",
        form=form,
        schedule=schedule,
        today=date.today(),
        is_edit=is_edit,
    )


@expense_bp.route("/recurring/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_recurring_expense(expense_id):
    """Delete a recurring expense schedule and optionally its associated expenses"""
    user_id = session.get("user_id")
    schedule = RecurringSchedule.query.filter_by(
        id=expense_id, user_id=user_id
    ).first_or_404()

    try:
        # Check if we should delete associated expenses
        delete_expenses = request.form.get("delete_expenses") == "1"

        if delete_expenses:
            # Find all expenses from this schedule
            expenses = Expense.query.filter_by(recurring_schedule_id=expense_id).all()

            for expense in expenses:
                # If expense is paid, reverse the payment
                if expense.paid:
                    success, _ = reverse_expense_payment(expense)

                # Delete the expense
                db.session.delete(expense)

        # Store description for flash message
        schedule_desc = schedule.description

        # Delete the schedule
        db.session.delete(schedule)
        db.session.commit()

        flash(f"Recurring expense '{schedule_desc}' deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting recurring expense: {str(e)}", "danger")

    return redirect(url_for("expense.recurring_expenses"))


@expense_bp.route("/by_paycheck")
@login_required
def expenses_by_paycheck():
    """View expenses organized by which paycheck they'll be paid from"""
    user_id = session.get("user_id")

    # Get date range filter (default to next 60 days if not specified)
    start_date, end_date = get_date_range_filter()
    if not start_date:
        start_date = date.today()
    if not end_date:
        end_date = start_date + timedelta(days=60)

    # Get all paychecks in the date range
    paychecks = (
        Paycheck.query.filter(
            Paycheck.user_id == user_id,
            Paycheck.scheduled_date >= start_date,
            Paycheck.scheduled_date <= end_date,
        )
        .order_by(Paycheck.scheduled_date)
        .all()
    )

    # Get all expenses in the date range
    expense_query = Expense.query.filter(
        Expense.user_id == user_id,
        Expense.scheduled_date >= start_date,
        Expense.scheduled_date <= end_date,
    )

    # Apply category filter if specified
    category_id = request.args.get("category_id", type=int)
    if category_id:
        expense_query = expense_query.filter_by(category_id=category_id)

    expenses = expense_query.order_by(Expense.scheduled_date).all()

    # Map expenses to paychecks
    expenses_by_paycheck = {p.id: [] for p in paychecks}

    # First, populate with expenses that have a direct paycheck_id assignment
    for expense in expenses:
        if expense.paycheck_id and expense.paycheck_id in expenses_by_paycheck:
            expenses_by_paycheck[expense.paycheck_id].append(expense)

    # Next, assign remaining expenses based on date logic
    for expense in expenses:
        if expense.paycheck_id is None:
            # Find the last paycheck that comes before the expense date
            assigned = False

            # Sort paychecks by date (earliest to latest)
            sorted_paychecks = sorted(paychecks, key=lambda p: p.scheduled_date)

            # Find the last paycheck that comes before or on the expense date
            for i, paycheck in enumerate(sorted_paychecks):
                if paycheck.scheduled_date > expense.scheduled_date:
                    # This paycheck is after the expense date
                    if i > 0:
                        # Assign to the previous paycheck (the last one before the expense)
                        appropriate_paycheck = sorted_paychecks[i - 1]
                        expenses_by_paycheck[appropriate_paycheck.id].append(expense)

                        # Also update the paycheck_id in the database for future reference
                        expense.paycheck_id = appropriate_paycheck.id
                        assigned = True
                    break

            # If we went through all paychecks and none are after the expense date,
            # assign to the last paycheck
            if not assigned and sorted_paychecks:
                appropriate_paycheck = sorted_paychecks[-1]
                expenses_by_paycheck[appropriate_paycheck.id].append(expense)
                expense.paycheck_id = appropriate_paycheck.id
                assigned = True

            # If there are no paychecks available or the expense is before all paychecks,
            # assign to the first paycheck
            if not assigned and paychecks:
                appropriate_paycheck = sorted_paychecks[0]
                expenses_by_paycheck[appropriate_paycheck.id].append(expense)
                expense.paycheck_id = appropriate_paycheck.id

    # Commit the changes to paycheck_id
    db.session.commit()

    # Calculate totals
    paycheck_totals = {}
    paycheck_remaining = {}

    for paycheck in paychecks:
        total_expenses = sum(
            expense.amount for expense in expenses_by_paycheck[paycheck.id]
        )
        paycheck_totals[paycheck.id] = total_expenses
        paycheck_remaining[paycheck.id] = paycheck.net_salary - total_expenses

    # Get all expense categories for filtering
    categories = ExpenseCategory.query.all()

    # Get accounts for payment modal
    accounts = Account.query.filter_by(user_id=user_id).all()

    # Read the JavaScript for drag and drop functionality
    with open("app/static/js/expenses/drag-drop.js", "r") as js_file:
        drag_drop_js = js_file.read()

    return render_template(
        "expenses/by_paycheck.html",
        paychecks=paychecks,
        expenses=expenses,
        expenses_by_paycheck=expenses_by_paycheck,
        paycheck_totals=paycheck_totals,
        paycheck_remaining=paycheck_remaining,
        categories=categories,
        accounts=accounts,
        start_date=start_date,
        end_date=end_date,
        today=date.today(),
        include_drag_drop_js=drag_drop_js,
    )


@expense_bp.route("/income-expenses-by-paycheck")
@login_required
def income_expenses_by_paycheck():
    """View income and expenses organized by paycheck with running balance"""
    user_id = session.get("user_id")

    # Get date range filter (default to next 60 days if not specified)
    start_date, end_date = get_date_range_filter()
    if not start_date:
        start_date = date.today()
    if not end_date:
        end_date = start_date + timedelta(days=60)

    # Get starting balance filter
    starting_balance = request.args.get("starting_balance", type=float, default=0.0)
    starting_balance = decimal.Decimal(str(starting_balance))

    # Get optional account filter
    account_id = request.args.get("account_id", type=int)

    # Get all paychecks in the date range
    paychecks = (
        Paycheck.query.filter(
            Paycheck.user_id == user_id,
            Paycheck.scheduled_date >= start_date,
            Paycheck.scheduled_date <= end_date,
        )
        .order_by(Paycheck.scheduled_date)
        .all()
    )

    # Get all expenses in the date range
    expense_query = Expense.query.filter(
        Expense.user_id == user_id,
        Expense.scheduled_date >= start_date,
        Expense.scheduled_date <= end_date,
    )

    # Apply account filter if specified
    if account_id:
        # For expenses, filter those that have been paid from the specified account
        paid_expense_ids = (
            db.session.query(ExpensePayment.expense_id)
            .filter(ExpensePayment.account_id == account_id)
            .all()
        )
        paid_expense_ids = [id[0] for id in paid_expense_ids]

        # Filter to only include expenses paid from this account or not yet paid
        expense_query = expense_query.filter(
            db.or_(Expense.id.in_(paid_expense_ids), Expense.paid == False)
        )

        # For paychecks, filter those that deposit to the specified account
        paycheck_ids = (
            db.session.query(IncomePayment.paycheck_id)
            .filter(IncomePayment.account_id == account_id)
            .all()
        )
        paycheck_ids = [id[0] for id in paycheck_ids]

        # Update paychecks to only include those depositing to this account
        paychecks = [p for p in paychecks if p.id in paycheck_ids]

    expenses = expense_query.order_by(Expense.scheduled_date).all()

    # Map expenses to paychecks (each expense goes to the last paycheck before its due date)
    expenses_by_paycheck = {p.id: [] for p in paychecks}

    for expense in expenses:
        # Find the last paycheck that comes before the expense date
        assigned = False
        appropriate_paycheck = None

        # Sort paychecks by date (earliest to latest)
        sorted_paychecks = sorted(paychecks, key=lambda p: p.scheduled_date)

        # Find the last paycheck that comes before or on the expense date
        for i, paycheck in enumerate(sorted_paychecks):
            if paycheck.scheduled_date > expense.scheduled_date:
                # This paycheck is after the expense date
                if i > 0:
                    # Assign to the previous paycheck (the last one before the expense)
                    appropriate_paycheck = sorted_paychecks[i - 1]
                    expenses_by_paycheck[appropriate_paycheck.id].append(expense)
                    assigned = True
                break

        # If we went through all paychecks and none are after the expense date,
        # assign to the last paycheck
        if not assigned and sorted_paychecks:
            appropriate_paycheck = sorted_paychecks[-1]
            expenses_by_paycheck[appropriate_paycheck.id].append(expense)
            assigned = True

        # If there are no paychecks available or the expense is before all paychecks,
        # assign to the first paycheck
        if not assigned and paychecks:
            appropriate_paycheck = sorted_paychecks[0]
            expenses_by_paycheck[appropriate_paycheck.id].append(expense)

    # Calculate totals
    paycheck_totals = {}
    paycheck_remaining = {}

    for paycheck in paychecks:
        total_expenses = sum(
            expense.amount for expense in expenses_by_paycheck[paycheck.id]
        )
        paycheck_totals[paycheck.id] = total_expenses
        paycheck_remaining[paycheck.id] = paycheck.net_salary - total_expenses

    # Get all expense categories for filtering
    categories = ExpenseCategory.query.all()

    # Get accounts for filtering and payment modal
    accounts = Account.query.filter_by(user_id=user_id).all()

    # Dictionary for storing end balances
    end_balances = {}

    # Read the JavaScript for drag and drop functionality if needed
    with open("app/static/js/expenses/drag-drop.js", "r") as js_file:
        drag_drop_js = js_file.read()

    return render_template(
        "expenses/income_expenses_by_paycheck.html",
        paychecks=paychecks,
        expenses=expenses,
        expenses_by_paycheck=expenses_by_paycheck,
        paycheck_totals=paycheck_totals,
        paycheck_remaining=paycheck_remaining,
        categories=categories,
        accounts=accounts,
        start_date=start_date,
        end_date=end_date,
        starting_balance=starting_balance,
        end_balances=end_balances,
        today=date.today(),
        include_drag_drop_js=drag_drop_js,
    )


@expense_bp.route("/assign-to-paycheck", methods=["POST"])
@login_required
def assign_expense_to_paycheck():
    """API endpoint to assign an expense to a specific paycheck"""
    user_id = session.get("user_id")

    # Get expense and paycheck IDs from request
    expense_id = request.json.get("expense_id")
    paycheck_id = request.json.get("paycheck_id")

    if not expense_id or not paycheck_id:
        return (
            jsonify({"success": False, "message": "Missing expense_id or paycheck_id"}),
            400,
        )

    # Verify the expense belongs to the user
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first()
    if not expense:
        return jsonify({"success": False, "message": "Expense not found"}), 404

    # Verify the paycheck belongs to the user
    paycheck = Paycheck.query.filter_by(id=paycheck_id, user_id=user_id).first()
    if not paycheck:
        return jsonify({"success": False, "message": "Paycheck not found"}), 404

    try:
        # Update the expense's associated paycheck without changing the scheduled_date
        expense.paycheck_id = paycheck_id
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": "Expense assigned successfully",
                "expense_date": expense.scheduled_date.isoformat(),
                "original_date": True,  # Flag to tell frontend not to update the display date
            }
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


# Legacy route names for backward compatibility
@expense_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_expense():
    """Legacy route - redirect to manage_expense"""
    return manage_expense()


@expense_bp.route("/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    """Legacy route - redirect to manage_expense"""
    return manage_expense(expense_id)


@expense_bp.route("/recurring/add", methods=["GET", "POST"])
@login_required
def add_recurring_expense():
    """Legacy route - redirect to manage_recurring_expense"""
    return manage_recurring_expense()


@expense_bp.route("/recurring/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def edit_recurring_expense(expense_id):
    """Legacy route - redirect to manage_recurring_expense"""
    return manage_recurring_expense(expense_id)
