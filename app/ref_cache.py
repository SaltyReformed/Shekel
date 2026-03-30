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

from app.enums import (
    AcctCategoryEnum,
    AcctTypeEnum,
    CalcMethodEnum,
    DeductionTimingEnum,
    RecurrencePatternEnum,
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
_acct_type_meta = {}           # int (acct_type PK) -> dict with icon_class, max_term_months
_initialized = False


def init(db_session):
    """Load all reference table IDs into the in-memory cache.

    Must be called once during ``create_app()`` after reference data
    has been seeded and committed.  Safe to call multiple times (e.g.
    in tests that create fresh app instances) -- clears and reloads.

    Args:
        db_session: An active SQLAlchemy session (typically ``db.session``).

    Raises:
        RuntimeError: If any enum member has no matching row in the
            database.  The error message names every missing member.
    """
    # Deferred imports to avoid circular dependencies.  The models
    # module imports from extensions, which must be initialized before
    # the cache loads.
    from app.models.ref import (  # pylint: disable=import-outside-toplevel
        AccountType,
        AccountTypeCategory,
        CalcMethod,
        DeductionTiming,
        RecurrencePattern,
        Status,
        TaxType,
        TransactionType,
    )

    global _status_map, _txn_type_map, _acct_type_map  # pylint: disable=global-statement
    global _acct_category_map, _recurrence_pattern_map  # pylint: disable=global-statement
    global _deduction_timing_map, _calc_method_map, _tax_type_map  # pylint: disable=global-statement
    global _acct_type_meta, _initialized  # pylint: disable=global-statement

    # Clear any prior state (supports re-initialization in tests).
    _status_map = {}
    _txn_type_map = {}
    _acct_type_map = {}
    _acct_category_map = {}
    _recurrence_pattern_map = {}
    _deduction_timing_map = {}
    _calc_method_map = {}
    _tax_type_map = {}
    _acct_type_meta = {}

    # Build name -> id lookup from the database.
    status_rows = {row.name: row.id for row in db_session.query(Status).all()}
    txn_type_rows = {row.name: row.id for row in db_session.query(TransactionType).all()}
    acct_type_rows = {row.name: row.id for row in db_session.query(AccountType).all()}
    acct_category_rows = {row.name: row.id for row in db_session.query(AccountTypeCategory).all()}
    recurrence_pattern_rows = {row.name: row.id for row in db_session.query(RecurrencePattern).all()}
    deduction_timing_rows = {row.name: row.id for row in db_session.query(DeductionTiming).all()}
    calc_method_rows = {row.name: row.id for row in db_session.query(CalcMethod).all()}
    tax_type_rows = {row.name: row.id for row in db_session.query(TaxType).all()}

    # Map each enum member to its database ID, collecting any misses.
    missing = []

    for member in StatusEnum:
        db_id = status_rows.get(member.value)
        if db_id is None:
            missing.append(f"Status.{member.name} (expected name={member.value!r})")
        else:
            _status_map[member] = db_id

    for member in TxnTypeEnum:
        db_id = txn_type_rows.get(member.value)
        if db_id is None:
            missing.append(f"TransactionType.{member.name} (expected name={member.value!r})")
        else:
            _txn_type_map[member] = db_id

    for member in AcctTypeEnum:
        db_id = acct_type_rows.get(member.value)
        if db_id is None:
            missing.append(f"AccountType.{member.name} (expected name={member.value!r})")
        else:
            _acct_type_map[member] = db_id

    for member in AcctCategoryEnum:
        db_id = acct_category_rows.get(member.value)
        if db_id is None:
            missing.append(f"AccountTypeCategory.{member.name} (expected name={member.value!r})")
        else:
            _acct_category_map[member] = db_id

    for member in RecurrencePatternEnum:
        db_id = recurrence_pattern_rows.get(member.value)
        if db_id is None:
            missing.append(f"RecurrencePattern.{member.name} (expected name={member.value!r})")
        else:
            _recurrence_pattern_map[member] = db_id

    for member in DeductionTimingEnum:
        db_id = deduction_timing_rows.get(member.value)
        if db_id is None:
            missing.append(f"DeductionTiming.{member.name} (expected name={member.value!r})")
        else:
            _deduction_timing_map[member] = db_id

    for member in CalcMethodEnum:
        db_id = calc_method_rows.get(member.value)
        if db_id is None:
            missing.append(f"CalcMethod.{member.name} (expected name={member.value!r})")
        else:
            _calc_method_map[member] = db_id

    for member in TaxTypeEnum:
        db_id = tax_type_rows.get(member.value)
        if db_id is None:
            missing.append(f"TaxType.{member.name} (expected name={member.value!r})")
        else:
            _tax_type_map[member] = db_id

    if missing:
        raise RuntimeError(
            "ref_cache.init() failed -- the following enum members have no "
            "matching database row:\n  " + "\n  ".join(missing)
        )

    # Build account type metadata cache for icon/term-limit lookups.
    for row in db_session.query(AccountType).all():
        _acct_type_meta[row.id] = {
            "icon_class": row.icon_class,
            "max_term_months": row.max_term_months,
        }

    _initialized = True


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
