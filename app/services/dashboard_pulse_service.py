"""
Shekel Budget App -- Dashboard Pulse / Tracks Producers (Loop B B-1)

The narrow producers behind the Terminal Road dashboard rebuild's two
regions:

  * :func:`compute_pulse_section` -- the single ``balanceChanged`` refresh
    region (canvas + street + due-soon list): the as-of-today hero, the
    13-period projected end-balance chart + threshold, the full-horizon
    trough, the still-due totals (current + next period), and the current
    period's due-soon rows.  Everything derives from one transaction
    state, so one producer + endpoint serves it.
  * :func:`compute_tracks_section` -- the page-load-only position tier:
    savings-goal metro tracks (reshaped from the /savings goal producer)
    and the debt track (the /savings debt summary plus an honest
    principal-paid fraction).

This module is additive (Loop B B-1).  The live page keeps running on the
existing ``dashboard_service`` producers until the B-3 route swap.  Both
producers reuse ``dashboard_service``'s shared row query / bill builder /
anchor-date helpers (and the /savings producers for the tracks) rather
than re-deriving any of them, so the new surfaces and the existing ones
cannot disagree.

Split out of ``dashboard_service`` so neither module exceeds the
1000-line pylint cap; the savings-dashboard package set the precedent for
extracting cohesive dashboard concerns into their own modules.

Pure aggregation service -- no Flask imports, no database writes.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.user import UserSettings
from app.services import balance_at, pay_period_service
from app.services.dashboard_service import (
    _DEFAULT_STALENESS_DAYS,
    _get_last_anchor_date,
    _get_user_settings,
    _query_unpaid_expense_rows,
    _resolve_section_context,
    txn_to_bill_dict,
)
from app.services.entry_service import compute_remaining
from app.utils.dates import to_display_date
from app.utils.money import round_money

_ZERO = Decimal("0")

# The projected end-balance chart shows the current period plus the next
# 12 (~6 months at biweekly cadence -- the developer's normal grid
# timeframe; data-value pass, Gate B amendments).  Fewer points render
# when fewer periods exist.
_CHART_HORIZON_PERIODS = 13


# ── Pulse producer (canvas + street + due-soon) ────────────────────


def compute_pulse_section(user_id: int) -> dict | None:
    """Compute the dashboard pulse region (canvas + street + due-soon).

    The single ``balanceChanged`` refresh region of the Terminal Road
    rebuild (Loop B B-1).  Everything it returns derives from the same
    transaction state, so one producer + endpoint serves the hero figure,
    the projected end-balance chart, the full-horizon trough, the
    still-due totals, and the current period's due-soon rows.

    Returns ``None`` when the user has no resolvable account, no baseline
    scenario, or no period contains today -- the page renders its
    no-data / no-scenario fallback in that case (matching the other
    narrow producers' ``None`` contract).  Otherwise returns a dict with
    keys:

      * ``hero`` -- see :func:`_pulse_hero`.
      * ``chart`` -- see :func:`_pulse_chart`.
      * ``trough`` -- see :func:`_pulse_trough` (``None`` when no period
        is projected): the lowest projected end balance ahead.
      * ``peak`` -- see :func:`_pulse_peak` (``None`` in the same no-period
        case as ``trough``): the highest projected end balance ahead, the
        exact mirror of ``trough`` over the same full forward horizon.
      * ``still_due`` -- see :func:`_pulse_still_due`.
      * ``street`` -- see :func:`_pulse_street` (the current period's
        day-span and today's offset within it).
      * ``due_soon`` -- see :func:`_pulse_due_soon`.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        The pulse-region dict, or ``None`` when the region cannot be
        computed (no account / scenario / current period).
    """
    account, scenario, current_period = _resolve_section_context(user_id)
    if account is None or scenario is None or current_period is None:
        return None

    settings = _get_user_settings(user_id)
    all_periods = pay_period_service.get_all_periods(user_id)
    next_period = pay_period_service.get_next_period(current_period)

    # ONE projection walk over ALL periods through the ``balance_at`` seam:
    # the seam's cash map carries the running balance forward from the
    # anchor period, so the period list MUST include the anchor period (a
    # forward-only slice that excludes it yields an empty map -- the engine
    # has no seed).  The chart slices the current-period-forward tail to 13
    # points and the trough scans the whole forward tail.  The trough
    # horizon is the entire forward run -- the retired negative-projection
    # alert's full multi-year reach, but DELIBERATELY INCLUDING the current
    # period the alert skipped (``start_date <= today``): the chart's first
    # plotted point is the current period's end balance, so the labeled
    # "lowest point ahead" must be able to coincide with it rather than
    # understating the worst visible dip.  The seam returns ``None`` only
    # when the account has no anchor period -- the cache-divergence edge
    # (the ``current_anchor_period_id`` cache cleared while the history row
    # the resolver reads is still present; logged, unreachable in
    # production).  The empty-map fallback degrades the chart / trough / peak
    # to no points (their existing missing-key skips).  The hero is NOT empty
    # in that edge: it reads ``balance_at.balance_at``, whose cash path
    # reconciles the balance from the history row -- so the region shows the
    # reconciled hero balance with an empty chart, a safe degradation rather
    # than a wrong number.
    end_balances = balance_at.balance_map(account, scenario, all_periods)
    if end_balances is None:
        end_balances = {}
    forward_periods = [
        p for p in all_periods
        if p.period_index >= current_period.period_index
    ]

    # ONE unpaid-row query for the still-due totals AND the due-soon list:
    # both read the current period's rows (due-soon is exactly that subset)
    # and still-due additionally reads the next period's, so loading the
    # current+next set once -- with its single ``selectinload(entries)``
    # round trip -- and splitting it in memory avoids a second identical
    # query on the ``balanceChanged`` refresh path.
    period_ids = [current_period.id]
    if next_period is not None:
        period_ids.append(next_period.id)
    unpaid_rows = _query_unpaid_expense_rows(
        account.id, scenario.id, period_ids,
    )

    return {
        "hero": _pulse_hero(account, scenario, current_period, settings),
        "chart": _pulse_chart(forward_periods, end_balances, settings),
        "trough": _pulse_trough(
            forward_periods, end_balances, current_period,
        ),
        "peak": _pulse_peak(
            forward_periods, end_balances, current_period,
        ),
        "still_due": _pulse_still_due(
            unpaid_rows, current_period, next_period,
        ),
        "street": _pulse_street(current_period),
        "due_soon": _pulse_due_soon(unpaid_rows, current_period),
    }


def _pulse_hero(
    account: Account,
    scenario: Scenario,
    current_period: PayPeriod,
    settings: UserSettings | None,
) -> dict:
    """Build the pulse hero block: the as-of-today balance and its captions.

    The headline ``balance`` is the as-of-today projected checking
    balance from the ``balance_at`` seam (``balance_at.balance_at``) -- the
    exact figure ``dashboard_service.compute_balance_section`` shows (it
    reads the same seam) -- so the hero, the chart's first point, and the
    balance card all agree.  Net pay is retired (data-value pass); only the
    next-paycheck DATE survives.

    ``is_stale`` is ``True`` when the anchor has never been set OR its
    last update is strictly older than ``settings.anchor_staleness_days``
    (the same settings-driven staleness the retired Alerts card used; the
    rebuild moves the signal onto the "last updated" caption).

    Args:
        account: The resolved checking account.
        scenario: The baseline scenario.
        current_period: The period containing today.
        settings: The user's settings, or ``None``.

    Returns:
        A dict with keys ``balance``, ``period_start_date``,
        ``period_end_date``, ``account_name``, ``account_id``,
        ``last_updated_date``, ``is_stale``, ``next_paycheck_date``.
    """
    balance = balance_at.balance_at(account, scenario, date.today())
    # One fetch of the raw anchor instant, two truncations: staleness
    # counts days in the UTC frame (storage convention, unchanged), the
    # caption shows the day in the user's display timezone so a late-
    # evening Eastern true-up does not read as "tomorrow".
    last_anchor_dt = _get_last_anchor_date(account.id)

    return {
        "balance": balance,
        "period_start_date": current_period.start_date,
        "period_end_date": current_period.end_date,
        "account_name": account.name,
        "account_id": account.id,
        "last_updated_date": to_display_date(last_anchor_dt),
        "is_stale": _anchor_is_stale(_utc_day(last_anchor_dt), settings),
        "next_paycheck_date": _next_paycheck_date(account.user_id),
    }


def _anchor_is_stale(
    last_updated_date: date | None,
    settings: UserSettings | None,
) -> bool:
    """Return whether the checking anchor is stale (the warning condition).

    Stale means the anchor has NEVER been set (``last_updated_date`` is
    ``None``) OR its last update is strictly older than
    ``settings.anchor_staleness_days`` (the same settings-driven threshold
    the retired Alerts card used; the rebuild surfaces it on the "last
    updated" caption rather than as a separate alert).  A no-settings
    fallback uses ``_DEFAULT_STALENESS_DAYS``.  Extracted from
    :func:`_pulse_hero` so the never-set branch is testable without a
    resolvable anchor (the resolver hard-raises on a truly empty history,
    so the never-set state cannot reach the hero's balance call in
    production -- but the branch is defensive and worth pinning).

    Args:
        last_updated_date: The UTC date of the latest anchor event, or
            ``None`` when the anchor has never been set.
        settings: The user's settings, or ``None``.

    Returns:
        ``True`` when the anchor is never-set or older than the threshold.
    """
    if last_updated_date is None:
        return True
    staleness_days = (
        settings.anchor_staleness_days if settings
        else _DEFAULT_STALENESS_DAYS
    )
    return (date.today() - last_updated_date).days > staleness_days


def _pulse_chart(
    forward_periods: list[PayPeriod],
    end_balances: dict[int, Decimal],
    settings: UserSettings | None,
) -> dict:
    """Build the projected end-balance chart series and threshold line.

    Up to 13 points -- the current period plus the next 12 (fewer when
    fewer periods exist) -- each ``{end_date, balance}`` from the
    anchor-forward end-balance map.  The first point coincides with the
    hero by construction (same producer family, reservation semantics):
    with no entries dated after today the as-of-today balance equals the
    current period's projected end balance.  ``low_balance_threshold`` is
    the user's setting as a ``Decimal`` (the column is a NOT NULL
    whole-dollar integer, so it always carries a value when a settings
    row exists) or ``None`` only when the user has no settings row at
    all -- the UI draws it as a faint dashed line.

    Args:
        forward_periods: Periods from the current one forward, ordered by
            ``period_index``.
        end_balances: The ``period_id -> Decimal`` end-balance map from
            ``balances_for``.
        settings: The user's settings, or ``None`` when the user has no
            settings row.

    Returns:
        A dict with keys ``points`` (a list of ``{end_date, balance}``
        dicts) and ``low_balance_threshold`` (``Decimal``, or ``None``
        when ``settings`` is ``None``).
    """
    chart_periods = forward_periods[:_CHART_HORIZON_PERIODS]
    points = [
        {"end_date": period.end_date, "balance": end_balances[period.id]}
        for period in chart_periods
        if period.id in end_balances
    ]

    threshold = None
    if settings is not None:
        threshold = Decimal(str(settings.low_balance_threshold))

    return {"points": points, "low_balance_threshold": threshold}


def _pulse_trough(
    forward_periods: list[PayPeriod],
    end_balances: dict[int, Decimal],
    current_period: PayPeriod,
) -> dict | None:
    """Find the lowest projected end balance over the FULL forward horizon.

    The "lowest point ahead" stat -- the minimum extremum from
    :func:`_pulse_extremum` (``find_max=False``).  See that helper for the
    full-horizon scan, the deliberate current-period inclusion, and the
    ``offset`` deep-link contract.

    Args:
        forward_periods: Periods from the current one forward, ordered by
            ``period_index``.
        end_balances: The ``period_id -> Decimal`` end-balance map from
            ``balances_for``.
        current_period: The period containing today (the offset origin).

    Returns:
        A dict with keys ``balance`` (the minimum end balance),
        ``end_date``, and ``offset`` (>= 0).  ``None`` when no forward
        period has a projected balance (e.g. an empty projection).
    """
    return _pulse_extremum(
        forward_periods, end_balances, current_period, find_max=False,
    )


def _pulse_peak(
    forward_periods: list[PayPeriod],
    end_balances: dict[int, Decimal],
    current_period: PayPeriod,
) -> dict | None:
    """Find the highest projected end balance over the FULL forward horizon.

    The "highest point ahead" stat -- the exact mirror of
    :func:`_pulse_trough`, the maximum extremum from
    :func:`_pulse_extremum` (``find_max=True``).  Scans the same full
    forward horizon (current period onward, all periods, not just the 13
    charted points) and degrades to ``None`` in the same no-projection
    case as the trough.

    Args:
        forward_periods: Periods from the current one forward, ordered by
            ``period_index``.
        end_balances: The ``period_id -> Decimal`` end-balance map from
            ``balances_for``.
        current_period: The period containing today (the offset origin).

    Returns:
        A dict with keys ``balance`` (the maximum end balance),
        ``end_date``, and ``offset`` (>= 0).  ``None`` when no forward
        period has a projected balance (e.g. an empty projection).
    """
    return _pulse_extremum(
        forward_periods, end_balances, current_period, find_max=True,
    )


def _pulse_extremum(
    forward_periods: list[PayPeriod],
    end_balances: dict[int, Decimal],
    current_period: PayPeriod,
    find_max: bool,
) -> dict | None:
    """Find the extreme projected end balance over the FULL forward horizon.

    The shared core of :func:`_pulse_trough` (``find_max=False``, the
    minimum) and :func:`_pulse_peak` (``find_max=True``, the maximum):
    scans every period from the current one forward (not just the 13
    charted points), so a danger dip OR a peak beyond the chart window is
    still caught.  The horizon is the retired negative-projection alert's
    full multi-year forward reach, with one deliberate divergence: that
    alert skipped the current period (``start_date <= today``), whereas
    this scan INCLUDES it, because the chart's first plotted point is the
    current period's end balance and the "lowest/highest point ahead" stat
    must be able to coincide with it rather than understating the worst
    visible dip (or the visible peak).  The winner's ``offset`` is its
    ``period_index`` minus the current period's, so the UI can deep-link
    the grid at that period (``grid.index?offset=N``).

    Args:
        forward_periods: Periods from the current one forward, ordered by
            ``period_index``.
        end_balances: The ``period_id -> Decimal`` end-balance map from
            ``balances_for``.
        current_period: The period containing today (the offset origin).
        find_max: ``True`` to return the maximum end balance (the peak),
            ``False`` to return the minimum (the trough).

    Returns:
        A dict with keys ``balance`` (the extreme end balance),
        ``end_date``, and ``offset`` (>= 0).  ``None`` when no forward
        period has a projected balance (e.g. an empty projection).
    """
    extremum_period = None
    extremum_balance = None
    for period in forward_periods:
        balance = end_balances.get(period.id)
        if balance is None:
            continue
        if extremum_balance is None:
            extremum_balance = balance
            extremum_period = period
            continue
        if (balance > extremum_balance) if find_max else (balance < extremum_balance):
            extremum_balance = balance
            extremum_period = period

    if extremum_period is None:
        return None

    return {
        "balance": extremum_balance,
        "end_date": extremum_period.end_date,
        "offset": extremum_period.period_index - current_period.period_index,
    }


def _pulse_still_due(
    rows: list[Transaction],
    current_period: PayPeriod,
    next_period: PayPeriod | None,
) -> dict:
    """Compute the still-due totals for the current and next periods.

    Locked basis (Gate B4, data-value pass):

      * Untracked projected expense rows contribute ``effective_amount``
        (the row's displayed obligation: actual when populated, else
        estimated; never negative for an expense).
      * Entry-tracked rows contribute their entries-aware remaining
        (``estimated_amount`` minus the sum of recorded entries) FLOORED
        AT ZERO -- an over-budget envelope contributes ``0``, never a
        negative that would understate the total (its overspend already
        left the as-of-today balance).
      * Transfer-out shadow rows ARE included (B4b): a still-due total is
        an obligation / checking-depletion figure, and the shadow query
        carries them in as expense rows.

    Each period's total is summed in full ``Decimal`` precision and
    rounded once at the boundary with :func:`round_money`.

    Args:
        rows: The current+next periods' unpaid expense rows from the
            shared :func:`_query_unpaid_expense_rows` query (loaded once
            by :func:`compute_pulse_section` and shared with
            :func:`_pulse_due_soon`); each row is bucketed by its
            ``pay_period_id``.
        current_period: The period containing today.
        next_period: The period after the current one, or ``None``.

    Returns:
        A dict with keys ``current_period`` and ``next_period``, each a
        ``Decimal`` total (``round_money(_ZERO)`` -> ``Decimal("0.00")``
        for a period with no still-due rows; ``next_period`` is
        ``Decimal("0.00")`` when there is no next period), plus
        ``next_period_start`` and ``next_period_end`` (the next period's
        date range, both ``None`` when there is no next period -- the
        template renders a generate-periods fallback line then).
    """
    current_total = _ZERO
    next_total = _ZERO
    for txn in rows:
        contribution = _row_still_due(txn)
        if txn.pay_period_id == current_period.id:
            current_total += contribution
        elif next_period is not None and txn.pay_period_id == next_period.id:
            next_total += contribution

    return {
        "current_period": round_money(current_total),
        "next_period": round_money(next_total),
        "next_period_start": (
            next_period.start_date if next_period is not None else None
        ),
        "next_period_end": (
            next_period.end_date if next_period is not None else None
        ),
    }


def _row_still_due(txn: Transaction) -> Decimal:
    """Return one row's still-due contribution on the locked basis (B4a).

    An entry-tracked (envelope) row contributes its entries-aware
    remaining (``estimated_amount`` minus the sum of all recorded
    entries, via :func:`compute_remaining`) floored at zero -- so an
    over-budget envelope contributes ``0`` rather than a negative.  A
    non-tracked row contributes ``effective_amount`` (the obligation the
    bill row already displays; positive for an expense).  Returned
    unrounded; the caller rounds the period sum once at the boundary.

    Args:
        txn: A projected expense :class:`Transaction` with ``entries``
            eager-loaded (the canonical query loads them).

    Returns:
        The row's still-due ``Decimal`` contribution (>= 0).
    """
    if txn.tracks_purchases:
        remaining = compute_remaining(txn.estimated_amount, txn.entries)
        return remaining if remaining > _ZERO else _ZERO
    return txn.effective_amount


def _pulse_street(current_period: PayPeriod) -> dict:
    """Build the street band's day-span and today's offset within it.

    The street band lays the current period out day by day, and the
    due-soon rows already position each dated event at
    ``(due_date - current_period.start_date).days`` (see
    :func:`_pulse_due_soon`).  These two numbers SHARE that same basis so
    the band's percentage math lines up: the period start is day 0, the
    period end is ``days_total``, and an event due on the start sits at 0
    while one due on the end sits at ``days_total``.

      * ``days_total`` -- ``(end_date - start_date).days``: the day span
        of the current period.  The period-end station on the street sits
        at this offset (the far right of the band).
      * ``today_offset`` -- ``(today - start_date).days``: where the
        "Today" marker falls on the band.  May fall outside ``[0,
        days_total]`` only at a period boundary the producer never reaches
        (``compute_pulse_section`` resolves the period that CONTAINS today,
        so ``start_date <= today <= end_date`` and the offset is in range);
        the JS clamps defensively regardless.

    Args:
        current_period: The period containing today.

    Returns:
        A dict with integer keys ``days_total`` and ``today_offset``.
    """
    return {
        "days_total": (current_period.end_date - current_period.start_date).days,
        "today_offset": (date.today() - current_period.start_date).days,
    }


def _pulse_due_soon(
    rows: list[Transaction],
    current_period: PayPeriod,
) -> list[dict]:
    """Build the current period's due-soon rows (the street / mobile list).

    Reuses the shared row query and the shared :func:`txn_to_bill_dict`
    bill builder (the same dicts the live bills card renders), then adds
    two fields each row needs for the street band and its mobile
    fallback:

      * ``day_offset`` -- ``(due_date - current_period.start_date).days``
        for a dated row, so the row sits at the right day on the
        full-width street axis spanning the current period; ``None`` for
        an undated row.
      * ``undated`` -- ``True`` when the row has no ``due_date`` (it lands
        on the "anytime this period" shelf, not a day position).

    Only the current period's unpaid rows are returned (the audit's
    Tier-2 scope: overdue + rest of current period; prior-period unpaid
    rows stay out of scope, as they are for the live bills card).  Rows
    are sorted by ``due_date`` ascending (undated rows last, by name) so
    the list reads chronologically.

    Args:
        rows: The current+next periods' unpaid expense rows from the
            shared :func:`_query_unpaid_expense_rows` query (loaded once
            by :func:`compute_pulse_section` and shared with
            :func:`_pulse_still_due`).  This helper filters to the current
            period's rows -- the next-period rows in the shared set are
            the still-due totals' concern, not the due-soon list's.
        current_period: The period containing today.

    Returns:
        A list of bill dicts (see :func:`txn_to_bill_dict`) each carrying
        the extra ``day_offset`` and ``undated`` keys.
    """
    today = date.today()

    due_soon: list[dict] = []
    for txn in rows:
        if txn.pay_period_id != current_period.id:
            continue
        bill = txn_to_bill_dict(txn, today)
        if txn.due_date is not None:
            bill["day_offset"] = (txn.due_date - current_period.start_date).days
            bill["undated"] = False
        else:
            bill["day_offset"] = None
            bill["undated"] = True
        due_soon.append(bill)

    # Dated rows first, sorted by due_date; undated rows last, by name.
    due_soon.sort(
        key=lambda b: (
            b["undated"],
            b["due_date"] if b["due_date"] is not None else current_period.start_date,
            b["name"],
        )
    )
    return due_soon


def _utc_day(last_anchor_dt: datetime | None) -> date | None:
    """Truncate a stored UTC anchor instant to its UTC calendar day.

    The storage-domain convention shared by the staleness math and
    ``balance_resolver``: normalize to UTC before truncating so the day
    cannot shift by the server's local timezone.  ``None``-safe (an
    account whose anchor has never been set).  Distinct from
    ``app.utils.dates.to_display_date``, which truncates in the user's
    DISPLAY timezone -- staleness counts days in the UTC frame, the
    caption shows the day in the user's frame.

    Args:
        last_anchor_dt: The latest anchor ``created_at`` instant, or
            ``None``.

    Returns:
        The UTC calendar day, or ``None`` when ``last_anchor_dt`` is
        ``None``.
    """
    if last_anchor_dt is None:
        return None
    return last_anchor_dt.astimezone(timezone.utc).date()


def _last_anchor_update_date(account_id: int) -> date | None:
    """Return the UTC calendar date of the account's most recent anchor event.

    A thin date-only wrapper over ``dashboard_service._get_last_anchor_date``
    (which returns the raw ``created_at`` timestamp): UTC-normalizes the
    timestamp before truncating to a day (via :func:`_utc_day`) so the
    date matches ``balance_resolver``'s UTC-day convention and cannot
    shift by the server's local timezone.  ``None`` when the account has
    no anchor history (never set).

    Args:
        account_id: The account whose latest anchor date is wanted.

    Returns:
        The UTC date of the latest anchor event, or ``None``.
    """
    return _utc_day(_get_last_anchor_date(account_id))


def _next_paycheck_date(user_id: int) -> date | None:
    """Return the start date of the first pay period that begins after today.

    The next paycheck lands on the next period's payday (its
    ``start_date``).  ``None`` when no period starts after today (the
    schedule does not extend into the future).

    Args:
        user_id: The user whose pay periods to scan.

    Returns:
        The next future period's ``start_date``, or ``None``.
    """
    today = date.today()
    next_period = (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.start_date > today,
        )
        .order_by(PayPeriod.start_date)
        .first()
    )
    return next_period.start_date if next_period is not None else None


# ── Tracks producer (savings goals + debt position) ────────────────


def compute_tracks_section(user_id: int) -> dict:
    """Compute the position tier: savings-goal tracks and the debt track.

    The page-load-only position tier of the Terminal Road rebuild
    (Loop B B-1; deliberately not on the ``balanceChanged`` refresh path,
    per the Gate B6 rationale).  Reuses the /savings producers so both
    screens agree on the same figures:

      * ``goals`` -- one dict per active goal, reshaped from
        ``savings_dashboard_service.compute_goal_progress`` into the metro
        track contract (see :func:`_track_goal_datum`).
      * ``debt`` -- the debt summary from
        ``savings_dashboard_service.compute_debt_summary`` with an added
        ``principal_paid_fraction`` (the honest principal-paid fraction,
        or ``None`` when no per-loan original principal is available; the
        UI renders no positional marker in that case).  ``None`` when the
        user has no loan accounts.

    No exception is caught here: the producers this delegates to are the
    same code the /savings route runs without a guard, so a
    ``ValueError`` / ``KeyError`` / ``AttributeError`` from that
    computation is a programming bug that must fail loud, not be masked as
    an empty tracks tier (CLAUDE.md rule 4); letting it propagate fails
    loud and identically on the dashboard and /savings pages.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        A dict with keys ``goals`` (a list, possibly empty) and ``debt``
        (a dict or ``None``).
    """
    # Pylint: ``import-outside-toplevel`` -- Deferred: savings_dashboard_service
    # pulls the heaviest service import chain (+27 modules, measured); loaded only
    # when this path runs, not on every dashboard_pulse_service import.
    from app.services import savings_dashboard_service  # pylint: disable=import-outside-toplevel

    goal_data = savings_dashboard_service.compute_goal_progress(user_id)
    goals = [_track_goal_datum(gd) for gd in goal_data]

    debt = savings_dashboard_service.compute_debt_summary(user_id)
    if debt is not None:
        debt = dict(debt)
        debt["principal_paid_fraction"] = (
            savings_dashboard_service.compute_debt_principal_progress(user_id)
        )

    return {"goals": goals, "debt": debt}


def _track_goal_datum(goal_datum: dict) -> dict:
    """Reshape one ``compute_goal_progress`` entry into the metro-track contract.

    Pulls only the fields the savings track renders -- the goal's name and
    account name, the progress percent and balance/target, and the
    ``calculate_trajectory`` outputs (pace, projected completion date,
    required monthly) -- so the template reads a flat dict rather than
    reaching into the nested ``goal`` ORM object and ``trajectory`` sub-dict.

    Args:
        goal_datum: One per-goal dict from
            ``savings_dashboard_service.compute_goal_progress`` (carries
            ``goal``, ``progress_pct``, ``current_balance``,
            ``resolved_target``, ``trajectory``, ``monthly_contribution``).

    Returns:
        A dict with keys ``name``, ``account_name``, ``account_id``,
        ``progress_pct``, ``current_balance``, ``target_amount``,
        ``target_date``, ``pace``, ``projected_completion_date``,
        ``required_monthly``, ``monthly_contribution``.
    """
    goal = goal_datum["goal"]
    trajectory = goal_datum["trajectory"]
    return {
        "name": goal.name,
        "account_name": goal.account.name,
        "account_id": goal.account_id,
        "progress_pct": goal_datum["progress_pct"],
        "current_balance": goal_datum["current_balance"],
        "target_amount": goal_datum["resolved_target"],
        "target_date": goal.target_date,
        "pace": trajectory["pace"],
        "projected_completion_date": trajectory["projected_completion_date"],
        "required_monthly": trajectory["required_monthly"],
        "monthly_contribution": goal_datum["monthly_contribution"],
    }
