"""Shared read primitives over a loan's linked-ledger postings.

Two low-level queries the genesis balance readers in :mod:`._reader` are built
on -- the ``-(sum of the loan's linked postings)`` balance-at-T readers
(:func:`._reader.confirmed_loan_balance_at` and
:func:`._reader.confirmed_loan_balance_map`):

* :func:`_has_opening_posting` -- the "is this loan configured in this scenario"
  sentinel every reader guards on before it trusts a ``$0.00`` (an unconfigured
  loan reads ``None``, not a misleading zero).
* :func:`_visible_nets` -- the one grouped ``(entry_date, net)`` load the
  per-period map prefix-sums into a running balance.

Kept in one module so the readers share a single definition of each rather than
re-issuing the query; scoped throughout to a loan's LINKED ledger (its resolved
per-loan liability account, :func:`app.services.posting_service._ledger_account_for`)
and the budget scenario.

Reads only -- no writes, no commit.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting


def _has_opening_posting(linked_ledger_id: int, scenario_id: int) -> bool:
    """Return whether an OPENING leg is posted on a loan's linked ledger.

    The configured-loan test the ``None`` sentinel rests on.  A loan gets
    exactly one OPENING-kind leg on its linked ledger per scenario -- the
    origination anchor correction, whose ``owed_before`` is zero and whose
    linked leg is ``-original_principal`` (always non-zero for a real loan, so
    always posted; :func:`._anchors._loan_anchor_correction_target`).  Its
    absence means the loan is not configured in this scenario (no
    :class:`~app.models.loan_params.LoanParams`, or a what-if the opening was
    never posted into), which the reader reports as ``None`` -- routing the
    caller to its needs-setup path, never to a misleading ``$0``.

    Scoped to the linked ledger so the opening's OTHER leg (the
    ``+original_principal`` on the per-loan opening-equity account, same kind) is
    not what matches; scoped to the scenario so a loan opened in the baseline
    does not read as configured in a what-if it was never posted into.

    Args:
        linked_ledger_id: The loan's linked ledger account id
            (:func:`app.services.posting_service._ledger_account_for`).
        scenario_id: The budget scenario to scope to.

    Returns:
        ``True`` when an OPENING-kind posting exists on the linked ledger in the
        scenario, else ``False``.
    """
    opening_kind_id = ref_cache.posting_kind_id(PostingKindEnum.OPENING)
    return db.session.query(
        db.session.query(Posting.id)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked_ledger_id,
            Posting.posting_kind_id == opening_kind_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .exists()
    ).scalar()


def _visible_nets(
    linked_ledger_id: int, scenario_id: int,
) -> list[tuple[date, Decimal]]:
    """Return ``(entry_date, net)`` per date, ascending -- the one grouped load.

    Each posting's ``entry_date`` (the day the event it records happened -- step
    C2's one clock) with that date's net movement on the loan's linked ledger.
    :func:`._reader.confirmed_loan_balance_map` prefix-sums it into a running
    confirmed balance at each pay-period boundary from a single query.

    Args:
        linked_ledger_id: The loan's linked ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``[(entry_date, net), ...]`` ascending by date.
    """
    return (
        db.session.query(
            JournalEntry.entry_date,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked_ledger_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .group_by(JournalEntry.entry_date)
        .order_by(JournalEntry.entry_date)
        .all()
    )
