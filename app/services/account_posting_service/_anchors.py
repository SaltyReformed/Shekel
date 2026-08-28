"""Account-anchor correction posting: the opening and true-up reconcile.

Posts a non-loan account's anchor corrections -- the once-per-account OPENING
(its earliest :class:`~app.models.account.AccountAnchorHistory` row) and a
TRUE-UP per later row -- into the append-only double-entry ledger, so the
account's linked ledger sums to an ABSOLUTE balance:  the latest anchor
assertion plus the settled facts recorded after that assertion moment.  This
is the shipped loan genesis pattern generalized to every non-loan account
(Build-Order Step 5); after it, the trial balance closes app-wide.

Every anchor the account carries posts one balanced correction (:mod:`._walk`
computes its ``ledger_before``)::

    linked ledger         (anchor_balance - ledger_before)   [opening | trueup]
    counter ledger        (ledger_before - anchor_balance)   [opening | trueup]
                          -----------------------------------
                          0

The delta is ledger-native sign -- it holds for Asset AND Liability non-loan
accounts with no class branch, exactly like the engine.  A zero delta books
nothing (a fresh $0 account mints no entries and no counter row, staying
hard-deletable).

**WHICH counter ledger is a total dispatch over the account's projection kind**
(ruling **R-FO**, plan step X-f3d), resolved by
:func:`app.services.ledger_account_service.anchor_correction_counter_kind` and
materialised once per reconcile by :func:`_counter_ledger_accounts`.  An
OPENING books to the account's equity row whatever its kind -- capital brought
onto the books is not something earned -- and a TRUE-UP books to what the
difference WAS: interest income for an ``INTEREST`` account, a change in
value for an ``INVESTMENT`` or ``APPRECIATING`` one, and (until plan step X-f3c makes
that residual a transaction the user accepts, ruling **R-FN**) the equity row
for a ``PLAIN`` one.  Before it, every kind plugged to equity, which is why
``$10,653.91`` of return earned over 4.5 months was invisible on the income
statement (measured on a production clone 2026-08-13).

**Reconciled to target, keyed by (source kind, pay period, entry date).**  An
anchor correction has no concrete source FK to key on, so the reconcile keys
each anchor's entry by its ``source_kind_id`` (``account_opening`` vs.
``account_trueup``), the pay period CONTAINING the assertion's day, and its
``entry_date`` -- the fact's own
:attr:`~app.services.cash_ledger.CashAnchorFact.observed_on`, the civil day the
assertion is the CLOSING BALANCE for (ruling R-DH).  It was
``_utc_civil_date(asserted_at)`` until 2026-07-31: a second statement of a rule
the fact already resolves, and one that dated a late-evening Eastern true-up
into the next day, putting the correction on a different day from the settles it
corrects.  Two anchors merge to one target, landing on the later value, only
when they share ALL THREE key parts.  A pre-true-up source whose net later
changes moves the walk's ``ledger_before``; re-running the sync re-derives the
target and posts the balancing delta, so a stale correction self-heals.
Flushes but never commits -- the caller owns the transaction.

**The period is DERIVED FROM THE DAY, and since plan step X-f1c3c that is the
app's ONLY day-to-period rule** (ruling R-DH: *"an assertion's period and the
civil day it was true are two statements of one fact, and the moment they can
be set independently they can disagree"*).  Every anchor correction, cash and
loan alike, is filed through
:meth:`app.services.pay_calendar.PayCalendar.filing_period` against the entry's
own date, loaded through the one door both writers share
(:func:`app.services._posting_reconcile.filing_calendar_for`).

**That rule became ONE clamp at plan step C2-d.**  It was
``loan_ledger.resolve_anchor_pay_period`` -- containment, else the latest
period ENDING before the day, else the earliest -- a three-branch chain across
two functions, one of them a public export of the LOAN package that this cash
package had to import (finding **N-169**).  The 2026-08-10 ruling named what
the chain was actually computing: *the latest period STARTING on or before the
day, else the earliest*.  The equivalence to that chain, the PRECONDITION it
holds under, and the proofs covering each half are stated once at
:meth:`app.services.pay_calendar.PayCalendar.filing_period` rather than
repeated here and in the loan twin.

**That sentence used to carry a limit, and finding N-170 was the limit** --
closed structurally rather than repaired.  A WRITE-side resolver,
``account_service.resolve_anchor_period_id``, fell back to the user's EARLIEST
period when no period contained the day, while this one falls back to the LATEST
period ending before it: they agreed inside the schedule and diverged maximally
for a day AFTER it, index 0 against index 60 on a 61-period production calendar.
Ruling R-EO deleted the assertion's stored period and ruling R-EH deleted the
account's anchor columns, which left that resolver with no caller at all, so it
is DELETED and one rule remains.

One limit does survive, and it belongs to the ledger rather than to a
disagreement.  An entry's ``pay_period_id`` and its ``entry_date`` are NOT "two
columns that cannot drift": whenever no period contains the day, the entry is
filed in a period its own date falls outside, by construction and deliberately
-- the alternative is a correction with no period at all, and
``journal_entries.pay_period_id`` is NOT NULL.  What the derivation buys is that
the drift is a function of the CALENDAR rather than of which clock a writer
happened to read, and that it self-heals the moment the containing period
exists.

**What that replaced, and the two ways it was wrong** (finding N-161).  The
key was ``(source kind, entry date)`` with the period carried alongside it in
a parallel map, written last-fact-wins from ``account_anchor_history``'s stored
``pay_period_id`` -- the source row's CURRENT period, verbatim what R2 forbids
(:mod:`app.services.posting_service`: a correction "carries the PAY PERIOD of
the postings it reverses ... never the source row's current period").  Measured
on production data: two assertions observed 2026-06-03 whose stored periods are
5 and 6 had posted as ``+$3,054.36`` in period 5 and ``-$2,854.36`` in period
6 -- one key could not tell them apart, so the second reconciled as a delta
against the first's whole posted amount, and its reversal of period-5 postings
was filed into period 6.

**Reading the STORED period was the second defect, and a first build of this
step kept it.**  ``account_anchor_history.pay_period_id`` was not an
independent fact -- it was a CACHE of this same derivation, written by
``resolve_anchor_period_id`` from the same day, and ruling R-EO has since
deleted BOTH.  The row that made the defect visible was filed by a broken
clock (created 21:28 Eastern on period 5's last
day, stored against period 6 -- exactly the case
``routes/accounts/crud.py``'s own comment was written to prevent: *"the grid
buckets the correction by ``observed_on`` and the ledger stamps it with
``pay_period_id``, so the two surfaces disagree by the whole correction"*).
Projecting that cache would have made the posted ledger disagree with the grid's
"Book vs bank" row by the whole correction, permanently.  Measured on a
production clone across 61 periods: deriving from the day agrees with
:func:`app.services.balance_at.grid_balance_view` on EVERY period; reading the
stored column disagrees on two.  **The comparison is over TRUE-UPS, and saying
so is load-bearing**: ``_assertion_sums`` folds every ASSERTION correction,
while the ledger also keeps an ``account_opening`` entry in a period -- so an
unscoped comparison could not have agreed on 61 and a draft of this docstring
implied one had.  *That sentence read ``corrections[1:]`` and "excluding the
OPENING because ruling R-I moves it into the fold's seed" until plan step
X-f3c-2a; the fold seeds from the stored opening equity now, so no correction is
excluded there and the ledger's opening entry books that same stored fact.*
The agreement is pinned by
``TestLedgerAgreesWithTheGridOnAssertionPeriods`` rather than left as a
measurement in prose (finding N-169's other half).  A mis-filed row is a defect
in the ROW (finding N-168), repaired at its source -- and until it is, the
ledger states the day's truth rather than inheriting the row's error.

Two same-day assertions always share a containing period, so they still merge
into one entry; separating THOSE needs an identity the ledger does not carry
yet (plan step X-ai-s).
"""

