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

**The pay-period-start bound is period assignment, not a boundary rule.**  Each
posting is attributed to a whole pay period (its journal entry's NOT NULL
``pay_period_id``), and pay periods are contiguous, so bounding by
``pay_period.start_date <= as_of`` selects exactly the postings whose period has
begun -- the same confirmed cut the walk (:func:`._walk.walk_loan_ledger`)
applied when it produced them, not a recomputed special case.  This is why the
per-period map (keyed by period start) IS the canonical period-END-keyed loan
balance (:func:`app.services.account_projection.compute_loan_period_balance_map`):
a posting's period start is a real boundary and periods are contiguous, so
``<= period.start`` and ``<= period.end`` select the identical posting set.

**Inert.**  Nothing reads these functions yet; the current-balance read switch
and the per-period map read switch wire them in later commits (the reader joins
the balance-producer fence then).  Reads only -- no writes, no commit.
"""

from bisect import bisect_right
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.pay_period import PayPeriod
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


def _scope_to_linked_ledger(query, linked_ledger_id: int, scenario_id: int):
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
    loan whose ``as_of`` precedes its opening period (a within-range loan read
    before it originated) returns ``Decimal("0.00")`` -- configured, but nothing
    confirmed yet as of that date.

    **Domain: ``as_of <= today``.**  A future date is a forward projection, out
    of the confirmed ledger's domain; the reader RAISES rather than silently
    returning today's balance, so a caller that needs a projected balance is
    forced to route to :func:`app.services.loan_resolver.resolve_loan`.  (The
    per-period map, which DOES answer future periods -- by carrying the confirmed
    balance flat for the caller to overlay the projection on -- is
    :func:`confirmed_loan_balance_map`.)

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
        _scope_to_linked_ledger(
            db.session.query(
                db.func.coalesce(db.func.sum(Posting.amount), _ZERO_MONEY)
            ),
            linked.id, scenario_id,
        )
        .filter(PayPeriod.start_date <= as_of)
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
    canonical period-END-keyed loan balance
    (:func:`app.services.account_projection.compute_loan_period_balance_map`): a
    posting's period start is a real boundary, so ``<= period.start`` and
    ``<= period.end`` select the identical set (a payment "due in this period"
    nets in as its period's posting either way).

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
    # One load: each pay-period start carrying a posting on the linked ledger,
    # with that period's net.  Grouped by start_date (unique per user's periods),
    # ascending, so a single forward pass builds the prefix cumulative.
    grouped = (
        _scope_to_linked_ledger(
            db.session.query(PayPeriod.start_date, db.func.sum(Posting.amount)),
            linked.id, scenario_id,
        )
        .group_by(PayPeriod.start_date)
        .order_by(PayPeriod.start_date)
        .all()
    )
    boundaries: list[date] = []
    cumulative_at_boundary: list[Decimal] = []
    running = _ZERO_MONEY
    for start_date, period_net in grouped:
        running += period_net
        boundaries.append(start_date)
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
