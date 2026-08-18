"""What a SET of purchases adds up to, and the contexts a screen renders.

``entry_service``'s derivation half: the pure per-set reductions (the debit /
credit split, the remaining budget, the settled actual, the percent complete),
the in-period check, and the three builders that assemble the whole context an
envelope's cell or entry list renders from.

**Nothing here writes**, which is why it is the leaf: the write doors
(:mod:`._doors`) read the reductions from here and nothing here reads them, so
the arrow runs one way and neither half can grow a cycle through the other.

**The sum of ALL of a row's purchases is NOT here**, and it left at plan step
X-au-c3.  It was ``compute_actual_from_entries``, named for a column that step
removed; it is now :func:`app.services.row_valuation.purchases_total`, in the
module both the cash and loan tiers can reach -- because the settlement record's
own accessor needs it, and a module under both tiers cannot import this one.

Architecture:
  - No Flask imports.
  - All monetary arithmetic uses Decimal.
"""

from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.utils.entry_partition import partition_entries
from app.utils.money import percent_complete


def compute_entry_sums(
    entries: list[TransactionEntry],
) -> tuple[Decimal, Decimal]:
    """Compute (sum_debit, sum_credit) from a list of entries.

    Pure function -- no database access.

    Args:
        entries: List of TransactionEntry objects.

    Returns:
        Tuple of (sum_debit, sum_credit) as Decimals.
    """
    debit_entries, credit_entries = partition_entries(entries)
    sum_debit = sum((e.amount for e in debit_entries), Decimal("0"))
    sum_credit = sum((e.amount for e in credit_entries), Decimal("0"))
    return sum_debit, sum_credit


def build_entry_sums_dict(
    transactions: list,
    budgets: dict[int, Decimal],
) -> dict[int, dict]:
    """Build a {txn_id: sums_dict} mapping for transactions with entries.

    Used by grid routes and HTMX cell-render endpoints to pre-compute
    entry aggregates for the cell template.  Only transactions with
    non-empty entries are included in the result.

    The dict carries ``budget``, ``remaining`` and ``over_budget`` so the
    grid cell template renders without inline Jinja arithmetic
    (E-16 / MED-04).  ``remaining`` is computed via
    :func:`compute_remaining` (the E-21 declared base minus the sum of all
    entries), so the cell's over-budget styling is driven by the same single
    rule that the dashboard bill row sees via ``bill.entry_remaining``.

    **The base arrives as an ARGUMENT** (plan step X-au-c2b).  It was read here
    as ``txn.estimated_amount``, the COLUMN -- and under the amount model a
    derived row stores none, so that read would meet a ``None`` in a
    subtraction on a live screen at the first per-kind cutover.  The caller
    resolves its whole row set ONCE through
    :func:`~app.services.cash_ledger.amounts_by_id` and hands the map down, so
    the cell's numerator, its denominator and the balance row beside it are one
    figure rather than three reads that agree by luck.

    Pure function -- no database access beyond what was already loaded
    on the Transaction objects (expects entries to be accessible, either
    via eager load or lazy access).

    Args:
        transactions: List of Transaction objects with entries accessible.
        budgets: ``{transaction_id: Decimal}`` covering every row in
            *transactions*, from the caller's one
            :func:`~app.services.cash_ledger.amounts_by_id` call.  Indexed with
            ``[]``: a row missing from it is a caller that priced a different
            set, and a default here would be a fabricated budget.

    Returns:
        dict mapping transaction ID to {"debit": Decimal, "credit": Decimal,
        "total": Decimal, "count": int, "budget": Decimal,
        "remaining": Decimal, "over_budget": bool, "pct": Decimal}.  Empty dict
        if no transactions have entries.  ``pct`` is the entries-to-estimate ratio clamped
        to [0, 100] via :func:`pct_complete`; it drives the mobile
        progress-bar's ``data-progress-pct`` attribute on the unified
        ``render_row_card`` macro per mobile-first v3 plan Commit 13.
    """
    result: dict[int, dict] = {}
    for txn in transactions:
        if txn.entries:
            debit, credit = compute_entry_sums(txn.entries)
            total = debit + credit
            budget = budgets[txn.id]
            remaining = compute_remaining(budget, txn.entries)
            result[txn.id] = {
                "debit": debit,
                "credit": credit,
                "total": total,
                "count": len(txn.entries),
                "budget": budget,
                "remaining": remaining,
                "over_budget": remaining < Decimal("0"),
                "pct": pct_complete(total, budget),
            }
    return result


