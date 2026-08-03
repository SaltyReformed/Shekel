"""Shared read primitives over a loan's linked-ledger postings.

The low-level queries the attribution reads in :mod:`._reader` and the sync's
E1a checks in :mod:`._sync` are built on:

* :func:`_has_opening_posting` -- the "is this loan configured in this scenario"
  sentinel a posting read guards on before it trusts a ``$0.00`` (an
  unconfigured loan answers ``None``, not a misleading zero).
* :func:`_transfer_nets_by_date` -- the per-``(transfer, entry_date)`` nets the
  sync's lineage staleness probe compares against each settled payment's
  expected settle date and cash (step E1a).

**The checked-projection assert's posted side left this module at plan step
X-d.**  It was ``_visible_nets`` here, and the cash anchor sync's assert needed
the identical query over the identical table -- so it became
:func:`app.services.posting_reads.linked_ledger_nets_by_date`, beside the
chart-of-accounts lookup both posting packages already share, rather than a
second copy of the query whose whole job is to GRADE a ledger.  The private
query core the two loads here used to share went with it: with one grouped load
left in this module, a builder taking group columns and filter lists was
indirection around a single call site.

Scoped throughout to a loan's LINKED ledger (its resolved per-loan liability
account, :func:`app.services.posting_service._ledger_account_for`) and the
budget scenario.

Reads only -- no writes, no commit.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
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


def _transfer_nets_by_date(
    linked_ledger_id: int, scenario_id: int,
) -> dict[int, dict[date, Decimal]]:
    """Return each transfer's non-zero per-date nets on the loan's linked ledger.

    The E1a lineage staleness probe's posted side
    (:func:`._sync._reconcile_lineage_transfer_entries`): one grouped query
    over the linked ledger's TRANSFER-source entries, keyed
    ``{transfer_id: {entry_date: net}}`` with zero-net dates dropped (a
    reconciled reversal pair nets its date to zero, which is the clean state).
    Entries whose ``transfer_id`` is NULL -- a hard-deleted transfer's
    ``SET NULL`` residue -- are excluded: there is no row left to re-sync, so
    any cross-date residue there is an F1-class data item the
    checked-projection assert surfaces rather than something the probe's
    consumer can heal.

    **Date-keyed, deliberately period-blind.**  A right-date / right-amount
    posting filed under the WRONG pay period would read clean here: no app
    flow can produce one (``pay_period_id`` has been posting-relevant and
    reconciled since Step 2), and the fold and the checked-projection assert
    key on dates alone, so the probe vouches for WHEN and HOW MUCH -- never
    for per-period statement attribution.

    Args:
        linked_ledger_id: The loan's linked ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``{transfer_id: {entry_date: non-zero net}}``; a fully-clean reverted
        transfer does not appear (all its dates net zero).
    """
    rows = (
        db.session.query(
            JournalEntry.transfer_id,
            JournalEntry.entry_date,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked_ledger_id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id
            == ref_cache.posting_source_id(PostingSourceEnum.TRANSFER),
            JournalEntry.transfer_id.isnot(None),
        )
        .group_by(JournalEntry.transfer_id, JournalEntry.entry_date)
        .all()
    )
    posted: dict[int, dict[date, Decimal]] = {}
    for transfer_id, entry_date, net in rows:
        if net != 0:
            posted.setdefault(transfer_id, {})[entry_date] = net
    return posted
