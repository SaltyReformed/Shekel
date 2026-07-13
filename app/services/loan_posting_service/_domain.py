"""The loan ledger's DOMAIN: from when does it know a balance at all?

The confirmed balance readers (:mod:`._reader`) answer ``$0.00`` for any date
before a loan's opening.  For a loan tracked from origination that zero is true --
the debt did not exist yet.  For a MID-LIFE IMPORT, whose opening is a
``tracking_start`` dated years after origination, it is not: the loan existed and
was owed, and the ledger simply has no record of it.

The zero therefore means "no record", not "no debt", and a caller that treats it as
money gets an answer that is not merely imprecise but inverted.  The year-end
summary did exactly that: it read a mid-life-imported Mortgage's opening balance as
$0.00, subtracted a real $175,870.41 year-end balance from it, and reported the
borrower having ADDED $175,870.41 of debt in a year they had steadily paid it down.

This module holds the bound that prevents it.  A caller measuring a CHANGE across a
window clamps that window to :func:`confirmed_loan_ledger_domain`, so it only ever
asks the readers a question they can answer.

Reads only -- no writes, no commit.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.services.posting_service import _ledger_account_for
from app.utils.money import round_money

from ._asof import effective_date, scope_to_linked_ledger

_ZERO_MONEY = Decimal("0.00")


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
    """Return ``(visible_on, net)`` per date, ascending -- the one grouped load.

    Each date a posting becomes visible on (:func:`._asof.effective_date`) with
    that date's net movement on the loan's linked ledger.  Shared by
    :func:`confirmed_loan_balance_map` (which prefix-sums it) and
    :func:`_confirmed_loan_ledger_start` (which finds where the prefix first turns
    non-zero), so the two cannot disagree about what the ledger contains.

    Args:
        linked_ledger_id: The loan's linked ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``[(visible_on, net), ...]`` ascending by date.
    """
    return scope_to_linked_ledger(
        db.session.query(
            effective_date().label("visible_on"),
            db.func.sum(Posting.amount),
        ),
        linked_ledger_id, scenario_id,
    ).group_by(effective_date()).order_by(effective_date()).all()


@dataclass(frozen=True)
class LoanLedgerDomain:
    """Where a loan's confirmed ledger begins, and what it opens at.

    The two facts a caller needs in order to measure a CHANGE in the loan's balance
    without asking the readers a question they cannot answer.

    Attributes:
        start_date: The VISIBILITY date -- the earliest date the ledger carries a
            non-zero balance.  Use it to decide whether a window needs clamping,
            and for nothing else: it is a pay-period start, so it can sit a few
            days BEFORE the balance was actually asserted.
        opening_date: The CIVIL date the opening balance was asserted on (the
            loan's origination, or its ``tracking_start`` for a mid-life import).
            This is the instant ``opening_balance`` is as-of, so it is the date to
            SHOW a user.
        opening_balance: The balance the ledger OPENS at, ``-(sum of the linked
            ledger's OPENING-kind postings)``.

    **The two dates are different on purpose, and confusing them prints a lie.**  On
    the real Mortgage the tracking-start asserts $178,375.43 on 2026-03-31, but its
    pay period begins 2026-03-26, so ``start_date`` is 2026-03-26 -- a date on which
    the readers say the balance was $178,103.41.  Publishing the pair
    ``(start_date, opening_balance)`` would therefore show a balance the seam itself
    contradicts, five days before it was asserted.  ``opening_date`` is the honest
    label; ``start_date`` is the correct clamp.

    ``opening_balance`` is deliberately NOT ``confirmed_loan_balance_at(start_date)``.
    Postings are visible from their pay period's START, so a payment falling in the
    same pay period as the opening shares its visibility date -- on the real
    Mortgage, the April-1 payment sits in the pay period that begins 2026-03-26, the
    very period the 2026-03-31 tracking-start lands in.  Reading the balance AT the
    start date would therefore net that payment away, folding principal the borrower
    paid INSIDE the window into the window's opening balance, and under-reporting
    principal paid by exactly that amount ($272.02).  The opening-kind sum is the
    balance BEFORE any payment in the window, which is what an opening balance means.
    """

    start_date: date
    opening_date: date
    opening_balance: Decimal


def confirmed_loan_ledger_domain(
    loan_account_id: int, scenario_id: int,
) -> LoanLedgerDomain | None:
    """Return where a loan's confirmed ledger begins, and what it opens at.

    See :class:`LoanLedgerDomain`.  ``None`` when the loan has no opening posting
    (unconfigured), or when every posting nets to zero.

    Args:
        loan_account_id: The loan account to bound.
        scenario_id: The budget scenario to scope to.

    Returns:
        The loan's :class:`LoanLedgerDomain`, or ``None``.
    """
    start = _confirmed_loan_ledger_start(loan_account_id, scenario_id)
    if start is None:
        return None
    linked = _ledger_account_for(loan_account_id)
    opening_kind_id = ref_cache.posting_kind_id(PostingKindEnum.OPENING)
    # The OPENING-kind legs, grouped by the CIVIL date each was asserted on.  The
    # ledger is append-only, so a superseded opening still sits here beside its
    # reversal -- and nets to zero.  Skipping the zero-net dates is what leaves the
    # LIVE opening: on the real Mortgage, the 2018-12-01 origination (reversed when
    # the operator recorded a tracking-start) nets to $0.00 and the 2026-03-31
    # tracking-start carries the whole -$178,375.43.
    by_civil_date = scope_to_linked_ledger(
        db.session.query(
            JournalEntry.entry_date,
            db.func.coalesce(db.func.sum(Posting.amount), _ZERO_MONEY),
        ),
        linked.id, scenario_id,
    ).filter(
        Posting.posting_kind_id == opening_kind_id,
    ).group_by(JournalEntry.entry_date).order_by(JournalEntry.entry_date).all()

    live = [(when, net) for when, net in by_civil_date if net != _ZERO_MONEY]
    if not live:
        # Every opening was reversed and nothing replaced it: the ledger carries no
        # opening balance, which is the unconfigured sentinel, not a $0 loan.
        return None
    opening_date = live[0][0]
    # Debit-positive ledger: the linked opening leg is -(owed), so negate.
    net = sum((net for _when, net in live), _ZERO_MONEY)
    return LoanLedgerDomain(
        start_date=start,
        opening_date=opening_date,
        opening_balance=round_money(_ZERO_MONEY - net),
    )


def _confirmed_loan_ledger_start(
    loan_account_id: int, scenario_id: int,
) -> date | None:
    """Return the first date this loan's confirmed ledger knows a balance.

    The lower edge of the ledger's DOMAIN: the earliest date at which the loan's
    running confirmed balance becomes NON-ZERO.  In practice that is its opening --
    the origination for a loan tracked from day one, the ``tracking_start`` for a
    mid-life import.

    Defined on the CUMULATIVE balance rather than on the earliest posting date,
    because the ledger is append-only and a superseded correction is REVERSED, not
    deleted.  A mid-life import that was first opened at its origination and later
    re-opened at a tracking-start therefore still carries the original
    origination-dated entries -- they simply net to zero.  Taking the earliest
    posting date would hand back that dead origination date (2018-12-01 on the real
    Mortgage) and defeat the whole purpose of this function: the caller would clamp
    its window to a date the ledger has no balance for, and read ``$0.00`` again.

    Callers need this because :func:`confirmed_loan_balance_at` answers ``$0.00``
    for a date before it, and that zero means "no record", NOT "no debt" (see its
    caveat).  A caller measuring a CHANGE across a window -- the year-end summary's
    principal-paid, say -- must clamp the window to start here, or it subtracts a
    real balance from a fabricated zero and reports the borrower ADDING debt they
    actually paid down.  Asking a question the ledger cannot answer, then presenting
    the non-answer as money, is the failure this exists to prevent.

    Args:
        loan_account_id: The loan account to bound.
        scenario_id: The budget scenario to scope to.

    Returns:
        The earliest date the loan's ledger carries a non-zero balance, or ``None``
        when it has no opening posting at all (an unconfigured loan -- the same
        sentinel the balance readers use), or when every posting nets to zero.
    """
    linked = _ledger_account_for(loan_account_id)
    if not _has_opening_posting(linked.id, scenario_id):
        return None
    running = _ZERO_MONEY
    for visible_on, date_net in _visible_nets(linked.id, scenario_id):
        running += date_net
        if running != _ZERO_MONEY:
            return visible_on
    return None
