"""
Shekel Budget App — Authentication Service

Handles password hashing, verification, and user registration.
No Flask imports — this is a pure service module.
"""

import re
from decimal import Decimal

import bcrypt

from app.extensions import db
from app.models.user import User, UserSettings
from app.models.account import Account
from app.models.category import Category
from app.models.ref import AccountType, FilingStatus, TaxType
from app.models.scenario import Scenario
from app.models.tax_config import FicaConfig, StateTaxConfig, TaxBracket, TaxBracketSet
from app.exceptions import AuthError, ConflictError, ValidationError

DEFAULT_CATEGORIES = [
    ("Income", "Salary"),
    ("Income", "Other Income"),
    ("Home", "Mortgage/Rent"),
    ("Home", "Electricity"),
    ("Home", "Gas"),
    ("Home", "Water"),
    ("Home", "Internet"),
    ("Home", "Phone"),
    ("Home", "Home Insurance"),
    ("Auto", "Car Payment"),
    ("Auto", "Car Insurance"),
    ("Auto", "Fuel"),
    ("Auto", "Maintenance"),
    ("Family", "Groceries"),
    ("Family", "Dining Out"),
    ("Family", "Spending Money"),
    ("Family", "Subscriptions"),
    ("Health", "Medical"),
    ("Health", "Dental"),
    ("Financial", "Savings Transfer"),
    ("Financial", "Extra Debt Payment"),
    ("Credit Card", "Payback"),
]

DEFAULT_FEDERAL_BRACKETS = {
    2025: {
        "single": {
            "standard_deduction": Decimal("15000"),
            "child_credit_amount": Decimal("2000"),
            "other_dependent_credit_amount": Decimal("500"),
            "brackets": [
                (0, 11925, Decimal("0.1000")),
                (11925, 48475, Decimal("0.1200")),
                (48475, 103350, Decimal("0.2200")),
                (103350, 197300, Decimal("0.2400")),
                (197300, 250525, Decimal("0.3200")),
                (250525, 626350, Decimal("0.3500")),
                (626350, None, Decimal("0.3700")),
            ],
        },
        "married_jointly": {
            "standard_deduction": Decimal("30000"),
            "child_credit_amount": Decimal("2000"),
            "other_dependent_credit_amount": Decimal("500"),
            "brackets": [
                (0, 23850, Decimal("0.1000")),
                (23850, 96950, Decimal("0.1200")),
                (96950, 206700, Decimal("0.2200")),
                (206700, 394600, Decimal("0.2400")),
                (394600, 501050, Decimal("0.3200")),
                (501050, 751600, Decimal("0.3500")),
                (751600, None, Decimal("0.3700")),
            ],
        },
        "married_separately": {
            "standard_deduction": Decimal("15000"),
            "child_credit_amount": Decimal("2000"),
            "other_dependent_credit_amount": Decimal("500"),
            "brackets": [
                (0, 11925, Decimal("0.1000")),
                (11925, 48475, Decimal("0.1200")),
                (48475, 103350, Decimal("0.2200")),
                (103350, 197300, Decimal("0.2400")),
                (197300, 250525, Decimal("0.3200")),
                (250525, 375800, Decimal("0.3500")),
                (375800, None, Decimal("0.3700")),
            ],
        },
        "head_of_household": {
            "standard_deduction": Decimal("22500"),
            "child_credit_amount": Decimal("2000"),
            "other_dependent_credit_amount": Decimal("500"),
            "brackets": [
                (0, 17000, Decimal("0.1000")),
                (17000, 64850, Decimal("0.1200")),
                (64850, 103350, Decimal("0.2200")),
                (103350, 197300, Decimal("0.2400")),
                (197300, 250500, Decimal("0.3200")),
                (250500, 626350, Decimal("0.3500")),
                (626350, None, Decimal("0.3700")),
            ],
        },
    },
    2026: {
        "single": {
            "standard_deduction": Decimal("15350"),
            "child_credit_amount": Decimal("2000"),
            "other_dependent_credit_amount": Decimal("500"),
            "brackets": [
                (0, 12150, Decimal("0.1000")),
                (12150, 49475, Decimal("0.1200")),
                (49475, 105525, Decimal("0.2200")),
                (105525, 201350, Decimal("0.2400")),
                (201350, 255800, Decimal("0.3200")),
                (255800, 639500, Decimal("0.3500")),
                (639500, None, Decimal("0.3700")),
            ],
        },
        "married_jointly": {
            "standard_deduction": Decimal("30700"),
            "child_credit_amount": Decimal("2000"),
            "other_dependent_credit_amount": Decimal("500"),
            "brackets": [
                (0, 24300, Decimal("0.1000")),
                (24300, 98950, Decimal("0.1200")),
                (98950, 211050, Decimal("0.2200")),
                (211050, 402700, Decimal("0.2400")),
                (402700, 511500, Decimal("0.3200")),
                (511500, 767200, Decimal("0.3500")),
                (767200, None, Decimal("0.3700")),
            ],
        },
        "married_separately": {
            "standard_deduction": Decimal("15350"),
            "child_credit_amount": Decimal("2000"),
            "other_dependent_credit_amount": Decimal("500"),
            "brackets": [
                (0, 12150, Decimal("0.1000")),
                (12150, 49475, Decimal("0.1200")),
                (49475, 105525, Decimal("0.2200")),
                (105525, 201350, Decimal("0.2400")),
                (201350, 255800, Decimal("0.3200")),
                (255800, 383600, Decimal("0.3500")),
                (383600, None, Decimal("0.3700")),
            ],
        },
        "head_of_household": {
            "standard_deduction": Decimal("23000"),
            "child_credit_amount": Decimal("2000"),
            "other_dependent_credit_amount": Decimal("500"),
            "brackets": [
                (0, 17350, Decimal("0.1000")),
                (17350, 66200, Decimal("0.1200")),
                (66200, 105525, Decimal("0.2200")),
                (105525, 201350, Decimal("0.2400")),
                (201350, 255800, Decimal("0.3200")),
                (255800, 639500, Decimal("0.3500")),
                (639500, None, Decimal("0.3700")),
            ],
        },
    },
}

