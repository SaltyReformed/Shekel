"""
Shekel Budget App -- Reference Table Cache

Loads reference table IDs once at application startup so that service
and route code can resolve enum members to integer IDs without hitting
the database on every request.

Usage::

    from app import ref_cache
    from app.enums import StatusEnum

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

The cache is initialized by ``create_app()`` after reference tables
are seeded.  If any enum member has no corresponding database row,
``init()`` raises ``RuntimeError`` -- the app refuses to start with
an incomplete reference schema.

Thread safety is NOT provided.  This is a single-user, single-process
Flask application; the cache is written once at startup and read-only
thereafter.
"""

# ------------------------------------------------------------------
# Implementation Plan Discrepancies (Commit #1)
# ------------------------------------------------------------------
# - None found.
# ------------------------------------------------------------------

from app.enums import StatusEnum, TxnTypeEnum

# Module-level state -- populated by init(), read by accessors.
_status_map = {}     # StatusEnum member -> int (database PK)
_txn_type_map = {}   # TxnTypeEnum member -> int (database PK)
_initialized = False


def init(db_session):
    """Load all reference table IDs into the in-memory cache.

    Must be called once during ``create_app()`` after reference data
    has been seeded and committed.  Safe to call multiple times (e.g.
    in tests that create fresh app instances) -- clears and reloads.

    Args:
        db_session: An active SQLAlchemy session (typically ``db.session``).

    Raises:
        RuntimeError: If any StatusEnum or TxnTypeEnum member has no
            matching row in the database.  The error message names
            every missing member.
    """
    # Deferred imports to avoid circular dependencies.  The models
    # module imports from extensions, which must be initialized before
    # the cache loads.
    from app.models.ref import Status, TransactionType  # pylint: disable=import-outside-toplevel

    global _status_map, _txn_type_map, _initialized  # pylint: disable=global-statement

    # Clear any prior state (supports re-initialization in tests).
    _status_map = {}
    _txn_type_map = {}

    # Build name -> id lookup from the database.
    status_rows = {row.name: row.id for row in db_session.query(Status).all()}
    txn_type_rows = {row.name: row.id for row in db_session.query(TransactionType).all()}

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

    if missing:
        raise RuntimeError(
            "ref_cache.init() failed -- the following enum members have no "
            "matching database row:\n  " + "\n  ".join(missing)
        )

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
