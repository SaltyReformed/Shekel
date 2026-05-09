"""
Shekel Budget App -- Structured Log Event Definitions

Defines standardized event categories, a registry of every Python-tier
event the application emits, and a helper for emitting structured log
entries with consistent fields.  All log events include the event name
and category as structured ``extra`` fields, making them filterable in
Grafana/Loki.

Three layers of audit coverage cooperate to produce the project's
forensic record (commit C-13, C-14, C-15 of the 2026-04-15 security
remediation plan):

  1. **DB-tier triggers** write to ``system.audit_log`` on every
     INSERT/UPDATE/DELETE against financial/auth tables.  Most
     tamper-resistant -- the runtime app role cannot DROP the trigger.
  2. **Python-tier ``log_event``** (this module) emits structured
     events at every service-layer mutation.  Enables "what happened"
     queries keyed by event name without scanning audit_log row diffs.
  3. **Off-host shipping** flows both layers to S3 / Loki / rsyslog so
     the app container cannot rewrite history.

Event naming convention:  ``<verb>_<noun>`` or ``<noun>_<event>`` in
snake_case.  Every event used in the codebase MUST be registered here
via :func:`_register` so tests can assert completeness and so
operators can introspect the catalogue from the Python REPL.

Audit references: F-080 (Medium), F-085 (Low), F-144 (Low) -- all
remediated by commit C-14.
"""
import logging


# ---------------------------------------------------------------------------
# Event category constants
# ---------------------------------------------------------------------------
#
# Categories partition the event namespace by *intent*.  A single
# category-string travels with every record, so dashboards can filter
# (e.g. "show me every ACCESS event in the last hour") without having
# to maintain a name-to-category lookup table at query time.

#: Authentication and credential lifecycle (login/logout, password
#: change, MFA enrol/verify/disable, lockouts, HIBP rejections).
AUTH = "auth"

#: Business-domain mutations (transfers, transactions, entries, pay
#: periods, recurrence generation, carry-forward).  The dominant
#: category for service-tier events.
BUSINESS = "business"

#: Authorization decisions and IDOR boundary events (cross-user
#: resource access denied, owner-only route blocked, true 404s).
#: Distinguishing ACCESS from AUTH lets SOC tooling alert on probing
#: patterns without false-positives from password failures.
ACCESS = "access"

#: Reserved category for explicit audit-trail markers that do not fit
#: the other categories (e.g. compliance attestations, manual data
#: corrections).  No events emit under AUDIT today; the constant is
#: here so a future feature can add one without bumping the registry's
#: schema version.
AUDIT = "audit"

#: Unhandled-error events emitted alongside an exception trace so the
#: operator can wire alerting on event-name without grepping
#: tracebacks.
ERROR = "error"

#: Per-request lifecycle (slow_request, request_complete) emitted by
#: ``app.utils.logging_config``.  Not a service-tier category.
PERFORMANCE = "performance"


# ---------------------------------------------------------------------------
# Event registry
# ---------------------------------------------------------------------------
#
# Every event the application emits via :func:`log_event` MUST be
# registered here.  The registry serves three purposes:
#
#   1. **Test completeness gate.**  ``tests/test_utils/test_log_events.py``
#      asserts that no two events share a name and that every category
#      string is one of the constants above.  A new event that is
#      emitted but not registered makes the test fail loudly.
#
#   2. **Operator introspection.**  ``python -c "from app.utils.log_events
#      import EVENT_REGISTRY; pprint(EVENT_REGISTRY)"`` lists every
#      Python-tier event the app can emit, the category each falls
#      under, and a one-line description.  Used by the SOC runbook.
#
#   3. **Self-documenting call sites.**  Service modules import the
#      ``EVT_*`` symbol rather than passing a bare string, so a typo
#      surfaces at import time as a NameError rather than at run time
#      as a silent observability gap.

EVENT_REGISTRY: dict[str, dict[str, str]] = {}


