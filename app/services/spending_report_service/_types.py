"""The Spending report's result shapes: frozen, Decimal-only, no behaviour.

Every value :func:`~app.services.spending_report_service.compute_spending_report`
returns, plus the three private records its own helpers pass between them.  They
live in one module for the reason :mod:`app.services.ledger_report_service._types`
does: the four producing modules beside this one all construct them, and a
result shape defined next to ONE producer reads as that producer's private
vocabulary rather than the package's contract.

Boundary discipline: no query, no Flask import, no arithmetic -- these are the
shapes, and the modules that fill them are where the rules live.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services import spending_analysis
from app.services.pay_calendar import PayCalendar


@dataclass(frozen=True)
class SpendingWindow:
    """The time window a spending report is computed over.

    A discriminated selector mirroring
    :class:`app.services.ledger_report_service.StatementWindow`:
    ``window_type`` decides which of the other fields are meaningful --
    ``period_id`` for ``"pay_period"``, ``month`` + ``year`` for
    ``"month"``, ``year`` for ``"year"``.  A deliberately separate value
    object per its own bounded context (the retrospective spending surface);
    the route layer shares only the parameter parsing.

    Attributes:
        window_type: One of ``"pay_period"`` / ``"month"`` / ``"year"``.
        period_id: The pay period id (``"pay_period"`` windows only).
        month: The calendar month 1-12 (``"month"`` windows only).
        year: The calendar year (``"month"`` and ``"year"`` windows).
    """

    # Pylint: ``duplicate-code`` -- incidental structural similarity with the
    # ``StatementWindow`` four-field selector.  They are deliberately separate
    # value objects in separate bounded contexts (a spending window scopes
    # settled-expense attribution; a statement window scopes a confirmed-ledger
    # paid-date basis), so a shared base would couple two unrelated attribution
    # rules (coding-standards rule 13).  One-sided disable so ``StatementWindow``
    # stays the un-disabled anchor.
    # pylint: disable=duplicate-code
    window_type: str
    period_id: int | None = None
    month: int | None = None
    year: int | None = None
    # pylint: enable=duplicate-code


@dataclass(frozen=True)
class Comparison:
    """A hero comparison: a baseline plus the signed delta and percent.

    Build one with :meth:`of` so the delta and percent are derived
    identically for both the vs-prior and vs-average chips, and every field
    is ``None`` together when the comparison has no baseline (no prior
    window, or no trailing windows to average).

    Attributes:
        baseline: The comparison baseline (prior spend, or trailing
            average), or ``None`` when no baseline exists.
        delta: ``current - baseline`` (signed), or ``None``.
        pct: ``delta`` as a percent of ``baseline``, or ``None`` -- also
            ``None`` when ``baseline`` is zero (an empty prior window).
    """

    baseline: Decimal | None
    delta: Decimal | None
    pct: Decimal | None

    @classmethod
    def of(cls, current: Decimal, baseline: Decimal | None) -> "Comparison":
        """Build a comparison of ``current`` against ``baseline``.

        Args:
            current: The chosen window's spend.
            baseline: The comparison baseline, or ``None`` when none exists
                (all three fields then come back ``None``).

        Returns:
            The :class:`Comparison`.  When ``baseline`` is zero the delta is
            still real (``current``) but ``pct`` is ``None`` (no percent of
            zero -- via :func:`spending_analysis.signed_pct`).
        """
        if baseline is None:
            return cls(baseline=None, delta=None, pct=None)
        delta = current - baseline
        return cls(
            baseline=baseline,
            delta=delta,
            pct=spending_analysis.signed_pct(delta, baseline),
        )


@dataclass(frozen=True)
class HeroFigures:
    """The Spending hero band.

    Attributes:
        spent_total: Total settled spend in the chosen window.
        vs_prior: Comparison against the immediately preceding window of the
            same type.
        vs_average: Comparison against the trailing same-type window average.
        payment_timing: The window's timeliness dict
            (``total_bills_paid`` / ``paid_on_time`` / ``paid_late`` /
            ``avg_days_before_due``), or ``None`` when no bill in the window
            has both a paid date and a due date.
    """

    spent_total: Decimal
    vs_prior: Comparison
    vs_average: Comparison
    payment_timing: dict | None


@dataclass(frozen=True)
class SeriesPoint:
    """One bar of the hero chart's trailing same-type window series.

    Attributes:
        window: The window this point covers, or ``None`` when the step
            walked past the user's pay-period history (a ``"pay_period"``
            window with no earlier period; calendar windows always shift).
        total: The window's settled spend, or ``None`` when the window
            overlaps no pay period (before the user's history) -- the chart
            renders such a point as a baseline tick, and the vs-average
            derivation excludes it.  A window with periods but no settled
            spend is ``Decimal("0")`` (a real lean window that DOES count
            toward the average).
    """

    window: SpendingWindow | None
    total: Decimal | None


@dataclass(frozen=True)
class SpendingItemRow:
    """One 'Where It Went' drill-down item (a single category).

    Attributes:
        category_id: The category's id (``0`` for the Uncategorized bucket).
        item_name: The category item label.
        amount: Settled spend for this category in the window.
        share: ``amount`` as a fraction of the window total (a full-precision
            ``Decimal`` in ``[0, 1]``; templates render, never compute).
        delta: ``amount`` minus the category's prior-window spend (signed;
            the D7 window-over-window change basis).
        is_new: ``True`` when the category had no prior-window spend, so the
            whole amount is new spending (rendered as a "new" badge instead
            of a percent of zero).
    """

    category_id: int
    item_name: str
    amount: Decimal
    share: Decimal
    delta: Decimal
    is_new: bool


@dataclass(frozen=True)
class SpendingGroupRow:
    """One 'Where It Went' group row with its drill-down items.

    Attributes:
        group_name: The category group label.
        amount: Settled spend for the whole group in the window.
        share: ``amount`` as a fraction of the window total.
        delta: ``amount`` minus the group's prior-window spend (signed).
            The prior side sums EVERY prior-window category in the group,
            including categories with no spend in the chosen window, so a
            group whose big bill stopped shows the drop.
        is_new: ``True`` when the group had no prior-window spend at all.
        items: The group's :class:`SpendingItemRow` items, amount-descending.
    """

    group_name: str
    amount: Decimal
    share: Decimal
    delta: Decimal
    is_new: bool
    items: list[SpendingItemRow]


@dataclass(frozen=True)
class ChangeRow:
    """One By-change lens row: a category's window-over-window movement.

    Every category with settled spend in the chosen window OR its prior
    window gets a row -- including zero-current rows (prior spend, none
    now; the D7 rider), so a stopped bill is as visible as a grown one.

    Attributes:
        category_id: The category's id (``0`` for the Uncategorized bucket).
        group_name: The category group label.
        item_name: The category item label.
        current: The chosen window's settled spend (``0`` when none).
        prior: The prior window's settled spend (``0`` when none).
        delta: ``current - prior`` (signed).
        is_new: ``True`` when ``prior`` is zero and ``current`` is not.
    """

    category_id: int
    group_name: str
    item_name: str
    current: Decimal
    prior: Decimal
    delta: Decimal
    is_new: bool


@dataclass(frozen=True)
class Surprise:
    """A settled row whose entered actual differed from its estimate.

    Attributes:
        transaction_id: The settled transaction's id.
        name: The transaction name.
        group_name: Its category group label.
        item_name: Its category item label.
        estimated: The estimate at entry.
        actual: The entered actual at settle.
        delta: ``actual - estimated`` (signed; positive = over estimate).
    """

    transaction_id: int
    name: str
    group_name: str
    item_name: str
    estimated: Decimal
    actual: Decimal
    delta: Decimal


@dataclass(frozen=True)
class Surprises:
    """The capped surprises list plus the net across ALL surprises.

    Attributes:
        rows: The surprises sorted by ``abs(delta)`` descending and capped at
            :data:`._surprises._MAX_SURPRISES`.
        net: The signed sum of EVERY surprise's delta (not just the capped
            rows) -- the window's net over/under estimate.
    """

    rows: list[Surprise]
    net: Decimal


@dataclass(frozen=True)
class SpendingScope:
    """The page-context facts the template renders as scope labels.

    Attributes:
        account_id: The checking account the report is scoped to.
        account_name: That account's display name (the on-screen scope
            label the audit's cross-cutting fix requires).
        settled_only: Always ``True`` -- the surface is measured
            (settled-only); carried so the measured chip reads it rather
            than hard-coding the basis.
        window_label: The human window label (e.g. ``"January 2026"``).
    """

    account_id: int
    account_name: str
    settled_only: bool
    window_label: str


@dataclass(frozen=True)
class SpendingReport:
    """The complete Spending surface dataset for one window.

    Attributes:
        scope: The account / basis / window page context.
        hero: The hero band (spent, vs-prior, vs-average, payment timing).
        series: The trailing same-type window series, oldest first, with
            the chosen window last (:data:`._window._CHART_WINDOW_COUNT` points).
        breakdown: The 'Where It Went' group rows, amount-descending.
        changes: The By-change lens rows, delta-magnitude-descending.
        surprises: The capped estimate-surprises list and its net.
    """

    scope: SpendingScope
    hero: HeroFigures
    series: list[SeriesPoint]
    breakdown: list[SpendingGroupRow]
    changes: list[ChangeRow]
    surprises: Surprises


@dataclass(frozen=True)
class _ResolvedWindow:
    """A window resolved to the period set and date span it covers.

    ``first_day`` / ``last_day`` are ``None`` for a ``"pay_period"`` window
    (the period IS the span; ``period_ids`` drives the fetch); for a
    ``"month"`` / ``"year"`` window they bound the COALESCE(due_date, pay
    period start) attribution fetch, and ``period_ids`` (the overlapping
    periods) serves only as the tracked-window signal for the None-vs-zero
    total rule (:func:`_window_total`).
    """

    period_ids: list[int]
    first_day: date | None
    last_day: date | None
    label: str


@dataclass(frozen=True)
class _CategoryTotal:
    """A category's settled spend in one window, with its display labels."""

    group_name: str
    item_name: str
    amount: Decimal


@dataclass(frozen=True)
class _ScopeIds:
    """The scope every settled-spend window load reads through.

    One cohesive concept -- WHOSE data a window reads (the owner, the checking
    account, the baseline scenario) and the pay CALENDAR they resolve against,
    which rides here because ``_build_series`` resolves thirteen windows and it
    varies with none -- thirteen derivations of one value otherwise (C2-f1).
    """

    user_id: int
    account_id: int
    scenario_id: int
    calendar: PayCalendar
