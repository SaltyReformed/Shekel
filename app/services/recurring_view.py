"""
Shekel Budget App -- Unified Recurring-Definitions View Producer (Loop B, P1)

Single producer behind the unified ``/templates`` (Recurring) surface that
replaces the three parallel pages ``/templates`` + ``/transfers`` +
``/obligations``.  It shapes the user's active recurring definitions
(income, expense, and transfer templates) into one display model: a
summary band, three grouped sections with per-section subtotals, and per
row a defined amount, monthly + per-paycheck equivalents, an
engine-backed next date, and a share of its section's committed total.

Two units, one source of truth
------------------------------
Every monetary figure is produced in BOTH units so the page-wide
Monthly / Per-paycheck toggle can switch without recomputing money in the
template or in JS.  There is exactly one monthly source of truth --
``obligations_aggregator.template_monthly_or_none`` (E-24 / HIGH-05, also
behind the /savings emergency-fund baseline) -- and the per-paycheck value
is DERIVED from it by the single factor ``MONTHS_PER_YEAR /
PAY_PERIODS_PER_YEAR`` (12 / 26).  The toggle therefore only re-expresses
the same committed figure in a different unit; it never opens a second
money path that could disagree with the first.

Next dates are engine-backed
----------------------------
The next occurrence is the date the recurrence engine itself would assign
to the next generated instance: ``recurrence_engine.match_periods`` picks
the matching pay periods and ``recurrence_engine.compute_due_date`` gives
the instance's due date, so a row's "next date" cannot disagree with the
grid cell it points at.  This retires the ``/obligations`` approximation
(``_next_occurrence``) the audit flagged.

What appears vs what totals
---------------------------
The list is a management surface, so it shows EVERY active definition,
including the non-repeating (rule-less) definitions you still need to edit,
archive, or delete.  The summary band and the section subtotals, however,
sum only genuine recurring commitments -- ``template_monthly_or_none``
returns ``None`` for rule-less / expired / missing-or-zero-amount templates, so
those rows render with a blank equivalent and contribute nothing to any
total (matching the retired /obligations kernel exactly).

Boundary discipline (``CLAUDE.md`` Architecture): no Flask imports; inputs
are already-loaded ORM template lists (or any duck-typed equivalent, as the
tests build with ``types.SimpleNamespace``) plus the user's pay periods and
an ``as_of`` date; output is a frozen dataclass tree of ``Decimal`` /
``date``.  All money math is ``Decimal``; the route/template only display.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.services.obligations_aggregator import (
    RecurringTemplate,
    template_monthly_or_none,
)
from app.services.recurrence_engine import compute_due_date, match_periods
from app.utils.money import (
    MONTHS_PER_YEAR,
    PAY_PERIODS_PER_YEAR,
    round_money,
)

# Per-paycheck is the monthly equivalent re-expressed over the biweekly pay
# cadence: a monthly figure covers ``PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR``
# paychecks, so dividing by that ratio (equivalently, multiplying by
# ``MONTHS_PER_YEAR / PAY_PERIODS_PER_YEAR`` = 12 / 26) gives the amount
# attributable to one paycheck.  Defined once here so the toggle's second
# unit has a single conversion site.
_MONTHLY_TO_PER_PAYCHECK = MONTHS_PER_YEAR / PAY_PERIODS_PER_YEAR

_HUNDRED = Decimal("100")
_PERCENT_QUANTUM = Decimal("0.1")


@dataclass(frozen=True)
class UnitPair:
    """A monetary figure in both display units.

    ``monthly`` and ``per_paycheck`` are the same underlying commitment
    expressed two ways, each rounded to cents.  Both are ``None`` together
    for a definition that is not a recurring commitment (rule-less, expired,
    or missing/zero amount) -- the page renders a blank equivalent for such a
    row rather than a misleading zero.
    """

    monthly: Decimal | None
    per_paycheck: Decimal | None


@dataclass(frozen=True)
class RecurringRow:
    """One recurring-definition row in a section.

    Attributes:
        template: The ORM template (``TransactionTemplate`` or
            ``TransferTemplate``) this row renders.  Carried whole so the
            template layer can read its name, badges, category / accounts,
            recurrence rule (for the shared ``recurrence_cell`` macro), and
            the edit / archive / delete action links -- none of which the
            producer recomputes.
        equivalent: The monthly + per-paycheck commitment (:class:`UnitPair`),
            both ``None`` for a non-recurring definition.
        next_date: The engine-assigned due date of the next occurrence on
            or after ``as_of``, or ``None`` when the definition has no
            future recurring occurrence (no rule, or expired).
        share_pct: This row's monthly equivalent as a percentage (0-100) of
            its section's committed monthly total, for the share bar; ``None``
            when the row does not contribute (non-recurring) or the section
            total is zero.
    """

    template: RecurringTemplate
    equivalent: UnitPair
    next_date: date | None
    share_pct: Decimal | None


@dataclass(frozen=True)
class RecurringSection:
    """One kind-grouped section (income, expenses, or transfers).

    ``rows`` are ordered by monthly-equivalent cost descending (the locked
    default landing order), non-recurring rows last.  ``subtotal`` is the
    section's committed total in both units; it equals
    ``obligations_aggregator.committed_monthly`` for the section by
    construction (same full-precision filter+sum, rounded once), so the two
    surfaces cannot drift.
    """

    rows: tuple[RecurringRow, ...]
    subtotal: UnitPair


@dataclass(frozen=True)
class SummaryBand:
    """The obligations kernel: committed income vs outflow, no projection.

    Measured from the recurring definitions themselves (never a balance
    projection), so it stands in for the retired /obligations monthly lens.

    Attributes:
        income: Committed recurring income, both units.
        expenses: Committed recurring expenses, both units.
        transfers_out: Committed recurring transfers, both units.
        net: ``income - expenses - transfers_out`` per unit, computed from
            the rounded section subtotals so the tile equals what the shown
            figures subtract to.
        expenses_pct_of_income: Expenses as a percentage (0-100) of income,
            or ``None`` when income is zero (no ratio to show).
    """

    income: UnitPair
    expenses: UnitPair
    transfers_out: UnitPair
    net: UnitPair
    expenses_pct_of_income: Decimal | None


@dataclass(frozen=True)
class RecurringView:
    """The full display model for the unified Recurring surface."""

    income: RecurringSection
    expenses: RecurringSection
    transfers: RecurringSection
    band: SummaryBand


def _unit_pair(monthly_full: Decimal | None) -> UnitPair:
    """Round a full-precision monthly figure into both display units.

    ``monthly_full`` is the unquantized monthly equivalent from
    ``template_monthly_or_none`` (per-row) or a sum of such values (per
    section).  Rounding once here, at the display boundary, keeps
    intermediate sums at full precision so pennies cannot accumulate drift
    (the ``committed_monthly`` contract).  ``None`` in propagates to both
    fields so a non-recurring row shows a blank equivalent.
    """
    if monthly_full is None:
        return UnitPair(monthly=None, per_paycheck=None)
    return UnitPair(
        monthly=round_money(monthly_full),
        per_paycheck=round_money(monthly_full * _MONTHLY_TO_PER_PAYCHECK),
    )


def _share_pct(
    monthly_full: Decimal | None, section_total_full: Decimal,
) -> Decimal | None:
    """This row's share (0-100) of its section's committed monthly total.

    ``None`` when the row does not contribute (non-recurring) or the
    section has no committed total to take a share of (avoids a divide by
    zero and a meaningless bar).  Computed from the full-precision values
    so the share reflects the true proportion, then quantized for display.
    """
    if monthly_full is None or section_total_full == 0:
        return None
    return (monthly_full / section_total_full * _HUNDRED).quantize(
        _PERCENT_QUANTUM, rounding=ROUND_HALF_UP,
    )


def _next_occurrence(
    template: RecurringTemplate, periods: list, as_of: date,
) -> date | None:
    """Engine-backed date of the next occurrence on or after ``as_of``.

    Uses the same public recurrence-engine helpers that generate the grid
    instances: ``match_periods`` selects the pay periods the rule fires in,
    and ``compute_due_date`` gives the due date the generated instance would
    carry.  Returns the first such due date on or after ``as_of`` (the
    current period can match with a due date already past, so the search
    advances to the next matching period), or ``None`` when no matching
    period has a due date on or after ``as_of`` -- no rule, or an expired
    rule whose remaining candidate periods are all in the past.
    Otherwise this tracks the engine exactly: if the engine would still
    generate a future instance (e.g. an expired rule whose final period
    straddles ``as_of``), that instance's date is reported, matching the
    grid cell it points at.

    **The rule-less branch is the whole "does not recur" case** since plan
    step R2e-3.  A second guard used to sit beside it for the ``Once``
    pattern, which ``match_periods`` has no branch for: without it a
    one-time definition emitted a spurious "unknown pattern" warning on
    every render.  With the pattern retired, no such rule exists to guard.
    """
    rule = getattr(template, "recurrence_rule", None)
    if rule is None:
        return None
    for period in match_periods(rule, rule.pattern_id, periods, as_of):
        due = compute_due_date(rule, period)
        if due >= as_of:
            return due
    return None


def _build_section(
    templates: list[RecurringTemplate], periods: list, as_of: date,
) -> RecurringSection:
    """Build one kind-grouped section: its rows and both-units subtotal.

    Every template becomes a row (management surface shows all active
    definitions); its monthly equivalent, when the aggregator returns one,
    also accumulates into the full-precision section total the subtotal and
    each row's share are measured against.  Rows are ordered by monthly cost
    descending with non-recurring rows last (the locked default), preserving
    the caller's incoming order among equals.  The subtotal rounds the
    full-precision total once, so it equals ``committed_monthly`` for the
    section by construction.
    """
    monthly_by_template = []
    section_total_full = Decimal("0")
    for template in templates:
        monthly_full = template_monthly_or_none(template, as_of)
        monthly_by_template.append((template, monthly_full))
        if monthly_full is not None:
            section_total_full += monthly_full

    rows = [
        RecurringRow(
            template=template,
            equivalent=_unit_pair(monthly_full),
            next_date=_next_occurrence(template, periods, as_of),
            share_pct=_share_pct(monthly_full, section_total_full),
        )
        for template, monthly_full in monthly_by_template
    ]
    rows.sort(
        key=lambda row: (
            row.equivalent.monthly is not None,
            row.equivalent.monthly
            if row.equivalent.monthly is not None
            else Decimal("0"),
        ),
        reverse=True,
    )
    return RecurringSection(
        rows=tuple(rows), subtotal=_unit_pair(section_total_full),
    )


def _build_band(
    income: UnitPair,
    expenses: UnitPair,
    transfers_out: UnitPair,
) -> SummaryBand:
    """Assemble the summary band from the three section subtotals.

    ``net`` subtracts expenses and transfers from income in each unit using
    the already-rounded subtotals, so the tile equals what the shown section
    totals subtract to.  Section subtotals are never ``None`` (an empty
    section rounds ``Decimal("0")``), so the arithmetic is always defined.
    ``expenses_pct_of_income`` is ``None`` when income is zero.
    """
    net = UnitPair(
        monthly=income.monthly - expenses.monthly - transfers_out.monthly,
        per_paycheck=(
            income.per_paycheck
            - expenses.per_paycheck
            - transfers_out.per_paycheck
        ),
    )
    if income.monthly is not None and income.monthly > 0:
        expenses_pct_of_income = (
            expenses.monthly / income.monthly * _HUNDRED
        ).quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP)
    else:
        expenses_pct_of_income = None
    return SummaryBand(
        income=income,
        expenses=expenses,
        transfers_out=transfers_out,
        net=net,
        expenses_pct_of_income=expenses_pct_of_income,
    )


def build_view(
    income_templates: list[RecurringTemplate],
    expense_templates: list[RecurringTemplate],
    transfer_templates: list[RecurringTemplate],
    periods: list,
    as_of: date,
) -> RecurringView:
    """Produce the unified Recurring surface's full display model.

    Args:
        income_templates: The user's active recurring income
            ``TransactionTemplate`` rows.
        expense_templates: The user's active recurring expense
            ``TransactionTemplate`` rows.
        transfer_templates: The user's active recurring ``TransferTemplate``
            rows.
        periods: All the user's ``PayPeriod`` rows, for engine-backed next
            dates (``match_periods`` seeds and filters against the full set).
        as_of: Reference date -- "now" for the expired-rule filter and the
            next-occurrence search.  Callers pass ``date.today()``.

    Returns:
        A :class:`RecurringView`: the summary band plus income, expenses,
        and transfers sections, each with cost-descending rows and a
        both-units subtotal.  Every figure is a ``Decimal`` rounded to
        cents; the caller only displays.
    """
    income_section = _build_section(income_templates, periods, as_of)
    expense_section = _build_section(expense_templates, periods, as_of)
    transfer_section = _build_section(transfer_templates, periods, as_of)
    band = _build_band(
        income_section.subtotal,
        expense_section.subtotal,
        transfer_section.subtotal,
    )
    return RecurringView(
        income=income_section,
        expenses=expense_section,
        transfers=transfer_section,
        band=band,
    )
