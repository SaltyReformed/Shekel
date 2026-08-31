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
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
from app.models.ref import FilingStatus
from app.models.scenario import Scenario
from app.models.tax_config import FicaConfig
from app.exceptions import AuthError, ConflictError, ValidationError
from app.services import (
    account_service,
    pay_period_write,
    pay_schedule_service,
)
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


def _reject_impossible_first_payday(
    first_payday: date, cadence_days: int, today: date,
) -> None:
    """Refuse a sign-up payday the owner could not have been LAST paid on.

    Registration asks for the owner's MOST RECENT payday, and the rule is one
    sentence: **the paycheck that payday opens must still be running.**  The
    first period spans ``[first_payday, first_payday + cadence_days - 1]``, so
    the accepted window is ``[today - cadence_days + 1, today]`` -- and that is
    what makes the rest of registration work with no second step.  Sign-up day
    falls INSIDE period 0, so the default account's opening assertion, dated
    today, has a real paycheck to post its correction into rather than reaching
    :meth:`~app.services.pay_calendar.PayCalendar.filing_period`'s clamp.

    Each end refuses for its own reason.  A FUTURE day is not a payday that has
    happened.  A day whose paycheck has already ENDED means a later payday has
    arrived that the owner did not name, and opening the schedule there would
    date period 0 entirely in the past.

    **The lower refusal's message is deliberately about the paycheck, not about
    the owner's arithmetic.**  It read "one of the two does not match the
    other" until an adversarial review of plan step X-ad-a found a user whose
    two answers match perfectly: on payday itself, before the deposit posts,
    "the day money landed" is honestly one cadence ago.  The form's wording was
    the real defect and it was fixed there -- the question now asks for the
    date on the paycheck.  This message stopped accusing them either way.

    Args:
        first_payday: The stated most recent payday.
        cadence_days: The stated days between their paydays.  Already bounded
            by :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`
            before this runs, so the window below cannot be absurdly wide.
        today: The owner's civil today, read once by the caller so this bound
            and the opening assertion's day cannot straddle midnight.

    Raises:
        ValidationError: *first_payday* lies outside the window.  Each message
            names the offending day and the bound it broke.
    """
    if first_payday > today:
        raise ValidationError(
            f"Your most recent payday cannot be {first_payday.isoformat()}: "
            f"that day has not happened yet (today is {today.isoformat()}).  "
            "Enter the date of the paycheck you have already been paid."
        )
    earliest = today - timedelta(days=cadence_days - 1)
    if first_payday < earliest:
        raise ValidationError(
            f"The paycheck starting {first_payday.isoformat()} has already "
            f"ended: paid every {cadence_days} days, it covered through "
            f"{(first_payday + timedelta(days=cadence_days - 1)).isoformat()}."
            f"  Enter the payday whose paycheck covers today -- "
            f"{earliest.isoformat()} or later."
        )