def _register(name: str, category: str, description: str) -> str:
    """Register an event in :data:`EVENT_REGISTRY` and return its name.

    Raises a :class:`ValueError` on duplicate registration.  Called at
    module import time for every ``EVT_*`` constant defined below.

    Args:
        name: The structured-log ``event`` field value.  MUST be a
            snake_case string of the form ``<verb>_<noun>`` or
            ``<noun>_<event>``.  No dots, no hyphens.
        category: One of the category constants above.  Validated so
            a typo in the constant name (e.g. ``"buisness"``) cannot
            quietly bypass dashboard filters.
        description: Single-line human-readable summary of WHEN the
            event fires.  Surfaces in operator introspection output.

    Returns:
        The ``name`` argument unchanged so the registration can be
        used as a one-liner module-level constant assignment::

            EVT_FOO = _register("foo", BUSINESS, "...")

    Raises:
        ValueError: If ``name`` is already in the registry, or if
            ``category`` is not a recognised category constant.
    """
    if name in EVENT_REGISTRY:
        # Duplicate registration is always a programming error -- two
        # service modules emitting the same event name would conflate
        # otherwise-distinct actions in dashboards.  Fail loud at
        # import time.
        raise ValueError(
            f"log event {name!r} is already registered; refusing duplicate "
            "registration so dashboards stay unambiguous."
        )
    if category not in (AUTH, BUSINESS, ACCESS, AUDIT, ERROR, PERFORMANCE):
        raise ValueError(
            f"log event {name!r} declares unknown category {category!r}; "
            "use AUTH / BUSINESS / ACCESS / AUDIT / ERROR / PERFORMANCE."
        )
    EVENT_REGISTRY[name] = {
        "category": category,
        "description": description,
    }
    return name


# ── Auth events (existing call sites; constants centralise names) ──

EVT_LOGIN_SUCCESS = _register(
    "login_success", AUTH,
    "User completed primary authentication (no MFA gate or post-MFA).",
)
EVT_LOGIN_FAILED = _register(
    "login_failed", AUTH,
    "Login attempt rejected (bad credentials or active lockout).",
)
EVT_LOGOUT = _register(
    "logout", AUTH,
    "User terminated their session via /logout.",
)
EVT_USER_REGISTERED = _register(
    "user_registered", AUTH,
    "New user account created via /register (F-085 / C-14).",
)
EVT_PASSWORD_CHANGED = _register(
    "password_changed", AUTH,
    "User changed their password via /change-password.",
)
EVT_SESSIONS_INVALIDATED = _register(
    "sessions_invalidated", AUTH,
    "User invoked the global session-invalidation control.",
)
EVT_OTHER_SESSIONS_INVALIDATED = _register(
    "other_sessions_invalidated", AUTH,
    "Background helper invalidated every session except the current one.",
)
EVT_REAUTH_FAILED = _register(
    "reauth_failed", AUTH,
    "Step-up re-authentication failed (bad password or bad TOTP).",
)
EVT_REAUTH_SUCCESS = _register(
    "reauth_success", AUTH,
    "Step-up re-authentication succeeded; fresh-login window restarted.",
)
EVT_MFA_LOGIN_SUCCESS = _register(
    "mfa_login_success", AUTH,
    "User completed login including the MFA factor.",
)
EVT_MFA_ENABLED = _register(
    "mfa_enabled", AUTH,
    "User enrolled an authenticator and confirmed the first TOTP.",
)
EVT_MFA_DISABLED = _register(
    "mfa_disabled", AUTH,
    "User removed their MFA enrolment via /mfa/disable.",
)
EVT_BACKUP_CODES_REGENERATED = _register(
    "backup_codes_regenerated", AUTH,
    "User issued themselves a new set of MFA backup codes.",
)
EVT_TOTP_REPLAY_REJECTED = _register(
    "totp_replay_rejected", AUTH,
    "TOTP code matched a previously-consumed timestep (F-005 / C-09).",
)
EVT_HIBP_CHECK_FAILED = _register(
    "hibp_check_failed", AUTH,
    "HIBP breached-password check failed open (network/HTTP error).",
)
EVT_HIBP_CHECK_REJECTED = _register(
    "hibp_check_rejected", AUTH,
    "HIBP rejected a candidate password as breached.",
)
EVT_ACCOUNT_LOCKED = _register(
    "account_locked", AUTH,
    "Threshold-many consecutive failures triggered an account lockout.",
)


