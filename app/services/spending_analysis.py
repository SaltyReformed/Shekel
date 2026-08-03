"""
Shekel Budget App -- Shared Spending-Analysis Primitives

The settled-expense primitives shared by every retrospective spending
surface: the Year-End spending section (:mod:`year_end_summary_service`)
and the unified Spending report (:mod:`spending_report_service`, whose
``_build_surprises`` is the settled-surprises kernel the retired Variance
tab once owned; the retired per-period Trends engine read spending through
here too until the D7 rebuild removed its last consumer).

Extracting them here means the "what counts as measured spending" rule is
defined once rather than re-implemented per surface (coding-standards rule
13; the T-P3 ``projection_inputs`` precedent, which closed a cross-file
``duplicate-code`` finding the same way):

* :func:`query_settled_expenses` -- the one settled-expense ORM query
  (settled status, expense type, not deleted, scoped to one account /
  scenario / period set).  Every consumer reads spending through it, so a
  change to what "settled spending" selects is a single edit.
* :func:`query_settled_expenses_in_span` -- the same row filters selected
  by the attribution rule instead of a period set: COALESCE(due_date,
  owning period start) inside a calendar span, across ALL the user's pay
  periods.  The Spending report's calendar windows read through it so a
  bill due in month M is attributed to M even when its funding period does
  not overlap M.
* :func:`resolved_actual_amount` -- the settled-surprises kernel's
  estimate-at-entry vs actual-at-settle rule (a settled row uses its
  entered actual, falling back to the estimate; an unsettled row has no
  actual yet, so it reads back its estimate and shows zero variance).
* :func:`signed_pct` -- the guarded "signed value as a percentage of a
  base" helper (``None`` when the base is zero), shared by the surprises
  figures and the Spending hero's vs-prior / vs-average chips.
* :func:`payment_timeliness_from_txns` -- the on-time / late / average
  days-before-due rule, given the already window-attributed settled
  expenses.

Pure-function module -- no Flask imports; the only side effect is the read
query in :func:`query_settled_expenses`.
"""

import calendar as cal_mod
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import contains_eager, joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.balance_predicates import settled_status_ids
from app.utils.money import CENTS, HUNDRED, ZERO

_WINDOW_TYPES = frozenset({"pay_period", "month", "year"})


def validate_window(
    window_type: str,
    period_id: int | None,
    month: int | None,
    year: int | None,
) -> None:
    """Validate a discriminated pay-period / month / year window selector.

    Shared by the analytics window-based report services (the Spending
    report and the confirmed-ledger Income Statement) so the required-field
    rule and its error messages live in one place rather than a copy per
    surface.

    Args:
        window_type: One of ``"pay_period"`` / ``"month"`` / ``"year"``.
        period_id: The pay period id (required for ``"pay_period"``).
        month: The calendar month 1-12 (required with ``year`` for
            ``"month"``).
        year: The calendar year (required for ``"month"`` and ``"year"``).

    Raises:
        ValueError: If ``window_type`` is unknown, or a required field for
            the type is missing.
    """
    if window_type not in _WINDOW_TYPES:
        raise ValueError(
            f"Invalid window_type {window_type!r}. Must be one of "
            f"{sorted(_WINDOW_TYPES)}."
        )
    if window_type == "pay_period" and period_id is None:
        raise ValueError(
            "period_id is required when window_type is 'pay_period'."
        )
    if window_type == "month" and (month is None or year is None):
        raise ValueError(
            "Both month and year are required when window_type is 'month'."
        )
    if window_type == "year" and year is None:
        raise ValueError("year is required when window_type is 'year'.")


