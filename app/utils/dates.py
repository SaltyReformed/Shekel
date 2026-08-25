"""
Shekel Budget App -- Calendar Date Utilities

Pure date-arithmetic helpers shared across the projection services, plus
the presentation-layer timezone conversion used to render stored UTC
instants in the user's wall clock.  No Flask, no SQLAlchemy: these
operate on :class:`datetime.date` / :class:`datetime.datetime` values
only, so they import cleanly into any service or test without the app
stack.
"""
import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

# Single source of truth for the timezone the UI presents instants in.
# Storage and every backend computation stay UTC (each ``timestamptz``
# column is stored UTC by ``CreatedAtMixin``/``TimestampMixin``); this
# constant governs DISPLAY only, converting a stored UTC instant to the
# user's wall clock at the presentation boundary.  ``America/New_York``
# is a DST-aware zone, so the rendered clock is EDT (UTC-4) in summer and
# EST (UTC-5) in winter rather than a wrong fixed offset.
#
# Note: the anchor-history dedupe index keys the STORED ``observed_on``
# column (``app/models/account.py``) since plan step 2, so it needs no
# timezone expression at all and buckets the same civil day this constant
# defines.  It bucketed ``created_at`` by UTC day until then -- an
# independent zone, which is what made two assertions on two Eastern days
# sharing a UTC day collide (finding N-133 / F12).
DISPLAY_TIMEZONE = ZoneInfo("America/New_York")

# How far this application's calendar reaches: the ONE opinion every layer
# states about which dates a user may put on record.
#
# **Here rather than in the validation layer, since plan step R7c-b.**  They
# were declared in ``app/schemas/validation/_helpers.py``, which is the right
# home while only a SCHEMA enforces them -- and it stopped being the only one:
# ``budget.recurrence_rules.starts_on`` is authored, ``NOT NULL`` and reachable
# through a door no schema stands in front of (the recurrence preview reads it
# from ``request.args``), so ``services/recurrence/_resolution.py`` mirrors the
# bound too.  A service may not import from the validation layer, and the
# alternative was a second pair of numbers that could drift from these; this
# module is what both layers already depend on.  The schema keeps its names as
# re-exports, so nothing that imported them from there had to move.
#
# The values are the ones ``routes/salary/tax_config.py`` uses for a tax YEAR.
# Two tables mirror them for writers that never see a schema:
# ``ck_template_amount_versions_effective_date_range`` and
# ``ck_recurrence_rules_starts_on_range``.
#
# An HTML date input accepts a four- or five-digit-year typo, and both columns
# STORE what they are given: a stray ``0202`` becomes a template's earliest
# amount version -- anchoring every date before the series, which the
# withdrawal door refuses to remove -- and a stray ``9999`` overflows the pay
# calendar's forward projection with an ``OverflowError`` no handler catches.
CALENDAR_DATE_MIN: date = date(2000, 1, 1)
CALENDAR_DATE_MAX: date = date(2100, 12, 31)


