"""
Shekel Budget App -- Calendar Service

Groups transactions by calendar month and day, computes per-month
income/expense/net totals, detects 3rd-paycheck months, flags
large/infrequent transactions, and projects month-end balances.

Pure-function service -- no Flask imports, no database writes.
"""

import calendar
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import balance_at, cash_ledger
from app.services.account_resolver import resolve_analytics_account
from app.services.calendar_infrequency import (
    badge_cadence,
    is_infrequent,
)
from app.services.pay_calendar import PayCadence
from app.services.pay_period_service import get_overlapping_periods
from app.services.balance_at import BalanceContext
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_balance_contributing,
)
from app.utils.dates import attribution_date

logger = logging.getLogger(__name__)

# Day cells show at most this many named flow lines; any beyond collapse to
# a single "+N more" line whose residual net is computed in the service
# (templates never do money math).  The locked calendar anatomy fixes this
# at three (income first, then expenses by descending magnitude).
MAX_VISIBLE_DAY_FLOWS = 3


class CalendarAccountNotResolvableError(LookupError):
    """Raised when the calendar cannot resolve a backing ACCOUNT.

    After Commits 3-8 of the main remediation locked the E-19 / CRIT-01
    invariant ("anchor is never NULL; ``resolve_anchor`` raises or
    returns a valid ``AnchorPoint``"), an ``account is None`` outcome from
    :func:`~app.services.account_resolver.resolve_analytics_account`
    indicates an *upstream* defect (a deleted analytics account), not a normal
    "empty calendar" state.  Pre-F-2 the service silently substituted a zeroed
    :class:`MonthSummary` / :class:`YearOverview`, which masked the upstream bug
    behind a ``$0.00`` calendar shown to the user with no error.  Raising
    instead lets the route layer answer with the project-standard 404
    ("404 for both 'not found' and 'not yours'", see
    :mod:`app.utils.auth_helpers`).

    **It no longer covers a missing baseline SCENARIO** (plan step X-v2, ruling
    R-BW).  Both entries used to raise this for that state too, and the route
    turned it into a 404 -- a seventh answer to a question fifteen other
    surfaces were each answering differently, and the least useful of them: a
    404 tells a user with a repairable setup problem that their calendar does
    not exist.  The seam now raises
    :class:`~app.exceptions.BaselineMissingError` and one application-level
    handler renders the repair.  Two conditions that shared one exception are
    two exceptions again, and this one means exactly what its name says.
    """


@dataclass(frozen=True)
class DayEntry:  # pylint: disable=too-many-instance-attributes
    """A single transaction's representation on a calendar day.

    Pylint: ``too-many-instance-attributes`` (10/7) -- suppressed
    because this is a cohesive value record -- one transaction's row on a
    calendar day -- consumed verbatim by the calendar surface: the CSV
    month export reads the display fields as adjacent columns (folding the
    booleans into single Income/Expense, Status, Large, and Infrequent
    columns), the month-detail table renders name/category/amount and the
    income/paid flags as individual cells, and the route reads
    amount/is_income for day totals.  The two category fields are read as
    independent columns, never as a unit.  Every field is an irreducible
    column of the row; splitting it would fragment one domain concept and
    break every consumer for no design gain.
    """

    transaction_id: int
    name: str
    amount: Decimal
    is_income: bool
    is_paid: bool
    is_large: bool
    is_infrequent: bool
    category_group: str | None
    category_item: str | None
    due_date: date | None


@dataclass(frozen=True)
class DayOverflow:
    """The collapsed "+N more" residual for a day with more flows than fit.

    A day cell renders at most :data:`MAX_VISIBLE_DAY_FLOWS` named flow lines;
    the remainder collapse to one "+N more" line.  This carries that line's
    two service-computed values so the template does no money math: the
    ``count`` of hidden flows and their signed ``net`` (income positive,
    expense negative).  Only days whose flow count exceeds the cap have an
    entry (see :func:`_assign_transactions_to_days`).
    """

    count: int
    net: Decimal


