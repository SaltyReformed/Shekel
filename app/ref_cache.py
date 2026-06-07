"""
Shekel Budget App -- Reference Table Cache

Loads reference table IDs once at application startup so that service
and route code can resolve enum members to integer IDs without hitting
the database on every request.

Usage::

    from app import ref_cache
    from app.enums import StatusEnum, AcctTypeEnum

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    checking_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)

The cache is initialized by ``create_app()`` after reference tables
are seeded.  If any enum member has no corresponding database row,
``init()`` raises ``RuntimeError`` -- the app refuses to start with
an incomplete reference schema.

Thread safety is NOT provided.  This is a single-user, single-process
Flask application; the cache is written once at startup and read-only
thereafter.
"""

import functools
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict

import sqlalchemy.exc

from app.enums import (
    AcctCategoryEnum,
    AcctTypeEnum,
    CalcMethodEnum,
    DeductionTimingEnum,
    GoalModeEnum,
    IncomeUnitEnum,
    LoanAnchorSourceEnum,
    RecurrencePatternEnum,
    RoleEnum,
    StatusEnum,
    TaxTypeEnum,
    TxnTypeEnum,
)

_logger = logging.getLogger(__name__)


class _AcctTypeMeta(TypedDict):
    """Cached presentation metadata for a built-in account type."""

    icon_class: str | None
    max_term_months: int | None


@dataclass
class _RefState:
    """Process-lifetime reference-cache state.

    A single module-level instance (``_cache``) holds every cached map.
    ``init()`` mutates this object's dicts in place and never rebinds it
    or the module name, so no ``global`` statement is required.

    ``enum_ids`` maps each reference enum class to its ``{member: database
    PK}`` lookup; ``acct_type_meta`` maps an account-type PK to its
    presentation metadata.  Written once at startup (re-written in tests)
    and read-only thereafter via the accessor functions below.
    """

    enum_ids: dict[type[Enum], dict[Enum, int]] = field(default_factory=dict)
    acct_type_meta: dict[int, _AcctTypeMeta] = field(default_factory=dict)
    initialized: bool = False


_cache = _RefState()


@dataclass(frozen=True)
class _RefSpec:
    """Declarative description of one reference table for ``init()`` to load.

    ``label`` (the warning text and ``unavailable`` key) and ``error_prefix``
    (the missing-row error prefix) are derived from the model so there is a
    single source of truth: ``label`` is the table name and ``error_prefix``
    the model class name (e.g. the ``RoleEnum`` table's model is ``UserRole``,
    so its errors read ``UserRole.<member>``).
    """

    enum: type[Enum]
    model: type
    # Filter the query to seeded built-ins (``user_id IS NULL``); set only for
    # account_types.  After commit C-28 / F-044 owners can register custom
    # types whose names collide with built-ins (a user's own "HYSA" alongside
    # the seeded "HYSA").  The cache promises a single stable ID per
    # ``AcctTypeEnum`` member, so it must see only the built-in rows; custom
    # types resolve via the ORM relationship in templates, never this cache.
    builtin_only: bool = False

    @property
    def label(self) -> str:
        """Return the reference table name (warning text / unavailable key)."""
        return self.model.__tablename__

    @property
    def error_prefix(self) -> str:
        """Return the model class name used to prefix missing-row errors."""
        return self.model.__name__

    def query(self, db_session) -> dict[str, int]:
        """Return a ``{row.name: row.id}`` lookup for this table's rows.

        Args:
            db_session: An active SQLAlchemy session.

        Returns:
            dict[str, int]: Row name mapped to its integer primary key.
        """
        model_query = db_session.query(self.model)
        if self.builtin_only:
            model_query = model_query.filter(self.model.user_id.is_(None))
        return {row.name: row.id for row in model_query.all()}


