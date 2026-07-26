"""
Shekel Budget App -- Cash ledger: what a SET of rows SUMS TO (flows, not stocks).

A flow, not a stock: these answer "how much moved through this account during
period P", where a balance answers "what is held at time T".  That distinction
is why this layer is not part of the :mod:`app.services.balance_at` seam -- a
subtotal is a peer reduction over the same transaction rows the balance folds,
not a step on the way to a balance.

Two levels, one rule:

  * :func:`sum_projected` -- the Projected-only (income, expense) reduction over
    an already-loaded set of rows, valuing each through :mod:`._amounts`.  This
    is the shared engine.
  * :func:`period_subtotal` / :func:`period_subtotals` -- the same reduction per
    pay period, loading the rows itself and rounding ``net`` once at the
    boundary.

The two questions -- flow and stock -- are bound by an invariant, and keeping
one row-valuation engine is how it is kept.  The invariant itself MOVED at
plan step X-c2b2 (ruling R-K): it used to be

    balances[p] - balances[p-1] == period_subtotals(...)[p].net

which held only because both sides counted exactly the still-UNPAID rows of
one anchor-seeded walk -- neither could see a settled row at all, so every
past column read ``$0.00`` while thousands of dollars moved through it
(finding N-41).  The subtotals now count EVERY row attributed to a period and
the balance counts money that MOVED, so the identity gained a named remainder
and lives with the producer that computes both sides from one row set:
:class:`app.services.balance_at._cash_fold.CashPeriodFigures`.

What survives here is the half that made the old identity hold and still makes
the new one hold: :func:`sum_projected` is the ONE per-row valuation both
groupings reduce through -- the same entries-aware expense reduction, the same
live override map -- and ``net`` is rounded ONCE at the boundary
(``round_money(income - expense)``) rather than as the difference of two
separately-rounded legs.  Two producers that agreed only by coincidence is
what F-002 Pair C / F-004 were, and E-25 restored.

``period_subtotal`` / ``period_subtotals`` / :class:`PeriodSubtotal` have had
no production consumer since plan step X-c2b1 (the grid reads the seam's
per-period view) and are deleted at X-c2b3; ``sum_projected`` stays.

**Why the engine lives HERE (plan step D1c).**  ``sum_projected`` sat inside
``balance_calculator`` -- a PRODUCER module -- and was called from outside the
balance cluster, which is what made that module unmovable (finding N-30).  It
is an explicitly-ruled NON-producer and it is a reduction over rows, so it
belongs with the reduction it powers rather than with the balance walk that
consumes it.  The arrow now runs one way: the producers import this, and this
imports none of them.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclass out; no Flask import.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.balance_predicates import is_projected
from app.utils.money import round_money

from ._amounts import _expense_amount, income_amount, live_amount_overrides
from ._facts import load_balance_transactions


def sum_projected(transactions, amount_overrides=None, *, as_of=None):
    """Sum projected (unsettled) income and expenses for one pay period.

    Part of this module's public surface (no leading underscore): the
    canonical cash producer ``balance_calculator.calculate_balances`` and the
    per-period reduction below both call it, so the projected-sum rule lives in
    exactly one place rather than being re-implemented per surface.

    Only Projected items contribute to the projected balance: settled
    (done / received), credit, and cancelled transactions are excluded
    via the centralized ``is_projected`` predicate (D6-09 / MED-02), so
    this filter shares one definition with
    :func:`~app.services.cash_ledger._amounts._entry_aware_amount` and
    the balance resolver's date-cut path.

    The same Projected-only sum applies to every period the balance walk
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

    **The as-of window is a parameter here too (plan step D1c).**
    ``balance_resolver`` carried a private ``_sum_period_as_of`` whose own
    docstring said it "mirrors ``sum_projected``" and differed in exactly one
    expression -- the expense valuation.  Two loops applying the same
    Projected gate to the same rows, kept in step by hand, is the
    agreeing-by-coincidence shape the balance arc exists to end; the date cut
    is DATA, so it belongs in a parameter rather than in a second copy.  It is
    a date rather than an injected valuation function deliberately: an
    argument a caller can get wrong is a defect, not a contract, and a wrong
    function here would silently ship a wrong balance.

    Args:
        transactions: Transaction objects for a single pay period.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (Workstream B); None preserves the
            stored-amount behavior byte-identical.
        as_of: Optional calendar date bounding ENTRY inclusion inside the
            expense reduction (E-27 / HIGH-02), forwarded verbatim to
            :func:`~app.services.cash_ledger._amounts._expense_amount`.
            ``None`` (the default) counts every loaded entry, which is what
            the period-boundary balance walk wants; a date is what the
            calendar surfaces pass so a purchase dated after it does not
            reduce the reservation early.  Transactions themselves are NEVER
            filtered by it -- the date-sensitivity lives in the per-entry
            reduction, not in row inclusion.  Income carries no entries, so
            the bound is a no-op on that leg either way.

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
            expenses += _expense_amount(txn, amount_overrides, as_of)

    return income, expenses


