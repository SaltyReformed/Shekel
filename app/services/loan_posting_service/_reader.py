"""Loan confirmed-balance reader: the genesis sum-of-postings read side.

The read half of the genesis loan sub-ledger.  The write modules
(:mod:`._payments`, :mod:`._anchors`, :mod:`._sync`) post a loan's OPENING,
every confirmed payment's split, and every balance TRUE-UP onto ONE linked
ledger account (:func:`app.services.posting_service._ledger_account_for`); this
module reads them back, so a loan's confirmed balance is::

    owed(as_of) = round_money(-(sum of the loan's linked-ledger postings whose
                                 entry_date is on or before as_of))

with no external anchor read and no post-anchor eligibility filter -- the plain
sum the read-switch arc exists to reach, superseding the resolver's read-time
replay of confirmed history.  Because every source posts onto the one linked
ledger, at ``as_of = date.today()`` this equals
``round_money(-posting_service.account_posting_total(loan, scenario))`` -- the
quantity the reconciliation oracle grades against the independent anchor
replay (and B2's fold, which the seam reads); the ``entry_date`` bound
generalises it to any historical date, and the per-period map applies it at
every period boundary.

**The as-of bound is ONE clock: each posting counts from its ``entry_date`` --
the day the event it records happened** (step C2).  The writer already stamps
that day honestly: a payment's cash and split legs carry its SETTLED date
(:func:`app.services.posting_service._civil_settle_date`), an anchor correction
carries the ``anchor_date`` it asserts
(:func:`app.services._posting_reconcile.emit_anchor_correction_entry`).  So
``entry_date <= as_of`` selects exactly the events that have happened by *as_of*,
with no per-source special-casing and no pay-period join -- the same cut the fold
applies from source (:func:`app.services.balance_at._fold.fold_loan_balances`, whose
visible-on rule is the SAME settled-date / anchor-date derivation), which is what
keeps the two equal on every day (step B2).

Before C2 the reader bounded cash by its pay period's ``start_date`` and an
anchor by ``LEAST(entry_date, period.start)`` -- two boundary predicates standing
in for the instant, and the anchor one made a future-dated opening visible early
(N-10).  The per-period reader (:func:`confirmed_loan_balance_map`) sums the
postings whose ``entry_date`` falls on or before each period's END, so it answers
the same period-END balance the scalar reports at that date.

**Wiring status.**  The current-balance scalar (:func:`confirmed_loan_balance_at`)
and the per-period map (:func:`confirmed_loan_balance_map`) are no longer the
production balance surface: the balance seam cut its loan reads over to the event
FOLD (:func:`app.services.balance_at.positions`; the scalar at plan step C3b1,
the map at C3b3), so a cold posting cache is a repairable inconsistency, not a
read outage.  They read the POSTING ledger as the general ledger now -- the
reconciliation oracle's independent window onto the postings
(``tests/test_integration/test_posting_ledger_loan_reconciliation.py``), and the
checked projection the fold validates at write time (plan E1).  The history rows
keep their own consumers.  The paid-in-year tax / chip figures moved OFF the
postings onto the fold (steps C3c / C6c:
:func:`app.services.balance_at.loan_interest_in_year`,
:func:`~app.services.balance_at.loan_interest_paid_in_year`,
:func:`~app.services.balance_at.loan_principal_paid_in_year`), so this module no
longer answers them.  Reads only -- no writes, no commit.
"""

from bisect import bisect_right
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import loan_loaders
from app.services.loan_ledger import confirmed_shadows_through
from app.services.posting_service import _ledger_account_for
from app.utils.money import round_money

from ._linked_ledger import _has_opening_posting, _visible_nets

_ZERO_MONEY = Decimal("0.00")