@dataclass(frozen=True)
class DailyView:  # pylint: disable=too-many-instance-attributes
    """The month's daily running-balance projection for the calendar surface.

    Pylint: ``too-many-instance-attributes`` (8/7) -- suppressed because this
    is one cohesive value record: the month view's daily projection, read as
    a unit by the calendar month template and its flow strip.  Splitting it
    would fragment a single concept (the running balance and the summary-strip
    figures derived from it) for no design gain.

    The balances are projected-only and entry-aware, sourced from the balance
    seam's :func:`~app.services.balance_at.cash_daily_balance_series`, so the
    day-cell end-of-day hero and the flow strip line share one basis and
    reconcile with the grid.  The ``elapsed_*`` / ``remaining_*`` figures are
    the MEASURED / PROJECTED nominal in-out folds (from ``day_totals``, the
    same contribution basis the day cells show), so they tie to the
    day cells beside them; they COINCIDE with the projected balances in
    ordinary data but deliberately diverge where the projection excludes a
    settled row (already in the anchor), applies an envelope's entry-aware
    reservation, or takes a live override -- the measured-vs-modeled
    distinction the presentation labels, not a defect.  ``None`` on
    :class:`MonthSummary` for the year overview (which does not render daily
    balances; slice-1 scope).

    Attributes:
        daily_balances: ``{day_of_month: Decimal}`` projected end-of-day
            running balance, one entry per calendar day in the month.
        trough_day: The day of the month's minimum end-of-day balance (the
            earliest such day on a tie), or ``None`` for an empty month.
        trough_balance: That minimum end-of-day balance, or ``None``.
        balance_today: The end-of-day balance on the current day when it
            falls in this month (display timezone), else ``None``.
        elapsed_income / elapsed_expense: Income / expense that has landed on
            or before today (measured-so-far); the whole month when the month
            is entirely past.
        remaining_income / remaining_expense: Income / expense still projected
            after today; the whole month when the month is entirely future.
    """

    daily_balances: dict[int, Decimal]
    trough_day: int | None
    trough_balance: Decimal | None
    balance_today: Decimal | None
    elapsed_income: Decimal
    elapsed_expense: Decimal
    remaining_income: Decimal
    remaining_expense: Decimal


@dataclass(frozen=True)
class MonthSummary:  # pylint: disable=too-many-instance-attributes
    """Aggregated data for one calendar month.

    Pylint: ``too-many-instance-attributes`` (13/7) -- suppressed
    because this is a cohesive single-return aggregate -- one calendar
    month's summary -- whose fields are flat columns read together by the
    calendar surface: the month and year templates render the money fields
    and is_third_paycheck_month, and the CSV year export emits them as one
    row per month.  The three money fields are the month's headline
    numbers read individually, not a sub-object read as a unit, so there is
    no section to extract; nesting would fragment one contract across the
    templates and the exporter for no design gain.  ``day_totals`` is the
    per-day income/expense map (parallel to ``day_entries``) the analytics
    calendar route renders directly, so the route does no money math of
    its own.  ``day_overflow`` is the parallel per-day "+N more" residual;
    ``daily`` bundles the whole daily running-balance projection as one
    cohesive sub-object (:class:`DailyView`), ``None`` for the year overview.
    ``account_name`` is the resolved analytics account's display name (the
    on-screen scope label the month template renders so the checking-only
    scope is stated, not silent -- the analytics-audit cross-cutting fix).

    ``projected_end_balance`` is the period-flat seam scalar at the last
    calendar day (the containing period's end); the month view's honest
    "balance on the last day of the month" is ``daily.daily_balances[last]``,
    which the flow strip ends on -- the two differ when the month ends
    mid-period, and the month template uses the running value so the strip
    and the month-end tile agree.
    """

    year: int
    month: int
    total_income: Decimal
    total_expenses: Decimal
    net: Decimal
    projected_end_balance: Decimal
    is_third_paycheck_month: bool
    day_entries: dict[int, list[DayEntry]]
    day_totals: dict[int, tuple[Decimal, Decimal]]
    day_overflow: dict[int, DayOverflow]
    paycheck_days: list[int]
    daily: DailyView | None
    account_name: str


@dataclass(frozen=True)
class YearOverview:
    """12-month year overview data."""

    year: int
    months: list[MonthSummary]
    annual_income: Decimal
    annual_expenses: Decimal
    annual_net: Decimal


