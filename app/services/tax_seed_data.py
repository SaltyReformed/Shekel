"""
Shekel Budget App -- Tax Seed Data and Row Builders

The primary-source-verified default federal / state tax constants a new
user is seeded with, plus the pure ``build_*`` helpers that map a defaults
entry to an un-added SQLAlchemy row.  Extracted from ``auth_service`` (which
had reached its module-size ceiling) so the seed data and the sign-up path
live apart; the builders are shared verbatim by the sign-up path
(``auth_service._seed_tax_data_for_user``) and the idempotent repair script
``scripts/seed_tax_brackets.py`` so the two cannot drift on which keys feed
which columns.

No Flask, no ``db.session`` -- every builder returns an un-added model
instance and the caller owns ``session.add``/``flush``.

Constant provenance (all verified 2026-07 against primary sources for the
Taxes slice T-P5 follow-up):

* **Child Tax Credit $2,200/child (2025 and 2026)** -- OBBBA (P.L. 119-21
  sec. 70104) raised the maximum CTC to $2,200 for tax years beginning in
  2025 and made the TCJA expansion permanent; Rev. Proc. 2025-32 sec.
  4.05(1) confirms $2,200 for 2026.  Corrects the prior $2,000 seed.
* **Refundable ACTC cap $1,700/child (2025 and 2026)** -- 2025 Schedule
  8812 instructions; Rev. Proc. 2025-32 sec. 4.05(2).
* **NC standard deduction by filing status** -- N.C.G.S. 105-153.5(a)(1)
  (Single/MFS $12,750, MFJ $25,500, HoH $19,125).
* **NC per-child deduction tiers** -- N.C.G.S. 105-153.5(a1) / NCDOR D-401
  Child Deduction Table.
"""

from decimal import Decimal

from app.models.tax_config import (
    StateChildDeduction,
    StateTaxConfig,
    TaxBracket,
    TaxBracketSet,
)