@dataclass(frozen=True)
class RegistrationSpec:
    """Everything one sign-up states: an identity, and a real pay calendar.

    **The pay-calendar half is plan step X-ad-a** (ruling **R-DB**, and the
    cross-arc fork ruled to it on 2026-08-09).  Registration used to FABRICATE
    a pay period covering ``[today, today + 13]`` because
    :func:`app.services.account_service.create_account` refuses an owner with
    no pay period -- and that invented payday is what then blocked the owner's
    real one: the forward-only batch guard of the day
    (``pay_period_service._reject_overlapping_batch``, replaced at plan step
    C3-b by ``pay_period_write._reject_backward_payday``) refused any batch
    starting on or before the latest existing ``end_date``, so ``today + 1``
    through ``today + 13`` were REFUSED outright and every date from
    ``today + 14`` on left a permanent hole in the calendar (finding
    **N-123**).  The remedy is not a better guess: it is to ask.

    Frozen, so a constructed spec is an immutable record of one sign-up
    request, and modelled on
    :class:`app.services.account_service.AccountSpec` for the same reason --
    seven co-loaded values are one concept, not a keyword list.

    **No field carries a default, deliberately.**  ``first_payday`` has no
    defensible one, and giving the other two a default here would put a second
    copy of the app's biweekly premise below the one place that states it
    (``BaseConfig.DEFAULT_PAY_CADENCE_DAYS`` / ``DEFAULT_PAY_PERIOD_HORIZON``,
    applied by the Marshmallow layer and by ``scripts/seed_user.py``).  This
    module reads no Flask config by design -- see the module docstring.
    **That holds for ``history_opens_on`` too, and it is the field where the
    rule is least obvious**: ``None`` is its commonest value and would look
    like a harmless default.  It is now the SAFE reading -- not stated, so
    count only the record -- but a caller still has to reach it deliberately,
    because the field is the difference between an owner who answered and one
    who was never asked.

    Attributes:
        email: The user's email address.  Stripped and lowercased by
            :func:`register_user`, which also re-validates its shape.
        password: The plaintext password (12 characters minimum, 72 UTF-8
            bytes maximum -- bcrypt's hard input cap).
        display_name: The user's display name; stripped, and required to be
            non-empty after stripping.
        first_payday: The day the owner was LAST paid, which opens their
            schedule.  **Last, never next**, and that is the 2026-08-09
            ruling: an answer in ``[today - cadence_days + 1, today]`` has
            nothing to overlap on an empty calendar AND covers sign-up day, so
            the default account's opening balance has a paycheck to post into
            and the owner needs no second step.  A NEXT payday would leave
            sign-up day uncovered, and the opening correction would then reach
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period`'s
            clamp -- which exists for a loan opened years before the owner's
            first payday, not for a balance asserted today.
        cadence_days: Days between the owner's paydays.  Bounded by
            ``ck_pay_schedule_cadence_range``; persisted as the owner's
            schedule so extend and the rolling top-up have a cadence to
            continue from rather than inferring one (pay-calendar finding
            **P8**).
        num_periods: How many periods to generate forward from
            *first_payday*.
        history_opens_on: How far back the owner's paychecks reach, or ``None``
            for NOT STATED, which counts only the recorded paydays (plan step
            **balance:X-bh-2**, ruling **balance:R-IA** as amended
            2026-08-31).  The FLOOR on the
            backward payday rhythm the paycheck engine counts a month position
            and a year-to-date over.  It may not fall after *first_payday*:
            paychecks cannot have begun after the most recent one.  Asked here
            because the app knows the CADENCE and cannot derive when a job
            began, and asked at SIGN-UP because the form is already asking the
            other two halves of the same rhythm.
    """

    email: str
    password: str
    display_name: str
    first_payday: date
    cadence_days: int
    num_periods: int
    history_opens_on: "date | None"