# ── Access / authorization events (F-144 / C-14) ───────────────────
#
# These three close the "silent IDOR" gap noted in F-144: prior to
# C-14, ``require_owner`` and ``get_or_404`` returned 404 without
# emitting any event, so a probing companion or cross-user attacker
# left no application-tier trace.  Now every ownership-failure path
# emits a structured event the SOC can alert on.

EVT_ACCESS_DENIED_OWNER_ONLY = _register(
    "access_denied_owner_only", ACCESS,
    "Companion (or other non-owner) tried to load an owner-only route.",
)
EVT_ACCESS_DENIED_CROSS_USER = _register(
    "access_denied_cross_user", ACCESS,
    "User tried to load a resource that exists but belongs to another user.",
)
EVT_RESOURCE_NOT_FOUND = _register(
    "resource_not_found", ACCESS,
    "Ownership check ran against a primary key that has no row.",
)
EVT_RATE_LIMIT_EXCEEDED = _register(
    "rate_limit_exceeded", ACCESS,
    "Flask-Limiter rejected a request that exceeded its per-IP quota.",
)


# ── Business events: existing call sites ───────────────────────────

EVT_RECURRENCE_GENERATED = _register(
    "recurrence_generated", BUSINESS,
    "Recurrence engine created transactions for a template.",
)
EVT_CROSS_USER_BLOCKED = _register(
    "cross_user_blocked", BUSINESS,
    "Defense-in-depth check refused a recurrence operation crossing users.",
)
EVT_CARRY_FORWARD = _register(
    "carry_forward", BUSINESS,
    "User carried forward unpaid items from one period to the next.",
)
EVT_LOAN_RECURRENCE_END_DATE_UPDATED = _register(
    "loan_recurrence_end_date_updated", BUSINESS,
    "Loan recurrence rule end date snapped to the projected payoff date.",
)


# ── Business events: transfer service ──────────────────────────────

EVT_TRANSFER_CREATED = _register(
    "transfer_created", BUSINESS,
    "Transfer service created a transfer with its two shadow transactions.",
)
EVT_TRANSFER_UPDATED = _register(
    "transfer_updated", BUSINESS,
    "Transfer service updated a transfer and propagated to shadows.",
)
EVT_TRANSFER_SOFT_DELETED = _register(
    "transfer_soft_deleted", BUSINESS,
    "Transfer service flagged a transfer (and its shadows) is_deleted=True.",
)
EVT_TRANSFER_HARD_DELETED = _register(
    "transfer_hard_deleted", BUSINESS,
    "Transfer service hard-deleted a transfer; CASCADE removed shadows.",
)
EVT_TRANSFER_RESTORED = _register(
    "transfer_restored", BUSINESS,
    "Transfer service restored a soft-deleted transfer.",
)
EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT = _register(
    "transfer_restore_refused_archived_account", BUSINESS,
    "restore_transfer refused to reactivate a transfer whose source or "
    "destination account is archived (is_active = False).  Closes F-164.",
)


# ── Business events: credit (legacy per-transaction) workflow ──────

EVT_CREDIT_MARKED = _register(
    "credit_marked", BUSINESS,
    "Transaction marked Credit; payback expense generated in next period.",
)
EVT_CREDIT_UNMARKED = _register(
    "credit_unmarked", BUSINESS,
    "Credit-marked transaction reverted to Projected; payback deleted.",
)


# ── Business events: per-entry credit workflow ─────────────────────

EVT_ENTRY_PAYBACK_CREATED = _register(
    "entry_payback_created", BUSINESS,
    "Aggregated CC Payback created from credit entries on a transaction.",
)
EVT_ENTRY_PAYBACK_UPDATED = _register(
    "entry_payback_updated", BUSINESS,
    "Aggregated CC Payback amount adjusted as credit entries changed.",
)
EVT_ENTRY_PAYBACK_DELETED = _register(
    "entry_payback_deleted", BUSINESS,
    "Aggregated CC Payback removed when its credit entries went to zero.",
)


# ── Business events: transaction entries ───────────────────────────

