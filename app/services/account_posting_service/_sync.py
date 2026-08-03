"""Account anchor-posting orchestration: per-scenario sync, all-scenarios, per-user.

The entry points that drive a non-loan account's anchor reconcile -- one walk
(:func:`app.services.cash_ledger.walk_cash_ledger`, the READ fold's own, since
plan step X-d deleted this package's postings-sourced twin) feeding one reconcile
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
* :func:`backfill_all_account_anchor_postings` -- the one-time, deploy-wide
  historical sweep over every non-loan account across all owners (C7),
  reusing the identical go-forward sync so backfill == go-forward by
  construction.
* :func:`resync_anchor_postings` -- the re-derive several accounts at once,
  and the ONE statement of WHEN a re-derive may run: only once the source rows
  and the ledger are meant to agree again, because the checked-projection
  assert rides on it (plan step X-d, ruling R-DM).
* :func:`self_heal_anchor_corrections` -- the effect-time self-heal the
  tails of ``posting_service.sync_transfer_postings`` /
  ``sync_transaction_postings`` invoke after emitting source delta entries:
  a source whose posted effect changed at-or-before an account's latest
  anchor assertion moved that anchor's walked ``ledger_before``, so the
  stale correction is re-derived in the same transaction.  It is
  :func:`resync_anchor_postings` behind an emitted-something gate.

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
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ref import AccountType
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services._posting_reconcile import assert_ledger_projects_facts
from app.services.cash_ledger import (
    CashLedgerWalk,
    dated_deltas,
    walk_cash_ledger,
)
from app.services.posting_reads import _ledger_account_for
from app.services.scenario_resolver import get_baseline_scenario

from ._anchors import reconcile_account_anchor_corrections

logger = logging.getLogger(__name__)

_ZERO_MONEY = Decimal("0.00")


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

    The per-scenario chokepoint: walks the account's SOURCE rows and assertions
    once (:func:`app.services.cash_ledger.walk_cash_ledger`), reconciles the
    resulting opening / true-up corrections to the ledger
    (:func:`._anchors.reconcile_account_anchor_corrections`), then verifies that
    the ledger it just wrote still equals the fold of those facts
    (:func:`_assert_checked_projection`).

    **The walk is the READ fold's, since plan step X-d.**  This package walked
    the POSTED copy of the account's events until then, which made the
    corrections a function of the ledger they are written into and left two
    representations of one event set.  One walk, both consumers -- ruling R-H --
    and the assert below is what turns a stale posting from a second opinion
    into a detectable, repairable cache inconsistency (E1a's shape, for cash).

    Idempotent and self-healing: a re-run at the same state writes nothing.
    Touches ONLY the account's own linked and anchor-equity ledgers.  A
    missing account or an amortizing loan is a no-op (see
    :func:`_load_non_amortizing_account`).  Flushes but does not commit
    (the caller owns the transaction).

    Args:
        account_id: The non-loan account whose corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.

    Raises:
        PostingError: When the reconciled ledger does not equal the fold of the
            account's facts (:func:`_assert_checked_projection`).
    """
    if _load_non_amortizing_account(account_id) is None:
        return
    walk = walk_cash_ledger(account_id, scenario_id)
    # ONE linked-ledger resolution per sync, shared by the reconcile and the
    # assert -- the same shape ``loan_posting_service.sync_loan_postings`` uses,
    # and for the same reason (each resolving its own is a redundant query).
    linked_ledger_id = _ledger_account_for(account_id).id
    reconcile_account_anchor_corrections(
        account_id, scenario_id, walk.anchor_corrections,
    )
    _assert_checked_projection(
        account_id, scenario_id, walk, linked_ledger_id,
    )


