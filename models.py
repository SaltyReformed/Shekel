from flask_sqlalchemy import SQLAlchemy
from datetime import date

db = SQLAlchemy()

# ---------------------------
# Lookup / Reference Tables
# ---------------------------


class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(50), unique=True, nullable=False
    )  # e.g., 'VIEW_FINANCES', 'MANAGE_FINANCES', 'ADMIN'
    description = db.Column(db.Text)


class AccountType(db.Model):
    __tablename__ = "account_types"
    id = db.Column(db.Integer, primary_key=True)
    type_name = db.Column(
        db.String(50), nullable=False
    )  # e.g., 'Checking', 'Savings', etc.
    is_debt = db.Column(db.Boolean, nullable=False)


class ScheduleType(db.Model):
    __tablename__ = "schedule_types"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(50), unique=True, nullable=False
    )  # e.g., 'income', 'expense'
    description = db.Column(db.Text)


class Frequency(db.Model):
    __tablename__ = "frequencies"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(50), unique=True, nullable=False
    )  # e.g., 'biweekly', 'monthly', etc.
    description = db.Column(db.Text)


class IncomeCategory(db.Model):
    __tablename__ = "income_categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    description = db.Column(db.Text)


class ExpenseCategory(db.Model):
    __tablename__ = "expense_categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    description = db.Column(db.Text)
    color = db.Column(db.String(7), default="#6c757d")  # Hex color code
    monthly_budget = db.Column(
        db.Numeric(10, 2), nullable=True
    )  # Monthly budget amount
    icon = db.Column(db.String(100), nullable=True)  # Optional SVG path for icon


# ---------------------------
# Core Tables
# ---------------------------


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    email = db.Column(db.String(100))
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"))
    role = db.relationship("Role", backref="users")


class Account(db.Model):
    __tablename__ = "accounts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    account_name = db.Column(db.String(100))
    type_id = db.Column(db.Integer, db.ForeignKey("account_types.id"))
    balance = db.Column(db.Numeric(10, 2), default=0.00)
    user = db.relationship("User", backref="accounts")
    account_type = db.relationship("AccountType", backref="accounts")
    transactions = db.relationship(
        "Transaction", back_populates="account", cascade="all, delete-orphan"
    )


class RecurringSchedule(db.Model):
    __tablename__ = "recurring_schedules"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    type_id = db.Column(db.Integer, db.ForeignKey("schedule_types.id"))
    description = db.Column(db.String(255))
    frequency_id = db.Column(db.Integer, db.ForeignKey("frequencies.id"))
    interval = db.Column(db.Integer, default=1)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)
    amount = db.Column(
        db.Numeric(10, 2), nullable=False
    )  # Base amount for the recurring event
    user = db.relationship("User", backref="recurring_schedules")
    schedule_type = db.relationship("ScheduleType", backref="recurring_schedules")
    frequency = db.relationship("Frequency", backref="recurring_schedules")


class Paycheck(db.Model):
    __tablename__ = "paychecks"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    scheduled_date = db.Column(db.Date, nullable=False)
    gross_salary = db.Column(
        db.Numeric(10, 2), nullable=False
    )  # Gross amount per paycheck
    taxes = db.Column(db.Numeric(10, 2))  # May be NULL for projected paychecks
    deductions = db.Column(db.Numeric(10, 2))  # May be NULL for projected paychecks
    net_salary = db.Column(db.Numeric(10, 2))  # Calculated or provided net amount
    is_projected = db.Column(db.Boolean, nullable=False, default=True)
    category_id = db.Column(db.Integer, db.ForeignKey("income_categories.id"))
    recurring_schedule_id = db.Column(
        db.Integer, db.ForeignKey("recurring_schedules.id")
    )
    paid = db.Column(
        db.Boolean, nullable=False, default=False
    )  # Indicates if the paycheck has been processed
    user = db.relationship("User", backref="paychecks")
    income_category = db.relationship("IncomeCategory", backref="paychecks")
    recurring_schedule = db.relationship("RecurringSchedule", backref="paychecks")


