"""Account-anchor correction posting: the opening and true-up reconcile.

Posts a non-loan account's anchor corrections -- the once-per-account OPENING
(its earliest :class:`~app.models.account.AccountAnchorHistory` row) and a
TRUE-UP per later row -- into the append-only double-entry ledger, so the
account's linked ledger sums to an ABSOLUTE balance:  the latest anchor
assertion plus the settled facts recorded after that assertion moment.  This
is the shipped loan genesis pattern generalized to every non-loan account
(Build-Order Step 5); after it, the trial balance closes app-wide.

Every anchor the account carries posts one balanced correction, whose size is
the walk's own
:attr:`~app.services.cash_ledger.CashAnchorCorrection.delta`
(``anchor_balance - balance_before``)::

    linked ledger         (anchor_balance - balance_before)  [opening | trueup]
    anchor-equity ledger  (balance_before - anchor_balance)  [opening | trueup]
                          -----------------------------------
                          0

**The walk is ``cash_ledger``'s, since plan step X-d (ruling R-H).**  This
package had its own, folding the POSTED copy of the account's events; that made
the corrections a function of the ledger they are then written into -- a copy
grading itself -- and left two representations of one event set to be held in
step.  The writer now consumes
:func:`app.services.cash_ledger.walk_cash_ledger`, the same walk the read fold
folds, so the projection and the posted ledger cannot drift by construction
rather than by a test.  What GRADES the result is the checked-projection assert
in :mod:`._sync`.

The delta is ledger-native sign -- it holds for Asset AND Liability non-loan
accounts with no class branch, exactly like the engine.  A zero delta books
nothing (a fresh $0 account mints no entries and no ``anchor_equity`` row,
staying hard-deletable).

**Reconciled to target, keyed by (source kind, entry date).**  An anchor
correction has no concrete source FK to key on, so the reconcile keys each
anchor's entry by its ``source_kind_id`` (``account_opening`` vs.
``account_trueup``) and its ``entry_date`` -- the fact's own
:attr:`~app.services.cash_ledger.CashAnchorFact.observed_on`, the civil day the
assertion is the CLOSING BALANCE for (ruling R-DH).  It was
``_utc_civil_date(asserted_at)`` until 2026-07-31: a second statement of a rule
the fact already resolves, and one that dated a late-evening Eastern true-up
into the next day, putting the correction on a different day from the settles it
corrects.  Two same-day same-kind
anchors merge to one target landing on the later value, mirroring the F-103
unique-index semantics and the loan merge behavior.  A pre-true-up source
whose net later changes moves the walk's ``ledger_before``; re-running the
sync re-derives the target and posts the balancing delta, so a stale
correction self-heals.  The entry's ``pay_period_id`` is the history row's
own period (R2: the period of what it corrects) -- no period resolution can
fail, because that FK is NOT NULL by referential integrity.  Flushes but
never commits -- the caller owns the transaction.
"""

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.services import ledger_account_service
from app.services._posting_reconcile import (
    CorrectionKey,
    LegMap,
    account_owner_id,
    delta_legs,
    emit_anchor_correction_entry,
    merge_target_legs,
    posted_correction_legs,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import CashAnchorCorrection, CashAnchorFact
from app.services.posting_reads import _ledger_account_for


class _AnchorEquityLedger:
    """One account's anchor-equity ledger id, resolved AT MOST once per sync.

    **Ruling R-DL.**  The equity account and the linked account were each
    resolved INSIDE the per-correction loop, so an account with 53 non-zero
    corrections issued 106 SELECTs for the same two rows -- measured 2026-08-02
    on the real Checking account at ``64.5 ms`` of a ``66.3 ms`` reconcile that
    writes nothing.  Every true-up, every account create and the deploy-wide
    backfill paid it, and it is what made plan step X-d's "never skip the
    self-heal" (ruling R-DK) look unaffordable at ``70.9 ms`` a settle.

    **It stays LAZY, and that is not an optimization -- it is behaviour.**
    :func:`app.services.ledger_account_service.get_or_create_anchor_equity_account`
    MINTS the row on first use, and a fresh ``$0`` account whose every
    correction books nothing must mint no ``anchor_equity`` row at all (it
    stays hard-deletable, which :func:`_account_anchor_correction_target`
    documents).  So the id is resolved on the first NON-ZERO correction and
    reused from there, rather than resolved up front with the loop's other
    invariants.
    """

    def __init__(self, account_id: int, owner_id: int) -> None:
        """Record the account and owner; resolve nothing yet.

        Args:
            account_id: The non-loan account whose corrections are being
                reconciled.
            owner_id: That account's owner (the equity account's owner).
        """
        self._account_id = account_id
        self._owner_id = owner_id
        self._ledger_id: int | None = None

    def ledger_id(self) -> int:
        """Return the anchor-equity ledger account id, minting it on first call.

        Returns:
            The ``budget.ledger_accounts.id`` of this account's anchor-equity
            account.

        Raises:
            ValueError: If the resolver rejects the account (not owned by the
                owner, or an amortizing loan -- both broken invariants by this
                point, the reconcile having already classified the kind).
        """
        if self._ledger_id is None:
            self._ledger_id = (
                ledger_account_service.get_or_create_anchor_equity_account(
                    self._owner_id, self._account_id,
                ).id
            )
        return self._ledger_id


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
    sets on the FIRST row of its ``(observed_on, created_at, id)`` load -- the
    BUSINESS-date order, not the recording order.  The two agreed for free until
    plan step 2 made ``observed_on`` user-supplied, and getting it wrong posted a
    ``$1,307.66`` true-up to the ledger tagged as the account's OPENING (finding
    N-133 / R1).

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
    correction: CashAnchorCorrection,
    linked_ledger_id: int,
    equity: _AnchorEquityLedger,
) -> LegMap:
    """Build the two-leg target for one anchor correction, or empty when it books nothing.

    The linked leg is the correction's own
    :attr:`~app.services.cash_ledger.CashAnchorCorrection.delta`
    (``anchor_balance - balance_before``, tagged ``opening`` or ``trueup``);
    the anchor-equity leg is its negative, so the two sum to zero and the
    linked ledger's implied balance moves from ``balance_before`` to the
    asserted value.  A correction whose walk already landed on the asserted
    balance books NOTHING -- an empty target, so no zero leg is written and no
    anchor-equity account is minted for it, and the account stays
    hard-deletable.

    **The delta is READ off the correction rather than recomputed** (plan step
    X-d).  ``CashAnchorCorrection.delta`` is the same subtraction, stated once
    on the record three other readers already take it from (the re-key, the
    fold's R-I seed, the period view's assertion component); recomputing it
    here made this writer a fourth statement of it.

    Both ledger ids arrive resolved (ruling R-DL): the linked one from the
    caller, which needs it anyway to read the posted side back, and the equity
    one through :class:`_AnchorEquityLedger`, which mints it at most once per
    sync AND only when a correction actually books.  They were each resolved
    per correction, which is how 53 corrections issued 106 SELECTs for two rows.

    Args:
        correction: One :class:`~app.services.cash_ledger.CashAnchorCorrection`
            from :func:`app.services.cash_ledger.walk_cash_ledger`.
        linked_ledger_id: The account's LINKED ledger account id.
        equity: The account's lazy anchor-equity ledger resolver.

    Returns:
        ``{ledger_account_id: (amount, posting_kind_id)}`` (the two balanced
        legs, or empty when the correction books nothing).

    Raises:
        ValueError: If the anchor-equity resolver rejects the account (not
            owned by the owner, or an amortizing loan -- both broken
            invariants at this point, the reconcile having already classified).
    """
    delta = correction.delta
    if delta == 0:
        return {}
    _source_enum, posting_kind_enum = _account_correction_kinds(
        correction.anchor,
    )
    posting_kind_id = ref_cache.posting_kind_id(posting_kind_enum)
    return {
        linked_ledger_id: (delta, posting_kind_id),
        equity.ledger_id(): (-delta, posting_kind_id),
    }


