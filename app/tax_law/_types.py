"""The shapes the tax law is written in, and the checks that make a malformed year unwritable.

Every value is a frozen dataclass over ``Decimal`` literals: the law is data the
release carries, never a row a user or a deploy can write (ruling
**salary:R-SAL74**).  A year is checked when its module is imported, so a
transcription slip in next year's figures -- a bracket ladder with a gap, a
filing status left out -- stops the application from starting rather than
pricing a paycheck against it.

**The attribute names are the calculator's.**  :mod:`app.services.tax_calculator`
reads ``standard_deduction``, ``brackets`` (each ``min_income`` / ``max_income``
/ ``rate`` / ``sort_order``), the three credit amounts and the five FICA
figures off whatever it is handed; these carry exactly those names, so the
calculator prices the law without knowing where it came from.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.enums import FilingStatusEnum, TaxTypeEnum

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class Bracket:
    """One rung of a federal bracket ladder: ``rate`` on income in ``(min_income, max_income]``.

    ``max_income`` is ``None`` on the top rung only.  ``sort_order`` is the
    rung's position in its ladder, set by :func:`ladder` and never written by
    hand, because the calculator orders rungs by it.
    """

    min_income: Decimal
    max_income: Decimal | None
    rate: Decimal
    sort_order: int


def ladder(*rungs: tuple[str, str | None, str]) -> tuple[Bracket, ...]:
    """Build a bracket ladder from ``(min_income, max_income, rate)`` strings.

    The one place a rung's ``sort_order`` is derived (its position), and the
    one place a ladder's shape is checked: it starts at zero, each rung starts
    where the one below it ends, only the top rung is open-ended, and every
    rate is a fraction.

    Args:
        *rungs: The rungs from the bottom up, each as decimal strings exactly
            as the source prints them (``max_income`` ``None`` on the top rung).

    Returns:
        The ladder as a tuple of :class:`Bracket`.

    Raises:
        ValueError: If the rungs do not form one contiguous ladder from zero.
    """
    brackets = tuple(
        Bracket(
            min_income=Decimal(low),
            max_income=None if high is None else Decimal(high),
            rate=Decimal(rate),
            sort_order=position,
        )
        for position, (low, high, rate) in enumerate(rungs)
    )
    if not brackets or brackets[0].min_income != _ZERO:
        raise ValueError("a bracket ladder starts at zero income")
    for lower, upper in zip(brackets, brackets[1:]):
        if lower.max_income is None or lower.max_income != upper.min_income:
            raise ValueError(
                f"bracket rung {upper.sort_order} starts at {upper.min_income}, "
                f"not where rung {lower.sort_order} ends ({lower.max_income})"
            )
    if brackets[-1].max_income is not None:
        raise ValueError("the top bracket rung is open-ended")
    for rung in brackets:
        if not _ZERO <= rung.rate <= _ONE:
            raise ValueError(f"bracket rate {rung.rate} is not a fraction")
    return brackets


@dataclass(frozen=True)
class FederalRules:
    """One filing status's federal income-tax rules for one tax year.

    What :func:`app.services.tax_calculator.calculate_federal_withholding` and
    :func:`~app.services.tax_calculator.calculate_annual_federal_liability`
    read: the standard deduction, the bracket ladder, the per-child and
    per-other-dependent credits and the refundable Additional Child Tax Credit
    cap per child.
    """

    standard_deduction: Decimal
    child_credit_amount: Decimal
    other_dependent_credit_amount: Decimal
    child_credit_refundable_cap: Decimal
    brackets: tuple[Bracket, ...]


@dataclass(frozen=True)
class FicaRules:
    """One tax year's Social Security and Medicare rules (they carry no filing status)."""

    ss_rate: Decimal
    ss_wage_base: Decimal
    medicare_rate: Decimal
    medicare_surtax_rate: Decimal
    medicare_surtax_threshold: Decimal


@dataclass(frozen=True)
class ChildDeductionTier:
    """One AGI tier of a state's per-child deduction.

    ``deduction_per_child`` applies to AGI in ``(agi_min, agi_max]``, and
    ``agi_max`` is ``None`` on the open-ended top tier.  A threshold dollar
    belongs to the LOWER tier, as the statute reads ("Up to $X", then "Over $X");
    :func:`app.services.tax_calculator.resolve_child_deduction_per_child` keys
    on ``agi_max`` alone.
    """

    agi_min: Decimal
    agi_max: Decimal | None
    deduction_per_child: Decimal


@dataclass(frozen=True)
class StateYearLaw:
    """One state's income-tax law for one tax year.

    The rate is the STATE's, stated once; the standard deduction and the child
    deduction tiers are per filing status, because the statute states them per
    status.  Every filing status is present in both, or the year does not load.
    """

    tax_type: TaxTypeEnum
    flat_rate: Decimal | None
    standard_deduction: Mapping[FilingStatusEnum, Decimal]
    child_deduction_tiers: Mapping[FilingStatusEnum, tuple[ChildDeductionTier, ...]]

    def __post_init__(self):
        """Refuse a state year that leaves a filing status out."""
        _require_every_status(self.standard_deduction, "state standard deduction")
        _require_every_status(self.child_deduction_tiers, "state child deduction")


@dataclass(frozen=True)
class TaxYearLaw:
    """Everything the app knows of one tax year's law, and where it came from.

    ``sources`` names each published document the year's figures were
    transcribed from, so a reader checking a figure knows where to look and a
    reader adding next year knows what to look for.  The federal rules cover
    every filing status and FICA is always present, or the year does not load;
    a state appears only where the app supports it.
    """

    tax_year: int
    sources: tuple[str, ...]
    federal: Mapping[FilingStatusEnum, FederalRules]
    fica: FicaRules
    states: Mapping[str, StateYearLaw]

    def __post_init__(self):
        """Refuse a year that is missing a filing status or cites no source."""
        _require_every_status(self.federal, f"{self.tax_year} federal rules")
        if not self.sources:
            raise ValueError(f"the {self.tax_year} tax law cites no source")


@dataclass(frozen=True)
class TaxLaw:
    """Every tax year the app carries, oldest first, each year once."""

    years: tuple[TaxYearLaw, ...]

    def __post_init__(self):
        """Refuse years out of order or repeated."""
        numbers = [year.tax_year for year in self.years]
        if numbers != sorted(set(numbers)):
            raise ValueError(f"tax years must be distinct and ascending: {numbers}")


def _require_every_status(by_status: Mapping, what: str) -> None:
    """Raise unless *by_status* holds exactly one entry per filing status.

    Args:
        by_status: A mapping keyed on :class:`~app.enums.FilingStatusEnum`.
        what: What the mapping is, for the error message.

    Raises:
        ValueError: If a status is missing or a key is not a status.
    """
    if set(by_status) != set(FilingStatusEnum):
        missing = sorted(m.value for m in set(FilingStatusEnum) - set(by_status))
        raise ValueError(f"{what} must cover every filing status; missing {missing}")
