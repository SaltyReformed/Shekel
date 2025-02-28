from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
)
from app.forms import SalaryForm, OneTimeIncomeForm
from models import (
    db,
    User,
    SalaryChange,
    Paycheck,
    RecurringSchedule,
    ScheduleType,
    Frequency,
    IncomeCategory,
    Account,
    IncomePayment,
)
from datetime import datetime, date, timedelta
from sqlalchemy import func
import decimal
from functools import wraps

income_bp = Blueprint("income", __name__, url_prefix="/income")


# Helper function to require login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


# Utility function to calculate paycheck details from annual salary
def calculate_paycheck_from_annual(annual_salary, frequency, tax_rates):
    # Convert annual salary to per-paycheck amount based on frequency
    pay_periods = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}

    periods = pay_periods.get(frequency, 26)  # Default to biweekly if not specified
    gross_per_period = decimal.Decimal(annual_salary) / periods

    # Calculate taxes and deductions
    federal_tax = gross_per_period * (
        decimal.Decimal(tax_rates.get("federal", 22)) / 100
    )
    state_tax = gross_per_period * (decimal.Decimal(tax_rates.get("state", 5)) / 100)
    retirement = gross_per_period * (
        decimal.Decimal(tax_rates.get("retirement", 5)) / 100
    )
    health_insurance = decimal.Decimal(tax_rates.get("health", 100))
    other_deductions = decimal.Decimal(tax_rates.get("other", 0))

    # Calculate net pay
    taxes = federal_tax + state_tax
    deductions = retirement + health_insurance + other_deductions
    net_pay = gross_per_period - taxes - deductions

    return {
        "gross_salary": gross_per_period.quantize(decimal.Decimal("0.01")),
        "federal_tax": federal_tax.quantize(decimal.Decimal("0.01")),
        "state_tax": state_tax.quantize(decimal.Decimal("0.01")),
        "total_tax": taxes.quantize(decimal.Decimal("0.01")),
        "retirement": retirement.quantize(decimal.Decimal("0.01")),
        "health_insurance": health_insurance.quantize(decimal.Decimal("0.01")),
        "other_deductions": other_deductions.quantize(decimal.Decimal("0.01")),
        "total_deductions": deductions.quantize(decimal.Decimal("0.01")),
        "net_pay": net_pay.quantize(decimal.Decimal("0.01")),
    }


