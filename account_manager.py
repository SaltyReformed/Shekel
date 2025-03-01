from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import db, Account, AccountType, User, Transaction, AccountInterest
from functools import wraps
from datetime import date
from decimal import Decimal
import calendar


account_bp = Blueprint("account", __name__, url_prefix="/accounts")


# Helper function to require login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


# Account management routes
@account_bp.route("/")
@login_required
def overview():
    user_id = session.get("user_id")
    accounts = Account.query.filter_by(user_id=user_id).all()

    # Get interest settings for each account
    account_interest = {}
    for account in accounts:
        interest = AccountInterest.query.filter_by(account_id=account.id).first()
        if interest and interest.enabled:
            account_interest[account.id] = interest

    # Calculate totals
    assets_total = sum(a.balance for a in accounts if not a.account_type.is_debt)
    debts_total = sum(a.balance for a in accounts if a.account_type.is_debt)
    net_worth = assets_total - debts_total

    # Calculate future balances at 1 year for accounts with interest
    future_balances = {}
    for account_id, interest in account_interest.items():
        account = next((a for a in accounts if a.id == account_id), None)
        if account:
            future_balances[account_id] = estimate_future_balance(
                account, 12
            )  # 12 months projection

    # Determine if there are any asset or debt accounts
    has_assets = any(not a.account_type.is_debt for a in accounts)
    has_debts = any(a.account_type.is_debt for a in accounts)

    return render_template(
        "accounts/overview.html",
        accounts=accounts,
        assets_total=assets_total,
        debts_total=debts_total,
        net_worth=net_worth,
        account_interest=account_interest,
        future_balances=future_balances,
        has_assets=has_assets,
        has_debts=has_debts,
    )


@account_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_account():
    user_id = session.get("user_id")
    account_types = AccountType.query.all()

    if request.method == "POST":
        account_name = request.form.get("account_name")
        type_id = request.form.get("type_id")
        initial_balance = request.form.get("initial_balance", "0.00")

        # Validate inputs
        if not account_name:
            flash("Account name is required", "danger")
            return render_template(
                "accounts/edit_account.html", account_types=account_types, is_edit=False
            )

        # Convert initial balance to Decimal
        try:
            initial_balance = Decimal(initial_balance)
        except:
            flash("Invalid initial balance", "danger")
            return render_template(
                "accounts/edit_account.html", account_types=account_types, is_edit=False
            )

        # Create new account
        account = Account(
            user_id=user_id,
            account_name=account_name,
            type_id=type_id,
            balance=initial_balance,
        )

        db.session.add(account)
        db.session.commit()

        flash(f"Account '{account_name}' created successfully", "success")
        return redirect(url_for("account.overview"))

    return render_template(
        "accounts/edit_account.html", account_types=account_types, is_edit=False
    )


@account_bp.route("/edit/<int:account_id>", methods=["GET", "POST"])
@login_required
def edit_account(account_id):
    user_id = session.get("user_id")
    account = Account.query.filter_by(id=account_id, user_id=user_id).first_or_404()
    account_types = AccountType.query.all()

    if request.method == "POST":
        account_name = request.form.get("account_name")
        type_id = request.form.get("type_id")

        # Validate inputs
        if not account_name:
            flash("Account name is required", "danger")
            return render_template(
                "accounts/edit_account.html",
                account=account,
                account_types=account_types,
                is_edit=True,
            )

        # Update account details
        account.account_name = account_name
        account.type_id = type_id

        db.session.commit()

        flash(f"Account '{account_name}' updated successfully", "success")
        return redirect(url_for("account.overview"))

    return render_template(
        "accounts/edit_account.html",
        account=account,
        account_types=account_types,
        is_edit=True,
    )


