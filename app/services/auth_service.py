"""
Shekel Budget App -- Authentication Service

Handles password hashing, verification, breached-password checking,
account lockout, and user registration.  No Flask imports -- this is a
pure service module.  Configuration that needs to be runtime-tunable
(lockout threshold, lockout duration, HIBP toggle) is read via
``os.getenv`` at function-call time so the test suite can override
each value through ``monkeypatch.setenv`` without going through the
Flask config object.  Defaults documented alongside the matching
``BaseConfig`` settings in ``app/config.py``.
"""

import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import bcrypt
import requests

from app import ref_cache
from app.enums import AcctTypeEnum, TaxTypeEnum
from app.extensions import db
from app.models.user import User, UserSettings
# Account / AccountAnchorHistory are not imported at module level;
# the canonical factory in ``app.services.account_service`` materializes
# both and is the only acceptable creation path post-E-19.
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import FilingStatus
from app.models.scenario import Scenario
from app.models.tax_config import FicaConfig
from app.exceptions import AuthError, ConflictError, ValidationError
from app.services import account_service
from app.services.tax_seed_data import (
    DEFAULT_FEDERAL_BRACKETS,
    DEFAULT_FICA,
    DEFAULT_STATE_CHILD_DEDUCTIONS,
    DEFAULT_STATE_TAX,
    build_state_child_deductions,
    build_state_tax_configs,
    build_tax_bracket_set,
    build_tax_brackets,
)
from app.utils.dates import display_today
from app.utils.log_events import (
    AUTH,
    EVT_ACCOUNT_LOCKED,
    EVT_HIBP_CHECK_FAILED,
    EVT_HIBP_CHECK_REJECTED,
    log_event,
)


logger = logging.getLogger(__name__)


# ----- Account-lockout helpers (audit finding F-033 / commit C-11) -----
#
# Read on every call rather than at module import so the test suite can
# adjust thresholds via ``monkeypatch.setenv``.  Defaults match
# ``BaseConfig.LOCKOUT_THRESHOLD`` and ``BaseConfig.LOCKOUT_DURATION_MINUTES``.

_LOCKOUT_THRESHOLD_DEFAULT = 10
_LOCKOUT_DURATION_MINUTES_DEFAULT = 15


def _get_lockout_threshold():
    """Read the consecutive-failure threshold for triggering a lockout.

    Returns the integer threshold from ``LOCKOUT_THRESHOLD`` in the
    environment, defaulting to ``10`` if unset.  A non-positive value
    is rejected because it would never trip (zero or negative threshold
    means "lock immediately on the first wrong password" -- a config
    accident, not a deliberate posture; legitimate users would lock
    themselves out with their first typo).

    Returns:
        int: Number of consecutive failures required to trip a lockout.

    Raises:
        ValueError: If the configured value is not a positive integer.
    """
    value = int(os.getenv("LOCKOUT_THRESHOLD", str(_LOCKOUT_THRESHOLD_DEFAULT)))
    if value <= 0:
        raise ValueError(
            f"LOCKOUT_THRESHOLD must be a positive integer; got {value}."
        )
    return value


def _get_lockout_duration():
    """Read the lockout window length as a ``timedelta``.

    Returns the duration from ``LOCKOUT_DURATION_MINUTES`` in the
    environment, defaulting to ``15`` minutes if unset.  A non-positive
    value is rejected because a zero-duration lockout never blocks any
    login attempt (the ``locked_until > now`` check is strict greater-
    than) and a negative value is logically meaningless.

    Returns:
        timedelta: How long an account remains locked after the
            threshold trips.

    Raises:
        ValueError: If the configured value is not a positive integer.
    """
    minutes = int(
        os.getenv(
            "LOCKOUT_DURATION_MINUTES",
            str(_LOCKOUT_DURATION_MINUTES_DEFAULT),
        )
    )
    if minutes <= 0:
        raise ValueError(
            "LOCKOUT_DURATION_MINUTES must be a positive integer; "
            f"got {minutes}."
        )
    return timedelta(minutes=minutes)