from decimal import Decimal

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.models.account import Account
from app.models.ledger_account import LedgerAccount
from app.services import ledger_account_service
from app.services._posting_reconcile import (
    CorrectionKey,
    LegMap,
    emit_correction_deltas,
    filing_calendar_for,
    merge_target_legs,
    posted_correction_legs,
)
from app.services.account_projection import classify_account
from app.services.pay_calendar import PayCalendar
from app.services.posting_reads import _ledger_account_for

from ._walk import AccountAnchorCorrection


def _account_correction_kinds(
    correction: AccountAnchorCorrection,
) -> tuple[PostingSourceEnum, PostingKindEnum]:
    """Return the (journal source kind, posting leg kind) for one correction.

    The account's OPENING EQUITY books the opening (source ``account_opening``,
    leg kind ``opening``); every balance assertion books a TRUE-UP (source
    ``account_trueup``, leg kind ``trueup``).  The leg kinds are the same
    ``opening`` / ``trueup`` pair the loan corrections use (REUSED by design --
    the journal SOURCE distinguishes account from loan corrections).

    **Keyed off a STORED fact since plan step X-f3c-2a**
    (:attr:`~._walk.AccountAnchorCorrection.opens_the_books`, which the walk
    sets from ``budget.account_openings``).  It was keyed off
    ``CashAnchorFact.is_opening`` -- the account's earliest assertion by
    ``(observed_on, created_at, id)`` -- so a BACK-DATED assertion re-elected
    the opening and re-dated its journal entry, and the previous opening
    silently became a true-up.  *That key said ``(created_at, id)`` until plan
    step X-an-b, before plan step 2 made ``observed_on`` user-supplied, and
    trusting it tagged a ``$1,307.66`` true-up as the account's OPENING.  Two
    repairs of one ordering; the third was to stop inferring the answer.*

    Args:
        correction: The correction from :func:`._walk.walk_account_ledger`
            whose kinds to resolve.

    Returns:
        ``(PostingSourceEnum, PostingKindEnum)`` -- ``(ACCOUNT_OPENING,
        OPENING)`` for the opening-equity correction, else ``(ACCOUNT_TRUEUP,
        TRUEUP)``.
    """
    if correction.opens_the_books:
        return PostingSourceEnum.ACCOUNT_OPENING, PostingKindEnum.OPENING
    return PostingSourceEnum.ACCOUNT_TRUEUP, PostingKindEnum.TRUEUP


