"""
Shekel Budget App -- Dashboard Shared Helpers

The shared query / bill-builder / anchor-date helpers behind the Terminal
Road dashboard's pulse region (``dashboard_pulse_service``).  After the
Loop B B-3 rebuild this module no longer assembles a full page dict: the
retired summary cards (alerts, cash runway, payday, savings-goal /
debt cards, the two-period bills list) are gone (see
``docs/design/dashboard_card_audit.md`` "Retirements").  What survives is
the canonical, reused machinery:

  * :func:`_resolve_section_context` -- the account / scenario /
    current-period resolution the pulse producer's head shares.
  * :func:`_query_unpaid_expense_rows` -- the ONE Projected-expense query
    the still-due totals and due-soon list read.
  * :func:`txn_to_bill_dict` + :func:`_entry_progress_fields` -- the
    render-ready bill dict (with the E-21 single-base entry progress) the
    due-soon list renders.
  * :func:`compute_balance_section` -- the hero-shaped balance fragment
    the anchor editor's Cancel / Escape reverts to (``revert=dashboard``).
  * the settings / anchor-date helpers (:func:`_get_user_settings`,
    :func:`_get_last_anchor_date`) the pulse producer reuses.

Pure aggregation service -- no Flask imports, no database writes.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.user import UserSettings
from app.services import balance_resolver, pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import compute_entry_sums, compute_remaining
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.balance_predicates import is_projected_clause

_ZERO = Decimal("0")

# Anchor-staleness fallback when the user has no settings row.  Shared
# with ``dashboard_pulse_service._anchor_is_stale`` (the rebuild surfaces
# the staleness signal on the hero's "last updated" caption).
_DEFAULT_STALENESS_DAYS = 14


def compute_balance_section(user_id: int) -> dict:
    """Compute the hero-shaped balance fragment for the anchor-edit revert.

    The narrow producer behind ``dashboard.balance_section`` -- the GET
    endpoint the dashboard anchor editor's Cancel / Escape (and the
    409-conflict retry) reverts to (``accounts._anchor_revert_url`` maps
    ``revert=dashboard`` here).  It re-renders ``_pulse_balance.html``, the
    ``#balance-display`` control the editor replaced, so it returns a
    dict shaped like the pulse hero: a ``hero`` sub-dict carrying the
    as-of-today ``balance`` and the ``account_id`` the control needs.

    The ``balance`` is the canonical as-of-today projected checking
    balance from ``balance_resolver.balance_as_of_date`` -- the exact
    figure the pulse hero shows -- so the reverted fragment and the main
    pulse region agree to the cent.  When no period contains today the
    resolver cannot project to today, so the raw anchor balance is used
    (the editor is only reachable with a current period in practice; this
    keeps the helper total).

    Args:
        user_id: The current user's id.

    Returns:
        A dict with key ``hero`` -> ``{balance, account_id}``, or
        ``{"hero": None}`` when the user has no resolvable account.
    """
    account, scenario, current_period = _resolve_section_context(user_id)
    if account is None or scenario is None:
        return {"hero": None}

    if current_period is not None:
        balance = balance_resolver.balance_as_of_date(
            account, scenario.id, date.today(),
        )
    else:
        balance = account.current_anchor_balance or _ZERO

    return {"hero": {"balance": balance, "account_id": account.id}}


def _resolve_section_context(
    user_id: int,
) -> tuple[Account | None, Scenario | None, PayPeriod | None]:
    """Resolve the account, baseline scenario, and current period.

    The shared head-of-function resolution the pulse producer
    (``dashboard_pulse_service.compute_pulse_section``) and
    :func:`compute_balance_section` both need, so the resolution is
    defined once rather than copied.

    Returns:
        ``(account, scenario, current_period)``.  ``account`` is ``None``
        when the user has no resolvable grid account; ``scenario`` is
        ``None`` when there is no baseline scenario; ``current_period``
        is ``None`` when no period contains today.
    """
    settings = _get_user_settings(user_id)
    account = resolve_grid_account(user_id, settings)
    if account is None:
        return None, None, None

    scenario = get_baseline_scenario(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    return account, scenario, current_period


# ── Shared bill query and render-ready bill dict ───────────────────


def _query_unpaid_expense_rows(
    account_id: int,
    scenario_id: int,
    period_ids: list[int],
) -> list[Transaction]:
    """Load the unpaid (Projected) expense rows for a set of periods.

    The single query the dashboard's bill surfaces share -- the still-due
    totals and the due-soon list (both in
    ``dashboard_pulse_service.compute_pulse_section``) -- so the row set,
    eager-loads, and the Projected / expense / not-deleted filter are
    defined exactly once rather than copied per producer (DRY).

    Transfer-out shadows ARE included: they are expense-typed
    transactions, so they satisfy the expense filter and are obligations
    that still draw down checking (the Gate B4b ruling).  Income shadows
    are not (they are income-typed).

    selectinload(entries) + joinedload(template) avoid N+1 lookups when a
    consumer checks ``is_envelope`` or iterates entries for the
    entries-aware still-due / progress computation.  The Projected filter
    routes through the centralized ``is_projected_clause`` (D6-09 /
    MED-02) so every SQL filter over Projected shares one definition with
    the Python ``is_projected`` predicate.

    Args:
        account_id: The account whose rows to load.
        scenario_id: The scenario the rows belong to.
        period_ids: The pay period ids to load rows for.  An empty list
            yields an empty result.

    Returns:
        The matching :class:`Transaction` rows, with ``category``,
        ``pay_period``, ``template``, and ``entries`` eager-loaded.
    """
    if not period_ids:
        return []

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.pay_period),
            joinedload(Transaction.template),
            selectinload(Transaction.entries),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            is_projected_clause(Transaction),
            Transaction.transaction_type_id == expense_type_id,
        )
        .all()
    )


def _is_entry_tracked(txn: Transaction) -> bool:
    """Return True if the transaction's template enables envelope tracking.

    Per E-21 (MED-03 / F-028 / F-056) entry-tracked bill rows anchor
    every visible figure (amount cell, remaining, over-budget flag) on
    ``estimated_amount`` -- the declared budget base -- so the row's
    three numbers always answer the same question.  Centralising the
    "is this row entry-tracked" check here keeps :func:`txn_to_bill_dict`
    and :func:`_entry_progress_fields` from re-deriving it inline (and
    so cannot drift apart): both call this helper.

    Args:
        txn: The Transaction to inspect.  ``txn.template`` must be
            accessible (eager-loaded by the caller for collections).

    Returns:
        True when the transaction is purchase-tracked -- either its
        template has ``is_envelope = True`` or, for an ad-hoc row, its
        own ``is_envelope`` flag is set; otherwise False.
    """
    return txn.tracks_purchases


def txn_to_bill_dict(txn: Transaction, today: date) -> dict:
    """Build a bill dict for the dashboard bills template from a Transaction.

    Used by ``dashboard_pulse_service._pulse_due_soon`` to produce one
    render-ready dict per due-soon bill.

    Expects txn.template and txn.entries to be accessible -- callers
    dealing with collections should eager-load them via selectinload
    /joinedload to avoid N+1 queries.

    E-21 / MED-03 / F-028 / F-056: for entry-tracked (envelope) bills
    the ``amount`` field is set from ``estimated_amount`` so it shares
    the same declared base as ``entry_remaining`` and
    ``entry_over_budget`` (also derived from ``estimated_amount`` in
    :func:`_entry_progress_fields`).  ``amount_base`` carries the
    label the template surfaces to the user ("budget") so the base is
    disclosed in the UI, not implicit.  Non-entry-tracked rows keep
    ``effective_amount`` (tier-3 actual when populated, otherwise
    estimated) because the row has no progress fields to be
    inconsistent with; ``amount_base`` is None there so the template
    skips the label.

    Args:
        txn: The Transaction to convert.
        today: The reference date used to compute days_until_due.

    Returns:
        Dict matching the bills template contract, including the
        entry progress fields from _entry_progress_fields and the
        ``amount_base`` label that discloses which base the amount
        cell uses.
    """
    days_until = (txn.due_date - today).days if txn.due_date else None
    is_entry_tracked = _is_entry_tracked(txn)
    if is_entry_tracked:
        amount = txn.estimated_amount
        amount_base = "budget"
    else:
        amount = txn.effective_amount
        amount_base = None
    bill = {
        "id": txn.id,
        "name": txn.name,
        "amount": amount,
        "amount_base": amount_base,
        "due_date": txn.due_date,
        "period_start_date": txn.pay_period.start_date,
        "category_group": txn.category.group_name if txn.category else None,
        "category_item": txn.category.item_name if txn.category else None,
        "is_transfer": txn.transfer_id is not None,
        "days_until_due": days_until,
    }
    bill.update(_entry_progress_fields(txn))
    return bill


def _entry_progress_fields(txn: Transaction) -> dict:
    """Build entry progress fields for a bill dict from a Transaction.

    Returns a dict with keys is_tracked, entry_total, entry_count,
    entry_remaining, and entry_over_budget.  When the transaction is
    not entry-capable (no template with is_envelope) or has no
    recorded entries, the progress fields are None/0/False and the
    dashboard template falls back to the standard amount display.
    Otherwise returns the debit+credit sum, the remaining budget,
    and a flag indicating whether the sum exceeds the estimated
    amount.

    Per E-21 / MED-03 / F-028 / F-056 the remaining and over-budget
    figures are computed against ``txn.estimated_amount`` -- the
    declared E-21 budget base -- so the row's three numbers (amount,
    remaining, over-budget) all share one base.  ``txn_to_bill_dict``
    anchors the amount cell on the same base; the template surfaces
    ``bill.amount_base`` to disclose it.

    Expects txn.template and txn.entries to already be loaded on the
    transaction object (eager-loaded by the caller).

    Args:
        txn: The Transaction to inspect.

    Returns:
        Dict with the five entry progress fields.
    """
    is_tracked = _is_entry_tracked(txn)
    if not is_tracked or not txn.entries:
        return {
            "is_tracked": is_tracked,
            "entry_total": None,
            "entry_count": 0,
            "entry_remaining": None,
            "entry_over_budget": False,
            "entry_over_budget_amount": None,
        }

    debit, credit = compute_entry_sums(txn.entries)
    total = debit + credit
    remaining = compute_remaining(txn.estimated_amount, txn.entries)
    over_budget = total > txn.estimated_amount
    # Templates display, never compute (coding-standards): the
    # over-budget overage is the positive dollar amount by which the
    # entries exceed the declared budget base.  Computing it here keeps
    # the ``|abs`` arithmetic out of the bill-row template, where it
    # previously lived.  ``None`` when the row is not over budget so the
    # template renders the "remaining" branch instead.
    over_budget_amount = (
        total - txn.estimated_amount if over_budget else None
    )
    return {
        "is_tracked": True,
        "entry_total": total,
        "entry_count": len(txn.entries),
        "entry_remaining": remaining,
        "entry_over_budget": over_budget,
        "entry_over_budget_amount": over_budget_amount,
    }


# ── Shared settings / anchor-date helpers ──────────────────────────


def _get_user_settings(user_id: int) -> UserSettings | None:
    """Load user settings."""
    return (
        db.session.query(UserSettings)
        .filter_by(user_id=user_id)
        .first()
    )


def _get_last_anchor_date(account_id: int):
    """Return the created_at of the most recent anchor history entry.

    Returns None if no anchor history exists.
    """
    entry = (
        db.session.query(AccountAnchorHistory.created_at)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.created_at.desc())
        .first()
    )
    return entry[0] if entry else None
