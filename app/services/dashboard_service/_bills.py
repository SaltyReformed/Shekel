"""
Shekel Budget App -- Dashboard: the shared bill query and bill dict.

The ONE Projected-expense row query the dashboard's two bill surfaces
read (the still-due totals and the due-soon list, both in
:func:`~._pulse.compute_pulse_section`), plus the render-ready bill dict
those rows become -- with the E-21 single-base entry progress.  Defined
here once rather than per producer, so the row set, the eager loads and
the Projected / expense / not-deleted filter cannot drift apart.

Pure aggregation -- no Flask imports, no database writes.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services.entry_service import compute_entry_sums, compute_remaining
from app.utils.balance_predicates import is_projected_clause


# ── Shared bill query and render-ready bill dict ───────────────────


def _query_unpaid_expense_rows(
    account_id: int,
    scenario_id: int,
    period_ids: list[int],
) -> list[Transaction]:
    """Load the unpaid (Projected) expense rows for a set of periods.

    The single query the dashboard's bill surfaces share -- the still-due
    totals and the due-soon list (both in
    :func:`~._pulse.compute_pulse_section`) -- so the row set,
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


# ``_is_entry_tracked`` used to live here and is DELETED (pay-calendar plan
# step C2-f2e).  It was ``return txn.tracks_purchases`` -- a pass-through in
# front of the model property whose OWN docstring calls itself "the single
# source of truth for the 'is this an envelope / entry-capable row?' question
# across services, routes, and templates".  Its stated purpose was to keep this
# module's two readers from re-deriving the check inline "and so drift apart",
# and that purpose was already false: ``_pulse._row_still_due`` prices the same
# rows and reads ``txn.tracks_purchases`` directly, so there were two spellings
# and the centralising one covered neither of the surfaces that could disagree.
# A second door in front of a single source of truth is not centralisation; it
# is one more thing that can be pointed somewhere else.  All three sites read
# the property now.


def txn_to_bill_dict(
    txn: Transaction, today: date, contribution: Decimal, budget: Decimal,
) -> dict:
    """Build a bill dict for the dashboard bills template from a Transaction.

    Used by :func:`~._pulse._due_soon` to produce one
    render-ready dict per due-soon bill.

    Expects txn.template and txn.entries to be accessible -- callers
    dealing with collections should eager-load them via selectinload
    /joinedload to avoid N+1 queries.

    E-21 / MED-03 / F-028 / F-056: for entry-tracked (envelope) bills
    the ``amount`` field is set from the row's resolved BUDGET so it shares
    the same declared base as ``entry_remaining`` and
    ``entry_over_budget`` (built from the same figure in
    :func:`_entry_progress_fields`).  ``amount_base`` carries the
    label the template surfaces to the user ("budget") so the base is
    disclosed in the UI, not implicit.  Non-entry-tracked rows show what
    the row CONTRIBUTES; ``amount_base`` is None there so the template
    skips the label.

    **That contribution arrives as an ARGUMENT** (plan step X-au-c2).  It
    read ``txn.effective_amount``, a model property that could not answer
    for a row whose amount is DERIVED -- such a row stores no figure, and
    resolving a paycheck needs the owner's whole pay-period set, which no
    per-row property can hold.  The caller resolves the whole row set
    ONCE through :func:`app.services.cash_ledger.contributions_by_id` and
    indexes it here, so the paycheck engine runs once per read pass
    rather than once per bill (finding **N-228**).

    Args:
        txn: The Transaction to convert.
        today: The reference date used to compute days_until_due.
        contribution: What this row contributes, from the caller's
            :func:`~app.services.cash_ledger.contributions_by_id` map --
            ``0`` for a row that contributes nothing, the entered
            figure a SETTLED row RECORDED as moved, else the row's resolved
            amount.  Read only for a non-entry-tracked row; an envelope
            answers on its E-21 budget base instead.
        budget: What the row's amount RESOLVES to, from the caller's
            :func:`~app.services.cash_ledger.amounts_by_id` map -- the E-21
            declared base.  An ARGUMENT rather than a read of
            ``txn.estimated_amount`` since plan step X-au-c2b, for the reason
            the contribution beside it is one: under the amount model a derived
            row stores no figure in that column, and this is the second of the
            two questions a caller must resolve for a whole row set at once.

    Returns:
        Dict matching the bills template contract, including the
        entry progress fields from _entry_progress_fields and the
        ``amount_base`` label that discloses which base the amount
        cell uses.
    """
    days_until = (txn.due_date - today).days if txn.due_date else None
    is_entry_tracked = txn.tracks_purchases
    if is_entry_tracked:
        amount = budget
        amount_base = "budget"
    else:
        amount = contribution
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
    bill.update(_entry_progress_fields(txn, budget))
    return bill


def _entry_progress_fields(txn: Transaction, budget: Decimal) -> dict:
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
    figures are computed against the row's RESOLVED amount -- the
    declared E-21 budget base -- so the row's three numbers (amount,
    remaining, over-budget) all share one base.  ``txn_to_bill_dict``
    anchors the amount cell on the same base and passes it in; the template
    surfaces ``bill.amount_base`` to disclose it.

    Expects txn.template and txn.entries to already be loaded on the
    transaction object (eager-loaded by the caller).

    Args:
        txn: The Transaction to inspect.
        budget: The row's resolved amount -- the E-21 base, resolved once for
            the whole row set by the caller (plan step X-au-c2b).  It was read
            here as ``txn.estimated_amount``, the COLUMN a derived row does not
            carry.

    Returns:
        Dict with the five entry progress fields.
    """
    is_tracked = txn.tracks_purchases
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
    remaining = compute_remaining(budget, txn.entries)
    over_budget = total > budget
    # Templates display, never compute (coding-standards): the
    # over-budget overage is the positive dollar amount by which the
    # entries exceed the declared budget base.  Computing it here keeps
    # the ``|abs`` arithmetic out of the bill-row template, where it
    # previously lived.  ``None`` when the row is not over budget so the
    # template renders the "remaining" branch instead.
    over_budget_amount = total - budget if over_budget else None
    return {
        "is_tracked": True,
        "entry_total": total,
        "entry_count": len(txn.entries),
        "entry_remaining": remaining,
        "entry_over_budget": over_budget,
        "entry_over_budget_amount": over_budget_amount,
    }