def _correction_delta(correction: AccountAnchorCorrection) -> Decimal:
    """Return what one anchor correction books on the account's linked ledger.

    ``anchor_balance - ledger_before``: the jump from what the walked ledger
    said just before this assertion to what the assertion declares.  Zero means
    the correction books NOTHING, which is the one predicate two callers share
    -- :func:`_counter_ledger_accounts` (which mints no chart row for a
    correction that books nothing) and :func:`_account_anchor_correction_target`
    (which returns an empty target for it) -- so it is stated once rather than
    spelled twice and left to drift.

    Args:
        correction: The anchor correction from :func:`._walk.walk_account_ledger`.

    Returns:
        The signed linked-ledger delta as a ``Decimal``.
    """
    return correction.target_balance - correction.ledger_before


def _counter_ledger_accounts(
    owner_id: int,
    account: Account,
    corrections: list[AccountAnchorCorrection],
) -> list[LedgerAccount | None]:
    """Resolve each correction's COUNTER chart row, ``None`` where it books nothing.

    Ruling **R-FO** made the counter leg a dispatch over the account's
    projection kind (:func:`~app.services.ledger_account_service.anchor_correction_counter_kind`),
    so an account can need up to two counter rows: ``anchor_equity`` for the
    correction that OPENS its books and, for a modelled account,
    ``interest_income`` or ``unrealized_change`` for the ones after it.  This
    walks the corrections once, in order, and returns one entry per correction
    -- so the caller never has to re-derive which row a correction belongs to.

    **"The opening" is the account's EARLIEST ASSERTION, and keying it on the
    DELTA series instead was tried and REFUSED.**  The tempting refinement is
    "the first correction that books a non-zero delta", which answers the case
    where the create form's ``$0`` pre-fill left the earliest assertion booking
    nothing.  It breaks a case that matters more: an account whose opening books
    nothing because THE RECORDS ALREADY EXPLAIN IT -- a Roth opened at
    ``$1,000.00`` with a ``$1,000.00`` settled transfer already dated before it
    -- would then have its first real ``$150.00`` market gain treated as capital
    and booked to equity, which is the defect ruling R-FO exists to close,
    reintroduced through the back door (adversarial review, 2026-08-14).
    ``is_opening`` is also a property of the ASSERTION HISTORY where a
    delta-keyed rule is a property of ONE SCENARIO's posted sources: the same
    correction could open the books in one scenario and not in another.  The
    ``$0`` pre-fill was the real defect and it is fixed where it lives -- the
    create form now ASKS, which is ruling R-EX's own argument about the
    registration payday applied to the other figure nobody can default.

    **A row is minted only when a correction really books into it.**  A
    correction whose ``ledger_before`` already equals its anchor balance mints
    nothing, so a fresh $0 account keeps zero ledger rows and stays
    hard-deletable -- which the account-delete CASCADE argument rests on -- and
    a resolved row is reused across corrections rather than re-queried, since
    the resolver issues a query and Checking carries 102 corrections.

    Args:
        owner_id: The account owner's user id.
        account: The non-loan :class:`~app.models.account.Account` being
            reconciled, with ``account_type`` loaded (the dispatch classifies
            it).
        corrections: The account's corrections from
            :func:`._walk.walk_account_ledger`, chronological.

    Returns:
        One entry per correction, in the same order: its counter
        :class:`~app.models.ledger_account.LedgerAccount`, or ``None`` when the
        correction books nothing.

    Raises:
        ValueError: If the dispatch has no rule for the account's projection
            kind, or the resolver rejects the account (not owned by *owner_id*,
            or an amortizing loan -- both broken invariants at this point, the
            walk having already classified).
    """
    projection_kind = classify_account(account)
    resolved: dict[LedgerAccountKindEnum, LedgerAccount] = {}
    counters: list[LedgerAccount | None] = []
    for correction in corrections:
        if _correction_delta(correction) == 0:
            counters.append(None)
            continue
        kind = ledger_account_service.anchor_correction_counter_kind(
            projection_kind, is_opening=correction.opens_the_books,
        )
        if kind not in resolved:
            resolved[kind] = (
                ledger_account_service.get_or_create_account_counter_account(
                    owner_id, account.id, kind,
                )
            )
        counters.append(resolved[kind])
    return counters