def build_entry_lists_dict(
    transactions: list,
    budgets: dict[int, Decimal],
) -> dict[int, dict]:
    """Build a {txn_id: entry_list_data} mapping for envelope transactions.

    Pre-computes the entry-list rendering inputs that
    ``_render_entry_list`` in ``app/routes/entries.py`` produces per
    HTMX request, so the mobile grid macro can render entries inline
    on the initial grid response instead of lazy-loading them one
    request per envelope card.  With 6 visible pay periods and ~10
    envelope templates each, the lazy-load fan-out is ~60 parallel
    GETs on the entries endpoint, which exceeds the
    ``RATELIMIT_DEFAULT`` ceiling of ``30 per minute`` and leaves the
    over-limit cards stuck on the loading spinner.  Server-side
    rendering eliminates the fan-out entirely.

    Only purchase-tracking rows (``txn.tracks_purchases`` -- a template
    with ``is_envelope`` set, or an ad-hoc row carrying its own
    ``is_envelope`` flag) get an entry, matching the macro's guard for
    whether to render the inline entries section.  Non-tracking
    transactions are silently skipped.

    Expects ``entries`` and ``template`` eager-loaded on the
    Transaction objects.  **It is PURE again at plan step X-f3b**: it stopped
    being so on 2026-08-13, when the indicator asked each account's clearing
    rule and so paid one indexed read per distinct account.  Ruling **R-FM**
    made the indicator a question about the PURCHASE -- has its bank posting day
    been recorded -- so the read is gone with the question, and a grid render
    issues no query here at all.

    Args:
        transactions: List of Transaction objects with ``entries`` and
            ``template`` accessible.
        budgets: ``{transaction_id: Decimal}`` covering every row in
            *transactions* (see :func:`build_entry_sums_dict`), forwarded to
            :func:`entry_list_view`.

    Returns:
        dict mapping envelope transaction ID to one
        :func:`entry_list_view` -- the WHOLE context
        ``grid/_transaction_entries.html`` renders an envelope from.

        Empty dict when no transaction in the input has an envelope
        template.
    """
    return {
        txn.id: entry_list_view(txn, list(txn.entries), budgets[txn.id])
        for txn in transactions
        if txn.tracks_purchases
    }


def entry_list_view(
    txn: Transaction, entries: list[TransactionEntry], budget: Decimal,
) -> dict:
    """Return the WHOLE derived context one envelope's entry list renders from.

    **The ONE producer of that context, and it exists because a caller-built
    one was measured wrong.**  ``grid/_transaction_entries.html`` was assembled
    independently by the HTMX refresh (``routes/entries.py``) and by the grid
    macro (via :func:`build_entry_lists_dict`), and only the refresh supplied
    the indicator's key -- so on every INITIAL render of the grid, the mobile
    card, the companion view and the full-edit popover, the template asked a
    Jinja ``Undefined``, which answers ``False`` silently.  Every already-posted
    purchase read *"Not yet seen on a statement, so the budget is still held
    back"* while the projection had already released its reservation: the screen
    contradicted the number beside it, on 9 of 9 such purchases on the
    2026-08-13 production clone (`$640.70`).

    Two callers assembling one template's context is the shape; a forgotten key
    was the instance.  Returning the whole context from one place makes the
    omission unrepresentable at the service tier -- the route splats this
    mapping rather than naming its keys.

    **The indicator asks the PURCHASE since plan step X-f3b, not the account**
    (ruling **R-FM**).  It asked
    :meth:`~app.services.cash_ledger.StatementCoverage.is_cleared`, so the
    tooltip had a THIRD state -- a purchase whose posting day is recorded but
    which no assertion covers yet -- captioned *"the budget is still held
    back"*.  That sentence is now false.  The screen therefore asks the question
    the reservation asks, has this purchase posted, and the two cannot disagree
    because one fact is behind both.  It also drops a ``coverage_for`` read per
    rendered ACCOUNT from every grid render.

    Args:
        txn: The envelope transaction being rendered.  Its pay period bounds
            the out-of-period warning.
        budget: What the row's amount RESOLVES to -- the E-21 declared base the
            remaining figure is computed against
            (:func:`~app.services.cash_ledger.amounts_by_id`).  An argument
            rather than a read of ``txn.estimated_amount`` since plan step
            X-au-c2b: a derived row stores no figure in that column.
        entries: The transaction's entries, already loaded and ordered by
            ``purchased_on``.  Taken as an argument rather than read off *txn*
            because the two callers load them differently -- the route through
            the owner-scoped :func:`get_entries_for_transaction`, the grid off
            an eager-loaded relationship -- and neither may lose its scoping to
            share this derivation.

    Returns:
        The four keys the template consumes:

          - ``entries``: the list as given.
          - ``remaining`` (Decimal): the row's resolved budget minus the sum
            of all entries (debit + credit), via :func:`compute_remaining`.
          - ``out_of_period_ids`` (set[int]): entry IDs whose ``purchased_on``
            falls outside the parent pay period, surfacing the OP-4
            date-awareness warning.
          - ``posted_ids`` (set[int]): entry IDs whose bank posting day is
            recorded, so their money has left the account and their envelope is
            no longer holding their budget back.  It is the SAME fact the
            reservation buckets on
            (``cash_ledger._amounts._entry_checking_impact``), decided HERE in
            Python; the template renders the answer and never re-derives it.
    """
    return {
        "entries": entries,
        "remaining": compute_remaining(budget, entries),
        "out_of_period_ids": {
            e.id for e in entries
            if not check_purchase_date_in_period(e.purchased_on, txn)
        },
        "posted_ids": {
            e.id for e in entries if e.settled_on is not None
        },
    }


