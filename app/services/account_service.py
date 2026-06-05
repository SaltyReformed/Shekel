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
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.exceptions import ValidationError
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.services import pay_period_service


logger = logging.getLogger(__name__)


def _resolve_anchor_period_id(user_id: int) -> int:
    """Return the pay_period_id to anchor a new account against.

    Resolution order mirrors the migration cfb15e782f86 backfill rule:

      1. The user's current pay period (the one containing today's
         date).  This is the most semantically accurate origin when
         it exists -- a freshly created account becomes live "now".
      2. The user's earliest pay period (lowest ``period_index``).
         Used when no period contains today (e.g. the user generated
         only historical periods).

    Args:
        user_id: ``auth.users.id`` of the account owner.

    Returns:
        The resolved ``budget.pay_periods.id``.

    Raises:
        ValidationError: When the user has no pay periods at all.
            Caller should surface this as a UX prompt to generate
            pay periods first; production callers must not silently
            paper over the absence by inserting a synthetic period.
    """
    current = pay_period_service.get_current_period(user_id)
    if current is not None:
        return current.id
    earliest = (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.period_index)
        .first()
    )
    if earliest is None:
        raise ValidationError(
            f"Cannot create an account for user_id={user_id}: the user "
            "has no pay periods.  Generate pay periods first so the "
            "account's anchor has a period to reference."
        )
    return earliest.id


def create_account(
    *,
    user_id: int,
    account_type_id: int,
    name: str,
    anchor_balance: Decimal,
    anchor_period_id: int | None = None,
    notes: str = "origination",
    **extra_columns,
) -> Account:
    """Construct an Account row plus its matching AccountAnchorHistory.

    Performs the E-19 / CRIT-01 invariant work in one place: resolves
    the anchor period (if not supplied), constructs the Account with
    non-NULL anchor columns, flushes to assign ``account.id``, then
    inserts the origination history row.  The pair is appended to
    the current session; the caller commits.

    Args:
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
        anchor_period_id: Optional ``budget.pay_periods.id`` to anchor
            against.  When omitted, the service resolves it from the
            user's pay periods via :func:`_resolve_anchor_period_id`.
        notes: Free-text label written into the origination
            ``AccountAnchorHistory`` row's ``notes`` column so the
            audit trail names the originating path.  Defaults to
            ``"origination"``; callers like the seed scripts override
            to e.g. ``"origination (seed_user.py)"``.
        **extra_columns: Additional ``Account`` columns (e.g.
            ``sort_order``, ``is_active``).  Forwarded verbatim to
            the model constructor.

    Returns:
        The newly added :class:`Account` (already flushed; ``account.id``
        is set, ``current_anchor_balance`` and ``current_anchor_period_id``
        are non-NULL, and a matching ``AccountAnchorHistory`` row sits
        in the session pending commit).

    Raises:
        ValidationError: When ``anchor_period_id`` is omitted and the
            user has no pay periods.  Re-raised from
            :func:`_resolve_anchor_period_id`.
        TypeError: When ``anchor_balance`` is not a ``Decimal``.  The
            project rejects ``float`` in monetary code; passing
            ``int`` or ``str`` is also a caller bug.
    """
    # ``Decimal`` is the canonical type for monetary values per
    # ``docs/coding-standards.md``.  ``int`` is exact when converted to
    # Decimal and is a common test-fixture shorthand for "exactly $0";
    # we coerce it.  ``float`` is rejected outright -- ``Decimal(0.1)``
    # introduces silent precision drift, and the project forbids it.
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

    if anchor_period_id is None:
        anchor_period_id = _resolve_anchor_period_id(user_id)

    account = Account(
        user_id=user_id,
        account_type_id=account_type_id,
        name=name,
        current_anchor_balance=anchor_balance,
        current_anchor_period_id=anchor_period_id,
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
        pay_period_id=anchor_period_id,
        anchor_balance=anchor_balance,
        notes=notes,
    ))

    logger.info(
        "Created account %s (id=%d, user_id=%d) anchored to period %d at $%s",
        name, account.id, user_id, anchor_period_id, anchor_balance,
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