# Federal income-tax defaults (see module docstring for provenance).  CTC is
# $2,200/child and the refundable ACTC cap $1,700/child for both years;
# other-dependent credit stays $500 (TCJA, not indexed).
DEFAULT_FEDERAL_BRACKETS = {
    2025: {
        "single": {
            "standard_deduction": Decimal("15000"),
            "child_credit_amount": Decimal("2200"),
            "other_dependent_credit_amount": Decimal("500"),
            "child_credit_refundable_cap": Decimal("1700"),
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
            "child_credit_amount": Decimal("2200"),
            "other_dependent_credit_amount": Decimal("500"),
            "child_credit_refundable_cap": Decimal("1700"),
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
            "child_credit_amount": Decimal("2200"),
            "other_dependent_credit_amount": Decimal("500"),
            "child_credit_refundable_cap": Decimal("1700"),
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
            "child_credit_amount": Decimal("2200"),
            "other_dependent_credit_amount": Decimal("500"),
            "child_credit_refundable_cap": Decimal("1700"),
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
    # 2026 brackets per IRS Rev. Proc. 2025-32 (One Big Beautiful Bill Act).
    2026: {
        "single": {
            "standard_deduction": Decimal("16100"),
            "child_credit_amount": Decimal("2200"),
            "other_dependent_credit_amount": Decimal("500"),
            "child_credit_refundable_cap": Decimal("1700"),
            "brackets": [
                (0, 12400, Decimal("0.1000")),
                (12400, 50400, Decimal("0.1200")),
                (50400, 105700, Decimal("0.2200")),
                (105700, 201775, Decimal("0.2400")),
                (201775, 256225, Decimal("0.3200")),
                (256225, 640600, Decimal("0.3500")),
                (640600, None, Decimal("0.3700")),
            ],
        },
        "married_jointly": {
            "standard_deduction": Decimal("32200"),
            "child_credit_amount": Decimal("2200"),
            "other_dependent_credit_amount": Decimal("500"),
            "child_credit_refundable_cap": Decimal("1700"),
            "brackets": [
                (0, 24800, Decimal("0.1000")),
                (24800, 100800, Decimal("0.1200")),
                (100800, 211400, Decimal("0.2200")),
                (211400, 403550, Decimal("0.2400")),
                (403550, 512450, Decimal("0.3200")),
                (512450, 768700, Decimal("0.3500")),
                (768700, None, Decimal("0.3700")),
            ],
        },
        "married_separately": {
            "standard_deduction": Decimal("16100"),
            "child_credit_amount": Decimal("2200"),
            "other_dependent_credit_amount": Decimal("500"),
            "child_credit_refundable_cap": Decimal("1700"),
            "brackets": [
                (0, 12400, Decimal("0.1000")),
                (12400, 50400, Decimal("0.1200")),
                (50400, 105700, Decimal("0.2200")),
                (105700, 201775, Decimal("0.2400")),
                (201775, 256225, Decimal("0.3200")),
                (256225, 384350, Decimal("0.3500")),
                (384350, None, Decimal("0.3700")),
            ],
        },
        "head_of_household": {
            "standard_deduction": Decimal("24150"),
            "child_credit_amount": Decimal("2200"),
            "other_dependent_credit_amount": Decimal("500"),
            "child_credit_refundable_cap": Decimal("1700"),
            "brackets": [
                (0, 17700, Decimal("0.1000")),
                (17700, 67450, Decimal("0.1200")),
                (67450, 105700, Decimal("0.2200")),
                (105700, 201775, Decimal("0.2400")),
                (201775, 256200, Decimal("0.3200")),
                (256200, 640600, Decimal("0.3500")),
                (640600, None, Decimal("0.3700")),
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
    # 2026 SS wage base per SSA announcement Oct 2025.
    2026: {
        "ss_rate": Decimal("0.0620"),
        "ss_wage_base": Decimal("184500"),
        "medicare_rate": Decimal("0.0145"),
        "medicare_surtax_rate": Decimal("0.0090"),
        "medicare_surtax_threshold": Decimal("200000"),
    },
}

# NC standard deduction by filing status (N.C.G.S. 105-153.5(a)(1)).  The
# former seed used the single-filer $12,750 for ALL statuses (finding 2b),
# understating MFJ and HoH.  These statutory amounts are not inflation-
# indexed, so 2026 uses the same enacted values (re-verify against the 2026
# D-401 when NCDOR posts it).  MFS $12,750 is the base value; the "$0 if
# spouse claims itemized deductions" edge case is not modeled (v1).
_NC_STANDARD_DEDUCTION_BY_STATUS = {
    "single": Decimal("12750"),
    "married_jointly": Decimal("25500"),
    "married_separately": Decimal("12750"),
    "head_of_household": Decimal("19125"),
}

# NC flat rate per N.C.G.S. 105-153.7(a): 4.25% for 2025, 3.99% "After 2025".
DEFAULT_STATE_TAX = {
    2025: {
        "state_code": "NC",
        "flat_rate": Decimal("0.0425"),
        "standard_deduction_by_status": _NC_STANDARD_DEDUCTION_BY_STATUS,
    },
    2026: {
        "state_code": "NC",
        "flat_rate": Decimal("0.0399"),
        "standard_deduction_by_status": _NC_STANDARD_DEDUCTION_BY_STATUS,
    },
}

# NC per-child deduction tiers (N.C.G.S. 105-153.5(a1)).  Each entry is
# ``(agi_min, agi_max, deduction_per_child)`` where the tier applies to AGI
# in ``(agi_min, agi_max]`` -- the statute reads "Up to $X" (inclusive) then
# "Over $X - Up to $Y", so a threshold value belongs to the LOWER tier.
# ``agi_max = None`` is the open-ended top tier (deduction $0).  Single and
# Married Filing Separately share identical tiers.  Amounts are per-child and
# not indexed, so 2025 and 2026 reuse the same table.
_NC_SINGLE_MFS_CHILD_TIERS = [
    (0, 20000, Decimal("3000")),
    (20000, 30000, Decimal("2500")),
    (30000, 40000, Decimal("2000")),
    (40000, 50000, Decimal("1500")),
    (50000, 60000, Decimal("1000")),
    (60000, 70000, Decimal("500")),
    (70000, None, Decimal("0")),
]
_NC_CHILD_DEDUCTION_TIERS = {
    "married_jointly": [
        (0, 40000, Decimal("3000")),
        (40000, 60000, Decimal("2500")),
        (60000, 80000, Decimal("2000")),
        (80000, 100000, Decimal("1500")),
        (100000, 120000, Decimal("1000")),
        (120000, 140000, Decimal("500")),
        (140000, None, Decimal("0")),
    ],
    "head_of_household": [
        (0, 30000, Decimal("3000")),
        (30000, 45000, Decimal("2500")),
        (45000, 60000, Decimal("2000")),
        (60000, 75000, Decimal("1500")),
        (75000, 90000, Decimal("1000")),
        (90000, 105000, Decimal("500")),
        (105000, None, Decimal("0")),
    ],
    "single": _NC_SINGLE_MFS_CHILD_TIERS,
    "married_separately": _NC_SINGLE_MFS_CHILD_TIERS,
}
DEFAULT_STATE_CHILD_DEDUCTIONS = {
    2025: {"state_code": "NC", "tiers_by_status": _NC_CHILD_DEDUCTION_TIERS},
    2026: {"state_code": "NC", "tiers_by_status": _NC_CHILD_DEDUCTION_TIERS},
}


def build_tax_bracket_set(
    user_id: int,
    filing_status_id: int,
    tax_year: int,
    status_name: str,
    data: dict[str, object],
) -> TaxBracketSet:
    """Build (not add) a TaxBracketSet row from a DEFAULT_FEDERAL_BRACKETS entry.

    The single dict-to-row mapping shared by the sign-up path
    (:func:`auth_service._seed_tax_data_for_user`) and the per-user repair
    script ``scripts/seed_tax_brackets.py``, so the two cannot drift on which
    keys feed which columns.  Keys are indexed directly -- a missing key is a
    defect in the defaults dict and must fail loud, not silently seed a zero
    credit or refundable cap.

    Args:
        user_id: The owning user's ID.
        filing_status_id: PK of the ``ref.filing_statuses`` row.
        tax_year: The bracket set's tax year.
        status_name: The filing-status key (e.g. ``"married_jointly"``),
            used only for the display description.
        data: One filing status's entry from
            :data:`DEFAULT_FEDERAL_BRACKETS` (standard_deduction, credit
            amounts, refundable cap, brackets).

    Returns:
        An un-added TaxBracketSet; the caller owns session.add/flush.
    """
    return TaxBracketSet(
        user_id=user_id,
        filing_status_id=filing_status_id,
        tax_year=tax_year,
        standard_deduction=data["standard_deduction"],
        child_credit_amount=data["child_credit_amount"],
        other_dependent_credit_amount=data["other_dependent_credit_amount"],
        child_credit_refundable_cap=data["child_credit_refundable_cap"],
        description=f"{tax_year} Federal - {status_name.replace('_', ' ').title()}",
    )


def build_tax_brackets(
    bracket_set_id: int,
    brackets: list[tuple[int, int | None, Decimal]],
) -> list[TaxBracket]:
    """Build (not add) the TaxBracket rows for one bracket set.

    Shared with ``scripts/seed_tax_brackets.py`` (see
    :func:`build_tax_bracket_set` for the rationale).  ``sort_order``
    is the tuple's position in the defaults list.

    Args:
        bracket_set_id: PK of the already-flushed parent TaxBracketSet.
        brackets: The ``"brackets"`` list from a
            :data:`DEFAULT_FEDERAL_BRACKETS` entry --
            ``(min_income, max_income_or_None, rate)`` tuples.

    Returns:
        Un-added TaxBracket rows; the caller owns session.add.
    """
    return [
        TaxBracket(
            bracket_set_id=bracket_set_id,
            min_income=Decimal(str(min_inc)),
            max_income=Decimal(str(max_inc)) if max_inc else None,
            rate=rate,
            sort_order=idx,
        )
        for idx, (min_inc, max_inc, rate) in enumerate(brackets)
    ]


def build_state_tax_configs(
    user_id: int,
    tax_type_id: int,
    tax_year: int,
    data: dict[str, object],
    filing_status_ids: dict[str, int],
) -> list[StateTaxConfig]:
    """Build (not add) one StateTaxConfig per filing status for a year.

    Shared with ``scripts/seed_tax_brackets.py`` (see
    :func:`build_tax_bracket_set` for the rationale).  Post-T-P5 the state
    config is keyed on filing status (the NC standard deduction is
    status-specific), so this builds ONE row per status in
    ``data["standard_deduction_by_status"]``; the flat rate and state code
    are status-independent.  A status absent from ``filing_status_ids`` (the
    ref table lacks it) is skipped rather than failing.

    Args:
        user_id: The owning user's ID.
        tax_type_id: PK of the ``ref.tax_types`` row (FLAT).
        tax_year: The config's tax year.
        data: One year's entry from :data:`DEFAULT_STATE_TAX`
            (``state_code``, ``flat_rate``, ``standard_deduction_by_status``).
        filing_status_ids: ``{status_name: ref.filing_statuses.id}``.

    Returns:
        Un-added StateTaxConfig rows (one per resolvable status); the caller
        owns session.add.
    """
    configs = []
    for status_name, standard_deduction in data["standard_deduction_by_status"].items():
        filing_status_id = filing_status_ids.get(status_name)
        if filing_status_id is None:
            continue
        configs.append(
            StateTaxConfig(
                user_id=user_id,
                tax_type_id=tax_type_id,
                filing_status_id=filing_status_id,
                tax_year=tax_year,
                state_code=data["state_code"],
                flat_rate=data["flat_rate"],
                standard_deduction=standard_deduction,
            )
        )
    return configs


def build_state_child_deductions(
    user_id: int,
    tax_year: int,
    data: dict[str, object],
    filing_status_ids: dict[str, int],
) -> list[StateChildDeduction]:
    """Build (not add) the per-child deduction tier rows for every status.

    Shared with ``scripts/seed_tax_brackets.py``.  Builds every tier row for
    every status in ``data["tiers_by_status"]``; each tier tuple is
    ``(agi_min, agi_max_or_None, deduction_per_child)``.  A status absent from
    ``filing_status_ids`` is skipped.

    Args:
        user_id: The owning user's ID.
        tax_year: The tier rows' tax year.
        data: One year's entry from :data:`DEFAULT_STATE_CHILD_DEDUCTIONS`
            (``state_code``, ``tiers_by_status``).
        filing_status_ids: ``{status_name: ref.filing_statuses.id}``.

    Returns:
        Un-added StateChildDeduction rows; the caller owns session.add.
    """
    rows = []
    for status_name, tiers in data["tiers_by_status"].items():
        filing_status_id = filing_status_ids.get(status_name)
        if filing_status_id is None:
            continue
        for agi_min, agi_max, per_child in tiers:
            rows.append(
                StateChildDeduction(
                    user_id=user_id,
                    filing_status_id=filing_status_id,
                    tax_year=tax_year,
                    state_code=data["state_code"],
                    agi_min=Decimal(str(agi_min)),
                    agi_max=Decimal(str(agi_max)) if agi_max is not None else None,
                    deduction_per_child=per_child,
                )
            )
    return rows
