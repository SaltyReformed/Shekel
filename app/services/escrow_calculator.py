"""
Shekel Budget App -- Escrow Calculator

Pure-function service for mortgage escrow calculations.
No database access -- operates only on values passed in.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.utils.dates import months_between
from app.utils.money import (
    CENTS,
    MONTHS_PER_YEAR,
    ZERO,
    round_money,
    round_money_floor,
)


@dataclass(frozen=True)
class EscrowComponentDisplay:
    """Display DTO for one escrow component (MED-04 / E-16).

    Carries both the stored annual amount and the derived per-period
    monthly amount so the Jinja template renders without inline
    arithmetic -- the previous template-resident
    ``comp.annual_amount|float / 12`` introduced a binary-float cast
    on a Decimal before the divide, masking precision drift behind
    the formatter.  The monthly amounts are cent-allocated across the
    component set (largest remainder, deep-hunt #17) so the rendered
    rows sum exactly to :func:`calculate_monthly_escrow`'s
    sum-then-round total -- see :func:`_allocate_monthly_amounts`.

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


@dataclass(frozen=True)
class ResolvedEscrowLine:
    """An escrow line resolved to the version in effect on a given date.

    The output of :func:`resolve_active_lines` and the input every "today's
    escrow" surface consumes -- the supersession model's answer to "what escrow
    does this line carry on date D."  ``id`` is the LINE's stable identity (not a
    version id): it is the target of the escrow tab's delete/edit control, so a
    rename or amount change never breaks the link.  ``annual_amount`` /
    ``inflation_rate`` come from the resolved version; ``created_at`` is that
    version's insert timestamp, carried solely for the forward-inflation display
    projection (:func:`calculate_monthly_escrow`'s ``as_of_date`` branch) so a
    resolved line drives the same "next year" note the legacy component did.

    Exposes exactly the attributes :func:`calculate_monthly_escrow` and
    :func:`build_escrow_display` read (``id``, ``name``, ``annual_amount``,
    ``inflation_rate``, ``created_at``), so the resolved set is a drop-in for the
    component list those functions took under the legacy model.
    """

    id: int
    name: str
    annual_amount: Decimal
    inflation_rate: Decimal | None
    created_at: datetime | None


def _allocate_monthly_amounts(annuals: list[Decimal]) -> list[Decimal]:
    """Cent-allocate the monthly escrow total across components (#17).

    Largest-remainder allocation: each component starts from its
    floored ``annual / 12`` and the leftover cents -- the difference
    between the sum of floors and the sum-then-rounded total -- go to
    the components with the largest fractional remainders (ties broken
    by input order; Python's sort is stable).  The result is per-row
    display values that each lie within one cent of the exact
    ``annual / 12`` AND sum exactly to the same total
    :func:`calculate_monthly_escrow` computes without ``as_of_date``
    -- so the escrow tab's rows always add up to its badge.  The
    aggregate's own sum-then-round rule (the E-26 boundary rounding
    feeding the loan payment) is untouched; only the per-row display
    split changes.

    Args:
        annuals: Full-precision annual amounts of the ACTIVE
            components, in display order.

    Returns:
        The per-component monthly display amounts, same order, summing
        to ``round_money(sum(annual / 12))``.
    """
    exacts = [annual / MONTHS_PER_YEAR for annual in annuals]
    total = round_money(sum(exacts, ZERO))
    bases = [round_money_floor(exact) for exact in exacts]
    remainder_cents = int((total - sum(bases, ZERO)) / CENTS)
    by_remainder = sorted(
        range(len(exacts)),
        key=lambda i: exacts[i] - bases[i],
        reverse=True,
    )
    monthlies = list(bases)
    for i in by_remainder[:remainder_cents]:
        monthlies[i] += CENTS
    return monthlies


def build_escrow_display(components: list) -> list[EscrowComponentDisplay]:
    """Build display DTOs for the escrow components list (MED-04 / E-16).

    Builds one display row per GIVEN component and cent-allocates the
    aggregate monthly total across them via :func:`_allocate_monthly_amounts`,
    so each row's monthly value is within one cent of its exact ``annual / 12``
    and the rows sum exactly to the badge total ``calculate_monthly_escrow``
    renders beside them (deep-hunt #17 -- per-row HALF_UP rounding made two
    $100/yr components display 8.33 + 8.33 = 16.66 against a 16.67 badge).

    Like :func:`calculate_monthly_escrow`, this does NOT filter by active state
    -- the caller supplies the set to display (the currently-active components,
    via :func:`app.services.loan_loaders.load_active_escrow_components`).
    Processing the identical set both functions receive keeps the rows-sum-to-
    badge invariant true for ANY input, rather than only when the caller happens
    to pre-filter removed components out (they both would otherwise diverge on a
    removed component -- rows omit it, badge counts it -- resurfacing #17).

    Args:
        components: Iterable of escrow components with ``.id``,
            ``.name``, ``.annual_amount``, and optionally ``.inflation_rate``.

    Returns:
        List of :class:`EscrowComponentDisplay`, one per input component, in
        input order.
    """
    annuals = [Decimal(str(comp.annual_amount)) for comp in components]
    monthlies = _allocate_monthly_amounts(annuals)
    rows: list[EscrowComponentDisplay] = []
    for comp, annual, monthly in zip(components, annuals, monthlies):
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
    """Sum the given escrow components' annual amounts / 12.

    The pure summation primitive: the caller supplies the component set already
    resolved for the date in question.  For the supersession escrow model that
    resolution is :func:`resolve_active_lines` (each line's in-effect, non-removed
    version on a date), and :func:`escrow_monthly_as_of` is the date-keyed wrapper
    that pairs the two -- the split reads it per payment date, the display / cash
    surfaces on today.  This function no longer filters by active state itself; it
    sums exactly the components handed to it.

    Args:
        components: List of objects with .annual_amount, and optionally
                    .inflation_rate, .created_at.
        as_of_date: If provided, applies inflation from component
                    created_at to as_of_date (a FORWARD-projection escalation
                    only; recorded past/present escrow is exact, so the loan
                    split never passes this).

    Returns:
        Monthly escrow amount rounded to 2 decimal places.
    """
    total = Decimal("0.00")

    for comp in components:
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
                months_elapsed = months_between(created, as_of_date)
                years_elapsed = max(
                    months_elapsed / MONTHS_PER_YEAR, Decimal("0")
                )
                if years_elapsed > 0:
                    annual = annual * (1 + rate) ** years_elapsed

        monthly = annual / MONTHS_PER_YEAR
        total += monthly

    return round_money(total)


def _version_as_of(versions: list, on_date: date) -> object | None:
    """Return a line's version in effect on ``on_date`` (supersession resolution).

    The version with the greatest ``effective_date <= on_date`` -- the one the
    later versions have not yet superseded.  ``None`` when the line has no version
    on or before ``on_date`` (it did not exist yet).  This is the single
    "which version applies" primitive; a removal tombstone is a normal version
    here (it wins if it is the latest on/before the date), and the caller decides
    a tombstone contributes 0.

    Args:
        versions: The line's :class:`~app.models.escrow_line.EscrowComponentVersion`
            objects (each with ``effective_date``), in any order.
        on_date: The date to resolve the in-effect version for.

    Returns:
        The in-effect version, or ``None`` if none is on/before ``on_date``.
    """
    candidates = [v for v in versions if v.effective_date <= on_date]
    if not candidates:
        return None
    return max(candidates, key=lambda v: v.effective_date)


def resolve_active_lines(lines: list, on_date: date) -> list[ResolvedEscrowLine]:
    """Resolve each escrow line to its in-effect, non-removed version on ``on_date``.

    For every line, pick the version in effect on ``on_date``
    (:func:`_version_as_of`); drop the line when it has no version yet or its
    in-effect version is a removal tombstone (``is_removed``), so it contributes
    nothing on that date.  The returned rows preserve input line order (the
    loaders sort by name), which the display cent-allocation relies on for stable
    tie-breaking.

    The single "what escrow is active on date D" resolver: both the display
    surfaces (via :func:`build_escrow_display`) and the monthly total (via
    :func:`escrow_monthly_as_of`) read exactly this set, so a rendered row and the
    figure it is summed into can never disagree.

    Args:
        lines: :class:`~app.models.escrow_line.EscrowLine` objects, each exposing
            ``id``, ``name``, and ``versions``.
        on_date: The date to resolve every line's in-effect version for.

    Returns:
        One :class:`ResolvedEscrowLine` per line active on ``on_date``, in the
        input line order; empty when no line is active.
    """
    resolved: list[ResolvedEscrowLine] = []
    for line in lines:
        version = _version_as_of(line.versions, on_date)
        if version is None or version.is_removed:
            continue
        inflation = version.inflation_rate
        resolved.append(ResolvedEscrowLine(
            id=line.id,
            name=line.name,
            annual_amount=Decimal(str(version.annual_amount)),
            inflation_rate=(
                Decimal(str(inflation)) if inflation is not None else None
            ),
            created_at=getattr(version, "created_at", None),
        ))
    return resolved


def escrow_monthly_as_of(lines: list, on_date: date) -> Decimal:
    """Monthly escrow total for a loan's lines as of ``on_date`` (the DRY heart).

    Resolves each line to its in-effect, non-removed version
    (:func:`resolve_active_lines`) and sums ``annual_amount / 12`` with the same
    sum-then-round boundary :func:`calculate_monthly_escrow` uses (E-26).  The
    ONE function every date-keyed escrow figure flows through: the loan-payment
    split reads it on each payment's date and the cash / display surfaces read it
    on today, so the escrow built into a payment's cash and the escrow its split
    subtracts are the same figure by construction, never by coincidence.  No
    inflation is applied -- recorded past/present escrow is exact; inflation is a
    forward-projection display concern only (:func:`calculate_monthly_escrow`'s
    ``as_of_date`` branch).

    Args:
        lines: :class:`~app.models.escrow_line.EscrowLine` objects with their
            ``versions`` loaded.
        on_date: The date to compute the monthly escrow total for.

    Returns:
        The rounded monthly escrow total; ``Decimal("0.00")`` when no line is
        active on ``on_date``.
    """
    return calculate_monthly_escrow(resolve_active_lines(lines, on_date))


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
    return round_money(monthly_pi + escrow)