def _account_anchor_correction_target(
    correction: AccountAnchorCorrection,
    linked: LedgerAccount,
    counter: LedgerAccount | None,
) -> LegMap:
    """Build the two-leg target for one anchor correction, or empty when it books nothing.

    The linked leg is ``anchor_balance - ledger_before`` (tagged ``opening``
    or ``trueup``); the COUNTER leg is its negative, so the two sum to zero and
    the linked ledger's implied balance moves from ``ledger_before`` to the
    asserted value.  A correction whose ``ledger_before`` already equals the
    anchor balance books NOTHING -- an empty target, so no zero leg is written.

    **The counter leg NAMES what the difference was** (ruling **R-FO**): the
    equity opening, interest income, or change in value, decided by
    :func:`_counter_ledger_accounts` and handed in here.  Only the counter side
    moves; the linked leg, its amount and its posting kind are exactly what they
    were before that ruling, which is why the whole change is balance-neutral on
    every account balance the app reports.

    Args:
        correction: The anchor correction from :func:`._walk.walk_account_ledger`.
        linked: The account's LINKED ledger account, resolved once by the
            reconcile.
        counter: This correction's counter chart row from
            :func:`_counter_ledger_accounts`, or ``None`` when that function
            found the correction books nothing -- the same predicate this
            function applies, through the same :func:`_correction_delta`, so
            the two cannot disagree about which corrections have a row.

    Returns:
        ``{ledger_account_id: (amount, posting_kind_id)}`` (the two balanced
        legs, or empty when the correction books nothing).
    """
    delta = _correction_delta(correction)
    if delta == 0:
        return {}
    _source_enum, posting_kind_enum = _account_correction_kinds(correction)
    posting_kind_id = ref_cache.posting_kind_id(posting_kind_enum)
    return {
        linked.id: (delta, posting_kind_id),
        counter.id: (-delta, posting_kind_id),
    }


