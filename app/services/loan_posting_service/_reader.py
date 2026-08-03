"""Loan posting ATTRIBUTION reads: what each payment's posted legs say it paid.

The read half of the genesis loan sub-ledger.  The write modules
(:mod:`._payments`, :mod:`._anchors`, :mod:`._sync`) post a loan's OPENING,
every confirmed payment's split, and every balance TRUE-UP onto ONE linked
ledger account (:func:`app.services.posting_service._ledger_account_for`); this
module reads those legs back **per payment** -- the interest posted against one
settled shadow, the escrow, the real principal its linked legs net to -- which
is what the payment-history table (:mod:`._display`) renders.

**It no longer answers "what does this loan owe on date T", and that is the
point (plan step E1e).**  Two sum-of-postings balance readers lived here
(``confirmed_loan_balance_at`` / ``confirmed_loan_balance_map``) while the read
switch made the posted cache the displayed balance.  Both ends of that era are
gone: the balance seam answers a loan from the event FOLD (steps C3b1 / C3b3),
its confirmed schedule rows come from the walk (step E1d-b), and the balance
sheet reads these postings through
:mod:`app.services.ledger_report_service` -- so the readers had no caller in
``app/`` at all.  Their last role was to be the counterparty the fold and the
resolver are graded against, and an oracle's window belongs on the oracle's
side, so it moved into the test suite (``tests/_test_helpers.py``'s
``posted_loan_balance_at``).  This package is the GENERAL ledger: the balance
sheet, the statements, the attribution -- never the answer to "what do I owe"
(plan Section 3).  With the readers gone, no public balance producer exists
outside the ``balance_at`` seam at all, which is what let the W9906 call fence
delete whole.

**The one clock still governs what these reads see** (step C2): each posting
counts from its ``entry_date``, the day the event it records happened.  The
writer stamps that day honestly -- a payment's cash and split legs carry its
SETTLED date (:func:`app.utils.balance_predicates.settled_day`), an
anchor correction carries the ``anchor_date`` it asserts
(:func:`app.services._posting_reconcile.emit_anchor_correction_entry`) -- which
is the same cut the fold applies from source
(:func:`app.services.balance_at._fold.fold_loan_balances`), and what keeps the
posted projection and the fold equal on every day (step B2, and the write-time
assert of step E1a).

The paid-in-year tax / chip figures moved OFF the postings onto the fold (steps
C3c / C6c: :func:`app.services.balance_at.loan_interest_in_year`,
:func:`~app.services.balance_at.loan_interest_paid_in_year`,
:func:`~app.services.balance_at.loan_principal_paid_in_year`), so this module no
longer answers those either.  Reads only -- no writes, no commit.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.loan_params import LoanParams
from app.models.transaction import Transaction
from app.services import loan_loaders
from app.services.loan_ledger import confirmed_shadows_through
from app.services.posting_service import _ledger_account_for

from ._linked_ledger import _has_opening_posting

_ZERO_MONEY = Decimal("0.00")


def _net_by_shadow_for_kind(
    loan_account_id: int,
    scenario_id: int,
    kind_enum: LedgerAccountKindEnum,
) -> dict[int, Decimal]:
    """Return each payment shadow's NET posted amount on one per-loan ledger kind.

    Sums the postings on the loan's per-loan ledger of *kind_enum*
    (``loan_interest`` / ``loan_escrow`` / ...), grouped by the payment shadow
    they book under (``journal_entries.transaction_id`` -- every such leg is a
    loan-payment split correction, which links by the income shadow's id).  A
    payment shadow's net across all its legs of this kind is the original split
    plus any true-up / rate re-split delta or reversal, so a reverted payment
    nets to zero and drops out with no status filter.  A HARD-deleted payment's
    legs carry a NULL ``transaction_id`` (``journal_entries.transaction_id`` is
    ``ON DELETE SET NULL``) after its correction was already reversed to zero
    (:func:`._payments.reverse_loan_payment_postings_for_shadow` runs before the
    delete); the ``isnot(None)`` filter drops that dead group explicitly.

    The one query shape behind every per-loan-kind per-shadow reader (interest
    for the tax figure, escrow for the payment-history split), so no two can
    drift on what counts as a payment's posted amount of a given kind.

    Args:
        loan_account_id: The loan whose per-payment legs to sum.
        scenario_id: The budget scenario to scope to.
        kind_enum: The per-loan ledger kind to sum (e.g.
            :attr:`~app.enums.LedgerAccountKindEnum.LOAN_INTEREST`).

    Returns:
        ``{shadow transaction id: net Decimal}``; empty when no leg of this kind
        is posted yet.
    """
    kind_id = ref_cache.ledger_account_kind_id(kind_enum)
    return dict(
        db.session.query(
            JournalEntry.transaction_id, db.func.sum(Posting.amount),
        )
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.loan_account_id == loan_account_id,
            LedgerAccount.kind_id == kind_id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.transaction_id.isnot(None),
        )
        .group_by(JournalEntry.transaction_id)
        .all()
    )


def _interest_net_by_shadow(
    loan_account_id: int, scenario_id: int,
) -> dict[int, Decimal]:
    """Return each payment shadow's NET posted interest, keyed by shadow id.

    The ``loan_interest`` specialisation of :func:`_net_by_shadow_for_kind` (see
    it for the net / reversal / hard-delete semantics), read by the
    payment-history table (:func:`._display.confirmed_loan_payment_history`),
    which places each net on its payment's row.

    Args:
        loan_account_id: The loan whose per-payment interest to sum.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``{shadow transaction id: net interest Decimal}``; empty when no
        interest leg is posted yet.
    """
    return _net_by_shadow_for_kind(
        loan_account_id, scenario_id, LedgerAccountKindEnum.LOAN_INTEREST,
    )


def _principal_net_by_shadow(
    loan_account_id: int, scenario_id: int,
) -> dict[int, Decimal]:
    """Return each settled payment's NET principal on the loan's linked ledger.

    A payment's principal is its net on the loan's LINKED (liability) ledger --
    the Step-2 cash leg plus the Step-4 split correction -- which by the balanced
    construction of the correction is exactly the real debt it paid down (a
    payoff-overpayment's excess goes to a Refund leg, not principal).  The cash
    leg links by the payment's ``transfer_id`` (``transaction_id`` NULL); the
    correction links by the income shadow's ``transaction_id``; so both linkages
    map to the same settled shadow and their nets accumulate into that payment's
    principal.  A non-payment linked posting -- the opening, every true-up, a raw
    transaction typed onto the loan -- matches no settled shadow and is excluded,
    so this is payment principal only.

    Covers EVERY settled payment (no period bound), matching the all-settled
    basis of :func:`_interest_net_by_shadow`, so
    :func:`confirmed_loan_payment_history` can index the map by its
    confirmed-through-``as_of`` shadows and a payment's principal / interest split
    stay on one payment set.

    Args:
        loan_account_id: The loan whose per-payment principal to sum.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``{shadow transaction id: net principal Decimal}`` (unrounded running
        sums; the caller rounds); empty when the loan has no settled payment.
    """
    shadows = loan_loaders.settled_income_shadows(loan_account_id, scenario_id)
    shadow_ids = {shadow.id for shadow in shadows}
    shadow_id_by_transfer = {
        shadow.transfer_id: shadow.id for shadow in shadows
    }
    linked = _ledger_account_for(loan_account_id)
    principal_by_shadow: dict[int, Decimal] = {}
    for _date, _source, transfer_id, transaction_id, net in _linked_entry_nets(
        linked.id, scenario_id,
    ):
        if transaction_id in shadow_ids:
            key = transaction_id
        elif transfer_id in shadow_id_by_transfer:
            key = shadow_id_by_transfer[transfer_id]
        else:
            continue
        principal_by_shadow[key] = (
            principal_by_shadow.get(key, _ZERO_MONEY) + net
        )
    return principal_by_shadow


def _linked_entry_nets(
    linked_ledger_id: int, scenario_id: int,
) -> list[tuple[date, int, int | None, int | None, Decimal]]:
    """Return each journal entry's net on a loan's linked ledger, with its keys.

    One grouped load of EVERY posting on the linked ledger in the scenario --
    the same total set the balance readers sum -- projected per journal entry
    as ``(entry_date, source_kind_id, transfer_id, transaction_id, net)``.
    :func:`_principal_net_by_shadow` groups them onto the payment each belongs
    to, which is what the payment-history table's principal column reads.
    Reading the nets per entry -- rather than re-deriving splits from rates --
    is what keeps that column a READ of the ledger's actual legs rather than a
    recomputation of the walk it is meant to cross-check.

    Args:
        linked_ledger_id: The loan's linked ledger account id
            (:func:`app.services.posting_service._ledger_account_for`).
        scenario_id: The budget scenario to scope to.

    Returns:
        One ``(entry_date, source_kind_id, transfer_id, transaction_id, net)``
        tuple per distinct linkage group; empty when nothing is posted yet.
    """
    return (
        db.session.query(
            JournalEntry.entry_date,
            JournalEntry.source_kind_id,
            JournalEntry.transfer_id,
            JournalEntry.transaction_id,
            db.func.sum(Posting.amount),
        )
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked_ledger_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .group_by(
            JournalEntry.entry_date,
            JournalEntry.source_kind_id,
            JournalEntry.transfer_id,
            JournalEntry.transaction_id,
        )
        .all()
    )


def _confirmed_history_inputs(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> tuple[LoanParams, LedgerAccount, list[Transaction]] | None:
    """Load the shared inputs of the confirmed history producers, or None.

    The entry guard + load behind the payment-history table
    (:func:`._display.confirmed_loan_payment_history`): a configured loan
    (:class:`~app.models.loan_params.LoanParams`) with an OPENING posting in the
    scenario, plus its confirmed income shadows through *as_of*.  Returns ``None``
    when the ledger cannot answer -- no params, or no opening posting -- so both
    surfaces fall back / hide on the identical condition.

    Args:
        loan_account_id: The loan account to load.
        scenario_id: The budget scenario to scope to.
        as_of: The display boundary for the confirmed shadows.

    Returns:
        ``(params, linked ledger account, confirmed shadows through as_of)``, or
        ``None`` when the loan is unconfigured / not opened in the scenario.
    """
    params = loan_loaders.load_loan_params(loan_account_id)
    if params is None:
        return None
    linked = _ledger_account_for(loan_account_id)
    if not _has_opening_posting(linked.id, scenario_id):
        return None
    shadows = confirmed_shadows_through(loan_account_id, scenario_id, as_of)
    return params, linked, shadows