@account_bp.route("/delete/<int:account_id>", methods=["POST"])
@login_required
def delete_account(account_id):
    user_id = session.get("user_id")
    account = Account.query.filter_by(id=account_id, user_id=user_id).first_or_404()

    try:
        db.session.delete(account)
        db.session.commit()
        flash(f"Account '{account.account_name}' deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash("Error deleting account. It may have linked transactions.", "danger")

    return redirect(url_for("account.overview"))


# Account Types management routes (admin only)
@account_bp.route("/types")
@login_required
def account_types():
    # Check if user is admin
    user = User.query.get(session["user_id"])
    if not user or not user.role or user.role.name != "ADMIN":
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("account.overview"))

    account_types = AccountType.query.all()
    return render_template("accounts/account_types.html", account_types=account_types)


@account_bp.route("/types/add", methods=["GET", "POST"])
@login_required
def add_account_type():
    # Check if user is admin
    user = User.query.get(session["user_id"])
    if not user or not user.role or user.role.name != "ADMIN":
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("account.overview"))

    if request.method == "POST":
        type_name = request.form.get("type_name")
        is_debt = "is_debt" in request.form

        # Validate inputs
        if not type_name:
            flash("Account type name is required", "danger")
            return render_template("accounts/edit_account_type.html", is_edit=False)

        # Create new account type
        account_type = AccountType(type_name=type_name, is_debt=is_debt)

        db.session.add(account_type)
        db.session.commit()

        flash(f"Account type '{type_name}' created successfully", "success")
        return redirect(url_for("account.account_types"))

    return render_template("accounts/edit_account_type.html", is_edit=False)


@account_bp.route("/types/edit/<int:type_id>", methods=["GET", "POST"])
@login_required
def edit_account_type(type_id):
    # Check if user is admin
    user = User.query.get(session["user_id"])
    if not user or not user.role or user.role.name != "ADMIN":
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("account.overview"))

    account_type = AccountType.query.get_or_404(type_id)

    if request.method == "POST":
        type_name = request.form.get("type_name")
        is_debt = "is_debt" in request.form

        # Validate inputs
        if not type_name:
            flash("Account type name is required", "danger")
            return render_template(
                "accounts/edit_account_type.html",
                account_type=account_type,
                is_edit=True,
            )

        # Update account type
        account_type.type_name = type_name
        account_type.is_debt = is_debt

        db.session.commit()

        flash(f"Account type '{type_name}' updated successfully", "success")
        return redirect(url_for("account.account_types"))

    return render_template(
        "accounts/edit_account_type.html", account_type=account_type, is_edit=True
    )


@account_bp.route("/types/delete/<int:type_id>", methods=["POST"])
@login_required
def delete_account_type(type_id):
    # Check if user is admin
    user = User.query.get(session["user_id"])
    if not user or not user.role or user.role.name != "ADMIN":
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("account.overview"))

    account_type = AccountType.query.get_or_404(type_id)

    try:
        db.session.delete(account_type)
        db.session.commit()
        flash(
            f"Account type '{account_type.type_name}' deleted successfully", "success"
        )
    except Exception as e:
        db.session.rollback()
        flash(
            "Error deleting account type. It may be in use by one or more accounts.",
            "danger",
        )

    return redirect(url_for("account.account_types"))