def get_month_detail(  # pylint: disable=too-many-arguments
    user_id: int,
    year: int,
    month: int,
    account_id: int | None = None,
    large_threshold: int = 500,
    *,
    today: date | None = None,
) -> MonthSummary:
    """Compute calendar data for a single month.

    Queries transactions for pay periods that overlap the given month,
    assigns each transaction to a calendar day via due_date (clamped into
    its pay period), and computes income/expense totals, projected
    month-end balance, large/infrequent flags, and -- when ``today`` is
    supplied -- the daily running-balance projection (:class:`DailyView`).

    Pylint: ``too-many-arguments`` (6/5) -- these six are independent
    calendar-render inputs, not a cohesive entity: the owner id, the target
    year and month, the optional account scope, the large-flag threshold, and
    the display-tz ``today`` that gates the daily view.  They are passed
    straight through from the route's own request args, so a param object
    would be stamp coupling; ``get_year_overview`` shares the same
    non-cohesive shape minus ``today``.

    Args:
        user_id: The user's ID.
        year: Calendar year.
        month: Calendar month (1-12).
        account_id: Account to scope transactions to.  Defaults to
            the user's first active checking account.
        large_threshold: Amount at or above which a transaction is
            flagged as large.
        today: The current date in the display timezone (the route resolves
            it).  When supplied, the month's daily running-balance view is
            computed and attached as :attr:`MonthSummary.daily` (the flow
            strip, day-cell balances, and elapsed/remaining split need it);
            when ``None`` (aggregate callers that do not render daily
            balances) ``daily`` is ``None`` and no balance-series read runs.

    Returns:
        A MonthSummary with day-level and aggregate data.

    Raises:
        CalendarAccountNotResolvableError: The analytics account cannot be
            resolved -- an upstream defect the route turns into a 404.
        PayCalendarError: See
            :func:`~app.services.calendar_infrequency.badge_cadence`.
        RecurrenceResolutionError: See
            :func:`~app.services.calendar_infrequency.is_infrequent`.  NEW at
            plan step R7a-2b together with the one above, and the unhandled
            500 is the intended answer -- ``routes/analytics.py`` catches only
            the first of these three.
    """
    account = resolve_analytics_account(user_id, account_id)
    if account is None:
        raise CalendarAccountNotResolvableError(
            f"Analytics account not resolvable for user_id={user_id} "
            f"account_id={account_id} year={year} month={month}",
        )

    balance_ctx = BalanceContext.build(user_id)
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    periods = get_overlapping_periods(user_id, first_day, last_day)
    transactions = _query_transactions_for_range(
        account.id, balance_ctx.scenario_id, user_id, first_day, last_day,
    )

    ctx = _MonthBuildContext(
        year=year, account=account, periods=periods,
        transactions=transactions,
        contributions=cash_ledger.contributions_by_id(
            user_id, balance_ctx.scenario_id, transactions,
        ),
        large_threshold=large_threshold,
        balance_ctx=balance_ctx, today=today,
        pay_cadence=badge_cadence(user_id, transactions),
    )
    return _build_month_summary(month, ctx)


def get_year_overview(
    user_id: int,
    year: int,
    account_id: int | None = None,
    large_threshold: int = 500,
) -> YearOverview:
    """Compute 12-month overview for a calendar year.

    Fetches all transactions for the year in a single query, then
    partitions by month in Python to avoid 12 database round trips.

    Args:
        user_id: The user's ID.
        year: Calendar year.
        account_id: Account to scope to.  Defaults to checking.
        large_threshold: Large transaction threshold.

    Returns:
        A YearOverview with 12 MonthSummary entries (Jan-Dec).

    Raises:
        The three :func:`get_month_detail` raises, for the same reasons.
    """
    account = resolve_analytics_account(user_id, account_id)
    if account is None:
        raise CalendarAccountNotResolvableError(
            f"Analytics account not resolvable for user_id={user_id} "
            f"account_id={account_id} year={year}",
        )

    balance_ctx = BalanceContext.build(user_id)
    first_day = date(year, 1, 1)
    last_day = date(year, 12, 31)
    periods = get_overlapping_periods(user_id, first_day, last_day)
    all_txns = _query_transactions_for_range(
        account.id, balance_ctx.scenario_id, user_id, first_day, last_day,
    )

    ctx = _MonthBuildContext(
        year=year, account=account, periods=periods,
        transactions=all_txns,
        contributions=cash_ledger.contributions_by_id(
            user_id, balance_ctx.scenario_id, all_txns,
        ),
        large_threshold=large_threshold,
        balance_ctx=balance_ctx, today=None,
        pay_cadence=badge_cadence(user_id, all_txns),
    )
    months = [_build_month_summary(m, ctx) for m in range(1, 13)]

    annual_income = sum(ms.total_income for ms in months)
    annual_expenses = sum(ms.total_expenses for ms in months)

    return YearOverview(
        year=year,
        months=months,
        annual_income=annual_income,
        annual_expenses=annual_expenses,
        annual_net=annual_income - annual_expenses,
    )


