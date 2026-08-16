"""
Shekel Budget App -- Shared Transaction-Cell Render Helper

The cross-blueprint home for rendering a transaction's grid cell.
Every HTMX response that re-renders a cell -- the transaction CRUD and
status routes, and the entries CRUD routes' out-of-band cell refresh --
must ship the same context (notably ``entry_sums`` and ``budgets``, which drive
the amount display and the envelope progress), so the render has exactly one
definition with a public name instead of a module-private helper imported across
blueprint packages.  Follows the package-level shared-helper convention
of ``app/routes/_commit_helpers.py``.
"""

from decimal import Decimal
from typing import Any

from flask import render_template

from app.models.transaction import Transaction
from app.services.cash_ledger import amount_basis, amounts_by_id
from app.services.entry_service import build_entry_sums_dict


def fragment_budgets(*rows: Transaction) -> dict[int, Decimal]:
    """Return ``{transaction_id: resolved amount}`` for ONE fragment's rows.

    The single-row door onto the amount model (plan step X-au-c2b), for the HTMX
    fragments that re-render one cell or one card.  Every template that shows a
    row's amount reads this map rather than ``txn.estimated_amount``, because
    under the amount model a derived row stores nothing in that column and the
    cell would render an empty string where a figure belongs.

    **The pins come off the first row, and that is safe HERE and nowhere else**:
    a fragment renders rows the request already scoped to one owner and one
    scenario, so the basis it builds is theirs.  A cross-owner or cross-scenario
    set would price rows against the wrong salary profile and the wrong loan --
    which is why the batch surfaces (the grid, the companion view, the pulse)
    take the READ PASS's basis
    (:meth:`~app.services.balance_at.BalanceContext.amounts`) instead of calling
    this, and why this one is named for the fragment it serves.

    Args:
        rows: The rows the fragment renders.  All must belong to one owner and
            one scenario.

    Returns:
        ``{transaction_id: Decimal}`` covering every row; ``{}`` for none.
    """
    if not rows:
        return {}
    first = rows[0]
    return amounts_by_id(
        rows, amount_basis(first.account.user_id, first.scenario_id),
    )


def render_transaction_cell(txn: Transaction, **extra: Any) -> str:
    """Render the transaction cell template with its amount and entry context.

    Wraps render_template so every HTMX cell response includes the ``budgets``
    map the amount display reads and the ``entry_sums`` dict the progress
    indicator on tracked transactions needs.

    Args:
        txn: The Transaction object to render.
        **extra: Additional keyword arguments forwarded to
            render_template (e.g. ``wrap_div=True``, ``wrap_oob=True``,
            ``conflict=True``).

    Returns:
        Rendered HTML string.
    """
    budgets = fragment_budgets(txn)
    return render_template(
        "grid/_transaction_cell.html",
        txn=txn,
        budgets=budgets,
        entry_sums=build_entry_sums_dict([txn], budgets),
        **extra,
    )