# Route to view income overview
@income_bp.route("/")
@login_required
def overview():
    user_id = session.get("user_id")

    # Get current and historical salary information
    salary_history = (
        SalaryChange.query.filter_by(user_id=user_id)
        .order_by(SalaryChange.effective_date.desc())
        .all()
    )

    # Get recent paychecks
    recent_paychecks = (
        Paycheck.query.filter_by(user_id=user_id)
        .order_by(Paycheck.scheduled_date.desc())
        .limit(5)
        .all()
    )

    # Get one-time income (using RecurringSchedule where interval is None or 0)
    onetime_income = (
        db.session.query(RecurringSchedule, Paycheck)
        .join(Paycheck, Paycheck.recurring_schedule_id == RecurringSchedule.id)
        .join(ScheduleType, ScheduleType.id == RecurringSchedule.type_id)
        .filter(ScheduleType.name == "income")
        .filter(RecurringSchedule.user_id == user_id)
        .filter(RecurringSchedule.interval == None)
        .order_by(Paycheck.scheduled_date.desc())
        .limit(5)
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

    year_income = (
        db.session.query(func.sum(Paycheck.gross_salary))
        .filter(
            Paycheck.user_id == user_id,
            Paycheck.scheduled_date >= date(current_year, 1, 1),
            Paycheck.scheduled_date <= date(current_year, 12, 31),
        )
        .scalar()
        or 0
    )

    return render_template(
        "income/overview.html",
        salary_history=salary_history,
        recent_paychecks=recent_paychecks,
        onetime_income=onetime_income,
        month_income=month_income,
        year_income=year_income,
    )


# Route to add or edit salary
@income_bp.route("/salary", methods=["GET", "POST"])
@login_required
def manage_salary():
    form = SalaryForm()
    user_id = session.get("user_id")

    # If it's a GET request with an ID parameter, we're editing an existing salary
    salary_id = request.args.get("id")
    if request.method == "GET" and salary_id:
        salary = SalaryChange.query.filter_by(id=salary_id, user_id=user_id).first()
        if salary:
            form.gross_annual_salary.data = salary.gross_annual_salary
            form.effective_date.data = salary.effective_date
            form.end_date.data = salary.end_date
            form.notes.data = salary.notes if hasattr(salary, "notes") else ""

    if form.validate_on_submit():
        if salary_id:
            # Update existing salary
            salary = SalaryChange.query.filter_by(id=salary_id, user_id=user_id).first()
            if not salary:
                flash("Salary record not found.", "danger")
                return redirect(url_for("income.overview"))
        else:
            # Create new salary record
            salary = SalaryChange(user_id=user_id)

        # Set the salary data
        if form.salary_type.data == "annual":
            # Direct annual salary entry
            salary.gross_annual_salary = form.gross_annual_salary.data
        else:
            # Calculate annual from net paycheck
            net_paycheck = form.net_paycheck_amount.data
            frequency = form.pay_frequency.data
            pay_periods = {
                "weekly": 52,
                "biweekly": 26,
                "semimonthly": 24,
                "monthly": 12,
            }
            periods = pay_periods.get(frequency, 26)

            # Estimate approximate gross annual from net paycheck
            # This is a simplification - a real implementation would need more complex logic
            # to reverse-calculate the gross from net
            tax_deduction_factor = (
                1
                + (
                    form.federal_tax_rate.data
                    + form.state_tax_rate.data
                    + form.retirement_contribution_rate.data
                )
                / 100
            )
            health_other_annual = (
                form.health_insurance_amount.data + form.other_deductions_amount.data
            ) * periods

            estimated_annual = (
                net_paycheck * periods * tax_deduction_factor
            ) + health_other_annual
            salary.gross_annual_salary = estimated_annual

        salary.effective_date = form.effective_date.data
        salary.end_date = form.end_date.data

        db.session.add(salary)
        db.session.commit()

        # If we need to generate paychecks immediately:
        if "generate_paychecks" in request.form:
            # Get or create appropriate frequency record
            frequency = Frequency.query.filter_by(name=form.pay_frequency.data).first()
            if not frequency:
                frequency = Frequency(
                    name=form.pay_frequency.data,
                    description=f"{form.pay_frequency.data} payments",
                )
                db.session.add(frequency)
                db.session.commit()

            # Get income schedule type
            schedule_type = ScheduleType.query.filter_by(name="income").first()
            if not schedule_type:
                schedule_type = ScheduleType(
                    name="income", description="Regular income"
                )
                db.session.add(schedule_type)
                db.session.commit()

            # Create recurring schedule for the salary
            schedule = RecurringSchedule(
                user_id=user_id,
                type_id=schedule_type.id,
                description=f"Salary - {salary.gross_annual_salary}/year",
                frequency_id=frequency.id,
                interval=1,  # 1 payment per frequency (e.g., every 2 weeks for biweekly)
                start_date=salary.effective_date,
                end_date=salary.end_date,
                amount=salary.gross_annual_salary
                / (
                    26 if form.pay_frequency.data == "biweekly" else 12
                ),  # Default to biweekly or monthly
            )
            db.session.add(schedule)
            db.session.commit()

            flash(
                "Salary updated and paycheck schedule created. You can now edit individual paychecks.",
                "success",
            )
        else:
            flash("Salary information updated successfully.", "success")

        return redirect(url_for("income.overview"))

    # Get current and previous salaries for reference
    salary_history = (
        SalaryChange.query.filter_by(user_id=user_id)
        .order_by(SalaryChange.effective_date.desc())
        .all()
    )

    return render_template(
        "income/manage_salary.html",
        form=form,
        salary_history=salary_history,
        editing=bool(salary_id),
    )


# AJAX route to calculate paycheck details
@income_bp.route("/calculate-paycheck", methods=["POST"])
@login_required
def calculate_paycheck():
    data = request.json

    if data.get("salary_type") == "annual":
        annual_salary = data.get("gross_annual_salary", 0)
        frequency = data.get("pay_frequency", "biweekly")

        tax_rates = {
            "federal": data.get("federal_tax_rate", 22),
            "state": data.get("state_tax_rate", 5),
            "retirement": data.get("retirement_contribution_rate", 5),
            "health": data.get("health_insurance_amount", 100),
            "other": data.get("other_deductions_amount", 0),
        }

        result = calculate_paycheck_from_annual(annual_salary, frequency, tax_rates)
        return jsonify(result)

    return jsonify({"error": "Invalid calculation type"})


# Route to add one-time income
@income_bp.route("/one-time", methods=["GET", "POST"])
@login_required
def one_time_income():
    form = OneTimeIncomeForm()
    user_id = session.get("user_id")

    # Populate category dropdown
    categories = IncomeCategory.query.all()
    form.category_id.choices = [(c.id, c.name) for c in categories]

    # Add a blank option at the beginning
    form.category_id.choices.insert(0, (0, "-- Select Category --"))

    # Populate account dropdown
    accounts = Account.query.filter_by(user_id=user_id).all()
    form.account_id.choices = [(a.id, a.account_name) for a in accounts]

    if form.validate_on_submit():
        # Get income schedule type
        schedule_type = ScheduleType.query.filter_by(name="income").first()
        if not schedule_type:
            schedule_type = ScheduleType(name="income", description="Income")
            db.session.add(schedule_type)
            db.session.commit()

        # Create one-time income as a schedule with no interval
        schedule = RecurringSchedule(
            user_id=user_id,
            type_id=schedule_type.id,
            description=form.description.data,
            frequency_id=None,  # No frequency for one-time income
            interval=None,  # No interval for one-time income
            start_date=form.income_date.data,
            end_date=form.income_date.data,  # Same start and end date for one-time
            amount=form.amount.data,
        )
        db.session.add(schedule)
        db.session.commit()

        # Create the paycheck record
        paycheck = Paycheck(
            user_id=user_id,
            scheduled_date=form.income_date.data,
            gross_salary=form.amount.data,
            taxes=(
                form.amount.data * decimal.Decimal(0.30) if form.is_taxable.data else 0
            ),  # Rough tax estimate if taxable
            deductions=0,  # No deductions for one-time income
            net_salary=(
                form.amount.data * decimal.Decimal(0.70)
                if form.is_taxable.data
                else form.amount.data
            ),
            is_projected=False,  # It's a real, one-time income
            category_id=form.category_id.data if form.category_id.data != 0 else None,
            recurring_schedule_id=schedule.id,
            paid=True,  # Assume it's already paid
        )
        db.session.add(paycheck)
        db.session.commit()

        # Record the payment to the account
        income_payment = IncomePayment(
            paycheck_id=paycheck.id,
            account_id=form.account_id.data,
            payment_date=form.income_date.data,
            amount=paycheck.net_salary,
        )
        db.session.add(income_payment)

        # Update account balance
        account = Account.query.get(form.account_id.data)
        account.balance += paycheck.net_salary

        db.session.commit()

        flash("One-time income recorded successfully.", "success")
        return redirect(url_for("income.overview"))

    return render_template("income/one_time_income.html", form=form)


# Route to manage paychecks
@income_bp.route("/paychecks", methods=["GET"])
@login_required
def manage_paychecks():
    user_id = session.get("user_id")

    # Get all paychecks, filter by date range if provided
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    query = Paycheck.query.filter_by(user_id=user_id)

    if start_date:
        query = query.filter(
            Paycheck.scheduled_date >= datetime.strptime(start_date, "%Y-%m-%d").date()
        )

    if end_date:
        query = query.filter(
            Paycheck.scheduled_date <= datetime.strptime(end_date, "%Y-%m-%d").date()
        )

    paychecks = query.order_by(Paycheck.scheduled_date.desc()).all()

    # Group by month for easier display
    paychecks_by_month = {}
    for paycheck in paychecks:
        month_key = paycheck.scheduled_date.strftime("%Y-%m")
        if month_key not in paychecks_by_month:
            paychecks_by_month[month_key] = {
                "month_name": paycheck.scheduled_date.strftime("%B %Y"),
                "paychecks": [],
            }
        paychecks_by_month[month_key]["paychecks"].append(paycheck)

    # Sort months in reverse order (newest first)
    sorted_months = sorted(paychecks_by_month.keys(), reverse=True)

    return render_template(
        "income/paychecks.html",
        paychecks_by_month=paychecks_by_month,
        sorted_months=sorted_months,
    )


# Route to edit a specific paycheck
@income_bp.route("/paychecks/<int:paycheck_id>", methods=["GET", "POST"])
@login_required
def edit_paycheck(paycheck_id):
    user_id = session.get("user_id")
    paycheck = Paycheck.query.filter_by(id=paycheck_id, user_id=user_id).first_or_404()

    # Simple form for this route, could reuse SalaryForm with modifications
    if request.method == "POST":
        # Update paycheck
        paycheck.gross_salary = decimal.Decimal(request.form.get("gross_salary", 0))
        paycheck.taxes = decimal.Decimal(request.form.get("taxes", 0))
        paycheck.deductions = decimal.Decimal(request.form.get("deductions", 0))
        paycheck.net_salary = (
            paycheck.gross_salary - paycheck.taxes - paycheck.deductions
        )
        paycheck.scheduled_date = datetime.strptime(
            request.form.get("scheduled_date"), "%Y-%m-%d"
        ).date()
        paycheck.paid = "paid" in request.form

        # If changing to paid, create a payment record
        if paycheck.paid and not paycheck.income_payments:
            account_id = request.form.get("account_id")
            if account_id:
                income_payment = IncomePayment(
                    paycheck_id=paycheck.id,
                    account_id=account_id,
                    payment_date=date.today(),
                    amount=paycheck.net_salary,
                )
                db.session.add(income_payment)

                # Update account balance
                account = Account.query.get(account_id)
                account.balance += paycheck.net_salary

        db.session.commit()
        flash("Paycheck updated successfully.", "success")
        return redirect(url_for("income.manage_paychecks"))

    # Get accounts for dropdown
    accounts = Account.query.filter_by(user_id=user_id).all()

    return render_template(
        "income/edit_paycheck.html", paycheck=paycheck, accounts=accounts
    )