DEFAULT_FICA = {
    2025: {
        "ss_rate": Decimal("0.0620"),
        "ss_wage_base": Decimal("176100"),
        "medicare_rate": Decimal("0.0145"),
        "medicare_surtax_rate": Decimal("0.0090"),
        "medicare_surtax_threshold": Decimal("200000"),
    },
    2026: {
        "ss_rate": Decimal("0.0620"),
        "ss_wage_base": Decimal("180000"),
        "medicare_rate": Decimal("0.0145"),
        "medicare_surtax_rate": Decimal("0.0090"),
        "medicare_surtax_threshold": Decimal("200000"),
    },
}

DEFAULT_STATE_TAX = {
    "state_code": "NC",
    "flat_rate": Decimal("0.0450"),
}


def _seed_tax_data_for_user(user_id):
    """Create default federal brackets, FICA, and state tax for a new user."""
    filing_statuses = {
        fs.name: fs for fs in db.session.query(FilingStatus).all()
    }

    for tax_year, year_data in DEFAULT_FEDERAL_BRACKETS.items():
        for status_name, data in year_data.items():
            fs = filing_statuses.get(status_name)
            if not fs:
                continue
            bracket_set = TaxBracketSet(
                user_id=user_id,
                filing_status_id=fs.id,
                tax_year=tax_year,
                standard_deduction=data["standard_deduction"],
                child_credit_amount=data["child_credit_amount"],
                other_dependent_credit_amount=data["other_dependent_credit_amount"],
                description=f"{tax_year} Federal - {status_name.replace('_', ' ').title()}",
            )
            db.session.add(bracket_set)
            db.session.flush()

            for idx, (min_inc, max_inc, rate) in enumerate(data["brackets"]):
                db.session.add(TaxBracket(
                    bracket_set_id=bracket_set.id,
                    min_income=Decimal(str(min_inc)),
                    max_income=Decimal(str(max_inc)) if max_inc else None,
                    rate=rate,
                    sort_order=idx,
                ))

    for tax_year, data in DEFAULT_FICA.items():
        db.session.add(FicaConfig(user_id=user_id, tax_year=tax_year, **data))

    tax_type = db.session.query(TaxType).filter_by(name="flat").first()
    if tax_type:
        db.session.add(StateTaxConfig(
            user_id=user_id,
            tax_type_id=tax_type.id,
            state_code=DEFAULT_STATE_TAX["state_code"],
            flat_rate=DEFAULT_STATE_TAX["flat_rate"],
        ))