def _account_anchor_correction_targets(
    corrections: list[CashAnchorCorrection],
    linked_ledger_id: int,
    equity: _AnchorEquityLedger,
) -> tuple[dict[CorrectionKey, LegMap], dict[CorrectionKey, int]]:
    """Merge an account's corrections into per-(source, date) targets + periods.

    Groups every correction by its ``(source_kind_id, civil entry_date)``
    key and sums the legs within each group
    (:func:`app.services._posting_reconcile.merge_target_legs`), so two
    same-day same-kind anchors net to a single balanced target that lands
    the ledger on the LATER value -- exactly the combined jump they express
    (each correction's delta already accounts for the prior one).  A
    correction that books nothing still creates its key with an empty leg
    map, so an entry it previously posted (now matching) is reversed to
    zero by the reconcile.

    Alongside the leg targets, records each key's entry ``pay_period_id`` --
    the history row's own period, the LATEST fact's row winning a merged key
    (the corrections arrive chronological, so the last write is the latest).

    Args:
        corrections: The account's corrections from
            :func:`app.services.cash_ledger.walk_cash_ledger`, chronological.
        linked_ledger_id: The account's LINKED ledger account id.
        equity: The account's lazy anchor-equity ledger resolver.

    Returns:
        ``(targets, periods)`` --
        ``{(source_kind_id, entry_date): {ledger_account_id: (amount,
        kind_id)}}`` and ``{(source_kind_id, entry_date): pay_period_id}``.
    """
    targets: dict[CorrectionKey, LegMap] = {}
    periods: dict[CorrectionKey, int] = {}
    for correction in corrections:
        source_enum, _posting_kind = _account_correction_kinds(
            correction.anchor,
        )
        key = (
            # The correction's journal entry is dated the day the assertion is
            # the CLOSING BALANCE for, read off the fact rather than re-derived
            # from its recording instant (ruling R-DH).  It was
            # ``_utc_civil_date(asserted_at)``, a second statement of a rule
            # ``cash_anchor_facts`` already resolves -- and one that put a
            # late-evening Eastern true-up on the next day's entry.
            # ``.civil_day`` is the named unwrap of the assertion's
            # ``ObservedOn`` (ruling R-DJ): ``journal_entries.entry_date`` is a
            # plain ``DATE`` column, which is a legitimate raw-date use and is
            # visible as one here.
            ref_cache.posting_source_id(source_enum),
            correction.anchor.observed_on.civil_day,
        )
        periods[key] = correction.anchor.pay_period_id
        merge_target_legs(
            targets.setdefault(key, {}),
            _account_anchor_correction_target(
                correction, linked_ledger_id, equity,
            ),
        )
    return targets, periods