# Transaction routes
@account_bp.route("/transaction/add", methods=["GET", "POST"])
@login_required
def add_transaction():
    user_id = session.get("user_id")
    accounts = Account.query.filter_by(user_id=user_id).all()

    if request.method == "POST":
        account_id = request.form.get("account_id")
        amount = request.form.get("amount")
        description = request.form.get("description", "")
        transaction_date_str = request.form.get("transaction_date")
        transaction_type = request.form.get("transaction_type")

        # Validate inputs
        if (
            not account_id
            or not amount
            or not transaction_date_str
            or not transaction_type
        ):
            flash("All fields are required", "danger")
            return render_template("accounts/add_transaction.html", accounts=accounts)

        # Convert amount to Decimal
        try:
            amount = Decimal(amount)
            if amount <= 0:
                flash("Amount must be positive", "danger")
                return render_template(
                    "accounts/add_transaction.html", accounts=accounts
                )
        except:
            flash("Invalid amount", "danger")
            return render_template("accounts/add_transaction.html", accounts=accounts)

        # Convert date
        try:
            transaction_date = date.fromisoformat(transaction_date_str)
        except:
            flash("Invalid date format", "danger")
            return render_template("accounts/add_transaction.html", accounts=accounts)

        # Get account
        account = Account.query.filter_by(id=account_id, user_id=user_id).first()
        if not account:
            flash("Invalid account", "danger")
            return render_template("accounts/add_transaction.html", accounts=accounts)

        # Process based on transaction type
        if transaction_type == "deposit":
            # Create deposit transaction
            transaction = Transaction(
                account_id=account_id,
                transaction_date=transaction_date,
                amount=amount,
                description=description,
                transaction_type="deposit",
            )

            # Update account balance
            account.balance += amount

            db.session.add(transaction)
            db.session.commit()

            flash("Deposit recorded successfully", "success")

        elif transaction_type == "withdrawal":
            # Create withdrawal transaction
            transaction = Transaction(
                account_id=account_id,
                transaction_date=transaction_date,
                amount=amount,
                description=description,
                transaction_type="withdrawal",
            )

            # Update account balance (subtract for withdrawal)
            account.balance -= amount

            db.session.add(transaction)
            db.session.commit()

            flash("Withdrawal recorded successfully", "success")

        elif transaction_type == "transfer":
            # For transfers, we need a destination account
            to_account_id = request.form.get("to_account_id")
            if not to_account_id:
                flash("Destination account is required for transfers", "danger")
                return render_template(
                    "accounts/add_transaction.html", accounts=accounts
                )

            # Get destination account
            to_account = Account.query.filter_by(
                id=to_account_id, user_id=user_id
            ).first()
            if not to_account:
                flash("Invalid destination account", "danger")
                return render_template(
                    "accounts/add_transaction.html", accounts=accounts
                )

            # Create withdrawal transaction
            from_transaction = Transaction(
                account_id=account_id,
                transaction_date=transaction_date,
                amount=amount,
                description=f"Transfer to {to_account.account_name}: {description}",
                transaction_type="transfer_out",
            )

            # Create deposit transaction
            to_transaction = Transaction(
                account_id=to_account_id,
                transaction_date=transaction_date,
                amount=amount,
                description=f"Transfer from {account.account_name}: {description}",
                transaction_type="transfer_in",
            )

            # Update balances
            account.balance -= amount
            to_account.balance += amount

            # Add transactions and link them
            db.session.add(from_transaction)
            db.session.flush()  # Get ID for the first transaction

            # Link the transactions
            to_transaction.related_transaction_id = from_transaction.id
            db.session.add(to_transaction)
            from_transaction.related_transaction_id = to_transaction.id

            db.session.commit()

            flash("Transfer completed successfully", "success")

        return redirect(url_for("account.transactions", account_id=account_id))

    return render_template("accounts/add_transaction.html", accounts=accounts)


@account_bp.route("/transactions")
@login_required
def all_transactions():
    user_id = session.get("user_id")
    accounts = Account.query.filter_by(user_id=user_id).all()
    account_ids = [a.id for a in accounts]

    query = Transaction.query.filter(Transaction.account_id.in_(account_ids))

    # Filter by account if specified
    account_filter = request.args.get("account")
    if account_filter:
        query = query.filter_by(account_id=account_filter)

    # Filter by date range if specified
    start_date = request.args.get("start_date")
    if start_date:
        query = query.filter(
            Transaction.transaction_date >= date.fromisoformat(start_date)
        )
    end_date = request.args.get("end_date")
    if end_date:
        query = query.filter(
            Transaction.transaction_date <= date.fromisoformat(end_date)
        )

    transactions = query.order_by(Transaction.transaction_date.desc()).all()
    return render_template(
        "accounts/transactions.html", transactions=transactions, accounts=accounts
    )