# ── Internal helpers ────────────────────────────────────────────────


def _query_transactions_for_range(
    account_id: int,
    scenario_id: int,
    user_id: int,
    first_day: date,
    last_day: date,
) -> list[Transaction]:
    """Load the transactions of every pay period overlapping the range.

    Fetches by PERIOD MEMBERSHIP -- all balance-contributing rows whose
    ``pay_period_id`` is a period overlapping ``[first_day, last_day]`` --
    NOT by raw ``due_date``.  This is the basis the clamped
    :func:`~app.utils.dates.attribution_date` display rule and the daily
    balance producer both use: a transaction is attributed to a day inside
    its own pay period, so the day cell that renders it and the balance line
    that steps for it share one period-anchored day.  A ``due_date`` that
    strays outside its period (the reason the prior query needed a second
    due-date-in-range path) is pulled back to the period boundary by the
    clamp; fetching by membership means such a row is still loaded for the
    month its period lands in and is never dropped.  ``_get_display_day``
    then filters each loaded row to the single month its clamped attribution
    date falls in, so a period straddling two months splits its rows across
    them without double-counting.

    Eager-loads category, status, template -> recurrence_rule, and
    pay_period to prevent N+1 queries downstream.

    Per F-3 / HIGH-02 / W-065, the row-set is constrained by
    :func:`~app.utils.balance_predicates.balance_contributing_clause`
    (``is_deleted=False AND status_id NOT IN (Credit, Cancelled)``)
    rather than the prior inline ``is_deleted=False``-only gate.  This
    is the locked Choice-2 semantic from
    ``remediation_follow_up_plan.md`` Section 2: calendar day cells
    display realized payments at their settled date, so the predicate
    is "balance-contributing" (Projected + Settled, excludes Credit and
    Cancelled) -- intentionally wider than the grid period subtotal's
    Projected-only predicate.  The two surfaces diverge by design.
    """
    overlapping = get_overlapping_periods(user_id, first_day, last_day)
    period_ids = [p.id for p in overlapping]

    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.status),
            joinedload(Transaction.template).joinedload(
                TransactionTemplate.recurrence_rule,
            ),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(period_ids),
        )
        .all()
    )


def _build_day_entry(
    txn: Transaction,
    amount: Decimal,
    income_type_id: int,
    threshold: Decimal,
    pay_cadence: PayCadence | None,
) -> DayEntry:
    """Create a DayEntry from a transaction.

    Args:
        txn: The transaction to convert.
        amount: What the row is WORTH, from the build's one
            :func:`~app.services.cash_ledger.contributions_by_id` call
            (:attr:`_MonthBuildContext.contributions`).  It replaced
            ``txn.effective_amount`` at plan step X-au-c2: that model property
            could not answer for a row whose amount is DERIVED, because such a
            row stores no figure and resolving one needs a database -- and, for
            a paycheck, the owner's whole pay-period set.
        income_type_id: Ref ID for the Income transaction type.
        threshold: Amount at or above which a transaction is large.
        pay_cadence: The owner's pay cadence for the infrequent badge, or
            ``None`` when no row in this build repeats
            (:func:`~app.services.calendar_infrequency.badge_cadence`).

    Returns:
        A frozen DayEntry dataclass.
    """
    return DayEntry(
        transaction_id=txn.id,
        name=txn.name,
        amount=amount,
        is_income=txn.transaction_type_id == income_type_id,
        is_paid=bool(txn.status and txn.status.is_settled),
        is_large=abs(amount) >= threshold,
        is_infrequent=is_infrequent(txn, pay_cadence),
        category_group=txn.category.group_name if txn.category else None,
        category_item=txn.category.item_name if txn.category else None,
        due_date=txn.due_date,
    )