@dataclass(frozen=True)
class PeriodSubtotal:
    """Immutable producer output for one period's entries-aware subtotal.

    Returned by :func:`period_subtotal`.  ``income`` and ``expense``
    use the same entries-aware reduction the balance calculator
    applies; ``net`` is the combined-rounded period delta
    (``round_money(income - expense)``), so by construction
    ``balances[p] - balances[p-1] == period_subtotal(..., p).net`` --
    the same-page same-formula property F-002 Pair C / F-004 break
    and E-25 restore.

    Attributes:
        income: Sum of Projected income transactions in the period.
            Income uses :attr:`Transaction.effective_amount` (entries
            do not apply to income -- they live on expenses only).
        expense: Sum of Projected expense transactions, each reduced
            by the entries-aware formula
            ``max(estimated - cleared_debit - sum_credit,
            uncleared_debit)`` when the transaction carries entries.
            For an expense with no entries this collapses to
            ``effective_amount``, which matches the no-entries
            consumer behavior pre-Commit-5 (regression-safe for
            grid/dashboard whose pinned tests stay byte-identical).
        net: ``round_money(income - expense)`` -- the period delta
            rounded once at the boundary (NOT the difference of the two
            separately-rounded legs), so it equals the balance
            roll-forward's once-rounded period delta and the E-25
            reconciliation ``balances[p] - balances[p-1] == net`` holds
            by construction (see :func:`period_subtotal`).  Returned
            pre-computed so a consumer never has to re-derive it (and
            risk a divergent sign or rounding mode).  Equals
            ``income - expense`` exactly on all current data (every leg
            is cent-quantized); only a hypothetical sub-cent leg would
            make it differ from the displayed legs' difference by a
            cent, with ``net`` being the balance-reconciling value.
    """

    income: Decimal
    expense: Decimal
    net: Decimal


def _subtotal_from_transactions(
    transactions: list[Transaction],
    amount_overrides: dict[int, Decimal],
) -> PeriodSubtotal:
    """Income / expense / net subtotal for one period's loaded txns.

    The shared per-period core of :func:`period_subtotals` (and thus
    :func:`period_subtotal`, which delegates to it).  Delegates to
    :func:`sum_projected` above (Projected-only, entry-aware expense
    reduction, ``effective_amount`` for income) and rounds ``net`` as ONE
    combined ``round_money(income - expense)`` -- the once-at-the-boundary
    discipline that makes ``net`` reconcile with the balance
    roll-forward (DH-#62 / Batch V; rationale on :class:`PeriodSubtotal`).

    ``transactions`` is one ``pay_period_id``'s balance-contributing
    rows (``entries`` eager-loaded); an empty list yields a zero
    subtotal.  Only the ``amount_overrides`` keys for ``transactions``
    are read, so a map built over a wider set (the batch case) is
    equivalent to a per-period one.

    Args:
        transactions: One period's balance-contributing rows.
        amount_overrides: The ``{transaction_id: Decimal}`` live map.

    Returns:
        The period's :class:`PeriodSubtotal`.
    """
    # The audit's E-25 mandate reuses the shared projected-sum engine beside
    # this rather than rewriting it (CLAUDE.md rule 10); the balance walk
    # calls the SAME function, which is what makes the delta invariant hold.
    income, expense = sum_projected(transactions, amount_overrides)
    return PeriodSubtotal(
        income=round_money(income),
        expense=round_money(expense),
        net=round_money(income - expense),
    )


