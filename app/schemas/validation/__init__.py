"""
Shekel Budget App -- Marshmallow Validation Schemas

Validates and deserializes incoming request data.  Used by routes
to keep controllers thin and push validation logic out of Flask.

Percent / decimal-rate convention (E-28 / HIGH-06, Commit 24)
-------------------------------------------------------------

Percentage rate fields (FICA, state flat rates, inflation, APY,
trend alert threshold, etc.) are stored as decimal fractions in the
database -- a 6.2% rate is persisted as ``Decimal("0.0620")`` in a
``Numeric(5, 4)`` or ``Numeric(7, 5)`` column with a database
CHECK constraint pinning the value to ``[0, 1]`` (and similar
ranges for the few "match-multiplier" cases).  The forms that
collect these values render the user-facing percent
(``0.0620 * 100 == 6.20``).

E-28 collapses the storage / schema / form domain split into one
consistent rule: **schemas validate the stored fraction**, and the
percent-to-fraction conversion happens in the schema's own
``@pre_load`` hook so the storage tier (DB CHECK) and the
validation tier (Marshmallow ``Range``) accept and reject exactly
the same set of values.  The shared helper
:func:`_normalize_percent_fields` divides each declared percent
field by 100 before the field's ``Range`` validator runs, and the
``_PERCENT_FIELDS`` class attribute on each schema enumerates the
fields that need conversion (an explicit declaration, not an
implicit name pattern, so a future renamer cannot silently break
the conversion).  Routes that consume one of these schemas no
longer divide -- the loaded value is already the fraction the DB
expects.

The DB CHECK constraints added by commit C-24 of the 2026-04-15
security remediation plan are the storage-tier counterpart to these
validators; they are deliberately not redundant because they catch
raw-SQL bypasses of the route layer.  See
``migrations/versions/<C-24>_marshmallow_range_check_sweep.py`` for
the storage-tier bounds and keep both sides in sync when adding a
new column.

Monetary range validators
-------------------------

Pure monetary fields (deduction amount, SalaryProfile W-4 fields,
TaxBracketSet credits, etc.) get ``Range(min=...)`` validators per
commit C-24.  The minimum mirrors the database CHECK (``>= 0`` or
``> 0`` per column); the maximum is set well below the column's
storage limit but above any plausible real-world value, so a typo
that injects an extra digit is rejected with a clean field-level
400 instead of being silently committed.

Package layout
--------------

The historical 2,937-line ``validation.py`` module is split (Phase-3
pylint cleanup) into per-domain sub-modules, mirroring the route
packages that consume them.  Every public schema is re-exported here so
``from app.schemas.validation import XSchema`` keeps working unchanged.
Shared primitives (base schema, range validators, percent-conversion
helper, envelope-on-income rule) live in :mod:`._helpers`.
"""