def _fold_income_expense(
    entries: list[DayEntry],
) -> tuple[Decimal, Decimal]:
    """Fold a collection of day entries into an (income, expense) pair.

    The single income/expense sign-fold for the calendar surface: income
    is the signed sum of the ``is_income`` entries; expense is the sum of
    ``abs(amount)`` over the non-income entries.  Both legs seed at
    ``Decimal("0")`` so an empty or all-one-sign collection yields a
    ``Decimal``, never an int ``0`` -- money is always Decimal.  Applied
    per day to build :attr:`MonthSummary.day_totals`, from which the
    month headline totals are then summed, so the per-day cells the
    analytics calendar renders and the month total derive from one rule
    and cannot drift.

    Args:
        entries: The :class:`DayEntry` records for one day (or any
            collection to fold); each carries ``amount`` and ``is_income``.

    Returns:
        ``(income, expense)`` as a pair of ``Decimal`` values.
    """
    income = sum((e.amount for e in entries if e.is_income), Decimal("0"))
    expense = sum(
        (abs(e.amount) for e in entries if not e.is_income), Decimal("0"),
    )
    return income, expense


def _assign_transactions_to_days(
    ctx: "_MonthBuildContext",
    month: int,
) -> tuple[
    dict[int, list[DayEntry]],
    dict[int, tuple[Decimal, Decimal]],
    dict[int, DayOverflow],
]:
    """Assign transactions to calendar days and fold per-day totals.

    Returns the day_map, the per-day ``{day: (income, expense)}`` totals
    map, and the per-day ``{day: DayOverflow}`` collapse map for the target
    month.  Deduplicates by transaction ID to prevent double-counting when
    periods overlap month boundaries.

    Each day's entries are ordered income first, then expenses by descending
    magnitude (the locked calendar anatomy); the first
    :data:`MAX_VISIBLE_DAY_FLOWS` are the visible named lines and any beyond
    are summarized in :class:`DayOverflow` (count plus signed residual net).

    Per F-3 / W-065, every transaction is re-checked against
    :func:`~app.utils.balance_predicates.is_balance_contributing`
    before being assigned to a day.  This is the belt-and-suspenders
    half of the locked Choice-2 predicate: the SQL filter in
    :func:`_query_transactions_for_range` already constrains the row
    set, but reapplying the Python predicate here ensures a future
    regression that drops the SQL filter alone (or routes a different
    query into this helper) cannot leak Cancelled / Credit rows into
    the day-cell display.  ``is_balance_contributing`` is generated
    from the same ``ref_cache`` accessors as the SQL clause so the
    two predicates cannot disagree.

    **It takes the CONTEXT rather than the five settings it reads off it**
    (plan step X-au-c2).  Adding ``contributions`` -- what each row is WORTH,
    resolved once for the whole build -- took the plain-data signature to six
    arguments, and the project's rule for a PRIVATE helper is to decompose
    rather than to wrap arguments in an object (``docs/plans/lessons.md``).
    There is nothing to decompose here: the bundle already exists, this helper
    has exactly one caller, and every one of those settings is a field of the
    context that caller is holding.  Taking it directly is what stops the rows
    and their valuation from arriving as two arguments a caller could mismatch,
    which is the hazard :class:`~app.services.cash_ledger.ProjectedBasis`
    states one tier down.

    Args:
        ctx: The build's :class:`_MonthBuildContext` -- its ``transactions``,
            their ``contributions``, the ``year``, the ``large_threshold`` and
            the ``pay_cadence``.  ``contributions`` is indexed with ``[]``: a
            row missing from it is a build that priced a different set, and a
            default would be a fabricated figure in a day cell.
        month: Target calendar month (1-12).
    """
    threshold = Decimal(str(ctx.large_threshold))
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    seen_ids: set[int] = set()
    day_map: dict[int, list[DayEntry]] = defaultdict(list)

    for txn in ctx.transactions:
        if txn.id in seen_ids:
            continue
        if not is_balance_contributing(txn):
            continue
        display_day = _get_display_day(txn, month, ctx.year)
        if display_day is None:
            continue

        seen_ids.add(txn.id)
        entry = _build_day_entry(
            txn, ctx.contributions[txn.id], income_type_id, threshold,
            ctx.pay_cadence,
        )
        day_map[display_day].append(entry)

    # Order each day income first, then expenses by descending magnitude.
    for day in day_map:
        day_map[day].sort(key=lambda e: (not e.is_income, -abs(e.amount)))

    day_totals = {
        day: _fold_income_expense(entries)
        for day, entries in day_map.items()
    }
    day_overflow = {
        day: _day_overflow(entries)
        for day, entries in day_map.items()
        if len(entries) > MAX_VISIBLE_DAY_FLOWS
    }

    return dict(day_map), day_totals, day_overflow


