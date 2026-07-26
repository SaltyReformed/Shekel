"""
Shekel Budget App -- Cash ledger: what a SET of rows SUMS TO (flows, not stocks).

A flow, not a stock: this answers "how much moved through this account", where a
balance answers "what is held at time T".  That distinction is why this layer is
not part of the :mod:`app.services.balance_at` seam -- a sum is a peer reduction
over the same transaction rows the balance folds, not a step on the way to a
balance.

ONE function since plan step X-c2b3: :func:`sum_projected`, the Projected-only
``(income, expense)`` reduction over an already-loaded row set, valuing each row
through :mod:`._amounts`.  It is the shared engine BOTH cash bases reduce
through -- the seam's fold (``balance_at._cash_fold``) for the still-projected
tier and the retiring anchor-forward walk (``balance_at._calculator``) for the
investment / appreciation bases -- which is what keeps ONE entries-aware expense
rule and ONE live-override basis across them.

**Its per-period siblings are gone, and what they were FOR is why.**
``period_subtotal`` / ``period_subtotals`` / ``PeriodSubtotal`` loaded a window's
rows themselves and reduced them per pay period, so that

    balances[p] - balances[p-1] == period_subtotals(...)[p].net

held by construction.  It held only because both sides counted exactly the
still-UNPAID rows of one anchor-seeded walk -- neither could see a settled row at
all, so every past column read ``$0.00`` while thousands of dollars moved through
it (finding N-41).  Ruling R-K changed what a subtotal COUNTS: the subtotals now
count EVERY row attributed to a period and the balance counts money that MOVED,
so the identity gained a named remainder and lives with the producer that derives
both sides from ONE valued row set,
:class:`app.services.balance_at._cash_fold.CashPeriodFigures`.  That makes it
their SUCCESSOR rather than their peer, which is why they deleted instead of
surviving as a second per-period basis: plan step X-c2b1 took their last
production caller (the grid footer) and X-c2b3 deleted them.  Their
once-at-the-boundary ``round_money(income - expense)`` discipline moved with
them.

What survives here is the half that made the old identity hold and still makes
the new one hold: ONE per-row valuation, so no grouping can price a row
differently from the balance it reconciles against.  Two producers that agreed
only by coincidence is what F-002 Pair C / F-004 were, and E-25 restored.

**Why the engine lives HERE (plan step D1c).**  ``sum_projected`` sat inside
``balance_calculator`` -- a PRODUCER module -- and was called from outside the
balance cluster, which is what made that module unmovable (finding N-30).  It
is an explicitly-ruled NON-producer and it is a reduction over rows, so it
belongs with the reduction it powers rather than with the balance walk that
consumes it.  The arrow now runs one way: the producers import this, and this
imports none of them.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, plain data out; no Flask import.
"""

from decimal import Decimal

from app.utils.balance_predicates import is_projected

from ._amounts import _expense_amount, income_amount


def sum_projected(transactions, amount_overrides=None):
    """Sum projected (unsettled) income and expenses for one pay period.

    Part of this module's public surface (no leading underscore): both cash
    bases call it -- the seam's fold and the retiring anchor-forward walk -- so
    the projected-sum rule lives in exactly one place rather than being
    re-implemented per surface.

    Only Projected items contribute to the projected balance: settled
    (done / received), credit, and cancelled transactions are excluded
    via the centralized ``is_projected`` predicate (D6-09 / MED-02), so
    this filter shares one definition with
    :func:`~app.services.cash_ledger._amounts._entry_aware_amount` and
    the fold's plan tier.

    The same Projected-only sum applies to every period a balance walk
    visits, anchor and post-anchor alike (D6-06): in the anchor period
    the excluded settled items are the ones already reflected in the
    anchor balance the user entered; in post-anchor periods nothing is
    settled yet.  Either way only the projected remainder is summed.  The
    anchor-vs-roll-forward distinction -- which starting balance this sum
    is added to -- lives solely in
    :func:`~app.services.balance_at._calculator.calculate_balances`, not here,
    which is why a single helper serves both branches (collapsed from the
    historically-separate ``_sum_remaining`` / ``_sum_all`` once both
    became Projected-only).

    Income uses :func:`~app.services.cash_ledger._amounts.income_amount`
    (effective_amount, or a live override when present).  Expenses use
    :func:`~app.services.cash_ledger._amounts._expense_amount`, which
    applies the entry-checking formula for projected expenses with loaded
    entries and honors a live override, falling back to effective_amount
    otherwise.

    **It takes no date (plan step X-c2c1).**  D1c had unified two loops --
    ``balance_resolver``'s private ``_sum_period_as_of``, whose own docstring
    said it "mirrors ``sum_projected``" and differed in exactly one expression
    -- into one reduction with an optional ``as_of`` bounding ENTRY inclusion
    inside the expense leg (E-27 / HIGH-02).  Ruling R-M then closed the fork
    that bound existed for at the SOURCE (plan step X-c0 refuses a future
    ``entry_date`` at both write doors), so it provably dropped nothing and
    deleted; the rationale is stated once, at
    :func:`~app.services.cash_ledger._amounts._entry_aware_amount`.  What a row
    is WORTH is now a function of the row, so ruling R-G's clamp in the seam's
    fold is the only place a cash balance consults a date.

    Scoped honestly, because "no clock" would be a stronger claim than the code
    supports: the *override map* this reduction is HANDED can still be built
    from a wall-clock read one package over
    (``loan_payment_service.live_loan_transfer_amounts`` calls ``date.today()``
    for a derive-mode loan-payment shadow -- finding **N-40**, recorded and
    unchanged here).  This function reads no clock; its inputs are not yet
    guaranteed to have been built without one.

    Args:
        transactions: Transaction objects for a single pay period.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (Workstream B); None preserves the
            stored-amount behavior byte-identical.

    Returns:
        (total_income, total_expenses) as a Decimal tuple.
    """
    income = Decimal("0.00")
    expenses = Decimal("0.00")

    for txn in transactions:
        if not is_projected(txn):
            continue

        if txn.is_income:
            income += income_amount(txn, amount_overrides)
        elif txn.is_expense:
            expenses += _expense_amount(txn, amount_overrides)

    return income, expenses