class IncomePayment(db.Model):
    __tablename__ = "income_payments"
    id = db.Column(db.Integer, primary_key=True)
    paycheck_id = db.Column(db.Integer, db.ForeignKey("paychecks.id"), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    payment_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    # New fields for tracking allocation type
    is_percentage = db.Column(db.Boolean, default=False)
    percentage = db.Column(
        db.Numeric(5, 2), nullable=True
    )  # Store percentage if applicable

    paycheck = db.relationship("Paycheck", backref="income_payments")
    account = db.relationship("Account", backref="income_payments")


class SalaryChange(db.Model):
    __tablename__ = "salary_changes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    effective_date = db.Column(
        db.Date, nullable=False
    )  # When the new salary takes effect
    end_date = db.Column(db.Date)  # Optional end date for the salary period
    gross_annual_salary = db.Column(db.Numeric(10, 2), nullable=False)
    user = db.relationship("User", backref="salary_changes")


class SalaryDepositAllocation(db.Model):
    __tablename__ = "salary_deposit_allocations"
    id = db.Column(db.Integer, primary_key=True)
    salary_id = db.Column(
        db.Integer, db.ForeignKey("salary_changes.id"), nullable=False
    )
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    is_percentage = db.Column(db.Boolean, default=True)
    percentage = db.Column(db.Numeric(5, 2), nullable=True)
    amount = db.Column(db.Numeric(10, 2), nullable=True)

    salary = db.relationship("SalaryChange", backref="deposit_allocations")
    account = db.relationship("Account", backref="salary_allocations")


class Expense(db.Model):
    __tablename__ = "expenses"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    scheduled_date = db.Column(db.Date, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("expense_categories.id"))
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.Text)
    paid = db.Column(
        db.Boolean, nullable=False, default=False
    )  # Indicates if the expense has been paid
    recurring_schedule_id = db.Column(
        db.Integer, db.ForeignKey("recurring_schedules.id")
    )
    user = db.relationship("User", backref="expenses")
    expense_category = db.relationship("ExpenseCategory", backref="expenses")
    recurring_schedule = db.relationship("RecurringSchedule", backref="expenses")
    notes = db.Column(db.Text)  # For additional notes about the expense


class ExpenseChange(db.Model):
    __tablename__ = "expense_changes"
    id = db.Column(db.Integer, primary_key=True)
    recurring_schedule_id = db.Column(
        db.Integer, db.ForeignKey("recurring_schedules.id")
    )
    effective_date = db.Column(
        db.Date, nullable=False
    )  # When the new amount takes effect
    end_date = db.Column(db.Date)  # Optional end date for the changed period
    new_amount = db.Column(db.Numeric(10, 2), nullable=False)
    recurring_schedule = db.relationship("RecurringSchedule", backref="expense_changes")


class ExpensePayment(db.Model):
    __tablename__ = "expense_payments"
    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey("expenses.id"), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    payment_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)

    expense = db.relationship("Expense", backref="expense_payments")
    account = db.relationship("Account", backref="expense_payments")


# Transaction model for recording account transactions
class Transaction(db.Model):
    __tablename__ = "transactions"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False, default=date.today)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.String(255))
    transaction_type = db.Column(
        db.String(50)
    )  # 'deposit', 'withdrawal', 'transfer_in', 'transfer_out'

    # For transfers
    related_transaction_id = db.Column(
        db.Integer, db.ForeignKey("transactions.id"), nullable=True
    )

    account = db.relationship("Account", back_populates="transactions")
    related_transaction = db.relationship(
        "Transaction", remote_side=[id], backref="related_transactions"
    )


class AccountInterest(db.Model):
    __tablename__ = "account_interest"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=False, unique=True
    )
    rate = db.Column(
        db.Numeric(5, 2), nullable=False
    )  # Annual interest rate (e.g., 4.00%)
    compound_frequency = db.Column(
        db.String(20), nullable=False, default="monthly"
    )  # daily, monthly, quarterly, annually
    accrual_day = db.Column(
        db.Integer, default=None
    )  # Day of month for accrual (NULL = end of month)
    interest_type = db.Column(db.String(20), default="simple")  # simple or compound
    enabled = db.Column(db.Boolean, default=True)
    last_accrual_date = db.Column(db.Date, default=None)

    account = db.relationship("Account", backref="interest_settings")
