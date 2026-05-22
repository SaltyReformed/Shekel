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

import logging

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

# Module-level state -- populated by init(), read by accessors.
_status_map = {}               # StatusEnum member -> int (database PK)
_txn_type_map = {}             # TxnTypeEnum member -> int (database PK)
_acct_type_map = {}            # AcctTypeEnum member -> int (database PK)
_acct_category_map = {}        # AcctCategoryEnum member -> int (database PK)
_recurrence_pattern_map = {}   # RecurrencePatternEnum member -> int (database PK)
_deduction_timing_map = {}     # DeductionTimingEnum member -> int (database PK)
_calc_method_map = {}          # CalcMethodEnum member -> int (database PK)
_tax_type_map = {}             # TaxTypeEnum member -> int (database PK)
_goal_mode_map = {}            # GoalModeEnum member -> int (database PK)
_income_unit_map = {}          # IncomeUnitEnum member -> int (database PK)
_role_map = {}                 # RoleEnum member -> int (database PK)
_loan_anchor_source_map = {}   # LoanAnchorSourceEnum member -> int (database PK)
_acct_type_meta = {}           # int (acct_type PK) -> dict with icon_class, max_term_months
_initialized = False

_logger = logging.getLogger(__name__)


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
    # Deferred imports to avoid circular dependencies.  The models
    # module imports from extensions, which must be initialized before
    # the cache loads.
    from app.models.ref import (  # pylint: disable=import-outside-toplevel
        AccountType,
        AccountTypeCategory,
        CalcMethod,
        DeductionTiming,
        GoalMode,
        IncomeUnit,
        LoanAnchorSource,
        RecurrencePattern,
        Status,
        TaxType,
        TransactionType,
        UserRole,
    )

    global _status_map, _txn_type_map, _acct_type_map  # pylint: disable=global-statement
    global _acct_category_map, _recurrence_pattern_map  # pylint: disable=global-statement
    global _deduction_timing_map, _calc_method_map, _tax_type_map  # pylint: disable=global-statement
    global _goal_mode_map, _income_unit_map  # pylint: disable=global-statement
    global _role_map, _loan_anchor_source_map, _acct_type_meta, _initialized  # pylint: disable=global-statement

    # Clear any prior state (supports re-initialization in tests).
    _status_map = {}
    _txn_type_map = {}
    _acct_type_map = {}
    _acct_category_map = {}
    _recurrence_pattern_map = {}
    _deduction_timing_map = {}
    _calc_method_map = {}
    _tax_type_map = {}
    _goal_mode_map = {}
    _income_unit_map = {}
    _role_map = {}
    _loan_anchor_source_map = {}
    _acct_type_meta = {}

    # Build name -> id lookup from the database.  ``account_types``
    # is filtered to seeded built-ins (``user_id IS NULL``) because
    # after commit C-28 / F-044 owners can register custom types
    # whose names collide with built-ins (the user's own "HYSA"
    # alongside the seeded "HYSA").  The cache's enum-to-id contract
    # promises a single, stable ID per ``AcctTypeEnum`` member; the
    # filter restores uniqueness and keeps custom types out of a
    # cache that is loaded once at startup and could not see them
    # appear later anyway.
    #
    # Each query is wrapped in ``_load_rows`` so a missing ref table
    # (pre-migration bootstrap) does not poison the entire cache.
    # ``None`` from the loader means the table did not exist; the
    # enum sweep below skips that table's enum check so accessors
    # for unrelated tables remain usable.
    status_rows = _load_rows(
        db_session, "statuses",
        lambda: {row.name: row.id for row in db_session.query(Status).all()},
    )
    txn_type_rows = _load_rows(
        db_session, "transaction_types",
        lambda: {row.name: row.id for row in db_session.query(TransactionType).all()},
    )
    acct_type_rows = _load_rows(
        db_session, "account_types",
        lambda: {
            row.name: row.id
            for row in db_session.query(AccountType)
            .filter(AccountType.user_id.is_(None))
            .all()
        },
    )
    acct_category_rows = _load_rows(
        db_session, "account_type_categories",
        lambda: {row.name: row.id for row in db_session.query(AccountTypeCategory).all()},
    )
    recurrence_pattern_rows = _load_rows(
        db_session, "recurrence_patterns",
        lambda: {row.name: row.id for row in db_session.query(RecurrencePattern).all()},
    )
    deduction_timing_rows = _load_rows(
        db_session, "deduction_timings",
        lambda: {row.name: row.id for row in db_session.query(DeductionTiming).all()},
    )
    calc_method_rows = _load_rows(
        db_session, "calc_methods",
        lambda: {row.name: row.id for row in db_session.query(CalcMethod).all()},
    )
    tax_type_rows = _load_rows(
        db_session, "tax_types",
        lambda: {row.name: row.id for row in db_session.query(TaxType).all()},
    )
    goal_mode_rows = _load_rows(
        db_session, "goal_modes",
        lambda: {row.name: row.id for row in db_session.query(GoalMode).all()},
    )
    income_unit_rows = _load_rows(
        db_session, "income_units",
        lambda: {row.name: row.id for row in db_session.query(IncomeUnit).all()},
    )
    role_rows = _load_rows(
        db_session, "user_roles",
        lambda: {row.name: row.id for row in db_session.query(UserRole).all()},
    )
    loan_anchor_source_rows = _load_rows(
        db_session, "loan_anchor_sources",
        lambda: {
            row.name: row.id
            for row in db_session.query(LoanAnchorSource).all()
        },
    )

    # Track which tables were unavailable so the enum-completeness
    # sweep can skip them (a missing-table warning has already been
    # logged) and the caller can decide whether to skip Jinja globals.
    unavailable = [
        label for label, rows in (
            ("statuses", status_rows),
            ("transaction_types", txn_type_rows),
            ("account_types", acct_type_rows),
            ("account_type_categories", acct_category_rows),
            ("recurrence_patterns", recurrence_pattern_rows),
            ("deduction_timings", deduction_timing_rows),
            ("calc_methods", calc_method_rows),
            ("tax_types", tax_type_rows),
            ("goal_modes", goal_mode_rows),
            ("income_units", income_unit_rows),
            ("user_roles", role_rows),
            ("loan_anchor_sources", loan_anchor_source_rows),
        ) if rows is None
    ]

    # Replace any ``None`` (missing table) with an empty dict so the
    # ``.get`` lookups in the enum sweep work uniformly.  The sweep
    # also skips the "missing row" complaint when the table itself
    # was unavailable.
    status_rows = status_rows or {}
    txn_type_rows = txn_type_rows or {}
    acct_type_rows = acct_type_rows or {}
    acct_category_rows = acct_category_rows or {}
    recurrence_pattern_rows = recurrence_pattern_rows or {}
    deduction_timing_rows = deduction_timing_rows or {}
    calc_method_rows = calc_method_rows or {}
    tax_type_rows = tax_type_rows or {}
    goal_mode_rows = goal_mode_rows or {}
    income_unit_rows = income_unit_rows or {}
    role_rows = role_rows or {}
    loan_anchor_source_rows = loan_anchor_source_rows or {}

    unavailable_set = set(unavailable)

    # Map each enum member to its database ID, collecting any misses.
    # A missing row in a TABLE THAT EXISTS is fatal -- that is a
    # genuine seed/data error.  A missing row in a TABLE THAT DOES
    # NOT EXIST is not appended (already warned during loading).
    missing = []

    for member in StatusEnum:
        db_id = status_rows.get(member.value)
        if db_id is None:
            if "statuses" not in unavailable_set:
                missing.append(f"Status.{member.name} (expected name={member.value!r})")
        else:
            _status_map[member] = db_id

    for member in TxnTypeEnum:
        db_id = txn_type_rows.get(member.value)
        if db_id is None:
            if "transaction_types" not in unavailable_set:
                missing.append(f"TransactionType.{member.name} (expected name={member.value!r})")
        else:
            _txn_type_map[member] = db_id

    for member in AcctTypeEnum:
        db_id = acct_type_rows.get(member.value)
        if db_id is None:
            if "account_types" not in unavailable_set:
                missing.append(f"AccountType.{member.name} (expected name={member.value!r})")
        else:
            _acct_type_map[member] = db_id

    for member in AcctCategoryEnum:
        db_id = acct_category_rows.get(member.value)
        if db_id is None:
            if "account_type_categories" not in unavailable_set:
                missing.append(f"AccountTypeCategory.{member.name} (expected name={member.value!r})")
        else:
            _acct_category_map[member] = db_id

    for member in RecurrencePatternEnum:
        db_id = recurrence_pattern_rows.get(member.value)
        if db_id is None:
            if "recurrence_patterns" not in unavailable_set:
                missing.append(f"RecurrencePattern.{member.name} (expected name={member.value!r})")
        else:
            _recurrence_pattern_map[member] = db_id

    for member in DeductionTimingEnum:
        db_id = deduction_timing_rows.get(member.value)
        if db_id is None:
            if "deduction_timings" not in unavailable_set:
                missing.append(f"DeductionTiming.{member.name} (expected name={member.value!r})")
        else:
            _deduction_timing_map[member] = db_id

    for member in CalcMethodEnum:
        db_id = calc_method_rows.get(member.value)
        if db_id is None:
            if "calc_methods" not in unavailable_set:
                missing.append(f"CalcMethod.{member.name} (expected name={member.value!r})")
        else:
            _calc_method_map[member] = db_id

    for member in TaxTypeEnum:
        db_id = tax_type_rows.get(member.value)
        if db_id is None:
            if "tax_types" not in unavailable_set:
                missing.append(f"TaxType.{member.name} (expected name={member.value!r})")
        else:
            _tax_type_map[member] = db_id

    for member in GoalModeEnum:
        db_id = goal_mode_rows.get(member.value)
        if db_id is None:
            if "goal_modes" not in unavailable_set:
                missing.append(f"GoalMode.{member.name} (expected name={member.value!r})")
        else:
            _goal_mode_map[member] = db_id

    for member in IncomeUnitEnum:
        db_id = income_unit_rows.get(member.value)
        if db_id is None:
            if "income_units" not in unavailable_set:
                missing.append(f"IncomeUnit.{member.name} (expected name={member.value!r})")
        else:
            _income_unit_map[member] = db_id

    for member in RoleEnum:
        db_id = role_rows.get(member.value)
        if db_id is None:
            if "user_roles" not in unavailable_set:
                missing.append(f"UserRole.{member.name} (expected name={member.value!r})")
        else:
            _role_map[member] = db_id

    for member in LoanAnchorSourceEnum:
        db_id = loan_anchor_source_rows.get(member.value)
        if db_id is None:
            if "loan_anchor_sources" not in unavailable_set:
                missing.append(
                    f"LoanAnchorSource.{member.name} (expected name={member.value!r})"
                )
        else:
            _loan_anchor_source_map[member] = db_id

    if missing:
        raise RuntimeError(
            "ref_cache.init() failed -- the following enum members have no "
            "matching database row:\n  " + "\n  ".join(missing)
        )

    # Build account type metadata cache for icon/term-limit lookups.
    # Same filter as ``acct_type_rows`` -- the cache is loaded once
    # at startup and only knows about seeded built-ins.  Owner-scoped
    # custom types still resolve their icon/max_term via the ORM
    # relationship in templates (``account.account_type.icon_class``)
    # without going through this cache.  Wrapped in ``_load_rows``
    # semantics: a missing ``ref.account_types`` table is already in
    # ``unavailable_set``, so we skip the meta load too.
    if "account_types" not in unavailable_set:
        for row in (
            db_session.query(AccountType)
            .filter(AccountType.user_id.is_(None))
            .all()
        ):
            _acct_type_meta[row.id] = {
                "icon_class": row.icon_class,
                "max_term_months": row.max_term_months,
            }

    _initialized = True
    return unavailable


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _status_map[member]


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _txn_type_map[member]


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return transaction_type_id == _txn_type_map[TxnTypeEnum.INCOME]


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _acct_type_map[member]


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _acct_category_map[member]


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _recurrence_pattern_map[member]


def acct_type_icon(acct_type_id):
    """Return the Bootstrap icon class for an account type, or a default.

    Args:
        acct_type_id: The integer primary key of a ``ref.account_types`` row.

    Returns:
        str -- the ``icon_class`` value, or ``'bi-bank'`` if unset.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    meta = _acct_type_meta.get(acct_type_id, {})
    return meta.get("icon_class") or "bi-bank"


def acct_type_max_term(acct_type_id):
    """Return the max term months for an account type, or None if no limit.

    Args:
        acct_type_id: The integer primary key of a ``ref.account_types`` row.

    Returns:
        int or None -- the ``max_term_months`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    meta = _acct_type_meta.get(acct_type_id, {})
    return meta.get("max_term_months")


def deduction_timing_id(member):
    """Return the integer primary key for a DeductionTimingEnum member."""
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _deduction_timing_map[member]


def calc_method_id(member):
    """Return the integer primary key for a CalcMethodEnum member."""
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _calc_method_map[member]


def tax_type_id(member):
    """Return the integer primary key for a TaxTypeEnum member."""
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _tax_type_map[member]


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _goal_mode_map[member]


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _income_unit_map[member]


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _role_map[member]


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
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _loan_anchor_source_map[member]