# ----- HIBP breached-password helpers (audit finding F-086 / C-11) -----

# Public k-anonymity endpoint.  ``api.pwnedpasswords.com`` accepts a
# 5-character SHA-1 prefix and returns every full SHA-1 hash beginning
# with that prefix, separated by suffix-and-count pairs.  The plaintext
# password is never sent to HIBP -- only the prefix, which is shared by
# at minimum 2^15 ~ 32k possible passwords, so the lookup leaks no
# information about which password is being checked.
_HIBP_ENDPOINT = "https://api.pwnedpasswords.com/range/{prefix}"

# Default network timeout if ``HIBP_TIMEOUT_SECONDS`` is unset.  Three
# seconds is short enough that a registration form does not appear to
# hang on a slow upstream and long enough to absorb normal jitter on
# api.pwnedpasswords.com.
_HIBP_TIMEOUT_SECONDS_DEFAULT = 3.0


def _hibp_check_enabled():
    """Return True when the HIBP breached-password check should run.

    Reads ``HIBP_CHECK_ENABLED`` from the environment; truthy strings
    (``"true"``, ``"1"``, ``"yes"``, case-insensitive) enable the check.
    Anything else disables it.  The default when unset is ``True`` so
    that an operator who simply runs the app in production gets the
    breach check; explicit ``HIBP_CHECK_ENABLED=false`` is required to
    opt out.

    The conftest in ``tests/conftest.py`` flips this off for the whole
    suite via an autouse fixture so individual tests do not have to
    mock ``requests.get`` on every fixture path that calls
    ``hash_password``.  Tests that exercise HIBP behaviour explicitly
    flip it back on through ``monkeypatch.setenv``.

    Returns:
        bool: True iff the check should be performed.
    """
    return os.getenv("HIBP_CHECK_ENABLED", "true").lower() in (
        "true", "1", "yes",
    )


