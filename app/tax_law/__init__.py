"""
Shekel Budget App -- The Tax Law

**The ONE copy of the tax law the app prices against** (ruling
**salary:R-SAL74**, plan step **salary:X-at-1**): federal income tax per filing
status, Social Security and Medicare, and each supported state's income tax,
one module per tax year, each naming the documents it was transcribed from.

It replaced a copy PER USER.  Signup copied the seed constants into five
``salary`` tables, every deploy copied any rows a user lacked, and the Settings
page could overwrite a user's state and FICA rows -- so the law lived once in
the code and once per user in the database, kept equal by nothing.  (On
production, 2026-09-24, both users' 2025 and 2026 copies of the bracket sets
and their ladders, the child deduction tiers and the FICA rows equalled the
code field for field, and the state rows' tax type did; the state rate and
deduction were graded by re-pricing instead: every projected paycheck and the
Taxes tab, whose figures read them, priced from the code as they did from the
copies, and a planted North Carolina rate moved the result.)  A copy cannot
reach a year its owner never received, and a correction to the code never
reached a row already copied.  Now nothing in the app writes the law: a new year, or a
correction, is a release that edits these modules.

**Adding a tax year** is a new ``_year_<YYYY>.py`` beside the others, listed
in :data:`LAW` below.  Its federal rules must cover every filing status and it
must cite its sources, or importing it fails (:mod:`app.tax_law._types`); the
bracket ladders are checked for gaps.  Which year's law prices a given year is
the resolver's rule, :func:`app.services.tax_config_service.resolve_tax_year`,
not this package's.
"""

from app.tax_law._types import (
    Bracket,
    ChildDeductionTier,
    FederalRules,
    FicaRules,
    StateYearLaw,
    TaxLaw,
    TaxYearLaw,
    ladder,
)
from app.tax_law._year_2025 import YEAR_2025
from app.tax_law._year_2026 import YEAR_2026

#: Every tax year the app carries, oldest first.
LAW = TaxLaw(years=(YEAR_2025, YEAR_2026))

__all__ = [
    "LAW",
    "Bracket",
    "ChildDeductionTier",
    "FederalRules",
    "FicaRules",
    "StateYearLaw",
    "TaxLaw",
    "TaxYearLaw",
    "ladder",
]
