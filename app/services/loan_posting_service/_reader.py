"""Loan confirmed-balance reader: the genesis sum-of-postings read side.

The read half of the genesis loan sub-ledger.  The write modules
(:mod:`._payments`, :mod:`._anchors`, :mod:`._sync`) post a loan's OPENING,
every confirmed payment's split, and every balance TRUE-UP onto ONE linked
ledger account (:func:`app.services.posting_service._ledger_account_for`); this
module reads them back, so a loan's confirmed balance is::

    owed(as_of) = round_money(-(sum of the loan's linked-ledger postings whose
                                 pay period has begun by as_of))

with no external anchor read and no post-anchor eligibility filter -- the plain
sum the read-switch arc exists to reach, superseding the resolver's read-time
replay of confirmed history.  Because every source posts onto the one linked
ledger, at ``as_of = date.today()`` this equals
``round_money(-posting_service.account_posting_total(loan, scenario))`` -- the
quantity the reconciliation oracle already proves equals the resolver's
replayed ``current_balance``; the pay-period-start bound generalises it to any
historical date, and the per-period map applies it at every period boundary.

**The as-of bound is period assignment, not a boundary rule.**  Each posting is
attributed to a whole pay period (its journal entry's NOT NULL ``pay_period_id``),
and pay periods are contiguous, so bounding CASH by ``pay_period.start_date <=
as_of`` selects exactly the postings whose period has begun -- the same confirmed
cut the walk (:func:`app.services.loan_ledger.walk_loan_ledger`) applied when it
produced them, not
a recomputed special case.  This is why the per-period map (keyed by period start)
IS the canonical period-END-keyed loan balance the projection reports
(:func:`app.services.account_projection.compute_forward_loan_period_balance_map`):
a posting's period start is a real boundary and periods are contiguous, so
``<= period.start`` and ``<= period.end`` select the identical posting set.

An ANCHOR (the opening, every true-up) is bounded by ``LEAST(entry_date,
period.start)`` instead.  For the ordinary anchor -- one a pay period CONTAINS --
that collapses to the period start, exactly as before, and nothing moves.  It differs
only for an anchor that predates EVERY pay period the user has, which
``journal_entries.pay_period_id`` being NOT NULL forces to be filed under the
earliest period anyway
(:func:`._anchors._resolve_anchor_pay_period`); that fallback
can only ever push such an anchor LATER than it truly happened, and a period-bounded
reader believed it.  The ``LEAST`` restores the anchor's own civil date, which is
the only date it ever asserted.  Both readers take the key from the one place that
defines it (:func:`._asof.effective_date`), so they cannot drift on which postings a
date selects.

**Wiring status.**  The current-balance scalar AND the history rows are wired
through the ``loan_payment_service.confirmed_loan_view`` seam (read switch C8,
bundled at C11), the per-period map through
``net_worth_kernel._build_amortizing_balance_map`` (C9), and the tax interest
through the year-end hybrid (C10); the readers join the balance-producer fence
in the final read-switch commit.  Reads only -- no writes, no commit.
"""

from bisect import bisect_right
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingSourceEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import loan_loaders, loan_resolver
from app.services.amortization_engine import AmortizationRow
from app.services.loan_ledger import confirmed_shadows_through
from app.services.posting_service import _ledger_account_for
from app.services.rate_period_engine import (
    RatePeriod,
    payment_number,
    period_for_date,
)
from app.utils.dates import to_display_civil_date
from app.utils.money import round_money

from ._asof import effective_date, scope_to_linked_ledger
from ._domain import _has_opening_posting, _visible_nets

_ZERO_MONEY = Decimal("0.00")


