import json
from datetime import date, datetime, timedelta
from calendar import monthrange
from decimal import Decimal
from functools import wraps
from flask import Blueprint, jsonify, render_template, session
from sqlalchemy import func, extract
from models import (
    db,
    User,
    Account,
    AccountType,
    Transaction,
    Expense,
    ExpenseCategory,
    Paycheck,
    RecurringSchedule,
    SalaryChange,
)

# Create a blueprint for dashboard
dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


# Helper function to require login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


def get_month_name(month_number):
    try:
        # Make sure month_number is an integer
        month_int = int(month_number)
        return datetime(2000, month_int, 1).strftime("%b")
    except (ValueError, TypeError):
        return "Unknown"


# Helper function to safely convert Decimal to float
def decimal_to_float(decimal_value):
    if isinstance(decimal_value, Decimal):
        return float(decimal_value)
    return decimal_value


# Helper to convert datetime/date to string
def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


# Dashboard main view
@dashboard_bp.route("/")
@login_required
def dashboard():
    user_id = session.get("user_id")
    user = User.query.get(user_id)

    # Get first name or username for greeting
    display_name = user.first_name if user.first_name else user.username

    return render_template("dashboard.html", display_name=display_name)


# API endpoint for account balances
@dashboard_bp.route("/api/account-balances")
@login_required
def account_balances():
    user_id = session.get("user_id")

    # Get all accounts with their types
    accounts = (
        db.session.query(Account, AccountType)
        .join(AccountType, Account.type_id == AccountType.id)
        .filter(Account.user_id == user_id)
        .all()
    )

    account_data = []
    for account, account_type in accounts:
        account_data.append(
            {
                "id": account.id,
                "name": account.account_name,
                "balance": decimal_to_float(account.balance),
                "type": account_type.type_name,
                "is_debt": account_type.is_debt,
            }
        )

    return jsonify(account_data)


# API endpoint for monthly income & expenses
@dashboard_bp.route("/api/monthly-finances")
@login_required
def monthly_finances():
    user_id = session.get("user_id")

    # Get data for the last 6 months
    end_date = date.today()
    start_date = end_date - timedelta(days=180)

    # Get monthly income from paychecks
    income_data = (
        db.session.query(
            extract("year", Paycheck.scheduled_date).label("year"),
            extract("month", Paycheck.scheduled_date).label("month"),
            func.sum(Paycheck.gross_salary).label("total_income"),
        )
        .filter(
            Paycheck.user_id == user_id,
            Paycheck.scheduled_date >= start_date,
            Paycheck.scheduled_date <= end_date,
        )
        .group_by("year", "month")
        .order_by("year", "month")
        .all()
    )

    # Get monthly expenses
    expense_data = (
        db.session.query(
            extract("year", Expense.scheduled_date).label("year"),
            extract("month", Expense.scheduled_date).label("month"),
            func.sum(Expense.amount).label("total_expenses"),
        )
        .filter(
            Expense.user_id == user_id,
            Expense.scheduled_date >= start_date,
            Expense.scheduled_date <= end_date,
        )
        .group_by("year", "month")
        .order_by("year", "month")
        .all()
    )

    # Process income data
    monthly_income = {}
    for year, month, total in income_data:
        year_month = f"{int(year)}-{int(month):02d}"
        monthly_income[year_month] = decimal_to_float(total)

    # Process expense data
    monthly_expenses = {}
    for year, month, total in expense_data:
        year_month = f"{int(year)}-{int(month):02d}"
        monthly_expenses[year_month] = decimal_to_float(total)

    # Combine the data
    combined_data = []
    all_months = sorted(
        set(list(monthly_income.keys()) + list(monthly_expenses.keys()))
    )

    for year_month in all_months:
        year, month = map(int, year_month.split("-"))
        month_name = get_month_name(month)

        combined_data.append(
            {
                "year_month": year_month,
                "month": month_name,
                "income": monthly_income.get(year_month, 0),
                "expenses": monthly_expenses.get(year_month, 0),
                "balance": monthly_income.get(year_month, 0)
                - monthly_expenses.get(year_month, 0),
            }
        )

    return jsonify(combined_data)


# API endpoint for expense categories
@dashboard_bp.route("/api/expense-categories")
@login_required
def expense_categories():
    user_id = session.get("user_id")
    current_month = datetime.now().month
    current_year = datetime.now().year

    # Get first and last day of current month
    _, last_day = monthrange(current_year, current_month)
    start_of_month = date(current_year, current_month, 1)
    end_of_month = date(current_year, current_month, last_day)

    # Get expenses by category for the current month
    category_expenses = (
        db.session.query(
            ExpenseCategory.id,
            ExpenseCategory.name,
            ExpenseCategory.color,
            func.sum(Expense.amount).label("total"),
        )
        .join(Expense, Expense.category_id == ExpenseCategory.id)
        .filter(
            Expense.user_id == user_id,
            Expense.scheduled_date >= start_of_month,
            Expense.scheduled_date <= end_of_month,
        )
        .group_by(ExpenseCategory.id, ExpenseCategory.name, ExpenseCategory.color)
        .all()
    )

    # Format the data
    categories_data = []
    for id, name, color, total in category_expenses:
        categories_data.append(
            {
                "id": id,
                "name": name,
                "color": color or "#6c757d",  # Default color if none set
                "value": decimal_to_float(total),
            }
        )

    return jsonify(categories_data)