def register_user(spec: RegistrationSpec):
    """Register a new user with default settings, a pay calendar, and a baseline.

    Creates a User, UserSettings (with model defaults), the owner's pay
    periods and schedule row, a baseline Scenario, the default Checking
    account with its opening assertion, the default categories and the default
    tax configuration -- atomically.  Does NOT commit; the caller owns the
    transaction.

    **Every refusal happens before the ``User`` row is added**, which is a
    property worth stating: a validation that fired later would leave a
    partly-built owner in the session, protected only by the fact that no
    caller commits after catching.  The pay-calendar inputs are checked in the
    same block as the email and the password for exactly that reason.

    Args:
        spec: The :class:`RegistrationSpec` for this sign-up.

    Returns:
        The newly created User object (settings, periods, schedule, scenario,
        account, categories and tax rows are attached to the same session).

    Raises:
        ValidationError: The email format is invalid, the display name is
            empty, the password is too short or too long, the cadence falls
            outside ``ck_pay_schedule_cadence_range``, the stated payday is
            not one the owner could have been LAST paid on -- in the future,
            or more than one cadence back -- or the stated pay-history opening
            falls outside ``ck_pay_schedule_history_opens_range`` or after that
            payday.
        ConflictError: A user with the given email already exists.
    """
    # Sanitize inputs.
    email = spec.email.strip().lower()
    display_name = spec.display_name.strip()

    # Validate email format.
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValidationError("Invalid email format.")

    # Validate display name is not empty.
    if not display_name:
        raise ValidationError("Display name is required.")

    # Validate password length.
    if len(spec.password) < 12:
        raise ValidationError("Password must be at least 12 characters.")
    if len(spec.password.encode("utf-8")) > 72:
        raise ValidationError("Password is too long. Please use 72 characters or fewer.")

    # The clock is the USER's, not the process's (ruling R-DH (b)), and it is
    # read ONCE: this same day bounds the stated payday below and dates the
    # opening assertion further down, so the two cannot land on different
    # civil days when a request straddles midnight in the display zone.
    today = display_today()
    # Both write doors' preconditions, asked HERE rather than where they fire,
    # so the claim above is true: the schedule's own bound (what
    # ``budget.pay_schedule`` may store) and the writer's (what
    # ``record_paydays`` can materialise, and how much of it), then this
    # module's own question about the day.  Asking them late would let a bad
    # cadence or a zero horizon refuse several statements after the ``User``
    # row exists, under a message about accounts rather than about the input.
    pay_schedule_service.reject_out_of_range_cadence(spec.cadence_days)
    pay_schedule_service.reject_out_of_range_history_opening(
        spec.history_opens_on,
    )
    pay_period_write.reject_unmaterialisable_batch(
        spec.num_periods, spec.cadence_days,
    )
    _reject_impossible_first_payday(spec.first_payday, spec.cadence_days, today)
    # Against the payday the FORM states rather than the one the schedule will
    # record, which are the same day here and are asked of one shared rule
    # (see that function).  Asking it now is what keeps this block the whole
    # of registration's refusals: ``set_history_opening`` below asks the same
    # question of the recorded schedule, and reaching a refusal there would
    # leave a half-built owner in the session.
    pay_schedule_service.reject_history_opening_after_payday(
        spec.history_opens_on, spec.first_payday,
    )

    # Check email uniqueness.
    if User.query.filter_by(email=email).first():
        raise ConflictError("An account with this email already exists.")

    # Create user.
    user = User(
        email=email,
        password_hash=hash_password(spec.password),
        display_name=display_name,
    )
    db.session.add(user)
    db.session.flush()

    # Create default settings (model defaults handle values).
    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # The owner's REAL pay calendar, from the payday they stated (plan step
    # X-ad-a, ruling R-DB).  It must exist before the default account:
    # ``create_account`` refuses an owner with no pay periods
    # (``_require_pay_period_schedule``) because the opening balance posts a
    # correction, and ``pay_calendar.PayCalendar.filing_period`` raises when
    # there is no materialised period to file it under (finding **N-192**).
    # ``record_paydays`` writes the ``budget.pay_schedule`` row in the same
    # call -- the cadence rule, plan step C3-b -- which is what keeps
    # registration from re-opening pay-calendar finding **P8**, a payday with
    # no schedule row beside it, on every new sign-up.
    pay_period_write.record_paydays(
        user_id=user.id,
        first_payday=spec.first_payday,
        num_periods=spec.num_periods,
        cadence_days=spec.cadence_days,
    )
    # How far back those paychecks reach (plan step balance:X-bh-2, ruling
    # balance:R-IA) -- AFTER the batch, because that call is what creates the
    # ``budget.pay_schedule`` row this writes into (the cadence rule) and this
    # door refuses an owner without one.  It is a separate call rather than an
    # argument to ``record_paydays`` because a batch of paydays does not state
    # when a job began: every extend and regenerate would otherwise have to
    # restate, or deliberately not restate, the owner's answer.  ``None`` is
    # written as ``None``, which is what the column already holds -- the call
    # is unconditional so the field cannot be silently skipped by a future
    # edit that reads it as optional.  A sign-up that leaves the box blank
    # therefore stores an ABSENCE, and the engine counts that owner from their
    # recorded paydays until they say otherwise.
    pay_schedule_service.set_history_opening(user.id, spec.history_opens_on)

    # Create the baseline scenario BEFORE the first account (Build-Order
    # Step 5): ``account_service.create_account`` posts the new account's
    # opening anchor correction into every scenario, and postings are
    # scenario-scoped -- a baseline-less create has nowhere to post and is
    # loudly skipped.  Creating the baseline first keeps "production users
    # get a baseline at registration" true at the moment it matters.
    scenario = Scenario(user_id=user.id, name="Baseline", is_baseline=True)
    db.session.add(scenario)

    # Default Checking account via the canonical factory (E-19): the service
    # writes the Account row and its matching origination
    # AccountAnchorHistory assertion in one call, so the contract is enforced
    # in exactly one place across every Account-creating path (this service,
    # the /accounts route, scripts, fixtures).  It said "both anchor columns"
    # until plan step X-f1e2 -- ruling R-EH deleted those columns two steps
    # earlier and the sentence outlived them.
    # Decimal("0.00") is a real value per E-12, not "missing".
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type_id,
            name="Checking",
            anchor_balance=Decimal("0.00"),
            # SIGN-UP DAY, and at plan step X-ad-a that stopped being the same
            # thing as the schedule's opening payday.  This read
            # ``bootstrap_period.start_date`` while registration fabricated a
            # period starting today; the owner's real first payday is now up to
            # one cadence in the PAST, and a balance of $0.00 is being asserted
            # NOW rather than as of that payday.  It is still the same clock
            # reading -- ``today``, taken once at the top of this function --
            # which is what keeps the assertion and the period bounding it on
            # one civil day.  Two fields have left this call: the explicit
            # ``anchor_period_id`` at plan step X-f1c3c (an assertion carries a
            # DAY) and the ``notes="origination (sign-up)"`` label at X-f1e2
            # (ruling R-ES -- nothing read it, and the registration INSERT is
            # in ``system.audit_log`` either way).
            observed_on=today,
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