def compute_remaining(
    budget: Decimal,
    entries: list[TransactionEntry],
) -> Decimal:
    """Compute remaining budget: the declared base minus the sum of ALL entries.

    Uses the sum of ALL entries regardless of payment method (debit +
    credit) because the remaining balance represents budget consumption,
    not checking impact.  Negative values indicate overspending.

    Per E-21 (audit MED-03 / F-028 / F-056) the budget base for an
    entry-tracked bill row is the row's own AMOUNT unconditionally --
    never ``actual_amount`` and never status-dependent.  This is why
    the signature takes that base directly rather than the whole
    ``Transaction``: the base cannot be switched on at runtime;
    callers that want to display "remaining" against a different base
    are out of contract and must compute it themselves.

    **The parameter was named ``estimated_amount`` until plan step X-au-c2b**,
    and renaming it is the point rather than tidying: a parameter named after a
    COLUMN invites the next caller to pass that column, and under the amount
    model a derived row's is ``NULL``.  Every caller now passes what the row's
    amount RESOLVES to (:func:`~app.services.cash_ledger.amounts_by_id`), so
    the row's amount cell, its remaining and its over-budget flag share one
    declared base by construction.

    Pure function -- no database access.

    Args:
        budget: The transaction's resolved amount -- the E-21 declared base for
            the row's plan-vs-actual figures.
        entries: List of TransactionEntry objects.

    Returns:
        Decimal -- the remaining budget (negative means overspent).
    """
    total_spent = sum((e.amount for e in entries), Decimal("0"))
    return budget - total_spent


def pct_complete(total: Decimal, target: Decimal) -> Decimal:
    """Compute percent complete, clamped to [0, 100].

    Feeds the entry-tracking progress-bar widths on the companion
    transaction card (and any other surface that needs an entry
    aggregate as a percentage of its declared budget base).  Returns a
    Decimal so money math never crosses the Decimal/float boundary at
    the route layer (MED-04 / E-16): the companion route used to
    ``float(total / estimated * Decimal("100"))`` inline, which violated
    the "money math is service-layer Decimal, not route-layer float"
    standard.  Thin domain-named wrapper over
    :func:`app.utils.money.percent_complete` -- the single numeric
    contract the dashboard and companion progress surfaces both share.

    The two-decimal-place result is safe to render as-is in CSS width
    values: ``data-progress-pct="55.50"`` is parsed by
    ``app/static/js/progress_bar.js`` via ``parseFloat`` before being
    applied as an inline width, and CSS itself accepts the decimal
    notation in ``%`` values.

    Args:
        total: Sum of entries against the budgeted line.
        target: Budgeted estimated amount.  If <= 0 the function
            returns ``Decimal("0")`` rather than dividing by zero or
            producing a misleading negative percentage.

    Returns:
        Decimal in [0, 100] quantised to two decimal places when the
        guard does not fire; ``Decimal("0")`` when ``target <= 0``.
    """
    return percent_complete(total, target)


def check_purchase_date_in_period(
    purchased_on: date,
    transaction: Transaction,
) -> bool:
    """Check whether a purchase's date falls within the pay period range.

    Informational utility for UI warnings (OP-4).  Does NOT block
    entry creation or updates -- late-posting purchases may
    legitimately fall outside the period range.

    It reads ``purchased_on`` and not ``settled_on``, and that is the
    distinction the split exists for: this warning asks "is this purchase
    budgeted to the right pay period", which is a BUDGET-clock question.  When
    the money reached the bank is a cash-clock fact and belongs to the balance
    fold, not to a budgeting warning.

    Args:
        purchased_on: The day the purchase was made.
        transaction: The parent Transaction (with pay_period loaded).

    Returns:
        True if *purchased_on* is within [start_date, end_date], False
        otherwise.
    """
    period = transaction.pay_period
    return period.start_date <= purchased_on <= period.end_date