# API endpoint for expense breakdown by category and month
@dashboard_bp.route("/api/expense-breakdown")
@login_required
def expense_breakdown():
    try:
        user_id = session.get("user_id")

        # Get data for the last 6 months
        end_date = date.today()
        start_date = end_date - timedelta(days=180)

        # Get expense categories
        categories = ExpenseCategory.query.all()
        category_map = {c.id: c.name for c in categories}

        # Get expenses by category and month
        expenses_by_month_category = (
            db.session.query(
                extract("year", Expense.scheduled_date).label("year"),
                extract("month", Expense.scheduled_date).label("month"),
                Expense.category_id,
                func.sum(Expense.amount).label("total"),
            )
            .filter(
                Expense.user_id == user_id,
                Expense.scheduled_date >= start_date,
                Expense.scheduled_date <= end_date,
                Expense.category_id != None,
            )
            .group_by(
                extract("year", Expense.scheduled_date),
                extract("month", Expense.scheduled_date),
                Expense.category_id,
            )
            .order_by(
                extract("year", Expense.scheduled_date),
                extract("month", Expense.scheduled_date),
            )
            .all()
        )

        # Format the data for chart display
        breakdown_by_month = {}

        for year, month, category_id, total in expenses_by_month_category:
            # Convert Decimal to int for month and year
            year_int = int(year)
            month_int = int(month)

            year_month = f"{year_int}-{month_int:02d}"
            month_name = get_month_name(month_int)  # Pass as integer

            if year_month not in breakdown_by_month:
                breakdown_by_month[year_month] = {
                    "year_month": year_month,
                    "month": month_name,
                    "expenses": 0,
                }

            category_name = category_map.get(category_id, "Uncategorized")
            safe_category_key = category_name.lower().replace(" ", "_")
            breakdown_by_month[year_month][safe_category_key] = decimal_to_float(total)
            breakdown_by_month[year_month]["expenses"] += decimal_to_float(total)

        # If no data was found, return sample data for the last 6 months
        if not breakdown_by_month:
            # Create sample data for the last 6 months
            for i in range(6):
                month_date = end_date - timedelta(days=30 * i)
                year_month = f"{month_date.year}-{month_date.month:02d}"
                month_name = get_month_name(month_date.month)

                breakdown_by_month[year_month] = {
                    "year_month": year_month,
                    "month": month_name,
                    "expenses": 0,
                    "other": 0,  # Add at least one category so chart renders
                }

        # Convert to list sorted by year and month
        breakdown_data = sorted(
            breakdown_by_month.values(), key=lambda x: x["year_month"]
        )

        return jsonify(breakdown_data)
    except Exception as e:
        # Log the error and return a simple array with the error
        print(f"Error in expense breakdown: {str(e)}")
        return jsonify(
            [{"month": "Error", "expenses": 0, "error_message": str(e), "other": 0}]
        )


# API endpoint for current month's financial summary
@dashboard_bp.route("/api/current-summary")
@login_required
def current_summary():
    user_id = session.get("user_id")
    current_month = datetime.now().month
    current_year = datetime.now().year

    # Get first and last day of current month
    _, last_day = monthrange(current_year, current_month)
    start_of_month = date(current_year, current_month, 1)
    end_of_month = date(current_year, current_month, last_day)

    # Get total income for the month
    month_income = (
        db.session.query(func.sum(Paycheck.gross_salary))
        .filter(
            Paycheck.user_id == user_id,
            Paycheck.scheduled_date >= start_of_month,
            Paycheck.scheduled_date <= end_of_month,
        )
        .scalar()
        or 0
    )

    # Get total expenses for the month
    month_expenses = (
        db.session.query(func.sum(Expense.amount))
        .filter(
            Expense.user_id == user_id,
            Expense.scheduled_date >= start_of_month,
            Expense.scheduled_date <= end_of_month,
        )
        .scalar()
        or 0
    )

    # Calculate total balances
    accounts = Account.query.filter_by(user_id=user_id).all()
    total_balance = sum(account.balance for account in accounts)

    # Get assets and debts
    assets = []
    debts = []

    for account in accounts:
        account_type = AccountType.query.get(account.type_id)
        if account_type:
            if account_type.is_debt:
                debts.append(
                    {
                        "id": account.id,
                        "name": account.account_name,
                        "balance": decimal_to_float(account.balance),
                    }
                )
            else:
                assets.append(
                    {
                        "id": account.id,
                        "name": account.account_name,
                        "balance": decimal_to_float(account.balance),
                    }
                )

    # Calculate net worth
    assets_total = sum(a["balance"] for a in assets)
    debts_total = sum(d["balance"] for d in debts)
    net_worth = assets_total - debts_total

    # Calculate savings rate if income > 0
    savings = month_income - month_expenses
    savings_rate = (savings / month_income * 100) if month_income > 0 else 0

    summary = {
        "total_balance": decimal_to_float(total_balance),
        "month_income": decimal_to_float(month_income),
        "month_expenses": decimal_to_float(month_expenses),
        "savings": decimal_to_float(savings),
        "savings_rate": round(savings_rate, 1),
        "assets_total": decimal_to_float(assets_total),
        "debts_total": decimal_to_float(debts_total),
        "net_worth": decimal_to_float(net_worth),
    }

    return jsonify(summary)
