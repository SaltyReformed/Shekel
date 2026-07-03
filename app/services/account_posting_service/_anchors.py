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

**Reconciled to target, keyed by (source kind, entry date).**  An anchor
correction has no concrete source FK to key on, so the reconcile keys each
anchor's entry by its ``source_kind_id`` (``account_opening`` vs.
``account_trueup``) and its ``entry_date`` (the assertion instant's UTC civil
date -- the ``AnchorPoint.as_of_date`` convention; the late-evening-ET/UTC-day
edge is identical to shipped loan corrections).  Two same-day same-kind
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
from app.services.posting_service import _ledger_account_for, _utc_civil_date

from ._walk import AccountAnchorCorrection, AccountAnchorFact


def _account_correction_kinds(
    fact: AccountAnchorFact,
) -> tuple[PostingSourceEnum, PostingKindEnum]:
    """Return the (journal source kind, posting leg kind) for an anchor's correction.

    The account's earliest history row books the OPENING (source
    ``account_opening``, leg kind ``opening``); every later row is a user
    balance assertion and books a TRUE-UP (source ``account_trueup``, leg
    kind ``trueup``).  The leg kinds are the same ``opening`` / ``trueup``
    pair the loan corrections use (REUSED by design -- the journal SOURCE
    distinguishes account from loan corrections).  Keyed off the fact's
    ``is_opening`` flag, which :func:`._walk._anchor_facts` derives from the
    ``(created_at, id)`` order.

    Args:
        fact: The :class:`._walk.AccountAnchorFact` whose correction kinds
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
    corrections: list[AccountAnchorCorrection], owner_id: int,
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
            :func:`._walk.walk_account_ledger`, chronological.
        owner_id: The account owner's user id.

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
            ref_cache.posting_source_id(source_enum),
            _utc_civil_date(correction.anchor.asserted_at),
        )
        periods[key] = correction.anchor.pay_period_id
        merge_target_legs(
            targets.setdefault(key, {}),
            _account_anchor_correction_target(correction, owner_id),
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
    corrections: list[AccountAnchorCorrection],
) -> None:
    """Reconcile an account's opening + true-up corrections to a PRE-WALKED list.

    The reconcile half of the account anchor sync, taking the corrections
    already produced by :func:`._walk.walk_account_ledger` (the
    :mod:`._sync` entry points drive both halves).  Builds the
    per-``(source kind, date)`` target legs
    (:func:`_account_anchor_correction_targets`), reads back what is posted
    (:func:`app.services._posting_reconcile.posted_correction_legs`, scoped
    to the account's linked ledger), and emits ONE balanced delta per key
    that differs -- posting a new opening / true-up, adjusting a correction
    whose ``ledger_before`` moved (a pre-assertion source changed), or
    reversing one a matching balance retired.

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
        ValueError: If the anchor-equity resolver rejects the account (see
            :func:`_account_anchor_correction_target`).
    """
    if not corrections:
        return
    owner_id = account_owner_id(account_id)
    if owner_id is None:
        return
    targets, target_periods = _account_anchor_correction_targets(
        corrections, owner_id,
    )
    linked = _ledger_account_for(account_id)
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
