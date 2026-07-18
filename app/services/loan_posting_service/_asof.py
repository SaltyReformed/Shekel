"""Loan-ledger read selection: WHICH postings a balance read sees, and AS OF WHEN.

The single posting-selection layer both confirmed-balance readers
(:mod:`._reader`) are built on, so the scalar and the per-period map can never
drift on which postings a given date selects -- the invariant
``confirmed_loan_balance_map[P] == confirmed_loan_balance_at(P.start_date)``
holds by construction rather than by two hand-synchronised query tails.

Two pieces:

* :func:`scope_to_linked_ledger` -- the shared FROM / JOIN / WHERE (one loan's
  linked ledger, one scenario).
* :func:`effective_date` -- the as-of key, which differs by the NATURE of the
  fact a posting records: cash is budgeted to a pay period, an anchor asserts a
  civil date.  See its docstring; that distinction is the whole module.

Reads only -- this module builds expressions, it does not execute them.
"""

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.pay_period import PayPeriod


def _anchor_source_ids() -> list[int]:
    """Return the source kinds whose CIVIL DATE is the authoritative one.

    An anchor correction (the loan's opening and every balance true-up) is an
    ASSERTION ABOUT A DATE.  It carries no budget dimension -- it is not cash, and
    no pay period "contains" it in any economic sense; its ``pay_period_id`` exists
    only because the column is NOT NULL.  A payment is the opposite: it IS cash,
    deliberately budgeted to a period.

    That difference is what :func:`effective_date` turns into two bounds.

    Returns:
        The ``loan_opening`` / ``loan_trueup`` posting-source ids.
    """
    return [
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING),
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP),
    ]


def effective_date():
    """Return the date a posting becomes visible to a balance-as-of read.

    The single as-of key BOTH readers bound by, so the scalar and the map cannot
    drift on WHICH postings a date selects (``map[P] == balance_at(P.start)`` stays
    true by construction).

    * **Cash** (a payment's split correction): its pay period's ``start_date``.
      This is deliberate and load-bearing -- a payment settled BEFORE its pay period
      begins must not appear in a displayed balance until that period starts
      (:func:`app.services.loan_loaders.settled_income_shadows`: "posting early
      changes when the fact is RECORDED, never when it is SHOWN").
    * **An anchor** (opening / true-up): ``LEAST(entry_date, pay_period.start)``.

    The ``LEAST`` repairs a lie the storage layer is forced into.  An anchor's
    ``entry_date`` IS its ``anchor_date`` (the real civil date it asserts), but
    ``journal_entries.pay_period_id`` is NOT NULL, so an anchor that predates every
    pay period the user has must still be filed under one --
    :func:`app.services.loan_ledger.resolve_anchor_pay_period` falls back to
    the EARLIEST period.
    That fallback can only ever push an anchor LATER than it truly happened, and a
    period-bounded reader then believes it did.  A loan originated 2025-01-01 whose
    owner's pay periods begin 2026-01-02 was reported as owing NOTHING for the whole
    of 2025 -- the year-end summary read its Jan-1 balance as $0.00 and so reported
    NEGATIVE principal paid.

    ``LEAST`` corrects exactly that and nothing else: when a period genuinely
    contains the anchor, ``entry_date >= period.start`` and the expression collapses
    to the period bound, leaving every existing balance untouched.

    Verified against the production clone: since step C1 the LIVE opening on each
    loan is its ORIGINATION (the Mortgage's 2018-12-01, the Van Loan's 2023-02-14),
    both years before the owner's first pay period (2026-03-26), so the ``LEAST``
    resolves each to its own civil date and every rendered period sees the opening.
    A mid-life ``tracking_start`` is now an ordinary true-up whose ``LEAST``
    collapses to its containing-period start.  For a date strictly before a loan's
    tracking-start this DOES move the confirmed balance -- the reader sees the
    origination opening held flat, the honest pre-tracking plateau: the Van Loan's
    first period (start 2026-03-26, before its 2026-04-11 tracking-start) reads its
    $32,402.45 origination principal where it read $0.00 before (B-11, signed off at
    C1).  The Mortgage's tracking-start falls in that first period, so its ``LEAST``
    lands on the period start and the map does not move; a date at/after the
    tracking-start is unchanged for both.  Safe by structure, not luck:
    :func:`app.services.account_projection.find_period_containing_date` falls back to
    the latest period ENDING on or before its target, so the ``periods[0]`` fallback
    fires only for a date preceding every period.  An anchor's effective date is
    therefore always either a period start or a date strictly before the first
    period -- never strictly inside a period.

    Returns:
        A SQLAlchemy expression usable in a filter, GROUP BY, or ORDER BY over a
        ``Posting -> JournalEntry -> PayPeriod`` join.
    """
    return db.case(
        (
            JournalEntry.source_kind_id.in_(_anchor_source_ids()),
            db.func.least(JournalEntry.entry_date, PayPeriod.start_date),
        ),
        else_=PayPeriod.start_date,
    )


def scope_to_linked_ledger(query, linked_ledger_id: int, scenario_id: int):
    """Scope a :class:`Posting` query to one loan's linked ledger in one scenario.

    The shared FROM / JOIN / WHERE of the confirmed-balance scalar and map
    readers -- they differ only in projection (a coalesced total vs a per-period
    total) and tail (an as-of bound vs a group-by) -- so the two cannot drift on
    WHICH postings they sum: ``confirmed_loan_balance_map[P]`` and
    ``confirmed_loan_balance_at(P.start_date)`` are then the same sum by
    construction.  Joins each posting to its journal entry (for the scenario
    scope and the pay-period link) and that entry to its pay period (for the
    as-of bound / period grouping the callers add), then filters to the one
    linked ledger in the one scenario.

    Args:
        query: A ``db.session.query(...)`` over :class:`Posting` whose projection
            the caller has already set (a ``SUM`` for the scalar reader;
            ``start_date, SUM`` for the map reader).
        linked_ledger_id: The loan's linked ledger account id
            (:func:`app.services.posting_service._ledger_account_for`).
        scenario_id: The budget scenario to scope to.

    Returns:
        The *query* with the entry + pay-period joins and the ledger + scenario
        filters applied; the caller adds its own tail (as-of bound or grouping)
        and executor.
    """
    return (
        query
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(PayPeriod, JournalEntry.pay_period_id == PayPeriod.id)
        .filter(
            Posting.ledger_account_id == linked_ledger_id,
            JournalEntry.scenario_id == scenario_id,
        )
    )
