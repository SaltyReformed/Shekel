"""
Shekel Budget App -- Account Service

Canonical factory for creating ``budget.accounts`` rows.  Every code
path that materializes an Account MUST route through ``create_account``
here so the E-19 / CRIT-01 invariant -- "every account row carries a
matching ``AccountAnchorHistory`` assertion from the moment it exists"
-- is enforced in exactly one place.

This service is Flask-isolated per the project architecture rule
(``CLAUDE.md`` Architecture section): it takes plain data, returns a
plain SQLAlchemy object, never imports ``request``/``session``.  The
caller is responsible for the surrounding transaction (no commit
inside this module).

Background -- audit finding CRIT-01 / governing intent E-19: before
this remediation, the five balance producers (grid, /accounts,
/savings, dashboard, net worth) forked four different ways for the
NULL-anchor case.

**That invariant is enforced HERE and nowhere else, and saying so is a
correction.**  This docstring claimed a storage-tier half -- migration
``cfb15e782f86``'s ``NOT NULL`` plus ``ck_accounts_anchor_balance_present``
on ``accounts.current_anchor_balance`` -- and ruling **R-EH** dropped that
column, its check and the deferrable FK beside it (plan step X-f1c3c).  The
claim was never quite the invariant anyway: a ``NOT NULL`` column forced a
VALUE onto the account row, not the existence of a history row, and no
standard-PostgreSQL constraint can require a child row without a trigger.
What catches a bypassing caller now is
:func:`app.services.cash_ledger.resolve_anchor`, which raises ``RuntimeError``
rather than returning a wrong number -- fail-loud at the first READ instead of
at the write.  Project rule, unchanged: ``Account(...)`` direct construction
belongs only in tests that deliberately exercise that failure.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AccountOpeningSourceEnum, AcctCategoryEnum
from app.extensions import db
from app.exceptions import ValidationError
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.services import (
    account_posting_service,
    anchor_service,
    ledger_account_service,
    opening_service,
)


logger = logging.getLogger(__name__)


def _require_pay_period_schedule(user_id: int) -> None:
    """Refuse to create an account for an owner with no pay-period schedule.

    **A precondition of ACCOUNT CREATION, not a property of a day** (ruling
    **R-ER**, plan step X-f1c4c).  It was the middle arm of a three-part guard
    this module shared with nothing; when :func:`create_account` stopped being
    that guard's only caller the arm had to be placed, and it belongs here:
    :func:`app.services.anchor_service.resolve_observation_day` now owns the two
    bounds that are about the DAY, and asking this one there would have refused
    a true-up on an account that already exists -- exactly the refusal ruling
    R-EO deleted from that door, and under a message about creating an account.

    **Its stated reason was already false when it moved.**  It read "so the
    account's anchor has a period to reference", which ruling R-EO falsified by
    deleting ``account_anchor_history.pay_period_id`` -- an assertion references
    no period.  The live reason is one line further down this module: the tail
    of :func:`create_account` posts the opening's anchor correction, and
    ``account_posting_service`` derives each correction's pay period from the
    day it asserts, raising when the owner's calendar is empty (finding
    **N-192**).  Refusing here turns that 500 into a ``ValidationError`` the
    route can route to the generate-periods page.

    Args:
        user_id: The prospective account owner.

    Raises:
        ValidationError: When the user has no ``budget.pay_periods`` rows.  The
            route discriminates this shape from a bad observation day by
            re-asking the database rather than by reading the message, so the
            wording is free to say what is true.
    """
    if not db.session.query(
        db.session.query(PayPeriod).filter_by(user_id=user_id).exists()
    ).scalar():
        raise ValidationError(
            f"Cannot create an account for user_id={user_id}: the user has no "
            "pay periods.  Generate pay periods first -- the opening balance "
            "posts a correction into the period containing the day it asserts, "
            "and an empty calendar has no such period."
        )


@dataclass(frozen=True)
class AccountSpec:
    """The canonical inputs for creating an account.

    Bundles the five fields every :func:`create_account` call site
    supplies into one cohesive value object so the factory takes a
    single argument instead of a long keyword list.  The clump is what
    every caller co-loads: a new account is always created from an
    owner, a type, a name, and a real-money anchor with the civil day
    that anchor was true.  **It shed two fields to two rulings**: an
    optional explicit anchor PERIOD at **R-EO** (plan step X-f1c3b),
    which deleted the column it fed, and the audit-trail ``notes``
    string at **R-ES** (plan step X-f1e2), which deleted the column
    ``AccountAnchorHistory`` held it in.  Open-ended ``Account``
    columns are NOT part of this concept -- they pass through
    :func:`create_account`'s ``**extra_columns`` instead.

    Frozen so a constructed spec is an immutable record of one
    creation request.

    Attributes:
        user_id: ``auth.users.id`` of the account owner.
        account_type_id: ``ref.account_types.id`` of the account type.
            Caller is responsible for the C-28 ownership guard (a
            type is either a seeded built-in or owned by ``user_id``);
            this service does not re-check.
        name: Display name of the account.  Caller is responsible for
            the per-user uniqueness guard against ``uq_accounts_user_name``.
        anchor_balance: Real-money anchor in dollars.  Must be a
            ``Decimal`` (the project coding standard rejects float for
            monetary values); zero is a legitimate value per E-12 and
            is preserved rather than treated as "missing".
        observed_on: The civil day the asserted balance was TRUE, in the
            user's timezone (ruling R-DH).  Defaults to today when
            omitted, which is what a user typing their current bank
            balance means.  A PAST day is the point of the field: an
            account that existed before it was entered into Shekel, or
            one funded after its opening was typed, is stated rather
            than guessed at.  A FUTURE day is rejected -- a balance
            cannot have been observed on a day that has not happened.
    """

    user_id: int
    account_type_id: int
    name: str
    anchor_balance: Decimal
    observed_on: date | None = None


def create_account(spec: AccountSpec, **extra_columns) -> Account:
    """Construct an Account row plus its matching AccountAnchorHistory.

    Performs the E-19 / CRIT-01 invariant work in one place: resolves
    the assertion's civil day, constructs the Account, flushes to assign
    ``account.id``, then appends the origination assertion carrying that
    day.  The pair is added to the current session; the caller commits.

    **The assertion is appended through
    :func:`app.services.anchor_service.stage_anchor_true_up`, not constructed
    here, and that is ruling R-ES** (plan step X-f1e2).  This function built the
    ``AccountAnchorHistory`` row itself until then, which made it the table's
    SECOND writer -- and the two differed in every rule that is not the row's
    columns: the stager takes the owner's write lock, applies ruling R-EQ's
    did-this-change compare, and logs the resolved day in the one line both
    doors share.  With one writer those rules cannot be true on one path and
    absent on the other.  **The books OPENING goes through its own single
    writer for the identical reason since plan step X-f3c-2b-2a**
    (:func:`app.services.opening_service.stage_account_opening`), which is what
    lets an owner restate it later without this function becoming that table's
    second writer.

    The order is also better than it was, and the improvement is MEASURED
    rather than argued: the advisory lock used to appear at statement 7, five
    statements after the assertion INSERT at statement 2.  **Re-measured
    2026-08-31 on a production clone, on both sides of X-f3c-2b-2a, because that
    step changed which function takes the lock**: it is statement 3 either way
    -- ``opening_service`` now takes it where ``anchor_service`` used to, and
    the wire position is unchanged -- while the assertion INSERT moved from 6
    to 8, the two added statements being the opening door's own governing read
    and its re-entrant re-acquisition.  *The figure this sentence quoted for
    that INSERT was 5, and it had decayed silently: it was measured at X-f1e2,
    before ``budget.account_openings`` existed to be written at all.*  **None
    of this puts the path outside finding N-193's class** -- ``INSERT INTO
    budget.accounts``
    still runs first and takes an index lock on ``uq_accounts_user_name``, so
    the advisory lock is not the transaction's FIRST lock.  An adversarial
    review traced the cycles: none exists against N-193's named antagonists
    (a pay-period truncate / reset / regenerate CASCADEs to
    ``journal_entries``, ``transfers``, ``transactions`` and
    ``recurrence_rules``, never to ``accounts``), and the one it did reproduce
    -- create versus a same-name rename -- is pre-existing and made LESS likely
    by this change.  Recorded as finding **N-202**.

    **It no longer resolves an anchor PERIOD.**  It used to derive one from
    ``observed_on`` (``resolve_anchor_period_id``, deleted as callerless in the
    same commit) for two consumers: the ``accounts.current_anchor_period_id``
    cache column that ruling R-EH deleted, and the origination assertion's own
    ``pay_period_id`` that ruling R-EO deleted.  With neither, an account is
    created from a name, a type, a balance and the day that balance was true.

    **The origination row is DATED here, before anything reads it**, and
    that ordering is load-bearing (ruling R-DH, plan step 2).  This
    function's tail posts the opening's anchor correction, whose journal
    entry is keyed on the assertion's ``observed_on``; a caller that
    created the account and re-stamped the day afterwards would leave the
    ledger holding a correction under the old key until some later sync
    happened to run, and whichever reader triggered that sync would see
    the re-date as its own change.  Supplying the day up front is what
    makes "the account was opened on day X" one write rather than two.

    Args:
        spec: The :class:`AccountSpec` carrying the owner, type, name,
            anchor balance and the day that balance was true.
        **extra_columns: Additional ``Account`` columns (e.g.
            ``sort_order``, ``is_active``).  Forwarded verbatim to
            the model constructor.

    Returns:
        The newly added :class:`Account` (already flushed, so ``account.id``
        is set), with its matching origination ``AccountAnchorHistory`` row in
        the session pending commit.

    Raises:
        ValidationError: When the user has no pay periods at all
            (:func:`_require_pay_period_schedule`, this module's own
            precondition), or when ``observed_on`` is in the future or precedes
            the user's recorded history
            (:func:`app.services.anchor_service.resolve_observation_day`, the
            rule every assertion writer shares).  **Two sources, one exception
            type, and the route must not tell them apart by the message** --
            ``routes/accounts/crud.create_account`` re-asks the database, which
            is why splitting them at ruling R-ER changed no destination.
        TypeError: When ``anchor_balance`` is not a ``Decimal``.  The
            project rejects ``float`` in monetary code; passing
            ``int`` or ``str`` is also a caller bug.
        RuntimeError: When the write door declines to append the origination
            assertion.  Structurally unreachable -- the stager declines only
            when an assertion already governs the submitted day, and an account
            flushed two lines earlier carries none -- and raised rather than
            ignored because this function IS the E-19 / CRIT-01 invariant.  The
            same fail-loud placement
            :func:`app.services.cash_ledger.resolve_anchor` documents for the
            READ side, moved to the write that establishes the state.
    """
    # ``Decimal`` is the canonical type for monetary values per
    # ``docs/coding-standards.md``.  ``int`` is exact when converted to
    # Decimal and is a common test-fixture shorthand for "exactly $0";
    # we coerce it.  ``float`` is rejected outright -- ``Decimal(0.1)``
    # introduces silent precision drift, and the project forbids it.
    anchor_balance = spec.anchor_balance
    if isinstance(anchor_balance, float):
        raise TypeError(
            f"anchor_balance must be Decimal (got float -- floats "
            "introduce silent precision drift in monetary code; "
            f"construct Decimal from a string: {anchor_balance!r})"
        )
    if isinstance(anchor_balance, int) and not isinstance(anchor_balance, bool):
        anchor_balance = Decimal(anchor_balance)
    if not isinstance(anchor_balance, Decimal):
        raise TypeError(
            f"anchor_balance must be Decimal, got {type(anchor_balance).__name__}"
        )

    # An account cannot be created without a pay-period schedule, and that is
    # this module's own precondition rather than a rule about the day below
    # (ruling R-ER) -- see :func:`_require_pay_period_schedule` for the reason,
    # which is the opening correction this function's own tail posts.  Asked
    # FIRST: with an empty calendar the day bound's floor collapses to today, so
    # letting it answer first would report "your recorded history starts on
    # <today>" to a user whose real problem is that they have no history at all.
    _require_pay_period_schedule(spec.user_id)
    # The day the asserted balance was TRUE (ruling R-DH), defaulted and bounded
    # in ONE call by the module that owns what an assertion is (ruling R-ER).
    # ONE call is the whole point and it is load-bearing twice over: the floor
    # moves with the clock, so a second application can refuse what this one
    # produced, and this one runs BEFORE the account row exists so a refusal
    # leaves nothing behind.  ``ObservationDay`` is what carries "already
    # bounded" to the writer instead of a convention.
    observed_on = anchor_service.resolve_observation_day(
        spec.user_id, spec.observed_on,
    )

    account = Account(
        user_id=spec.user_id,
        account_type_id=spec.account_type_id,
        name=spec.name,
        **extra_columns,
    )
    db.session.add(account)
    db.session.flush()

    # **The books OPEN here** (plan step X-f3c-2a, ruling R-GX).  A brand-new
    # account holds no records for an assertion to already contain, so its
    # opening EQUITY is exactly the balance the owner typed -- which is why
    # this is a ``user_declared`` row and not a derivation.  Every account gets
    # one, an amortizing loan included: ``balance_at.balance_at`` falls through
    # to the cash fold for an amortizing account carrying no ``LoanParams``
    # (``_resolution.configured_loan`` answers ``None``), and that fold refuses
    # an account with no opening rather than fabricating a level for it.
    #
    # It is written BEFORE the assertion and the posting sync below, and the
    # order is load-bearing: ``account_posting_service`` walks this record to
    # book the ``account_opening`` journal entry, and
    # ``cash_ledger.account_opening_fact`` raises without it.
    #
    # **Through ``opening_service``'s door rather than by constructing the row
    # here** (plan step X-f3c-2b-2a), and that is ruling **R-ES** applied one
    # table over: this function was the table's only writer until an owner
    # could restate the books, and two writers of one append-only table is how
    # the assertion table came to have a row written with no lock, no
    # did-this-change compare and no audit line.  The day is NOT re-bounded
    # there -- it is ``observed_on``'s, already resolved above by the one rule
    # both assertion doors share, and a second application of a clock-dependent
    # bound is the defect ruling R-ER deletes.
    #
    # The return is deliberately NOT checked, unlike the assertion's below, and
    # the asymmetry is real: the stager declines only when a governing opening
    # already matches the submission, and this account was INSERTed four
    # statements ago -- no row can reference an id that did not exist when the
    # transaction began.  ``False`` here is unreachable rather than merely
    # unlikely, and the state it would leave -- an account with no opening --
    # is the one ``cash_ledger.account_opening_fact`` raises on at the first
    # READ, loudly, naming this factory.
    opening_service.stage_account_opening(
        account=account,
        opening=opening_service.BooksOpening(
            opened_on=observed_on.civil_day,
            equity=anchor_balance,
        ),
        source=AccountOpeningSourceEnum.USER_DECLARED,
    )

    # The origination assertion goes through the ONE write door (ruling R-ES,
    # plan step X-f1e2).  This function constructed the row itself until then,
    # which made it the table's second writer: it took no owner write lock, ran
    # no did-this-change compare and logged a different line, so "one door
    # writes an assertion" held by convention rather than by construction.
    #
    # The return is CHECKED.  The stager declines only when an assertion already
    # governs the submitted day, and a just-flushed account carries none, so a
    # decline means the E-19 / CRIT-01 invariant this module exists to enforce
    # has broken.  The alternative is an anchorless account whose first READER
    # raises out of ``cash_ledger.resolve_anchor``, far from the cause.
    if not anchor_service.stage_anchor_true_up(
        account=account,
        new_balance=anchor_balance,
        observed_on=observed_on,
    ):
        raise RuntimeError(
            f"create_account: no origination assertion was appended for "
            f"account id={account.id} (${anchor_balance} on {observed_on.civil_day}) "
            "-- an assertion already governs that day on a just-created "
            "account, which breaks the E-19 / CRIT-01 invariant."
        )

    # Pair the account with its chart-of-accounts ledger account
    # (Build-Order Step 2): exactly one Asset/Liability ledger account per
    # real account, so the double-entry posting ledger has somewhere to
    # post.  Idempotent and side-effecting only -- the returned Account is
    # unchanged.  Historical accounts were paired by the Commit-2 backfill
    # migration; this call is the go-forward half.
    ledger_account_service.create_ledger_account_for_account(account)

    # Post the account's OPENING anchor correction (Build-Order Step 5): a
    # non-zero anchor books a balanced opening onto the fresh pairing, so
    # the linked ledger's ABSOLUTE total equals the asserted balance from
    # t0 (a $0 anchor books nothing and stays hard-deletable; an amortizing
    # loan is a structural no-op -- its opening posts from LoanParams at
    # params-create).  A baseline-less owner is skipped with a loud log and
    # recovered by ``create_baseline``'s per-user resync.
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )

    logger.info(
        "Created account %s (id=%d, user_id=%d) asserted at $%s on %s",
        spec.name, account.id, spec.user_id, anchor_balance, observed_on.civil_day,
    )
    return account


def list_active_accounts(user_id: int) -> list[Account]:
    """Return a user's active accounts ordered for display dropdowns.

    Shared by every form route that renders an account picker (the
    transaction-template, transfer-template, savings-goal, and settings
    forms) so the option list is consistently ordered by
    ``(sort_order, name)`` -- the arrangement the user set on the
    accounts page.  Archived accounts (``is_active = False``) are
    excluded because they are not selectable targets for new rows.

    Args:
        user_id: ``auth.users.id`` of the owner whose accounts to list.

    Returns:
        The owner's active :class:`Account` rows, ordered by
        ``sort_order`` then ``name``.
    """
    return (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )


def active_accounts_query(user_id: int, *, amortizing: bool):
    """Return the query for a user's active accounts on ONE amortizing side.

    The kind-boundary query composer shared by every surface that
    partitions active accounts on ``AccountType.has_amortization``: the
    debt-strategy loader (``amortizing=True`` -- every loan account) and
    the grid's resolver + Default Grid Account picker
    (``amortizing=False`` -- ruling D4 / step A1: the grid refuses an
    amortizing account).  ``amortizing`` is a filter VALUE, not a
    behavior switch; both sides are the identical query shape, which is
    why one definition exists (the duplicate-code gate caught the
    copies).  Returns the UNORDERED query so each caller adds its own
    tail -- the pickers order by ``(sort_order, name)``, the grid
    resolver's fallback by ``(sort_order, id)`` -- the same
    build-the-expression contract the reconcile readers follow (a caller
    completes the query with its own tail).

    Args:
        user_id: ``auth.users.id`` of the owner whose accounts to query.
        amortizing: Which side of the ``has_amortization`` boundary to
            return.

    Returns:
        The filtered ``Account`` query; the caller adds ordering and an
        executor (``.all()`` / ``.first()``).
    """
    return (
        db.session.query(Account)
        .join(Account.account_type)
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            AccountType.has_amortization.is_(amortizing),
        )
    )


def get_account_type_ids_in_use(user_id: int) -> set[int]:
    """Return the account_type_ids the user currently has accounts of.

    Powers the account-type delete guard (a type that is in use cannot
    be deleted) shared by the accounts-list page and the settings
    account-types page.

    Args:
        user_id: ``auth.users.id`` of the owner.

    Returns:
        Set of ``account_type_id`` integers in use by the user's
        accounts.
    """
    return {
        row[0] for row in
        db.session.query(Account.account_type_id)
        .filter_by(user_id=user_id)
        .distinct()
        .all()
    }


def list_retirement_investment_account_types() -> list[AccountType]:
    """Return every AccountType in the retirement or investment category.

    The shared source for the salary contribution-target dropdown
    (:func:`app.routes.salary._helpers._get_investment_accounts`) and the
    retirement dashboard's pretax/Roth account-type partitioning
    (:mod:`app.services.retirement_dashboard_service`).  Returns the full
    rows rather than just the id set because the dashboard reads
    ``AccountType.is_pretax`` off them.

    Returns:
        List of :class:`AccountType` rows whose category is RETIREMENT
        or INVESTMENT.
    """
    retirement_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)
    investment_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)
    return (
        db.session.query(AccountType)
        .filter(AccountType.category_id.in_([retirement_cat_id, investment_cat_id]))
        .all()
    )