def _posted_only_key_period_id(
    linked_ledger_id: int, scenario_id: int, key: CorrectionKey,
) -> int:
    """Return the period of the posted correction a target-less key reverses.

    A key present in the posted ledger but absent from the walked targets
    has no history row to take a period from (its anchor rows are gone --
    unreachable through the linear lifecycle, since a history row and its
    correction entry share a pay period and CASCADE together, but the
    reconcile's union loop is defensive).  The R2-faithful period for its
    reversal is the period of the postings it reverses, read back from the
    LATEST posted correction entry for the key.

    Args:
        linked_ledger_id: The account's LINKED ledger account id (scopes
            the key to this account's corrections).
        scenario_id: The budget scenario to scope to.
        key: The ``(source_kind_id, entry_date)`` being reversed.  The
            caller guarantees at least one posted entry matches (the key
            came from the posted map), so the lookup cannot miss.

    Returns:
        The latest posted correction entry's ``pay_period_id``.
    """
    entry_ids = (
        db.session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id == linked_ledger_id)
    )
    return (
        db.session.query(JournalEntry.pay_period_id)
        .filter(
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id == key[0],
            JournalEntry.entry_date == key[1],
            JournalEntry.id.in_(entry_ids),
        )
        .order_by(JournalEntry.id.desc())
        .limit(1)
        .scalar()
    )


