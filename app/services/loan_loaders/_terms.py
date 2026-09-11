"""A loan's TERM rows: its params, anchors, rates, escrow lines and due dates.

The half of :mod:`app.services.loan_loaders` that answers *what are this loan's
contractual facts* -- the :class:`~app.models.loan_params.LoanParams` /
:class:`~app.models.loan_anchor_event.LoanAnchorEvent` /
:class:`~app.models.loan_features.RateHistory` /
:class:`~app.models.escrow_line.EscrowLine` loaders, the synthesized origination
anchor, and the ONE derivation of which installment a payment satisfies.

Sits above :mod:`._shadows`, which owns *which rows are this account's payments*:
:func:`_settled_payment_due_dates` reads that partition and nothing there reads
back.  The two were one 1,054-line module until plan step **balance:X-bl-2a**
pushed it past pylint's 1,000-line ceiling, and the cut is the seam that step
created rather than the line count: a contractual fact about a loan and a query
for its payment rows are two questions, and only the second grew.

A LEAF: it imports models, the pure engine primitives and the shared balance
predicates -- never another loan service.  Flask-isolated, reads only, no
commits.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import selectinload

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.escrow_line import EscrowLine
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.models.transaction import Transaction
from app.services.amortization_engine import RateChangeRecord
from app.services.rate_period_engine import monthly_due_date
from app.utils.dates import anchor_chronology_key

from ._shadows import settled_income_shadows

# the ``(anchor_date, created_at, event_id)`` chronology's second term --
# exactly as the stored origination row (created at loan setup, before any
# true-up) did.
_ORIGINATION_CREATED_AT = datetime.min.replace(tzinfo=timezone.utc)

# The synthesized origination anchor's event id: the id twin of
# _ORIGINATION_CREATED_AT above, and there for the same reason -- the synthesized
# fact has NO stored row, and ``app.utils.dates.anchor_chronology_key`` needs a
# comparable value in every term.  Zero is below every
# ``budget.loan_anchor_events.id`` (SERIAL, so the sequence starts at 1).  It
# never actually decides an ordering: _ORIGINATION_CREATED_AT is strictly
# earlier than the ``func.now()`` instant of every stored row, so the key is
# settled one term before this one is read.
_ORIGINATION_EVENT_ID = 0


@dataclass(frozen=True)
class LoanAnchorFact:
    """One dated balance assertion for a loan, as plain data.

    The single anchor shape BOTH anchor consumers walk (the read switch's
    final commit): the genesis posting walk derives its opening / true-up
    corrections from these, and the loan resolver's replay fallback consumes
    them as its duck-typed anchor events (it reads ``anchor_date`` /
    ``anchor_balance`` / ``created_at`` / ``event_id``, all here).  Three
    provenances:

    * **The origination anchor is SYNTHESIZED from the immutable
      :class:`LoanParams`** (``origination_date`` / ``original_principal``)
      rather than read from a stored row -- the origination
      :class:`LoanAnchorEvent` write is retired, since that row was always a
      verbatim copy of the params (verified on production data).  Legacy
      stored origination rows are ignored, not migrated: append-only history,
      value-identical to the synthesis.  It is the loan's ONE opening
      (``is_opening=True``), ALWAYS -- a loan originates before it can be
      tracked, so the ledger opens at origination and there is no date it reads
      the loan out of existence (plan step C1).
    * **A tracking-start is a real stored fact** (the ``tracking_start``
      :class:`LoanAnchorEvent` a mid-life import appends): the operator's real
      balance as of a date at/before the first recorded payment.  It is an
      ordinary balance ASSERTION (``is_opening=False``, ``is_tracking_start=True``)
      that RESETS the running balance at its own date -- NOT the opening.  The
      window between origination and it carries no payment record, so the walk
      holds the opening balance flat across it (the honest ACTUAL fold; the
      contractual back-projection that fills it is a separate ESTIMATED tier).
    * **A user true-up is a real stored fact** (the ``user_trueup``
      :class:`LoanAnchorEvent` the balance-edit flow appends): the operator's
      dated balance assertion, the source document the self-healing TRUEUP
      correction is derived from and re-derived against.  Also an
      ``is_opening=False`` assertion (``is_tracking_start=False``).

    Attributes:
        account_id: The loan account the assertion belongs to.
        anchor_date: The date the balance was asserted (origination date for
            the opening).
        anchor_balance: The asserted balance owed (the original principal for
            the opening), cent-quantized ``Decimal``.
        is_opening: ``True`` ONLY for the loan's single opening, the synthesized
            origination; ``False`` for a tracking-start or a user true-up (both
            ordinary balance assertions) -- drives the OPENING vs TRUEUP posting
            kinds.
        created_at: The assertion's creation instant, the chronology's SECOND
            term; the synthesized origination uses the earliest possible UTC
            instant so any same-day assertion wins.  It does NOT decide a tie on
            its own: ``created_at`` is ``server_default=func.now()``, which
            PostgreSQL evaluates at TRANSACTION START, so every row written in
            one transaction shares an instant.
        event_id: The stored ``budget.loan_anchor_events.id``, the chronology's
            THIRD and final term -- what makes the key TOTAL, so exactly one
            ordering of a loan's anchors exists (see
            :func:`load_loan_anchor_facts`).  The synthesized origination has no
            stored row and carries :data:`_ORIGINATION_EVENT_ID`.
        is_tracking_start: ``True`` for a ``tracking_start`` assertion (a
            mid-life import's balance-as-of-date), ``False`` for the origination
            opening and every user true-up.  Display provenance only (the drift
            scorecard labels the tracking-start row); the balance math never
            branches on it.
    """

    account_id: int
    anchor_date: date
    anchor_balance: Decimal
    is_opening: bool
    created_at: datetime
    event_id: int
    is_tracking_start: bool = False


def load_loan_anchor_facts(params: LoanParams) -> list[LoanAnchorFact]:
    """Return a loan's anchor facts: the origination opening + every assertion.

    The one anchor loader every consumer shares (the genesis walk and every
    resolver-input builder), so no two sites can disagree on what a loan's
    anchors are.  The single ``is_opening`` anchor is ALWAYS the synthesized
    origination (:func:`synthesize_origination_anchor` -- from the immutable
    *params*, never a stored row; see :class:`LoanAnchorFact`).  Every stored
    ``tracking_start`` and ``user_trueup`` :class:`LoanAnchorEvent` is loaded as
    an ``is_opening=False`` balance ASSERTION -- the two differ only in
    ``is_tracking_start`` (a display label; the walk resets on both identically).

    **The returned order IS the loan's chronology, and stating it HERE is the
    point of this function** (plan step X-an-b, closing finding N-196).  Facts
    come back ascending by ``(anchor_date, created_at, event_id)`` -- BUSINESS
    date first, the recording instant next, the stored row id last -- which is
    the cash side's key, term for term
    (:func:`app.services.cash_ledger.cash_anchor_facts`), and the loan side was
    the only gap.  Two properties are load-bearing and neither was true before:

    * **The key is TOTAL.**  ``created_at`` is ``server_default=func.now()``,
      which PostgreSQL evaluates at TRANSACTION START, so two anchors written in
      one transaction (a backfill, a migration, a fixture) share an instant and
      ``(anchor_date, created_at)`` does not order them.  ``event_id`` does, and
      the later INSERT wins -- the same "the last one recorded is that day's
      closing balance" rule the cash walk and this table's own write door
      (:func:`app.services.anchor_service._governing_loan_anchor`) already apply.
    * **It is ONE statement, not a rule each consumer re-derives.**  This list
      had no ``ORDER BY`` and its two consumers each broke a tie their own way:
      the fold's walk (:func:`app.services.loan_ledger.walk_loan_ledger`) reset
      at the LAST of a tie, the resolver
      (:func:`app.services.loan_resolver.select_latest_anchor`) took ``max()``,
      the FIRST -- so on a full tie carrying two balances they were GUARANTEED to
      name opposite rows, and which row each named was whatever PostgreSQL
      returned.  Finding **N-133 / R1** ruled this same question on the cash
      side: state the order where the rows are READ, and do not restate it as a
      re-sort in the consumer.

    The sort is in PYTHON rather than an ``ORDER BY`` because the synthesized
    origination has no stored row to order with, so SQL alone cannot produce the
    final list; sorting the MERGED list is what makes the contract hold for
    every fact rather than only the stored ones.

    **Origination is the opening ALWAYS** (plan step C1): a loan originates
    before it can be tracked, so opening at a mid-life ``tracking_start`` read the
    loan out of existence for the whole pre-tracking window (the false pre-opening
    zero, B-11).  A ``tracking_start`` now RESETS the running balance at its own
    date like any true-up, so a date at/after it is unchanged, while a date before
    it reads the origination opening held flat -- the honest fold of the recorded
    facts.

    Args:
        params: The loan's :class:`LoanParams` row (supplies the account id
            and the immutable origination fields).

    Returns:
        The :class:`LoanAnchorFact` list ascending by ``(anchor_date,
        created_at, event_id)`` -- always non-empty (the synthesized origination
        opening is always present), so a configured loan is always resolvable.
        The opening is first for every loan the write doors admit, since both
        refuse an assertion earlier than ``origination_date``; the sort does not
        depend on that guard holding.
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
    facts = [synthesize_origination_anchor(params)]
    facts.extend(
        LoanAnchorFact(
            account_id=event.account_id,
            anchor_date=event.anchor_date,
            anchor_balance=Decimal(str(event.anchor_balance)),
            is_opening=False,
            created_at=event.created_at,
            event_id=event.id,
            is_tracking_start=(event.source_id == tracking_start_source_id),
        )
        for event in events
    )
    facts.sort(key=anchor_chronology_key)
    return facts


