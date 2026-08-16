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
from app.services.cash_ledger import amount_basis, display_amounts_by_id
from app.services.entry_service import build_entry_sums_dict


def fragment_budgets(txn: Transaction) -> dict[int, Decimal]:
    """Return ``{transaction_id: resolved amount}`` for ONE fragment's rows.

    The single-row door onto the amount model (plan step X-au-c2b), for the HTMX
    fragments that re-render one cell or one card.  Every template that shows a
    row's amount reads this map rather than ``txn.estimated_amount``, because
    under the amount model a derived row stores nothing in that column and the
    cell would render an empty string where a figure belongs.

    **It answers by the same rule the pages do** --
    :func:`~app.services.cash_ledger.display_amounts_by_id`, the resolved amount
    superseded by a live recompute.  An adversarial review found that rule
    written twice and differently: the grid merged the seam's override map over
    its resolved one while every fragment published the resolved map ALONE under
    the same context key, so a drifted salary row showed its live net on the
    grid and its stale column in the quick-edit box the same click opened -- and
    that box is what a save posts back from.

    **It takes ONE row, and the signature is the guard.**  It took ``*rows`` and
    pinned the basis off ``rows[0]``, which needed a paragraph about cross-owner
    sets to be safe; every fragment renders exactly one row, so taking one row
    deletes the question rather than documenting it.  A batch surface takes the
    READ PASS's basis instead
    (:meth:`~app.services.balance_at.BalanceContext.amounts`).

    Args:
        txn: The row the fragment renders.

    Returns:
        ``{transaction_id: Decimal}`` -- one entry, keyed for the templates and
        the entry builders, which all index a map.
    """
    return display_amounts_by_id(
        [txn], amount_basis(txn.account.user_id, txn.scenario_id),
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
