"""
Shekel Budget App -- Analytics View-Model Builders

The route-layer presentation helpers for the analytics blueprint,
separated from :mod:`app.routes.analytics` so that module holds the HTTP
handlers (request parsing, auth, redirects, ``render_template``) and this
one holds the pure "turn a service report into template-ready data" logic
(Single Responsibility; it also keeps the handler module under the
1000-line ceiling as the page grows one slice at a time).

Every function here is pure: it takes a service report (or plain scalars)
and returns dicts / JSON strings / SVG point strings, with NO Flask
request context, NO database access, and NO models.  This is the one layer
where ``float`` is allowed -- at the Chart.js / SVG serialization boundary
(coding-standards: floats live only here, never in a money calculation);
the money values themselves are computed as ``Decimal`` in the services.
"""

import calendar as cal_mod
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# ── Taxes tab display ──────────────────────────────────────────────

# Two-decimal display quantum for the Taxes tab's percentage chips
# (rates arrive as 4-dp fractions; render as 0.01%-resolution percents).
_PCT_QUANTUM = Decimal("0.01")
_HUNDRED = Decimal("100")


def build_taxes_display(report):
    """Compute the Taxes tab's route-layer display values.

    The presentation splits every signed figure into a magnitude plus a
    direction flag (a "$1,234 owed" hero must not render "-$1,234 owed"),
    and scales the 4-dp rate fractions to 2-dp percents -- Decimal display
    math that belongs at the route layer (the ``build_variance_chart_data``
    precedent), never in the template.

    Args:
        report: The populated
            :class:`~app.services.tax_report_service.TaxReport`.

    Returns:
        dict with the hero/chip magnitudes and direction flags, the
        percent-scaled rates (``None`` propagated for a ``None`` effective
        rate or state rate), and the Schedule A margin split.
    """
    refund = report.refund
    effective = report.chips.effective_rate
    state_rate = report.liability.state.flat_rate
    return {
        "hero_is_refund": refund.total_refund >= 0,
        "hero_amount": abs(refund.total_refund),
        "federal_is_refund": refund.federal_refund >= 0,
        "federal_amount": abs(refund.federal_refund),
        "state_is_refund": refund.state_refund >= 0,
        "state_amount": abs(refund.state_refund),
        "effective_pct": (
            (effective * _HUNDRED).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP)
            if effective is not None else None
        ),
        "marginal_pct": (
            report.chips.marginal_rate * _HUNDRED
        ).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP),
        "state_rate_pct": (
            (state_rate * _HUNDRED).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP)
            if state_rate is not None else None
        ),
        "margin_positive": report.schedule_a.margin > 0,
        "margin_amount": abs(report.schedule_a.margin),
    }


# ── Spending tab display ───────────────────────────────────────────
# The hero comparison magnitude/direction split (the ``build_taxes_display``
# precedent) and the sparkline SVG geometry (the ``serialize_flow_strip``
# float boundary).

# Sparkline geometry for the per-category trend cells: a 100 x 24 user-unit
# viewBox with vertical padding so the peak and trough are not clipped by the
# stroke width.  The polyline is scaled into this box here (the one place
# ``float`` is allowed -- presentation geometry, never a money value); the
# template renders the point string verbatim.
_SPARK_WIDTH = 100
_SPARK_HEIGHT = 24
_SPARK_PAD = 3


def prev_month(year, month):
    """Return the ``(year, month)`` one calendar month before the argument.

    Args:
        year: The reference calendar year.
        month: The reference calendar month (1-12).

    Returns:
        The prior month's ``(year, month)``, rolling the year back at January.
    """
    if month == 1:
        return year - 1, 12
    return year, month - 1


def next_month(year, month):
    """Return the ``(year, month)`` one calendar month after the argument.

    Args:
        year: The reference calendar year.
        month: The reference calendar month (1-12).

    Returns:
        The next month's ``(year, month)``, rolling the year forward at
        December.
    """
    if month == 12:
        return year + 1, 1
    return year, month + 1


def build_spending_nav(today, year, month):
    """Build the Spending tab's month-navigation context.

    Args:
        today: The current date in the display timezone.
        year: The shown calendar year.
        month: The shown calendar month (1-12).

    Returns:
        dict of the month label, the prev/next month targets (with the prior
        month's name for the vs-prior chip caption), an ``is_current_month``
        flag for the in-progress caption, and ``can_go_next`` -- ``False``
        once the shown month is the current month or later, capping forward
        navigation on this measured surface.
    """
    prev_year, prev_mon = prev_month(year, month)
    next_year, next_mon = next_month(year, month)
    return {
        "month_name": cal_mod.month_name[month],
        "prev_year": prev_year,
        "prev_month": prev_mon,
        "prev_month_name": cal_mod.month_name[prev_mon],
        "next_year": next_year,
        "next_month": next_mon,
        "is_current_month": year == today.year and month == today.month,
        "can_go_next": (year, month) < (today.year, today.month),
    }


