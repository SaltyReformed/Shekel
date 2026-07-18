"""The loan ledger's DOMAIN: from when does it know a balance at all?

**SUPERSEDED by step C1, and slated for deletion at C3.**  This module was written
when a mid-life import's ledger OPENED at its ``tracking_start`` (dated years after
origination), so the readers answered a false ``$0.00`` for the whole pre-tracking
window and a change-across-a-window caller misread that "no record" as "no debt" --
the year-end summary read a Mortgage's opening as $0.00, subtracted a real year-end
balance, and reported the borrower ADDING debt they had steadily paid down.  This
module held the bound that prevented it: a caller clamped its window to the loan's
domain, so it only ever asked the readers a question they could answer.

Since step C1 the ledger opens at ORIGINATION and a ``tracking_start`` is an ordinary
true-up, so a date before origination reads ``$0.00`` because the debt truly did not
exist yet (see :func:`._reader.confirmed_loan_balance_at`).  ``confirmed_loan_ledger_domain``
therefore now reports the ORIGINATION opening, not the tracking-start -- which no
longer answers "where do the RECORDS begin" for a mid-life import (that is its
tracking-start true-up).  Its only consumer, the year-end principal-progress clamp,
is dead code (deleted at F2); the ``tracking_start``-as-opening narrative in the
docstrings below is pre-C1 history.

**It answers "where does the RECORD begin", never "does this loan exist yet".**
Those are different questions, and this one takes no as-of because it is not asked
of a date: it reports what the ledger CONTAINS.  A loan that has not originated has
an opening posting all the same (the genesis walk records every anchor whatever its
date, and the readers decide what has happened), so this producer would happily
report a $200,000.00 opening for a mortgage that has not closed -- dated from its
pay period's START (N-10).  The existence question belongs to whoever holds the
as-of, which is the seam entry
(:func:`app.services.balance_at.loan_ledger_domain`); it asks the FACT
(``origination_date``) and answers ``None`` for such a loan, and the W9906 fence
makes it the only door consumers may use.  So the promise above holds for every
legal caller -- but this function alone does not carry it.

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
    """Return ``(entry_date, net)`` per date, ascending -- the one grouped load.

    Each posting's ``entry_date`` (the day the event it records happened -- step
    C2's one clock) with that date's net movement on the loan's linked ledger.
    Shared by :func:`confirmed_loan_balance_map` (which prefix-sums it) and
    :func:`_confirmed_loan_ledger_start` (which finds where the prefix first turns
    non-zero), so the two cannot disagree about what the ledger contains.

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


@dataclass(frozen=True)
class LoanLedgerDomain:
    """Where a loan's confirmed ledger begins, and what it opens at.

    The two facts a caller needs in order to measure a CHANGE in the loan's balance
    without asking the readers a question they cannot answer.

    Attributes:
        start_date: The VISIBILITY date -- the earliest date the ledger carries a
            non-zero balance.  Since step C2's one clock this is the ``entry_date``
            of the first balance event (the origination opening), i.e. the date it
            was actually asserted; use it to decide whether a window needs clamping.
        opening_date: The CIVIL date the opening balance was asserted on -- the
            loan's origination (since step C1 the ledger opens there always, even
            for a mid-life import).  This is the instant ``opening_balance`` is
            as-of, so it is the date to SHOW a user.
        opening_balance: The balance the ledger OPENS at, ``-(sum of the linked
            ledger's OPENING-kind postings)``.

    Since step C2 ``start_date`` and ``opening_date`` coincide for an ordinary loan
    -- both are the origination's ``entry_date`` -- because an event is now visible
    from the day it happened, not from its pay period's start.  (Before C2 the
    opening was visible from its pay period's START, which could sit days before it
    was asserted, so the two dates diverged and the pair had to be published with
    care.)  ``opening_balance`` stays the OPENING-kind sum rather than
    ``confirmed_loan_balance_at(start_date)`` so it is the balance BEFORE any
    later same-day payment nets against it -- the balance an opening MEANS -- which
    is what a change-across-a-window caller must subtract from.
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
    # LIVE opening: since step C1 that is the ORIGINATION (on the real Mortgage the
    # 2018-12-01 origination carries the whole -$202,000.00; the 2026-03-31
    # tracking-start is now a true-up, not an OPENING leg, so it does not appear
    # here, and the pre-C1 tracking-start OPENING it replaced nets to $0.00).
    by_civil_date = (
        db.session.query(
            JournalEntry.entry_date,
            db.func.coalesce(db.func.sum(Posting.amount), _ZERO_MONEY),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            JournalEntry.scenario_id == scenario_id,
            Posting.posting_kind_id == opening_kind_id,
        )
        .group_by(JournalEntry.entry_date)
        .order_by(JournalEntry.entry_date)
        .all()
    )

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