def synthesize_origination_anchor(params: LoanParams) -> LoanAnchorFact:
    """Return a loan's ORIGINATION anchor, synthesized from its immutable params.

    The origination-dated opening -- ``(origination_date, original_principal)``
    -- ALWAYS.  Since step C1 the origination is :func:`load_loan_anchor_facts`'
    opening too (a ``tracking_start`` no longer supersedes it, it is an ordinary
    assertion), and it is that loader's single ``is_opening`` fact.  It is also
    ``[0]`` for every loan the write doors admit, since both refuse an assertion
    earlier than ``origination_date`` -- but read the FLAG, never the position:
    the loader sorts (plan step X-an-b) rather than prepending, so position is a
    consequence of the data, and every ``is_opening`` reader in ``app/`` already
    scans for the flag.  This function
    stays the loan's ONE definition of the origination anchor, reused there and by
    the callers that need JUST it -- the contractual back-projection that fills a
    tracking-start loan's pre-tracking months
    (:func:`app.services.balance_at._resolution.contractual_schedule_from_origination`)
    seeds from origination alone, without the loan's true-up assertions.

    The synthesized origination carries :data:`_ORIGINATION_CREATED_AT` (the
    earliest possible instant) and :data:`_ORIGINATION_EVENT_ID` (below every
    stored id) for the ``(anchor_date, created_at, event_id)`` chronology
    :func:`load_loan_anchor_facts` orders on, so an assertion made ON the
    origination date still outranks it -- exactly as the retired stored
    origination row (created at loan setup) did.  It has no stored row, so
    both are sentinels rather than read values; see the constants for why a
    total key needs them.

    Pure: reads only the immutable *params* fields, no query.

    Args:
        params: The loan's :class:`LoanParams` row (supplies the account id and
            the immutable ``origination_date`` / ``original_principal``).

    Returns:
        The origination :class:`LoanAnchorFact` (``is_opening=True``,
        ``is_tracking_start=False``).
    """
    return LoanAnchorFact(
        account_id=params.account_id,
        anchor_date=params.origination_date,
        anchor_balance=Decimal(str(params.original_principal)),
        is_opening=True,
        created_at=_ORIGINATION_CREATED_AT,
        event_id=_ORIGINATION_EVENT_ID,
        is_tracking_start=False,
    )


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
    (:func:`app.services.loan_ledger.compute_loan_payment_splits`)
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
    (:func:`app.services.balance_at._resolution.resolved_loan`, the loan
    PITI resolver, and the fold's
    :func:`app.services.loan_ledger.compute_loan_payment_splits`), so
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
    :func:`app.services.loan_ledger.compute_loan_payment_splits` returns
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