def _day_overflow(entries: list[DayEntry]) -> DayOverflow:
    """Summarize the flows past the visible cap into a "+N more" residual.

    The entries are pre-sorted (income first, then expenses by descending
    magnitude), so the hidden tail is everything after the first
    :data:`MAX_VISIBLE_DAY_FLOWS`.  The residual ``net`` is signed (income
    positive, expense negative) and seeded at ``Decimal("0")`` so it stays a
    ``Decimal``.  Called only for days whose flow count exceeds the cap.

    Args:
        entries: One day's ordered :class:`DayEntry` list (length greater
            than :data:`MAX_VISIBLE_DAY_FLOWS`).

    Returns:
        The :class:`DayOverflow` for the hidden tail.
    """
    hidden = entries[MAX_VISIBLE_DAY_FLOWS:]
    net = sum(
        (e.amount if e.is_income else -abs(e.amount) for e in hidden),
        Decimal("0"),
    )
    return DayOverflow(count=len(hidden), net=net)


@dataclass(frozen=True)
class _MonthBuildContext:  # pylint: disable=too-many-instance-attributes
    """The pre-queried data and config shared across a year's month summaries.

    ``get_year_overview`` resolves the account, scenario, overlapping
    periods, and transaction set once and builds twelve summaries from
    them, varying only the month (``get_month_detail`` builds one).
    Bundling these into the context the build shares keeps
    :func:`_build_month_summary` a two-argument call and makes that
    resolved-once-reused relationship explicit.

    ``today`` is the display-timezone current date supplied by the month
    view (``None`` for the year overview): when set, the build computes the
    month's daily running-balance :class:`DailyView`; when ``None`` no
    balance-series read runs and ``MonthSummary.daily`` stays ``None``.

    ``pay_cadence`` is the owner's, for the infrequent badge, and is ``None``
    exactly when no row in this build repeats -- see
    :func:`~app.services.calendar_infrequency.badge_cadence`.

    ``contributions`` is what each of those ``transactions`` is WORTH, resolved
    ONCE for the whole build (plan step X-au-c2).  It travels beside the rows
    rather than being recomputed per month because the year overview builds
    twelve summaries from one row set, and the salary producer behind it runs
    the paycheck engine over the owner's entire pay-period set (finding
    **N-228**) -- so a per-month valuation would run it twelve times for one
    answer.  It is a field of this context rather than a second argument for
    the reason :class:`~app.services.cash_ledger.ProjectedBasis` gives one tier
    down: the rows and their prices are one fact, and two arguments are two
    things a caller can mismatch.

    Pylint: ``too-many-instance-attributes`` (9/7) -- these nine ARE one
    calendar build's inputs, read as a flat unit by
    :func:`_build_month_summary` and its helpers, with no cohesive sub-group to
    nest: the year and account scope the query, the periods, transactions and
    their contributions are its result, and the threshold, context, clock and
    cadence are four independent per-build settings.  Mirrors
    :class:`DayEntry`'s 10/7 here.
    """

    year: int
    account: Account
    periods: list[PayPeriod]
    transactions: list[Transaction]
    contributions: dict[int, Decimal]
    large_threshold: int
    balance_ctx: BalanceContext
    today: date | None
    pay_cadence: PayCadence | None


