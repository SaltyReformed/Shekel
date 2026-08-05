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
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.exceptions import ValidationError
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.services import (
    account_posting_service,
    anchor_service,
    ledger_account_service,
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

    Bundles the six fields every :func:`create_account` call site
    supplies into one cohesive value object so the factory takes a
    single argument instead of a long keyword list.  The clump is what
    every caller co-loads: a new account is always created from an
    owner, a type, a name, and a real-money anchor (with the civil day
    that anchor was true and an audit-trail note).  **It carried an
    optional explicit anchor PERIOD until ruling R-EO** (plan step
    X-f1c3b), which deleted the column the field fed.  Open-ended
    ``Account`` columns are NOT part of this concept -- they pass
    through :func:`create_account`'s ``**extra_columns`` instead.

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
        notes: Free-text label written into the origination
            ``AccountAnchorHistory`` row's ``notes`` column so the
            audit trail names the originating path.  Defaults to
            ``"origination"``; callers like the seed scripts override
            to e.g. ``"origination (seed_user.py)"``.
    """

    user_id: int
    account_type_id: int
    name: str
    anchor_balance: Decimal
    observed_on: date | None = None
    notes: str = "origination"


def create_account(spec: AccountSpec, **extra_columns) -> Account:
    """Construct an Account row plus its matching AccountAnchorHistory.

    Performs the E-19 / CRIT-01 invariant work in one place: resolves
    the assertion's civil day, constructs the Account, flushes to assign
    ``account.id``, then inserts the origination history row carrying that
    day.  The pair is appended to the current session; the caller commits.

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
            anchor balance, the day that balance was true, and the audit
            note for the account to create.
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
    # It was defaulted here and then handed to a separate guard that read the
    # clock again -- two readings, and a midnight tick between them could refuse
    # this function's own default (the floor arm; the note that used to sit here
    # considered only the future arm, whose ``>`` test is forgiving).
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

    # Origination history row -- the resolver in Commit 4 reads the
    # most recent AccountAnchorHistory entry as the date-anchored
    # source of truth, so writing this row at creation guarantees the
    # column cache and the event stream agree from t0.
    db.session.add(AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=anchor_balance,
        observed_on=observed_on,
        notes=spec.notes,
    ))

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
        spec.name, account.id, spec.user_id, anchor_balance, observed_on,
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
