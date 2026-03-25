"""
Shekel Budget App -- Model Registry

All models are imported here so that Alembic's autogenerate can
discover them.  Import order matters: ref tables first, then auth,
then budget (to satisfy foreign key dependencies).
"""

# pylint: disable=unused-import

# Reference / lookup tables
from app.models.ref import (
    AccountType,
    CalcMethod,
    DeductionTiming,
    FilingStatus,
    RaiseType,
    RecurrencePattern,
    Status,
    TaxType,
    TransactionType,
)

# Authentication
from app.models.user import MfaConfig, User, UserSettings

# Budget domain
from app.models.pay_period import PayPeriod
from app.models.account import Account, AccountAnchorHistory
from app.models.category import Category
from app.models.recurrence_rule import RecurrenceRule
from app.models.scenario import Scenario
from app.models.transaction_template import TransactionTemplate
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.savings_goal import SavingsGoal
from app.models.hysa_params import HysaParams
from app.models.mortgage_params import MortgageParams, MortgageRateHistory, EscrowComponent
from app.models.auto_loan_params import AutoLoanParams
from app.models.investment_params import InvestmentParams

# Salary domain
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.pension_profile import PensionProfile
from app.models.calibration_override import (
    CalibrationOverride,
    CalibrationDeductionOverride,
)
from app.models.tax_config import (
    FicaConfig,
    StateTaxConfig,
    TaxBracket,
    TaxBracketSet,
)
