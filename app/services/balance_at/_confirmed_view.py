"""Balance-at-T seam -- a loan's CONFIRMED view, folded from the walk.

Plan step **E1c** (``docs/audits/balance_architecture/README.md``).  A loan's
confirmed state -- the balance it owes today and the CONFIRMED schedule rows the
amortization table and the forward projection seed from -- has one producer today,
:func:`app.services.loan_payment_service.confirmed_loan_view`, which reads it out
of the POSTED ledger (a cache of journal entries).  This module builds the SAME
:class:`~app.services.loan_resolver.ConfirmedLedgerView` from the event WALK
instead -- the balance from the fold (:func:`app.services.balance_at._fold.fold_from_walk`)
and the history rows re-derived from the walk's per-payment splits and per-anchor
corrections -- so the confirmed view stops depending on the posting cache being warm.

**Why this completes the read switch (plan Section 3).**  The seam's balance
SCALAR, per-period MAP, and LIABILITY band already fold the walk (steps C3b1/C3b3);
the confirmed VIEW that seeds the RESOLVER is the last surface still reading the
partial posting readers.  Building it from the walk is what lets those readers
(``confirmed_loan_balance_at`` / ``confirmed_loan_history_rows``) delete at plan
step E1e -- and lets a loan with a cold posting cache seed its schedule from source
facts rather than fall back to the money-blind anchor replay (finding B-12).

**Byte-equal by construction, proven by oracle.**  The row builder reproduces the
posting reader's :func:`app.services.loan_posting_service.confirmed_loan_history_rows`
exactly: each row's ``principal`` / ``interest`` equal the posted linked / interest
nets (the checked-projection invariant plan step E1a asserts at write time), and
its ``remaining_balance`` is the SAME contract-order running sum over the SAME
visible event set the reader walks.  The step E1c oracle parallel-runs this against
the posting view on every shape (``tests/test_services/test_confirmed_view_oracle.py``);
the two agree wherever both answer, and the two DELIBERATE divergences the fold's
totality creates -- a BROKEN loan (originated, no opening posting) it folds where the
reader returns ``None`` (finding B-12), and a raw transaction typed onto a loan the
walk cannot see (finding N-11, forbidden at source by BG) -- are demonstrated there,
not hidden.

**ADDITIVE and unwired (plan step E1c).**  Only the oracle reads
:func:`confirmed_view`; step E1d-b threads it through the seam's
:func:`~app.services.balance_at._resolution.resolved_loan` into
:func:`~app.services.balance_at._resolution.resolve_loan_bundle` and points the
loan-route call sites at it, then the posting readers delete.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.loan_params import LoanParams
from app.services.loan_ledger import (
    LoanLedgerWalk,
    anchor_visible_on,
    payment_visible_on,
)
from app.services.loan_loaders import load_loan_params
from app.services.loan_resolver import ConfirmedLedgerView
from app.services.amortization_engine import AmortizationRow
from app.services.rate_period_engine import (
    ConfirmedRowInputs,
    confirmed_amortization_row,
)
from app.utils.money import round_money

from ._context import BalanceContext
from ._fold import fold_from_walk

_ZERO_MONEY = Decimal("0.00")

# Contract-order tie tags: a PAYMENT sorts before an ANCHOR on a shared date, so a
# payment due exactly on an anchor's date is subsumed by that anchor's reset -- the
# SAME tie-break the walk applies (``loan_ledger.merge_anchor_and_payment_events``)
# and the posting reader mirrors (``confirmed_loan_history_rows``).
_TAG_PAYMENT = 0
_TAG_ANCHOR = 1


def _history_rows_from_walk(
    walk: LoanLedgerWalk, params: LoanParams, as_of: date,
) -> list[AmortizationRow]:
    """Return a loan's CONFIRMED schedule rows, folded from the walk through *as_of*.

    The walk-based twin of
    :func:`app.services.loan_posting_service.confirmed_loan_history_rows`,
    reproducing it row-for-row (see the module docstring for the byte-equality
    argument).  One :class:`~app.services.amortization_engine.AmortizationRow` per
    settled payment VISIBLE by *as_of*, chronological, each carrying that payment's
    ACTUAL economics off the walk's split:

    * ``principal`` / ``interest`` -- the split's real principal and accrued
      interest, which the posting writer books verbatim onto the ledger, so they
      equal the reader's posted nets (plan step E1a's checked-projection invariant).
    * ``remaining_balance`` -- the genesis running balance owed AFTER this payment,
      accumulated in CONTRACT order over ONLY the events visible by *as_of*: an
      anchor moves the balance by ``owed_before - anchor_balance`` (the exact linked
      net its posted correction carries), a payment by ``+principal``, and the row
      reads ``-(cumulative)``.  It is NOT the walk's full-timeline balance-after and
      NOT the fold sampled at the payment's visible date: a payment settled in the
      future (not yet visible) but due EARLIER must not reduce a visible row's
      balance, exactly as the reader's own event walk excludes it.
    * ``payment`` / ``extra_payment`` -- the actual P&I split against the governing
      period's contractual ``period_pi`` under the schedule-row invariant
      ``principal + interest == payment + extra_payment``.
    * ``month`` / ``payment_date`` -- numbered and dated at the installment the
      payment satisfies (the split's ``due_date``, the walk's own ordering key), so
      a late-settled payment is dated at the installment it paid, never the next.
    * ``interest_rate`` -- the split's governing period ``annual_rate`` (the SAME
      period its ``interest`` accrued at, carried on the split -- plan step E1c).

    Event ORDER mirrors the write walk exactly: payments by DUE date, anchors by
    their own date, a payment BEFORE a same-date anchor, ties within a type keeping
    the walk's ``(pay_period.start_date, id)`` order via a stable sort.  On an
    on-schedule loan every row is therefore byte-identical to the reader's row.

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk` (the read
            pass's memoized walk, :meth:`~app.services.balance_at.BalanceContext.loan_walk`).
        params: The loan's :class:`~app.models.loan_params.LoanParams`
            (``origination_date`` numbers the rows).
        as_of: The display boundary; a payment whose SETTLED date has not arrived by
            it, and an anchor dated after it, are excluded (they belong to the
            projection, not the confirmed history).

    Returns:
        The chronological confirmed :class:`~app.services.amortization_engine.AmortizationRow`
        list (possibly empty for a configured loan with no confirmed payment yet).
    """
    # Every event VISIBLE by as_of, tagged for the contract-order sort.  A payment
    # is visible from its settled date, an anchor from its own date (the ONE clock,
    # :mod:`app.services.loan_ledger._visible`) -- the SAME bound the reader applies
    # (``confirmed_shadows_through`` for payments, ``entry_date <= as_of`` for the
    # posted anchor corrections).
    events: list[tuple[date, int, object]] = [
        (split.due_date, _TAG_PAYMENT, split)
        for split in walk.payment_splits
        if payment_visible_on(split.income_shadow) <= as_of
    ] + [
        (correction.anchor.anchor_date, _TAG_ANCHOR, correction)
        for correction in walk.anchor_corrections
        if anchor_visible_on(correction.anchor.anchor_date) <= as_of
    ]
    events.sort(key=lambda event: (event[0], event[1]))

    # ``linked_sum`` is the cumulative net on the loan's linked ledger; owed is its
    # negation.  It accumulates in the SAME sign and order as the reader's
    # ``_replay_history_events`` so the row balances cannot drift.
    linked_sum = _ZERO_MONEY
    rows: list[AmortizationRow] = []
    for _event_date, tag, item in events:
        if tag == _TAG_ANCHOR:
            # The anchor's posted linked net -- the jump its reset booked, in
            # linked-ledger sign (``owed_before - anchor_balance``).  Drives owed
            # from ``owed_before`` to ``anchor_balance`` additively, matching the
            # posted correction the reader sums.
            linked_sum += (
                item.owed_before - item.anchor.anchor_balance
            )
            continue
        principal = round_money(item.principal)
        interest = round_money(item.interest)
        linked_sum += principal
        rows.append(confirmed_amortization_row(ConfirmedRowInputs(
            origination_date=params.origination_date,
            due_date=item.due_date,
            principal=principal,
            interest=interest,
            period=item.period,
            # Debit-positive ledger: owed is the negated cumulative linked net,
            # ``0 - sum`` so a zero cumulative reads 0.00, never -0.00.
            remaining_balance=round_money(_ZERO_MONEY - linked_sum),
        )))
    return rows


def confirmed_view(
    ctx: BalanceContext, account: Account,
) -> "ConfirmedLedgerView | None":
    """Return *account*'s genesis-ledger confirmed view, folded from the walk, or None.

    The walk-based replacement for
    :func:`app.services.loan_payment_service.confirmed_loan_view` (plan step E1c):
    the confirmed balance owed as of ``ctx.as_of`` (the FOLD, not the posting
    reader) bundled with the ledger-derived confirmed schedule rows, or ``None`` to
    fall back to the resolver's anchor replay.

    Returns ``None`` on the SAME guards the posting view returns ``None`` for, so a
    caller's fallback is unchanged:

    * ``ctx.scenario_id`` is ``None`` (no baseline scenario to scope the walk to);
    * ``ctx.as_of`` is after today (a future date is a forward projection, out of
      the confirmed view's domain -- the resolver projects it);
    * *account* is not a configured loan (no :class:`~app.models.loan_params.LoanParams`);
    * ``ctx.as_of`` PRECEDES the loan's ``origination_date`` -- nothing has happened
      to it yet, and the fold's honest ``0.00`` for an empty prefix must not seed the
      forward projection (the same rule stated in full on
      :func:`app.services.balance_at.positions`'s not-yet-originated branch).

    **One case the posting view returns ``None`` for, this does NOT: a BROKEN loan**
    (originated, no OPENING posting -- a cold cache or a what-if never posted into).
    The fold answers from the loan's SOURCE facts, so it returns the real confirmed
    view where the posting reader could not (finding B-12) -- the same E1
    repairable-cache decision the scalar and map already took (steps C3b1 / C3b3).
    The step E1c oracle documents this divergence rather than asserting equality
    through it.

    Args:
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext` -- its
            ``scenario`` scopes the walk, and its ``as_of`` is the evaluation date
            (the resolver's NOW).  The caller owns the ownership check on *account*.
        account: The loan account whose confirmed view to build.

    Returns:
        The :class:`~app.services.loan_resolver.ConfirmedLedgerView`, or ``None`` to
        fall back to the resolver's anchor replay.
    """
    if ctx.scenario_id is None or ctx.as_of > date.today():
        return None
    params = load_loan_params(account.id)
    if params is None or ctx.as_of < params.origination_date:
        return None
    walk = ctx.loan_walk(account)
    return ConfirmedLedgerView(
        balance=fold_from_walk(walk, [ctx.as_of])[ctx.as_of],
        history_rows=_history_rows_from_walk(walk, params, ctx.as_of),
    )