def query_settled_expenses(
    scenario_id: int,
    period_ids: list[int],
    account_id: int,
) -> list[Transaction]:
    """Load settled expense transactions for one account over given periods.

    Filters: settled status only (Paid/Received/Settled -- so Cancelled and
    Credit, which are not settled, are excluded), expense type only, not
    deleted, one account, one scenario, and ``pay_period_id`` in
    *period_ids*.  Transfer shadows are included: they are ordinary
    ``Transaction`` rows that participate in spending.  Eager-loads
    ``category`` and ``pay_period`` to prevent N+1 lookups when the caller
    groups by category or attributes by period.

    Args:
        scenario_id: The budget scenario to scope to (the caller's
            baseline).
        period_ids: The pay-period ids to include.  An empty list yields
            an empty result.
        account_id: The account to scope to (the analytics checking scope).

    Returns:
        The matching settled expense :class:`Transaction` rows.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    # Pylint: ``duplicate-code`` -- the settled-expense query core.  The
    # account / scenario / period / expense-type filter prefix coincides
    # with ``dashboard_service``'s expense query, but the two diverge on the
    # parts that matter (eager-loads and the settled-vs-projected status
    # gate), so a shared builder would need both as parameters and save no
    # logic (coding-standards rule 13).  One-sided ``duplicate-code``
    # disable, mirroring the journal_entry/transfer FK-block precedent.
    # pylint: disable=duplicate-code
    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.status_id.in_(settled_status_ids()),
        )
        .all()
    )
    # pylint: enable=duplicate-code


def query_settled_expenses_in_span(
    scenario_id: int,
    account_id: int,
    user_id: int,
    first_day: date,
    last_day: date,
) -> list[Transaction]:
    """Load the settled expenses ATTRIBUTED to a calendar span.

    The same row filters as :func:`query_settled_expenses`, selected by the
    attribution rule instead of a period set: rows whose
    ``COALESCE(due_date, owning period start)`` falls inside
    ``[first_day, last_day]``, across ALL the user's pay periods.  The
    former period-overlap pre-filter under-fetched at window boundaries: a
    settled bill due in month M whose funding period did not overlap M was
    attributed to NO month window at all (its own period's months excluded
    it by date; M never loaded its period).  Selecting by the attribution
    day itself makes every settled expense belong to exactly one calendar
    window, and the result no longer depends on which window is viewed.

    The COALESCE runs in SQL on the joined period row -- the same rule
    consumers previously applied in Python -- so the filter and the
    attribution stay one definition.

    Args:
        scenario_id: The budget scenario to scope to (the caller's
            baseline).
        account_id: The account to scope to (the analytics checking scope).
        user_id: The owning user (scopes the pay-period join).
        first_day: The span's first calendar day (inclusive).
        last_day: The span's last calendar day (inclusive).

    Returns:
        The matching settled expense :class:`Transaction` rows, with
        ``category`` and ``pay_period`` eager-loaded like the sibling query.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    attribution_day = db.func.coalesce(
        Transaction.due_date, PayPeriod.start_date,
    )
    return (
        db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .options(
            joinedload(Transaction.category),
            contains_eager(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            PayPeriod.user_id == user_id,
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.status_id.in_(settled_status_ids()),
            attribution_day >= first_day,
            attribution_day <= last_day,
        )
        .all()
    )


def resolved_actual_amount(txn: Transaction) -> Decimal:
    """Return the 'actual' amount for an estimate-vs-actual comparison.

    The Variance/surprises kernel's rule: a settled transaction uses its
    entered ``actual_amount`` when populated, else its ``estimated_amount``
    (the done-without-actual edge case -- a zero variance, not a spurious
    one).  A projected transaction has no actual yet, so it reads back its
    ``estimated_amount`` and its individual variance is exactly zero.  A
    "surprise" is a row whose resolved actual differs from its estimate --
    only a settled row with an explicitly entered, different actual can
    produce one.

    Args:
        txn: The transaction to resolve.  ``txn.status`` is declared
            ``lazy="joined"`` so ``is_settled`` is available without an
            explicit load.

    Returns:
        The comparison actual as a ``Decimal``.
    """
    if txn.status and txn.status.is_settled:
        if txn.actual_amount is not None:
            return txn.actual_amount
        return txn.estimated_amount
    return txn.estimated_amount


def calendar_window_bounds(
    window_type: str, year: int, month: int | None,
) -> tuple[date, date]:
    """Return the inclusive ``(first_day, last_day)`` of a calendar window.

    Shared by the analytics window services (the Spending report and the
    confirmed-ledger Income Statement) so the month / year date-span rule is
    defined once rather than re-derived per surface.

    Args:
        window_type: ``"month"`` (spans ``month`` within ``year``) or any
            other value, treated as the full ``year``.
        year: The calendar year.
        month: The calendar month 1-12 (required for a ``"month"`` window;
            ignored for a year window).

    Returns:
        The first and last calendar dates of the month or year.
    """
    if window_type == "month":
        last_dom = cal_mod.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_dom)
    return date(year, 1, 1), date(year, 12, 31)


