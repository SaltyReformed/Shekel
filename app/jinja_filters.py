"""Presentation-only Jinja template filters.

Registered on the application in :func:`app.create_app` via
:func:`register_template_filters`.  Extracted from the factory
(``app/__init__.py``) so that module stays under its statement / line
ceiling and so display helpers have a home that is not the
already-large factory.

Every filter here is a DISPLAY transform: it formats or relabels a
value the route or service already computed.  None performs financial
arithmetic -- monetary math lives in the services per the project's
"templates display, never compute" rule (CLAUDE.md).  The two arithmetic
helpers below (``to_percent``, ``months_to_years``) operate on rates and
term lengths, not money, and exist precisely so templates do not inline
that math.
"""

from datetime import datetime
from decimal import Decimal

from flask import Flask

from app.utils.dates import to_display_tz

# English month names, indexed by ``month_number - 1``.  The single
# source of truth for the per-template ``month_names`` dicts that used to
# be hand-maintained in six templates plus an inline list (polyglot audit
# TPLB/TPL-07): the ``month_name`` filter reads these so callers no longer
# define their own.
_MONTH_NAMES_FULL: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTH_NAMES_ABBR: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# Months in a year -- named so the year conversion is not a bare literal.
_MONTHS_PER_YEAR = 12


def to_percent(value: Decimal | None) -> Decimal | None:
    """Convert a storage-domain decimal-fraction rate into its percent.

    Presentation transformation only (E-16 / MED-04): the rate is
    stored as ``Decimal("0.07")`` for 7 %, the user-facing display is
    ``7.00 %``.  Multiplying by ``100`` in :class:`~decimal.Decimal`
    preserves the stored precision; the older Jinja pattern
    ``value|float * 100`` introduced a binary-float cast on the Decimal
    before the multiply and is no longer used anywhere.

    Args:
        value: Decimal storage-domain rate, or ``None``.

    Returns:
        ``value * 100`` as a Decimal, or ``None`` when ``value`` is
        ``None``.  Numeric formatting (``"%.2f"|format(...)``) is applied
        by the caller; this filter never quantises so the caller's chosen
        precision wins.
    """
    if value is None:
        return None
    return Decimal(str(value)) * Decimal("100")


def local_datetime(value: datetime | None, fmt: str = "%b %-d, %Y") -> str:
    """Render a stored UTC instant in the user's display timezone.

    Presentation-only conversion: every ``timestamptz`` in this app is
    stored UTC; this expresses one in
    :data:`app.utils.dates.DISPLAY_TIMEZONE` (Eastern) before formatting,
    so a late-evening Eastern event does not display on the next UTC day.
    ``fmt`` is a ``strftime`` format (default: ``"Jun 11, 2026"``).
    Returns ``""`` for ``None`` so a template can pipe an absent timestamp
    through without guarding.

    Args:
        value: A stored UTC datetime, or ``None``.
        fmt: A ``strftime`` format string.

    Returns:
        The display-timezone formatted string, or ``""`` when ``value``
        is ``None``.
    """
    if value is None:
        return ""
    return to_display_tz(value).strftime(fmt)


def ordinal(value: int | None) -> str:
    """Render an integer with its English ordinal suffix (``1`` -> ``"1st"``).

    Display helper for day-of-month rendering (polyglot audit TPLB/TPL-12,
    moving the inline ternary out of ``loan/dashboard.html``).  Handles the
    11/12/13 "teens" exception, so 11/12/13 are ``"th"`` while 1/21/31 are
    ``"st"``, 2/22 ``"nd"``, 3/23 ``"rd"``.

    Args:
        value: The integer to suffix, or ``None``.

    Returns:
        ``"<n><suffix>"`` (e.g. ``"21st"``), or ``""`` when ``value`` is
        ``None``.
    """
    if value is None:
        return ""
    number = int(value)
    if 11 <= number % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def months_to_years(months: int | None, digits: int = 1) -> int | float | str:
    """Convert a term length in months to years for display (24 -> ``2.0``).

    Display helper for loan term rendering (polyglot audit TPLB/TPL-12,
    replacing inline ``(term_months / 12)|round(...)`` in the loan
    templates).  Not money: a term length, kept out of the template per
    the "templates display, never compute" rule.

    Args:
        months: Whole-month term length, or ``None``.
        digits: Decimal places to round to.  ``0`` returns a whole-year
            :class:`int` (matching the old ``|round(0)|int`` site); a
            positive value returns a rounded :class:`float` (matching the
            old ``|round(1)`` site).

    Returns:
        The year value as an ``int`` (``digits == 0``) or ``float``
        (``digits > 0``), or ``""`` when ``months`` is ``None``.
    """
    if months is None:
        return ""
    years = round(int(months) / _MONTHS_PER_YEAR, digits)
    if digits == 0:
        return int(years)
    return years


def month_name(value: int | None, abbr: bool = False) -> str:
    """Map a 1-12 month number to its English name (``1`` -> ``"January"``).

    Single source for the month-name lookups that used to be duplicated as
    per-template ``month_names`` dicts (polyglot audit TPLB/TPL-07).  Pass
    ``abbr=True`` for the three-letter form (``"Jan"``) used by the list
    views; the default full name (``"January"``) matches the form selects.

    Args:
        value: A month number in ``1..12``, or ``None``.
        abbr: ``True`` for the abbreviated name, ``False`` for the full
            name.

    Returns:
        The month name, or ``""`` for ``None`` or an out-of-range number.
    """
    if value is None:
        return ""
    index = int(value)
    if not 1 <= index <= _MONTHS_PER_YEAR:
        return ""
    names = _MONTH_NAMES_ABBR if abbr else _MONTH_NAMES_FULL
    return names[index - 1]


def register_template_filters(app: Flask) -> None:
    """Register every presentation filter on the given Flask app.

    Called once from :func:`app.create_app`.  Idempotent: re-registering
    the same name overwrites it with the same callable, so a repeat call
    has no observable effect.

    Args:
        app: The Flask application whose ``jinja_env`` gains the filters.
    """
    app.add_template_filter(to_percent, "to_percent")
    app.add_template_filter(local_datetime, "local_datetime")
    app.add_template_filter(ordinal, "ordinal")
    app.add_template_filter(months_to_years, "months_to_years")
    app.add_template_filter(month_name, "month_name")