def _check_pwned_password(plain_password):
    """Reject a password that has appeared in a public breach dataset.

    Uses HIBP's k-anonymity API: only the first five characters of the
    password's SHA-1 hash leave the host.  The full SHA-1 is never
    transmitted, and the response contains 800 to 1000 candidate
    suffixes per prefix (out of which at most one will be the real
    match), so the lookup leaks no usable information about which
    password was being checked.  See https://haveibeenpwned.com/API/v3
    section "Searching by range".

    Behaviour matrix:

      * Check disabled (``HIBP_CHECK_ENABLED=false``) -> return without
        contacting HIBP.  Used by the test suite and by operators who
        intentionally disable the outbound dependency.
      * Network error, timeout, or non-2xx response -> log a warning
        and return.  Fail-open: a transient HIBP outage must not stop
        a legitimate user from registering or rotating their password.
        The warning is the operator-side signal that breach checking
        is currently degraded.
      * Password's full SHA-1 suffix is present in the response with
        a count > 0 -> raise ``ValidationError``.  The user-facing
        message names the breach class and instructs the user to pick
        a different password; it does NOT reveal the breach count or
        any HIBP-specific terminology that would leak which dataset
        the password was matched against.

    Args:
        plain_password: The plaintext password to check.  Encoded as
            UTF-8 before hashing; non-string input would surface as a
            ``UnicodeDecodeError`` from ``encode`` and is the caller's
            responsibility to avoid.

    Returns:
        None on accept (either the check ran and the password was not
        breached, or the check was disabled, or HIBP was unreachable
        and the call failed open).

    Raises:
        ValidationError: The password's SHA-1 hash matches a known
            breached entry.
    """
    if not _hibp_check_enabled():
        return

    sha1 = hashlib.sha1(  # nosec B324 -- SHA-1 is mandated by the HIBP API
        plain_password.encode("utf-8"),
    ).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]

    timeout = float(
        os.getenv(
            "HIBP_TIMEOUT_SECONDS",
            str(_HIBP_TIMEOUT_SECONDS_DEFAULT),
        )
    )

    try:
        response = requests.get(
            _HIBP_ENDPOINT.format(prefix=prefix),
            timeout=timeout,
            # Add-Padding asks HIBP to pad the response with random
            # bogus suffixes so a passive network observer cannot use
            # the response size to narrow the candidate set.  The
            # responses still parse correctly because every padded line
            # has a count of zero, which the loop below ignores.
            headers={"Add-Padding": "true"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        # Catches timeouts, connection errors, DNS failures, and HTTP
        # error responses (4xx/5xx).  Fail-open with a structured log
        # event so the operator can wire an alert on the
        # ``hibp_check_failed`` event without having to grep
        # WARNING-level lines.
        log_event(
            logger, logging.WARNING, EVT_HIBP_CHECK_FAILED, AUTH,
            "HIBP breached-password check failed; allowing password",
            error_class=type(exc).__name__,
        )
        return

    for line in response.text.splitlines():
        # Each line is "<35-char SHA-1 suffix>:<count>".  A line with no
        # colon is a protocol violation by HIBP rather than a security
        # risk; skip it and continue scanning.  A malicious response
        # that omits the colon would be safer to ignore than to crash
        # the form.
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        record_suffix = parts[0].strip().upper()
        if record_suffix != suffix:
            continue
        # The suffix matches; the count decides whether it is a real
        # breach record.  Honour the Add-Padding protocol requested
        # above: padded lines carry a count of 0 and are bogus noise, so
        # only a positive count is a genuine breach.  A non-integer
        # count is a malformed line -- skip it rather than treat a
        # matching suffix as a confirmed breach.
        try:
            count = int(parts[1].strip())
        except ValueError:
            continue
        if count > 0:
            log_event(
                logger, logging.INFO, EVT_HIBP_CHECK_REJECTED, AUTH,
                "HIBP rejected breached password at hash time",
            )
            raise ValidationError(
                "This password has appeared in a known data breach. "
                "Please choose a different one."
            )

DEFAULT_CATEGORIES = [
    ("Income", "Salary"),
    ("Income", "Other Income"),
    ("Home", "Mortgage/Rent"),
    ("Home", "Electricity"),
    ("Home", "Gas"),
    ("Home", "Water"),
    ("Home", "Internet"),
    ("Home", "Phone"),
    ("Home", "Home Insurance"),
    ("Auto", "Car Payment"),
    ("Auto", "Car Insurance"),
    ("Auto", "Fuel"),
    ("Auto", "Maintenance"),
    ("Family", "Groceries"),
    ("Family", "Dining Out"),
    ("Family", "Spending Money"),
    ("Family", "Subscriptions"),
    ("Health", "Medical"),
    ("Health", "Dental"),
    ("Financial", "Savings Transfer"),
    ("Financial", "Extra Debt Payment"),
    ("Transfers", "Incoming"),
    ("Transfers", "Outgoing"),
    ("Credit Card", "Payback"),
]


def _seed_tax_data_for_user(user_id):
    """Create default federal brackets, FICA, and NC state tax for a new user.

    Fresh-user path: no existence checks (a brand-new user has no tax
    rows).  The per-row construction is shared with the idempotent repair
    script ``scripts/seed_tax_brackets.py`` via the ``build_*`` helpers in
    ``tax_seed_data``; ``FicaConfig`` needs no builder -- its ``**data``
    spread maps the defaults dict to columns directly at both sites.

    Post-T-P5 the state layer is filing-status-aware: one
    :class:`~app.models.tax_config.StateTaxConfig` per filing status (the NC
    standard deduction is status-specific) plus the AGI-tiered NC per-child
    deduction rows (:class:`~app.models.tax_config.StateChildDeduction`).
    """
    filing_statuses = {
        fs.name: fs for fs in db.session.query(FilingStatus).all()
    }
    filing_status_ids = {name: fs.id for name, fs in filing_statuses.items()}

    for tax_year, year_data in DEFAULT_FEDERAL_BRACKETS.items():
        for status_name, data in year_data.items():
            fs = filing_statuses.get(status_name)
            if not fs:
                continue
            bracket_set = build_tax_bracket_set(
                user_id, fs.id, tax_year, status_name, data,
            )
            db.session.add(bracket_set)
            db.session.flush()

            for bracket in build_tax_brackets(bracket_set.id, data["brackets"]):
                db.session.add(bracket)

    for tax_year, data in DEFAULT_FICA.items():
        db.session.add(FicaConfig(user_id=user_id, tax_year=tax_year, **data))

    flat_type_id = ref_cache.tax_type_id(TaxTypeEnum.FLAT)
    if flat_type_id:
        for tax_year, data in DEFAULT_STATE_TAX.items():
            for config in build_state_tax_configs(
                user_id, flat_type_id, tax_year, data, filing_status_ids,
            ):
                db.session.add(config)
        for tax_year, data in DEFAULT_STATE_CHILD_DEDUCTIONS.items():
            for row in build_state_child_deductions(
                user_id, tax_year, data, filing_status_ids,
            ):
                db.session.add(row)


def hash_password(plain_password, rounds=None):
    """Hash a plaintext password using bcrypt, after a HIBP breach check.

    The breach check runs before the bcrypt salt is generated so that a
    rejected password does not pay the bcrypt cost (small win on
    legitimate flow, larger win on automated abuse) and so the
    ``ValidationError`` raised on a breached password reaches the
    caller before any side effects.  When ``HIBP_CHECK_ENABLED`` is
    unset or truthy (production default) the check contacts HIBP via
    its k-anonymity endpoint; see :func:`_check_pwned_password` for
    the protocol and the fail-open semantics on network error.

    Args:
        plain_password: The plaintext password string.
        rounds: Optional bcrypt cost factor (log2 iterations).
            Defaults to bcrypt's built-in default if not specified.

    Returns:
        The bcrypt hash as a string.

    Raises:
        ValidationError: If the password exceeds 72 bytes (bcrypt
            input limit) or if HIBP reports the password has appeared
            in a known breach.
    """
    if len(plain_password.encode("utf-8")) > 72:
        raise ValidationError("Password is too long. Please use 72 characters or fewer.")
    # Breached-password check before bcrypt so a rejected password
    # avoids the (deliberately slow) hash work and the caller sees the
    # validation error before any persistent side effects.  Fail-open
    # on HIBP outage; see :func:`_check_pwned_password`.
    _check_pwned_password(plain_password)
    salt = bcrypt.gensalt(rounds=rounds) if rounds else bcrypt.gensalt()
    return bcrypt.hashpw(
        plain_password.encode("utf-8"), salt
    ).decode("utf-8")


def verify_password(plain_password, password_hash):
    """Verify a plaintext password against a bcrypt hash.

    Defensive hardening (commit C-44 / audit finding F-083): the
    previous guard only short-circuited on ``plain_password is None``,
    so any other falsy-but-not-None value (empty string, ``bytes``,
    ``int``, ``Decimal``, ``list``, ...) reached ``.encode("utf-8")``
    and raised ``AttributeError`` which propagated as a 500.  This
    function now fails closed on every non-string input.

    Specifically:

    * ``plain_password`` must be a non-empty ``str``.  Non-string
      types (``None``, ``bytes``, ``int``, ``Decimal``, sequences,
      mappings) and the empty string both return ``False`` without
      reaching bcrypt.  Every authentication route gates on a
      Marshmallow schema with ``min=1`` length (``LoginSchema``,
      ``ReauthSchema``, ``MfaDisableSchema``, ``ChangePasswordSchema``),
      so an empty plaintext only reaches this function when the
      caller is broken; treating it as a non-match is safer than
      round-tripping bcrypt's empty-string hash.
    * ``password_hash`` must be a non-empty ``str``.  Same rejection
      set as ``plain_password`` -- a non-string or empty hash cannot
      be a valid bcrypt digest, so the lookup never had to reach
      bcrypt anyway.
    * A ``password_hash`` that is a non-empty ``str`` but does not
      parse as a bcrypt hash (corrupted DB row, hash from an older
      incompatible scheme, manual operator typo) raises
      ``ValueError`` ("Invalid salt") inside ``bcrypt.checkpw``.
      The catch below converts it to ``False`` so a corrupted row
      cannot crash the login flow.  Operators observe the corruption
      via the lockout-counter increment in :func:`authenticate` and
      the structured ``account_locked`` log event once the threshold
      trips.

    All failure modes return the same ``False`` value -- "wrong
    type", "empty string", "corrupted hash", and "wrong password"
    are indistinguishable at the call site so an attacker probing
    with exotic payloads cannot use response shape to fingerprint
    caller-side bugs.

    Args:
        plain_password: The plaintext password to check.  Expected
            to be a non-empty ``str``.
        password_hash:  The stored bcrypt hash.  Expected to be a
            non-empty ``str`` containing a parseable bcrypt digest.

    Returns:
        True if the inputs are well-formed and ``bcrypt.checkpw``
        confirms the plaintext matches the hash; False otherwise.
    """
    if not isinstance(plain_password, str) or not plain_password:
        return False
    if not isinstance(password_hash, str) or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except ValueError:
        # bcrypt.checkpw raises ValueError ("Invalid salt") when
        # password_hash is a valid str but not a parseable bcrypt
        # digest.  Fail closed so a corrupted row or a hash from an
        # older incompatible scheme cannot crash the login flow with
        # a 500.  See module docstring above for the audit-trail
        # consequences (lockout counter still increments, surfacing
        # the corruption through the existing forensic path).
        return False


# Timing-equalization dummy hash (deep-hunt #27).  ``authenticate`` runs a
# throwaway bcrypt verification against this hash on the no-user branch so
# an unknown email costs the same wall-clock time as a known email with a
# wrong password.  Without it the no-user branch returns before any bcrypt
# work while the wrong-password branch pays a full ``bcrypt.checkpw``,
# letting an attacker enumerate registered addresses by timing ``/login``
# despite the constant response message.  Generated once at import with the
# same default cost factor as real password hashes (``bcrypt.gensalt()`` in
# :func:`hash_password`), so if bcrypt's default rounds change the
# equalization cost tracks it automatically.  The plaintext is irrelevant --
# only the ``bcrypt.checkpw`` cost matters and the verification result is
# always discarded.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    b"shekel-timing-equalization-placeholder", bcrypt.gensalt()
).decode("utf-8")


def authenticate(email, password):
    """Authenticate a user by email and password, enforcing account lockout.

    Lockout flow (audit finding F-033 / commit C-11):

      * If no user matches ``email`` -- run one throwaway bcrypt
        verification against ``_DUMMY_PASSWORD_HASH`` (timing
        equalization, deep-hunt #27) so this branch costs the same as a
        wrong-password attempt, then raise ``AuthError`` with the same
        generic message used for wrong-password.  This closes the
        response-content AND the response-timing email-enumeration
        oracle.
      * If the user row's ``locked_until`` is set and still in the
        future -- raise ``AuthError`` WITHOUT calling
        :func:`verify_password`.  Skipping the bcrypt step means an
        attacker observing response timing cannot distinguish "locked
        and password is wrong" from "locked and password is right",
        so a lockout window cannot be used as a side-channel oracle.
      * If the password is wrong -- increment ``failed_login_count``.
        At the configured threshold (``LOCKOUT_THRESHOLD``, default
        10) stamp ``locked_until = now + LOCKOUT_DURATION_MINUTES``,
        zero the counter (so a second lockout requires another
        threshold-many failures), emit a structured ``account_locked``
        log event for forensics, and commit.  Always commit the
        increment even when the threshold has not yet tripped so the
        counter survives the transactional boundary.
      * If the password is right but ``is_active`` is False -- raise
        ``AuthError`` with a message that explicitly says the account
        is disabled.  We have already proven knowledge of the
        password, so this branch is not an information leak.
      * If the password is right and the account is active -- reset
        ``failed_login_count`` to 0 and clear ``locked_until`` (a
        successful login implicitly closes any prior lockout
        window).  Commit only when the reset is non-trivial so
        successful logins on accounts with no failure history do not
        pay an unnecessary write.

    All branches share the same generic ``"Invalid email or password."``
    message except for the explicit "Account is disabled." branch
    above.  In particular, the lockout-rejection branch deliberately
    uses the generic message so an attacker probing whether an account
    is locked sees the same response as wrong-password and learns
    nothing.

    Args:
        email:    The user's email address.
        password: The plaintext password.

    Returns:
        The :class:`~app.models.user.User` object on successful auth.

    Raises:
        AuthError: If the email is not found, the account is in an
            active lockout window, the password is wrong, or the
            account is disabled.
    """
    user = db.session.query(User).filter_by(email=email).first()
    if user is None:
        # Timing equalization (deep-hunt #27): pay the same bcrypt cost as
        # the wrong-password branch below so the no-user response is not
        # measurably faster, which would let an attacker enumerate
        # registered emails by timing /login.  verify_password mirrors the
        # real branch's input-validation short-circuits exactly (empty /
        # non-string passwords stay fast on both paths), so timing matches
        # for every input shape.  The result is intentionally discarded.
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise AuthError("Invalid email or password.")

    now = datetime.now(timezone.utc)

    # Lockout gate FIRST so we never run bcrypt on a locked account.
    # See module docstring above for the timing-oracle rationale.
    if user.locked_until is not None and user.locked_until > now:
        raise AuthError("Invalid email or password.")

    if not verify_password(password, user.password_hash):
        # Increment in-place, then decide whether the threshold tripped.
        # Tolerate a defensive ``None`` (a row predating the column or
        # a manual reset) by coalescing to 0; any future schema-level
        # NOT NULL guarantee makes the coalesce a no-op rather than a
        # crash.
        user.failed_login_count = (user.failed_login_count or 0) + 1
        threshold = _get_lockout_threshold()
        if user.failed_login_count >= threshold:
            user.locked_until = now + _get_lockout_duration()
            # Zero the counter once the lockout fires so a second
            # lockout cycle requires another threshold-many failures
            # rather than one extra.  Without the reset, a user whose
            # failures already crossed the threshold would re-trigger
            # a fresh lockout on the very next attempt after the
            # window expires.
            user.failed_login_count = 0
            log_event(
                logger, logging.WARNING, EVT_ACCOUNT_LOCKED, AUTH,
                "Account locked after consecutive failed logins",
                user_id=user.id, lockout_until=user.locked_until.isoformat(),
            )
        db.session.commit()
        raise AuthError("Invalid email or password.")

    if not user.is_active:
        # Past the password gate, so naming the disabled state here is
        # not an information leak: an attacker without the password
        # never reaches this branch.
        raise AuthError("Account is disabled.")

    # Successful auth.  Clear any residual lockout state, but only
    # commit when there is something to clear -- the common path is a
    # user with ``failed_login_count == 0`` and ``locked_until is None``
    # for whom the commit would be a no-op write.
    if user.failed_login_count != 0 or user.locked_until is not None:
        user.failed_login_count = 0
        user.locked_until = None
        db.session.commit()

    return user


def change_password(user, current_password, new_password):
    """Change a user's password after verifying the current one.

    Args:
        user: The User object whose password is being changed.
        current_password: The user's current plaintext password.
        new_password: The new plaintext password (must be >= 12 chars).

    Returns:
        None on success.

    Raises:
        AuthError: If current_password does not match the stored hash.
        ValidationError: If new_password is shorter than 12 characters.
    """
    if not verify_password(current_password, user.password_hash):
        raise AuthError("Current password is incorrect.")
    if len(new_password) < 12:
        raise ValidationError("New password must be at least 12 characters.")
    if len(new_password.encode("utf-8")) > 72:
        raise ValidationError("Password is too long. Please use 72 characters or fewer.")
    user.password_hash = hash_password(new_password)


def register_user(email, password, display_name):
    """Register a new user with default settings and a baseline scenario.

    Creates a User, UserSettings (with model defaults), and a baseline
    Scenario atomically.  Does NOT commit -- the caller is responsible
    for committing the transaction.

    Args:
        email:        The user's email address.
        password:     The plaintext password (must be >= 12 chars).
        display_name: The user's display name.

    Returns:
        The newly created User object (unflushed settings and scenario
        are attached to the same session).

    Raises:
        ValidationError: If the email format is invalid, the display
            name is empty, or the password is too short.
        ConflictError: If a user with the given email already exists.
    """
    # Sanitize inputs.
    email = email.strip().lower()
    display_name = display_name.strip()

    # Validate email format.
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValidationError("Invalid email format.")

    # Validate display name is not empty.
    if not display_name:
        raise ValidationError("Display name is required.")

    # Validate password length.
    if len(password) < 12:
        raise ValidationError("Password must be at least 12 characters.")
    if len(password.encode("utf-8")) > 72:
        raise ValidationError("Password is too long. Please use 72 characters or fewer.")

    # Check email uniqueness.
    if User.query.filter_by(email=email).first():
        raise ConflictError("An account with this email already exists.")

    # Create user.
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    db.session.add(user)
    db.session.flush()

    # Create default settings (model defaults handle values).
    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # Bootstrap pay period (E-19, Commit 3).  The default Checking
    # account's anchor columns are NOT NULL (migration cfb15e782f86),
    # so an account cannot exist without a pay period to point at.
    # The user's actual pay schedule is captured later via
    # /pay-periods/generate, which appends additional periods starting
    # at ``max(period_index) + 1``; this bootstrap takes period_index 0
    # and is preserved so the anchor reference stays valid.  Start
    # date is today and cadence is the project-default 14 days so the
    # grid shows a current-period balance immediately after sign-up.
    #
    # The day is the USER's, not the process's (ruling R-DH (b)).  The
    # origination assertion this period anchors is dated ``display_today()``
    # by ``create_account``, so building the period from ``date.today()``
    # would, in any process not pinned to the display zone, open the schedule
    # on the day AFTER the assertion it exists to hold.
    today = display_today()
    bootstrap_period = PayPeriod(
        user_id=user.id,
        start_date=today,
        end_date=today + timedelta(days=13),
        period_index=0,
    )
    db.session.add(bootstrap_period)
    db.session.flush()

    # Create the baseline scenario BEFORE the first account (Build-Order
    # Step 5): ``account_service.create_account`` posts the new account's
    # opening anchor correction into every scenario, and postings are
    # scenario-scoped -- a baseline-less create has nowhere to post and is
    # loudly skipped.  Creating the baseline first keeps "production users
    # get a baseline at registration" true at the moment it matters.
    scenario = Scenario(user_id=user.id, name="Baseline", is_baseline=True)
    db.session.add(scenario)

    # Default Checking account via the canonical factory (E-19): the
    # service writes both anchor columns + a matching origination
    # AccountAnchorHistory row in one call, so the contract is
    # enforced in exactly one place across every Account-creating
    # path (this service, the /accounts route, scripts, fixtures).
    # Decimal("0.00") is a real value per E-12, not "missing".
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type_id,
            name="Checking",
            anchor_balance=Decimal("0.00"),
            anchor_period_id=bootstrap_period.id,
            # Day one of the period it is anchored to -- the same clock that
            # built the period above, so the row's two statements of "when"
            # agree by construction rather than by both happening to read the
            # same wall clock in the same second.
            observed_on=bootstrap_period.start_date,
            notes="origination (sign-up)",
        ),
    )

    # Create default categories.
    for sort_idx, (group, item) in enumerate(DEFAULT_CATEGORIES):
        db.session.add(Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
            sort_order=sort_idx,
        ))

    # Create default tax configuration (federal brackets, FICA, state).
    _seed_tax_data_for_user(user.id)

    return user
