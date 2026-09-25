"""The 2025 tax law, transcribed from the documents :data:`SOURCES` names.

**One figure here is known to be superseded, and it stands as transcribed.**
P.L. 119-21 (signed 2025-07-04) raised the 2025 federal standard deduction to
$15,750 (single and married filing separately), $31,500 (married filing
jointly) and $23,625 (head of household); the figures below are Rev. Proc.
2024-40 sec. 3.15's $15,000 / $30,000 / $22,500, which is what the app has
priced 2025 on since it was first seeded.  Plan step salary:X-at-1 moved the
law into this module without changing a figure, so the correction -- which
moves every 2025 liability for a bracket-priced filer -- is finding
**salary:SAL-576**'s, not a side effect of the move.
"""

from decimal import Decimal

from app.enums import FilingStatusEnum
from app.tax_law import _north_carolina
from app.tax_law._types import (
    FederalRules,
    FicaRules,
    TaxYearLaw,
    ladder,
)

SOURCES = (
    "IRS Rev. Proc. 2024-40, sec. 3.01 (tax rate tables) and sec. 3.15 "
    "(standard deduction)",
    "P.L. 119-21 sec. 70104 (child tax credit $2,200 per child)",
    "2025 Schedule 8812 instructions (refundable child tax credit $1,700 per "
    "child)",
    "IRC sec. 24(h)(4) (credit for other dependents $500)",
    "SSA 2025 COLA fact sheet (Social Security wage base $176,100)",
    "IRC sec. 3101(a) and (b)(1) (Social Security 6.2%, Medicare 1.45%); "
    "sec. 3102(f) (Additional Medicare 0.9% withheld on wages over $200,000)",
    "N.C.G.S. 105-153.7(a) (North Carolina flat rate 4.25% for 2025)",
    "N.C.G.S. 105-153.5(a)(1) and (a1) (North Carolina standard and child "
    "deductions)",
)

_FEDERAL = {
    FilingStatusEnum.SINGLE: FederalRules(
        standard_deduction=Decimal("15000.00"),
        child_credit_amount=Decimal("2200.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1700.00"),
        brackets=ladder(
            ("0.00", "11925.00", "0.1000"),
            ("11925.00", "48475.00", "0.1200"),
            ("48475.00", "103350.00", "0.2200"),
            ("103350.00", "197300.00", "0.2400"),
            ("197300.00", "250525.00", "0.3200"),
            ("250525.00", "626350.00", "0.3500"),
            ("626350.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.MARRIED_JOINTLY: FederalRules(
        standard_deduction=Decimal("30000.00"),
        child_credit_amount=Decimal("2200.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1700.00"),
        brackets=ladder(
            ("0.00", "23850.00", "0.1000"),
            ("23850.00", "96950.00", "0.1200"),
            ("96950.00", "206700.00", "0.2200"),
            ("206700.00", "394600.00", "0.2400"),
            ("394600.00", "501050.00", "0.3200"),
            ("501050.00", "751600.00", "0.3500"),
            ("751600.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.MARRIED_SEPARATELY: FederalRules(
        standard_deduction=Decimal("15000.00"),
        child_credit_amount=Decimal("2200.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1700.00"),
        brackets=ladder(
            ("0.00", "11925.00", "0.1000"),
            ("11925.00", "48475.00", "0.1200"),
            ("48475.00", "103350.00", "0.2200"),
            ("103350.00", "197300.00", "0.2400"),
            ("197300.00", "250525.00", "0.3200"),
            ("250525.00", "375800.00", "0.3500"),
            ("375800.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.HEAD_OF_HOUSEHOLD: FederalRules(
        standard_deduction=Decimal("22500.00"),
        child_credit_amount=Decimal("2200.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1700.00"),
        brackets=ladder(
            ("0.00", "17000.00", "0.1000"),
            ("17000.00", "64850.00", "0.1200"),
            ("64850.00", "103350.00", "0.2200"),
            ("103350.00", "197300.00", "0.2400"),
            ("197300.00", "250500.00", "0.3200"),
            ("250500.00", "626350.00", "0.3500"),
            ("626350.00", None, "0.3700"),
        ),
    ),
}

_FICA = FicaRules(
    ss_rate=Decimal("0.0620"),
    ss_wage_base=Decimal("176100.00"),
    medicare_rate=Decimal("0.0145"),
    medicare_surtax_rate=Decimal("0.0090"),
    medicare_surtax_threshold=Decimal("200000.00"),
)

YEAR_2025 = TaxYearLaw(
    tax_year=2025,
    sources=SOURCES,
    federal=_FEDERAL,
    fica=_FICA,
    states={"NC": _north_carolina.state_year("0.0425")},
)
