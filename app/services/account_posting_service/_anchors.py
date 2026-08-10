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
    anchor-equity ledger  (ledger_before - anchor_balance)   [opening | trueup]
                          -----------------------------------
                          0

The delta is ledger-native sign -- it holds for Asset AND Liability non-loan
accounts with no class branch, exactly like the engine.  A zero delta books
nothing (a fresh $0 account mints no entries and no ``anchor_equity`` row,
staying hard-deletable).

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
so is load-bearing**: ``_assertion_sums`` folds ``anchor_corrections[1:]``,
excluding the OPENING because ruling R-I moves it into the fold's seed, while
the ledger keeps an ``account_opening`` entry in a period -- so an unscoped
comparison could not have agreed on 61 and a draft of this docstring implied
one had.  The agreement is pinned by
``TestLedgerAgreesWithTheGridOnAssertionPeriods`` rather than left as a
measurement in prose (finding N-169's other half).  A mis-filed row is a defect
in the ROW (finding N-168), repaired at its source -- and until it is, the
ledger states the day's truth rather than inheriting the row's error.

Two same-day assertions always share a containing period, so they still merge
into one entry; separating THOSE needs an identity the ledger does not carry
yet (plan step X-ai-s).
"""

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.services import ledger_account_service
from app.services._posting_reconcile import (
    CorrectionKey,
    LegMap,
    emit_correction_deltas,
    filing_calendar_for,
    merge_target_legs,
    posted_correction_legs,
)
from app.services.cash_ledger import CashAnchorFact
from app.services.pay_calendar import PayCalendar
from app.services.posting_reads import _ledger_account_for

from ._walk import AccountAnchorCorrection


def _account_correction_kinds(
    fact: CashAnchorFact,
) -> tuple[PostingSourceEnum, PostingKindEnum]:
    """Return the (journal source kind, posting leg kind) for an anchor's correction.

    The account's earliest history row books the OPENING (source
    ``account_opening``, leg kind ``opening``); every later row is a user
    balance assertion and books a TRUE-UP (source ``account_trueup``, leg
    kind ``trueup``).  The leg kinds are the same ``opening`` / ``trueup``
    pair the loan corrections use (REUSED by design -- the journal SOURCE
    distinguishes account from loan corrections).  Keyed off the fact's
    ``is_opening`` flag, which :func:`app.services.cash_ledger.cash_anchor_facts`
    derives from the ``(observed_on, created_at, id)`` order -- BUSINESS date
    first.  *This said ``(created_at, id)`` until plan step X-an-b: that was the
    key before plan step 2 made ``observed_on`` a user-supplied column, and
    trusting it here would reproduce the defect that once tagged a ``$1,307.66``
    true-up as the account's OPENING.*

    Args:
        fact: The :class:`~app.services.cash_ledger.CashAnchorFact` whose
            correction kinds
            to resolve.

    Returns:
        ``(PostingSourceEnum, PostingKindEnum)`` -- ``(ACCOUNT_OPENING,
        OPENING)`` for the earliest row, else ``(ACCOUNT_TRUEUP, TRUEUP)``.
    """
    if fact.is_opening:
        return PostingSourceEnum.ACCOUNT_OPENING, PostingKindEnum.OPENING
    return PostingSourceEnum.ACCOUNT_TRUEUP, PostingKindEnum.TRUEUP


def _account_anchor_correction_target(
    correction: AccountAnchorCorrection, owner_id: int,
) -> LegMap:
    """Build the two-leg target for one anchor correction, or empty when it books nothing.

    The linked leg is ``anchor_balance - ledger_before`` (tagged ``opening``
    or ``trueup``); the anchor-equity leg is its negative, so the two sum to
    zero and the linked ledger's implied balance moves from ``ledger_before``
    to the asserted value.  A correction whose ``ledger_before`` already
    equals the anchor balance books NOTHING -- an empty target, so no zero
    leg is written and no anchor-equity account is minted for it.

    The per-account anchor-equity account is resolved lazily (created on
    first use) only when the correction is non-zero, via
    :func:`app.services.ledger_account_service.get_or_create_anchor_equity_account`
    -- whose non-loan guard is also the structural guarantee this package
    never books onto a loan's chart.

    Args:
        correction: The anchor correction from :func:`._walk.walk_account_ledger`.
        owner_id: The account owner's user id (for the anchor-equity account).

    Returns:
        ``{ledger_account_id: (amount, posting_kind_id)}`` (the two balanced
        legs, or empty when the correction books nothing).

    Raises:
        PostingError: If the account has no linked ledger account (a broken
            chart-of-accounts pairing).
        ValueError: If the anchor-equity resolver rejects the account (not
            owned by *owner_id*, or an amortizing loan -- both broken
            invariants at this point, the walk having already classified).
    """
    fact = correction.anchor
    delta = fact.anchor_balance - correction.ledger_before
    if delta == 0:
        return {}
    _source_enum, posting_kind_enum = _account_correction_kinds(fact)
    posting_kind_id = ref_cache.posting_kind_id(posting_kind_enum)
    linked = _ledger_account_for(fact.account_id)
    equity = ledger_account_service.get_or_create_anchor_equity_account(
        owner_id, fact.account_id,
    )
    return {
        linked.id: (delta, posting_kind_id),
        equity.id: (-delta, posting_kind_id),
    }


def _account_anchor_correction_targets(
    corrections: list[AccountAnchorCorrection],
    owner_id: int,
    calendar: PayCalendar,
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
        owner_id: The account owner's user id.
        calendar: The owner's whole pay calendar, from
            :func:`app.services._posting_reconcile.filing_calendar_for`.
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period` is the
            one place the "is there a period to point at" question is asked and
            refused, so this carries no precondition of its own -- an earlier
            draft claimed the loader had already checked, which was a second
            statement of one predicate and the two had drifted.

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {ledger_account_id:
        (amount, kind_id)}}``.
    """
    targets: dict[CorrectionKey, LegMap] = {}
    for correction in corrections:
        source_enum, _posting_kind = _account_correction_kinds(
            correction.anchor,
        )
        # The correction's journal entry is dated the day the assertion is the
        # CLOSING BALANCE for, read off the fact rather than re-derived from
        # its recording instant (ruling R-DH).  It was
        # ``_utc_civil_date(asserted_at)``, a second statement of a rule
        # ``cash_anchor_facts`` already resolves -- and one that put a
        # late-evening Eastern true-up on the next day's entry.
        observed_on = correction.anchor.observed_on
        key = (
            ref_cache.posting_source_id(source_enum),
            calendar.filing_period(observed_on).period_id,
            observed_on,
        )
        merge_target_legs(
            targets.setdefault(key, {}),
            _account_anchor_correction_target(correction, owner_id),
        )
    return targets


def reconcile_account_anchor_corrections(
    account_id: int,
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
    (every delta is zero).  Touches ONLY the account's own linked and
    anchor-equity ledgers.  An empty *corrections* list (a missing or
    history-less account) or an owner that cannot be resolved is a no-op.
    Flushes but does not commit (the caller owns the transaction).

    Args:
        account_id: The non-loan account whose corrections to reconcile.
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
        ValueError: If the anchor-equity resolver rejects the account (see
            :func:`_account_anchor_correction_target`).
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
    resolved = filing_calendar_for(account_id)
    if resolved is None:
        return
    owner_id, calendar = resolved
    linked = _ledger_account_for(account_id)
    emit_correction_deltas(
        owner_id,
        scenario_id,
        target=_account_anchor_correction_targets(
            corrections, owner_id, calendar,
        ),
        posted=posted_correction_legs(
            linked.id,
            scenario_id,
            [
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
            ],
        ),
    )