def period_subtotal(
    account: Account,
    scenario_id: int,
    period: PayPeriod,
    *,
    amount_overrides: dict[int, Decimal] | None = None,
) -> PeriodSubtotal:
    """Entries-aware income / expense / net subtotal for one period (E-25).

    The single source of truth for "what is the projected net change
    in checking for this period" -- the grid footer, obligations
    summary, and any per-period roll-up consume this so the same
    entries-aware formula generates both the subtotal and the balance
    row (closing the F-002 Pair C / F-004 divergence).  ``net`` is the
    combined-rounded delta, so ``balances[p] - balances[p-1] ==
    period_subtotal(..., p).net`` by construction (rounding rationale on
    :class:`PeriodSubtotal`).

    A thin single-period adapter over :func:`period_subtotals` (returns
    its :class:`PeriodSubtotal` for ``period``); to subtotal MANY
    periods (the grid footer) call that directly -- it issues ONE
    transaction load for the whole window, not one per period.
    ``amount_overrides`` is the optional Workstream-B live projected-net
    ``{transaction_id: Decimal}`` map, built when None so income
    reflects the live paycheck consistently with the balance row.

    Args:
        account: The :class:`~app.models.account.Account`.
        scenario_id: The scenario id.
        period: The pay period to subtotal.
        amount_overrides: Optional live ``{transaction_id: Decimal}`` map.

    Returns:
        The period's :class:`PeriodSubtotal`.
    """
    return period_subtotals(
        account, scenario_id, [period], amount_overrides=amount_overrides,
    )[period.id]


def period_subtotals(
    account: Account,
    scenario_id: int,
    periods: list[PayPeriod],
    *,
    amount_overrides: dict[int, Decimal] | None = None,
) -> dict[int, PeriodSubtotal]:
    """Batch entries-aware subtotal -- one query for the whole window.

    The canonical multi-period producer (and the implementation
    :func:`period_subtotal` delegates to).  Issues a SINGLE
    :func:`~app.services.cash_ledger._facts.load_balance_transactions` over all
    ``periods`` then groups the rows by ``pay_period_id``, instead of one
    SELECT per period.
    This is what the grid footer consumes: the pre-existing per-period
    loop was an N+1 (one transaction query per visible column, over a
    set the page had already loaded twice) -- exactly the N+1 the
    ``database.md`` rule calls out for the grid route "especially"
    (DH-#36).

    Byte-identical to per-period :func:`_subtotal_from_transactions`
    calls: the grouping reproduces the single-period filter exactly,
    and the override map is read per transaction (so a union-set build
    equals per-period maps -- the build-once-and-thread property
    ``balances_for`` relies on), so the E-25 balance-delta
    invariant ``balances[p] - balances[p-1] == ...[p].net`` holds for
    every period.

    Args:
        account: The :class:`~app.models.account.Account`.  Must be
            attached to ``db.session``.
        scenario_id: The scenario id.
        periods: The pay periods to subtotal.  A period with no
            contributing transactions maps to a zero subtotal.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (Workstream B); built ONCE here over the
            whole loaded set when None, so the income line reflects the
            live paycheck consistently with the balance row.

    Returns:
        ``dict`` mapping each ``period.id`` to its
        :class:`PeriodSubtotal`.  Every input period is present as a
        key (a zero subtotal when it has no contributing transactions).
    """
    transactions = load_balance_transactions(
        account, scenario_id, [period.id for period in periods],
    )
    # Build the live override map ONCE over the union set (the same
    # build-once-and-thread pattern ``balances_for`` uses); each period's
    # subtotal reads only the keys for its own transactions, so a union
    # map is equivalent to per-period maps.
    if amount_overrides is None:
        amount_overrides = live_amount_overrides(
            account, scenario_id, transactions,
        )
    grouped: dict[int, list[Transaction]] = {}
    for txn in transactions:
        grouped.setdefault(txn.pay_period_id, []).append(txn)
    return {
        period.id: _subtotal_from_transactions(
            grouped.get(period.id, []), amount_overrides,
        )
        for period in periods
    }
