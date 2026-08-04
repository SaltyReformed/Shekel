"""
Shekel Budget App -- Account Service

Canonical factory for creating ``budget.accounts`` rows.  Every code
path that materializes an Account MUST route through ``create_account``
here so the E-19 / CRIT-01 invariant -- "every account row carries a
non-NULL anchor balance, a non-NULL anchor period, and a matching
AccountAnchorHistory row from the moment it exists" -- is enforced in
exactly one place.

This service is Flask-isolated per the project architecture rule
(``CLAUDE.md`` Architecture section): it takes plain data, returns a
plain SQLAlchemy object, never imports ``request``/``session``.  The
caller is responsible for the surrounding transaction (no commit
inside this module).

Background -- audit finding CRIT-01 / governing intent E-19: before
this remediation, the five balance producers (grid, /accounts,
/savings, dashboard, net worth) forked four different ways for the
NULL-anchor case.  The remediation makes the NULL state unreachable
both at the storage tier (migration ``cfb15e782f86`` adds NOT NULL +
``ck_accounts_anchor_balance_present``) and at the application tier
(this factory).  An ``Account(...)`` construction that bypasses this
factory remains a latent footgun -- the DB constraint fires, but the
caller pays a 500-shaped error instead of a clean ``ValidationError``.
Project rule: ``Account(...)`` direct construction is only acceptable
in tests that deliberately exercise the storage-tier constraint via
raw SQL.
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
    ledger_account_service,
    pay_period_service,
)
from app.utils.dates import display_today


logger = logging.getLogger(__name__)


def earliest_observable_day(user_id: int) -> date:
    """Return the earliest civil day a balance may be asserted for.

    The floor :func:`_reject_undatable_observation` enforces, exported so the
    account-create form can set the date input's ``min`` to the same day the
    service would refuse below -- ONE definition of the bound, rather than a
    template literal that drifts from the validation behind it.

    **The rule itself moved to**
    :func:`app.services.pay_period_service.earliest_recordable_day` at plan
    step X-f1c (ruling **R-EL**), because the settle-day write door needs the
    same bound and ``status_seam`` must stay below the services that call it.
    This stays as the account-facing name its callers already use; the
    behaviour is unchanged and there is one implementation.

    Args:
        user_id: The account owner whose schedule sets the floor.

    Returns:
        The earliest assertable civil day.  Today when the user has no pay
        periods at all -- the account create itself then fails on the missing
        schedule, which is a clearer error than a date bound.
    """
    return pay_period_service.earliest_recordable_day(user_id)


def _reject_undatable_observation(user_id: int, observed_on: date) -> None:
    """Refuse an opening day the app cannot honestly model a balance from.

    ``observed_on`` is USER-SUPPLIED and it is not merely a label: it opens the
    modelled-return window (``balance_at._asset_fold._AccrualWindow``, which
    materialises EVERY calendar day from it to the reader's horizon) and it is
    the first period a payroll contribution can be modelled into
    (``_asset_contributions``).  An unbounded value is therefore both a
    correctness defect and a work amplifier: a Property or 401(k) opened "as
    of" year 1 would fabricate contribution history for every past period and
    fold over three quarters of a million days on every dashboard render.

    Two bounds, and each refuses for its own reason:

    * **Not in the future.**  A balance cannot have been observed on a day the
      user has not seen.
    * **Not before the earlier of the schedule's start and today.**  The
      assertion's correction is dated inside a pay period, and the projection
      has nothing to say about a day preceding the whole schedule.  The floor
      takes the EARLIER of the two so a user whose periods are all still in
      the future can nonetheless assert what they hold today.  **It had a
      second reason that ruling R-EO removed**: without the bound,
      ``resolve_anchor_period_id`` fell back to the EARLIEST period and FILED
      the assertion against a period its own ``observed_on`` fell outside.  An
      assertion is no longer filed against a period at all, so that half is
      gone with the function; the accrual-window reason above is why the floor
      stays.

    Args:
        user_id: The account owner, whose pay-period schedule sets the floor.
        observed_on: The candidate civil day.

    Raises:
        ValidationError: When the day is in the future, before the schedule
            starts, or the user has no pay periods at all.  The message names
            the offending value and the bound it broke; the route surfaces it
            verbatim.
    """
    today = display_today()
    if observed_on > today:
        raise ValidationError(
            f"Cannot observe a balance on {observed_on.isoformat()}: that day "
            f"has not happened yet (today is {today.isoformat()}).  An "
            "opening balance states what the account held on a day you have "
            "already seen."
        )
    if not db.session.query(
        db.session.query(PayPeriod).filter_by(user_id=user_id).exists()
    ).scalar():
        raise ValidationError(
            f"Cannot create an account for user_id={user_id}: the user "
            "has no pay periods.  Generate pay periods first so the "
            "account's anchor has a period to reference."
        )
    floor = earliest_observable_day(user_id)
    if observed_on < floor:
        raise ValidationError(
            f"Cannot observe a balance on {observed_on.isoformat()}: your "
            f"recorded history starts on {floor.isoformat()}, and a balance "
            "asserted before then has no pay period to be recorded against.  "
            "Use a day on or after that, or generate earlier pay periods "
            "first."
        )


@dataclass(frozen=True)
class AccountSpec:
    """The canonical inputs for creating an account.

    Bundles the six fields every :func:`create_account` call site
    supplies into one cohesive value object so the factory takes a
    single argument instead of a long keyword list.  The clump is what
    every caller co-loads: a new account is always created from an
    owner, a type, a name, and a real-money anchor (with an optional
    explicit anchor period and an audit-trail note).  Open-ended
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
            anchor balance, the day that balance was true, an optional
            explicit anchor period, and the audit note for the account
            to create.
        **extra_columns: Additional ``Account`` columns (e.g.
            ``sort_order``, ``is_active``).  Forwarded verbatim to
            the model constructor.

    Returns:
        The newly added :class:`Account` (already flushed, so ``account.id``
        is set), with its matching origination ``AccountAnchorHistory`` row in
        the session pending commit.

    Raises:
        ValidationError: When ``observed_on`` is in the future (a balance
            cannot have been observed on a day that has not happened), when it
            precedes the user's recorded history, or when the user has no pay
            periods at all -- all three from
            :func:`_reject_undatable_observation`.
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

    # The day the asserted balance was TRUE (ruling R-DH).  It dates the
    # origination assertion, and the anchor period is resolved FROM it, so the
    # row's two statements of "when" cannot disagree.
    observed_on = (
        spec.observed_on if spec.observed_on is not None else display_today()
    )
    _reject_undatable_observation(spec.user_id, observed_on)
    # NB the default and the guard read ``display_today()`` once each, and the
    # guard re-reads it.  That is deliberate rather than sloppy: the guard is
    # also the entry point for a CALLER-supplied day, so it cannot take the
    # default's clock reading on trust.  A midnight tick between the two makes
    # the default one day stale, never invalid -- the guard's test is ``>``.

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
