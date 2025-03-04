from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    DecimalField,
    DateField,
    SelectField,
    RadioField,
    BooleanField,
    TextAreaField,
    IntegerField,
    FieldList,
    FormField,
    HiddenField,
)
from wtforms.validators import DataRequired, Optional, NumberRange, Length
from datetime import date


# Income Management Forms
class DepositAllocationForm(FlaskForm):
    """Form for a single deposit allocation"""

    allocation_type = RadioField(
        "Allocation Type",
        choices=[("percentage", "Percentage"), ("amount", "Fixed Amount")],
        default="percentage",
    )
    account_id = SelectField("Account", coerce=int, validators=[DataRequired()])
    percentage = DecimalField(
        "Percentage",
        validators=[Optional(), NumberRange(min=0, max=100)],
        default=100.0,
    )
    amount = DecimalField(
        "Amount", validators=[Optional(), NumberRange(min=0)], default=0.0
    )
    # Used to track existing records when editing
    payment_id = HiddenField()

    class Meta:
        # Don't use CSRF for this nested form
        csrf = False


class SalaryForm(FlaskForm):
    salary_type = RadioField(
        "Salary Type",
        choices=[("annual", "Annual Salary"), ("net_paycheck", "Net Paycheck Amount")],
        default="annual",
        validators=[DataRequired()],
    )

    # Annual salary fields
    gross_annual_salary = DecimalField(
        "Gross Annual Salary", validators=[Optional(), NumberRange(min=0)], places=2
    )

    # Paycheck fields
    pay_frequency = SelectField(
        "Pay Frequency",
        choices=[
            ("weekly", "Weekly"),
            ("biweekly", "Biweekly"),
            ("semimonthly", "Twice Monthly"),
            ("monthly", "Monthly"),
        ],
        default="biweekly",
    )

    net_paycheck_amount = DecimalField(
        "Net Paycheck Amount", validators=[Optional(), NumberRange(min=0)], places=2
    )

    # Tax and deduction rates
    federal_tax_rate = DecimalField(
        "Federal Tax Rate (%)",
        default=22.0,
        validators=[Optional(), NumberRange(min=0, max=100)],
        places=2,
    )
    state_tax_rate = DecimalField(
        "State Tax Rate (%)",
        default=5.0,
        validators=[Optional(), NumberRange(min=0, max=100)],
        places=2,
    )
    retirement_contribution_rate = DecimalField(
        "Retirement Contribution (%)",
        default=5.0,
        validators=[Optional(), NumberRange(min=0, max=100)],
        places=2,
    )
    health_insurance_amount = DecimalField(
        "Health Insurance per Paycheck",
        default=100.0,
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    other_deductions_amount = DecimalField(
        "Other Deductions per Paycheck",
        default=0.0,
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )

    # Deposit allocation
    deposit_allocations = FieldList(FormField(DepositAllocationForm), min_entries=1)

    # Date range
    effective_date = DateField(
        "Effective Date", default=date.today, validators=[DataRequired()]
    )
    end_date = DateField("End Date", validators=[Optional()])

    # Notes
    notes = StringField("Notes")


class OneTimeIncomeForm(FlaskForm):
    description = StringField("Description", validators=[DataRequired()])
    amount = DecimalField(
        "Amount", validators=[DataRequired(), NumberRange(min=0)], places=2
    )
    income_date = DateField("Date", default=date.today, validators=[DataRequired()])
    category_id = SelectField("Category", coerce=int)
    account_id = SelectField(
        "Deposit to Account", coerce=int, validators=[DataRequired()]
    )
    is_taxable = BooleanField("Taxable Income", default=True)
    notes = StringField("Notes")


# Configuration Management Forms
class IncomeCategoryForm(FlaskForm):
    name = StringField("Category Name", validators=[DataRequired(), Length(max=50)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=200)])
    color = StringField("Category Color", default="#0a6901")
    icon = StringField("Icon SVG Path", validators=[Optional(), Length(max=100)])


class FrequencyForm(FlaskForm):
    name = StringField("Frequency Name", validators=[DataRequired(), Length(max=50)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=200)])