def build_spending_display(report):
    """Compute the Spending tab's route-layer hero comparison display.

    Splits each hero comparison into a magnitude plus a direction the template
    renders without money math (the ``build_taxes_display`` precedent): a
    positive delta is more spending ("up", danger), a negative delta is less
    ("down", done).  A comparison with no baseline (no prior window / no
    trailing windows) collapses to ``None`` so the template omits the chip.

    Args:
        report: The populated
            :class:`~app.services.spending_report_service.SpendingReport`.

    Returns:
        dict with ``vs_prior`` and ``vs_average`` comparison-display dicts
        (or ``None`` each).
    """
    return {
        "vs_prior": _comparison_display(report.hero.vs_prior),
        "vs_average": _comparison_display(report.hero.vs_average),
    }


def _comparison_display(comparison):
    """Render one hero comparison as a magnitude / direction / percent dict.

    Args:
        comparison: A
            :class:`~app.services.spending_report_service.Comparison`.

    Returns:
        A dict with the absolute ``amount`` (magnitude), the ``direction``
        (``"up"`` / ``"down"`` / ``"flat"``), and the signed ``pct`` (or
        ``None``), or ``None`` when the comparison has no baseline (its delta
        is ``None``).
    """
    if comparison.delta is None:
        return None
    if comparison.delta > 0:
        direction = "up"
    elif comparison.delta < 0:
        direction = "down"
    else:
        direction = "flat"
    return {
        "amount": abs(comparison.delta),
        "direction": direction,
        "pct": comparison.pct,
    }


def spending_sparklines(report):
    """Serialize each trendable category's sparkline to SVG polyline points.

    Walks the breakdown and, for every item carrying a trend, converts its
    per-period series into a ``"x,y x,y ..."`` point string sized to the
    :data:`_SPARK_WIDTH` x :data:`_SPARK_HEIGHT` viewBox.  The template renders
    the string in an inline ``<polyline>`` (SVG geometry attributes are not
    governed by ``style-src``, so no inline-style CSP concern); the stroke
    colour comes from a direction CSS class, so the cell is theme-reactive
    with no JS.

    Args:
        report: The populated
            :class:`~app.services.spending_report_service.SpendingReport`.

    Returns:
        dict mapping ``category_id`` to its polyline point string, for every
        item whose ``trend`` is populated.
    """
    points = {}
    for group in report.breakdown:
        for item in group.items:
            if item.trend is None:
                continue
            points[item.category_id] = _sparkline_points(
                item.trend.series, item.trend.is_flat,
            )
    return points


def _sparkline_points(series, is_flat):
    """Scale a per-period Decimal series into SVG polyline points.

    The single ``float`` boundary for the sparkline (presentation geometry,
    never a money value): x is evenly spaced across the width; y inverts the
    normalized value so a higher dollar amount sits higher on screen.  A flat
    series (the producer's flat-guard) or a zero-range series renders as a
    centered horizontal line rather than stretching sub-percent noise to full
    height.

    Args:
        series: The chronological per-period totals (``Decimal``).
        is_flat: The producer's flat-guard flag for this series.

    Returns:
        A ``"x,y x,y ..."`` point string in the viewBox's user units (empty
        for an empty series -- the trend contract never yields one, so the
        empty return is a defensive floor).
    """
    count = len(series)
    if count == 0:
        return ""
    values = [float(value) for value in series]
    low, high = min(values), max(values)
    usable = _SPARK_HEIGHT - 2 * _SPARK_PAD
    flat = is_flat or high == low
    coords = []
    for index, value in enumerate(values):
        x = (
            _SPARK_WIDTH / 2 if count == 1
            else index * _SPARK_WIDTH / (count - 1)
        )
        if flat:
            y = _SPARK_HEIGHT / 2
        else:
            y = _SPARK_PAD + usable * (1 - (value - low) / (high - low))
        coords.append(f"{x:.2f},{y:.2f}")
    return " ".join(coords)


# ── Calendar tab serialization ─────────────────────────────────────


