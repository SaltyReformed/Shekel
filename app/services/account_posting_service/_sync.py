"""Account anchor-posting orchestration: per-scenario sync, all-scenarios, per-user.

The entry points that drive a non-loan account's anchor reconcile -- one walk
(:func:`._walk.walk_account_ledger`) feeding one reconcile
(:func:`._anchors.reconcile_account_anchor_corrections`) per (account,
scenario):

* :func:`sync_account_anchor_postings` -- one scenario.
* :func:`sync_account_anchor_postings_all_scenarios` -- the owner's baseline
  scenario PLUS every scenario holding an entry that posts on the account's
  linked ledger.  Anchor history lives on the ACCOUNT, not a scenario, so an
  account-global event (create, true-up, direct anchor edit) re-bases the
  corrections in every scenario at once -- the cash analogue of the loan
  rule; R8 owns the residual multi-scenario policy.
* :func:`resync_user_account_anchor_postings` -- every non-loan account one
  user owns, for the single-user chokepoints (the pay-period reset, whose
  CASCade disposed the user's correction entries with the wiped periods, and
  the ``create_baseline`` recovery path, so openings are not silently
  stranded for a user who lacked a baseline at account-create time).
* :func:`self_heal_anchor_corrections` -- the effect-time self-heal the
  tails of ``posting_service.sync_transfer_postings`` /
  ``sync_transaction_postings`` invoke after emitting source delta entries:
  a source whose posted effect changed at-or-before an account's latest
  anchor assertion moved that anchor's walked ``ledger_before``, so the
  stale correction is re-derived in the same transaction.

An amortizing loan is a documented NO-OP at every entry point (never an
error: the chokepoints legitimately iterate all of a user's accounts): loans
book their anchor corrections through
:mod:`app.services.loan_posting_service` onto their per-loan
``equity_opening`` account, and the two correction families must stay
disjoint or a loan's balance would double-book.  The per-user enumerator
excludes loans structurally (``has_amortization IS FALSE`` -- exactly the
column ``classify_account`` branches on).

Idempotent and self-healing via reconcile-to-target.  Flushes but never
commits -- the caller owns the transaction boundary.
"""

import logging
from collections.abc import Iterable
from datetime import datetime

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.ref import AccountType
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.posting_reads import _ledger_account_for
from app.services.scenario_resolver import get_baseline_scenario

from ._anchors import reconcile_account_anchor_corrections
from ._walk import _as_utc_instant, _period_start_instant, walk_account_ledger

logger = logging.getLogger(__name__)


def _load_non_amortizing_account(account_id: int) -> Account | None:
    """Return the account when it is a real, non-loan row; else ``None``.

    The shared entry-point guard: a missing account (deleted between the
    caller's read and the sync) and an amortizing loan (the loan posting
    package's responsibility -- see the module docstring) are both "nothing
    to sync here", never an error, because the lifecycle chokepoints
    legitimately call the sync for every account a user touches.

    Args:
        account_id: The account to load and classify.

    Returns:
        The :class:`~app.models.account.Account` (its ``account_type``
        eager-loaded via the relationship's ``lazy="joined"``), or ``None``
        when absent or amortizing.
    """
    account = db.session.query(Account).filter_by(id=account_id).first()
    if account is None:
        return None
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return None
    return account


def sync_account_anchor_postings(account_id: int, scenario_id: int) -> None:
    """Reconcile one non-loan account's anchor corrections in one scenario.

    The per-scenario chokepoint: walks the account's anchor assertions
    against the scenario's posted sources
    (:func:`._walk.walk_account_ledger`) and reconciles the resulting
    opening / true-up corrections to the ledger
    (:func:`._anchors.reconcile_account_anchor_corrections`).

    Idempotent and self-healing: a re-run at the same state writes nothing.
    Touches ONLY the account's own linked and anchor-equity ledgers.  A
    missing account or an amortizing loan is a no-op (see
    :func:`_load_non_amortizing_account`).  Flushes but does not commit
    (the caller owns the transaction).

    Args:
        account_id: The non-loan account whose corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.
    """
    if _load_non_amortizing_account(account_id) is None:
        return
    reconcile_account_anchor_corrections(
        account_id, scenario_id, walk_account_ledger(account_id, scenario_id),
    )


