"""
Shekel Budget App -- Paycheck engine: the VALUE a priced paycheck is.

The six dataclasses :func:`~._pricing.calculate_paycheck` assembles and every
reader of a paycheck consumes -- the salary cockpit, the projection ledger,
the amount model, the payroll feeds.  They carry no behaviour beyond the
section totals that belong to the section owning the data (``taxes.total``,
``deductions.total_pre_tax``, ``earnings.total_taxable``,
``earnings.take_home_rate_pct``), so this leaf imports nothing of the engine
and every other leaf may import it.  Two rules sit beside them:
:func:`waterfall_gross` and :func:`waterfall_net`, the gross and the net a
waterfall of those totals makes, which a priced paycheck and a transcribed pay
stub share (plan step **salary:S11-b**).

Split out of the one-module engine at plan step **salary:C12** (ledger row
**P64**), which is what put the package where the module had been; the
package docstring carries the engine's contract and every leaf's place in it.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.utils.money import ZERO, round_money


@dataclass
class PricedLine:
    """One payroll line PRICED for one paycheck: a name and what it is worth.

    ``DeductionLine`` until plan step **salary:R18-b** (ruling **R-SAL38**),
    which prices the earning kinds through the same pass; a line of any of
    the four kinds is this value once priced, and which side it is on is
    which list of the breakdown holds it.  ``target_account_id`` is the
    account a DEDUCTION funds (the contribution feed reads it) and ``None``
    for every earning.

    ``paycheck_line_id`` is WHICH ``salary.paycheck_lines`` row was priced,
    since plan step **salary:S11-b**: the pay stub entry door sets each stub
    amount beside the line the app prices that payday and joins the two on
    this key, never on the display name.  The engine copies the priced line's
    ``id``; it is ``None`` only for a line that carries none -- the engine
    suite's duck-typed line fakes and the display fakes tests build by hand.
    """
    name: str
    amount: Decimal
    target_account_id: int = None
    paycheck_line_id: int | None = None


def waterfall_gross(base_pay: Decimal, taxable_earnings: Decimal) -> Decimal:
    """Return a paycheck's GROSS: base pay plus the taxable earnings.

    The first step of the waterfall :func:`waterfall_net` finishes, written
    once for the same two readers: the engine's
    :func:`~._lines.priced_gross` (and the year-to-date wage cumulative that
    replays it) and a transcribed pay stub's totals (plan step
    **salary:S11-b**).

    Args:
        base_pay: What the salary pays for the paycheck.
        taxable_earnings: The taxable earning lines' total.

    Returns:
        Their sum.
    """
    return base_pay + taxable_earnings


def waterfall_net(
    gross: Decimal, pre_tax: Decimal, taxes: Decimal, post_tax: Decimal,
    after_tax: Decimal,
) -> Decimal:
    """Return the deposit a paycheck's waterfall makes: its NET pay.

    Gross, less the pre-tax deductions, the withholding and the post-tax
    deductions, plus the after-tax earnings.  **The rule written once** for
    the two things that have a net: the paycheck the engine prices
    (:func:`~._pricing.calculate_paycheck`) and the real pay stub whose
    printed net the entry door checks (:mod:`app.services.pay_stub_service`,
    plan step **salary:S11-b**).  With :func:`waterfall_gross` it is the whole
    arithmetic the two share; which LINES feed each total is each caller's own
    grouping (the engine's pass per kind, the stub's sum per kind), so a new
    kind changes these signatures and both callers answer the change.

    Args:
        gross: Base pay plus the taxable earnings.
        pre_tax: The pre-tax deductions' total.
        taxes: The withholding's total.
        post_tax: The post-tax deductions' total.
        after_tax: The after-tax earnings' total.

    Returns:
        The net, rounded to the cent.
    """
    return round_money(gross - pre_tax - taxes - post_tax + after_tax)


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
    pre_tax: list[PricedLine] = field(default_factory=list)
    post_tax: list[PricedLine] = field(default_factory=list)

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
    """The earnings side of a single paycheck: base pay, its lines, and the figures they make.

    **Base pay plus a list of lines, since plan step salary:R18-b** (ruling
    **R-SAL38**; ledger row **D59**).  Until then this was four scalars and
    ``gross_biweekly`` WAS the salary rate, so nothing could add a dollar to
    a paycheck that was not an annual-salary raise; an employer allowance
    with a cadence had to be a separate income template, and one payroll
    deposit met two or three app rows.

    Attributes:
        annual_salary: The post-raise annual salary in effect on the payday.
        base_biweekly: What the SALARY pays for one paycheck -- the rate
            :meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on`
            derives (the annual over the paychecks a year of the rhythm in
            force on the payday, since plan step salary:X-av-2), and the
            base every PERCENTAGE line is a percentage of
            (R-SAL38: never of gross, so a percentage earning is not circular
            and no existing line moves when an earning joins).
        gross_biweekly: ``base_biweekly`` plus the TAXABLE earning lines --
            the stub's gross, the FICA base, what withholding annualises and
            what the year-to-date wage cumulative sums.
        taxable_income: ``gross_biweekly`` less the pre-tax deductions,
            floored at zero; the income-tax base.
        net_pay: The deposit: gross less both deduction passes and the
            withholding, plus the AFTER-TAX earning lines.
        taxable: The taxable earning lines priced for this paycheck, in
            the profile's line order.
        after_tax: The after-tax earning lines priced for this paycheck.
    """
    annual_salary: Decimal
    base_biweekly: Decimal
    gross_biweekly: Decimal
    taxable_income: Decimal = ZERO
    net_pay: Decimal = ZERO
    taxable: list[PricedLine] = field(default_factory=list)
    after_tax: list[PricedLine] = field(default_factory=list)

    @property
    def total_taxable(self) -> Decimal:
        """Return the sum of the taxable earning lines (``gross - base``)."""
        return sum((line.amount for line in self.taxable), ZERO)

    @property
    def total_after_tax(self) -> Decimal:
        """Return the sum of the after-tax earning lines."""
        return sum((line.amount for line in self.after_tax), ZERO)

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