def serialize_flow_strip(data, low_balance, today, year, month):
    """Serialize the month flow strip series to a JSON string.

    The calendar's single Chart.js serialization boundary (coding-standards:
    floats live only here, never in a calculation), mirroring the dashboard's
    ``_serialize_chart``.  Emits one point per calendar day of the month from
    the daily running-balance view the service computed
    (:class:`~app.services.calendar_service.DailyView`), plus the display
    indices the strip needs:

    - ``current_index``: the count of measured days (points ``[0,
      current_index)`` render solid, the rest dashed) -- ``today.day``
      inside the month, ``0`` for a wholly future month (all dashed), the
      day count for a wholly past month (all solid).  Matches the
      net-worth cockpit's ``current_index`` semantics so the shared
      ``splitSegment`` / ``todayMarkerPlugin`` helpers apply unchanged.
    - ``threshold``: the user's low-balance-threshold setting (the same
      source the grid and dashboard read -- Calendar rebuild decision 4).
    - ``payday_indices`` / ``trough_index``: 0-based day indices for the
      payday dots and the labeled trough dot.
    - ``week_tick_indices``: the 1st of the month plus every Sunday
      (0-based), the strip's weekly gridline/tick positions -- Sundays
      match the calendar grid's week start.

    Args:
        data: The month's :class:`MonthSummary` (``daily`` must be present;
            returns ``None`` when it is not, and the template skips the
            strip).
        low_balance: The user's low-balance threshold (whole dollars).
        today: The current date in the display timezone.
        year: Target calendar year.
        month: Target calendar month (1-12).

    Returns:
        A JSON string for the canvas ``data-chart`` attribute, or ``None``
        when no daily view was computed.
    """
    if data.daily is None:
        return None
    days_in_month = cal_mod.monthrange(year, month)[1]
    first_day = date(year, month, 1)
    last_day = date(year, month, days_in_month)

    if today < first_day:
        current_index = 0
    elif today > last_day:
        current_index = days_in_month
    else:
        current_index = today.day

    week_ticks = sorted({0} | {
        day - 1 for day in range(1, days_in_month + 1)
        if date(year, month, day).weekday() == cal_mod.SUNDAY
    })

    balances = data.daily.daily_balances
    return json.dumps({
        "labels": [
            date(year, month, day).strftime("%b %-d")
            for day in range(1, days_in_month + 1)
        ],
        "values": [
            float(balances[day]) for day in range(1, days_in_month + 1)
        ],
        "current_index": current_index,
        "threshold": float(low_balance),
        "payday_indices": [day - 1 for day in data.paycheck_days],
        "trough_index": (
            data.daily.trough_day - 1
            if data.daily.trough_day is not None else None
        ),
        "week_tick_indices": week_ticks,
    })


def build_calendar_weeks(year, month, data, today):
    """Build a list of week rows for the calendar grid.

    Each week is a list of 7 day dicts with keys: number, entries,
    is_paycheck, is_today, is_modeled, income_total, expense_total,
    daily_balance, overflow.  ``daily_balance`` is the day's projected
    end-of-day running balance (``None`` before the pay-period horizon or
    when no daily view was computed); ``overflow`` is the day's "+N more"
    residual or ``None``.  ``is_modeled`` marks days AFTER today in the
    display timezone -- the measured/modeled treatment is a date split, not
    a status split (Calendar rebuild decision 7): the cell's balance hero
    renders in secondary ink with a leading tilde on modeled days.
    Empty cells have number=0.  Uses Sunday as the first day of the week.
    """
    # Sunday-start calendar (firstweekday=6 in Python's calendar).
    cal = cal_mod.Calendar(firstweekday=6)
    month_weeks = cal.monthdayscalendar(year, month)

    paycheck_set = set(data.paycheck_days)
    daily_balances = data.daily.daily_balances if data.daily else {}

    weeks = []
    for week in month_weeks:
        row = []
        for day_num in week:
            if day_num == 0:
                row.append({
                    "number": 0,
                    "entries": [],
                    "is_paycheck": False,
                    "is_today": False,
                    "is_modeled": False,
                    "income_total": Decimal("0"),
                    "expense_total": Decimal("0"),
                    "daily_balance": None,
                    "overflow": None,
                })
            else:
                entries = data.day_entries.get(day_num, [])
                is_today = (
                    year == today.year
                    and month == today.month
                    and day_num == today.day
                )
                # calendar_service folds the per-day income/expense totals
                # (one rule, shared with the month headline); the route
                # only renders them.
                totals = data.day_totals.get(
                    day_num, (Decimal("0"), Decimal("0")),
                )
                row.append({
                    "number": day_num,
                    "entries": entries,
                    "is_paycheck": day_num in paycheck_set,
                    "is_today": is_today,
                    "is_modeled": (
                        (year, month, day_num)
                        > (today.year, today.month, today.day)
                    ),
                    "income_total": totals[0],
                    "expense_total": totals[1],
                    "daily_balance": daily_balances.get(day_num),
                    "overflow": data.day_overflow.get(day_num),
                })
        weeks.append(row)
    return weeks


# ── Variance tab chart data ────────────────────────────────────────


def build_variance_chart_data(report):
    """Build chart data dict from a VarianceReport.

    Converts Decimal values to float for JSON serialization in
    template data attributes.  Includes the per-group ``variance``
    array (``actual - estimated``) computed server-side so the
    variance tooltip in ``chart_variance.js`` renders without
    recomputing it client-side (MED-04 / E-17 / JN-03).

    Args:
        report: VarianceReport from the variance service.

    Returns:
        dict with labels, estimated, actual, and variance lists.
    """
    return {
        "labels": [g.group_name for g in report.groups],
        "estimated": [float(g.figures.estimated) for g in report.groups],
        "actual": [float(g.figures.actual) for g in report.groups],
        "variance": [float(g.figures.variance) for g in report.groups],
    }
