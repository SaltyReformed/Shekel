"""
Shekel Budget App -- Salary Cockpit Producers

Pure context builders for the ``/salary`` cockpit (the rebuilt salary
landing page) and the projection summary framing.  Every function is
pure: it takes plain data (pay periods already paired with the
:class:`~app.services.paycheck_calculator.PaycheckBreakdown` the paycheck
calculator produced for them, plus the reference ``today``) and returns
plain data.  No Flask, no DB access, no ``float`` -- all money math stays
in :class:`~decimal.Decimal`; the caller casts to ``float`` only at the
JSON serialization boundary.

The projection machinery is reused rather than re-derived (DRY): the
"next raise", "next third paycheck", and per-period series all read the
``raise_event`` string and ``is_third_paycheck`` flag the paycheck
calculator already computed onto each breakdown's
:class:`~app.services.paycheck_calculator.PeriodInfo`.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.enums import DeductionTimingEnum
from app.models.pay_period import PayPeriod
from app.services.paycheck_calculator import PaycheckBreakdown
from app.utils.money import HUNDRED, ZERO

# The net-pay staircase chart looks back this many pay periods before the
# period containing ``today`` and forward this many months after it, so
# the user sees recent history plus roughly the next year and a half of
# projected paychecks (matching the locked D2 "wide sky" layout).
PERIODS_BEFORE_TODAY = 6
HORIZON_MONTHS = 18

# One-decimal-place quantum for the composition and deduction-bar
# percentages.  These are percentages, not money: they are intentionally
# NOT rounded through ``round_money`` (which is the cents boundary) and
# are quantized with an explicit ROUND_HALF_UP so the display never
# reaches the decimal context's banker's-rounding default implicitly.
_PCT_QUANTUM = Decimal("0.1")

# The pair the calculator surfaces per period: the period itself plus the
# breakdown computed for it.  Every producer below consumes this shape.
PeriodPair = tuple[PayPeriod, PaycheckBreakdown]


def _add_months(base: date, months: int) -> date:
    """Return ``base`` advanced by ``months`` calendar months.

    The day is clamped to the 28th so month-length differences never
    overflow (e.g. Jan 31 + 1 month).  Used only to bound the chart
    horizon, where day-of-month precision is irrelevant.

    Args:
        base: The reference date.
        months: Whole months to add (non-negative in practice).

    Returns:
        A :class:`datetime.date` ``months`` after ``base``.
    """
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(base.day, 28))


def _pct_of_gross(value: Decimal, gross: Decimal) -> Decimal:
    """Return ``value`` as a one-decimal percentage of ``gross``.

    Returns ``Decimal("0")`` when ``gross`` is non-positive so a
    zero-gross period cannot divide by zero.

    Args:
        value: The segment amount (a slice of gross).
        gross: The period gross the segment is measured against.

    Returns:
        The percentage, quantized to one decimal place (ROUND_HALF_UP).
    """
    if gross <= ZERO:
        return ZERO
    return (value / gross * HUNDRED).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP)


def _calibration_zero_federal(breakdown: PaycheckBreakdown, calibration_active: bool) -> bool:
    """Return True when an active calibration withholds exactly zero federal.

    Surfaces the honest-but-surprising "$0.00 federal withholding" case
    (e.g. dependents that zero out withholding) so the composition card
    can caption it rather than look like a bug.

    Args:
        breakdown: The focused period's breakdown.
        calibration_active: Whether the profile's calibration is active.

    Returns:
        True iff a calibration is active AND the federal line is zero.
    """
    return calibration_active and breakdown.taxes.federal == ZERO


def clean_raise_label(raw_label: str) -> str:
    """Return a display-clean version of a calculator ``raise_event`` string.

    :func:`app.services.paycheck_calculator.get_raise_event` emits raw
    labels in exactly two shapes, joined with ``", "`` when several raises
    land in one period: ``"{TYPE} +{pct}%"`` (percentage, e.g.
    ``"MERIT +2.5000%"`` -- the trailing places follow the stored
    ``Numeric(5, 4)`` precision) and ``"{TYPE} +${amount:,.2f}"`` (flat,
    e.g. ``"COLA +$2,000.00"``).  This cleaner reformats each event for
    display: the type word is title-cased (``COLA`` -> ``Cola``, matching
    the app-wide ``raise_type.name|title`` convention) and a percentage's
    trailing zeros are trimmed (``+2.5000%`` -> ``+2.5%``,
    ``+3.0000%`` -> ``+3%``).  Flat amounts keep their to-the-cent money
    formatting verbatim.  Pure string manipulation on the emitter's own
    Decimal-derived text -- no ``float``, no re-derivation.

    Splitting on ``", "`` is safe against flat-amount thousands
    separators: the emitter's join is comma-space while ``$2,000.00``'s
    comma is always followed by a digit.

    Args:
        raw_label: The verbatim ``PeriodInfo.raise_event`` string; may be
            empty (no raise event) or carry multiple comma-joined events.

    Returns:
        The cleaned display label, with the empty string passed through.
    """
    if not raw_label:
        return ""
    cleaned_events: list[str] = []
    for event in raw_label.split(", "):
        type_part, sep, amount_part = event.partition(" +")
        if not sep:
            # Not an emitter shape; pass through untouched rather than
            # mangling an unrecognised string.
            cleaned_events.append(event)
            continue
        if amount_part.endswith("%"):
            number = amount_part[:-1]
            if "." in number:
                number = number.rstrip("0").rstrip(".")
            amount_part = f"{number}%"
        cleaned_events.append(f"{type_part.title()} +{amount_part}")
    return ", ".join(cleaned_events)


def raise_run_starts(current_raise_event: str, prev_raise_event: str | None) -> bool:
    """Return True when a period STARTS a raise run.

    The run-start seam shared by every consumer that must collapse the
    calculator's badge-every-period behavior down to one event per raise:
    the "next raise" chip and the chart's per-run labels (via the
    pairs-indexed :func:`_is_raise_run_start`) and the paycheck-anatomy
    raise banner (the cockpit route passes the focused and preceding
    periods' raw ``raise_event`` strings directly, so the banner shows on
    the paycheck where a raise takes effect rather than repeating on every
    paycheck of the raise month -- P-SA1).

    A period starts a run when its display-cleaned label
    (:func:`clean_raise_label`) is non-empty AND differs from the
    immediately-preceding period's: a label change starts a new run, a
    no-event period ends any run, and the same label recurring after a gap
    (next year's COLA) starts a fresh run.

    Args:
        current_raise_event: The period's raw ``raise_event`` string (empty
            when it carries no raise event).
        prev_raise_event: The immediately-preceding period's raw
            ``raise_event`` string, or ``None`` when there is no
            predecessor (the first period).

    Returns:
        True iff the period carries a raise event whose cleaned label
        differs from the immediately-preceding period's (or it has no
        predecessor).
    """
    label = clean_raise_label(current_raise_event)
    if not label:
        return False
    if prev_raise_event is None:
        return True
    return clean_raise_label(prev_raise_event) != label


def _is_raise_run_start(pairs: list[PeriodPair], idx: int) -> bool:
    """Return True when ``pairs[idx]`` is the FIRST period of a raise run.

    Pairs-indexed adapter over :func:`raise_run_starts`: it lifts the
    focused and preceding periods' ``raise_event`` strings out of the
    ordered ``(period, breakdown)`` pairs (the predecessor is ``None`` at
    index 0).  Used by :func:`next_raise_after` and
    :func:`build_chart_series`, which scan the full pairs list.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        idx: Index into ``pairs`` of the period under test.

    Returns:
        True iff the period at ``idx`` starts a raise run.
    """
    prev = pairs[idx - 1][1].period.raise_event if idx > 0 else None
    return raise_run_starts(pairs[idx][1].period.raise_event, prev)


def base_regular_net(pairs: list[PeriodPair], idx: int) -> Decimal:
    """Return the net of the nearest regular paycheck at the same salary.

    A third-paycheck period skips the 24x deductions, so its net spikes
    above the regular per-paycheck net.  For the staircase chart line and
    the third-paycheck delta chip we need the "base" regular net at the
    same annual-salary level: the net of the nearest NON third-paycheck
    period sharing the period-at-``idx``'s effective annual salary.  Prefer
    the closest earlier period; fall back to the closest later one; and, in
    the degenerate case where no regular period at that salary exists, fall
    back to the period's own net.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        idx: Index into ``pairs`` of the third-paycheck period.

    Returns:
        The base regular net pay as a :class:`~decimal.Decimal`.
    """
    target_salary = pairs[idx][1].earnings.annual_salary
    for j in range(idx - 1, -1, -1):
        breakdown = pairs[j][1]
        if (not breakdown.period.is_third_paycheck
                and breakdown.earnings.annual_salary == target_salary):
            return breakdown.earnings.net_pay
    for j in range(idx + 1, len(pairs)):
        breakdown = pairs[j][1]
        if (not breakdown.period.is_third_paycheck
                and breakdown.earnings.annual_salary == target_salary):
            return breakdown.earnings.net_pay
    return pairs[idx][1].earnings.net_pay


def next_raise_after(pairs: list[PeriodPair], today: date) -> dict[str, object] | None:
    """Return the first projected raise RUN starting strictly after ``today``.

    Scans the breakdowns (reusing the calculator's ``raise_event`` string)
    for run STARTS only (:func:`_is_raise_run_start`): the calculator
    badges every period of a raise month, so a run whose start is on or
    before ``today`` already landed and is skipped ENTIRELY even when its
    tail periods start after ``today``.  Live-data shape: with today
    07/03 and a July COLA run 07/02-07/30, the 07/16 and 07/30 tails are
    not "the next raise" -- the honest answer is the following run (the
    January Merit).

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.

    Returns:
        ``{"label": str, "period_start": date}`` for the next raise run's
        first period (the label display-cleaned via
        :func:`clean_raise_label`, e.g. ``"Merit +2.5%"``), or ``None``
        when no raise run starts after ``today``.
    """
    for idx, (period, breakdown) in enumerate(pairs):
        if not _is_raise_run_start(pairs, idx):
            continue
        if period.start_date > today:
            return {
                "label": clean_raise_label(breakdown.period.raise_event),
                "period_start": period.start_date,
            }
    return None


def next_third_after(pairs: list[PeriodPair], today: date) -> dict[str, object] | None:
    """Return the first projected third paycheck strictly after ``today``.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.

    Returns:
        ``{"period_start": date, "net": Decimal, "delta": Decimal}`` where
        ``delta`` is the third-paycheck net minus the base regular net at
        the same salary level, or ``None`` when no future third paycheck
        exists in the projection.
    """
    for idx, (period, breakdown) in enumerate(pairs):
        if period.start_date > today and breakdown.period.is_third_paycheck:
            net = breakdown.earnings.net_pay
            return {
                "period_start": period.start_date,
                "net": net,
                "delta": net - base_regular_net(pairs, idx),
            }
    return None


def yearly_net_totals(pairs: list[PeriodPair]) -> list[tuple[int, Decimal]]:
    """Return per-calendar-year net-pay totals, ordered by year.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.

    Returns:
        A list of ``(year, total_net)`` tuples sorted ascending by year.
    """
    totals: dict[int, Decimal] = {}
    for period, breakdown in pairs:
        year = period.start_date.year
        totals[year] = totals.get(year, ZERO) + breakdown.earnings.net_pay
    return [(year, totals[year]) for year in sorted(totals)]


def build_chips(
    pairs: list[PeriodPair], focused_breakdown: PaycheckBreakdown, today: date,
) -> dict[str, object]:
    """Build the hero chip-row data for the focused period.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        focused_breakdown: The breakdown for the focused period.
        today: The reference date (drives the "next" lookups).

    Returns:
        A dict with keys ``gross`` (Decimal), ``annual_salary`` (Decimal),
        ``take_home_rate_pct`` (Decimal | None), ``next_raise`` (dict |
        None: ``label`` + ``period_start``), and ``third_paycheck`` (dict |
        None: ``period_start`` + ``delta``).  Each "next" value is ``None``
        when the projection carries no such future event.
    """
    third = next_third_after(pairs, today)
    third_chip = None
    if third is not None:
        third_chip = {
            "period_start": third["period_start"],
            "delta": third["delta"],
        }
    return {
        "gross": focused_breakdown.earnings.gross_biweekly,
        "annual_salary": focused_breakdown.earnings.annual_salary,
        "take_home_rate_pct": focused_breakdown.earnings.take_home_rate_pct,
        "next_raise": next_raise_after(pairs, today),
        "third_paycheck": third_chip,
    }


def build_composition(
    breakdown: PaycheckBreakdown, calibration_active: bool,
) -> dict[str, object]:
    """Build the "where this paycheck goes" composition-card data.

    All figures are Decimals; the four segment percentages (of gross) are
    precomputed to one decimal place so the template performs no math.

    Args:
        breakdown: The focused period's breakdown.
        calibration_active: Whether the profile's calibration is active.

    Returns:
        A dict with the gross/taxable/net/pre_tax_total/taxes_total/
        post_tax_total Decimals, the ``pct_net`` / ``pct_pre_tax`` /
        ``pct_taxes`` / ``pct_post_tax`` one-decimal percentages, and the
        ``federal_zero_calibrated`` flag.
    """
    gross = breakdown.earnings.gross_biweekly
    pre_tax_total = breakdown.deductions.total_pre_tax
    taxes_total = breakdown.taxes.total
    post_tax_total = breakdown.deductions.total_post_tax
    net = breakdown.earnings.net_pay
    return {
        "gross": gross,
        "taxable": breakdown.earnings.taxable_income,
        "net": net,
        "pre_tax_total": pre_tax_total,
        "taxes_total": taxes_total,
        "post_tax_total": post_tax_total,
        "pct_net": _pct_of_gross(net, gross),
        "pct_pre_tax": _pct_of_gross(pre_tax_total, gross),
        "pct_taxes": _pct_of_gross(taxes_total, gross),
        "pct_post_tax": _pct_of_gross(post_tax_total, gross),
        "federal_zero_calibrated": _calibration_zero_federal(breakdown, calibration_active),
    }


def build_deduction_rows(breakdown: PaycheckBreakdown) -> list[dict[str, object]]:
    """Build the proportional deduction bar-list for the focused period.

    Includes both timings, ordered for the locked bar-list design: the
    pre-tax group first, then post-tax, each sorted by amount DESCENDING
    (largest-first per group; equal amounts keep the calculator's stable
    order).  ``bar_pct`` is scaled so the largest single line across both
    groups renders at 100.0 (route-computed so the template does no math).

    Args:
        breakdown: The focused period's breakdown.

    Returns:
        A list of dicts, one per line item, each with ``name`` (str),
        ``amount`` (Decimal), ``timing`` (the DeductionTiming enum value,
        display grouping only), and ``bar_pct`` (Decimal, one decimal).
        Empty when the period has no deduction lines.
    """
    # ``sorted`` is stable, so equal amounts retain the calculator's
    # original line order within each timing group.
    pre_sorted = sorted(
        breakdown.deductions.pre_tax, key=lambda line: line.amount, reverse=True,
    )
    post_sorted = sorted(
        breakdown.deductions.post_tax, key=lambda line: line.amount, reverse=True,
    )
    lines = (
        [(line, DeductionTimingEnum.PRE_TAX.value) for line in pre_sorted]
        + [(line, DeductionTimingEnum.POST_TAX.value) for line in post_sorted]
    )
    if not lines:
        return []
    max_amount = max(line.amount for line, _timing in lines)
    rows: list[dict[str, object]] = []
    for line, timing in lines:
        if max_amount > ZERO:
            bar_pct = (line.amount / max_amount * HUNDRED).quantize(
                _PCT_QUANTUM, rounding=ROUND_HALF_UP,
            )
        else:
            bar_pct = ZERO
        rows.append({
            "name": line.name,
            "amount": line.amount,
            "timing": timing,
            "bar_pct": bar_pct,
        })
    return rows


def _window_with_index(
    pairs: list[PeriodPair], today: date, lookback: int,
) -> list[tuple[int, PayPeriod, PaycheckBreakdown]]:
    """Return the display window as ``(global_index, period, breakdown)``.

    The window anchors on the first period whose ``end_date`` is on or
    after ``today`` (the current or next period), reaches back ``lookback``
    periods before it, and extends forward to :data:`HORIZON_MONTHS` months
    after ``today``.  Both ends clamp to the available periods.  The global
    index is preserved so :func:`base_regular_net` can search the full list
    (a window-edge third paycheck still resolves its base against periods
    outside the window).

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.
        lookback: Periods to include before the anchor (6 for the net-pay
            staircase; 0 for the forward-only salary path).

    Returns:
        The windowed ``(index, period, breakdown)`` triples, in order.
    """
    if not pairs:
        return []
    anchor = next(
        (i for i, (period, _bd) in enumerate(pairs) if period.end_date >= today),
        len(pairs) - 1,
    )
    start = max(0, anchor - lookback)
    horizon_end = _add_months(today, HORIZON_MONTHS)
    result: list[tuple[int, PayPeriod, PaycheckBreakdown]] = []
    for i in range(start, len(pairs)):
        period, breakdown = pairs[i]
        if period.start_date > horizon_end:
            break
        result.append((i, period, breakdown))
    return result


def build_chart_series(pairs: list[PeriodPair], today: date) -> dict[str, object]:
    """Build the net-pay staircase chart series (Decimals throughout).

    The main ``periods`` line is a clean staircase of REGULAR-paycheck net
    so raise steps read instantly: a third-paycheck period carries the BASE
    value (:func:`base_regular_net`) on the line, while its actual spiked
    net is emitted separately in ``thirds`` as a point event.  Raise
    events are emitted in ``raises`` with display-cleaned labels
    (:func:`clean_raise_label`), collapsed to one entry per RUN: the
    calculator badges every period of a raise month, so only run starts
    (:func:`_is_raise_run_start`, judged against the FULL pairs list) are
    emitted -- one label per raise instead of a stacked label per period.
    A run that started before the window paints no label inside it.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.

    Returns:
        A dict with ``periods`` (list of ``{start: date, net: Decimal}``),
        ``thirds`` (list of ``{start: date, net: Decimal}``), ``raises``
        (list of ``{start: date, label: str}``), and ``today`` (date).
    """
    windowed = _window_with_index(pairs, today, PERIODS_BEFORE_TODAY)
    periods_series: list[dict[str, object]] = []
    thirds: list[dict[str, object]] = []
    raises: list[dict[str, object]] = []
    for idx, period, breakdown in windowed:
        if breakdown.period.is_third_paycheck:
            line_net = base_regular_net(pairs, idx)
            thirds.append({"start": period.start_date, "net": breakdown.earnings.net_pay})
        else:
            line_net = breakdown.earnings.net_pay
        periods_series.append({"start": period.start_date, "net": line_net})
        if _is_raise_run_start(pairs, idx):
            raises.append({
                "start": period.start_date,
                "label": clean_raise_label(breakdown.period.raise_event),
            })
    return {
        "periods": periods_series,
        "thirds": thirds,
        "raises": raises,
        "today": today,
    }


def build_salary_path(pairs: list[PeriodPair], today: date) -> dict[str, object]:
    """Build the annual-salary staircase (forward-only) for the salary card.

    Uses the same horizon as the net-pay chart but forward-only (no
    look-back), so the sparkline reads as future salary growth.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.

    Returns:
        A dict with ``points`` (list of ``{start: date, annual: Decimal}``)
        and ``end_label`` (a formatted "$<value> <Mon Year>" string for the
        final point, empty when the window is empty).
    """
    windowed = _window_with_index(pairs, today, 0)
    points = [
        {"start": period.start_date, "annual": breakdown.earnings.annual_salary}
        for _idx, period, breakdown in windowed
    ]
    end_label = ""
    if points:
        last = points[-1]
        end_label = f"${last['annual']:,.0f} {last['start'].strftime('%b %Y')}"
    return {"points": points, "end_label": end_label}