def installment_for(
    due_date: date | None, period_start: date, payment_day: int,
) -> date:
    """Return the installment a loan payment satisfies, from PLAIN DATA.

    The arithmetic core of :func:`loan_payment_due_date`, over plain values
    instead of a stored shadow: the payment's own ``due_date`` when it has one,
    else the contractual day reconstructed from its pay-period start
    (:func:`~app.services.rate_period_engine.monthly_due_date`).  See that
    function for why the stored value is authoritative and when the fallback is
    correct.

    Extracted so a payment that does not EXIST yet can be keyed on the same rule
    as one that does.  The transfer write boundary
    (:func:`app.services.transfer_service._loan_posting._reject_payment_before_origination`,
    plan step C9b) must decide "which installment would this be?" before any row
    is written, and a guard keying on a rule of its own would refuse a different
    set of payments than the fold erases -- the boundary-predicate drift this
    architecture keeps paying for.  Same shape as the ONE allocation
    (``app.utils.money.apply_payment_cash``) factored out of the four walks that
    had each restated it (X-au-g-2c-3a).

    Pure: no I/O, no clock.

    Args:
        due_date: The payment's stored due date, or ``None``.
        period_start: The start date of the payment's pay period (the fallback
            basis).
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        The date of the monthly installment this payment satisfies.
    """
    if due_date is not None:
        return due_date
    return monthly_due_date(period_start, payment_day)