def reconcile_account_anchor_corrections(
    account_id: int,
    scenario_id: int,
    corrections: list[CashAnchorCorrection],
) -> None:
    """Reconcile an account's opening + true-up corrections to a PRE-WALKED list.

    The reconcile half of the account anchor sync, taking the corrections
    already produced by :func:`app.services.cash_ledger.walk_cash_ledger` (the
    :mod:`._sync` entry points drive both halves).  Builds the
    per-``(source kind, date)`` target legs
    (:func:`_account_anchor_correction_targets`), reads back what is posted
    (:func:`app.services._posting_reconcile.posted_correction_legs`, scoped
    to the account's linked ledger), and emits ONE balanced delta per key
    that differs -- posting a new opening / true-up, adjusting a correction
    whose ``balance_before`` moved (a pre-assertion source changed), or
    reversing one a matching balance retired.

    **It REFUSES an amortizing loan, and that refusal moved here at plan step
    X-d.**  It lived on the deleted postings walk, which this package no longer
    has: the walk it now consumes is ``cash_ledger``'s, which is deliberately
    kind-blind (a running-balance replay is a property of the rows, not of the
    account's kind, and the cash-flow seam view consults no kind either).  Which
    correction FAMILY a loan's anchors book into is a WRITE concern, so the
    guard belongs on the writer -- exactly where ``cash_ledger._walk``'s own
    module docstring said it would land.  Booking here as well as through
    :mod:`app.services.loan_posting_service` would double-book a loan's balance
    across two families on two charts.

    Idempotent and self-healing: a re-run at the same state writes nothing
    (every delta is zero).  Touches ONLY the account's own linked and
    anchor-equity ledgers.  An empty *corrections* list (a history-less
    account) or an owner that cannot be resolved is a no-op.
    Flushes but does not commit (the caller owns the transaction).

    Args:
        account_id: The non-loan account whose corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.
        corrections: The account's corrections from
            :func:`app.services.cash_ledger.walk_cash_ledger`.

    Raises:
        ValueError: If *account_id* is an amortizing loan (loans book their
            anchor corrections through the loan posting package, never here),
            or if the anchor-equity resolver rejects the account (see
            :func:`_account_anchor_correction_target`).
        PostingError: If the account has no linked ledger account (a broken
            chart-of-accounts pairing).
    """
    if not corrections:
        return
    account = db.session.query(Account).filter_by(id=account_id).first()
    if account is not None and (
        classify_account(account) is AccountProjectionKind.AMORTIZING
    ):
        raise ValueError(
            f"cannot reconcile account anchor corrections: account "
            f"id={account_id} is an amortizing loan (loans book their anchor "
            f"corrections through the loan posting package, never the account "
            f"correction family)"
        )
    owner_id = account_owner_id(account_id)
    if owner_id is None:
        return
    linked = _ledger_account_for(account_id)
    targets, target_periods = _account_anchor_correction_targets(
        corrections, linked.id, _AnchorEquityLedger(account_id, owner_id),
    )
    posted = posted_correction_legs(
        linked.id,
        scenario_id,
        [
            ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
            ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
        ],
    )
    for key in sorted(set(targets) | set(posted)):
        legs = delta_legs(targets.get(key, {}), posted.get(key, {}))
        if not legs:
            continue
        # .get, then the lazy fallback: the posted-only lookup is a query
        # and must not run for the (normal) keys a history row covers.
        pay_period_id = target_periods.get(key)
        if pay_period_id is None:
            pay_period_id = _posted_only_key_period_id(
                linked.id, scenario_id, key,
            )
        emit_anchor_correction_entry(
            owner_id, scenario_id, key, pay_period_id, legs,
        )