def _assert_checked_projection(
    account_id: int,
    scenario_id: int,
    walk: CashLedgerWalk,
    linked_ledger_id: int,
) -> None:
    """Assert the posted linked ledger equals the fold of the account's facts.

    **The X-d invariant, and E1a's for cash: ``sum(postings) == fold(ACTUAL
    events)``, checked at WRITE time -- per date, not just in total.**  The
    walk's events, re-keyed by the day each counts from
    (:func:`app.services.cash_ledger.dated_deltas` -- the ONE statement of that
    clock, shared with the balance seam's fold), must match the linked ledger's
    per-``entry_date`` nets exactly.  Anything else means the posted ledger has
    stopped being a faithful projection of the account's source rows, and every
    reader of the general ledger -- the statements, the reconciliation oracles
    -- is looking at a lie.  Raising HERE, inside the write transaction, turns
    that lie into a rollback at the write that caused it instead of a silent
    divergence found months later.

    **The sign is the walk's own, NOT its negative, and that is the difference
    from the loan side.**  A loan walk tracks OWED against a credit-normal
    liability ledger, so its assert negates; a cash walk's running balance IS
    the linked ledger's balance in one convention, for assets and liabilities
    alike (:func:`app.services.cash_ledger.dated_deltas` states this and names
    the trap: a sign flip still BALANCES every entry, so the trial balance
    closes and only the balance sheet is upside down).  Measured on the
    dev-runtime clone before this assert was written: it holds over 79 dated
    nets across 7 accounts, and the negated form fails all 7.

    The comparison is per DATE because a right amount at a wrong date is still
    a wrong ledger: the fold counts each event from its own civil day, so a
    stale-dated posting moves balances on every day between the two.  The
    reconciles this runs behind are date-keyed for exactly that reason (finding
    N-13), so a legitimate edit converges BEFORE this check reads the ledger
    back and can never trip it.

    The two known ways to fail it, named so the error is actionable:

    * a reconcile defect (a target computed or dated wrong) -- fix the
      reconcile, never this check;
    * **a posting the walk cannot model.**  A source-row walk cannot see a
      posting whose source row was hard-deleted (``ON DELETE SET NULL``), and
      ruling R-DI ceded the residue reader that used to absorb one silently.
      Such a row is an F1-class DATA item for a human: the reverse-before-delete
      discipline nets it to zero at every delete door in the app (traced at
      R-DI), and the ship sweep found none, so one appearing here means a door
      was added that does not reverse first.

    Skipped for an account with no assertion history (an empty walk) -- the
    reconcile above posted nothing and there is no fact stream to check against.

    Args:
        account_id: The account whose projection to verify (names it in the
            failure message).
        scenario_id: The budget scenario the reconcile ran in.
        walk: The SAME walk the reconcile just posted from -- one walk, then the
            check, never a second walk that could differ.
        linked_ledger_id: The account's LINKED ledger account id, resolved once
            by :func:`sync_account_anchor_postings`.

    Raises:
        PostingError: When any date's posted net differs from the fold's delta
            -- the projection is broken and the write must not commit.
    """
    if not walk.anchor_corrections:
        return
    expected: dict[date, Decimal] = {}
    for day, delta in dated_deltas(walk):
        # Posting space is the walk's OWN sign for cash, not its negative --
        # see the shared assert's docstring for why the loan side differs and
        # why the sign stays with each caller.
        expected[day] = expected.get(day, _ZERO_MONEY) + delta
    assert_ledger_projects_facts(
        f"Account {account_id}",
        expected,
        linked_ledger_id,
        scenario_id,
        "Either a reconcile defect (fix the reconcile) or a posting the "
        "account's source rows cannot explain -- a hard-delete residue row, "
        "which is an F1-class data item for a human (ruling R-DI).",
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


def resync_anchor_postings(
    account_ids: Iterable[int], scenario_id: int,
) -> None:
    """Re-derive several accounts' anchor corrections in one scenario.

    **The ONE name for "an operation has finished; re-derive what it touched",
    and every caller that owns the end of an operation calls it here** (plan
    step X-d, ruling R-DM).  Three do, and they are the three shapes an
    operation ends in:

    * :func:`self_heal_anchor_corrections` -- a source sync that EMITTED
      something, which is the ordinary settle / revert / edit;
    * ``posting_service.retire_transaction`` -- a transaction whose row is now
      final, hard-deleted or soft;
    * ``transfer_service.delete_transfer`` -- a transfer whose parent and both
      shadows are now final.

    **WHEN it runs is the rule, and stating it once is why this function
    exists.**  :func:`sync_account_anchor_postings` ends on the
    checked-projection assert, which compares the posted ledger against the
    account's SOURCE ROWS -- so it may only run when the rows and the ledger
    are meant to agree.  Every delete path reverses a row's postings to zero
    while the row still exists and still reads SETTLED, forced by the schema
    (``journal_entries.transaction_id`` / ``transfer_id`` are
    ``ON DELETE SET NULL``, so the reversal must be written while the link is
    live).  Between that reversal and the removal the two deliberately
    disagree, and grading them there refuses a half-finished operation.  The
    three callers above are exactly the moments after which they agree again.

    Idempotent: a re-run at the same state writes nothing (each account
    reconciles to target).  Duplicate ids collapse and the order is by id, so a
    transfer's two endpoints are re-derived once each, deterministically.  An
    amortizing loan among them is a structural no-op
    (:func:`_load_non_amortizing_account`).  Flushes but does not commit -- the
    caller owns the transaction.

    Args:
        account_ids: The real accounts to re-derive (a transaction's one cash
            account, a transfer's two endpoints).  Both are immutable on their
            source rows, so a caller may capture them before a hard delete.
        scenario_id: The scenario to re-derive within -- postings are
            scenario-scoped, so only that scenario's corrections can have moved.

    Raises:
        PostingError: When the reconciled ledger does not equal the fold of an
            account's facts (:func:`_assert_checked_projection`).
    """
    for account_id in sorted(set(account_ids)):
        sync_account_anchor_postings(account_id, scenario_id)


def self_heal_anchor_corrections(
    account_ids: Iterable[int],
    scenario_id: int,
    delta_entries: list[JournalEntry],
) -> None:
    """Resync the accounts whose anchor corrections *delta_entries* may have staled.

    The effect-time self-heal (Build-Order Step 5, C6): the tails of
    ``posting_service.sync_transfer_postings`` /
    ``sync_transaction_postings`` call this after emitting source delta
    entries.  It is the EMITTED-SOMETHING arm of
    :func:`resync_anchor_postings`, which owns the re-derive itself and states
    the rule about when it may run; this function owns only the gate.

    **It carried a SKIP predicate until plan step X-d, and ruling R-DK deleted
    it.**  Two conditions decided whether a walk could be avoided: whether the
    change rode on top of every assertion (so no correction could move), and
    whether the scenario already carried its corrections at all.  Three things
    ruled it out:

    * **Its own contract conceded it was optional.**  ``sync_account_anchor_postings``
      is idempotent and reconciles to target, so running it after every
      emission is always correct and only ever costs a walk; the predicate was
      never more than a proof that a particular walk would write nothing.
    * **It was a cost guard that SPELLS the money rule**, and this arc has paid
      for that shape once: finding N-133 / F4's silent timezone-sign dependency
      lived in this exact predicate for its whole life, and the second of its
      two conditions had to be ADDED after the first was found insufficient --
      a fire-predicate that misses a case leaves the ledger wrong in silence.
    * **The checked-projection assert now lives behind this call**, and a skip
      here is a skip of the CHECK as well as of the write.  The one case the
      predicate skipped -- a settle dated after the last assertion -- is the
      commonest write there is, so the fence would have reported clean over
      precisely the change it was least able to see.

    Measured 2026-08-02 on the real Checking account (55 assertions, 139 settled
    rows): the skip cost ``0.73 ms``, and the sync it skipped now costs
    ``11.13 ms`` over 12 SQL statements -- against ``70.87 ms`` over 110 before
    ruling R-DL hoisted the reconcile's N+1, which is what made the predicate
    look load-bearing.

    An account with no anchor history walks to an empty correction list and
    reconciles nothing; an amortizing loan is structurally skipped by
    :func:`sync_account_anchor_postings`.  Flushes but does not commit (the
    caller owns the transaction).

    Args:
        account_ids: The real accounts the emitted deltas' LINKED legs can
            touch (the transfer's two endpoints / the transaction's cash
            account -- both immutable on their source rows).
        scenario_id: The scenario the deltas were emitted in (postings are
            scenario-scoped, so only that scenario's corrections can have
            moved).
        delta_entries: The just-emitted delta
            :class:`~app.models.journal_entry.JournalEntry` list (empty ->
            no-op, so a sync that reconciled nothing pays nothing).

    Raises:
        PostingError: When the reconciled ledger does not equal the fold of an
            account's facts (:func:`_assert_checked_projection`).
    """
    if not delta_entries:
        return
    resync_anchor_postings(account_ids, scenario_id)


def _non_loan_accounts_id_query():
    """Return the base query for non-loan account ids, ascending.

    The one query definition the all-owners sweep and the per-user resync
    both build on: every account whose type is not amortizing
    (``has_amortization IS FALSE`` -- exactly the column ``classify_account``
    maps to ``AMORTIZING``, so this SQL filter and the per-account guard can
    never disagree).  Deliberately NOT filtered by ``is_active``: an archived
    account's posted corrections must keep reconciling (archiving disables new
    activity, it does not erase posted facts).  The two enumerators differ only
    in whether they add a ``user_id`` scope, so keeping the join / filter /
    order in one builder is what keeps them from drifting.

    Returns:
        A SQLAlchemy ``Query`` yielding ``(account_id,)`` rows for every
        non-loan account, ascending by id -- NOT executed; callers add a
        ``user_id`` filter (or none) and call ``.all()``.
    """
    return (
        db.session.query(Account.id)
        .join(AccountType, Account.account_type_id == AccountType.id)
        .filter(AccountType.has_amortization.is_(False))
        .order_by(Account.id)
    )


def _all_non_loan_account_ids() -> list[int]:
    """Return every non-loan account id, ascending (all owners).

    The all-owners enumerator behind the deploy-wide backfill
    (:func:`backfill_all_account_anchor_postings`), the cash analogue of
    ``loan_loaders.load_all_loan_account_ids``.  Deliberately NOT user-scoped:
    it is a system / deploy-time sweep over every owner's non-loan accounts
    (like the Step-2 / Step-3 settled-row backfills), and each posted
    correction still carries its own owner (the account's history row and
    scenario), so no row is mis-attributed.

    Returns:
        The non-loan account ids, ascending (already distinct -- ``Account.id``
        is the primary key); empty on an account-free database.
    """
    return [account_id for (account_id,) in _non_loan_accounts_id_query().all()]


def _non_loan_account_ids_for_user(user_id: int) -> list[int]:
    """Return every non-loan account id one user owns, ascending.

    The per-user counterpart to :func:`_all_non_loan_account_ids`, scoping the
    shared base query (:func:`_non_loan_accounts_id_query`) to *user_id*.  The
    loan mirror is ``loan_loaders.load_loan_account_ids_for_user``.

    Args:
        user_id: The owning user whose non-loan accounts to enumerate.

    Returns:
        The account ids, ascending.
    """
    return [
        account_id
        for (account_id,) in _non_loan_accounts_id_query()
        .filter(Account.user_id == user_id)
        .all()
    ]


def _reconcile_account_ids(account_ids: list[int]) -> list[int]:
    """Reconcile the given non-loan accounts across all their scenarios; return them.

    The shared body of the deploy-wide backfill
    (:func:`backfill_all_account_anchor_postings`) and the per-user resync
    (:func:`resync_user_account_anchor_postings`), mirroring
    ``loan_posting_service._reconcile_loan_account_ids``: each id is reconciled
    through :func:`sync_account_anchor_postings_all_scenarios`, the identical
    go-forward sync (so a reconciled correction is identical to a go-forward one
    by construction -- there is no second implementation that could drift).  The
    two public functions differ ONLY in their enumerator (all accounts vs one
    user's), which stays with each of them; this holds the loop itself in one
    place.

    Flushes but does NOT commit -- the caller owns the transaction boundary.

    Args:
        account_ids: The non-loan account ids to reconcile.

    Returns:
        ``account_ids`` unchanged, for the callers' return contract.
    """
    for account_id in account_ids:
        sync_account_anchor_postings_all_scenarios(account_id)
    return account_ids


def backfill_all_account_anchor_postings() -> list[int]:
    """Reconcile every non-loan account's anchor corrections (deploy backfill).

    The one-time, production-wide historical backfill (Build-Order Step 5, C7):
    for every non-loan account across all owners
    (:func:`_all_non_loan_account_ids`), reconcile its opening / true-up anchor
    corrections via :func:`sync_account_anchor_postings_all_scenarios`.  This
    posts the corrections for any account / anchor asserted BEFORE the C6
    go-forward wiring shipped (which therefore carries none), so every non-loan
    linked ledger sums to an ABSOLUTE balance on real historical data and the
    trial balance closes app-wide.

    Reuses the SAME per-account sync the go-forward chokepoints call
    (:func:`_reconcile_account_ids`), so a backfilled correction is identical
    to the go-forward one by construction -- there is no second implementation
    that could drift.  Idempotent and self-healing via reconcile-to-target: an
    account already carrying its go-forward corrections is already at target, so
    nothing is re-posted -- the backfill never double-posts, and a re-run at the
    same state writes nothing.  A $0-anchor account books nothing (no entry, no
    ``anchor_equity`` row), staying hard-deletable.

    Flushes but does NOT commit -- the caller owns the transaction boundary: the
    deploy hook
    (``scripts.init_database.backfill_all_account_anchor_postings_after_migration``,
    which initialises ``ref_cache`` first because the migration host does not),
    the backfill suite, or the reconciliation oracle.

    Returns:
        The non-loan account ids reconciled, ascending -- for the deploy log and
        test introspection (empty on an account-free database).
    """
    return _reconcile_account_ids(_all_non_loan_account_ids())


def resync_user_account_anchor_postings(user_id: int) -> list[int]:
    """Reconcile every non-loan account a SINGLE user owns, across scenarios.

    The per-user chokepoint, mirroring
    ``loan_posting_service.resync_user_loan_postings``: iterates the user's
    non-loan accounts (:func:`_non_loan_account_ids_for_user`) and reconciles
    each through :func:`_reconcile_account_ids` -- the identical go-forward
    sync the deploy backfill uses, so a re-synced correction is identical to a
    go-forward one by construction.

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
    return _reconcile_account_ids(_non_loan_account_ids_for_user(user_id))