def _build_month_summary(month: int, ctx: _MonthBuildContext) -> MonthSummary:
    """Assemble a MonthSummary for one month from pre-queried context.

    Assigns each transaction to a calendar day via _get_display_day,
    deduplicating by transaction ID to prevent double-counting when
    periods overlap month boundaries.

    Args:
        month: Target calendar month (1-12).
        ctx: The year/account/periods/transactions/threshold/scenario
            shared across the build (see :class:`_MonthBuildContext`).

    Returns:
        A MonthSummary for the target month.
    """
    day_entries, day_totals, day_overflow = _assign_transactions_to_days(
        ctx, month,
    )
    # Month headline totals are the sum of the per-day folds, so the
    # month and per-day numbers derive from the one _fold_income_expense
    # rule and cannot disagree.
    total_income = sum(
        (income for income, _expense in day_totals.values()), Decimal("0"),
    )
    total_expenses = sum(
        (expense for _income, expense in day_totals.values()), Decimal("0"),
    )

    end_balance = _compute_month_end_balance(
        ctx.account, ctx.year, month, ctx.balance_ctx,
    )
    third_paycheck_months = _detect_third_paycheck_months(ctx.periods, ctx.year)

    paycheck_days = sorted({
        p.start_date.day
        for p in ctx.periods
        if p.start_date.year == ctx.year and p.start_date.month == month
    })

    daily = (
        _compute_daily_view(ctx, month, day_totals)
        if ctx.today is not None else None
    )

    return MonthSummary(
        year=ctx.year,
        month=month,
        total_income=total_income,
        total_expenses=total_expenses,
        net=total_income - total_expenses,
        projected_end_balance=end_balance,
        is_third_paycheck_month=month in third_paycheck_months,
        day_entries=day_entries,
        day_totals=day_totals,
        day_overflow=day_overflow,
        paycheck_days=paycheck_days,
        daily=daily,
        account_name=ctx.account.name,
    )


def _compute_daily_view(
    ctx: _MonthBuildContext,
    month: int,
    day_totals: dict[int, tuple[Decimal, Decimal]],
) -> DailyView:
    """Build the month's daily running-balance projection.

    Reads the day-by-day end-of-day balance from the balance seam
    (:func:`~app.services.balance_at.cash_daily_balance_series`), derives the
    month trough (earliest day of the minimum balance) and today's balance,
    and splits the per-day income/expense folds into elapsed (on or before
    today) and remaining (after today).  ``ctx.today`` is the display-timezone
    current date; a month entirely in the past is all elapsed, one entirely
    in the future all remaining.

    Args:
        ctx: The build context (account, scenario, and display-tz ``today``,
            which must not be ``None`` here -- the caller gates on it).
        month: Target calendar month (1-12).
        day_totals: The per-day ``{day: (income, expense)}`` folds, split at
            ``today`` into the elapsed / remaining summary-strip figures.

    Returns:
        The assembled :class:`DailyView`.
    """
    first_day = date(ctx.year, month, 1)
    last_day = date(ctx.year, month, calendar.monthrange(ctx.year, month)[1])
    series = balance_at.cash_daily_balance_series(
        ctx.account, ctx.balance_ctx, first_day, last_day,
    )
    daily_balances = {day.day: balance for day, balance in series.items()}

    trough_day, trough_balance = _find_trough(daily_balances)

    today = ctx.today
    if today > last_day:
        split_day, balance_today = last_day.day, None
    elif today < first_day:
        split_day, balance_today = 0, None
    else:
        split_day, balance_today = today.day, daily_balances.get(today.day)

    elapsed = _fold_split(day_totals, split_day, True)
    remaining = _fold_split(day_totals, split_day, False)

    return DailyView(
        daily_balances=daily_balances,
        trough_day=trough_day,
        trough_balance=trough_balance,
        balance_today=balance_today,
        elapsed_income=elapsed[0],
        elapsed_expense=elapsed[1],
        remaining_income=remaining[0],
        remaining_expense=remaining[1],
    )


def _find_trough(
    daily_balances: dict[int, Decimal],
) -> tuple[int | None, Decimal | None]:
    """Return the (day, balance) of the month's minimum end-of-day balance.

    The series is day-ascending, so a strict ``<`` comparison keeps the
    EARLIEST day on a tie.  An empty month (no days) returns ``(None, None)``.

    Args:
        daily_balances: The ``{day: Decimal}`` end-of-day balances.

    Returns:
        ``(trough_day, trough_balance)``, or ``(None, None)`` when empty.
    """
    trough_day: int | None = None
    trough_balance: Decimal | None = None
    for day, balance in daily_balances.items():
        if trough_balance is None or balance < trough_balance:
            trough_day, trough_balance = day, balance
    return trough_day, trough_balance


