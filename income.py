import decimal
from datetime import date, datetime, timedelta
from functools import wraps

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

from app.forms import OneTimeIncomeForm, SalaryForm
from models import (
    Account,
    Frequency,
    IncomeCategory,
    IncomePayment,
    Paycheck,
    RecurringSchedule,
    SalaryChange,
    SalaryDepositAllocation,
    ScheduleType,
    User,
    db,
)

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

    # Get user accounts for deposit allocation dropdowns
    accounts = Account.query.filter_by(user_id=user_id).all()

    # Initialize the deposit allocation form with user's accounts
    for allocation_form in form.deposit_allocations:
        allocation_form.account_id.choices = [(a.id, a.account_name) for a in accounts]
        # Add a blank option
        allocation_form.account_id.choices.insert(0, (0, "-- Select Account --"))

    # If it's a GET request with an ID parameter, we're editing an existing salary
    salary_id = request.args.get("id")
    if request.method == "GET" and salary_id:
        salary = SalaryChange.query.filter_by(id=salary_id, user_id=user_id).first()
        if salary:
            form.gross_annual_salary.data = salary.gross_annual_salary
            form.effective_date.data = salary.effective_date
            form.end_date.data = salary.end_date
            form.notes.data = salary.notes if hasattr(salary, "notes") else ""

            # Load existing deposit allocations
            salary_allocations = SalaryDepositAllocation.query.filter_by(
                salary_id=salary.id
            ).all()

            # If we have existing allocations, we need to make sure we have enough form entries
            if salary_allocations:
                # Add more entries to the form if needed
                while len(form.deposit_allocations) < len(salary_allocations):
                    form.deposit_allocations.append_entry()

                # Update each form entry with allocation data
                for i, allocation in enumerate(salary_allocations):
                    form.deposit_allocations[i].account_id.data = allocation.account_id
                    form.deposit_allocations[i].allocation_type.data = (
                        "percentage" if allocation.is_percentage else "amount"
                    )
                    if allocation.is_percentage:
                        form.deposit_allocations[i].percentage.data = (
                            allocation.percentage
                        )
                    else:
                        form.deposit_allocations[i].amount.data = allocation.amount

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
            if form.gross_annual_salary.data is None:
                flash("Annual salary amount is required.", "danger")
                return render_template(
                    "income/manage_salary.html",
                    form=form,
                    salary_history=SalaryChange.query.filter_by(user_id=user_id)
                    .order_by(SalaryChange.effective_date.desc())
                    .all(),
                    accounts=accounts,
                    editing=bool(salary_id),
                )
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
        if hasattr(form, "notes"):  # Make sure notes field exists
            salary.notes = form.notes.data

        db.session.add(salary)
        db.session.commit()

        # Process deposit allocations
        # First, delete any existing allocations
        if salary_id:
            SalaryDepositAllocation.query.filter_by(salary_id=salary.id).delete()
            db.session.commit()

        # Process deposit allocations from the form data
        allocation_index = 0
        while f"deposit_allocations-{allocation_index}-account_id" in request.form:
            account_id = request.form.get(
                f"deposit_allocations-{allocation_index}-account_id"
            )
            allocation_type = request.form.get(
                f"deposit_allocations-{allocation_index}-allocation_type"
            )

            if account_id and int(account_id) > 0:
                if allocation_type == "percentage":
                    percentage = request.form.get(
                        f"deposit_allocations-{allocation_index}-percentage"
                    )
                    allocation = SalaryDepositAllocation(
                        salary_id=salary.id,
                        account_id=int(account_id),
                        is_percentage=True,
                        percentage=decimal.Decimal(percentage),
                        amount=None,
                    )
                else:
                    amount = request.form.get(
                        f"deposit_allocations-{allocation_index}-amount"
                    )
                    allocation = SalaryDepositAllocation(
                        salary_id=salary.id,
                        account_id=int(account_id),
                        is_percentage=False,
                        percentage=None,
                        amount=decimal.Decimal(amount),
                    )
                db.session.add(allocation)

            allocation_index += 1

        db.session.commit()

        # If we need to generate paychecks immediately:
        if "generate_paychecks" in request.form:
            # Redirect to the generate paychecks function
            return redirect(
                url_for("income.generate_paychecks_from_salary", salary_id=salary.id)
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

    # Get existing allocations for the template
    salary_allocations = []
    if salary_id:
        salary_allocations = SalaryDepositAllocation.query.filter_by(
            salary_id=salary_id
        ).all()

    return render_template(
        "income/manage_salary.html",
        form=form,
        salary_history=salary_history,
        accounts=accounts,
        editing=bool(salary_id),
        salary_allocations=salary_allocations,
    )


# Improved AJAX route to calculate paycheck details
@income_bp.route("/calculate-paycheck", methods=["POST"])
@login_required
def calculate_paycheck():
    """Calculate paycheck details based on provided salary information"""
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
    elif data.get("salary_type") == "net_paycheck":
        # Implementation for reverse calculation (from net to gross)
        net_paycheck = data.get("net_paycheck_amount", 0)
        frequency = data.get("pay_frequency", "biweekly")

        tax_rates = {
            "federal": data.get("federal_tax_rate", 22),
            "state": data.get("state_tax_rate", 5),
            "retirement": data.get("retirement_contribution_rate", 5),
            "health": data.get("health_insurance_amount", 100),
            "other": data.get("other_deductions_amount", 0),
        }

        # Calculate approximate gross from net
        pay_periods = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
        periods = pay_periods.get(frequency, 26)

        # Estimate tax and deduction rates
        tax_rate = (tax_rates["federal"] + tax_rates["state"]) / 100
        deduction_rate = tax_rates["retirement"] / 100
        fixed_deductions = tax_rates["health"] + tax_rates["other"]

        # Calculate gross (simplified approximation)
        gross_per_period = (
            decimal.Decimal(net_paycheck) + decimal.Decimal(fixed_deductions)
        ) / (1 - tax_rate - deduction_rate)
        annual_salary = gross_per_period * periods

        # Re-calculate details using our normal function to get consistent values
        result = calculate_paycheck_from_annual(annual_salary, frequency, tax_rates)

        # Add estimated annual salary to result
        result["estimated_annual"] = annual_salary.quantize(decimal.Decimal("0.01"))

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

    # Get all accounts for the dropdown
    accounts = Account.query.filter_by(user_id=user_id).all()

    # Import the deposit form (which uses the DepositAllocationForm)
    from app.forms import PaycheckDepositForm

    deposit_form = PaycheckDepositForm()

    # Set account choices for each allocation entry
    for alloc in deposit_form.allocations:
        alloc.account_id.choices = [(a.id, a.account_name) for a in accounts]

    if request.method == "GET":
        if paycheck.income_payments:
            # Ensure there are enough entries in the FieldList
            while len(deposit_form.allocations) < len(paycheck.income_payments):
                deposit_form.allocations.append_entry()
            for i, payment in enumerate(paycheck.income_payments):
                form = deposit_form.allocations[i]
                form.account_id.choices = [(a.id, a.account_name) for a in accounts]
                form.account_id.data = payment.account_id
                form.payment_id.data = payment.id
                if payment.is_percentage:
                    form.allocation_type.data = "percentage"
                    form.percentage.data = payment.percentage
                else:
                    form.allocation_type.data = "amount"
                    form.amount.data = payment.amount
        else:
            # No existing allocations – default to 100% allocation to the first account.
            deposit_form.allocations[0].allocation_type.data = "percentage"
            deposit_form.allocations[0].percentage.data = 100.0
            if accounts:
                deposit_form.allocations[0].account_id.data = accounts[0].id

        return render_template(
            "income/edit_paycheck.html", paycheck=paycheck, deposit_form=deposit_form
        )

    if request.method == "POST":
        # Update basic paycheck fields
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

        # Always process deposit allocations (regardless of whether paycheck.paid is True)
        allocation_data = []
        index = 0
        while f"allocations-{index}-account_id" in request.form:
            allocation_data.append(
                {
                    "account_id": int(
                        request.form.get(f"allocations-{index}-account_id")
                    ),
                    "allocation_type": request.form.get(
                        f"allocations-{index}-allocation_type"
                    ),
                    "percentage": request.form.get(f"allocations-{index}-percentage"),
                    "amount": request.form.get(f"allocations-{index}-amount"),
                    "payment_id": request.form.get(f"allocations-{index}-payment_id"),
                }
            )
            index += 1

        # Remove any existing income payments
        for payment in paycheck.income_payments:
            db.session.delete(payment)
        db.session.flush()

        # Create new income payment records from the submitted allocation data
        for alloc in allocation_data:
            account_id = alloc["account_id"]
            if alloc["allocation_type"] == "percentage":
                percentage = decimal.Decimal(alloc["percentage"] or 0)
                amount = (paycheck.net_salary * percentage / 100).quantize(
                    decimal.Decimal("0.01")
                )
                is_percentage = True
            else:
                amount = decimal.Decimal(alloc["amount"] or 0).quantize(
                    decimal.Decimal("0.01")
                )
                percentage = None
                is_percentage = False
            new_payment = IncomePayment(
                paycheck_id=paycheck.id,
                account_id=account_id,
                payment_date=paycheck.scheduled_date,
                amount=amount,
                is_percentage=is_percentage,
                percentage=percentage,
            )
            db.session.add(new_payment)

        db.session.commit()
        flash("Paycheck updated successfully with deposit allocations.", "success")
        return redirect(url_for("income.manage_paychecks"))


@income_bp.route("/salary/delete/<int:salary_id>", methods=["POST"])
@login_required
def delete_salary(salary_id):
    """Delete a salary record with option to delete associated paychecks"""
    user_id = session.get("user_id")
    salary = SalaryChange.query.filter_by(id=salary_id, user_id=user_id).first_or_404()

    # Check if this salary has generated any paychecks via recurring schedules
    associated_schedules = RecurringSchedule.query.filter_by(
        user_id=user_id, description=f"Salary - ${salary.gross_annual_salary:,.2f}/year"
    ).all()

    # Get the schedule IDs
    schedule_ids = [schedule.id for schedule in associated_schedules]

    # Find paychecks associated with these schedules
    associated_paychecks = Paycheck.query.filter(
        Paycheck.user_id == user_id,
        Paycheck.recurring_schedule_id.in_(schedule_ids) if schedule_ids else False,
    ).all()

    # Check if we should delete associated paychecks
    delete_paychecks = request.form.get("delete_paychecks") == "1"

    try:
        # Save the details for flash message
        effective_date = salary.effective_date.strftime("%b %d, %Y")
        annual_amount = salary.gross_annual_salary

        # If we should delete associated paychecks
        paycheck_count = 0
        if delete_paychecks and associated_paychecks:
            for paycheck in associated_paychecks:
                # If there are any income_payments associated with this paycheck, delete them first
                if paycheck.income_payments:
                    for payment in paycheck.income_payments:
                        # If the payment updated an account balance, reverse that update
                        if payment.account:
                            payment.account.balance -= payment.amount
                        db.session.delete(payment)

                # Delete the paycheck
                db.session.delete(paycheck)
                paycheck_count += 1

            # Delete the associated schedules
            for schedule in associated_schedules:
                db.session.delete(schedule)

        # Delete the salary record
        db.session.delete(salary)
        db.session.commit()

        if delete_paychecks and paycheck_count > 0:
            flash(
                f"Salary record (${annual_amount:,.2f} from {effective_date}) and {paycheck_count} associated paychecks were deleted successfully.",
                "success",
            )
        else:
            flash(
                f"Salary record (${annual_amount:,.2f} from {effective_date}) deleted successfully.",
                "success",
            )
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting salary record: {str(e)}", "danger")

    return redirect(url_for("income.overview"))


@income_bp.route("/generate-paychecks/<int:salary_id>", methods=["POST"])
@login_required
def generate_paychecks_from_salary(salary_id):
    user_id = session.get("user_id")
    salary = SalaryChange.query.filter_by(id=salary_id, user_id=user_id).first_or_404()

    # Get frequency from form
    frequency_name = request.form.get("frequency", "biweekly")

    # Find or create appropriate frequency record
    frequency = Frequency.query.filter_by(name=frequency_name).first()
    if not frequency:
        frequency = Frequency(
            name=frequency_name, description=f"{frequency_name} payments"
        )
        db.session.add(frequency)
        db.session.commit()

    # Get income schedule type
    schedule_type = ScheduleType.query.filter_by(name="income").first()
    if not schedule_type:
        schedule_type = ScheduleType(name="income", description="Regular income")
        db.session.add(schedule_type)
        db.session.commit()

    # Calculate paycheck amount based on frequency
    pay_periods = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
    periods = pay_periods.get(frequency_name, 26)  # Default to biweekly
    paycheck_amount = salary.gross_annual_salary / decimal.Decimal(periods)

    # Create recurring schedule for the salary
    schedule = RecurringSchedule(
        user_id=user_id,
        type_id=schedule_type.id,
        description=f"Salary - ${salary.gross_annual_salary:,.2f}/year",
        frequency_id=frequency.id,
        interval=1,  # 1 payment per frequency
        start_date=salary.effective_date,
        end_date=salary.end_date,
        amount=paycheck_amount,
    )
    db.session.add(schedule)
    db.session.commit()

    # Generate initial paychecks
    start_date = salary.effective_date
    tax_rate = decimal.Decimal("0.22")  # Default federal tax rate
    deduction_rate = decimal.Decimal("0.05")  # Default retirement rate

    # Get custom allocations for this salary
    salary_allocations = SalaryDepositAllocation.query.filter_by(
        salary_id=salary.id
    ).all()

    # List to store created paychecks
    paychecks = []

    # Generate paychecks until the end_date is reached
    i = 0
    while True:
        # Calculate paycheck date based on frequency
        if frequency_name == "weekly":
            paycheck_date = start_date + timedelta(days=7 * i)
        elif frequency_name == "biweekly":
            paycheck_date = start_date + timedelta(days=14 * i)
        elif frequency_name == "semimonthly":
            # Simplified – not accounting for exact semi-monthly dates
            paycheck_date = start_date + timedelta(days=15 * i)
        elif frequency_name == "monthly":
            # Simplified – does not account for different month lengths
            paycheck_date = start_date + timedelta(days=30 * i)
        else:
            paycheck_date = start_date + timedelta(days=14 * i)  # Default to biweekly

        # Break the loop if we've passed the end_date (if one is set)
        if salary.end_date and paycheck_date > salary.end_date:
            break

        # Optionally, skip if the date is too far in the past
        if paycheck_date < datetime.now().date() - timedelta(days=30):
            i += 1
            continue

        # Calculate paycheck components
        gross = paycheck_amount
        taxes = gross * tax_rate
        deductions = gross * deduction_rate
        net = gross - taxes - deductions

        # Create the paycheck record
        paycheck = Paycheck(
            user_id=user_id,
            scheduled_date=paycheck_date,
            gross_salary=gross,
            taxes=taxes,
            deductions=deductions,
            net_salary=net,
            is_projected=True,
            recurring_schedule_id=schedule.id,
            paid=False,
        )
        db.session.add(paycheck)
        db.session.commit()  # Commit to get the paycheck ID

        paychecks.append(paycheck)

        # Add income payment records based on allocations
        if salary_allocations:
            fixed_amount_total = decimal.Decimal("0.00")
            for allocation in [a for a in salary_allocations if not a.is_percentage]:
                payment = IncomePayment(
                    paycheck_id=paycheck.id,
                    account_id=allocation.account_id,
                    payment_date=paycheck_date,
                    amount=allocation.amount,
                    is_percentage=False,
                )
                db.session.add(payment)
                fixed_amount_total += allocation.amount

            remaining_amount = net - fixed_amount_total
            for allocation in [a for a in salary_allocations if a.is_percentage]:
                amount = (remaining_amount * allocation.percentage / 100).quantize(
                    decimal.Decimal("0.01")
                )
                payment = IncomePayment(
                    paycheck_id=paycheck.id,
                    account_id=allocation.account_id,
                    payment_date=paycheck_date,
                    amount=amount,
                    is_percentage=True,
                    percentage=allocation.percentage,
                )
                db.session.add(payment)
        else:
            default_account = Account.query.filter_by(user_id=user_id).first()
            if default_account:
                payment = IncomePayment(
                    paycheck_id=paycheck.id,
                    account_id=default_account.id,
                    payment_date=paycheck_date,
                    amount=net,
                    is_percentage=True,
                    percentage=100,
                )
                db.session.add(payment)

        db.session.commit()

        i += 1

    flash(
        f"Successfully generated paycheck schedule from salary record. View your paychecks for details.",
        "success",
    )
    return redirect(url_for("income.manage_paychecks"))


@income_bp.route("/salary/view/<int:salary_id>")
@login_required
def view_salary(salary_id):
    """View a specific salary record with options to edit, delete, or generate paychecks"""
    user_id = session.get("user_id")
    salary = SalaryChange.query.filter_by(id=salary_id, user_id=user_id).first_or_404()

    # Get the template we created earlier
    return render_template("income/view_salary.html", salary=salary)


# Helper function to generate future paychecks automatically
def generate_recurring_paychecks(
    user_id, schedule_id, start_date, end_date=None, num_periods=6
):
    """
    Generates projected paychecks for a recurring schedule

    Args:
        user_id: The user ID
        schedule_id: The recurring schedule ID
        start_date: The start date for the first paycheck
        end_date: Optional end date to stop generating paychecks
        num_periods: Number of paychecks to generate
    """
    schedule = RecurringSchedule.query.get_or_404(schedule_id)
    frequency = Frequency.query.get_or_404(schedule.frequency_id)

    # Calculate time delta between paychecks based on frequency
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

    # Generate paychecks
    paychecks = []
    current_date = start_date

    for i in range(num_periods):
        # Stop if we've reached the end date
        if end_date and current_date > end_date:
            break

        # Create the paycheck
        paycheck = Paycheck(
            user_id=user_id,
            scheduled_date=current_date,
            gross_salary=schedule.amount,
            taxes=schedule.amount
            * decimal.Decimal("0.22"),  # Simplified tax calculation
            deductions=schedule.amount
            * decimal.Decimal("0.05"),  # Simplified deductions
            net_salary=schedule.amount
            * decimal.Decimal("0.73"),  # Simplified net calculation
            is_projected=True,
            recurring_schedule_id=schedule.id,
            paid=False,
        )

        paychecks.append(paycheck)
        db.session.add(paycheck)

        # Increment the date for the next paycheck
        current_date += delta

    db.session.commit()
    return paychecks


@income_bp.route("/paychecks/delete/<int:paycheck_id>", methods=["POST"])
@login_required
def delete_paycheck(paycheck_id):
    """Delete an individual paycheck"""
    user_id = session.get("user_id")
    paycheck = Paycheck.query.filter_by(id=paycheck_id, user_id=user_id).first_or_404()

    # Store information for the flash message
    paycheck_date = paycheck.scheduled_date.strftime("%b %d, %Y")
    paycheck_amount = paycheck.gross_salary

    try:
        # If there are any income_payments associated with this paycheck, delete them first
        if paycheck.income_payments:
            for payment in paycheck.income_payments:
                # If the payment updated an account balance, reverse that update
                if payment.account:
                    payment.account.balance -= payment.amount
                db.session.delete(payment)

        # Delete the paycheck
        db.session.delete(paycheck)
        db.session.commit()

        flash(
            f"Paycheck for {paycheck_date} (${paycheck_amount:,.2f}) was successfully deleted.",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting paycheck: {str(e)}", "danger")

    return redirect(url_for("income.manage_paychecks"))