def hash_password(plain_password, rounds=None):
    """Hash a plaintext password using bcrypt.

    Args:
        plain_password: The plaintext password string.
        rounds: Optional bcrypt cost factor (log2 iterations).
            Defaults to bcrypt's built-in default if not specified.

    Returns:
        The bcrypt hash as a string.
    """
    salt = bcrypt.gensalt(rounds=rounds) if rounds else bcrypt.gensalt()
    return bcrypt.hashpw(
        plain_password.encode("utf-8"), salt
    ).decode("utf-8")


def verify_password(plain_password, password_hash):
    """Verify a plaintext password against a bcrypt hash.

    Args:
        plain_password: The plaintext password to check.
        password_hash:  The stored bcrypt hash.

    Returns:
        True if the password matches, False otherwise.
    """
    if plain_password is None:
        return False
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        password_hash.encode("utf-8"),
    )


def authenticate(email, password):
    """Authenticate a user by email and password.

    Args:
        email:    The user's email address.
        password: The plaintext password.

    Returns:
        The User object if authentication succeeds.

    Raises:
        AuthError: If the email is not found or the password is wrong.
    """
    user = db.session.query(User).filter_by(email=email).first()
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password.")
    if not user.is_active:
        raise AuthError("Account is disabled.")
    return user


def change_password(user, current_password, new_password):
    """Change a user's password after verifying the current one.

    Args:
        user: The User object whose password is being changed.
        current_password: The user's current plaintext password.
        new_password: The new plaintext password (must be >= 12 chars).

    Returns:
        None on success.

    Raises:
        AuthError: If current_password does not match the stored hash.
        ValidationError: If new_password is shorter than 12 characters.
    """
    if not verify_password(current_password, user.password_hash):
        raise AuthError("Current password is incorrect.")
    if len(new_password) < 12:
        raise ValidationError("New password must be at least 12 characters.")
    user.password_hash = hash_password(new_password)


def register_user(email, password, display_name):
    """Register a new user with default settings and a baseline scenario.

    Creates a User, UserSettings (with model defaults), and a baseline
    Scenario atomically.  Does NOT commit -- the caller is responsible
    for committing the transaction.

    Args:
        email:        The user's email address.
        password:     The plaintext password (must be >= 12 chars).
        display_name: The user's display name.

    Returns:
        The newly created User object (unflushed settings and scenario
        are attached to the same session).

    Raises:
        ValidationError: If the email format is invalid, the display
            name is empty, or the password is too short.
        ConflictError: If a user with the given email already exists.
    """
    # Sanitize inputs.
    email = email.strip().lower()
    display_name = display_name.strip()

    # Validate email format.
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValidationError("Invalid email format.")

    # Validate display name is not empty.
    if not display_name:
        raise ValidationError("Display name is required.")

    # Validate password length.
    if len(password) < 12:
        raise ValidationError("Password must be at least 12 characters.")

    # Check email uniqueness.
    if User.query.filter_by(email=email).first():
        raise ConflictError("An account with this email already exists.")

    # Create user.
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    db.session.add(user)
    db.session.flush()

    # Create default settings (model defaults handle values).
    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # Create default checking account.
    checking_type = db.session.query(AccountType).filter_by(name="checking").one()
    account = Account(
        user_id=user.id,
        account_type_id=checking_type.id,
        name="Checking",
        current_anchor_balance=0,
    )
    db.session.add(account)

    # Create baseline scenario.
    scenario = Scenario(user_id=user.id, name="Baseline", is_baseline=True)
    db.session.add(scenario)

    # Create default categories.
    for sort_idx, (group, item) in enumerate(DEFAULT_CATEGORIES):
        db.session.add(Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
            sort_order=sort_idx,
        ))

    # Create default tax configuration (federal brackets, FICA, state).
    _seed_tax_data_for_user(user.id)

    return user
