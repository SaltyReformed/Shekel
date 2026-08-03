"""Deploy-time cash resync: every settled source walked back through its sync.

The cash-side twin of ``loan_posting_service.backfill_all_loan_postings`` and
``account_posting_service.backfill_all_account_anchor_postings``, and the third
of the three deploy-time reconciles that between them cover every journal entry
the app writes.

**It is a PUBLIC sibling module, exactly as :mod:`app.services.posting_reads`
is, and plan step X-d is where ``posting_service`` crossed the 1000-line gate
that forced the question.**  Public rather than private because its one caller,
``scripts.init_database``, sits outside ``app.services`` and the package-privacy
gate (W9910) refuses a private module to an outside importer -- and because a
re-export from ``posting_service`` would close an import cycle, this module
driving that one.  Both other packages keep their deploy-wide sweep in a ``_sync``
module beside the per-mutation writers rather than inside them; the cash writer
had no such separation and grew one here.  A deploy sweep and a lifecycle writer
are different concerns with different callers -- ``scripts.init_database`` on one
side, every route and service on the other -- and only the sweep enumerates the
whole database.

Flask-isolated and commit-free like the writer it drives: it flushes through the
shared reconcile and leaves the transaction boundary to the deploy hook.
"""

from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.posting_service import (
    sync_transaction_postings,
    sync_transfer_postings,
)
from app.utils.balance_predicates import settled_status_ids


def resync_all_cash_postings() -> tuple[int, int]:
    """Re-reconcile every settled cash source's postings (deploy resync).

    The transaction / transfer twin of
    :func:`app.services.loan_posting_service.backfill_all_loan_postings` and
    :func:`app.services.account_posting_service.backfill_all_account_anchor_postings`,
    and the third of the three deploy-time reconciles that between them cover
    every journal entry the app writes.  It exists because those two do NOT
    reach an ordinary transaction or a NON-loan transfer: the loan package's
    staleness detector is scoped to one loan's linked ledger
    (``loan_posting_service._sync._resync_stale_transfers``) and the anchor
    backfill reconciles only the corrections, so a checking-to-savings transfer
    and every ordinary settled row were maintained per-mutation and by nothing
    else.

    **What it is FOR, and why it is a permanent hook rather than a one-off**
    (ruling R-DH (b), ``docs/audits/balance_architecture/anchor_settle_partition.md``).
    ``journal_entries.entry_date`` is derived by :func:`_civil_settle_date`,
    which moved from the UTC civil day to the user's on 2026-07-31.  Every entry
    written before that carries the old day, so the STORED ledger and the two
    folds that now read the new one disagree for any settle recorded between
    midnight UTC and the user's midnight -- on production, one ``$1,910.95``
    mortgage payment stamped 2026-07-02 00:38:53 UTC that belongs to the evening
    of 2026-07-01.  This walks every settled source back through the SAME
    go-forward sync, so a re-dated entry is identical to a freshly posted one by
    construction; there is no second implementation of the rule and no SQL
    restatement of it, which is the property this whole arc exists to hold.

    It stays wired on every deploy rather than being deleted after one run, for
    the same reason its two siblings are: reconcile-to-target makes it a no-op
    at target, so it costs one pass and converts any future drift -- a rule
    change, a hand-edited row, a half-applied migration -- into a self-heal
    instead of a silent divergence.

    Idempotent and self-healing.  A settled row already at target posts nothing;
    a row whose target DATE moved gets its old-date legs reversed and its new
    -date legs posted in one balanced pair by
    :func:`sync_transaction_postings` / :func:`sync_transfer_postings`, which
    reconcile over the ``(period, entry_date)`` keys already in the ledger
    unioned with the target (plan step E1a's per-date attribution) -- so a
    moved date is an ordinary reconcile, not a special case this function has to
    know about.

    Loan payment transfers are re-synced here too and that is deliberate
    duplication of effort, not of RULE: the loan package would reach the same
    ones through its own detector, and both paths call this module's
    :func:`sync_transfer_postings`, so whichever runs first leaves the other at
    target.

    Flushes but does NOT commit -- the caller owns the transaction boundary
    (``scripts.init_database.resync_all_cash_postings_after_migration``, which
    initialises ``ref_cache`` first because the migration host does not).

    **The counts are sources CHANGED, not sources walked** (finding N-133 / F8).
    A hook that rewrites the whole production ledger on every deploy and reports
    the same number whether it moved every date or nothing at all tells the
    operator only that it ran.  Both sync functions return the journal entries
    they emitted -- empty when already at target -- so "changed" is observable
    without a second query, and a healthy deploy logs ``0, 0``.  The FIRST
    deploy after a dating rule moves is the one that logs a non-zero count, and
    that line is the only evidence the one-time re-date happened.

    **The re-date is ONE-WAY, and that is a stated risk rather than a
    discovered one.**  ``entrypoint.sh`` runs ``set -eEuo pipefail`` and calls
    ``scripts/init_database.py``, so a failure here aborts the container and the
    auto-rollback fires before anything commits.  But if the healthcheck fails
    AFTER this commits, the rolled-back image reads a display-dated ledger with
    the previous image's UTC rules, and only the entries whose two days differ
    are affected (on production at the cutover: one payment, one day).  Rolling
    back ACROSS a dating change therefore needs this hook re-run under the old
    image, not just a container swap.

    Returns:
        ``(transactions_changed, transfers_changed)`` -- how many settled
        sources this pass actually re-posted, for the deploy log.
    """
    settled_ids = settled_status_ids()
    transactions = (
        db.session.query(Transaction)
        .options(
            selectinload(Transaction.entries),
            # ``pay_period`` is a plain lazy relationship, and BOTH
            # ``_transaction_entry_date`` (its ``start_date`` fallback) and
            # ``_settled_target`` (``pay_period.user_id``) dereference it once
            # per row -- 122 extra SELECTs on production's settled set with the
            # eager ``entries`` load right beside it doing the same job for the
            # other relationship (finding N-133 / F9).
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.is_deleted.is_(False),
            Transaction.transfer_id.is_(None),
            Transaction.status_id.in_(settled_ids),
        )
        .order_by(Transaction.id)
        .all()
    )
    transactions_changed = sum(
        1 for txn in transactions
        if sync_transaction_postings(txn, settled=True)
    )

    transfers = (
        db.session.query(Transfer)
        .options(joinedload(Transfer.pay_period))
        .filter(
            Transfer.is_deleted.is_(False),
            Transfer.status_id.in_(settled_ids),
        )
        .order_by(Transfer.id)
        .all()
    )
    transfers_changed = sum(
        1 for xfer in transfers
        if sync_transfer_postings(xfer, settled=True)
    )

    return transactions_changed, transfers_changed