def _load_rows(db_session, label, query_callable):
    """Run a ref-table query, tolerating a missing table.

    A ``ProgrammingError`` here almost always means the ref table does
    not exist yet -- the bootstrap window during ``flask db upgrade``
    when a migration that creates a new ref table is pending.  Catch
    it, roll the session back (a failed query poisons the transaction
    so subsequent queries would otherwise fail with "current
    transaction is aborted"), log loud, and return ``None`` so the
    caller can record the table as unavailable.

    All other database errors propagate -- a misconfigured DSN or a
    corrupted ref row is a real failure that must surface, not a
    bootstrap quirk to swallow.

    Args:
        db_session: SQLAlchemy session for rollback on failure.
        label: Short table label for the warning message.
        query_callable: Zero-arg callable that runs the query and
            returns the name->id dict.

    Returns:
        dict[str, int] on success, ``None`` if the table is missing.
    """
    try:
        return query_callable()
    except sqlalchemy.exc.ProgrammingError:
        db_session.rollback()
        _logger.warning(
            "ref_cache: ref table %s not available "
            "(likely pre-migration bootstrap); enums for this table will "
            "not be cached until the next app start after migrations run.",
            label,
        )
        return None


def _build_ref_specs(ref_models) -> list[_RefSpec]:
    """Return the ordered reference-table specs for ``init()`` to load.

    Built here (not at module scope) because the ORM models are imported
    lazily inside ``init()`` to break the import cycle.  The order matches
    the historical load order, which fixes the order of the ``unavailable``
    list and of the missing-row error message.

    Args:
        ref_models: The lazily-imported ``app.models.ref`` module.

    Returns:
        list[_RefSpec]: One spec per cached reference table.
    """
    return [
        _RefSpec(StatusEnum, ref_models.Status),
        _RefSpec(TxnTypeEnum, ref_models.TransactionType),
        _RefSpec(AcctTypeEnum, ref_models.AccountType, builtin_only=True),
        _RefSpec(AcctCategoryEnum, ref_models.AccountTypeCategory),
        _RefSpec(RecurrencePatternEnum, ref_models.RecurrencePattern),
        _RefSpec(DeductionTimingEnum, ref_models.DeductionTiming),
        _RefSpec(CalcMethodEnum, ref_models.CalcMethod),
        _RefSpec(TaxTypeEnum, ref_models.TaxType),
        _RefSpec(GoalModeEnum, ref_models.GoalMode),
        _RefSpec(IncomeUnitEnum, ref_models.IncomeUnit),
        _RefSpec(RoleEnum, ref_models.UserRole),
        _RefSpec(LoanAnchorSourceEnum, ref_models.LoanAnchorSource),
    ]


