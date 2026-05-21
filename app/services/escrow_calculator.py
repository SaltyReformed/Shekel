"""
Shekel Budget App -- Escrow Calculator

Pure-function service for mortgage escrow calculations.
No database access -- operates only on values passed in.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class EscrowComponentDisplay:
    """Display DTO for one escrow component (MED-04 / E-16).

    Carries both the stored annual amount and the derived per-period
    monthly amount so the Jinja template renders without inline
    arithmetic.  The monthly amount is rounded once, here, with the
    project default ROUND_HALF_UP -- the previous template-resident
    ``comp.annual_amount|float / 12`` introduced a binary-float cast
    on a Decimal before the divide, masking precision drift behind
    the formatter.

    ``inflation_rate`` is the storage-domain decimal fraction (e.g.
    ``Decimal("0.03")`` for 3 %); ``inflation_rate_pct`` is the same
    value multiplied by 100 for display, kept here so the template
    does not multiply rates inline either (E-16 applies to
    rate-arithmetic as much as to dollar-arithmetic).
    """

    id: int
    name: str
    annual_amount: Decimal
    monthly_amount: Decimal
    inflation_rate: Decimal | None
    inflation_rate_pct: Decimal | None


def build_escrow_display(components: list) -> list[EscrowComponentDisplay]:
    """Build display DTOs for the escrow components list (MED-04 / E-16).

    Filters inactive components (mirroring
    :func:`calculate_monthly_escrow`'s gate) and computes the per-
    component monthly amount as ``annual / 12`` rounded HALF_UP so
    every row's monthly value matches the rule the aggregate
    ``calculate_monthly_escrow`` uses.

    Args:
        components: Iterable of escrow components with ``.id``,
            ``.name``, ``.annual_amount``, ``.is_active``, and
            optionally ``.inflation_rate``.

    Returns:
        List of :class:`EscrowComponentDisplay` ordered as ``components``.
        Inactive components are skipped.
    """
    rows: list[EscrowComponentDisplay] = []
    for comp in components:
        if hasattr(comp, "is_active") and not comp.is_active:
            continue
        annual = Decimal(str(comp.annual_amount))
        monthly = (annual / 12).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        if getattr(comp, "inflation_rate", None) is not None:
            inflation = Decimal(str(comp.inflation_rate))
            inflation_pct = inflation * Decimal("100")
        else:
            inflation = None
            inflation_pct = None
        rows.append(EscrowComponentDisplay(
            id=comp.id,
            name=comp.name,
            annual_amount=annual,
            monthly_amount=monthly,
            inflation_rate=inflation,
            inflation_rate_pct=inflation_pct,
        ))
    return rows


def calculate_monthly_escrow(components: list, as_of_date: date | None = None) -> Decimal:
    """Sum active escrow components' annual amounts / 12.

    Args:
        components: List of objects with .annual_amount, .is_active,
                    and optionally .inflation_rate, .created_at.
        as_of_date: If provided, applies inflation from component
                    created_at to as_of_date.

    Returns:
        Monthly escrow amount rounded to 2 decimal places.
    """
    total = Decimal("0.00")

    for comp in components:
        if hasattr(comp, "is_active") and not comp.is_active:
            continue

        annual = Decimal(str(comp.annual_amount))

        # Apply inflation if both as_of_date and inflation_rate are present.
        if as_of_date and hasattr(comp, "inflation_rate") and comp.inflation_rate:
            rate = Decimal(str(comp.inflation_rate))
            created = getattr(comp, "created_at", None)
            if created:
                if hasattr(created, "date"):
                    created = created.date()
                # Month-aware elapsed calculation prevents inflating
                # a full year for a component created late in the
                # previous calendar year (M-05).
                months_elapsed = (
                    (as_of_date.year - created.year) * 12
                    + (as_of_date.month - created.month)
                )
                years_elapsed = max(
                    months_elapsed / Decimal("12"), Decimal("0")
                )
                if years_elapsed > 0:
                    annual = annual * (1 + rate) ** years_elapsed

        monthly = annual / 12
        total += monthly

    return total.quantize(TWO_PLACES, ROUND_HALF_UP)


def calculate_total_payment(
    monthly_pi: Decimal,
    components: list,
    as_of_date: date | None = None,
) -> Decimal:
    """P&I + monthly escrow = total monthly payment.

    Args:
        monthly_pi: Monthly principal & interest payment.
        components: Escrow components for the account.
        as_of_date: Optional date for inflation adjustment.

    Returns:
        Total monthly payment (P&I + escrow).
    """
    escrow = calculate_monthly_escrow(components, as_of_date)
    return (monthly_pi + escrow).quantize(TWO_PLACES, ROUND_HALF_UP)


def project_annual_escrow(
    components: list,
    years_forward: int,
    base_year: int,
) -> list[tuple[int, Decimal]]:
    """Project escrow totals per year with per-component inflation.

    Args:
        components: Escrow components with .annual_amount, .is_active,
                    and optionally .inflation_rate.
        years_forward: Number of years to project.
        base_year: The starting year for projections.

    Returns:
        List of (year, annual_amount) tuples.
    """
    results = []

    for year_offset in range(years_forward):
        year = base_year + year_offset
        annual_total = Decimal("0.00")

        for comp in components:
            if hasattr(comp, "is_active") and not comp.is_active:
                continue

            annual = Decimal(str(comp.annual_amount))

            if hasattr(comp, "inflation_rate") and comp.inflation_rate and year_offset > 0:
                rate = Decimal(str(comp.inflation_rate))
                annual = annual * (1 + rate) ** year_offset

            annual_total += annual.quantize(TWO_PLACES, ROUND_HALF_UP)

        results.append((year, annual_total.quantize(TWO_PLACES, ROUND_HALF_UP)))

    return results