def _fold_split(
    day_totals: dict[int, tuple[Decimal, Decimal]],
    split_day: int,
    elapsed: bool,
) -> tuple[Decimal, Decimal]:
    """Sum the per-day income / expense folds on one side of ``split_day``.

    ``elapsed`` selects days on or before ``split_day`` (measured so far);
    otherwise days strictly after it (still projected).  Both legs seed at
    ``Decimal("0")`` so money stays ``Decimal``.

    Args:
        day_totals: The per-day ``{day: (income, expense)}`` folds.
        split_day: The day the month splits at (today's day, or a whole-month
            boundary for a fully past / future month).
        elapsed: ``True`` for the elapsed side (day <= split_day), ``False``
            for the remaining side (day > split_day).

    Returns:
        ``(income, expense)`` summed over the selected side.
    """
    income = sum(
        (inc for day, (inc, _exp) in day_totals.items()
         if (day <= split_day) == elapsed),
        Decimal("0"),
    )
    expense = sum(
        (exp for day, (_inc, exp) in day_totals.items()
         if (day <= split_day) == elapsed),
        Decimal("0"),
    )
    return income, expense


def _get_display_day(
    txn: Transaction,
    target_month: int,
    target_year: int,
) -> int | None:
    """Determine the calendar day to display a transaction on.

    Returns the day-of-month when the transaction's attribution date falls in
    the target month, or None otherwise (preventing double-counting across
    month boundaries).

    The attribution date is the shared
    :func:`~app.utils.dates.attribution_date` rule -- ``due_date`` (fallback:
    the pay period ``start_date``) clamped into the transaction's own pay
    period span.  The clamp prevents a due_date that strays just outside its
    period from leaking a flow onto a neighboring period's day.

    **It no longer places a flow on the same day as the balance step for it,
    and that is an open fork rather than a settled rule** (plan step X-c2b2,
    finding N-58).  The balance line under these cells is the cash fold now:
    a SETTLED row steps it on the day its money moved (``settled_on``, the
    display-timezone civil day since ruling R-DH (b)) and a still-projected one
    on ``max(attribution, as_of + 1)`` (ruling R-G).  Neither is the budget
    attribution date this function returns, so a chip and its own balance step
    can sit days apart -- median 2, p75 6, max 25
    on the real Checking account (finding N-42).  The two agreed by
    construction before the cutover because the retired ramp distributed the
    same still-projected rows over these same attribution days.  The grid met
    the identical split and answered it with ruling R-K's remainder rows
    row; the calendar has no such row yet, and which way it should go -- move
    the chip to the cash clock, add a reconciling figure, or label the
    divergence -- is the developer's to rule.
    """
    period = txn.pay_period
    landing = attribution_date(
        txn.due_date, period.start_date, period.end_date,
    )
    if landing.month == target_month and landing.year == target_year:
        return landing.day
    return None


def _detect_third_paycheck_months(
    periods: list[PayPeriod],
    year: int,
) -> set[int]:
    """Identify months with 3+ pay period start_dates in the given year.

    Standard biweekly pay produces exactly 2 such months per year.
    """
    month_counts: dict[int, int] = defaultdict(int)
    for p in periods:
        if p.start_date.year == year:
            month_counts[p.start_date.month] += 1

    return {m for m, count in month_counts.items() if count >= 3}


def _compute_month_end_balance(
    account: Account,
    year: int,
    month: int,
    balance_ctx: BalanceContext,
) -> Decimal:
    """Project the account's cash-flow balance at the calendar month-end (E-27).

    Routes through the balance-at seam's cash-flow scalar
    :func:`~app.services.balance_at.cash_balance_at` (Level-1 Commit 8),
    which folds the account's cash events (plan step X-c2b2), at the
    actual last day of the month.  The cash-flow entry (not the
    kind-correct :func:`~app.services.balance_at.balance_at`) keeps this a
    pure transaction running-balance that reconciles with the day cells the
    calendar renders for the same month; the analytics account can be any
    kind via an explicit ``account_id``.  This is the HIGH-02 / W-277
    fix: pre-remediation the calendar walked a separate code path
    that (a) selected the last pay period whose ``end_date`` was on or
    before the calendar month-end (up to ~13 days stale when the
    period straddled the month boundary) and (b) issued a transaction
    query without ``selectinload(Transaction.entries)``, silently
    degrading to the unreduced contribution (the F-009 seam on a second
    surface).  Both defects collapse into the single canonical
    "balance as of date D" producer.

    Args:
        account: The :class:`~app.models.account.Account` to summarize.
        year: Target calendar year.
        month: Target calendar month (1-12).
        balance_ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        ``Decimal`` -- the projected balance on the last day of the
        target month, quantized to cents via
        :func:`~app.utils.money.round_money` inside the resolver.
    """
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return balance_at.cash_balance_at(account, balance_ctx, last_day)