def to_display_tz(value: datetime) -> datetime:
    """Convert a stored UTC instant to the UI display timezone.

    Presentation-only (E-16 sibling of ``to_percent``): the database and
    all backend logic operate in UTC; this is the boundary that expresses
    a stored instant in the user's wall clock (:data:`DISPLAY_TIMEZONE`).

    A naive ``value`` is assumed to be UTC -- every ``timestamptz`` in
    this app is stored UTC, but a value that has lost its tzinfo (e.g. a
    naive test fixture) would otherwise be interpreted in the server's
    local zone by ``astimezone``, silently shifting the rendered day.

    Args:
        value: A timezone-aware (or naive-assumed-UTC) datetime.

    Returns:
        The same instant expressed in :data:`DISPLAY_TIMEZONE`.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TIMEZONE)


def to_display_date(value: datetime | None) -> date | None:
    """Return the calendar date of a stored UTC instant in display tz.

    The day-of-record the user sees: the instant converted to
    :data:`DISPLAY_TIMEZONE` first, then truncated to a date, so a
    late-evening Eastern event does not roll onto the next UTC day.
    ``None``-safe so callers can pass an absent timestamp (e.g. an anchor
    that has never been set) straight through.

    Args:
        value: A stored UTC instant, or ``None``.

    Returns:
        The display-timezone calendar date, or ``None`` when ``value`` is
        ``None``.
    """
    if value is None:
        return None
    return to_display_tz(value).date()


def display_today() -> date:
    """Return the user's wall-clock 'today' in :data:`DISPLAY_TIMEZONE`.

    The calendar date it is right now on the user's clock, for surfaces that
    select a civil WINDOW (a tax/calendar year, a "this month") by the user's
    timezone rather than the server's UTC day.  Storage and the resolver's
    replay boundary stay UTC (``date.today()``); this is the presentation-layer
    "now".

    It keeps a display-tz-attributed figure in the SAME civil year as the
    analytics Taxes tab -- which already derives its year this way
    (``app/routes/analytics.py``) -- around the New Year boundary, when UTC has
    ticked to the next year but the user's Eastern clock has not.  The loan
    page's YTD interest / principal chips are exactly such a figure: their
    producer sums each payment by its display-tz civil paid date (the L9 rule),
    so the year they are summed IN must be the display-tz year to match.

    Returns:
        The current calendar date in :data:`DISPLAY_TIMEZONE`.
    """
    return to_display_tz(datetime.now(timezone.utc)).date()


def has_settled_by(settled_on: date | None, as_of: date) -> bool:
    """Return whether a row's cash had already moved on or before *as_of*.

    The companion question to
    :func:`app.utils.balance_predicates.settled_day`: that one answers WHICH day
    a settled row's money moved, this one answers whether it had moved YET.
    Both read the one stored fact ``transactions.settled_on``, so a caller
    cannot reach a different answer by comparing a different date.

    **It lives HERE, not beside** :func:`~app.utils.balance_predicates.settled_day`,
    on a purity argument that its consumers force.  It reads no status, no ORM
    row and no reference cache -- it is two ``date`` values and a comparison --
    while ``balance_predicates`` imports :class:`~app.models.transaction.Transaction`
    and :mod:`app.ref_cache`.  Its two callers are
    :mod:`app.services.rate_period_engine`, whose module docstring promises "no
    Flask, no ``db``", and :mod:`app.services.loan_resolver`, which goes to the
    trouble of a ``TYPE_CHECKING``-only import to stay a runtime model-free leaf
    (``loan_resolver/_periods.py``).  Putting it in ``balance_predicates`` would
    have given both a runtime edge to a model-importing module for a two-date
    comparison; this module is stdlib-only and both already import from it.

    **The two take opposite positions on a missing day, deliberately.**
    :func:`~app.utils.balance_predicates.settled_day` REFUSES ``None``, because
    its caller is holding a row it believes is settled and a missing day means
    the settled-iff-dated invariant is broken.  This one ACCEPTS it and answers
    ``False``: its callers classify a MIXED feed of settled and projected rows,
    and a projected row legitimately carries no day.  A row that has not settled
    has not settled by any date.

    **Written once, and the reason is the LEDGER, not a measured drift between
    its two callers** (plan step **X-an**, finding **N-187**).  The loan resolver
    splits its payment feed on this ONE predicate: the HISTORY side goes to
    :func:`app.services.rate_period_engine.replay_schedule` and the rest to
    ``loan_resolver._payoff._build_monthly_override``, which plans it.  They had
    two inline comparisons and those AGREED -- both read the payment's
    PAY-PERIOD start -- so the split stayed clean while the rule itself was
    wrong.  What they disagreed with was the posted ledger, which counts the same
    payment from the day it settled
    (:func:`app.services.loan_ledger.payment_visible_on`).  Naming the rule once
    is what stops the NEXT edit moving one side: the two spellings are the
    latent hazard, the ledger divergence is the measured one.

    **This predicate is the WHOLE split, and the replay then drops more.**  A
    payment this answers ``False`` for is planned, always.  A payment it answers
    ``True`` for is a CANDIDATE for the replay, which then excludes the ones an
    anchor subsumes (``due_date <= anchor_date``) and the ones past a payoff --
    and neither is handed back to the plan, correctly, because the anchor
    already contains them and a paid-off loan owes nothing.  So the two consumers
    are disjoint and their union is the SETTLED-BY set, not the whole feed.

    **Measured on production before the move** (both live loans, every day of a
    306-day span): no balance moved on any day, and the loan's amortization
    SCHEDULE lost a whole installment on 34 (loan, day) pairs -- a ``$1,910.95``
    mortgage payment absent from the confirmed history AND from the forward plan
    for the 12 days between its pay period opening and its cash leaving, with the
    page meanwhile naming the FOLLOWING month's installment as the next one due.

    Args:
        settled_on: The row's stored ``settled_on``, or ``None`` when the row has
            not settled.  Takes the VALUE rather than the row, for the same
            reason :func:`~app.utils.balance_predicates.settled_day` does:
            callers hold ORM attributes, batched query tuples and pure value
            objects alike.
        as_of: The evaluation date.

    Returns:
        ``True`` iff *settled_on* is a day at or before *as_of*.
    """
    return settled_on is not None and settled_on <= as_of


class DatedAssertion(Protocol):
    """The three fields a loan anchor's chronological position is built from.

    A structural type, not a base class: it names what
    :func:`anchor_chronology_key` reads without forcing its callers onto a
    concrete class.  The production value is
    :class:`app.services.loan_loaders.LoanAnchorFact`, which this module must
    not import (it would give a stdlib-only module a runtime edge to the model
    layer -- the same purity argument :func:`has_settled_by` makes above).

    Attributes:
        anchor_date: The civil day the balance was asserted FOR -- the business
            date, and the first term.
        created_at: The instant the assertion was RECORDED, aware-UTC.
        event_id: The stored ``budget.loan_anchor_events.id``.
    """

    anchor_date: date
    created_at: datetime
    event_id: int


def anchor_chronology_key(
    anchor: DatedAssertion,
) -> tuple[date, datetime, int]:
    """Return a loan anchor's position in its loan's ONE chronology.

    **The single definition of "which of a loan's balance assertions is later",
    written once and called by both consumers that must agree on it** (plan step
    X-an-b, closing finding **N-196**):

    * :func:`app.services.loan_loaders.load_loan_anchor_facts` sorts its facts by
      this, so the list every reader receives is already in chronological order;
    * :func:`app.services.loan_resolver.select_latest_anchor` takes the ``max()``
      of it, so the resolver seeds from the greatest whatever order it is handed.

    They must name the SAME row -- the walk resets the running balance at each
    anchor in turn, so the last one it sees decides the posted balance, while the
    resolver's seeds the replayed one.  Before this function they were two inline
    tuples, and the tuples had drifted: neither carried the row id, and
    ``max()`` returns the FIRST maximal element where the walk applies the LAST,
    so a tie named opposite rows.

    **It lives HERE for the reason :func:`has_settled_by` does, one step
    earlier in the same arc.**  Its two callers cannot share a module: the
    resolver is a runtime model-free leaf (``loan_resolver/_periods.py`` takes a
    ``TYPE_CHECKING``-only import to stay one) and ``loan_loaders`` imports
    models and ``db`` while declaring itself a leaf that imports "never another
    loan service".  So neither can import the other, and a key spelled in both
    would be two statements kept in step by hope.  This module is stdlib-only
    and both already depend on it.

    **All three terms, and why none is optional.**  ``anchor_date`` is the
    business day the assertion is ABOUT.  ``created_at`` orders two assertions
    about one business day, so the last one recorded is that day's closing
    balance -- and the synthesized origination fact carries the earliest possible
    instant, so a true-up asserted ON the origination date still outranks it.
    ``event_id`` makes the key TOTAL: ``created_at`` is
    ``server_default=func.now()``, which PostgreSQL evaluates at TRANSACTION
    START, so every row written in one transaction shares an instant --
    ``shekel-prod-db`` carries four such rows today, and they escape a tie only
    because no two of them share an ``anchor_date``.  The higher id wins, which
    is the rule the write door
    (:func:`app.services.anchor_service._governing_loan_anchor`) and both cash
    orderings already apply.

    Pure: three attribute reads and a tuple, no I/O and no clock.

    Args:
        anchor: Any :class:`DatedAssertion` -- an object exposing
            ``anchor_date``, ``created_at`` and ``event_id``.  Takes the OBJECT
            rather than the three values because its callers pass it as a
            ``key=`` function, which is the point: a caller that unpacked the
            fields itself would be free to reassemble them differently.

    Returns:
        The ``(anchor_date, created_at, event_id)`` tuple, ascending-comparable.
    """
    return (anchor.anchor_date, anchor.created_at, anchor.event_id)


def utc_instant(instant: datetime) -> datetime:
    """Return *instant* as an aware-UTC ``datetime``.

    The storage convention, applied once: an aware value converts to UTC, a
    naive value is assumed UTC (every ``timestamptz`` in this app is stored
    UTC).  Normalizing every stored instant through this one helper is what
    makes an instant-vs-instant comparison well-defined -- Python refuses to
    compare a naive datetime with an aware one, so a single un-normalized value
    is a ``TypeError`` at read time rather than a wrong answer.

    **It no longer answers "was this settle inside the asserted balance", and
    the paragraph that said it did was the deleted rule's argument** (ruling
    R-DH, 2026-07-31).  That paragraph cited one production pair -- an anchor at
    12:57:08 UTC and two expenses at 13:07 the same day, $108.15 -- as proof
    that the partition must turn on ORDER WITHIN A DAY.  Scored over the whole
    account rather than that one pair, the instant partition cost ``$4,001.42``
    on a single day and ``$40,554.34`` gross across four months, and the day
    rule books a SMALLER correction at that very assertion ($39.27 against
    $68.88).  The question is now answered by comparing two civil days --
    ``CashSourceFact.settled_on`` against ``CashAnchorFact.observed_on``, both
    stored or resolved once -- and the instants this helper normalizes only
    break a tie between two assertions about the SAME day.  Leaving the old
    argument here is how the old rule gets reintroduced, which is why the
    helpers that implemented it were deleted rather than kept.

    Args:
        instant: A stored ``created_at`` / ``asserted_at`` instant.  Naive
            values are assumed UTC.  (``paid_at`` was a third such caller
            until plan step X-f1 replaced it with a stored civil day.)

    Returns:
        The aware-UTC equivalent of *instant*.
    """
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def days_in_range(first_day: date, last_day: date) -> "list[date]":
    """Return every civil day in an inclusive range, ascending.

    Args:
        first_day: The first day.
        last_day: The last day.

    Returns:
        The days, or ``[]`` when *last_day* precedes *first_day* -- an inverted
        range is an empty range rather than an error, because callers that
        clamp one end (a report bounded at the reader's NOW) reach it
        legitimately.

    **One statement of a five-line loop three callers had written separately**
    -- twice in ``balance_at._cash_flow`` sixty lines apart, where cross-module
    ``duplicate-code`` cannot see them, and once in ``services.bank_agreement``.
    Found by adversarial review 2026-08-24.
    """
    days: "list[date]" = []
    day = first_day
    while day <= last_day:
        days.append(day)
        day += timedelta(days=1)
    return days


def add_months(start: date, months: int) -> date:
    """Add ``months`` calendar months to ``start``, day-clamped.

    The result's day is clamped to the target month's last day, so
    ``add_months(date(2026, 1, 31), 1)`` yields ``date(2026, 2, 28)``
    rather than raising for the nonexistent February 31st.

    Overflow guard: returns :attr:`datetime.date.max` when the result
    would exceed year 9999 (Python's maximum representable year) instead
    of raising, so a long projection horizon degrades gracefully to a
    sentinel far-future date.

    Args:
        start: The starting date.
        months: Number of months to add (non-negative).

    Returns:
        A new :class:`datetime.date` ``months`` months after ``start``,
        or :attr:`datetime.date.max` on year-9999 overflow.
    """
    total_months = start.month - 1 + months
    year = start.year + total_months // 12
    month = total_months % 12 + 1

    if year > 9999:
        return date.max

    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def months_between(start: date, end: date) -> int:
    """Return whole calendar months from ``start`` to ``end`` (day ignored).

    Computes ``(end.year - start.year) * 12 + (end.month - start.month)``:
    the number of month boundaries between the two dates, with the
    day-of-month disregarded.  The delta from 2026-01-15 to 2027-01-01 is
    12, and from 2026-01-31 to 2026-02-01 is 1.

    The result is signed and unclamped -- an ``end`` before ``start``
    yields a negative count.  Callers that need a floor (e.g. "months
    remaining cannot drop below zero") or an inclusive ``+ 1`` clamp or
    adjust at the call site, because the bound differs per caller.

    Args:
        start: The earlier reference date.
        end: The later reference date.

    Returns:
        The signed whole-month delta as an ``int``.
    """
    return (end.year - start.year) * 12 + (end.month - start.month)


#: English month names, indexed by ``month_number - 1``.
#:
#: The source of truth for the ``month_name`` Jinja filter and for
#: :func:`app.services.recurrence.describe`.  They lived in
#: :mod:`app.jinja_filters` until plan step R7a, which needed the same names in
#: a SERVICE -- the recurrence describer builds a cadence phrase like
#: ``"Quarterly (Apr 21)"`` -- and a service must not import the template
#: filter module, which reaches back into ``app.services``.  Copying the table
#: instead would put two spellings of one list in the codebase, so it moved
#: down here and both readers take it from one place.
#:
#: **It is not yet the only month-name producer in the application**, and
#: saying so would be a claim nobody had measured: ``routes/analytics.py``,
#: ``routes/analytics_view.py`` and
#: ``services/ledger_report_service/_income_statement.py`` still name months
#: through :mod:`calendar` and ``strftime``.  Converting them is its own task
#: (recurrence plan finding F-15); this table does not pretend they are gone.
#:
#: Spelled out rather than read from :mod:`calendar`, deliberately: that
#: module's names follow the process LOCALE, so a container started under a
#: non-English locale would render a different month name than the one every
#: test asserts -- which is exactly what those three surfaces do today.
_MONTH_NAMES_FULL: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTH_NAMES_ABBR: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


#: English weekday names, indexed by :meth:`datetime.date.weekday` (0 = Monday).
#:
#: Spelled out for the same reason as the month names above, and the reason is
#: not hypothetical here: ``f"{a_date:%A}"`` delegates to the platform
#: ``strftime`` and follows ``LC_TIME``, which ``deploy/`` pins nowhere -- it
#: pins ``TZ`` only.  A container started with ``LANG=de_DE.UTF-8`` would render
#: a weekly recurrence as ``Weekly (Donnerstags)`` beside months still in
#: English, and the test asserting ``Weekly (Thursdays)`` would fail on the
#: environment rather than on the code.
_WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)


def weekday_name(value: date) -> str:
    """Return *value*'s English weekday name (``"Thursday"``).

    Locale-independent by construction: indexed off
    :meth:`datetime.date.weekday` rather than formatted with ``%A``, so the
    name a surface renders does not depend on the process ``LC_TIME``.

    Args:
        value: Any date.

    Returns:
        The English weekday name.
    """
    return _WEEKDAY_NAMES[value.weekday()]


def month_name(value: int | None, abbr: bool = False) -> str:
    """Map a 1-12 month number to its English name (``1`` -> ``"January"``).

    Single source for the month-name lookups that used to be duplicated as
    per-template ``month_names`` dicts (polyglot audit TPLB/TPL-07).  Pass
    ``abbr=True`` for the three-letter form (``"Jan"``) used by the list
    views; the default full name (``"January"``) matches the form selects.

    Registered as the ``month_name`` Jinja filter by
    :func:`app.jinja_filters.register_template_filters`, and called directly by
    :func:`app.services.recurrence.describe`, which builds a cadence phrase in
    a service rather than in a template.  Defined HERE so those two readers
    cannot come to name March two different things.

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
    if not 1 <= index <= len(_MONTH_NAMES_FULL):
        return ""
    names = _MONTH_NAMES_ABBR if abbr else _MONTH_NAMES_FULL
    return names[index - 1]


def pay_period_label(start_date: date, end_date: date) -> str:
    """Return a pay period's human label (``"02/21 - 03/06"``).

    **One rule, two accessors**, and that is why it is here rather than on
    either of them.  A pay period is answered by TWO types in this
    application: the ORM row (:class:`app.models.pay_period.PayPeriod`) and
    the derived value
    (:class:`app.services.pay_calendar.DerivedPeriod`), which plan step
    **C2-f** moved every "which paycheck" reader onto -- a period-move
    ``<select>`` renders one and the conflict chooser the other.  **What this
    buys is that the FORMAT is stated once; it does not yet make the two
    labels equal**, and saying so is the honest form: the row feeds it the
    STORED ``end_date`` and the derived value the DERIVED one, so on the last
    period under the P12 / P28 shape the two screens still render one paycheck
    two ways.  Plan step C4 closes that by deleting the stored column.
    Neither type can import the other's
    module (the model would close a cycle through
    ``pay_calendar._loader``, and the calendar package is deliberately
    model-free), so the shared rule lives in this one, which both already
    depend on.

    **Plan step C4 deletes the model accessor with the column it reads**
    (``budget.pay_periods.end_date``); this function and the derived value's
    property are what survive it, which is the other reason the rule is not
    written inside the model.

    The year is shown only when the period STRADDLES one, because that is
    the only time it disambiguates: ``"12/26/26 - 01/08/27"`` says which
    January, while ``"02/21/26 - 03/06/26"`` says nothing ``"02/21 -
    03/06"`` did not.

    Args:
        start_date: The payday that opens the period.
        end_date: The last day the period covers.

    Returns:
        The label, with a two-digit year on both halves when the period
        crosses a year boundary and no year at all when it does not.
    """
    if start_date.year != end_date.year:
        return (
            f"{start_date.strftime('%m/%d/%y')} - "
            f"{end_date.strftime('%m/%d/%y')}"
        )
    return f"{start_date.strftime('%m/%d')} - {end_date.strftime('%m/%d')}"


def pay_period_range_label(start_date: date, end_date: date) -> str:
    """Return a pay period's WIDE label (``"Feb 21 - Mar 06, 2026"``).

    The second register, for the surfaces with room for month names: the
    Income Statement's window selector and heading, and the Spending report's
    window heading.  :func:`pay_period_label` above is the narrow one
    (``"02/21 - 03/06"``), for a grid column head and a ``<select>`` that has
    to fit beside other controls.  **Two registers is the deliberate part;
    three COPIES of one register was not**, and collapsing that is what this
    function is (plan step C2-f3a, ledger row **P47**'s duplicate half).

    It was written three times -- character for character the same output from
    three separate expressions of it:
    ``ledger_report_service._income_statement._window_label``,
    ``spending_report_service._window._window_label``, and inline Jinja in
    ``analytics/_income_statement.html``.  Two of the three render this label
    beside each other on ONE screen -- the statement's heading and the
    ``<option>`` the reader picked it from -- so editing either alone would put
    one paycheck on one page under two spellings.

    **What is left is FOUR more spellings, and P47's census of six did not have
    two of them** (re-censused 2026-08-18 by C2-f3a's adversarial reviews, over
    every `` - `` / `` -- `` join of two ``strftime`` calls in ``app/``).  The
    row's own status says its count is a floor and this is the measurement that
    proves it: ``companion/index.html`` (``%b %-d`` -- ``%b %-d, %Y``),
    ``grid/_mobile_plan.html`` (``%b %-d`` -- ``%b %-d``),
    ``_recurrence_preview`` (``%b %d, %Y`` - ``%b %d, %Y``), and a pair the
    census never named -- ``accounts/_reconcile_panel.html`` and
    ``dashboard/_pulse.html``, which render the IDENTICAL ``%b %-d`` -
    ``%b %-d`` and are therefore a second duplicate, not a second register.
    Untouched here: which registers a screen should speak is P47's open half
    and a display decision rather than a calendar one.

    **It takes the two dates rather than a period**, for
    :func:`pay_period_label`'s reason one function up: a pay period is answered
    by two types (the ORM row and
    :class:`~app.services.pay_calendar.DerivedPeriod`), neither of their
    modules may import the other, and both already depend on this one.

    **The year is carried on BOTH halves when the period straddles one**, and
    on the END alone when it does not -- ledger row **P67**, developer ruling
    2026-08-25.  It printed ``"Dec 26 - Jan 08, 2027"`` until then, which reads
    as a December in 2027 and is a December in 2026; the three copies C2-f3a
    collapsed all did that, and the collapse reproduced it byte for byte on
    purpose so a rendering change was not smuggled in behind a DRY fix.  This
    is that change, made on its own.

    **The rule is :func:`pay_period_label`'s, one function up**, so the two
    registers differ in WIDTH and not in when a year disambiguates: the narrow
    one answers ``"12/26/26 - 01/08/27"`` for this period and ``"02/21 -
    03/06"`` for one inside a single year.  Two registers was always the
    deliberate part; two RULES would be the drift.

    The month name comes from ``strftime`` and is therefore LC_TIME-dependent,
    which is finding **F-15**'s subject rather than this function's.

    Args:
        start_date: The payday that opens the period.
        end_date: The last day the period covers.

    Returns:
        The label -- abbreviated month name and zero-padded day on both halves,
        with a four-digit year on each half when the period crosses a year
        boundary and on the end alone when it does not.
    """
    if start_date.year != end_date.year:
        return (
            f"{start_date.strftime('%b %d')}, {start_date.year} - "
            f"{end_date.strftime('%b %d')}, {end_date.year}"
        )
    return (
        f"{start_date.strftime('%b %d')} - "
        f"{end_date.strftime('%b %d')}, {end_date.year}"
    )


def attribution_date(
    preferred: date | None, period_start: date, period_end: date,
) -> date:
    """Return the calendar day a pay-period item is attributed to, clamped.

    The single BUDGET-attribution rule, shared by the calendar's day-cell
    grouping (``calendar_service._get_display_day``) and the balance-at seam's
    PLANNED tier (``balance_at._cash_fold._cash_plan``), so the two cannot come
    to disagree about which day a planned item is BUDGETED to.

    **Both halves of this paragraph were false and ledger row N-97 is the
    correction.**  The seam's caller was named as
    ``balance_resolver.daily_cash_balance_series`` -- a producer plan step
    X-c2b3 had DELETED a month earlier, so the citation resolved to nothing.
    And the guarantee stated here was that a flow's cell and the balance line's
    step for it land on the SAME day: that stopped holding at plan step X-c2b2,
    when the balance line became the cash fold, which steps a SETTLED row on the
    day its money moved and a projected one on ``max(attribution, as_of + 1)``
    (rulings R-DH (b) and R-G).  Neither is this date, so a chip and its own
    step can sit days apart -- median 2, p75 6, max 25 on the real Checking
    account.  That divergence is finding **N-58**, it is an open fork rather
    than a settled rule, and ``calendar_service._get_display_day`` states it at
    the site.  What this function still guarantees is the budget attribution
    itself, which is what both readers ask it for.  An item lands on
    ``preferred`` -- its ``due_date`` -- falling back to the pay period's
    ``start_date`` when it has none; the result is then clamped into the
    item's own pay period ``[period_start, period_end]`` span.

    Clamping is load-bearing for the daily balance: every one of a period's
    contributing items must fall on or before the period ``end_date`` so the
    running balance summed through that day equals the period-end balance
    the grid shows (the calendar/grid reconciliation invariant).  A
    ``due_date`` outside the item's own period is possible -- the recurrence
    engine can date an item just outside its period's range, which is why
    the calendar query carries a due-date-in-range OR no-due-date path -- so
    such a stray date is pulled to the nearest period boundary rather than
    escaping onto a neighboring period's day and breaking that period's sum.

    Args:
        preferred: The item's preferred landing date (its ``due_date``), or
            ``None`` to fall back to ``period_start``.
        period_start: The item's pay period inclusive start date (both the
            None fallback and the lower clamp bound).
        period_end: The item's pay period inclusive end date (the upper
            clamp bound).  Assumed ``>= period_start`` (a model CHECK
            constraint enforces ``start_date < end_date``).

    Returns:
        The attributed calendar date, guaranteed within
        ``[period_start, period_end]``.
    """
    landing = preferred if preferred is not None else period_start
    if landing < period_start:
        return period_start
    if landing > period_end:
        return period_end
    return landing
