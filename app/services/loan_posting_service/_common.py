"""Shared reconcile + loader primitives for the loan posting sub-modules.

The pieces the payment-correction reconcile (:mod:`._payments`), the
anchor-correction reconcile (:mod:`._anchors`), and the loan-global
orchestration (:mod:`._sync`) genuinely share, extracted here so no consumer
re-spells them (a ``duplicate-code`` finding) and all agree exactly:

* :func:`delta_legs` -- turn a ``target`` ledger-leg map and the ``posted``
  ledger-leg map into the balanced DELTA legs that move posted to target.
* :func:`summed_posting_legs` -- the grouped "what is already posted" query
  shape the two posted-leg readers share.
* :func:`loan_owner_id` -- resolve a loan account's owner id, needed by the
  anchor reconcile (its per-loan equity account + pay periods) and by the
  all-scenarios sync (the baseline scenario the opening must post in).
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.services.posting_service import _PostingLeg

_ZERO_MONEY = Decimal("0.00")


def loan_owner_id(loan_account_id: int) -> int | None:
    """Return a loan account's owner (``auth.users.id``), or ``None`` if absent.

    The shared owner resolver for the loan posting package: the anchor reconcile
    (:func:`._anchors.reconcile_loan_anchor_corrections`) needs it for the
    per-loan opening-equity account and the NOT NULL ``pay_period_id``, and the
    all-scenarios sync (:func:`._sync.sync_loan_postings_all_scenarios`) needs it
    to resolve the baseline scenario the opening must post in even when the loan
    has no payments.  ``None`` only for a missing / deleted account, which every
    caller treats as "nothing to reconcile".

    Args:
        loan_account_id: The loan account whose owner to resolve.

    Returns:
        The owner's user id, or ``None`` when the account row is absent.
    """
    return (
        db.session.query(Account.user_id)
        .filter(Account.id == loan_account_id)
        .scalar()
    )


def summed_posting_legs(extra_columns: list, filters: list):
    """Return a grouped query summing each ledger's posted amount and kind.

    The shared shape of the two "what is already posted" readers -- the payment
    correction reader (:func:`._payments._posted_loan_payment_legs`) and the
    anchor correction reader (:func:`._anchors._posted_loan_anchor_correction_legs`)
    -- so neither re-spells the ``SUM(amount) GROUP BY ledger, kind`` join (a
    ``duplicate-code`` finding).  Sums ``Posting.amount`` per ledger account
    (carrying its single posting kind, since a loan correction ledger always
    holds one kind), joined to the owning :class:`JournalEntry`.

    Args:
        extra_columns: Extra group-key columns prepended to the SELECT and
            GROUP BY (empty for the payment reader; the anchor reader adds
            ``source_kind_id`` and ``entry_date`` to sub-group by correction).
        filters: The entry-scoping filter expressions.

    Returns:
        A SQLAlchemy ``Query``, NOT executed.  Each row unpacks as
        ``(*extra_columns, ledger_account_id, net_amount, posting_kind_id)``.
    """
    return (
        db.session.query(
            *extra_columns,
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
            Posting.posting_kind_id,
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(*filters)
        .group_by(
            *extra_columns,
            Posting.ledger_account_id,
            Posting.posting_kind_id,
        )
    )


def delta_legs(
    target: dict[int, tuple[Decimal, int]],
    posted: dict[int, tuple[Decimal, int]],
) -> list[_PostingLeg]:
    """Return the balanced delta legs bringing *posted* to *target*.

    For each ledger account touched by either side, the leg amount is
    ``target_amount - posted_amount``; a zero delta is dropped (no leg written).
    Because *target* sums to zero by construction and every *posted* entry is
    balanced, the non-zero deltas also sum to zero -- so the result is either
    empty (already at target, an idempotent no-op) or has ``>= 2`` legs that
    :func:`app.services.posting_service._emit_balanced_entry` accepts.

    The kind labels each leg's economic nature: a leg present on the target side
    takes its target kind; a leg present ONLY on the posted side (a component the
    new target dropped to zero) is reversed with the kind it was posted under, so
    a reversal never loses the kind it is undoing.

    Args:
        target: ``{ledger_account_id: (amount, posting_kind_id)}`` the ledger
            should net to (empty to reverse everything to zero).
        posted: ``{ledger_account_id: (net_amount, posting_kind_id)}`` currently
            posted (empty when nothing is posted yet).

    Returns:
        The balanced :class:`~app.services.posting_service._PostingLeg` deltas
        (empty when *posted* already equals *target*).
    """
    legs: list[_PostingLeg] = []
    for ledger_id in sorted(set(target) | set(posted)):
        target_amount, target_kind = target.get(ledger_id, (_ZERO_MONEY, None))
        posted_amount, posted_kind = posted.get(ledger_id, (_ZERO_MONEY, None))
        delta = target_amount - posted_amount
        if delta == 0:
            continue
        kind_id = target_kind if target_kind is not None else posted_kind
        legs.append(_PostingLeg(ledger_id, delta, kind_id))
    return legs