def init(db_session):
    """Load all reference table IDs into the in-memory cache.

    Must be called once during ``create_app()`` after reference data
    has been seeded and committed.  Safe to call multiple times (e.g.
    in tests that create fresh app instances) -- clears and reloads.

    Resilient to missing ref tables during the bootstrap window when
    ``flask db upgrade`` is mid-flight: a ref table that does not
    exist yet is logged as a warning and its enum members are left
    out of the cache, but the cache is still marked initialized so
    accessors for unrelated tables work.  A ref table that EXISTS but
    is missing a seeded enum row is still a fatal ``RuntimeError`` --
    that is a genuine data error, not a bootstrap quirk.

    Args:
        db_session: An active SQLAlchemy session (typically ``db.session``).

    Returns:
        list[str]: Labels of ref tables that were unavailable at init
        time (empty list in a healthy production app).  Callers can
        use this to decide whether to skip downstream work that
        depends on the complete cache (e.g. Jinja globals).

    Raises:
        RuntimeError: If any ref table EXISTS but is missing rows for
            one or more of its enum members.
    """
    # Deferred import to avoid circular dependencies.  The models module
    # imports from extensions, which must be initialized before the cache
    # loads.
    import app.models.ref as ref_models  # pylint: disable=import-outside-toplevel

    specs = _build_ref_specs(ref_models)

    # Reset prior state (supports re-initialization in tests).  Mutate the
    # _cache dicts in place; do NOT reset ``initialized`` here -- a failed
    # re-init leaves the previous flag value, matching the original behavior.
    _cache.enum_ids.clear()
    _cache.acct_type_meta.clear()
    for spec in specs:
        _cache.enum_ids[spec.enum] = {}

    # Load each ref table and map its enum members to database IDs.  Each
    # query is wrapped in ``_load_rows`` so a missing ref table (the
    # pre-migration bootstrap window) is recorded as unavailable rather than
    # poisoning the whole cache; that table's enum sweep is then skipped.  A
    # missing row in a table that EXISTS is fatal -- a genuine seed/data error.
    unavailable = []
    missing = []
    for spec in specs:
        rows = _load_rows(db_session, spec.label, functools.partial(spec.query, db_session))
        if rows is None:
            unavailable.append(spec.label)
            continue
        target = _cache.enum_ids[spec.enum]
        for member in spec.enum:
            db_id = rows.get(member.value)
            if db_id is None:
                missing.append(
                    f"{spec.error_prefix}.{member.name} (expected name={member.value!r})"
                )
            else:
                target[member] = db_id

    if missing:
        raise RuntimeError(
            "ref_cache.init() failed -- the following enum members have no "
            "matching database row:\n  " + "\n  ".join(missing)
        )

    # Build the account type metadata cache for icon/term-limit lookups.
    # Same built-in-only filter as the account_types map -- the cache is
    # loaded once at startup and only knows about seeded built-ins.  Skipped
    # when that table is unavailable (already warned during loading).
    # Owner-scoped custom types still resolve their icon/max_term via the ORM
    # relationship in templates (``account.account_type.icon_class``).
    if "account_types" not in unavailable:
        for row in (
            db_session.query(ref_models.AccountType)
            .filter(ref_models.AccountType.user_id.is_(None))
            .all()
        ):
            _cache.acct_type_meta[row.id] = {
                "icon_class": row.icon_class,
                "max_term_months": row.max_term_months,
            }

    _cache.initialized = True
    return unavailable


def _require_init():
    """Raise if the cache has not been initialized via ``init()``."""
    if not _cache.initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")


def status_id(member):
    """Return the integer primary key for a StatusEnum member.

    Args:
        member: A ``StatusEnum`` member (e.g. ``StatusEnum.PROJECTED``).

    Returns:
        int -- the ``ref.statuses.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid StatusEnum member.
    """
    _require_init()
    return _cache.enum_ids[StatusEnum][member]


def txn_type_id(member):
    """Return the integer primary key for a TxnTypeEnum member.

    Args:
        member: A ``TxnTypeEnum`` member (e.g. ``TxnTypeEnum.INCOME``).

    Returns:
        int -- the ``ref.transaction_types.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid TxnTypeEnum member.
    """
    _require_init()
    return _cache.enum_ids[TxnTypeEnum][member]


