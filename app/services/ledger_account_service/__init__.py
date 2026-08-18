"""
Shekel Budget App -- Ledger Account Service

Sole writer of ``budget.ledger_accounts`` (the chart of accounts for the
double-entry posting ledger).  Every posting leg lands in exactly one ledger
account, so every kind of leg needs a resolver that materialises its chart row
idempotently, stamps the row's ``kind_id`` and ``class_id``, and snapshots its
display ``name``.  This package holds all four of them behind one public
surface.

## Package layout

* :mod:`._common` -- the pieces every resolver shares: the ``name`` column
  width, the natural-key race handler (:func:`._common.add_or_reuse`), and the
  tenancy-filtered account load the two account-linked resolvers guard with.
* :mod:`._linked` -- the ``linked`` Asset/Liability row, one per real
  ``budget.accounts`` row (Build-Order Step 2), plus the class rule
  (:func:`ledger_class_id_for_category`) the account-type boundary guards apply
  to a PROPOSED category, and the re-class an unposted type change needs.
* :mod:`._categories` -- the per-category Income/Expense rows an ordinary
  settled transaction's counter-leg books into, and the per-(owner, class)
  ``Uncategorized`` fallback for a transaction with no category (Step 3).
* :mod:`._loans` -- the four per-loan rows: ``loan_interest``, ``loan_escrow``,
  ``loan_refund`` (Step 4's payment split) and ``equity_opening`` (the loan
  read switch's origination entry).
* :mod:`._counters` -- the per-account rows that hold a non-loan account's
  anchor-correction COUNTER leg (``anchor_equity`` from Step 5,
  ``interest_income`` and ``unrealized_change`` from ruling **R-FO**), and the
  total dispatch that decides WHICH of them a given correction books into.

It was ONE 962-line module until plan step X-f3d, which needed room in it: four
unrelated resolvers had accumulated behind one name, and the split is by the
kind of chart row each one writes.  The public names are unchanged and are
re-exported here, so every consumer keeps reading them off ``ledger_account_service``
-- the chart's one public surface, the same posture ``posting_service`` keeps
over ``posting_reads``.

## What the resolvers guarantee, and what the database does not

As the sole writer, this package stamps every row's explicit ``kind_id``
discriminator (``LedgerAccountKindEnum`` -> ``ref.ledger_account_kinds`` id):
:func:`create_ledger_account_for_account` writes ``linked``,
:func:`get_or_create_category_ledger_account` writes ``fallback`` (the
Uncategorized bucket) or ``category``,
:func:`get_or_create_loan_ledger_account` writes one of the four per-loan kinds,
and :func:`get_or_create_account_counter_account` writes one of the three
per-account counter kinds.
``kind_id`` is the authoritative discriminator readers branch on; no database
CHECK pins it to the row shape (see
:class:`app.models.ledger_account.LedgerAccount`), so stamping it correctly
here -- exactly as this package already stamps ``class_id`` -- is the app's
guarantee that the kind and the column shape agree.  For the per-loan and
per-account rows that guarantee is load-bearing, not belt-and-suspenders: the
shipped ``ck_ledger_accounts_loan_shape`` CHECK is columns-only (a CHECK cannot
subquery ``ref.ledger_account_kinds``), so the resolvers -- which reject any
non-loan kind and any wrongly-classified account before they write -- are the
only thing keeping a ``loan_account_id`` row's kind a loan kind and its target
a real loan.

This package is Flask-isolated per the project architecture rule
(``CLAUDE.md`` Architecture section): it takes plain data, returns plain
SQLAlchemy objects, never imports ``request``/``session``.  The caller owns
the surrounding transaction (no commit inside it).

The go-forward pairing entry point is :func:`create_ledger_account_for_account`,
called from ``account_service.create_account`` immediately after the account is
flushed.  Historical accounts (those created before Step 2) are paired once by
the Commit-2 backfill migration, which reproduces the same mapping in raw SQL.
Both producers leave a linked row's ``name`` NULL -- its display label derives
from the live ``account.name`` (see
:class:`app.models.ledger_account.LedgerAccount`).
"""

from ._categories import get_or_create_category_ledger_account
from ._counters import (
    anchor_correction_counter_kind,
    get_or_create_account_counter_account,
)
from ._linked import (
    create_ledger_account_for_account,
    find_linked_ledger_account,
    ledger_class_id_for_category,
    sync_linked_ledger_class,
)
from ._loans import get_or_create_loan_ledger_account

__all__ = [
    "anchor_correction_counter_kind",
    "create_ledger_account_for_account",
    "find_linked_ledger_account",
    "get_or_create_account_counter_account",
    "get_or_create_category_ledger_account",
    "get_or_create_loan_ledger_account",
    "ledger_class_id_for_category",
    "sync_linked_ledger_class",
]
