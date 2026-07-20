"""
Shekel Budget App -- Per-period cash FLOWS (what moved, not what is held).

A flow, not a stock: :func:`period_subtotals` answers "how much moved through
this account during period P", where a balance answers "what is held at time
T".  The distinction is the reason this is its own module and not part of the
balance seam -- a subtotal is a peer reduction over the same transaction rows
the balance folds, not a step on the way to a balance.

The two questions are nonetheless bound by one invariant, and keeping it is
this module's whole point:

    balances[p] - balances[p-1] == period_subtotals(...)[p].net

It holds by construction because both sides reduce the SAME rows through the
SAME engine (:func:`app.services.balance_calculator.sum_projected`) with the
same entries-aware expense reduction and the same live override map, and
because ``net`` is rounded ONCE at the boundary
(``round_money(income - expense)``) rather than as the difference of two
separately-rounded legs.  Two producers that agreed only by coincidence is
what F-002 Pair C / F-004 were, and E-25 restored.

Why its own module (plan step D1a).  These were three of the ten public names
in ``balance_resolver``, which sat at exactly 1000 lines -- pylint's default
module ceiling -- because it held three separable concerns: the event SOURCES
(now :mod:`app.services.cash_events`), these FLOW sums, and the balance
PRODUCERS.  Only the producers belong inside the balance seam; a flow sum
answers no balance-at-T question, so re-exporting it through the seam's public
surface to keep the grid working would have put a name on that surface that is
not the seam's job (``docs/audits/balance_architecture/README.md``, step D1).

Fence status, stated precisely because the two halves differ.  This module is
NOT on the W9906 call allowlist: it composes ``sum_projected`` (an explicit
non-producer) over rows loaded by :mod:`app.services.cash_events` and calls no
balance producer, so W9906 correctly flags it if it ever tries.  It IS scoped
for the W9909 completeness check, so a new public function here must be
classified as a producer or a non-producer rather than defaulting to unguarded.
D1a's adversarial review proved that second half load-bearing: a balance-at-T
map folded from ``resolve_anchor`` + ``period_subtotals`` + ``round_money``
touches no fenced NAME, so without the W9909 scope it -- and a route rendering
it -- both rated 10.00/10.  The dependency arrow runs one way: this module
imports ``cash_events``, and neither imports a producer.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclass out; no Flask import.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import balance_calculator
from app.services.cash_events import (
    live_amount_overrides,
    load_balance_transactions,
)
from app.utils.money import round_money


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
    :func:`period_subtotal`, which delegates to it).  Delegates to the
    engine's :func:`~app.services.balance_calculator.sum_projected`
    (Projected-only, entry-aware expense reduction, ``effective_amount``
    for income) and rounds ``net`` as ONE combined
    ``round_money(income - expense)`` -- the once-at-the-boundary
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
    # This module is ``balance_calculator``'s sibling reducer (see the module
    # docstring); the audit's E-25 mandate reuses the engine's public
    # projected-sum rather than rewriting it (CLAUDE.md rule 10).
    income, expense = balance_calculator.sum_projected(transactions, amount_overrides)
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
    :func:`~app.services.cash_events.load_balance_transactions` over all
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