def transaction_type_is_income(transaction_type_id):
    """Return True if *transaction_type_id* refers to the Income type row.

    Thin convenience over the cached map so cross-field validators and
    other call sites can ask "is this an income type?" without importing
    ``TxnTypeEnum`` themselves.  Used by the template Marshmallow schemas
    to enforce that ``is_envelope`` (envelope rollover semantics) is
    only set on expense templates.

    Args:
        transaction_type_id: Integer primary key of a
            ``ref.transaction_types`` row.

    Returns:
        bool -- True iff *transaction_type_id* equals the cached Income
        type ID; False for the Expense type or any unrecognised value.
        Callers that need to validate the FK itself must do so
        separately (this accessor never raises for unknown IDs).

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    _require_init()
    return transaction_type_id == _cache.enum_ids[TxnTypeEnum][TxnTypeEnum.INCOME]


def acct_type_id(member):
    """Return the integer primary key for an AcctTypeEnum member.

    Args:
        member: An ``AcctTypeEnum`` member (e.g. ``AcctTypeEnum.CHECKING``).

    Returns:
        int -- the ``ref.account_types.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid AcctTypeEnum member.
    """
    _require_init()
    return _cache.enum_ids[AcctTypeEnum][member]


def acct_category_id(member):
    """Return the integer primary key for an AcctCategoryEnum member.

    Args:
        member: An ``AcctCategoryEnum`` member (e.g. ``AcctCategoryEnum.ASSET``).

    Returns:
        int -- the ``ref.account_type_categories.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid AcctCategoryEnum member.
    """
    _require_init()
    return _cache.enum_ids[AcctCategoryEnum][member]


def recurrence_pattern_id(member):
    """Return the integer primary key for a RecurrencePatternEnum member.

    Args:
        member: A ``RecurrencePatternEnum`` member
                (e.g. ``RecurrencePatternEnum.MONTHLY``).

    Returns:
        int -- the ``ref.recurrence_patterns.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid RecurrencePatternEnum member.
    """
    _require_init()
    return _cache.enum_ids[RecurrencePatternEnum][member]


def acct_type_icon(type_id):
    """Return the Bootstrap icon class for an account type, or a default.

    Args:
        type_id: The integer primary key of a ``ref.account_types`` row.

    Returns:
        str -- the ``icon_class`` value, or ``'bi-bank'`` if unset.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    _require_init()
    meta = _cache.acct_type_meta.get(type_id, {})
    return meta.get("icon_class") or "bi-bank"


def acct_type_max_term(type_id):
    """Return the max term months for an account type, or None if no limit.

    Args:
        type_id: The integer primary key of a ``ref.account_types`` row.

    Returns:
        int or None -- the ``max_term_months`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    _require_init()
    meta = _cache.acct_type_meta.get(type_id, {})
    return meta.get("max_term_months")


def deduction_timing_id(member):
    """Return the integer primary key for a DeductionTimingEnum member."""
    _require_init()
    return _cache.enum_ids[DeductionTimingEnum][member]


def calc_method_id(member):
    """Return the integer primary key for a CalcMethodEnum member."""
    _require_init()
    return _cache.enum_ids[CalcMethodEnum][member]


def tax_type_id(member):
    """Return the integer primary key for a TaxTypeEnum member."""
    _require_init()
    return _cache.enum_ids[TaxTypeEnum][member]


def goal_mode_id(member):
    """Return the integer primary key for a GoalModeEnum member.

    Args:
        member: A ``GoalModeEnum`` member (e.g. ``GoalModeEnum.FIXED``).

    Returns:
        int -- the ``ref.goal_modes.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid GoalModeEnum member.
    """
    _require_init()
    return _cache.enum_ids[GoalModeEnum][member]


def income_unit_id(member):
    """Return the integer primary key for an IncomeUnitEnum member.

    Args:
        member: An ``IncomeUnitEnum`` member (e.g. ``IncomeUnitEnum.PAYCHECKS``).

    Returns:
        int -- the ``ref.income_units.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid IncomeUnitEnum member.
    """
    _require_init()
    return _cache.enum_ids[IncomeUnitEnum][member]


def role_id(member):
    """Return the integer primary key for a RoleEnum member.

    Args:
        member: A ``RoleEnum`` member (e.g. ``RoleEnum.OWNER``).

    Returns:
        int -- the ``ref.user_roles.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid RoleEnum member.
    """
    _require_init()
    return _cache.enum_ids[RoleEnum][member]


def loan_anchor_source_id(member):
    """Return the integer primary key for a LoanAnchorSourceEnum member.

    Used by the loan-anchor-event writer (Commit 12 backfill, Commit 16
    true-up flow) and the loan resolver (Commit 13) to compare against
    ``budget.loan_anchor_events.source_id`` without ever reading the
    string ``name``.  Matches the project-wide IDs-for-logic invariant.

    Args:
        member: A ``LoanAnchorSourceEnum`` member
                (e.g. ``LoanAnchorSourceEnum.ORIGINATION``).

    Returns:
        int -- the ``ref.loan_anchor_sources.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid LoanAnchorSourceEnum member.
    """
    _require_init()
    return _cache.enum_ids[LoanAnchorSourceEnum][member]