def precedes_origination(params: LoanParams, installment: date) -> bool:
    """Return whether *installment* falls at or before the loan's origination.

    **Ruling R-C's boundary, stated once** (plan step C9b, moved here at
    R16-b-2).  A loan cannot receive a payment before it exists: such a
    payment is ERASED by the fold -- it splits against a running balance of
    zero and the origination anchor resets over it -- while the cash side still
    debits the funding account.  The write door refuses it
    (``transfer_service._loan_posting._reject_payment_before_origination``),
    and since plan step R16-b-2 the forward plan's ESTIMATED tier asks the
    same question of every occurrence a definition names, so the plan never
    prices a row the door would refuse to write.  Two spellings of ``<=`` here
    would be exactly the drift ``CLAUDE.md`` rule 14 names.

    **The boundary is ``<=``, not ``<``.**  A payment due exactly ON the
    origination date is subsumed by that anchor's reset -- the same strict
    ``anchor_date < due_date`` post-anchor rule the replay applies -- so it is
    erased identically.  Swept and measured at C9b: due 02-01, 02-28 and 03-01
    against a 03-01 origination all book ``$0.00`` principal; 03-02 pays down
    ``$366.67``.

    Args:
        params: The loan's :class:`~app.models.loan_params.LoanParams`.
        installment: The installment a payment satisfies, or would satisfy
            (:func:`installment_for`).

    Returns:
        ``True`` when the fold would erase a payment on *installment*.
    """
    return installment <= params.origination_date


def loan_payment_due_date(shadow: Transaction, payment_day: int) -> date:
    """Return the monthly installment a loan payment shadow satisfies.

    The project's SINGLE derivation of "which contractual installment is this
    payment?" -- read by the fold's event stream
    (:func:`app.services.loan_ledger.loan_event_stream`),
    the payment-history table
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

    PRECONDITION on the stored value, and plan step R7c-c re-words it without
    changing it (plan ledger row **D27**): the payment's recurrence rule must
    fire ON A DAY OF THE MONTH, so
    :func:`app.services.recurrence.compute_due_date` stamps each instance
    with the installment date rather than falling back to ``period.start_date``
    (its no-day behaviour, and the origin of the legacy rows migration
    ``c4e91a7b2d38`` backfills).

    **The predicate was a COLUMN and is now a derivation.** It read
    ``recurrence_rules.day_of_month``, which R7c-c drops; the same question is
    :func:`app.services.recurrence.scheduling_day_of_month` answering non-``None``,
    which is :func:`app.services.recurrence.fires_on_day_of_month` joined with
    the rule's
    first occurrence.  The rules that satisfy it are unchanged -- the migration
    graded the two equal on all 46 live rules before dropping the column -- so
    this is the same precondition stated over the columns that survive.

    The loan payment-transfer flow guarantees it: ``app/routes/loan/
    payment_transfer.py`` derives the cadence through
    ``loan_recurrence_sync.loan_cadence_start``, which states the first
    contractual installment as the rule's ``starts_on``.  A loan payment set up
    as a plain every-paycheck transfer would NOT -- a ``PERIOD``-unit rule has
    no day-of-month coordinate at all -- and would keep regenerating pay-period
    starts into a column the posting walk now reads.  Still unenforced, which
    is what D27 records, and plan step R5 makes it structural by giving a
    generated row its own ``due_on``.

    This value is a POSTING INPUT, not display metadata: the fold's event stream
    (``loan_ledger.loan_event_stream``) DATES every payment by it, the replay
    (``loan_ledger.replay_loan_events``) orders on that date and applies its
    strict ``anchor_date < due_date`` post-anchor boundary against it, and the
    charge calendar keys its accrual periods off it -- so moving it moves the
    POSTED balance.  Any writer of
    ``due_date`` must therefore follow it with a posting reconcile --
    ``transfer_service._POSTING_RELEVANT_FIELDS`` is what enforces that.

    Its ``pay_period`` is read on EVERY call since the derivation moved into the
    shared :func:`installment_for` (previously only the no-``due_date`` branch
    touched it), so a caller must hand it a shadow whose ``pay_period`` is
    loaded.  **:func:`._shadows.income_shadows` is what guarantees that, not
    :func:`._shadows.query_shadow_income`**: since plan step **balance:X-bl-2a**
    the query loads only what its caller asks for, and the partition adds the
    period because it sorts on it.  A caller reaching the raw query with
    ``options=()`` and passing rows here would pay a lazy load per row, so every
    production caller comes through the partition; a shadow fetched by a bare
    ``session.get`` costs one here rather than only on the fallback path.

    Args:
        shadow: The loan-payment income shadow (its ``pay_period`` must be
            loaded; :func:`._shadows.income_shadows` eager-loads it -- see the
            note above, and NOT :func:`._shadows.query_shadow_income`, which
            loads only what its caller asks for).
        payment_day: The loan's contractual day-of-month due day
            (:attr:`app.models.loan_params.LoanParams.payment_day`), used only
            by the fallback.

    Returns:
        The date of the monthly installment this payment satisfies.
    """
    return installment_for(
        shadow.due_date, shadow.pay_period.start_date, payment_day,
    )


