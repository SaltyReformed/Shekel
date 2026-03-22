"""
Shekel Budget App -- Escrow Calculator

Pure-function service for mortgage escrow calculations.
No database access -- operates only on values passed in.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")


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
                years_elapsed = (as_of_date.year - created.year)
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