def confirmed_loan_balance_at(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> Decimal | None:
    """Return a loan's confirmed balance as of a past date (genesis sum-of-postings).

    ``owed(as_of) = round_money(-(sum of the loan's linked-ledger postings,
    scenario-scoped, whose pay period has begun by as_of))`` -- the opening
    (``-original_principal``), every confirmed payment's net principal (the
    Step-2 cash leg plus the Step-4 split correction), and every true-up, with
    no kind filter and no eligibility lower bound (see the module docstring).
    At ``as_of = date.today()`` this is the resolver's confirmed
    ``current_balance``, proven penny-exact by the reconciliation oracle.

    Returns ``None`` when the loan has no OPENING posting in the scenario (an
    unconfigured loan -- :func:`_has_opening_posting`), so the caller routes to
    its needs-setup path rather than showing a misleading ``$0``.  A configured
    loan whose ``as_of`` precedes its OPENING (a mid-life import read before
    tracking began) returns ``Decimal("0.00")`` -- see the caveat below.

    **Caveat: $0.00 before the opening is "no record", not "no debt".**  For a loan
    whose opening IS its origination the two coincide and the zero is correct (the
    debt did not exist yet).  For a mid-life import -- whose opening is a
    ``tracking_start`` dated years after origination -- the loan DID exist and DID
    owe money before that date, and the ledger simply has no record of it.  A caller
    asking for such a date is asking a question the confirmed ledger cannot answer,
    and must not present the zero as a balance: the year-end summary did, and
    reported NEGATIVE principal paid on real data.  Bound the window to the loan's
    opening instead.

    **Domain: ``as_of <= today``.**  A future date is a forward projection, out
    of the confirmed ledger's domain; the reader RAISES rather than silently
    returning today's balance, so a caller that needs a projected balance is
    forced to route to :func:`app.services.loan_resolver.resolve_loan`.  (The
    per-period map, which DOES answer future periods -- by carrying the confirmed
    balance flat for the caller to overlay the projection on -- is
    :func:`confirmed_loan_balance_map`.)

    **Do NOT ask this about a loan that has not ORIGINATED by *as_of*; it answers
    WRONG, and its callers are what stop that (N-10).**  An anchor's visible-on
    date is its pay period's START (:func:`._asof.effective_date`), so a
    future-dated origination is visible from a PAST date: this reader answers a
    2026-03-25 loan's full $200,000.00 principal when asked about 2026-03-20.  The
    honest bound is the anchor's own civil date, which moves history and so waits
    for step C2; until then every caller asks the FACT (``origination_date``)
    first -- :func:`app.services.net_worth_kernel.amortizing_balance_at` and
    :func:`app.services.loan_payment_service.confirmed_loan_view` are the only
    two, and both do.  A new caller must too.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose confirmed balance to read.
        scenario_id: The budget scenario to scope to (postings are
            scenario-scoped via ``journal_entries.scenario_id``).
        as_of: The evaluation date; must be on or before ``date.today()``.  Only
            postings whose pay period has begun by it are summed.

    Returns:
        The confirmed balance owed as a cent-quantized ``Decimal``, or ``None``
        when the loan has no opening posting in the scenario.

    Raises:
        ValueError: If *as_of* is after ``date.today()`` (out of the confirmed
            reader's domain -- route a future date to the forward projection).
        PostingError: If the loan account has no linked ledger account (a broken
            chart-of-accounts pairing -- from :func:`._ledger_account_for`).
    """
    if as_of > date.today():
        raise ValueError(
            f"confirmed_loan_balance_at answers only as_of <= today; got "
            f"{as_of.isoformat()}.  A future date is a forward projection -- "
            f"route it to resolve_loan, not the confirmed ledger."
        )
    linked = _ledger_account_for(loan_account_id)
    if not _has_opening_posting(linked.id, scenario_id):
        return None
    net = (
        scope_to_linked_ledger(
            db.session.query(
                db.func.coalesce(db.func.sum(Posting.amount), _ZERO_MONEY)
            ),
            linked.id, scenario_id,
        )
        .filter(effective_date() <= as_of)
        .scalar()
    )
    # Debit-positive ledger: the linked net is -(owed), so owed is its negation,
    # rounded once at the boundary (the legs are already cent-quantized, so this
    # only formalises the sign, matching the resolver's single round).  Written as
    # ``0 - net`` rather than ``-net`` so a zero net (a configured loan read before
    # its opening period begins) yields ``0.00``, never ``-0.00``.
    return round_money(_ZERO_MONEY - net)


def confirmed_loan_balance_map(
    loan_account_id: int, scenario_id: int, periods: list[PayPeriod],
) -> "OrderedDict[int, Decimal] | None":
    """Return each period's confirmed loan balance (genesis sum-of-postings).

    The batch, per-period form of :func:`confirmed_loan_balance_at`: for each
    :class:`~app.models.pay_period.PayPeriod` in *periods*, the confirmed balance
    is ``round_money(-(cumulative linked-ledger net of every posting attributed
    to a period beginning on or before this one))`` -- identical to
    ``confirmed_loan_balance_at(loan, scenario, period.start_date)`` but from ONE
    grouped posting load plus a Python prefix sum, not a query per period.

    Because postings are period-ASSIGNED and periods are contiguous, this IS the
    canonical period-END-keyed loan balance: a posting's period start is a real
    boundary, so ``<= period.start`` and ``<= period.end`` select the identical set
    (a payment "due in this period" nets in as its period's posting either way).

    **Future periods carry flat** (the read-switch overlay contract): a period
    after today has no confirmed postings, so its cumulative -- and thus its
    balance -- equals the last confirmed period's.  The per-period read switch
    overlays the forward projection on those future periods; the map returns the
    carried-flat confirmed value for them rather than raising, so the caller can
    pass its whole display window in one call.  (The scalar reader, a single
    ambiguous point, raises on a future date instead.)

    Returns ``None`` when the loan has no OPENING posting in the scenario (an
    unconfigured loan), for the same reason as the scalar -- the caller routes to
    needs-setup, not a map of zeros.  A period preceding the opening's period
    gets ``Decimal("0.00")`` (nothing confirmed yet as of that period).

    **Do NOT ask this about a loan that has not ORIGINATED; it answers WRONG for
    the period its origination falls in** -- the full opening balance, from that
    period's START, for a loan that closes later in it (N-10; see
    :func:`confirmed_loan_balance_at` for the mechanism and the fix that retires
    it).  Its one caller,
    :func:`app.services.net_worth_kernel._build_amortizing_balance_map`, asks the
    FACT (``owed_from``) before reading this.  A new caller must too.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose per-period balances to read.
        scenario_id: The budget scenario to scope to.
        periods: The pay periods to key the map by (any order; the result keys
            by ``period.id`` in the given order).  Postings in periods OUTSIDE
            this list (e.g. an opening in an earlier period) are still counted in
            each period's cumulative -- the load is not restricted to *periods*.

    Returns:
        An ``OrderedDict`` mapping ``period.id`` to its cent-quantized confirmed
        balance, or ``None`` when the loan has no opening posting in the
        scenario.

    Raises:
        PostingError: If the loan account has no linked ledger account (from
            :func:`._ledger_account_for`).
    """
    linked = _ledger_account_for(loan_account_id)
    if not _has_opening_posting(linked.id, scenario_id):
        return None
    # One load: each date a posting BECOMES VISIBLE on
    # (:func:`._asof.effective_date` -- a pay-period start for cash, an anchor's
    # own civil date when it predates every period), with that date's net.
    # Ascending, so a single forward pass builds the prefix cumulative.
    grouped = _visible_nets(linked.id, scenario_id)
    boundaries: list[date] = []
    cumulative_at_boundary: list[Decimal] = []
    running = _ZERO_MONEY
    for visible_on, date_net in grouped:
        running += date_net
        boundaries.append(visible_on)
        cumulative_at_boundary.append(running)

    balances: "OrderedDict[int, Decimal]" = OrderedDict()
    for period in periods:
        # The cumulative net of every posting-bearing period starting on or
        # before this one: bisect_right gives the count of such boundaries, so
        # the last one's prefix is the answer (0 when none precede -- a period
        # before the loan's opening).
        count = bisect_right(boundaries, period.start_date)
        cumulative = (
            cumulative_at_boundary[count - 1] if count > 0 else _ZERO_MONEY
        )
        # ``0 - cumulative`` (not ``-cumulative``) so a pre-opening period's zero
        # cumulative yields ``0.00``, never ``-0.00`` (see the scalar reader).
        balances[period.id] = round_money(_ZERO_MONEY - cumulative)
    return balances


def confirmed_loan_interest_in_year(
    loan_account_id: int, scenario_id: int, year: int,
) -> Decimal | None:
    """Return a loan's ACTUAL interest PAID in a calendar year (genesis ledger).

    The tax-reporting read side of the genesis loan sub-ledger: the real
    interest a loan's confirmed payments actually paid during *year*, for
    Schedule A.  Each confirmed payment posts its accrued interest onto the
    loan's per-loan ``loan_interest`` Expense ledger (:mod:`._payments`); this
    sums that ACTUAL interest -- not the amortization schedule's replayed figure,
    which is wrong for an off-schedule (extra / short) payment -- so the
    deduction reflects the interest truly paid.

    **Attributed by each payment's CURRENT paid date in the DISPLAY timezone,
    not by the posting's entry date.**  Mortgage interest is deductible in the
    year it was PAID, so a payment's NET interest (its original split PLUS any
    later true-up / rate re-split delta) is attributed to
    :func:`app.utils.dates.to_display_civil_date` of its shadow's current
    ``paid_at`` -- the user's wall-clock day, per the L9 decision (2026-07-03):
    a settle clicked 8:05pm Eastern on Dec 31 deducts in the Dec 31 tax year,
    even though the stored ``entry_date`` books the Jan 1 it becomes in UTC
    (storage stays on the UTC rule; only this reading boundary converts).
    Grouping the legs by their payment shadow and attributing the NET (rather
    than summing each leg by its own entry date) is what makes this robust to a
    reversal: reverting a payment clears its ``paid_at``, so its reversal leg is
    dated at the pay-period start (the entry dating's NULL fallback), which can
    fall in a DIFFERENT year than the original leg -- but the payment's NET
    interest is then zero, so it drops out of every year cleanly rather than
    stranding a spurious +/- interest across the year boundary.  A re-settled or
    edited-paid-date payment likewise reports its net at its CURRENT paid date.

    Only ``loan_interest``-kind legs are summed, so escrow and payoff-refund legs
    (neither a Schedule A mortgage-interest deduction) never leak in.

    Returns ``None`` when the loan has no OPENING posting in the scenario (an
    unconfigured / un-backfilled loan, or a what-if the opening was never posted
    into -- :func:`_has_opening_posting`), so the caller falls back to the
    schedule rather than reporting a misleading ``$0``.  A configured loan with
    no confirmed interest in *year* returns ``Decimal("0.00")``.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose paid interest to sum.
        scenario_id: The budget scenario to scope to (postings are
            scenario-scoped via ``journal_entries.scenario_id``).
        year: The calendar year to sum interest paid within.

    Returns:
        The actual interest paid during *year* as a cent-quantized ``Decimal``,
        or ``None`` when the loan has no opening posting in the scenario.

    Raises:
        PostingError: If the loan account has no linked ledger account (from
            :func:`._ledger_account_for`).
    """
    linked = _ledger_account_for(loan_account_id)
    if not _has_opening_posting(linked.id, scenario_id):
        return None
    # Attribute each payment's net interest to its CURRENT paid date's year in
    # the DISPLAY timezone (the tax-correct basis per L9; see the docstring); the
    # shared attribution reads ``paid_at`` / period start back per shadow so a
    # since-cleared ``paid_at`` falls back to the period start the entry dating
    # used, and a reverted payment (net zero) drops out cleanly.
    return _attribute_net_by_shadow_to_year(
        _interest_net_by_shadow(loan_account_id, scenario_id), year,
    )


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
    it for the net / reversal / hard-delete semantics).  Shared by the tax
    reader (:func:`confirmed_loan_interest_in_year`, which attributes each net to
    its civil paid YEAR) and the history readers
    (:func:`confirmed_loan_history_rows` and
    :func:`confirmed_loan_payment_history`, which place each net on its
    payment's row), so the surfaces cannot drift on what counts as a payment's
    actual interest.

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
    basis of :func:`_interest_net_by_shadow`, so the paid-year principal and
    interest chips sum over the identical payment set.

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


def _attribute_net_by_shadow_to_year(
    net_by_shadow: dict[int, Decimal], year: int,
) -> Decimal:
    """Sum the per-shadow nets whose payment was PAID in *year* (display civil date).

    The paid-date attribution shared by the interest and principal in-year
    readers: each shadow's net is attributed to the civil year of its payment's
    display-timezone paid date (:func:`app.utils.dates.to_display_civil_date` of
    the shadow's current ``paid_at``, falling back to its pay-period start when
    ``paid_at`` is cleared) -- the L9 tax-correct basis (see
    :func:`confirmed_loan_interest_in_year`).  Reading ``paid_at`` and the period
    start back per shadow makes both readers robust to a reversal the same way: a
    reverted payment's net is zero, so it drops from every year cleanly.

    Args:
        net_by_shadow: ``{shadow transaction id: net Decimal}`` (interest or
            principal), from :func:`_interest_net_by_shadow` /
            :func:`_principal_net_by_shadow`.
        year: The calendar year to sum within.

    Returns:
        The cent-quantized sum of the nets paid in *year* (``0.00`` when none).
    """
    if not net_by_shadow:
        return _ZERO_MONEY
    shadows = (
        db.session.query(Transaction)
        .options(joinedload(Transaction.pay_period))
        .filter(Transaction.id.in_(net_by_shadow.keys()))
        .all()
    )
    total = _ZERO_MONEY
    for shadow in shadows:
        paid_date = to_display_civil_date(
            shadow.paid_at, shadow.pay_period.start_date,
        )
        if paid_date.year == year:
            total += net_by_shadow[shadow.id]
    return round_money(total)


def _linked_entry_nets(
    linked_ledger_id: int, scenario_id: int,
) -> list[tuple[date, int, int | None, int | None, Decimal]]:
    """Return each journal entry's net on a loan's linked ledger, with its keys.

    One grouped load of EVERY posting on the linked ledger in the scenario --
    the same total set the balance readers sum -- projected per journal entry
    as ``(entry_date, source_kind_id, transfer_id, transaction_id, net)``.
    The history reader classifies each net by its source and linkage
    (:func:`_classify_linked_nets`).  Reading the nets per entry -- rather
    than re-deriving splits from rates -- is what makes the history rows a
    READ of the ledger's actual legs, not a recomputation.

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


def _payment_lineage_transfer_ids(
    loan_account_id: int, scenario_id: int,
) -> set[int]:
    """Return the transfer ids of EVERY income shadow the loan has ever carried.

    The payment-LINEAGE set the history classification rests on: every
    transfer that has (or had) a loan-side income shadow on this loan in this
    scenario, with NO status filter and INCLUDING soft-deleted rows.  Broader
    than the confirmed set on purpose -- a reverted, cancelled, future-period,
    or soft-deleted payment's ledger entries are still payment lineage, and
    the classifier must recognise them to DROP them (their nets are either
    zero by the reverse-to-target discipline, or excluded by the balance
    readers' period bound), never mistake them for a genuine non-payment
    balance event whose two reversal dates would wobble the row balances.

    Args:
        loan_account_id: The loan whose payment lineage to enumerate.
        scenario_id: The budget scenario to scope to.

    Returns:
        The distinct ``transfer_id`` set of the loan's income shadows (any
        status, deleted included); empty when the loan never had a payment.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    rows = (
        db.session.query(Transaction.transfer_id)
        .filter(
            Transaction.account_id == loan_account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
        )
        .distinct()
        .all()
    )
    return {transfer_id for (transfer_id,) in rows}


def _classify_linked_nets(
    entry_nets: list[tuple[date, int, int | None, int | None, Decimal]],
    shadows: list[Transaction],
    lineage_transfer_ids: set[int],
    as_of: date,
) -> tuple[dict[int, Decimal], list[tuple[date, Decimal]]]:
    """Split the linked-ledger nets into per-payment principal and other events.

    Classification, per group, in precedence order:

    1. **A confirmed payment's net.**  ``transaction_id`` is a confirmed
       shadow's id (the split correction) or ``transfer_id`` is a confirmed
       shadow's transfer (the Step-2 cash leg): the net accumulates into that
       payment's principal -- by the balanced construction of the correction,
       a payment's total linked net IS its real principal.
    2. **Non-confirmed payment lineage -- DROPPED.**  A ``loan_payment``-source
       group whose shadow is not confirmed (reverted / cancelled /
       soft-deleted, all reversed to net zero; or SET-NULL residue of a hard
       delete), and a ``transfer``-source group whose transfer is in the
       loan's payment lineage but not confirmed (same states, plus a settled
       payment whose pay period has not begun by *as_of*).  Dropping is exact:
       the reversed states net to zero -- but at TWO entry dates, so keeping
       them as dated events would transiently corrupt the row balances between
       those dates -- and the future-period payment is exactly what the
       balance readers' period bound excludes.  A ``transfer``-source group
       with a NULL ``transfer_id`` (hard-delete residue, reversed to zero
       before the SET NULL) drops for the same reason, as does a
       ``transaction``-source group with a NULL ``transaction_id``.
    3. **A genuine non-payment balance event.**  The opening, each true-up, a
       transfer OUT of the loan (forbidden at creation since review R6 -- see
       :func:`app.services._transfer_loan_posting._reject_transfer_out_of_loan`
       -- so this arm now defends only any pre-guard legacy row), a raw settled
       transaction typed onto the loan account -- applied at its own
       ``entry_date``, with events dated after *as_of* dropped.  That bound is
       THIS reader's, and the write walk no longer has one to mirror: it records
       every anchor whatever its date and leaves the date decision here
       (:func:`app.services.loan_ledger.walk_loan_ledger`).  Dating each event at
       its ``entry_date`` still matches the walk's ORDERING, which is what keeps
       these rows and the posted ledger on one chronology.

    Args:
        entry_nets: The per-entry nets from :func:`_linked_entry_nets`.
        shadows: The confirmed payment shadows through *as_of*
            (:func:`app.services.loan_ledger.confirmed_shadows_through`).
        lineage_transfer_ids: Every payment transfer the loan has ever carried
            (:func:`_payment_lineage_transfer_ids`), confirmed or not.
        as_of: The evaluation date bounding the non-payment events.

    Returns:
        ``(principal_by_shadow, other_events)`` -- the per-shadow-id summed
        principal nets, and the ``(entry_date, net)`` non-payment events.
    """
    shadow_ids = {shadow.id for shadow in shadows}
    shadow_id_by_transfer = {shadow.transfer_id: shadow.id for shadow in shadows}
    principal_by_shadow: dict[int, Decimal] = {}
    other_events: list[tuple[date, Decimal]] = []
    for entry_date, source_kind_id, transfer_id, transaction_id, net in entry_nets:
        if transaction_id in shadow_ids:
            key = transaction_id
        elif transfer_id in shadow_id_by_transfer:
            key = shadow_id_by_transfer[transfer_id]
        else:
            if not _is_dropped_payment_residue(
                source_kind_id, transfer_id, transaction_id,
                lineage_transfer_ids,
            ) and entry_date <= as_of:
                other_events.append((entry_date, net))
            continue
        principal_by_shadow[key] = (
            principal_by_shadow.get(key, _ZERO_MONEY) + net
        )
    return principal_by_shadow, other_events


def _is_dropped_payment_residue(
    source_kind_id: int,
    transfer_id: int | None,
    transaction_id: int | None,
    lineage_transfer_ids: set[int],
) -> bool:
    """Return whether an unmatched linked-ledger group is dropped lineage/residue.

    The rule-2 predicate of :func:`_classify_linked_nets` (see its docstring
    for the full financial rationale), applied only to groups already known
    NOT to belong to a confirmed payment:

    * a ``loan_payment``-source group is ALWAYS payment lineage (only the
      split reconcile writes that source) -- a non-confirmed shadow's
      reversed-to-zero correction or a hard delete's SET-NULL residue;
    * a ``transfer``-source group drops when its ``transfer_id`` is in the
      loan's payment lineage (reverted / cancelled / soft-deleted /
      future-period payment cash) or NULL (hard-delete residue) -- a
      ``transfer_id`` OUTSIDE the lineage is a transfer out of the loan, a
      real balance event that is KEPT (that flow is forbidden at creation
      since review R6, so this KEEP arm now defends only a pre-guard legacy
      row);
    * a ``transaction``-source group with a NULL ``transaction_id`` is an
      ordinary transaction's hard-delete residue (reversed to zero before
      the SET NULL).

    Args:
        source_kind_id: The group's journal source kind id.
        transfer_id: The group's ``transfer_id`` (may be ``None``).
        transaction_id: The group's ``transaction_id`` (may be ``None``).
        lineage_transfer_ids: Every payment transfer the loan has ever
            carried (:func:`_payment_lineage_transfer_ids`).

    Returns:
        ``True`` when the group must be dropped from the history walk.
    """
    if source_kind_id == ref_cache.posting_source_id(
        PostingSourceEnum.LOAN_PAYMENT
    ):
        return True
    if source_kind_id == ref_cache.posting_source_id(PostingSourceEnum.TRANSFER):
        return transfer_id is None or transfer_id in lineage_transfer_ids
    if source_kind_id == ref_cache.posting_source_id(
        PostingSourceEnum.TRANSACTION
    ):
        return transaction_id is None
    return False


def _replay_history_events(
    events: list[tuple[date, int, object]],
    principal_by_shadow: dict[int, Decimal],
    interest_by_shadow: dict[int, Decimal],
    periods: list[RatePeriod],
    params: LoanParams,
) -> list[AmortizationRow]:
    """Walk the merged history events into ledger-derived schedule rows.

    The running-balance heart of :func:`confirmed_loan_history_rows`, factored
    out so the loader stays within the locals limit.  Accumulates the
    cumulative linked net event by event -- a non-payment event (tag 1) just
    moves the balance; a payment event (tag 0) emits one
    :class:`AmortizationRow` carrying its actual ledger economics and the
    post-payment running balance (see the caller for the full field
    semantics).

    Args:
        events: The merged ``(event_date, tag, item)`` stream in walk order
            (``item`` is a payment shadow when ``tag == 0``, else a linked
            net ``Decimal``).
        principal_by_shadow: Each payment's summed linked net (its real
            principal), keyed by shadow id.
        interest_by_shadow: Each payment's net posted interest, keyed by
            shadow id.
        periods: The loan's rate periods (each row's governing rate and
            contractual P&I).
        params: The loan's :class:`~app.models.loan_params.LoanParams`
            (``origination_date`` numbers the rows).

    Returns:
        The chronological confirmed :class:`AmortizationRow` list.
    """
    linked_sum = _ZERO_MONEY
    rows: list[AmortizationRow] = []
    for event_date, tag, item in events:
        if tag == 1:
            linked_sum += item
            continue
        shadow = item
        principal = round_money(
            principal_by_shadow.get(shadow.id, _ZERO_MONEY)
        )
        interest = round_money(
            interest_by_shadow.get(shadow.id, _ZERO_MONEY)
        )
        linked_sum += principal
        period = period_for_date(periods, shadow.pay_period.start_date)
        extra = max(principal + interest - period.period_pi, _ZERO_MONEY)
        rows.append(AmortizationRow(
            month=payment_number(params.origination_date, event_date),
            payment_date=event_date,
            payment=round_money(principal + interest - extra),
            principal=principal,
            interest=interest,
            extra_payment=round_money(extra),
            # Debit-positive ledger: owed is the negated cumulative linked
            # net, ``0 - sum`` so a zero cumulative reads 0.00, never -0.00.
            remaining_balance=round_money(_ZERO_MONEY - linked_sum),
            is_confirmed=True,
            interest_rate=period.annual_rate,
        ))
    return rows


def _confirmed_history_inputs(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> "tuple[LoanParams, LedgerAccount, list[Transaction]] | None":
    """Load the shared inputs of the confirmed history producers, or None.

    The common entry guard + load behind both confirmed-history surfaces -- the
    amortization rows (:func:`confirmed_loan_history_rows`) and the payment-history
    table (:func:`._display.confirmed_loan_payment_history`): a configured loan
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


def confirmed_loan_history_rows(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> list[AmortizationRow] | None:
    """Return a loan's confirmed history as schedule rows read from the ledger.

    The ledger-derived amortization HISTORY adapter (the read switch's final
    surface): one :class:`~app.services.amortization_engine.AmortizationRow`
    per confirmed payment whose pay period has begun by *as_of*, chronological,
    each carrying the payment's ACTUAL economics read from the posted ledger
    legs -- never the resolver's contractual replay, which shows only scheduled
    principal / interest and is therefore wrong for an off-schedule payment:

    * ``interest`` -- the payment's net ``loan_interest`` legs
      (:func:`_interest_net_by_shadow`), the real accrual its split posted.
    * ``principal`` -- the payment's net on the LINKED ledger (its Step-2 cash
      leg plus its split correction), which by the balanced construction of
      the correction is exactly the real debt paid down (may be negative for
      an underpayment; excludes a payoff overpayment's Refund excess).
    * ``remaining_balance`` -- the genesis running balance ``-(cumulative
      linked net)`` after this payment, so the opening, every true-up, and any
      other linked posting move the row balances exactly as they move the
      balance readers.
    * ``payment`` / ``extra_payment`` -- the actual P&I split against the
      governing period's contractual P&I under the schedule-row invariant
      ``principal + interest == payment + extra_payment`` (the same algebra a
      projected row with extra uses), so the schedule table's totals need no
      per-row special-casing: ``payment`` is the contractual-shaped portion,
      ``extra_payment`` the actual excess above it.

    Row DATING mirrors the resolver's replay exactly: each row is dated at the
    installment the payment satisfies
    (:func:`app.services.loan_loaders.loan_payment_due_date` -- the shadow's own
    stored ``due_date``, NOT a derivation from its pay period, so a payment
    settled late is still dated at the installment it paid rather than at the
    NEXT month's), numbered continuously from origination (:func:`payment_number`),
    and tagged with the governing period's rate.  Event ORDER mirrors the write
    walk (:func:`app.services.loan_ledger.merge_anchor_and_payment_events`): payments by due
    date, non-payment balance events at their entry date, a payment sorting
    BEFORE a same-date event so a true-up dated on a due date subsumes the
    payment it follows.  On an on-schedule loan every row is therefore
    byte-identical to the replay's row -- EXCEPT across a biweekly due-month
    collision, where the replay's display redistribution shifts the second
    payment to the next month while the ledger row keeps the true due date
    (two same-month payments show as two rows in that month: more truthful,
    and the balances agree) -- and off-schedule the rows show what actually
    happened.

    Returns ``None`` when the loan has no :class:`LoanParams` or no OPENING
    posting in the scenario (unconfigured / un-backfilled / a what-if never
    posted into), so the caller keeps the resolver's replay rows -- the same
    fallback contract as :func:`confirmed_loan_balance_at`.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose confirmed history to read.
        scenario_id: The budget scenario to scope to.
        as_of: The evaluation date; must be on or before ``date.today()``.
            A payment whose pay period has not begun by it is a forward
            projection, excluded (its row belongs to the projection).

    Returns:
        The chronological confirmed :class:`AmortizationRow` list (possibly
        empty for a configured loan with no confirmed payment yet), or
        ``None`` when the ledger cannot answer for this loan / scenario.

    Raises:
        ValueError: If *as_of* is after ``date.today()`` (out of the confirmed
            reader's domain -- route a future date to the forward projection).
        PostingError: If the loan account has no linked ledger account (from
            :func:`._ledger_account_for`).
    """
    if as_of > date.today():
        raise ValueError(
            f"confirmed_loan_history_rows answers only as_of <= today; got "
            f"{as_of.isoformat()}.  A future date is a forward projection -- "
            f"route it to resolve_loan, not the confirmed ledger."
        )
    inputs = _confirmed_history_inputs(loan_account_id, scenario_id, as_of)
    if inputs is None:
        return None
    params, linked, shadows = inputs
    principal_by_shadow, other_events = _classify_linked_nets(
        _linked_entry_nets(linked.id, scenario_id),
        shadows,
        _payment_lineage_transfer_ids(loan_account_id, scenario_id),
        as_of,
    )
    interest_by_shadow = _interest_net_by_shadow(loan_account_id, scenario_id)
    periods = loan_resolver.resolve_periods(
        params, loan_loaders.load_rate_changes(loan_account_id),
    )

    # One chronological walk, mirroring the write walk's merge: payments keyed
    # by due date with tag 0, non-payment events by entry date with tag 1, so a
    # payment due exactly on an event's date is walked before it.  The stable
    # sort keeps the shadows' (pay-period start, id) order on equal keys.
    events: list[tuple[date, int, object]] = [
        (
            loan_loaders.loan_payment_due_date(shadow, params.payment_day),
            0,
            shadow,
        )
        for shadow in shadows
    ] + [
        (event_date, 1, net) for event_date, net in other_events
    ]
    events.sort(key=lambda event: (event[0], event[1]))

    return _replay_history_events(
        events, principal_by_shadow, interest_by_shadow, periods, params,
    )