def confirmed_loan_balance_at(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> Decimal | None:
    """Return a loan's confirmed balance as of a past date (genesis sum-of-postings).

    ``owed(as_of) = round_money(-(sum of the loan's linked-ledger postings,
    scenario-scoped, whose ``entry_date`` is on or before as_of))`` -- the opening
    (``-original_principal``), every confirmed payment's net principal (the
    Step-2 cash leg plus the Step-4 split correction), and every true-up, with
    no kind filter and no eligibility lower bound (see the module docstring).
    At ``as_of = date.today()`` this is the loan's confirmed balance, graded
    penny-exact by the reconciliation oracle's independent replay window and
    equal to the seam's fold on every day (B2).

    Returns ``None`` when the loan has no OPENING posting in the scenario (an
    unconfigured loan -- :func:`_has_opening_posting`), so the caller routes to
    its needs-setup path rather than showing a misleading ``$0``.  A configured
    loan whose ``as_of`` precedes its ORIGINATION returns ``Decimal("0.00")`` --
    the correct fold of an empty prefix: the debt did not exist yet.

    **The opening IS the loan's origination (step C1), so a returned $0.00 means
    "no debt".**  A mid-life import opens at its origination like any loan; its
    ``tracking_start`` is an ordinary true-up
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`) that RESETS the
    balance at its own date.  A date between origination and the tracking-start
    therefore reads the origination opening held FLAT -- the honest pre-tracking
    plateau (B-11) -- never ``$0.00``.  Before C1 the ledger opened at the
    tracking-start, so that whole window read a false ``$0.00`` a change-across-a-
    window caller misread as "no debt": the year-end summary reported NEGATIVE
    principal paid on real data.  Opening at origination closes that at the source.

    **Domain: ``as_of <= today``.**  A future date is a forward projection, out
    of the confirmed ledger's domain; the reader RAISES rather than silently
    returning today's balance, so a caller that needs a projected balance is
    forced to route to :func:`app.services.loan_resolver.resolve_loan`.  (The
    per-period map, which DOES answer future periods -- by carrying the confirmed
    balance flat for the caller to overlay the projection on -- is
    :func:`confirmed_loan_balance_map`.)

    **A loan that has not ORIGINATED by *as_of* reads ``0.00`` -- the honest fold
    of an empty prefix, no longer the N-10 leak.**  The opening correction carries
    the ``origination_date`` in its ``entry_date`` (step C2's one clock), so a
    future-dated origination is simply not selected by ``entry_date <= as_of``:
    a 2026-03-25 loan asked about 2026-03-20 sums nothing and returns ``0.00``.
    Before C2 the reader bounded the opening by its pay period's START and so
    handed back the full $200,000.00 principal five days early (N-10); the one
    clock closes that leak at the SOURCE, so the four ``origination_date`` guards
    that contained it are now redundant belt-and-braces (deleted at C3 with
    ``owed_from`` -- except the confirmed VIEW's, which stays for a reason
    independent of the clock: its ``0.00`` must not seed the forward projection,
    B-1).  A caller that must tell "owed nothing" from "no loan" still asks the
    FACT (``origination_date``), never this ``0.00`` -- but it is now the RIGHT zero.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose confirmed balance to read.
        scenario_id: The budget scenario to scope to (postings are
            scenario-scoped via ``journal_entries.scenario_id``).
        as_of: The evaluation date; must be on or before ``date.today()``.  Only
            postings whose ``entry_date`` is on or before it are summed.

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
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), _ZERO_MONEY)
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.entry_date <= as_of,
        )
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
    is ``round_money(-(cumulative linked-ledger net of every posting whose
    ``entry_date`` falls on or before this period's END))`` -- equal to
    ``confirmed_loan_balance_at(loan, scenario, period.end_date)`` for a past
    period (the scalar RAISES for a future ``period.end``, which is why this map
    exists to answer the future periods carried flat), and computed from ONE
    grouped posting load plus a Python prefix sum, not a query per period.

    **Keyed by period END** (step C2), which matches the period-END balance the
    forward projection reports (:func:`app.services.balance_at.positions` keys the
    same way), so the fold the seam now reads and this postings map agree at every
    period boundary.  Under the one clock a posting carries its own event date,
    which can fall mid
    period, so a payment settled during period P must count in P's balance -- which
    ``entry_date <= period.end`` selects and ``<= period.start`` would miss.  (Pre
    C2 every posting was dated at a period START, so the two bounds coincided; they
    no longer do.)

    **Future periods carry flat** (the read-switch overlay contract): every
    confirmed posting has an ``entry_date`` on or before today, hence on or before
    any future period's end, so a future period's cumulative -- and thus its
    balance -- equals the last confirmed period's.  The per-period read switch
    overlays the forward projection on those future periods; the map returns the
    carried-flat confirmed value for them rather than raising, so the caller can
    pass its whole display window in one call.  (The scalar reader, a single
    ambiguous point, raises on a future date instead.)

    Returns ``None`` when the loan has no OPENING posting in the scenario (an
    unconfigured loan), for the same reason as the scalar -- the caller routes to
    needs-setup, not a map of zeros.  A period ending before the origination
    gets ``Decimal("0.00")`` (nothing confirmed yet as of that period).

    A loan that has not ORIGINATED reads ``0.00`` for every period ending before
    its future origination date -- the honest empty-prefix fold, no longer the
    N-10 leak (the opening's ``entry_date`` is the future origination, so
    ``entry_date <= period.end`` selects nothing).  It has no PRODUCTION caller
    since the balance seam's per-period map cut over to the event fold
    (:func:`app.services.balance_at.positions_period_map`, plan step C3b3); the
    reconciliation oracle reads it as an independent window onto the postings,
    proving they project the fold at every period boundary (kept for that until
    plan E1 designs the general ledger's read/reconcile surface).

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
    # One load: each date a posting became visible on -- its ``entry_date``, the
    # settled date of a payment or the assert date of an anchor (step C2's one
    # clock) -- with that date's net.  Ascending, so a single forward pass builds
    # the prefix cumulative.
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
        # The cumulative net of every posting whose ``entry_date`` falls on or
        # before this period's END (step C2 -- period-END keyed, so a payment
        # settled MID-period counts in it): bisect_right gives the count of such
        # boundaries, so the last one's prefix is the answer (0 when none precede
        # -- a period ending before the loan's opening).
        count = bisect_right(boundaries, period.end_date)
        cumulative = (
            cumulative_at_boundary[count - 1] if count > 0 else _ZERO_MONEY
        )
        # ``0 - cumulative`` (not ``-cumulative``) so a pre-opening period's zero
        # cumulative yields ``0.00``, never ``-0.00`` (see the scalar reader).
        balances[period.id] = round_money(_ZERO_MONEY - cumulative)
    return balances


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