def _account_anchor_correction_targets(
    corrections: list[AccountAnchorCorrection],
    calendar: PayCalendar,
    linked: LedgerAccount,
    counters: list[LedgerAccount | None],
) -> dict[CorrectionKey, LegMap]:
    """Merge an account's corrections into per-(source, period, date) targets.

    Groups every correction by its ``(source_kind_id, pay_period_id, civil
    entry_date)`` key and sums the legs within each group
    (:func:`app.services._posting_reconcile.merge_target_legs`), so two
    anchors sharing all three key parts net to a single balanced target that
    lands the ledger on the LATER value -- exactly the combined jump they
    express (each correction's delta already accounts for the prior one).  A
    correction that books nothing still creates its key with an empty leg
    map, so an entry it previously posted (now matching) is reversed to
    zero by the reconcile.

    **Both key parts that identify WHEN come off the same fact**: the entry's
    date IS the assertion's ``observed_on``, and its period is the one that day
    files under (:meth:`app.services.pay_calendar.PayCalendar.filing_period`,
    the derivation the loan twin makes and the rule ruling R-DH states).  The
    stored ``account_anchor_history.pay_period_id`` is deliberately NOT read
    here -- it is a cache of this same derivation, and a row whose clock split
    it from its own day would otherwise put the ledger permanently at odds
    with the grid.  See the module docstring for the production measurement.

    Args:
        corrections: The account's corrections from
            :func:`._walk.walk_account_ledger`, chronological.
        calendar: The owner's whole pay calendar, from
            :func:`app.services._posting_reconcile.filing_calendar_for`.
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period` is the
            one place the "is there a period to point at" question is asked and
            refused, so this carries no precondition of its own -- an earlier
            draft claimed the loader had already checked, which was a second
            statement of one predicate and the two had drifted.
        linked: The account's LINKED ledger account, resolved once by the
            reconcile (which needs it anyway to read the posted side).
        counters: One counter chart row per correction, in the SAME order, from
            :func:`_counter_ledger_accounts` -- ``None`` where the correction
            books nothing.  Positional rather than keyed because which row a
            correction belongs to is a property of its POSITION in the series
            (the first booking opens the books), not of anything on the row.

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {ledger_account_id:
        (amount, kind_id)}}``.
    """
    targets: dict[CorrectionKey, LegMap] = {}
    for correction, counter in zip(corrections, counters, strict=True):
        source_enum, _posting_kind = _account_correction_kinds(correction)
        # The correction's journal entry is dated the day the assertion is the
        # CLOSING BALANCE for, read off the fact rather than re-derived from
        # its recording instant (ruling R-DH).  It was
        # ``_utc_civil_date(asserted_at)``, a second statement of a rule
        # ``cash_anchor_facts`` already resolves -- and one that put a
        # late-evening Eastern true-up on the next day's entry.
        observed_on = correction.observed_on
        key = (
            ref_cache.posting_source_id(source_enum),
            calendar.filing_period(observed_on).period_id,
            observed_on,
        )
        merge_target_legs(
            targets.setdefault(key, {}),
            _account_anchor_correction_target(correction, linked, counter),
        )
    return targets


