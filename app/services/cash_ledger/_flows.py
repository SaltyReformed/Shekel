"""
Shekel Budget App -- Cash ledger: what a SET of rows SUMS TO (flows, not stocks).

A flow, not a stock: this answers "how much moved through this account", where a
balance answers "what is held at time T".  That distinction is why this layer is
not part of the :mod:`app.services.balance_at` seam -- a sum is a peer reduction
over the same transaction rows the balance folds, not a step on the way to a
balance.

ONE function since plan step X-c2b3: :func:`sum_projected`, the Projected-only
``(income, expense)`` reduction over an already-loaded row set, valuing each row
through :mod:`._amounts`.  Since plan step X-g4b there is ONE consumer, the
seam's cash fold (``balance_at._cash_fold``) -- the anchor-forward walk that was
the second was deleted with the producers it served.  It stays here rather than
moving inside the seam for the reason this module exists at all: a SUM over rows
is a flow, not a stock, and the rule for what one row contributes is not a
balance rule.  Its home is also what keeps ONE entries-aware expense rule and
ONE live-override basis available to the next reader that needs a flow.

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
:class:`app.services.balance_at._cash_periods.CashPeriodFigures`.  That makes it
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

from ._amounts import ProjectedBasis, _expense_amount, income_amount


def sum_projected(transactions, basis: ProjectedBasis):
    """Sum projected (unsettled) income and expenses for one pay period.

    Part of this module's public surface (no leading underscore): the seam's
    cash fold reaches it from another package, so the projected-sum rule lives
    in exactly one place rather than being re-implemented per surface.  It was
    called by two cash bases until plan step X-g4b deleted the anchor-forward
    walk; one caller does not make it private, because the caller is across a
    package boundary and the rule is a FLOW rather than a balance.

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
    settled yet.  Either way only the projected remainder is summed.  WHERE a
    sum lands -- which day, on which running total -- has never lived here, and
    since plan step X-g4b there is no anchor-vs-roll-forward branch anywhere:
    the fold has one running total and this reduction values a day's group of
    rows on it (collapsed from the historically-separate ``_sum_remaining`` /
    ``_sum_all`` once both became Projected-only).

    Income uses :func:`~app.services.cash_ledger._amounts.income_amount`
    (effective_amount, or a live override when present).  Expenses use
    :func:`~app.services.cash_ledger._amounts._expense_amount`, which
    applies the entry-checking formula for projected expenses with loaded
    entries and honors a live override, falling back to effective_amount
    otherwise.

    **The basis is REQUIRED, and that is the point of bundling it** (plan step
    S1-c).  It used to be one optional ``amount_overrides`` map defaulting to
    ``None``; the entry reservation now also needs the day through which the
    account's purchases are reconciled, and two optional arguments is two ways
    for a caller to hand this reduction half a basis -- which would silently
    value every purchase as outstanding and hold whole budgets back.  One
    required record, built once per account by the reader that owns the walk,
    makes that shape unwritable.

    **It takes no date (plan step X-c2c1).**  D1c had unified two loops --
    ``balance_resolver``'s private ``_sum_period_as_of``, whose own docstring
    said it "mirrors ``sum_projected``" and differed in exactly one expression
    -- into one reduction with an optional ``as_of`` bounding ENTRY inclusion
    inside the expense leg (E-27 / HIGH-02).  Ruling R-M then closed the fork
    that bound existed for at the SOURCE (plan step X-c0 refuses a future
    PURCHASE date at both write doors), so it provably dropped nothing and
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
        basis: The account's
            :class:`~app.services.cash_ledger._amounts.ProjectedBasis` -- the
            live override map and the day through which its purchases are
            reconciled, built once per account by the caller that walked it.

    Returns:
        (total_income, total_expenses) as a Decimal tuple.
    """
    income = Decimal("0.00")
    expenses = Decimal("0.00")

    for txn in transactions:
        if not is_projected(txn):
            continue

        if txn.is_income:
            income += income_amount(txn, basis)
        elif txn.is_expense:
            expenses += _expense_amount(txn, basis)

    return income, expenses