def category_names(txn: Transaction) -> tuple[str, str]:
    """Return the ``(group_name, item_name)`` labels for a transaction.

    Reads the transaction's category, falling back to
    ``("Uncategorized", "Uncategorized")`` for a row with no category.
    Shared by the year-end spending section and the unified Spending report
    so both bucket an uncategorized row under the same label.

    Args:
        txn: The transaction whose category labels to resolve.

    Returns:
        The ``(group_name, item_name)`` pair.
    """
    if txn.category is None:
        return ("Uncategorized", "Uncategorized")
    return (txn.category.group_name, txn.category.item_name)


def signed_pct(numerator: Decimal, base: Decimal) -> Decimal | None:
    """Return ``numerator / base`` as a signed 2-dp percent, or ``None``.

    Guards division by zero: a zero base has no meaningful percentage (the
    variance-figures and the Spending hero's vs-prior / vs-average chips
    all read ``None`` in that case rather than a fabricated value).  The
    percentage is rounded to two decimal places with ``ROUND_HALF_UP`` (the
    project's money-rounding convention).

    Args:
        numerator: The signed quantity (a variance, or a vs-prior delta).
        base: The percentage base (an estimate, or a prior spend).

    Returns:
        ``(numerator / base) * 100`` quantized to 0.01, or ``None`` when
        ``base`` is zero.
    """
    if base == ZERO:
        return None
    return (numerator / base * HUNDRED).quantize(CENTS, rounding=ROUND_HALF_UP)


def payment_timeliness_from_txns(txns: list[Transaction]) -> dict | None:
    """Compute on-time / late / average-days metrics over settled expenses.

    Examines the subset of *txns* that carry both ``settled_on`` and
    ``due_date`` (the only rows whose timing is knowable -- an unsettled row
    has no settle day by construction).  A bill paid on or before its due date
    (``days_paid_before_due >= 0``) is on time; the average is signed
    (positive = paid early on average).  Routes through
    ``Transaction.days_paid_before_due``, which since plan step X-f1 subtracts
    two civil dates rather than converting an instant (ruling R-EC).

    The caller supplies transactions already attributed to the reporting
    window (the year-end section pre-filters by attribution year; the
    Spending report supplies the chosen window's settled expenses), so this
    core owns only the paid-at/due-date gate and the counting.

    Args:
        txns: Settled expense transactions attributed to the window.

    Returns:
        A dict with ``total_bills_paid``, ``paid_on_time``, ``paid_late``,
        and ``avg_days_before_due`` (a 2-dp ``Decimal``), or ``None`` when
        no transaction has both ``settled_on`` and ``due_date``.
    """
    applicable = [
        txn for txn in txns
        if txn.settled_on is not None and txn.due_date is not None
    ]
    if not applicable:
        return None

    paid_on_time = 0
    paid_late = 0
    total_days = 0
    for txn in applicable:
        days_before = txn.days_paid_before_due
        total_days += days_before
        if days_before >= 0:
            paid_on_time += 1
        else:
            paid_late += 1

    avg_days = (
        Decimal(str(total_days)) / Decimal(str(len(applicable)))
    ).quantize(CENTS, rounding=ROUND_HALF_UP)

    return {
        "total_bills_paid": len(applicable),
        "paid_on_time": paid_on_time,
        "paid_late": paid_late,
        "avg_days_before_due": avg_days,
    }
