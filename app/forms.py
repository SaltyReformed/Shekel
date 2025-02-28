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
)
from wtforms.validators import DataRequired, Optional, NumberRange, Length
from datetime import date


# Income Management Forms
class SalaryForm(FlaskForm):
    # Salary information
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
