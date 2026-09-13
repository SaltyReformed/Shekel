"""
Shekel Budget App -- Paycheck engine: the VALUE a priced paycheck is.

The six dataclasses :func:`~._pricing.calculate_paycheck` assembles and every
reader of a paycheck consumes -- the salary cockpit, the projection ledger,
the amount model, the payroll feeds.  They carry no behaviour beyond the
section totals that belong to the section owning the data (``taxes.total``,
``deductions.total_pre_tax``, ``earnings.take_home_rate_pct``), so this leaf
imports nothing of the engine and every other leaf may import it.

Split out of the one-module engine at plan step **salary:C12** (ledger row
**P64**), which is what put the package where the module had been; the
package docstring carries the engine's contract and every leaf's place in it.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.utils.money import ZERO


@dataclass
class DeductionLine:
    """A single deduction line item in a paycheck breakdown."""
    name: str
    amount: Decimal
    target_account_id: int = None


@dataclass
class TaxLines:
    """The four withholding lines computed for a single paycheck."""
    federal: Decimal = ZERO
    state: Decimal = ZERO
    social_security: Decimal = ZERO
    medicare: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        """Return the sum of the four withholding lines."""
        return self.federal + self.state + self.social_security + self.medicare


@dataclass
class DeductionBreakdown:
    """Pre- and post-tax deduction line items for a single paycheck."""
    pre_tax: list[DeductionLine] = field(default_factory=list)
    post_tax: list[DeductionLine] = field(default_factory=list)

    @property
    def total_pre_tax(self) -> Decimal:
        """Return the sum of the pre-tax deduction amounts."""
        return sum((d.amount for d in self.pre_tax), ZERO)

    @property
    def total_post_tax(self) -> Decimal:
        """Return the sum of the post-tax deduction amounts."""
        return sum((d.amount for d in self.post_tax), ZERO)


@dataclass
class Earnings:
    """Gross-to-net dollar figures for a single paycheck."""
    annual_salary: Decimal
    gross_biweekly: Decimal
    taxable_income: Decimal = ZERO
    net_pay: Decimal = ZERO

    @property
    def take_home_rate_pct(self) -> Decimal | None:
        """Return ``net / gross`` expressed as a percent (MED-04 / E-16).

        Pre-computed here so the salary breakdown template renders the
        take-home rate without Jinja-side division.  Returns ``None``
        when ``gross_biweekly`` is non-positive so the template can
        render a placeholder ``--`` without dividing by zero.

        **FULL precision, never quantized here** (plan step salary:S3-f-2a):
        ``retirement_dashboard_service.compute_gap_net_biweekly`` scales the
        retirement income target by this ratio, so a display rounding added
        here would move a money figure; the template rounds at its own edge.
        """
        if self.gross_biweekly <= ZERO:
            return None
        return (self.net_pay / self.gross_biweekly) * Decimal("100")


@dataclass
class PeriodInfo:
    """Pay-period identity and per-paycheck event flags.

    Attributes:
        payday: The day this paycheck arrives, the period's ``start_date``.
            **The TOTAL identity, added at plan step salary:S3-d**: every
            paycheck has a payday where only a MATERIALISED one has an id.  It
            retired ``projection_inputs``' ``payday_by_period_id`` table,
            which existed only to recover this fact from the id.
        period_id: ``budget.pay_periods.id``, or ``None`` for a paycheck on a
            PROJECTED payday -- one the owner's cadence reaches past their
            saved schedule.  **Typed ``int`` and documented "never ``None``
            structurally" until plan step salary:S3-d**, which held only while
            nothing priced a payday past the horizon; the pricer does, so the
            nullable :class:`~app.services.pay_calendar.DerivedPeriod` has
            always carried reaches here, for its reason -- a projected payday
            is not a row a foreign key can name.  REQUIRED with no default, so
            widening the type did not also make it forgettable.
        is_third_paycheck: Whether this is the third paycheck starting in its
            calendar month, which is what a 24-per-year deduction skips.
        raise_event: The raise taking effect in this period, as the label
            :func:`get_raise_event` composes, or ``""``.
    """
    payday: date
    period_id: "int | None"
    is_third_paycheck: bool = False
    raise_event: str = ""


@dataclass
class PaycheckBreakdown:
    """Complete paycheck breakdown for a single pay period.

    The breakdown is organised into four cohesive sections rather than a
    flat field list: :class:`PeriodInfo` (``period``), :class:`Earnings`
    (``earnings``), :class:`TaxLines` (``taxes``), and
    :class:`DeductionBreakdown` (``deductions``).  Section totals live on
    the section that owns the data (``taxes.total``,
    ``deductions.total_pre_tax``, ``earnings.take_home_rate_pct``).
    """
    period: PeriodInfo
    earnings: Earnings
    taxes: TaxLines = field(default_factory=TaxLines)
    deductions: DeductionBreakdown = field(default_factory=DeductionBreakdown)
