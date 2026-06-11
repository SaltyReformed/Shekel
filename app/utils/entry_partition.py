"""
Shekel Budget App -- Transaction-entry partition helper

Single source of the "which entries are credit-card purchases vs direct
debits" rule.  A transaction's individual purchase entries split into two
groups by the ``is_credit`` flag: credit entries flow through a separate
CC Payback transaction, while debit entries hit checking directly.

Two services need this same split but cannot share it through
``entry_service`` (the natural home of the other entry-aggregation
primitives) because of an import cycle: ``entry_service`` imports
``entry_credit_workflow.sync_entry_payback``, so the credit workflow
cannot import back from ``entry_service``.  This leaf util -- importing
only the model -- is reachable from both without a cycle, so the
partition predicate lives in exactly one place rather than being
hand-reproduced in each consumer (DH-#75).
"""

from app.models.transaction_entry import TransactionEntry


def partition_entries(
    entries: list[TransactionEntry],
) -> tuple[list[TransactionEntry], list[TransactionEntry]]:
    """Split entries into ``(debit_entries, credit_entries)`` by ``is_credit``.

    Pure function -- no database access.  The single definition of the
    credit-vs-debit partition consumed by both
    :func:`app.services.entry_service.compute_entry_sums` (which sums each
    group) and :func:`app.services.entry_credit_workflow.sync_entry_payback`
    (which needs the credit-entry list itself to size and link the CC
    Payback).  The return order mirrors ``compute_entry_sums``'s
    ``(sum_debit, sum_credit)`` convention.

    Args:
        entries: An iterable of :class:`TransactionEntry` rows, each
            exposing ``is_credit`` (bool).

    Returns:
        A ``(debit_entries, credit_entries)`` tuple of lists; an entry is
        a credit iff ``entry.is_credit`` is true.
    """
    debit_entries: list[TransactionEntry] = []
    credit_entries: list[TransactionEntry] = []
    for entry in entries:
        if entry.is_credit:
            credit_entries.append(entry)
        else:
            debit_entries.append(entry)
    return debit_entries, credit_entries
