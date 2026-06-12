"""
Shekel Budget App -- Shared Transaction-Cell Render Helper

The cross-blueprint home for rendering a transaction's grid cell.
Every HTMX response that re-renders a cell -- the transaction CRUD and
status routes, and the entries CRUD routes' out-of-band cell refresh --
must ship the same context (notably ``entry_sums``, which drives the
envelope progress display), so the render has exactly one definition
with a public name instead of a module-private helper imported across
blueprint packages.  Follows the package-level shared-helper convention
of ``app/routes/_commit_helpers.py``.
"""

from typing import Any

from flask import render_template

from app.models.transaction import Transaction
from app.services.entry_service import build_entry_sums_dict


def render_transaction_cell(txn: Transaction, **extra: Any) -> str:
    """Render the transaction cell template with entry_sums context.

    Wraps render_template so every HTMX cell response includes the
    entry_sums dict needed for the progress indicator on tracked
    transactions.

    Args:
        txn: The Transaction object to render.
        **extra: Additional keyword arguments forwarded to
            render_template (e.g. ``wrap_div=True``, ``wrap_oob=True``,
            ``conflict=True``).

    Returns:
        Rendered HTML string.
    """
    return render_template(
        "grid/_transaction_cell.html",
        txn=txn,
        entry_sums=build_entry_sums_dict([txn]),
        **extra,
    )