def reconcile_account_anchor_corrections(
    account: Account,
    scenario_id: int,
    corrections: list[AccountAnchorCorrection],
) -> None:
    """Reconcile an account's opening + true-up corrections to a PRE-WALKED list.

    The reconcile half of the account anchor sync, taking the corrections
    already produced by :func:`._walk.walk_account_ledger` (the
    :mod:`._sync` entry points drive both halves).  Builds the
    per-``(source kind, pay period, date)`` target legs
    (:func:`_account_anchor_correction_targets`), reads back what is posted
    (:func:`app.services._posting_reconcile.posted_correction_legs`, scoped
    to the account's linked ledger), and emits ONE balanced delta per key
    that differs
    (:func:`app.services._posting_reconcile.emit_correction_deltas`, the loop
    shared with the loan twin) -- posting a new opening / true-up, adjusting
    a correction whose ``ledger_before`` moved (a pre-assertion source
    changed), or reversing one a matching balance retired.

    Idempotent and self-healing: a re-run at the same state writes nothing
    (every delta is zero).  Touches ONLY the account's own linked ledger and
    the counter rows its own corrections book into.  An empty *corrections*
    list (a missing or history-less account) or an owner that cannot be
    resolved is a no-op.  Flushes but does not commit (the caller owns the
    transaction).

    **This is also what MIGRATES ruling R-FO's re-pointing, with no backfill.**
    The posted side is read per correction key across EVERY ledger the key
    touches, so an account whose true-ups are posted against ``anchor_equity``
    while the dispatch now names ``unrealized_change`` produces exactly one
    balanced delta per key -- reversing the equity leg, posting the new one --
    and the linked leg's delta is zero and is dropped.  The deploy runs this
    for every non-loan account
    (``account_posting_service.backfill_all_account_anchor_postings``), so the
    move lands in the same deploy as the migration that seeds the ref rows.

    **It takes the ACCOUNT, not its id**, since ruling R-FO gave the counter
    leg a dispatch over the account's projection kind: the caller
    (:func:`._sync.sync_account_anchor_postings`) has already loaded and
    classified the row to decide whether to sync at all, so taking the id here
    would re-query it -- and passing the object rather than an ``(id, object)``
    pair leaves nothing for a caller to mis-pair, which is the argument
    :func:`app.services._posting_reconcile.filing_calendar_for` makes for
    handing back the owner WITH the calendar.

    Args:
        account: The non-loan :class:`~app.models.account.Account` whose
            corrections to reconcile, with ``account_type`` loaded.
        scenario_id: The budget scenario to reconcile within.
        corrections: The account's corrections from
            :func:`._walk.walk_account_ledger`.

    Raises:
        PostingError: If the account has no linked ledger account (a broken
            chart-of-accounts pairing).
        PayCalendarError: The owner has no MATERIALISED pay period, so a
            correction's ``NOT NULL`` ``pay_period_id`` has nothing to point at
            -- refused by
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period`, which
            is the ONE place that question is asked (developer ruling
            2026-08-10).  Finding **N-192** is why it fails loud.
        ValueError: If the counter dispatch has no rule for the account's
            projection kind, or its resolver rejects the account (see
            :func:`_counter_ledger_accounts`).
    """
    if not corrections:
        return
    # The owner's whole calendar, loaded ONCE for the target keys through the
    # same door the loan twin takes (plan step C2-d), so the two halves cannot
    # come to file an anchor correction under different periods OR against
    # differently-loaded calendars.  It resolves the OWNER from the account and
    # returns both, so the two cannot be paired wrongly.  It refuses nothing:
    # ``filing_period`` below is the one place "is there a period to point at"
    # is asked, and a second copy of that test here had already drifted from it
    # (developer ruling 2026-08-10).
    resolved = filing_calendar_for(account.id)
    if resolved is None:
        return
    owner_id, calendar = resolved
    linked = _ledger_account_for(account.id)
    emit_correction_deltas(
        owner_id,
        scenario_id,
        target=_account_anchor_correction_targets(
            corrections,
            calendar,
            linked,
            _counter_ledger_accounts(owner_id, account, corrections),
        ),
        posted=posted_correction_legs(
            account.id,
            scenario_id,
            [
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
            ],
        ),
    )