EVT_ENTRY_CREATED = _register(
    "entry_created", BUSINESS,
    "Individual purchase entry recorded against an envelope transaction.",
)
EVT_ENTRY_UPDATED = _register(
    "entry_updated", BUSINESS,
    "Individual purchase entry modified (amount, description, date, credit).",
)
EVT_ENTRY_DELETED = _register(
    "entry_deleted", BUSINESS,
    "Individual purchase entry hard-deleted from a transaction.",
)
EVT_ENTRY_CLEARED_TOGGLED = _register(
    "entry_cleared_toggled", BUSINESS,
    "User flipped is_cleared on a single entry (manual reservation override).",
)
EVT_ENTRIES_CLEARED_ON_ANCHOR_TRUEUP = _register(
    "entries_cleared_on_anchor_trueup", BUSINESS,
    "Anchor true-up bulk-cleared past-dated entries on projected parents.",
)


# ── Business events: pay periods ───────────────────────────────────

EVT_PAY_PERIODS_GENERATED = _register(
    "pay_periods_generated", BUSINESS,
    "Pay-period service created one or more new biweekly periods.",
)


# ── Business events: recurrence engines (regenerate / resolve) ─────

EVT_RECURRENCE_REGENERATED = _register(
    "recurrence_regenerated", BUSINESS,
    "Recurrence engine deleted auto-generated rows and re-emitted them.",
)
EVT_RECURRENCE_CONFLICTS_RESOLVED = _register(
    "recurrence_conflicts_resolved", BUSINESS,
    "User resolved override/delete conflicts after a regeneration.",
)
EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED = _register(
    "resolve_conflicts_shadow_refused", BUSINESS,
    "recurrence_engine.resolve_conflicts refused to mutate a transfer "
    "shadow transaction (transfer_id IS NOT NULL).  Closes F-007.",
)
EVT_TRANSFER_RECURRENCE_GENERATED = _register(
    "transfer_recurrence_generated", BUSINESS,
    "Transfer recurrence engine generated transfers from a template.",
)
EVT_TRANSFER_RECURRENCE_REGENERATED = _register(
    "transfer_recurrence_regenerated", BUSINESS,
    "Transfer recurrence engine deleted and regenerated auto transfers.",
)
EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED = _register(
    "transfer_recurrence_conflicts_resolved", BUSINESS,
    "User resolved override/delete conflicts after a transfer regeneration.",
)


# ── Business events: transaction service helpers ───────────────────

EVT_TRANSACTION_SETTLED_FROM_ENTRIES = _register(
    "transaction_settled_from_entries", BUSINESS,
    "Envelope transaction settled at sum(entries) with status -> Done/Received.",
)


# ── Performance events (request lifecycle) ─────────────────────────

EVT_REQUEST_COMPLETE = _register(
    "request_complete", PERFORMANCE,
    "Request completed within the slow-request threshold (DEBUG-level).",
)
EVT_SLOW_REQUEST = _register(
    "slow_request", PERFORMANCE,
    "Request exceeded SLOW_REQUEST_THRESHOLD_MS (WARNING-level).",
)


# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    category: str,
    message: str,
    **extra,
):
    """Emit a structured log entry with standardized fields.

    Every call site SHOULD pass an ``EVT_*`` constant for *event*
    rather than a bare string so a typo surfaces as a NameError at
    import time.  The helper does not enforce registry membership at
    runtime to keep the per-emit cost at O(1) -- the test suite covers
    completeness instead (see ``tests/test_utils/test_log_events.py``).

    Args:
        logger: The logger instance (typically ``logging.getLogger(__name__)``).
        level: Logging level (e.g., ``logging.INFO``).
        event: Machine-readable event name -- pass an ``EVT_*`` constant.
        category: Event category (one of ``AUTH``, ``BUSINESS``,
            ``ACCESS``, ``AUDIT``, ``ERROR``, ``PERFORMANCE``).
        message: Human-readable description; appears in the log
            record's ``message`` field for analyst readability.
        **extra: Additional key-value pairs included in the JSON
            output's structured fields.  Decimal values MUST be
            serialised by the caller (``str(amount)``) -- the JSON
            formatter does not know how to encode Decimal natively.
    """
    logger.log(
        level,
        message,
        extra={"event": event, "category": category, **extra},
    )