class RecurringScheduleForm(FlaskForm):
    description = StringField(
        "Description", validators=[DataRequired(), Length(max=255)]
    )
    frequency_id = SelectField("Frequency", coerce=int, validators=[DataRequired()])
    interval = IntegerField("Interval", default=1, validators=[NumberRange(min=1)])
    start_date = DateField(
        "Start Date", default=date.today, validators=[DataRequired()]
    )
    end_date = DateField("End Date", validators=[Optional()])
    amount = StringField("Amount", validators=[DataRequired()])
    type_id = SelectField("Type", coerce=int, validators=[DataRequired()])


class ScheduleTypeForm(FlaskForm):
    name = StringField("Type Name", validators=[DataRequired(), Length(max=50)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=200)])


class PaycheckDepositForm(FlaskForm):
    """Form for managing paycheck deposits"""

    allocations = FieldList(FormField(DepositAllocationForm), min_entries=1)

    def validate_allocations(form, field):
        """Validate that percentages sum to 100% if using percentage allocation"""
        percentage_sum = 0
        for allocation in field.data:
            if allocation["allocation_type"] == "percentage":
                percentage_sum += float(allocation["percentage"] or 0)

        if percentage_sum != 0 and abs(percentage_sum - 100.0) > 0.01:
            raise ValueError("Percentage allocations must sum to 100%")


# Expense Management Forms


class ExpenseCategoryForm(FlaskForm):
    """Form for managing expense categories"""

    name = StringField("Category Name", validators=[DataRequired(), Length(max=50)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=200)])


class OneTimeExpenseForm(FlaskForm):
    """Form for adding a one-time expense"""

    description = StringField("Description", validators=[DataRequired()])
    amount = DecimalField(
        "Amount", validators=[DataRequired(), NumberRange(min=0)], places=2
    )
    expense_date = DateField("Date", default=date.today, validators=[DataRequired()])
    category_id = SelectField("Category", coerce=int)
    account_id = SelectField("Pay from Account", coerce=int, validators=[Optional()])
    is_paid = BooleanField("Mark as Paid", default=False)
    notes = StringField("Notes")


class RecurringExpenseForm(FlaskForm):
    """Form for setting up recurring expenses"""

    description = StringField(
        "Description", validators=[DataRequired(), Length(max=255)]
    )
    amount = DecimalField(
        "Amount", validators=[DataRequired(), NumberRange(min=0)], places=2
    )
    frequency_id = SelectField("Frequency", coerce=int, validators=[DataRequired()])
    interval = IntegerField("Interval", default=1, validators=[NumberRange(min=1)])
    start_date = DateField(
        "Start Date", default=date.today, validators=[DataRequired()]
    )
    end_date = DateField("End Date", validators=[Optional()])
    category_id = SelectField("Category", coerce=int)
    account_id = SelectField(
        "Default Payment Account", coerce=int, validators=[Optional()]
    )
    auto_pay = BooleanField("Automatically mark as paid when due", default=False)
    notes = StringField("Notes")


class ExpensePaymentForm(FlaskForm):
    """Form for marking an expense as paid"""

    account_id = SelectField(
        "Pay from Account", coerce=int, validators=[DataRequired()]
    )
    payment_date = DateField(
        "Payment Date", default=date.today, validators=[DataRequired()]
    )
    payment_amount = DecimalField(
        "Payment Amount", validators=[DataRequired(), NumberRange(min=0)], places=2
    )
    notes = StringField("Payment Notes")


class ExpenseFilterForm(FlaskForm):
    """Form for filtering expenses on the overview page"""

    category_id = SelectField("Category", coerce=int, validators=[Optional()])
    start_date = DateField("From Date", validators=[Optional()])
    end_date = DateField("To Date", validators=[Optional()])
    status = SelectField(
        "Status",
        choices=[("", "All Statuses"), ("paid", "Paid"), ("unpaid", "Unpaid")],
        validators=[Optional()],
    )
    recurring_only = BooleanField("Show only recurring expenses", default=False)