from app.schemas.validation.accounts import (
    AccountCreateSchema,
    AccountTypeCreateSchema,
    AccountTypeUpdateSchema,
    AccountUpdateSchema,
    AnchorUpdateSchema,
    InterestParamsCreateSchema,
    InterestParamsUpdateSchema,
)
from app.schemas.validation.auth import (
    ChangePasswordSchema,
    CompanionCreateSchema,
    CompanionEditSchema,
    LoginSchema,
    MfaConfirmSchema,
    MfaDisableSchema,
    MfaVerifySchema,
    ReauthSchema,
    RegisterSchema,
)
from app.schemas.validation.categories import (
    CategoryCreateSchema,
    CategoryEditSchema,
)
from app.schemas.validation.debt_strategy import DebtStrategyCalculateSchema
from app.schemas.validation.entries import (
    EntryCreateSchema,
    EntryUpdateSchema,
)
from app.schemas.validation.investments import (
    InvestmentContributionTransferSchema,
    InvestmentParamsCreateSchema,
    InvestmentParamsUpdateSchema,
)
from app.schemas.validation.loans import (
    EscrowComponentSchema,
    LoanAnchorTrueupSchema,
    LoanParamsCreateSchema,
    LoanParamsUpdateSchema,
    LoanPaymentTransferSchema,
    PayoffCalculatorSchema,
    RateChangeSchema,
    RefinanceSchema,
)
from app.schemas.validation.pay_periods import (
    PayPeriodExtendSchema,
    PayPeriodGenerateSchema,
    PayPeriodRegenerateSchema,
    PayPeriodTruncateSchema,
    PayScheduleSchema,
)
from app.schemas.validation.retirement import (
    PensionProfileCreateSchema,
    PensionProfileUpdateSchema,
    RetirementGapQuerySchema,
    RetirementSettingsSchema,
)
from app.schemas.validation.salary import (
    CalibrationConfirmSchema,
    CalibrationSchema,
    DeductionCreateSchema,
    DeductionUpdateSchema,
    FicaConfigSchema,
    RaiseCreateSchema,
    RaiseUpdateSchema,
    SalaryProfileCreateSchema,
    SalaryProfileUpdateSchema,
    StateTaxConfigSchema,
    TaxBracketSetSchema,
)
from app.schemas.validation.savings import (
    SavingsGoalCreateSchema,
    SavingsGoalUpdateSchema,
)
from app.schemas.validation.settings import UserSettingsSchema
from app.schemas.validation.templates import (
    TemplateCreateSchema,
    TemplateUpdateSchema,
)
from app.schemas.validation.transactions import (
    InlineTransactionCreateSchema,
    MarkDoneSchema,
    TransactionCreateSchema,
    TransactionUpdateSchema,
)
from app.schemas.validation.transfers import (
    TransferCreateSchema,
    TransferTemplateCreateSchema,
    TransferTemplateUpdateSchema,
    TransferUpdateSchema,
)

__all__ = [
    "AccountCreateSchema",
    "AccountTypeCreateSchema",
    "AccountTypeUpdateSchema",
    "AccountUpdateSchema",
    "AnchorUpdateSchema",
    "CalibrationConfirmSchema",
    "CalibrationSchema",
    "CategoryCreateSchema",
    "CategoryEditSchema",
    "ChangePasswordSchema",
    "CompanionCreateSchema",
    "CompanionEditSchema",
    "DebtStrategyCalculateSchema",
    "DeductionCreateSchema",
    "DeductionUpdateSchema",
    "EntryCreateSchema",
    "EntryUpdateSchema",
    "EscrowComponentSchema",
    "FicaConfigSchema",
    "InlineTransactionCreateSchema",
    "InterestParamsCreateSchema",
    "InterestParamsUpdateSchema",
    "InvestmentContributionTransferSchema",
    "InvestmentParamsCreateSchema",
    "InvestmentParamsUpdateSchema",
    "LoanAnchorTrueupSchema",
    "LoanParamsCreateSchema",
    "LoanParamsUpdateSchema",
    "LoanPaymentTransferSchema",
    "LoginSchema",
    "MarkDoneSchema",
    "MfaConfirmSchema",
    "MfaDisableSchema",
    "MfaVerifySchema",
    "PayPeriodExtendSchema",
    "PayPeriodGenerateSchema",
    "PayPeriodRegenerateSchema",
    "PayPeriodTruncateSchema",
    "PayScheduleSchema",
    "PayoffCalculatorSchema",
    "PensionProfileCreateSchema",
    "PensionProfileUpdateSchema",
    "RaiseCreateSchema",
    "RaiseUpdateSchema",
    "RateChangeSchema",
    "ReauthSchema",
    "RefinanceSchema",
    "RegisterSchema",
    "RetirementGapQuerySchema",
    "RetirementSettingsSchema",
    "SalaryProfileCreateSchema",
    "SalaryProfileUpdateSchema",
    "SavingsGoalCreateSchema",
    "SavingsGoalUpdateSchema",
    "StateTaxConfigSchema",
    "TaxBracketSetSchema",
    "TemplateCreateSchema",
    "TemplateUpdateSchema",
    "TransactionCreateSchema",
    "TransactionUpdateSchema",
    "TransferCreateSchema",
    "TransferTemplateCreateSchema",
    "TransferTemplateUpdateSchema",
    "TransferUpdateSchema",
    "UserSettingsSchema",
]
