"""
Shekel Budget App -- Loan Data Loaders (the loan services' leaf layer)

The pure data-loading functions every loan consumer shares: the
:class:`LoanParams` / :class:`LoanAnchorEvent` / :class:`RateHistory` /
:class:`~app.models.escrow_line.EscrowLine` row loaders and the shadow-income
query builder.
Extracted from :mod:`app.services.loan_payment_service` (the read switch's
final arc) so the loan POSTING package and the loan PAYMENT service both
depend on one leaf module instead of on each other: the posting package's
walk and reader need these loaders, while ``loan_payment_service`` hosts the
read-switch seam that imports the posting package's reader -- loading through
a shared leaf is what keeps that dependency one-directional (no import
cycle), rather than a lazy-import workaround.

This module is a LEAF: it imports models, the pure engine primitives
(:class:`~app.services.amortization_engine.RateChangeRecord`,
:func:`~app.services.rate_period_engine.monthly_due_date`), and the shared
balance predicates -- never another loan service.  Flask-isolated, reads only,
no commits.

This service queries ONLY budget.transactions (transfer invariant #5).
It NEVER queries budget.transfers.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app import ref_cache
from app.enums import LoanAnchorSourceEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.escrow_line import EscrowLine
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.models.transaction import Transaction
from app.services.amortization_engine import RateChangeRecord
from app.services.rate_period_engine import monthly_due_date
from app.utils.balance_predicates import (
    balance_excluded_status_ids,
    settled_status_ids,
)

# The synthesized origination anchor's created_at: the earliest possible
# instant (UTC-aware, comparable with the timestamptz ``created_at`` of real
# user-trueup rows), so a true-up asserted ON the origination date still wins
# the resolver's ``(anchor_date, created_at)`` latest-anchor tie-break --
# exactly as the stored origination row (created at loan setup, before any
# true-up) did.
_ORIGINATION_CREATED_AT = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class LoanAnchorFact:
    """One dated balance assertion for a loan, as plain data.

    The single anchor shape BOTH anchor consumers walk (the read switch's
    final commit): the genesis posting walk derives its opening / true-up
    corrections from these, and the loan resolver's replay fallback consumes
    them as its duck-typed anchor events (it reads ``anchor_date`` /
    ``anchor_balance`` / ``created_at``, all here).  Two provenances:

    * **The origination anchor is SYNTHESIZED from the immutable
      :class:`LoanParams`** (``origination_date`` / ``original_principal``)
      rather than read from a stored row -- the origination
      :class:`LoanAnchorEvent` write is retired, since that row was always a
      verbatim copy of the params (verified on production data).  Legacy
      stored origination rows are ignored, not migrated: append-only history,
      value-identical to the synthesis.
    * **A user true-up is a real stored fact** (the ``user_trueup``
      :class:`LoanAnchorEvent` the balance-edit flow appends): the operator's
      dated balance assertion, the source document the self-healing TRUEUP
      correction is derived from and re-derived against.

    Attributes:
        account_id: The loan account the assertion belongs to.
        anchor_date: The date the balance was asserted (origination date for
            the opening).
        anchor_balance: The asserted balance owed (the original principal for
            the opening), cent-quantized ``Decimal``.
        is_opening: ``True`` for the loan's single opening anchor (the
            synthesized origination, or the ``tracking_start`` event when a
            mid-life import recorded one -- see
            :func:`_opening_anchor_fact`), ``False`` for a user true-up --
            drives the OPENING vs TRUEUP posting kinds.
        created_at: The assertion's creation instant (the latest-anchor
            tie-break); the synthesized origination uses the earliest
            possible UTC instant so any same-day true-up wins.
        is_tracking_start: ``True`` only for an ``is_opening`` fact synthesized
            from a ``tracking_start`` event (a mid-life import), ``False`` for
            the origination opening and every true-up.  Display provenance only
            (the drift scorecard labels the opening row); the balance math never
            branches on it.
    """

    account_id: int
    anchor_date: date
    anchor_balance: Decimal
    is_opening: bool
    created_at: datetime
    is_tracking_start: bool = False


def _opening_anchor_fact(
    params: LoanParams,
    tracking_start_events: list[LoanAnchorEvent],
) -> LoanAnchorFact:
    """Return a loan's single ``is_opening`` fact -- tracking-start, else origination.

    The genesis ledger opens at exactly one anchor.  For a mid-life-imported loan
    that recorded a ``tracking_start`` event (the operator's real balance as of a
    date at/before the first recorded payment), the opening is synthesized from
    that event -- so the confirmed ledger opens at the recent known balance and
    there is no fictional origination-to-tracking-start plateau, and recorded
    payments accrue interest on the correct balance.  For every other loan the
    opening is synthesized from the immutable :class:`LoanParams`
    (``origination_date`` / ``original_principal``) exactly as before -- a
    complete-data or single-origination loan is byte-identical.

    When more than one ``tracking_start`` event exists (an operator correction --
    the table is append-only), the LATEST by ``created_at`` wins (the most
    recently asserted genesis supersedes earlier ones, mirroring how a correcting
    true-up supersedes an earlier one).  The opening fact always carries
    :data:`_ORIGINATION_CREATED_AT` (the earliest instant) for the
    ``(anchor_date, created_at)`` latest-anchor tie-break, so a true-up asserted
    on the very opening date still outranks it -- the same rule the synthesized
    origination uses.

    Args:
        params: The loan's :class:`LoanParams` row (the account id and the
            immutable origination fields the fallback opening uses).
        tracking_start_events: The loan's ``tracking_start``
            :class:`LoanAnchorEvent` rows (possibly empty).

    Returns:
        The single opening :class:`LoanAnchorFact` (``is_opening=True``).
    """
    is_tracking_start = bool(tracking_start_events)
    if is_tracking_start:
        latest = max(tracking_start_events, key=lambda event: event.created_at)
        anchor_date = latest.anchor_date
        anchor_balance = Decimal(str(latest.anchor_balance))
    else:
        anchor_date = params.origination_date
        anchor_balance = Decimal(str(params.original_principal))
    return LoanAnchorFact(
        account_id=params.account_id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        is_opening=True,
        created_at=_ORIGINATION_CREATED_AT,
        is_tracking_start=is_tracking_start,
    )


def load_loan_anchor_facts(params: LoanParams) -> list[LoanAnchorFact]:
    """Return a loan's anchor facts: the opening + true-ups.

    The one anchor loader every consumer shares (the genesis walk and every
    resolver-input builder), so no two sites can disagree on what a loan's
    anchors are.  The single ``is_opening`` anchor is synthesized by
    :func:`_opening_anchor_fact` -- from the loan's ``tracking_start`` event when
    one exists (a mid-life import), else from the immutable *params* (the
    origination, never read from a stored row -- see :class:`LoanAnchorFact`).
    The user true-ups are the loan's ``user_trueup`` :class:`LoanAnchorEvent`
    rows, in no guaranteed order (consumers sort by ``(anchor_date, created_at)``
    where order matters).

    Args:
        params: The loan's :class:`LoanParams` row (supplies the account id
            and the immutable origination fields).

    Returns:
        The :class:`LoanAnchorFact` list -- always non-empty (the opening
        fact is always first), so a configured loan is always resolvable.
    """
    trueup_source_id = ref_cache.loan_anchor_source_id(
        LoanAnchorSourceEnum.USER_TRUEUP,
    )
    tracking_start_source_id = ref_cache.loan_anchor_source_id(
        LoanAnchorSourceEnum.TRACKING_START,
    )
    events = (
        db.session.query(LoanAnchorEvent)
        .filter(
            LoanAnchorEvent.account_id == params.account_id,
            LoanAnchorEvent.source_id.in_(
                [trueup_source_id, tracking_start_source_id],
            ),
        )
        .all()
    )
    trueup_events = [
        event for event in events if event.source_id == trueup_source_id
    ]
    tracking_start_events = [
        event for event in events if event.source_id == tracking_start_source_id
    ]

    facts = [_opening_anchor_fact(params, tracking_start_events)]
    facts.extend(
        LoanAnchorFact(
            account_id=event.account_id,
            anchor_date=event.anchor_date,
            anchor_balance=Decimal(str(event.anchor_balance)),
            is_opening=False,
            created_at=event.created_at,
        )
        for event in trueup_events
    )
    return facts


def synthesize_origination_anchor(params: LoanParams) -> LoanAnchorFact:
    """Return a loan's ORIGINATION anchor, synthesized from its immutable params.

    The origination-dated opening -- ``(origination_date, original_principal)``
    -- ALWAYS, regardless of any ``tracking_start`` event.  This is deliberately
    NOT :func:`load_loan_anchor_facts`' opening fact, which a mid-life import's
    ``tracking_start`` supersedes (:func:`_opening_anchor_fact`): a caller that
    needs the loan's schedule FROM ORIGINATION -- the contractual back-projection
    that fills a tracking-start loan's pre-tracking months
    (:func:`app.services.loan_resolution.contractual_schedule_from_origination`)
    -- must seed from origination, not the recent tracking-start balance, or the
    pre-tracking debt would be a flat plateau instead of a real amortization
    curve.  Reuses the origination synthesis by passing NO tracking-start events,
    so the origination anchor stays defined in exactly one place.

    Pure: reads only the immutable *params* fields, no query.

    Args:
        params: The loan's :class:`LoanParams` row (supplies the account id and
            the immutable ``origination_date`` / ``original_principal``).

    Returns:
        The origination :class:`LoanAnchorFact` (``is_opening=True``,
        ``is_tracking_start=False``).
    """
    return _opening_anchor_fact(params, [])


def _rate_change_records_from(
    rate_history_records: list,
) -> list[RateChangeRecord] | None:
    """Convert loaded RateHistory rows to the engine's RateChangeRecord feed.

    The pure (no-DB) half of rate-change loading, shared by
    :func:`app.services.loan_payment_service.load_loan_context` (which also
    keeps the raw ORM rows for its ``rate_history`` display field) and
    :func:`load_rate_changes` (which needs only the feed), so the two cannot
    drift on how a :class:`RateHistory` row maps to a
    :class:`RateChangeRecord`.  Returns ``None`` -- not an empty list -- for no
    rows: the resolver treats ``None`` and an empty feed identically (an
    origination-row-less loan is unresolvable), and the explicit ``None``
    keeps the established contract a loan with no RateHistory has no feed at
    all.

    Args:
        rate_history_records: The loan's :class:`RateHistory` ORM rows (any
            order; each exposes ``effective_date`` / ``interest_rate`` /
            optional ``monthly_pi``).

    Returns:
        The :class:`RateChangeRecord` list, or ``None`` when there are no rows.
    """
    if not rate_history_records:
        return None
    return [
        RateChangeRecord(
            effective_date=rh.effective_date,
            interest_rate=Decimal(str(rh.interest_rate)),
            monthly_pi=(
                Decimal(str(rh.monthly_pi))
                if rh.monthly_pi is not None else None
            ),
        )
        for rh in rate_history_records
    ]


def load_rate_history(account_id: int) -> list:
    """Load a loan's raw :class:`RateHistory` rows, newest first.

    The one query definition behind BOTH rate-history consumers: the
    feed-only loader (:func:`load_rate_changes`) and
    :func:`app.services.loan_payment_service.load_loan_context`, which keeps
    the raw ORM rows for its ``rate_history`` display field alongside the
    mapped feed -- so the two cannot drift on how a loan's rate history is
    read (ordering, soft-delete handling).

    Args:
        account_id: The loan account whose rate history to load.

    Returns:
        The account's :class:`RateHistory` rows, ``effective_date`` DESC
        (possibly empty for an unconfigured loan).
    """
    return (
        db.session.query(RateHistory)
        .filter_by(account_id=account_id)
        .order_by(RateHistory.effective_date.desc())
        .all()
    )


def load_rate_changes(account_id: int) -> list[RateChangeRecord] | None:
    """Load a loan's rate-change feed (origination row plus any ARM adjustments).

    Queries the account's :class:`RateHistory` rows (newest first, the same
    order :func:`app.services.loan_payment_service.load_loan_context` uses)
    and maps them to the engine's :class:`RateChangeRecord` feed via
    :func:`_rate_change_records_from`.  The standalone loader for callers that
    need ONLY the feed -- the Build-Order Step 4 split walk
    (:func:`app.services.loan_posting_service.compute_loan_payment_splits`)
    builds the loan's rate periods from it via
    :func:`app.services.loan_resolver.resolve_periods` -- without paying for
    the rest of ``load_loan_context``'s payment-history / escrow /
    contractual-P&I work.

    Args:
        account_id: The loan account whose rate history to load.

    Returns:
        The :class:`RateChangeRecord` list (newest first), or ``None`` when the
        loan carries no :class:`RateHistory` row (an origination-row-less,
        unresolvable loan -- the resolver raises on such a feed).
    """
    return _rate_change_records_from(load_rate_history(account_id))


def load_loan_params(account_id: int) -> LoanParams | None:
    """Load a loan account's :class:`LoanParams` row, or None.

    The one-line "is this a configured loan, and if so what are its terms"
    lookup shared by every loan consumer
    (:func:`app.services.loan_payment_service.resolve_account_loan`, the loan
    PITI resolver, and the Step-4
    :func:`app.services.loan_posting_service.compute_loan_payment_splits`), so
    none of them re-spells the same query and a future change to how a loan's
    params are loaded (eager-loads, soft-delete handling) touches one site.
    ``None`` means the account has no loan configuration yet -- not an
    amortizing loan, or a loan whose setup is incomplete -- and the caller
    short-circuits.

    Args:
        account_id: The account whose loan parameters to load.

    Returns:
        The :class:`LoanParams` row, or ``None`` when the account is not a
        configured loan.
    """
    return (
        db.session.query(LoanParams)
        .filter_by(account_id=account_id)
        .first()
    )


def load_all_loan_account_ids() -> list[int]:
    """Return every configured loan account's id, ascending (all owners).

    The account id of every :class:`LoanParams` row -- one per amortizing loan,
    across all owners.  A loan can carry a Build-Order Step 4 split correction
    only once it has a :class:`LoanParams` row (:func:`load_loan_params`;
    :func:`app.services.loan_posting_service.compute_loan_payment_splits` returns
    ``[]`` otherwise), so this is exactly the set the one-time historical backfill
    (:func:`app.services.loan_posting_service.backfill_all_loan_postings`)
    iterates.  Deliberately NOT user-scoped: it is a system / deploy-time sweep
    over every owner's loans -- like the Step-2 / Step-3 settled-row backfills --
    and each posted correction still carries its own owner (from the payment
    shadow's pay period), so no row is mis-attributed.

    Returns:
        The loan account ids, ascending (``account_id`` is unique per
        :class:`LoanParams`, so already distinct); empty on a loan-free database.
    """
    rows = (
        db.session.query(LoanParams.account_id)
        .order_by(LoanParams.account_id)
        .all()
    )
    return [account_id for (account_id,) in rows]


def load_loan_account_ids_for_user(user_id: int) -> list[int]:
    """Return the given user's configured loan account ids, ascending.

    The per-OWNER counterpart to :func:`load_all_loan_account_ids`: every
    :class:`LoanParams` row whose account belongs to *user_id*, joined through
    :class:`~app.models.account.Account`.  Where the all-owners sweep backs the
    system / deploy-time backfill
    (:func:`app.services.loan_posting_service.backfill_all_loan_postings`), this
    scoped set backs a PER-USER re-sync: ``pay_period_admin.reset_pay_periods``
    calls it (via
    :func:`app.services.loan_posting_service.resync_user_loan_postings`) to
    rebuild only the reset user's loan genesis postings after the wipe -- the
    period CASCADE (``journal_entries.pay_period_id ON DELETE CASCADE``) disposes
    THIS user's loan opening / true-up entries along with the periods, so the
    reset stays inside its own single-user transaction rather than reconciling
    every owner's loans.

    Args:
        user_id: The owning user's id.

    Returns:
        The user's loan account ids, ascending (already distinct -- ``account_id``
        is unique per :class:`LoanParams`); empty when the user has no loan.
    """
    rows = (
        db.session.query(LoanParams.account_id)
        .join(Account, Account.id == LoanParams.account_id)
        .filter(Account.user_id == user_id)
        .order_by(LoanParams.account_id)
        .all()
    )
    return [account_id for (account_id,) in rows]


def load_escrow_lines(account_id: int) -> list:
    """Load a loan account's escrow LINES with every version, ordered by name.

    The single escrow read for the supersession model: one query returns each
    :class:`~app.models.escrow_line.EscrowLine` with its
    :class:`~app.models.escrow_line.EscrowComponentVersion` history eager-loaded
    (``selectinload`` -- one extra query for all lines, not one per line), so a
    caller resolves "escrow as of date D" purely in memory via
    :func:`app.services.escrow_calculator.escrow_monthly_as_of` /
    :func:`~app.services.escrow_calculator.resolve_active_lines`.  It serves BOTH
    the loan-payment split (which resolves each historical payment's date against
    the same rows, so a since-removed version still applies to a past payment) and
    the today's-escrow display / cash surfaces (which resolve on today) -- one
    loader, one source of truth, no separate active/all split.

    Args:
        account_id: The loan account whose escrow lines to load.

    Returns:
        The account's :class:`~app.models.escrow_line.EscrowLine` rows, ascending
        by ``name`` (stable order for the display cent-allocation), each with
        ``versions`` populated.  Empty when the account carries no escrow.
    """
    return (
        db.session.query(EscrowLine)
        .options(selectinload(EscrowLine.versions))
        .filter(EscrowLine.account_id == account_id)
        .order_by(EscrowLine.name)
        .all()
    )


def _settled_income_shadows(
    account_id: int, scenario_id: int,
) -> list[Transaction]:
    """Return a loan's SETTLED shadow-income rows (the shared settled-payment set).

    The single "which payments are settled" derivation that every settled-payment
    guard builds on: the shared :func:`query_shadow_income` predicate narrowed to
    the settled statuses.  Both :func:`_settled_payment_due_dates` (mapping each to
    its monthly due date for the anchor-ordering guards) and
    :func:`latest_settled_payment_period_start` (taking the greatest pay-period
    start for the escrow forward-only guard) read this ONE set, so they -- and the
    genesis walk's own settled-shadow set -- can never disagree on WHICH payments
    are settled.  ``pay_period`` is eager-loaded by :func:`query_shadow_income`, so
    callers read each shadow's period without an N+1.

    Args:
        account_id: The loan account whose settled payments to scan.
        scenario_id: The budget scenario to scope to.

    Returns:
        The account's settled shadow-income :class:`Transaction` rows (unordered),
        or ``[]`` when the loan has no settled payment.
    """
    return (
        query_shadow_income(account_id, scenario_id)
        .filter(Transaction.status_id.in_(settled_status_ids()))
        .all()
    )


def loan_payment_due_date(shadow: Transaction, payment_day: int) -> date:
    """Return the monthly installment a loan payment shadow satisfies.

    The project's SINGLE derivation of "which contractual installment is this
    payment?" -- read by the genesis write walk
    (:func:`app.services.loan_posting_service._walk._merge_anchor_and_payment_events`),
    the ledger history reader
    (:func:`app.services.loan_posting_service.confirmed_loan_history_rows`), the
    payment-history table
    (:func:`app.services.loan_posting_service.confirmed_loan_payment_history`),
    and the settled-payment guards below, so no two of them can disagree on a
    payment's due date.

    The shadow's OWN ``due_date`` is the answer: the recurrence engine stamps
    each generated instance with the date its rule produced
    (:func:`app.services.recurrence_engine`), so it is the installment's
    identity as a stored fact.  It is deliberately NOT re-derived from the
    payment's pay period, because a pay period is the CASH basis (when the money
    moved / which period the ledger books it in), not the installment basis: a
    payment settled LATE -- past its due date, into the next biweekly period, a
    routine event over a weekend or holiday -- sits in a pay period that no
    longer contains its due date.  Deriving the due date from that period's
    start (the pre-fix behaviour) then reports the NEXT month's installment: a
    July payment recorded as an August one, which both mis-states the payment
    history and stamps a CONFIRMED schedule row with a FUTURE date, breaking
    every date-basis balance walk that reads it.

    ``monthly_due_date`` remains the fallback for a shadow with no stored
    ``due_date`` (a hand-created or carried-forward row --
    :attr:`app.models.transaction.Transaction.due_date` is nullable).  It
    reconstructs the due date from the pay-period start, which is correct
    exactly while the payment's period still contains its due date.

    PRECONDITION on the stored value: the payment's recurrence rule must carry a
    ``day_of_month``, so :func:`app.services.recurrence_engine.compute_due_date`
    stamps each instance with the installment date rather than falling back to
    ``period.start_date`` (its no-day behaviour, and the origin of the legacy
    rows migration ``c4e91a7b2d38`` backfills).  The loan payment-transfer flow
    guarantees it -- ``app/routes/loan/payment_transfer.py`` builds the rule with
    ``day_of_month=params.payment_day`` -- but a loan payment set up as a plain
    every-paycheck transfer would not, and would keep regenerating pay-period
    starts into a column the posting walk now reads.

    This value is a POSTING INPUT, not display metadata: the genesis write walk
    (``loan_posting_service._walk._merge_anchor_and_payment_events``) orders
    payments by it and applies its strict ``anchor_date < due_date`` post-anchor
    boundary against it, so moving it moves the POSTED balance.  Any writer of
    ``due_date`` must therefore follow it with a posting reconcile --
    ``transfer_service._POSTING_RELEVANT_FIELDS`` is what enforces that.

    Args:
        shadow: The loan-payment income shadow (its ``pay_period`` must be
            loaded; :func:`query_shadow_income` eager-loads it).
        payment_day: The loan's contractual day-of-month due day
            (:attr:`app.models.loan_params.LoanParams.payment_day`), used only
            by the fallback.

    Returns:
        The date of the monthly installment this payment satisfies.
    """
    if shadow.due_date is not None:
        return shadow.due_date
    return monthly_due_date(shadow.pay_period.start_date, payment_day)


def _settled_payment_due_dates(
    account_id: int, scenario_id: int,
) -> list[date]:
    """Return the monthly due dates of a loan's SETTLED payments (shared derivation).

    The single settled-payment-due-date derivation that both
    :func:`load_settled_payment_due_months` (the year-end tax exclusion set) and
    :func:`earliest_settled_payment_due_date` (the tracking-start guard) build on,
    so the two provably agree with each other -- and with the genesis walk's own
    settled-shadow set -- on WHICH payments are settled and on each one's due date.
    The settled shadow set comes from :func:`_settled_income_shadows`; each is
    dated by :func:`loan_payment_due_date` (its stored ``due_date``, falling back
    to a derivation from its pay-period start).

    Args:
        account_id: The loan account whose settled payments to scan.
        scenario_id: The budget scenario to scope to.

    Returns:
        The settled payments' due dates (unordered), or ``[]`` for an unconfigured
        loan (no :class:`LoanParams`, hence no ``payment_day``) or one with no
        settled payment.
    """
    params = load_loan_params(account_id)
    if params is None:
        return []
    return [
        loan_payment_due_date(shadow, params.payment_day)
        for shadow in _settled_income_shadows(account_id, scenario_id)
    ]


def load_settled_payment_due_months(
    account_id: int, scenario_id: int,
) -> set[tuple[int, int]]:
    """Return the ``(year, month)`` due slots of a loan's SETTLED payments.

    The schedule-side partition key for the year-end mortgage-interest hybrid
    (:func:`app.services.year_end_summary_service._income_tax._loan_year_interest`):
    a settled payment's actual interest is read from the genesis ledger and
    attributed to its civil PAID date, so the schedule's projected row for the
    same due slot must be excluded or the hybrid double-counts.  The resolver's
    ``is_confirmed`` flag alone cannot make that cut -- its replay bounds
    confirmed payments by "pay period has begun", so an EARLY-settled payment
    (settled before its pay period starts) stays ``is_confirmed=False`` on the
    schedule while its interest is already posted (the write side splits at
    settlement -- see
    :func:`app.services.loan_posting_service._walk._settled_income_shadows`).

    Keyed by due (year, month) rather than the exact due date so the exclusion
    still matches a schedule row whose display date the resolver's
    biweekly-collision redistribution nudged within the month.  Known
    approximation: when TWO payments share a due month (a biweekly collision)
    and only one is settled early, the redistribution moves the second row to
    the next month, so the month key excludes the settled slot and keeps the
    shifted one -- matching intent; the reverse mismatch (excluding a shifted
    row that belongs to the unsettled payment) requires the settled payment
    itself to have been redistribution-shifted, which cannot happen (only
    schedule DISPLAY rows shift, never the payment's own due month).

    Args:
        account_id: The loan account whose settled due slots to load.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``{(year, month)}`` of :func:`monthly_due_date` over the loan's settled
        income shadows; empty for an unconfigured loan (no
        :class:`LoanParams` -- there is no ``payment_day`` to derive a due date
        from, and no split correction can exist either) or one with no settled
        payment.
    """
    return {
        (due.year, due.month)
        for due in _settled_payment_due_dates(account_id, scenario_id)
    }


def earliest_settled_payment_due_date(
    account_id: int, scenario_id: int,
) -> date | None:
    """Return the earliest settled payment's monthly due date, or ``None``.

    The lower bound the tracking-start opening flow validates against: a
    ``tracking_start`` opening must sort BEFORE every recorded payment in the
    genesis walk (which orders a payment before an anchor on an equal date), or
    the earliest payment would be subsumed by the opening's reset and dropped.
    The route rejects a tracking-start whose date is not strictly earlier than
    this.  Shares :func:`_settled_payment_due_dates` with
    :func:`load_settled_payment_due_months`, so the guard, the tax exclusion, and
    the walk provably agree on each payment's date.

    NOTE: point-in-time -- this scans only payments settled at record time.  A
    payment recorded LATER with a due date before the tracking-start would be
    subsumed by the walk; that requires contradictory operator input (a payment
    predating when they began tracking) and is the same structural property the
    origination opening already carries.

    Args:
        account_id: The loan account whose settled payments to scan.
        scenario_id: The budget scenario to scope to (the baseline, where the
            recorded payments live).

    Returns:
        The earliest ``monthly_due_date`` over the loan's settled income
        shadows, or ``None`` when the loan is unconfigured (no
        :class:`LoanParams`) or has no settled payment.
    """
    due_dates = _settled_payment_due_dates(account_id, scenario_id)
    return min(due_dates) if due_dates else None


def latest_settled_payment_period_start(
    account_id: int, scenario_id: int,
) -> date | None:
    """Return the latest settled payment's pay-period START date, or ``None``.

    The forward-only boundary the escrow effective-date guard validates against: a
    new or edited escrow version must take effect STRICTLY AFTER this date, or it
    would retroactively change an already-settled payment's escrow split and desync
    it from the cash frozen at settlement.  A version at ``effective_date > this``
    cannot be the greatest ``effective_date <= start`` for any settled payment, so
    no settled split moves.

    Keys on ``pay_period.start_date`` -- the EXACT date the genesis split
    (:func:`app.services.loan_posting_service._walk._replay_events`) and the
    settle-time cash freeze
    (:func:`app.services.loan_payment_service._shadow_live_amount`) resolve each
    payment's escrow at -- NOT the monthly due date
    :func:`_settled_payment_due_dates` derives for the anchor-ordering guards
    (those compare against the walk's anchor-vs-payment due-date sort; escrow
    resolves on the period start, so the boundary differs).  Shares
    :func:`_settled_income_shadows` with those, so the escrow guard and the split
    walk provably agree on which payments are settled.

    NOTE: point-in-time -- scans only payments settled at call time, mirroring
    :func:`earliest_settled_payment_due_date`.  A payment settled LATER against an
    earlier period is the same structural property the tracking-start guard
    carries; a settled payment's escrow is additionally frozen by capture-on-settle
    (:func:`app.services.loan_payment_service.live_loan_payment_amount`).

    Args:
        account_id: The loan account whose settled payments to scan.
        scenario_id: The budget scenario to scope to (the baseline, where the
            recorded payments live).

    Returns:
        The greatest ``pay_period.start_date`` over the loan's settled income
        shadows, or ``None`` when the loan has no settled payment.
    """
    starts = [
        shadow.pay_period.start_date
        for shadow in _settled_income_shadows(account_id, scenario_id)
    ]
    return max(starts) if starts else None


def query_shadow_income(account_id: int, scenario_id: int):
    """Return the base query for shadow-income transactions on an account.

    Shadow income is the income-leg shadow of a transfer INTO the account:
    a payment received by a loan, or a contribution into an investment
    account.  It is identified by ``transfer_id IS NOT NULL`` plus the
    Income transaction type, excluding soft-deleted rows and the
    balance-excluded statuses (Credit, Cancelled, via the centralized
    ``balance_excluded_status_ids`` accessor).  Centralizing that predicate
    keeps the loan-payment history and the year-end contribution feeds from
    drifting on what counts as shadow income (MED-02): a one-sided change
    to the rule would otherwise desynchronize the two surfaces.

    ``status`` and ``pay_period`` are eager-loaded because both current
    consumers read ``txn.status`` / ``txn.pay_period`` downstream without an
    N+1.  Period scoping and ordering stay with the caller because they
    differ: the payment history covers every period and orders by period
    start; the year-end feeds filter to a specific set of period IDs.

    Args:
        account_id: The account receiving the transfers.
        scenario_id: The active budget scenario.

    Returns:
        A SQLAlchemy ``Query`` over ``Transaction`` filtered to the
        account's shadow income (status + pay_period eager-loaded), NOT yet
        executed -- callers chain ``.filter`` / ``.join`` / ``.order_by`` /
        ``.all`` as their surface requires.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.status),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(balance_excluded_status_ids()),
        )
    )