def _scenarios_with_account_postings(linked_ledger_id: int) -> set[int]:
    """Return the scenarios holding an entry that posts on one linked ledger.

    The distinct ``scenario_id`` set over the journal entries with a leg on
    the given linked ledger -- source entries AND already-posted corrections
    alike, so an account-global change re-bases (or reverses) corrections in
    every scenario the account's ledger is live in, and a stale correction
    in a scenario whose sources have all reverted is still revisited.

    Args:
        linked_ledger_id: The account's LINKED ledger account id.

    Returns:
        The distinct scenario ids (unordered; the caller sorts).
    """
    rows = (
        db.session.query(JournalEntry.scenario_id)
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(Posting.ledger_account_id == linked_ledger_id)
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def sync_account_anchor_postings_all_scenarios(account_id: int) -> None:
    """Reconcile a non-loan account's anchor corrections across EVERY scenario.

    The account-GLOBAL chokepoint entry point (account create, anchor
    true-up, the direct anchor edit): anchor history lives on the account,
    not a scenario, so such a change re-bases the corrections in every
    scenario the account's ledger is live in.  Loops
    :func:`sync_account_anchor_postings` over the union of:

    * every scenario holding an entry that posts on the account's linked
      ledger (:func:`_scenarios_with_account_postings`), and
    * the owner's BASELINE scenario -- so a fresh account with no posted
      activity still gets its opening posted.  Corrections are per-scenario
      (postings are scenario-scoped); today only the baseline exists, so
      this is one entry in practice, but it is forward-compatible with
      scenario clone.

    A baseline-less owner with no posted activity syncs nothing; the skip is
    logged loudly because production users get a baseline at registration --
    only test fixtures (and the pre-``create_baseline`` recovery state) lack
    one, and a silently stranded opening would surface as a trial-balance
    gap much later.  A missing account or an amortizing loan is a quiet
    no-op (see :func:`_load_non_amortizing_account`).  Flushes but does not
    commit (the caller owns the transaction).

    Args:
        account_id: The non-loan account whose corrections to reconcile
            across every scenario its ledger is live in.
    """
    account = _load_non_amortizing_account(account_id)
    if account is None:
        return
    scenario_ids = _scenarios_with_account_postings(
        _ledger_account_for(account_id).id,
    )
    baseline = get_baseline_scenario(account.user_id)
    if baseline is not None:
        scenario_ids.add(baseline.id)
    elif not scenario_ids:
        logger.warning(
            "Account %d (user %d) has no baseline scenario and no posted "
            "ledger activity; skipping its anchor-correction sync (the "
            "opening will post when a baseline exists -- see "
            "resync_user_account_anchor_postings).",
            account_id, account.user_id,
        )
        return
    for scenario_id in sorted(scenario_ids):
        sync_account_anchor_postings(account_id, scenario_id)


def _latest_anchor_instant(account_id: int) -> datetime | None:
    """Return an account's latest anchor assertion instant (aware UTC), or ``None``.

    The self-heal predicate's right-hand side: ``MAX(created_at)`` over the
    account's :class:`~app.models.account.AccountAnchorHistory` rows -- the
    assertion instant of the row ``balance_resolver.resolve_anchor`` resolves
    -- normalized through the walk's UTC convention
    (:func:`._walk._as_utc_instant`).  One indexed lookup
    (``idx_anchor_history_account`` covers ``(account_id, created_at)``).

    Args:
        account_id: The account whose latest assertion instant to resolve.

    Returns:
        The aware-UTC instant, or ``None`` for an account with no anchor
        history (fixture-only) or a missing account.
    """
    value = (
        db.session.query(db.func.max(AccountAnchorHistory.created_at))
        .filter(AccountAnchorHistory.account_id == account_id)
        .scalar()
    )
    if value is None:
        return None
    return _as_utc_instant(value)


def self_heal_anchor_corrections(
    account_ids: Iterable[int],
    scenario_id: int,
    delta_entries: list[JournalEntry],
) -> None:
    """Resync the accounts whose anchor corrections *delta_entries* may have staled.

    The effect-time self-heal (Build-Order Step 5, C6): the tails of
    ``posting_service.sync_transfer_postings`` /
    ``sync_transaction_postings`` (which
    ``reverse_postings_before_delete`` routes through) call this after
    emitting source delta entries.  A source change attributed at-or-before
    an account's latest anchor assertion moves that anchor's walked
    ``ledger_before``, so its posted correction is stale until re-derived;
    a change attributed after every assertion rides on top and no
    correction moves.

    **The predicate reads the emitted entries' ``entry_date``s** -- resync
    an account iff the earliest emitted date's midnight-UTC instant is
    at-or-before the account's latest assertion instant.  A settle-side
    entry is dated at the source's CURRENT attribution civil date, and a
    reversal entry inherits the latest date it reverses (the R2 rule) --
    the OLD attribution's civil date -- so both sides of every lifecycle
    delta are covered, including the revert of an early-settled
    future-period source whose CURRENT attribution (its period start) sits
    after the anchor while the reversed effect preceded it.  A
    day-granular midnight comparison over-fires only for same-UTC-day
    changes, where the resync is an idempotent no-op walk.

    An account with no anchor history never fires; an amortizing loan
    passes the instant check (loans carry anchor history rows too) and is
    then structurally skipped by :func:`sync_account_anchor_postings`.
    Flushes but does not commit (the caller owns the transaction).

    Args:
        account_ids: The real accounts the emitted deltas' LINKED legs can
            touch (the transfer's two endpoints / the transaction's cash
            account -- both immutable on their source rows).
        scenario_id: The scenario the deltas were emitted in (postings are
            scenario-scoped, so only that scenario's corrections can have
            moved).
        delta_entries: The just-emitted delta
            :class:`~app.models.journal_entry.JournalEntry` list (empty ->
            no-op).
    """
    if not delta_entries:
        return
    earliest = min(
        _period_start_instant(entry.entry_date) for entry in delta_entries
    )
    for account_id in sorted(set(account_ids)):
        latest = _latest_anchor_instant(account_id)
        if latest is not None and earliest <= latest:
            sync_account_anchor_postings(account_id, scenario_id)


def _non_loan_account_ids_for_user(user_id: int) -> list[int]:
    """Return every non-loan account id one user owns, ascending.

    The per-user enumerator for :func:`resync_user_account_anchor_postings`:
    all of the user's accounts whose type is not amortizing
    (``has_amortization IS FALSE`` -- exactly the column
    ``classify_account`` maps to ``AMORTIZING``, so the SQL filter and the
    per-account guard can never disagree).  Deliberately NOT filtered by
    ``is_active``: an archived account's posted corrections must keep
    reconciling (archiving disables new activity, it does not erase posted
    facts).  The loan mirror is
    ``loan_loaders.load_loan_account_ids_for_user``.

    Args:
        user_id: The owning user whose non-loan accounts to enumerate.

    Returns:
        The account ids, ascending.
    """
    rows = (
        db.session.query(Account.id)
        .join(AccountType, Account.account_type_id == AccountType.id)
        .filter(
            Account.user_id == user_id,
            AccountType.has_amortization.is_(False),
        )
        .order_by(Account.id)
        .all()
    )
    return [row[0] for row in rows]


def resync_user_account_anchor_postings(user_id: int) -> list[int]:
    """Reconcile every non-loan account a SINGLE user owns, across scenarios.

    The per-user chokepoint, mirroring
    ``loan_posting_service.resync_user_loan_postings``: iterates the user's
    non-loan accounts (:func:`_non_loan_account_ids_for_user`) and
    reconciles each through
    :func:`sync_account_anchor_postings_all_scenarios` -- the identical
    go-forward sync, so a re-synced correction is identical to a go-forward
    one by construction.

    Two callers need it: ``pay_period_admin.reset_pay_periods`` (the wipe
    CASCADE-disposed the user's correction entries with their periods and
    ``_reanchor_accounts`` staged one fresh history row per account, so this
    re-derives the openings onto the rebuilt schedule) and
    ``routes.grid.create_baseline`` (the recovery path for baseline-less
    users, so openings skipped at account-create time are not silently
    stranded).  Scoped to one user because both are single-user operations.

    Idempotent and self-healing via reconcile-to-target.  Flushes but does
    NOT commit -- the caller owns the transaction boundary.

    Args:
        user_id: The owning user whose non-loan accounts to reconcile.

    Returns:
        The non-loan account ids reconciled, ascending (empty when the user
        has none).
    """
    account_ids = _non_loan_account_ids_for_user(user_id)
    for account_id in account_ids:
        sync_account_anchor_postings_all_scenarios(account_id)
    return account_ids
