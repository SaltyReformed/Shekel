"""Shekel DAST Seeder -- audit users and ownerA resources.

Creates three deterministic audit users (ownerA, ownerB, companionC)
plus a rich set of user-scoped resources for ownerA so the IDOR probe
has at least one concrete integer ID to attack for every user-owned
model.

Dev-only. Refuses to run unless FLASK_ENV is 'development' and
DATABASE_URL does not contain 'prod'. Designed to be executed inside
the shekel-dev-app container:

    docker exec shekel-dev-app python \\
        /home/shekel/app/scripts/audit/seed_dast_users.py \\
        --credentials-out /home/shekel/app/scripts/audit/.dast-credentials.json

The resulting credentials file is consumed by
``scripts/audit/idor_probe.py`` and records the seeded users' passwords
plus the integer IDs of every resource ownerA owns.

Idempotent: any existing audit users (identified by the well-known
``@audit.local`` email addresses) are deleted first. CASCADE on the
``user_id`` foreign keys handles their owned rows. No non-audit user
is touched.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

# Make the project root importable regardless of how the script is invoked.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Include this script's directory so ``_audit_common`` imports resolve
# both on the host and inside the shekel-dev-app container (where the
# project tree is mounted at /home/shekel/app).
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# pylint: disable=wrong-import-position
from _audit_common import atomic_write_json
from app import create_app, ref_cache
from app.enums import (
    AcctTypeEnum,
    CalcMethodEnum,
    DeductionTimingEnum,
    GoalModeEnum,
    RecurrencePatternEnum,
    RoleEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_features import EscrowComponent
from app.models.loan_params import LoanParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.pay_period import PayPeriod
from app.models.pension_profile import PensionProfile
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import FilingStatus, RaiseType
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password, register_user

logger = logging.getLogger(__name__)

# ---- Audit user credentials (deterministic, well-known) -------------------

# Emails are lowercase so they match what ``auth_service.register_user()``
# stores (it calls ``email.strip().lower()``). The login route query is
# case-sensitive, so an uppercase credential would never match.
OWNER_A_EMAIL = "ownera@audit.local"
OWNER_A_PASSWORD = "DastOwnerA!2026"
OWNER_A_DISPLAY = "Audit Owner A"

OWNER_B_EMAIL = "ownerb@audit.local"
OWNER_B_PASSWORD = "DastOwnerB!2026"
OWNER_B_DISPLAY = "Audit Owner B"

COMPANION_C_EMAIL = "companionc@audit.local"
COMPANION_C_PASSWORD = "DastCompC!2026"
COMPANION_C_DISPLAY = "Audit Companion C"

AUDIT_EMAILS: frozenset[str] = frozenset(
    {OWNER_A_EMAIL, OWNER_B_EMAIL, COMPANION_C_EMAIL}
)

# ---- Resource tuning constants --------------------------------------------

DEV_ENV_NAME = "development"

# Number of biweekly pay periods to materialize. 3 gives us enough for
# past/current/future coverage in salary breakdown routes.
PAY_PERIOD_COUNT = 3
PAY_PERIOD_DAYS = 14

# Seed financial values. Concrete enough for the probe, small enough to
# be unmistakable if they ever leak into a real environment.
HYSA_BALANCE = Decimal("10000.00")
MORTGAGE_BALANCE = Decimal("200000.00")
INVESTMENT_BALANCE = Decimal("50000.00")
SALARY_ANNUAL = Decimal("60000.00")
TEMPLATE_AMOUNT = Decimal("500.00")
TRANSFER_AMOUNT = Decimal("250.00")
SAVINGS_GOAL_TARGET = Decimal("5000.00")
PENSION_MULTIPLIER = Decimal("0.01850")
LOAN_RATE = Decimal("0.06500")
LOAN_TERM_MONTHS = 360
ENTRY_AMOUNT = Decimal("42.17")
ESCROW_ANNUAL = Decimal("3600.00")
INTEREST_APY = Decimal("0.04500")

# Password length enforced by register_user().
MIN_PASSWORD_LENGTH = 12


# ---- Safety gates ---------------------------------------------------------


def assert_dev_environment() -> None:
    """Refuse to run if the environment cannot be proven to be dev.

    Two checks:
      1. ``FLASK_ENV`` is ``development``.
      2. ``DATABASE_URL`` does not contain the substring ``prod`` in any
         case. This catches ``shekel-prod-db`` hostnames and any other
         url that was misdirected.
    """
    flask_env = os.getenv("FLASK_ENV", "")
    if flask_env != DEV_ENV_NAME:
        raise RuntimeError(
            f"Refusing to run: FLASK_ENV is {flask_env!r} "
            f"(must be {DEV_ENV_NAME!r}). This script is dev-only."
        )
    db_url = os.getenv("DATABASE_URL", "")
    if "prod" in db_url.lower():
        raise RuntimeError(
            f"Refusing to run: DATABASE_URL contains 'prod': {db_url!r}"
        )
    # Sanity: all hard-coded audit passwords must satisfy the app's
    # minimum length so register_user() accepts them.
    for pw in (OWNER_A_PASSWORD, OWNER_B_PASSWORD, COMPANION_C_PASSWORD):
        if len(pw) < MIN_PASSWORD_LENGTH:
            raise RuntimeError(
                f"Audit password shorter than {MIN_PASSWORD_LENGTH} "
                "characters -- update the constants."
            )


# ---- Ref-table lookups (not exposed via enums) ----------------------------


def _merit_raise_type_id() -> int:
    """Return the integer ID of the ``merit`` row in ref.raise_types."""
    row = db.session.query(RaiseType).filter_by(name="merit").one()
    return int(row.id)


def _single_filing_status_id() -> int:
    """Return the integer ID of the ``single`` row in ref.filing_statuses."""
    row = db.session.query(FilingStatus).filter_by(name="single").one()
    return int(row.id)


# ---- Cleanup --------------------------------------------------------------


def wipe_audit_users() -> None:
    """Delete any existing audit users so the seed produces fresh IDs.

    ``User.linked_owner_id`` is ``ON DELETE SET NULL``, so deleting the
    owner before the companion leaves the companion intact with a null
    link -- we then delete the companion in the same pass. All other
    user-owned rows cascade via ``ondelete='CASCADE'`` on their
    ``user_id`` FKs.
    """
    existing = (
        db.session.query(User).filter(User.email.in_(AUDIT_EMAILS)).all()
    )
    for user in existing:
        logger.info(
            "Deleting existing audit user %s (id=%s).", user.email, user.id,
        )
        db.session.delete(user)
    db.session.commit()


# ---- User creation --------------------------------------------------------


def create_owner(email: str, password: str, display_name: str) -> User:
    """Create an owner user via the application's register_user().

    Uses the live service to exercise the production onboarding path:
    creates the User, UserSettings, a default Checking Account, a
    Baseline Scenario, default categories, and tax configuration.
    """
    user = register_user(email, password, display_name)
    db.session.commit()
    return user


def create_companion(
    email: str, password: str, display_name: str, linked_owner_id: int,
) -> User:
    """Create a companion user linked to a specific owner.

    Companions have ``role_id`` set to the companion role and
    ``linked_owner_id`` set to the owner they share data with. A
    ``UserSettings`` row is also created so ``settings.show()`` never
    sees ``None`` on the companion's first login.
    """
    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    companion = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        role_id=companion_role_id,
        linked_owner_id=linked_owner_id,
        is_active=True,
    )
    db.session.add(companion)
    db.session.flush()
    db.session.add(UserSettings(user_id=companion.id))
    db.session.commit()
    return companion


# ---- ownerA resource creators ---------------------------------------------


def create_extra_accounts(user_id: int) -> dict[str, int]:
    """Create HYSA, Mortgage, and 401k accounts for ownerA.

    The default ``Checking`` account is created by ``register_user()``
    and is not re-created here. Returns a dict mapping short names to
    account IDs.
    """
    hysa = Account(
        user_id=user_id,
        account_type_id=ref_cache.acct_type_id(AcctTypeEnum.HYSA),
        name="Audit HYSA",
        current_anchor_balance=HYSA_BALANCE,
    )
    mortgage = Account(
        user_id=user_id,
        account_type_id=ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE),
        name="Audit Mortgage",
        current_anchor_balance=MORTGAGE_BALANCE,
    )
    investment = Account(
        user_id=user_id,
        account_type_id=ref_cache.acct_type_id(AcctTypeEnum.K401),
        name="Audit 401k",
        current_anchor_balance=INVESTMENT_BALANCE,
    )
    db.session.add_all([hysa, mortgage, investment])
    db.session.flush()
    return {
        "hysa": int(hysa.id),
        "mortgage": int(mortgage.id),
        "investment": int(investment.id),
    }


def create_account_params(account_ids: dict[str, int]) -> dict[str, int]:
    """Populate interest/loan/investment/escrow params for the extra accounts.

    Each row is the object that an HTMX partial or dashboard would hit
    when the probe follows ``/accounts/<id>/interest`` and similar
    routes. Returns a dict with the escrow component ID (other rows are
    unique-per-account and don't need their own probe ID).
    """
    interest = InterestParams(
        account_id=account_ids["hysa"],
        apy=INTEREST_APY,
        compounding_frequency="daily",
    )
    loan = LoanParams(
        account_id=account_ids["mortgage"],
        original_principal=MORTGAGE_BALANCE,
        current_principal=MORTGAGE_BALANCE,
        interest_rate=LOAN_RATE,
        term_months=LOAN_TERM_MONTHS,
        origination_date=date(2024, 1, 1),
        payment_day=1,
    )
    escrow = EscrowComponent(
        account_id=account_ids["mortgage"],
        name="Audit Property Tax",
        annual_amount=ESCROW_ANNUAL,
    )
    investment = InvestmentParams(
        account_id=account_ids["investment"],
        assumed_annual_return=Decimal("0.07000"),
        employer_contribution_type="none",
    )
    db.session.add_all([interest, loan, escrow, investment])
    db.session.flush()
    return {"escrow_component": int(escrow.id)}


def create_pay_periods(user_id: int) -> list[int]:
    """Create ``PAY_PERIOD_COUNT`` biweekly PayPeriod rows for the user.

    The first period starts 14 days before today so it spans now;
    subsequent periods are forward-sequential. Period indices start at
    1 so the rows sort in date order when viewed in the grid.
    """
    today = date.today()
    start = today - timedelta(days=PAY_PERIOD_DAYS)
    periods: list[PayPeriod] = []
    for idx in range(PAY_PERIOD_COUNT):
        period_start = start + timedelta(days=PAY_PERIOD_DAYS * idx)
        period_end = period_start + timedelta(days=PAY_PERIOD_DAYS - 1)
        periods.append(
            PayPeriod(
                user_id=user_id,
                start_date=period_start,
                end_date=period_end,
                period_index=idx + 1,
            )
        )
    db.session.add_all(periods)
    db.session.flush()
    return [int(p.id) for p in periods]


def _first_category_id(user_id: int) -> int:
    """Return the first default category ID for a freshly-created owner.

    ``register_user()`` inserts ~24 default categories; we just grab
    one for the template. The probe does not depend on which category
    -- it depends only on the existence of a category row that the
    user owns.
    """
    row = (
        db.session.query(Category)
        .filter_by(user_id=user_id)
        .order_by(Category.id.asc())
        .first()
    )
    if row is None:
        raise RuntimeError(
            f"No categories for user_id={user_id} -- register_user() failed?"
        )
    return int(row.id)


def create_salary_profile(
    user_id: int, scenario_id: int,
) -> dict[str, int]:
    """Create a SalaryProfile plus one SalaryRaise and one PaycheckDeduction.

    Returns IDs the probe uses for ``/salary/<profile_id>/*`` and the
    nested ``/salary/raises/<raise_id>/*`` and
    ``/salary/deductions/<ded_id>/*`` routes.
    """
    profile = SalaryProfile(
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=_single_filing_status_id(),
        name="Audit Salary",
        annual_salary=SALARY_ANNUAL,
    )
    db.session.add(profile)
    db.session.flush()

    bump = SalaryRaise(
        salary_profile_id=profile.id,
        raise_type_id=_merit_raise_type_id(),
        effective_month=6,
        effective_year=date.today().year + 1,
        percentage=Decimal("0.03000"),
    )
    deduction = PaycheckDeduction(
        salary_profile_id=profile.id,
        deduction_timing_id=ref_cache.deduction_timing_id(
            DeductionTimingEnum.PRE_TAX,
        ),
        calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
        name="Audit Health Insurance",
        amount=Decimal("150.00"),
        deductions_per_year=26,
    )
    db.session.add_all([bump, deduction])
    db.session.flush()
    return {
        "salary_profile": int(profile.id),
        "raise": int(bump.id),
        "deduction": int(deduction.id),
    }


def create_savings_goal(user_id: int, account_id: int) -> int:
    """Create one fixed-mode SavingsGoal linked to ownerA's HYSA."""
    goal = SavingsGoal(
        user_id=user_id,
        account_id=account_id,
        name="Audit Emergency Fund",
        target_amount=SAVINGS_GOAL_TARGET,
        goal_mode_id=ref_cache.goal_mode_id(GoalModeEnum.FIXED),
    )
    db.session.add(goal)
    db.session.flush()
    return int(goal.id)


def create_pension(user_id: int) -> int:
    """Create one active PensionProfile for ownerA."""
    pension = PensionProfile(
        user_id=user_id,
        name="Audit Pension",
        benefit_multiplier=PENSION_MULTIPLIER,
        consecutive_high_years=4,
        hire_date=date(2020, 1, 1),
    )
    db.session.add(pension)
    db.session.flush()
    return int(pension.id)


def create_template_and_transactions(
    user_id: int,
    account_id: int,
    scenario_id: int,
    period_ids: list[int],
) -> dict[str, Any]:
    """Create a RecurrenceRule, TransactionTemplate, and 3 Transactions.

    Also creates one TransactionEntry on the first transaction so the
    entries routes have an ``entry_id`` to probe. Returns template ID,
    transaction IDs (one per period), and entry ID.
    """
    rule = RecurrenceRule(
        user_id=user_id,
        pattern_id=ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.EVERY_PERIOD,
        ),
    )
    db.session.add(rule)
    db.session.flush()

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    category_id = _first_category_id(user_id)

    template = TransactionTemplate(
        user_id=user_id,
        account_id=account_id,
        category_id=category_id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type_id,
        name="Audit Recurring Expense",
        default_amount=TEMPLATE_AMOUNT,
        track_individual_purchases=True,
    )
    db.session.add(template)
    db.session.flush()

    projected_status_id = ref_cache.status_id(StatusEnum.PROJECTED)
    txn_ids: list[int] = []
    for period_id in period_ids:
        txn = Transaction(
            account_id=account_id,
            template_id=template.id,
            pay_period_id=period_id,
            scenario_id=scenario_id,
            status_id=projected_status_id,
            name="Audit Recurring Expense",
            category_id=category_id,
            transaction_type_id=expense_type_id,
            estimated_amount=TEMPLATE_AMOUNT,
        )
        db.session.add(txn)
        db.session.flush()
        txn_ids.append(int(txn.id))

    # One TransactionEntry on the first transaction so entry routes
    # have an ``entry_id`` to probe.
    entry = TransactionEntry(
        transaction_id=txn_ids[0],
        user_id=user_id,
        amount=ENTRY_AMOUNT,
        description="Audit probe entry",
        entry_date=date.today(),
    )
    db.session.add(entry)
    db.session.flush()

    return {
        "template": int(template.id),
        "transactions": txn_ids,
        "entry": int(entry.id),
    }


def create_transfer_pair(
    user_id: int,
    from_account_id: int,
    to_account_id: int,
    scenario_id: int,
    period_id: int,
) -> dict[str, int]:
    """Create a TransferTemplate and one Transfer instance.

    The Transfer's shadow Transactions are NOT created -- the probe
    targets the Transfer and TransferTemplate IDs directly, not the
    shadow transactions. If a route depends on shadow transactions
    and 500s on a bare Transfer, that is recorded in the probe output
    as a coverage gap, not a seeding error.
    """
    rule = RecurrenceRule(
        user_id=user_id,
        pattern_id=ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.EVERY_PERIOD,
        ),
    )
    db.session.add(rule)
    db.session.flush()

    tpl = TransferTemplate(
        user_id=user_id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        recurrence_rule_id=rule.id,
        name="Audit Savings Transfer",
        default_amount=TRANSFER_AMOUNT,
    )
    db.session.add(tpl)
    db.session.flush()

    projected_status_id = ref_cache.status_id(StatusEnum.PROJECTED)
    transfer = Transfer(
        user_id=user_id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        pay_period_id=period_id,
        scenario_id=scenario_id,
        status_id=projected_status_id,
        transfer_template_id=tpl.id,
        name="Audit Savings Transfer",
        amount=TRANSFER_AMOUNT,
    )
    db.session.add(transfer)
    db.session.flush()
    return {
        "transfer_template": int(tpl.id),
        "transfer": int(transfer.id),
    }


def _scenario_id_for(user_id: int) -> int:
    """Return the baseline scenario ID that ``register_user()`` created."""
    row = (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .one()
    )
    return int(row.id)


def _default_checking_id(user_id: int) -> int:
    """Return the default Checking account ID created by register_user()."""
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    row = (
        db.session.query(Account)
        .filter_by(user_id=user_id, account_type_id=checking_type_id)
        .order_by(Account.id.asc())
        .first()
    )
    if row is None:
        raise RuntimeError(
            f"register_user() did not create a Checking account for "
            f"user_id={user_id}."
        )
    return int(row.id)


def seed_owner_a_resources(user_id: int) -> dict[str, Any]:
    """Create every extra resource ownerA needs for full probe coverage.

    Sequenced in FK order: accounts → params → periods → salary →
    savings/pension → template + transactions (+ entry) → transfer pair.
    Returns a dict of all integer IDs the probe will need.
    """
    scenario_id = _scenario_id_for(user_id)
    checking_id = _default_checking_id(user_id)

    account_ids = create_extra_accounts(user_id)
    param_ids = create_account_params(account_ids)
    period_ids = create_pay_periods(user_id)
    salary_ids = create_salary_profile(user_id, scenario_id)
    savings_goal_id = create_savings_goal(user_id, account_ids["hysa"])
    pension_id = create_pension(user_id)
    txn_ids = create_template_and_transactions(
        user_id, checking_id, scenario_id, period_ids,
    )
    transfer_ids = create_transfer_pair(
        user_id,
        from_account_id=checking_id,
        to_account_id=account_ids["hysa"],
        scenario_id=scenario_id,
        period_id=period_ids[0],
    )

    db.session.commit()

    return {
        "user_id": user_id,
        "scenario_id": scenario_id,
        "checking_account_id": checking_id,
        "hysa_account_id": account_ids["hysa"],
        "mortgage_account_id": account_ids["mortgage"],
        "investment_account_id": account_ids["investment"],
        "escrow_component_id": param_ids["escrow_component"],
        "pay_period_ids": period_ids,
        "salary_profile_id": salary_ids["salary_profile"],
        "raise_id": salary_ids["raise"],
        "deduction_id": salary_ids["deduction"],
        "savings_goal_id": savings_goal_id,
        "pension_id": pension_id,
        "template_id": txn_ids["template"],
        "transaction_ids": txn_ids["transactions"],
        "entry_id": txn_ids["entry"],
        "transfer_template_id": transfer_ids["transfer_template"],
        "transfer_id": transfer_ids["transfer"],
    }


# ---- Category IDs for ownerA (for /categories/<id>/* probes) --------------


def collect_owner_category_ids(user_id: int) -> list[int]:
    """Return the integer IDs of all categories ownerA owns.

    ``register_user()`` creates ~24 default categories. The probe picks
    one to exercise each /categories/<id>/* route; having the full list
    in the credentials file makes the probe resilient to seed churn.
    """
    rows = (
        db.session.query(Category)
        .filter_by(user_id=user_id)
        .order_by(Category.id.asc())
        .all()
    )
    return [int(r.id) for r in rows]


# ---- Companion target ID (for /settings/companions/<id>/* probes) ---------


def collect_other_companions_of_owner_b(owner_b_id: int) -> int | None:
    """Return the ID of a companion linked to ownerB, if one exists.

    The probe needs one companion_id to target for the
    ``/settings/companions/<id>/*`` routes. Since companionC is linked
    to ownerA (not ownerB), there is no companion for ownerB in the
    default seed -- this function returns None in that case. The
    probe treats the settings/companions routes as an IDOR target
    using companionC's own ID (ownerA's companion) against ownerB's
    session, which is the correct attack pattern anyway.
    """
    row = (
        db.session.query(User)
        .filter_by(linked_owner_id=owner_b_id)
        .first()
    )
    return None if row is None else int(row.id)


# ---- Main orchestration ---------------------------------------------------


def seed_everything(credentials_out: Path) -> dict[str, Any]:
    """Run the full seed flow inside an app context and write credentials.

    Steps: wipe existing audit users, create ownerA and ownerB via the
    register service, create companionC directly, create ownerA's
    extra resources, and write the credentials/IDs file.
    """
    assert_dev_environment()
    app = create_app(DEV_ENV_NAME)
    with app.app_context():
        wipe_audit_users()

        owner_a = create_owner(
            OWNER_A_EMAIL, OWNER_A_PASSWORD, OWNER_A_DISPLAY,
        )
        owner_b = create_owner(
            OWNER_B_EMAIL, OWNER_B_PASSWORD, OWNER_B_DISPLAY,
        )
        companion_c = create_companion(
            COMPANION_C_EMAIL,
            COMPANION_C_PASSWORD,
            COMPANION_C_DISPLAY,
            linked_owner_id=int(owner_a.id),
        )

        owner_a_resources = seed_owner_a_resources(int(owner_a.id))
        owner_a_category_ids = collect_owner_category_ids(int(owner_a.id))

        payload: dict[str, Any] = {
            "schema_version": 1,
            "users": {
                "ownerA": {
                    "user_id": int(owner_a.id),
                    "email": OWNER_A_EMAIL,
                    "password": OWNER_A_PASSWORD,
                    "role": "owner",
                },
                "ownerB": {
                    "user_id": int(owner_b.id),
                    "email": OWNER_B_EMAIL,
                    "password": OWNER_B_PASSWORD,
                    "role": "owner",
                },
                "companionC": {
                    "user_id": int(companion_c.id),
                    "email": COMPANION_C_EMAIL,
                    "password": COMPANION_C_PASSWORD,
                    "role": "companion",
                    "linked_owner_id": int(owner_a.id),
                },
            },
            "ownerA_resources": {
                **owner_a_resources,
                "category_ids": owner_a_category_ids,
            },
        }

    atomic_write_json(credentials_out, payload)
    logger.info("Wrote credentials file: %s", credentials_out)
    return payload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Build and parse the CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Seed deterministic DAST audit users and ownerA resources "
            "for the IDOR probe. Dev-only."
        ),
    )
    parser.add_argument(
        "--credentials-out",
        type=Path,
        required=True,
        help=(
            "Path where the JSON credentials file is written. Must be "
            "writable. The file contains the seeded users' passwords "
            "and ownerA's resource IDs for the probe to consume."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Script entry point.

    Returns the process exit code. Zero on success, non-zero on any
    safety-gate failure or seed error.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        seed_everything(args.credentials_out)
    except RuntimeError as exc:
        logger.error("Seed refused or failed: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