@account_bp.route("/<int:account_id>/transactions")
@login_required
def transactions(account_id):
    user_id = session.get("user_id")
    account = Account.query.filter_by(id=account_id, user_id=user_id).first_or_404()

    query = Transaction.query.filter_by(account_id=account_id)

    # Filter by transaction type if provided
    transaction_type = request.args.get("transaction_type")
    if transaction_type:
        if transaction_type == "transfer":
            query = query.filter(
                Transaction.transaction_type.in_(["transfer_in", "transfer_out"])
            )
        else:
            query = query.filter_by(transaction_type=transaction_type)

    # Filter by date range if provided
    start_date = request.args.get("start_date")
    if start_date:
        query = query.filter(
            Transaction.transaction_date >= date.fromisoformat(start_date)
        )
    end_date = request.args.get("end_date")
    if end_date:
        query = query.filter(
            Transaction.transaction_date <= date.fromisoformat(end_date)
        )

    transactions = query.order_by(Transaction.transaction_date.desc()).all()
    return render_template(
        "accounts/account_transactions.html", account=account, transactions=transactions
    )


# Add these routes to your account_manager.py file, somewhere near the other transaction routes


@account_bp.route("/transaction/<int:transaction_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(transaction_id):
    user_id = session.get("user_id")

    # Find the transaction
    transaction = Transaction.query.get_or_404(transaction_id)

    # Check if the transaction belongs to the user
    account = Account.query.filter_by(
        id=transaction.account_id, user_id=user_id
    ).first_or_404()

    # Get all user accounts for the form dropdown
    accounts = Account.query.filter_by(user_id=user_id).all()

    if request.method == "POST":
        # Get form data
        description = request.form.get("description", "")
        transaction_date_str = request.form.get("transaction_date")

        # Only allow changing description and date for simplicity
        # Changing amount or type would require complex balance adjustments

        # Convert date
        try:
            transaction_date = date.fromisoformat(transaction_date_str)
        except:
            flash("Invalid date format", "danger")
            return render_template(
                "accounts/edit_transaction.html",
                transaction=transaction,
                accounts=accounts,
            )

        # Update transaction
        transaction.description = description
        transaction.transaction_date = transaction_date

        db.session.commit()

        flash("Transaction updated successfully", "success")

        # Redirect back to the appropriate transactions page
        if request.form.get("redirect_to_account"):
            return redirect(
                url_for("account.transactions", account_id=transaction.account_id)
            )
        else:
            return redirect(url_for("account.all_transactions"))

    return render_template(
        "accounts/edit_transaction.html", transaction=transaction, accounts=accounts
    )


@account_bp.route("/transaction/<int:transaction_id>/delete", methods=["POST"])
@login_required
def delete_transaction(transaction_id):
    user_id = session.get("user_id")

    # Find the transaction
    transaction = Transaction.query.get_or_404(transaction_id)

    # Check if the transaction belongs to the user
    account = Account.query.filter_by(
        id=transaction.account_id, user_id=user_id
    ).first_or_404()

    # Store information for redirection and message
    account_id = transaction.account_id
    redirect_to_account = request.form.get("redirect_to_account") == "1"
    transaction_date = transaction.transaction_date.strftime("%b %d, %Y")
    transaction_type = transaction.transaction_type
    transaction_amount = transaction.amount

    try:
        # Reverse the transaction effect on balance
        if transaction_type == "deposit" or transaction_type == "transfer_in":
            account.balance -= transaction_amount
        elif transaction_type == "withdrawal" or transaction_type == "transfer_out":
            account.balance += transaction_amount

        # If it's a transfer, handle the related transaction
        if transaction.related_transaction_id:
            related_transaction = Transaction.query.get(
                transaction.related_transaction_id
            )
            if related_transaction:
                # Find the related account
                related_account = Account.query.get(related_transaction.account_id)
                if related_account and related_account.user_id == user_id:
                    # Reverse the effect on the related account
                    if (
                        related_transaction.transaction_type == "deposit"
                        or related_transaction.transaction_type == "transfer_in"
                    ):
                        related_account.balance -= related_transaction.amount
                    elif (
                        related_transaction.transaction_type == "withdrawal"
                        or related_transaction.transaction_type == "transfer_out"
                    ):
                        related_account.balance += related_transaction.amount

                    # Delete the related transaction
                    db.session.delete(related_transaction)

        # Delete the transaction
        db.session.delete(transaction)
        db.session.commit()

        flash(
            f"Transaction from {transaction_date} for ${transaction_amount:,.2f} deleted successfully",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting transaction: {str(e)}", "danger")

    # Redirect back to the appropriate transactions page
    if redirect_to_account:
        return redirect(url_for("account.transactions", account_id=account_id))
    else:
        return redirect(url_for("account.all_transactions"))


@account_bp.route("/<int:account_id>/interest", methods=["GET", "POST"])
@login_required
def manage_interest(account_id):
    user_id = session.get("user_id")
    account = Account.query.filter_by(id=account_id, user_id=user_id).first_or_404()

    # Check if interest settings already exist
    interest_settings = AccountInterest.query.filter_by(account_id=account_id).first()
    if not interest_settings:
        interest_settings = AccountInterest(account_id=account_id, rate=0.00)

    if request.method == "POST":
        rate = request.form.get("rate", "0.00")
        compound_frequency = request.form.get("compound_frequency", "monthly")
        accrual_day = request.form.get("accrual_day", None)
        interest_type = request.form.get("interest_type", "simple")
        enabled = "enabled" in request.form

        # Convert accrual_day to integer or None
        if accrual_day and accrual_day.isdigit():
            accrual_day = int(accrual_day)
        else:
            accrual_day = None

        # Convert rate to Decimal
        try:
            rate = Decimal(rate)
            if rate < 0:
                flash("Interest rate cannot be negative", "danger")
                return render_template(
                    "accounts/manage_interest.html",
                    account=account,
                    interest_settings=interest_settings,
                )
        except:
            flash("Invalid interest rate", "danger")
            return render_template(
                "accounts/manage_interest.html",
                account=account,
                interest_settings=interest_settings,
            )

        # Update interest settings
        interest_settings.rate = rate
        interest_settings.compound_frequency = compound_frequency
        interest_settings.accrual_day = accrual_day
        interest_settings.interest_type = interest_type
        interest_settings.enabled = enabled

        # Save to database
        db.session.add(interest_settings)
        db.session.commit()

        flash("Interest settings updated successfully", "success")
        return redirect(url_for("account.overview"))

    return render_template(
        "accounts/manage_interest.html",
        account=account,
        interest_settings=interest_settings,
    )


@account_bp.route("/accrue-interest")
@login_required
def accrue_interest():
    user_id = session.get("user_id")
    today = date.today()

    # Get accounts with interest enabled
    interest_accounts = (
        db.session.query(Account, AccountInterest)
        .join(AccountInterest, Account.id == AccountInterest.account_id)
        .filter(Account.user_id == user_id, AccountInterest.enabled == True)
        .all()
    )

    accrued_count = 0
    for account, interest_settings in interest_accounts:
        should_accrue = False

        # Check if interest should be accrued today
        if interest_settings.compound_frequency == "daily":
            # Daily accrual - check if we haven't accrued today
            should_accrue = (
                interest_settings.last_accrual_date is None
                or interest_settings.last_accrual_date < today
            )

        elif interest_settings.compound_frequency == "monthly":
            # Monthly accrual - check if it's the specified day or last day of month
            if interest_settings.accrual_day:
                # Specific day of month
                should_accrue = today.day == interest_settings.accrual_day and (
                    interest_settings.last_accrual_date is None
                    or interest_settings.last_accrual_date.month != today.month
                    or interest_settings.last_accrual_date.year != today.year
                )
            else:
                # Last day of month
                _, last_day = calendar.monthrange(today.year, today.month)
                should_accrue = today.day == last_day and (
                    interest_settings.last_accrual_date is None
                    or interest_settings.last_accrual_date.month != today.month
                    or interest_settings.last_accrual_date.year != today.year
                )

        elif interest_settings.compound_frequency == "quarterly":
            # Quarterly accrual - check if it's end of quarter
            quarter_end_months = [3, 6, 9, 12]
            if today.month in quarter_end_months:
                _, last_day = calendar.monthrange(today.year, today.month)
                should_accrue = today.day == last_day and (
                    interest_settings.last_accrual_date is None
                    or interest_settings.last_accrual_date.month != today.month
                    or interest_settings.last_accrual_date.year != today.year
                )

        elif interest_settings.compound_frequency == "annually":
            # Annual accrual - check if it's December 31
            should_accrue = (
                today.month == 12
                and today.day == 31
                and (
                    interest_settings.last_accrual_date is None
                    or interest_settings.last_accrual_date.year != today.year
                )
            )

        # Calculate and add interest if needed
        if should_accrue:
            # Determine the period for interest calculation
            if interest_settings.last_accrual_date is None:
                # First time accruing - use a month as the period
                if interest_settings.compound_frequency == "daily":
                    days = 1
                elif interest_settings.compound_frequency == "monthly":
                    days = 30
                elif interest_settings.compound_frequency == "quarterly":
                    days = 91
                else:  # annually
                    days = 365
            else:
                # Calculate days since last accrual
                delta = today - interest_settings.last_accrual_date
                days = delta.days
                if days <= 0:
                    continue  # Skip if no days have passed

            # Calculate interest amount
            if interest_settings.interest_type == "simple":
                # Simple interest calculation
                daily_rate = interest_settings.rate / Decimal("100") / Decimal("365")
                interest_amount = account.balance * daily_rate * Decimal(str(days))
            else:
                # Compound interest calculation
                periods = days / (
                    365 / periods_per_year(interest_settings.compound_frequency)
                )
                periodic_rate = (
                    interest_settings.rate
                    / Decimal("100")
                    / periods_per_year(interest_settings.compound_frequency)
                )
                compound_factor = (1 + periodic_rate) ** Decimal(str(periods))
                interest_amount = account.balance * (compound_factor - 1)

            # Round to 2 decimal places
            interest_amount = interest_amount.quantize(Decimal("0.01"))

            if interest_amount > 0:
                # Create a transaction for the interest
                transaction = Transaction(
                    account_id=account.id,
                    transaction_date=today,
                    amount=interest_amount,
                    description=f"Interest accrual ({interest_settings.rate}% {interest_settings.compound_frequency})",
                    transaction_type="deposit",
                )

                # Update account balance
                account.balance += interest_amount

                # Update last accrual date
                interest_settings.last_accrual_date = today

                db.session.add(transaction)
                db.session.commit()

                accrued_count += 1

    if accrued_count > 0:
        flash(f"Interest accrued for {accrued_count} account(s)", "success")
    else:
        flash("No interest accrued today", "info")

    return redirect(url_for("account.overview"))


# Helper function to determine periods per year based on compound frequency
def periods_per_year(frequency):
    if frequency == "daily":
        return Decimal("365")
    elif frequency == "monthly":
        return Decimal("12")
    elif frequency == "quarterly":
        return Decimal("4")
    else:  # annually
        return Decimal("1")


# Function to estimate future balance with interest
def estimate_future_balance(account, months):
    interest_settings = AccountInterest.query.filter_by(account_id=account.id).first()
    if not interest_settings or not interest_settings.enabled:
        # No interest settings or disabled, return current balance
        return account.balance

    # Get base variables
    principal = account.balance
    annual_rate = interest_settings.rate / Decimal("100")

    if interest_settings.interest_type == "simple":
        # Simple interest formula: A = P(1 + rt)
        years = Decimal(str(months)) / Decimal("12")
        future_balance = principal * (1 + annual_rate * years)
    else:
        # Compound interest formula: A = P(1 + r/n)^(nt)
        if interest_settings.compound_frequency == "daily":
            n = Decimal("365")
        elif interest_settings.compound_frequency == "monthly":
            n = Decimal("12")
        elif interest_settings.compound_frequency == "quarterly":
            n = Decimal("4")
        else:  # annually
            n = Decimal("1")

        years = Decimal(str(months)) / Decimal("12")
        future_balance = principal * ((1 + annual_rate / n) ** (n * years))

    # Round to 2 decimal places
    return future_balance.quantize(Decimal("0.01"))