def _settled_payment_due_dates(
    account_id: int, scenario_id: int,
) -> list[date]:
    """Return the monthly due dates of a loan's SETTLED payments (shared derivation).

    The settled-payment-due-date derivation behind
    :func:`earliest_settled_payment_due_date` (the tracking-start guard), built on
    the same :func:`settled_income_shadows` set and the same
    :func:`loan_payment_due_date` per-payment rule the fold's event stream walks --
    so the guard, the walk, and the Schedule A interest merge
    (:func:`app.services.balance_at.loan_interest_in_year`, which derives its
    settled slots from that same fold walk) provably agree on WHICH payments are
    settled and on each one's due date.  Each shadow is dated by
    :func:`loan_payment_due_date` (its stored ``due_date``, falling back to a
    derivation from its pay-period start).

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
        # ``options=()``: this reads each shadow's stored due date and its pay
        # period, and ``income_shadows`` loads the period itself.  It paid for
        # the amount model's five-chain eager set until plan step
        # balance:X-bl-2a made the load the caller's statement.
        for shadow in settled_income_shadows(
            account_id, scenario_id, options=(),
        )
    ]


def earliest_settled_payment_due_date(
    account_id: int, scenario_id: int,
) -> date | None:
    """Return the earliest settled payment's monthly due date, or ``None``.

    The lower bound the tracking-start opening flow validates against: a
    ``tracking_start`` opening must sort BEFORE every recorded payment in the
    genesis walk (which orders a payment before an anchor on an equal date), or
    the earliest payment would be subsumed by the opening's reset and dropped.
    The route rejects a tracking-start whose date is not strictly earlier than
    this.  Built on :func:`_settled_payment_due_dates`, whose per-payment due-date
    rule the fold's event stream and the Schedule A interest merge share, so the
    guard, the walk, and the tax figure provably agree on each payment's date.

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


def latest_settled_payment_due_date(
    account_id: int, scenario_id: int,
) -> date | None:
    """Return the latest settled payment's monthly DUE date, or ``None``.

    The forward-only boundary the escrow effective-date guard validates against: a
    new or edited escrow version must take effect STRICTLY AFTER this date, or it
    would retroactively change an already-settled payment's escrow split and desync
    it from the cash frozen at settlement.  A version at ``effective_date > this``
    cannot be the greatest ``effective_date <= due date`` for any settled payment,
    so no settled split moves.

    Keys on the payment's DUE date -- contract time, the EXACT date the fold's
    walk (:func:`app.services.loan_ledger.walk_loan_ledger`) and the settle-time
    cash freeze
    (:func:`app.services.cash_ledger._loan_installment._installment_cash`) resolve each
    payment's escrow at (ruling D5, finding N-34).  It is the SAME
    :func:`_settled_payment_due_dates` derivation the anchor-ordering guards
    read, so the escrow guard, the walk, and the tax figure provably agree on
    each payment's date -- the mirror of
    :func:`earliest_settled_payment_due_date`, differing only in the bound taken.

    **A pay-period-start boundary is what this must not be:** a period begins up
    to ~2 weeks before the installment it pays, so a version effective inside
    that window clears a period-start guard yet still governs the settled
    payment's split.

    NOTE: point-in-time -- scans only payments settled at call time, mirroring
    :func:`earliest_settled_payment_due_date`.  A payment settled LATER against an
    earlier installment is the same structural property the tracking-start guard
    carries; a settled payment's escrow is additionally frozen by
    capture-on-settle
    (amount rule 4, via :func:`app.services.cash_ledger.amounts_by_id`).

    Args:
        account_id: The loan account whose settled payments to scan.
        scenario_id: The budget scenario to scope to (the baseline, where the
            recorded payments live).

    Returns:
        The greatest due date over the loan's settled income shadows, or ``None``
        when the loan is unconfigured (no :class:`LoanParams`) or has no settled
        payment.
    """
    due_dates = _settled_payment_due_dates(account_id, scenario_id)
    return max(due_dates) if due_dates else None
