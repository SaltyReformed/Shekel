# 01 -- Identity and Access Findings

## Summary
- Files read (full):
  - `app/routes/auth.py` (522 lines)
  - `app/services/auth_service.py` (422 lines)
  - `app/services/mfa_service.py` (159 lines)
  - `app/models/user.py` (156 lines)
  - `app/utils/auth_helpers.py` (125 lines)
  - `tests/test_integration/test_access_control.py` (1028 lines)
- Additional files read for verification: `app/extensions.py`, `app/config.py`,
  `app/__init__.py` (top and security-headers sections), `requirements.txt`,
  `gunicorn.conf.py`, `scripts/seed_user.py`, `scripts/seed_companion.py`,
  `migrations/versions/b961beb0edf6_add_entry_tracking_and_companion_support.py`,
  relevant portions of `tests/conftest.py` (user fixtures).
- Greps performed: `role_id` across migrations/scripts/tests, `TOTP_ENCRYPTION_KEY`/`FERNET_KEY`
  across whole repo, `random.` in `app/`, direct callers of `auth_helpers.py`, equality
  comparisons against password/backup hashes, `session_protection`/`load_user`, rate-limit
  storage, account lockout keywords.
- Checks performed: 1 Login flow, 2 Registration, 3 Session invalidation, 4 MFA bypass,
  5 Backup code handling, 6 TOTP verify, 7 TOTP secret at rest + key rotation,
  8 `require_owner` role_id fallback, 9 Access-control tests. No checks skipped.
- Finding count: 0 Critical / 2 High / 5 Medium / 3 Low / 4 Info
- Top concern: The TOTP secret and pending-MFA state live in Flask's signed-but-unencrypted
  session cookie, the pending-MFA state has no time cap, and consuming a backup code does
  not invalidate any other sessions -- three MFA-state-management gaps that together weaken
  the "something you have" factor below what a single-user budget app intending public
  release should ship with.

## Findings

### F-A-01: Pending-MFA session state has no time limit
- **Severity:** High
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-613 (Insufficient Session Expiration)
- **Location:** `app/routes/auth.py:100-107`, `app/routes/auth.py:259-303`
- **Evidence:**
  ```python
  if mfa_config:
      # Store pending auth state in session (user is NOT logged in yet).
      flask_session["_mfa_pending_user_id"] = user.id
      flask_session["_mfa_pending_remember"] = remember
      # Validate the next parameter at storage time (defense in depth).
      pending_next = request.args.get("next")
      flask_session["_mfa_pending_next"] = (
          pending_next if _is_safe_redirect(pending_next) else None
      )
      return redirect(url_for("auth.mfa_verify"))
  ```
  and:
  ```python
  pending_user_id = flask_session.get("_mfa_pending_user_id")
  if not pending_user_id:
      return redirect(url_for("auth.login"))
  ```
  The session key is only removed on successful verify, failed secret decrypt, or
  session user-deletion. There is no `flask_session["_mfa_pending_at"] = now()`
  timestamp and no check that compares it against a max age. Nothing in `config.py`
  sets `PERMANENT_SESSION_LIFETIME`, so the default of 31 days applies to the signed
  session cookie that carries this value.
- **Impact:** A victim who types their correct password into a shared or public device,
  then closes the tab without completing MFA, leaves a 31-day window during which any
  later visitor on the same browser profile can send a single correct TOTP code (for
  example from an authenticator app exposed on a sticky note, or a backup code found
  in the trash) and complete the login. Password compromise without device compromise
  is normally mitigated by TOTP; this finding erodes that because password entry
  stays good for a month. CLAUDE.md section 4 of the workflow calls out a 24-hour
  pending-MFA state explicitly as a finding, and the default here is 31 days.
- **Recommendation:** Record a monotonic UTC timestamp in the session when the pending
  state is created (`flask_session["_mfa_pending_at"] = datetime.now(timezone.utc).isoformat()`),
  and at the top of `mfa_verify` reject requests where the stored timestamp is more
  than a small number of minutes old (5 minutes is typical; 15 is the outer bound).
  Clear all three pending keys on rejection and redirect to `/login`.

### F-A-02: Backup-code consumption does not invalidate other sessions
- **Severity:** High
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-613 (Insufficient Session Expiration)
- **Location:** `app/routes/auth.py:310-319`
- **Evidence:**
  ```python
  elif backup_code:
      # Verify the 8-character backup code against stored hashes.
      idx = mfa_service.verify_backup_code(backup_code, mfa_config.backup_codes)
      if idx >= 0:
          # Remove the consumed backup code hash from the list.
          mfa_config.backup_codes = [
              h for i, h in enumerate(mfa_config.backup_codes) if i != idx
          ]
          db.session.commit()
          valid = True
  ```
  The handler commits the consumption, then falls through to the login completion
  block at lines 325-344, which calls `login_user(user, ...)` and sets
  `_session_created_at` on the current session. It does NOT set
  `current_user.session_invalidated_at`, so any previously-issued session cookies
  for this user (including "remember me" cookies persisted on other devices) remain
  valid.
- **Impact:** The canonical reason to use a backup code is that the authenticator
  device has been lost or compromised. In that scenario, any active session on the
  lost device is the attacker's session. Completing a backup-code login should
  immediately invalidate every other session so the legitimate user is the only
  authenticated session. Shekel's current behavior lets the attacker session keep
  running in parallel to the recovery login.
- **Recommendation:** In the backup-code branch (after `db.session.commit()` on
  line 318 and before the login_user call on line 334), set
  `user.session_invalidated_at = datetime.now(timezone.utc)` and commit again,
  exactly as `/change-password` already does on line 217. The immediately-following
  `flask_session["_session_created_at"] = ...` on line 335 will refresh the current
  session so this login survives the invalidation.

### F-A-03: TOTP encryption key has no rotation path
- **Severity:** Medium
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-320 (Key Management Errors)
- **Location:** `app/services/mfa_service.py:18-63`
- **Evidence:**
  ```python
  def get_encryption_key():
      key = os.getenv("TOTP_ENCRYPTION_KEY")
      if not key:
          raise RuntimeError("TOTP_ENCRYPTION_KEY environment variable is not set.")
      return Fernet(key)

  def encrypt_secret(plaintext_secret):
      return get_encryption_key().encrypt(plaintext_secret.encode("utf-8"))

  def decrypt_secret(encrypted_secret):
      return get_encryption_key().decrypt(encrypted_secret).decode("utf-8")
  ```
  The service instantiates a single-key `Fernet(key)` on every call. It never uses
  `MultiFernet`, it never records a version tag on the ciphertext, and nothing in
  `app/services/` or `scripts/` provides a re-wrap migration that reads with one
  key and writes with another. `docs/runbook.md:356` and `docs/runbook_secrets.md:33`
  document "rotating TOTP_ENCRYPTION_KEY" as a manual step whose only remediation
  path is "users must re-enroll MFA" (`docs/runbook_secrets.md:11`: "DESTRUCTIVE if
  changed: all MFA configurations become unreadable; users must re-enroll MFA").
- **Impact:** If the Fernet key is suspected compromised (leaked `.env`, leaked
  backup of the host, stolen keepass entry), the ONLY remediation path is to
  generate a new key and force every user to re-enroll TOTP (which also requires
  them to regenerate backup codes). For a solo-user app today, this is a manageable
  operational event; when the app goes public it becomes a destructive user-visible
  incident that users will resist, leading to "leave the key in place" as the
  pragmatic choice. The workflow doc section 1C.1 explicitly calls out "no rotation
  story" as a finding.
- **Recommendation:** Switch `get_encryption_key()` to return a `MultiFernet`
  constructed from a primary `TOTP_ENCRYPTION_KEY` plus an optional
  `TOTP_ENCRYPTION_KEY_OLD` read from the environment. `MultiFernet` tries the
  primary key first for encrypt (so new writes use the new key) and tries every
  key for decrypt (so existing ciphertexts under the old key still read). Add a
  one-shot `scripts/rotate_totp_key.py` that iterates `auth.mfa_configs`, decrypts
  each `totp_secret_encrypted` with the multi-key reader, re-encrypts with the
  primary key, and commits. Document the rotation procedure in `docs/runbook.md`
  so the key can be rotated without touching user-facing MFA state.

### F-A-04: MFA setup secret is stored in the client-side Flask session
- **Severity:** Medium
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-922 (Insecure Storage of Sensitive Information)
- **Location:** `app/routes/auth.py:365-366`, `app/routes/auth.py:386`
- **Evidence:**
  ```python
  secret = mfa_service.generate_totp_secret()
  flask_session["_mfa_setup_secret"] = secret
  ```
  and on confirm:
  ```python
  secret = flask_session.pop("_mfa_setup_secret", None)
  ```
  Flask's default session interface is `SecureCookieSessionInterface`: the cookie
  payload is HMAC-signed but NOT encrypted. `app/config.py` has no
  `SESSION_TYPE` override, no Flask-Session extension, and no server-side session
  store. The base32 TOTP secret is therefore embedded in the base64-decodable
  cookie body sitting in the user's browser for the duration of the setup flow.
- **Impact:** During the narrow setup window, anyone who can read the Flask session
  cookie (browser extension, shared-computer cookie theft, XSS -- Shekel has CSP
  but it permits inline styles and external CDNs, see Preliminary Finding #3) can
  decode and recover the plaintext TOTP secret. They can then clone the
  authenticator and maintain persistent access even after the user's password is
  rotated. The victim's only signal is that their authenticator app stopped being
  the sole source of correct codes, which is not a signal most users will notice.
- **Recommendation:** Keep the plaintext TOTP secret server-side for the duration
  of the setup flow. Two acceptable approaches: (a) Write the unconfirmed secret
  to `auth.mfa_configs` with `is_enabled=False` and a
  `pending_secret_encrypted` column, then on `/mfa/confirm` flip `is_enabled=True`
  and promote the pending secret to `totp_secret_encrypted`. (b) Store only an
  opaque server-side session ID keyed into a short-lived table or an in-memory
  cache; look up the plaintext secret by ID during confirm. Option (a) also has
  the benefit that a partially-enrolled user is recoverable on the next login
  attempt without restarting the scan-QR step.

### F-A-05: MFA disable does not invalidate other sessions
- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-613 (Insufficient Session Expiration)
- **Location:** `app/routes/auth.py:472-521`
- **Evidence:**
  ```python
  # Clear all MFA fields.
  mfa_config.totp_secret_encrypted = None
  mfa_config.is_enabled = False
  mfa_config.backup_codes = None
  mfa_config.confirmed_at = None
  db.session.commit()

  log_event(logger, logging.INFO, "mfa_disabled", AUTH,
            "MFA disabled", user_id=current_user.id)
  flash("Two-factor authentication has been disabled.", "success")
  return redirect(url_for("settings.show", section="security"))
  ```
  The disable flow correctly re-authenticates the user (password + TOTP, lines
  483-509), clears the MFA fields, and commits. It does not set
  `session_invalidated_at` and does not touch `_session_created_at`.
- **Impact:** A user who disables MFA because they believe one of their sessions
  was compromised has done nothing to invalidate that compromised session. The
  attacker session keeps the password-only login and now has no TOTP gate. CLAUDE.md
  and the workflow doc both list "MFA state change" as a session-invalidation
  trigger alongside password change.
- **Recommendation:** Immediately after the commit on line 516, add
  `current_user.session_invalidated_at = datetime.now(timezone.utc)` followed by
  a second commit, then refresh the current session with
  `flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()`
  so this session survives the invalidation. Same pattern as `/change-password`.

### F-A-06: No account lockout beyond IP rate-limiting
- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-307 (Improper Restriction of Excessive Authentication Attempts)
- **Location:** `app/routes/auth.py:73-132` (login); whole `app/routes/auth.py` (no
  lockout logic anywhere); `app/services/auth_service.py:294-312` (authenticate).
- **Evidence:**
  The only defense on the login route is the Flask-Limiter decorator on line 74:
  ```python
  @auth_bp.route("/login", methods=["GET", "POST"])
  @limiter.limit("5 per 15 minutes", methods=["POST"])
  def login():
  ```
  which keys on `get_remote_address` (see `app/extensions.py:10`,
  `Limiter(key_func=get_remote_address, ...)`). Grep for
  `failed_login|lockout|account_locked|login_attempts` across `app/` returns zero
  matches. The `User` model has no `failed_login_count`, `locked_until`, or
  similar column. `authenticate()` does not record failed attempts.
- **Impact:** An attacker who rotates source IPs (trivial from any cloud provider
  or a residential proxy network) is not slowed by the 5/15min limit because the
  limit is per-IP, not per-account. Credential stuffing against a known email is
  therefore unthrottled at the application layer. Separately, Flask-Limiter's
  default backend is `memory://` (`app/extensions.py:31`), and gunicorn runs
  `workers=2` (`gunicorn.conf.py:26`), so the documented 5/15min limit is already
  10/15min in practice (already covered as Preliminary Finding #4).
- **Recommendation:** Add a `failed_login_count` and `locked_until` column to
  `auth.users`. In `authenticate()`, on wrong password increment the counter; on
  success reset it. When the counter crosses a threshold (e.g. 10), set
  `locked_until = now + 15 minutes` and have the auth path raise a distinct
  "account locked" error. A per-account counter paired with the existing per-IP
  rate limit is the standard defense and does not depend on Flask-Limiter's
  storage choice. The "single-user app" argument against lockout breaks the
  moment a second real user is enrolled.

### F-A-07: Registration does not use structured audit logging
- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778 (Insufficient Logging)
- **Location:** `app/routes/auth.py:179`
- **Evidence:**
  ```python
  auth_service.register_user(email, password, display_name)
  db.session.commit()
  logger.info("action=user_registered email=%s", email)
  ```
  Every other state-changing auth event in the same file calls `log_event(logger,
  ..., AUTH, ..., user_id=...)` (e.g. lines 112, 128, 194, 220, 244, 336, 425,
  447, 518). Registration uses bare `logger.info` with a freeform string, omits
  the `AUTH` category, omits the structured event name, and omits the new user's
  ID. The audit log pipeline (`app/utils/log_events.py`) relies on `log_event()`
  for structured fields.
- **Impact:** The registration event is technically recorded but will not be
  queryable via the same filters as other auth events. An operator looking for
  "all auth events for user N" will miss the registration row. Downstream alerting
  on auth failures/successes has an inconsistent shape to handle. No data loss
  today, but breaks the "every state-changing route emits `log_event()`" invariant
  that CLAUDE.md relies on.
- **Recommendation:** Replace the bare log with
  `log_event(logger, logging.INFO, "user_registered", AUTH, "User registered",
  user_id=user.id, email=email)`. `register_user()` already returns the user
  object -- capture it on line 177 so the ID is available (currently the return
  value is discarded). This also correctly orders the commit and log so the ID is
  populated on the user.

### F-A-08: `verify_password` silently returns False on non-string passwords
- **Severity:** Low
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-20 (Improper Input Validation)
- **Location:** `app/services/auth_service.py:276-291`
- **Evidence:**
  ```python
  def verify_password(plain_password, password_hash):
      if plain_password is None:
          return False
      return bcrypt.checkpw(
          plain_password.encode("utf-8"),
          password_hash.encode("utf-8"),
      )
  ```
  The `is None` guard catches exactly one case: a caller passing `None`. Any
  other falsy-but-not-None value (empty string, empty bytes, non-ASCII types)
  reaches `.encode("utf-8")`. For an empty string, `bcrypt.checkpw(b"", hash)`
  returns False harmlessly. For a bytes object, `.encode("utf-8")` raises
  `AttributeError`, which propagates as a 500. For a Decimal or int
  (e.g. a typed form mistake), same.
- **Impact:** Minor. Any caller whose input isn't already a `str` produces a 500
  instead of an auth failure. Not exploitable for auth bypass -- the function
  returns False correctly for every realistic wrong-password case. Flagged for
  robustness, because every auth-adjacent helper in a money app should fail
  closed, not fail with an unhandled exception.
- **Recommendation:** Tighten the guard to
  `if not isinstance(plain_password, str) or not plain_password:` and return
  False. The change is trivial and protects against a future caller passing a
  non-string by mistake.

### F-A-09: `_assert_blocked` accepts 302 alongside 404
- **Severity:** Low
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-284 (Improper Access Control)
- **Location:** `tests/test_integration/test_access_control.py:28-41`
- **Evidence:**
  ```python
  def _assert_blocked(response, msg=""):
      """Assert that a response indicates the request was blocked.

      Ownership checks return either 302 (redirect with flash) or
      404 (direct not-found). A 200 means the attacker got access.
      """
      assert response.status_code in (302, 404), (
          f"Expected 302 or 404 but got {response.status_code}. "
          f"User B may have accessed User A's resource. {msg}"
      )
  ```
  CLAUDE.md says "Security response rule: 404 for both 'not found' and 'not
  yours.'" -- i.e. the canonical response to an IDOR attempt is 404, not 302.
  The helper accepts 302 as a pass condition, and 68 of the 69 tests in this
  file route through it. A route that returns 302 is either (a) redirecting an
  unauthenticated user to /login (correct, but that is a different property than
  "blocked by ownership") or (b) a route that has no ownership check and sends a
  flash plus a redirect on the failure, which is a weaker form of protection
  because the 302 tells the attacker the object ID exists. The tests also do not
  assert the redirect destination, so a redirect to an attacker-controlled page
  (open redirect) would pass.
- **Impact:** The test suite cannot distinguish "404 by ownership helper" from
  "302 by some other handler". A regression that downgrades an ownership helper
  from 404 to 302 would silently continue to pass. The "404 for both not-found
  and not-yours" CLAUDE.md rule is not actually enforced by the tests intended
  to enforce it.
- **Recommendation:** Split into two helpers. `_assert_not_found(response)` asserts
  `status_code == 404` and is used for every test that targets an ownership
  helper. A separate `_assert_redirected_to_login(response)` asserts
  `status_code == 302 and response.location.endswith('/login')` and is used only
  for routes that are `@login_required` without `auth_helpers`. Audit each of the
  69 tests and move them to the right helper; any that use 302 against an
  ownership helper are additional findings.

### F-A-10: `_is_safe_redirect` default is unused on the change_password route
- **Severity:** Info
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-601 (URL Redirection to Untrusted Site)
- **Location:** `app/routes/auth.py:201-228`
- **Evidence:** `change_password` always redirects to
  `url_for("settings.show", section="security")` regardless of input. No `next`
  parameter is consulted. This is fine -- no open-redirect surface exists on this
  route. Recorded so a future reader does not wonder why the helper is not called.
- **Impact:** None. Documented for completeness.
- **Recommendation:** None.

### F-A-11: Session fixation defense relies on Flask cookie-session semantics, not on session ID rotation
- **Severity:** Info
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-384 (Session Fixation)
- **Location:** `app/extensions.py:22-25`; `app/__init__.py:59-84`;
  `app/routes/auth.py:110-111`, `334-335`
- **Evidence:**
  ```python
  login_manager = LoginManager()
  login_manager.login_view = "auth.login"
  login_manager.login_message_category = "warning"
  ```
  and:
  ```python
  login_user(user, remember=remember)
  flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
  ```
  No explicit `login_manager.session_protection = "strong"` is set, so Flask-Login
  falls back to the default "basic", which only invalidates an anonymous session
  that changes IP/user-agent. Flask's default `SecureCookieSessionInterface` does
  NOT have a server-side session ID to rotate -- the entire session state lives
  in the signed cookie, and the cookie value changes naturally whenever its
  contents change (as they do on login, because `_user_id` and
  `_session_created_at` are added).
- **Impact:** Practically, Flask's stateless session cookie makes the classic
  session-fixation attack ("attacker sets victim's session ID before login")
  difficult because an attacker cannot force the victim's browser to accept an
  attacker-chosen signed cookie. The cookie ALSO changes content on login, so
  the pre-login cookie is invalidated at the application level. This is
  defense-in-depth by architecture, not by explicit rotation. Flagged so the
  reader does not assume "session ID rotation on login" in a Flask-Login + Flask
  session sense -- it does not happen, and it does not need to, given the
  cookie-only design.
- **Recommendation:** None for the current cookie-session design. If the project
  ever migrates to a server-side session store (Flask-Session with Redis, etc.),
  re-verify that the session ID is rotated on `login_user()` and on MFA pass,
  and consider setting `login_manager.session_protection = "strong"`.

### F-A-12: `require_owner` `role_id` fallback is defense-in-depth for tests only
- **Severity:** Info
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-840 (Business Logic Errors)
- **Location:** `app/utils/auth_helpers.py:29-55`; `app/models/user.py:33-38`;
  `migrations/versions/b961beb0edf6_...py:41-45`; `scripts/seed_user.py:94-98`;
  `scripts/seed_companion.py:97-104`; `tests/conftest.py:248-253`, `359-363`,
  `454-458`, `887-892`.
- **Evidence:**
  `auth_helpers.py:51-53`:
  ```python
  owner_id = ref_cache.role_id(RoleEnum.OWNER)
  if getattr(current_user, "role_id", owner_id) != owner_id:
      abort(404)
  ```
  `user.py:33-38`:
  ```python
  role_id = db.Column(
      db.Integer,
      db.ForeignKey("ref.user_roles.id", ondelete="RESTRICT"),
      nullable=False,
      server_default="1",  # 1 = owner
  )
  ```
  Migration `b961beb0edf6:41-45`:
  ```python
  op.execute("""
      ALTER TABLE auth.users
          ADD COLUMN role_id INTEGER NOT NULL DEFAULT 1
              REFERENCES ref.user_roles(id) ON DELETE RESTRICT
  """)
  ```
  Seed scripts: `seed_user.py` (line 94-98) omits `role_id` in the constructor,
  so the server default applies (owner). `seed_companion.py` (line 101) explicitly
  sets `role_id=companion_role_id`. Test fixtures in `tests/conftest.py` all omit
  `role_id`, so they default to owner via the server default.
  Every user-insert path in the repo ends up with `role_id=1` (owner) or
  `role_id=2` (companion, explicit). No path can produce NULL or unset `role_id`.
- **Impact:** The `getattr` fallback on line 52 is therefore defense-in-depth for
  an impossible state. In a real production user row, `current_user.role_id` is
  always populated because the column is NOT NULL with a server default. A Python
  object constructed in a unit test without explicit role_id and then examined in
  the SAME transaction where it has not yet been flushed could plausibly have
  `role_id=None` briefly, which is the only realistic scenario the `getattr`
  covers. Severity is Info because the `require_owner` helper is already
  wrapped with `@login_required`, which means `current_user` is a live DB object
  at that point, not a transient one. Preliminary Finding #2 already concluded
  this was Info pending a grep of insert paths; this finding closes the loop.
- **Recommendation:** None. Leaving the fallback in place is harmless and matches
  the docstring's stated intent. Alternatively, if the developer prefers to fail
  closed, replace line 52 with `if current_user.role_id != owner_id:` so a
  future bug that somehow produces a None role_id raises AttributeError instead
  of silently passing.

### F-A-13: Access control tests cover one HTMX route
- **Severity:** Info
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-284 (Improper Access Control)
- **Location:** `tests/test_integration/test_access_control.py:841`
- **Evidence:** Of the ~69 tests in `test_access_control.py`, exactly one
  (`test_investment_growth_chart_blocked` at line 833) sends
  `headers={"HX-Request": "true"}`. All other tests issue plain HTTP requests.
  HTMX routes in Shekel are distinguished from full-page routes by returning
  partials instead of full templates; some routes branch on the header to
  choose the response template.
- **Impact:** Low in practice because the ownership helpers
  (`get_or_404`, `get_owned_via_parent`, `require_owner`) all run BEFORE any
  response is built, so they do not depend on the Accept or HX-Request header.
  A route that serves a different partial on HX-Request will still hit the
  ownership check first, so IDOR cannot be bypassed via HX-Request. Flagged so
  a future reviewer does not mistakenly believe the HTMX surface is thoroughly
  probed by this file. The DAST IDOR probe in Section 1M of the workflow is
  the intended deeper coverage.
- **Recommendation:** None for this file. Section 1M should ensure the DAST
  probe sets `HX-Request: true` on every HTMX-serving endpoint.

### F-A-14: Backup code entropy is 32 bits, below NIST recommendation
- **Severity:** Info
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-330 (Use of Insufficiently Random Values)
- **Location:** `app/services/mfa_service.py:112-123`
- **Evidence:**
  ```python
  def generate_backup_codes(count=10):
      """Generate a list of single-use backup codes.

      Each code is an 8-character lowercase hex string.
      ...
      """
      return [secrets.token_hex(4) for _ in range(count)]
  ```
  `secrets.token_hex(4)` generates 4 random bytes rendered as 8 hex characters,
  which is 32 bits of entropy per code. Ten such codes give an attacker a
  combined search space of roughly 2^29 per-code guesses if she's willing to
  try all ten on every request (though the rate limit on `/mfa/verify` caps
  that at 5 attempts per 15 minutes per IP, per `app/routes/auth.py:251`). The
  source of randomness is `secrets`, which is correct (the check explicitly
  forbids `random.*`). The hash at rest is bcrypt
  (`app/services/mfa_service.py:137-142`), and comparison is `bcrypt.checkpw`
  which is constant-time. Those three pieces are all correct. Only the
  per-code entropy is below the conventional 64-128 bits.
- **Impact:** Negligible online (rate limit gates attempts). Negligible offline
  unless the bcrypt hashes leak -- then 32 bits per code is brute-forceable in
  minutes with a GPU farm. Flagged for when the app goes public or multi-user
  and the blast radius of a DB leak grows.
- **Recommendation:** Change `secrets.token_hex(4)` to `secrets.token_hex(8)`
  (64 bits, 16 hex chars) or `secrets.token_urlsafe(10)` (80 bits, 14 url-safe
  chars). The code length goes up from 8 to 14-16 characters, which is still
  one line on a printed backup sheet. Existing codes remain valid, but
  regenerated codes after the change use the new length. Update the
  `/mfa/backup_codes` template to widen its display if needed.

## What was checked and found clean

**1. Password hash comparison uses bcrypt.checkpw.** `app/services/auth_service.py:276-291`:
```python
return bcrypt.checkpw(
    plain_password.encode("utf-8"),
    password_hash.encode("utf-8"),
)
```
No `==` comparison on password hashes anywhere in `app/` (grep for
`password_hash.*==` and `==.*password_hash` returns zero matches). The helper
is called from `authenticate()` at line 308, `change_password()` at line 330,
and `/mfa/disable` at `app/routes/auth.py:483`. All three paths are clean.

**2. Backup code comparison uses bcrypt.checkpw.** `app/services/mfa_service.py:155-157`:
```python
for idx, hashed in enumerate(hashed_codes):
    if bcrypt.checkpw(code.encode("utf-8"), hashed.encode("utf-8")):
        return idx
```
`bcrypt.checkpw` is constant-time per code. The loop's iteration order leaks
which index matched on a match, but that index is immediately used to remove
the consumed code from the list (`app/routes/auth.py:315-317`), so no residual
information leak. `hmac.compare_digest` is not required here because
`bcrypt.checkpw` already provides constant-time comparison. No `==` comparison
on backup codes or their hashes anywhere in `app/` (grep returned zero).

**3. TOTP verify uses a safe window.** `app/services/mfa_service.py:96-109`:
```python
return pyotp.TOTP(secret).verify(code, valid_window=1)
```
`valid_window=1` means the previous, current, and next 30-second steps are
accepted -- 90 seconds of total drift tolerance, matching the workflow doc's
explicit standard ("valid_window should be 1"). No replay tracking is needed
because `pyotp.TOTP.verify` itself does not persist state, but see the note in
the open questions section about whether Shekel needs last-used-step tracking
for a multi-user future.

**4. TOTP secret at rest is Fernet-encrypted.** `app/services/mfa_service.py:42-63`
wraps every encrypt/decrypt through `Fernet(key).encrypt(...)` /
`Fernet(key).decrypt(...)`. `app/models/user.py:146` stores
`totp_secret_encrypted = db.Column(db.LargeBinary)`. Plaintext secrets never
touch the database. The `decrypt` call in `app/routes/auth.py:291-303` is
wrapped in a try/except `InvalidToken` so a wrong key produces a clean user
error instead of a 500. (But see F-A-03 for the key rotation gap.)

**5. TOTP_ENCRYPTION_KEY is never logged.** Grep for the env var name across
`app/` and `scripts/` turns up only config loading
(`app/config.py:25`: `TOTP_ENCRYPTION_KEY = os.getenv("TOTP_ENCRYPTION_KEY")`),
the service helper (`app/services/mfa_service.py:27-30`), and a presence check
with a warning about whether the NAME is set
(`app/__init__.py:43-47`: `app.logger.warning("TOTP_ENCRYPTION_KEY is not set...")`).
No `print()` or f-string embeds the value. No defaulted value in `config.py`.

**6. No `random` imports in auth-adjacent code.** Grep `import random|from random`
under `app/` returns zero. Backup codes use `secrets.token_hex`, TOTP secrets use
`pyotp.random_base32` (which uses `os.urandom` internally).

**7. Open-redirect helper rejects the classic bypass set.** `_is_safe_redirect`
in `app/routes/auth.py:29-70`:
```python
if any(c in stripped for c in ("\n", "\r", "\t")) or stripped.startswith("\\"):
    return False
parsed = urlparse(stripped)
if parsed.scheme or parsed.netloc:
    return False
```
Rejects newline/tab injection, backslash-authority (`\\evil.com`),
protocol-relative URLs (handled via `netloc`), scheme-bearing URLs (`javascript:`,
`data:`, `https://`), and empty/whitespace. The helper is called on
`request.args.get("next")` at both storage time (line 104) and redirect time
(line 330) in the MFA pending flow -- defense in depth.

**8. Email uniqueness is enforced at both layers.** Schema: `app/models/user.py:20`:
```python
email = db.Column(db.String(255), unique=True, nullable=False)
```
Service: `app/services/auth_service.py:379`:
```python
if User.query.filter_by(email=email).first():
    raise ConflictError("An account with this email already exists.")
```
Both are in place. The service check is not concurrency-safe on its own, but
the DB-level `UNIQUE` constraint will still reject a race. No second committed
user with the same email can exist.

**9. Registration enforces password length 12-72.** `app/services/auth_service.py:373-376`:
```python
if len(password) < 12:
    raise ValidationError("Password must be at least 12 characters.")
if len(password.encode("utf-8")) > 72:
    raise ValidationError("Password is too long. Please use 72 characters or fewer.")
```
Upper bound on 72 bytes prevents silent truncation by bcrypt. No complexity or
breach-check enforcement (no zxcvbn, no HIBP). For a solo-owner app this is
acceptable; when the app opens to the public, the absence of breached-password
rejection moves up to Low-to-Medium.

**10. role_id server default is NOT NULL with a server default.**
`app/models/user.py:33-38` and
`migrations/versions/b961beb0edf6_...py:41-45` both set
`NOT NULL DEFAULT 1`. Every insert path in the repo (`scripts/seed_user.py:94-98`,
`scripts/seed_companion.py:97-104`, `tests/conftest.py:248-253/359-363/454-458/887-892`)
either omits the column (so the server default applies) or sets it explicitly.
No path can produce NULL. Preliminary Finding #2 is confirmed resolved.

**11. Session invalidation on password change.** `app/routes/auth.py:214-222`:
```python
auth_service.change_password(current_user, current_password, new_password)
db.session.commit()
# Invalidate all other sessions after password change.
current_user.session_invalidated_at = datetime.now(timezone.utc)
db.session.commit()
flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
```
Combined with the check in `app/__init__.py:76-83`:
```python
if user.session_invalidated_at is not None:
    ...
    if created_dt < user.session_invalidated_at:
        return None
```
This is the standard "invalidate all other sessions" pattern and it works for
password change. The SAME pattern is used in `/invalidate-sessions`
(`app/routes/auth.py:231-247`). F-A-02 and F-A-05 note where this pattern is
missing.

**12. `/logout` uses POST.** `app/routes/auth.py:190-198`:
```python
@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
```
CSRF-protected (Flask-WTF covers all POST endpoints), cannot be triggered by a
cross-site GET.

**13. Rate limiting decorators are present on auth routes.**
- `/login` POST: `@limiter.limit("5 per 15 minutes", methods=["POST"])` at line 74
- `/register` GET: `@limiter.limit("10 per hour")` at line 136
- `/register` POST: `@limiter.limit("3 per hour")` at line 152
- `/mfa/verify` POST: `@limiter.limit("5 per 15 minutes", methods=["POST"])` at line 251

The per-IP nature and memory backend weaken these -- see F-A-06 and
Preliminary Finding #4 -- but the decorators themselves are in place.

**14. Cookie flags are correct in production.** `app/config.py:126-128`:
```python
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
```
Session cookie is Secure, HttpOnly, and SameSite=Lax in ProdConfig. Note that
`REMEMBER_COOKIE_SECURE`, `REMEMBER_COOKIE_HTTPONLY`, and
`REMEMBER_COOKIE_SAMESITE` are NOT explicitly set -- they inherit Flask-Login's
defaults, which match these values for Flask 3.x, but pinning them would make
the cookie policy self-documenting. Not a finding; style note.

**15. Access control test helper assertion message is clear.** The IDOR tests
all route through `_assert_blocked(response, msg=...)`, which includes the
request context in the failure message so a regression explains itself
without diving into the test body. (The helper's severity gap is F-A-09.)

**16. The MFA setup flow requires the secret session key to exist.**
`app/routes/auth.py:386-389`:
```python
secret = flask_session.pop("_mfa_setup_secret", None)
if secret is None:
    flash("MFA setup session expired. Please start again.", "danger")
    return redirect(url_for("auth.mfa_setup"))
```
Prevents direct-POST bypass of the scan-QR step. Confirm endpoint cannot run
without first loading `/mfa/setup`.

**17. The MFA verify endpoint aborts on user deletion mid-flow.**
`app/routes/auth.py:270-276`:
```python
user = db.session.get(User, pending_user_id)
if not user:
    # User was deleted between login steps -- clear pending state.
    flask_session.pop("_mfa_pending_user_id", None)
    ...
```
Correct handling of the user-deleted-between-steps race. Similarly the MFA-
disabled-between-steps case at lines 278-288.

**18. No `session["mfa_passed"]=True` tamper path.** The MFA flow does not
use a standalone "passed" flag on the session. The state machine is:
no session key (not logged in) -> `_mfa_pending_user_id` set (password ok,
not logged in) -> `login_user()` called (Flask-Login session). There is no
intermediate "mfa_passed" flag that an attacker could set to bypass TOTP.

**19. Companions cannot skip MFA setup via route-direct navigation.**
`/mfa/setup` and `/mfa/confirm` are `@login_required` only (not
`@require_owner`), so a companion logged in via `/login` (with their own
password + their own MFA if enabled) can set up MFA for themselves. They
cannot interact with any OWNER-only surface because of `@require_owner`
elsewhere. The line-51 fallback in `require_owner` cannot misfire because
companion users always have `role_id=2` (seed path is explicit; see F-A-12).

## Open questions for the developer

1. **Is the workflow-doc "90 seconds of total TOTP drift" the intended tolerance?**
   `valid_window=1` matches the workflow doc's explicit standard. Recording this
   here as a confirmation question only; no change needed unless you want
   `valid_window=0` for stricter clocks.

2. **Should `/mfa/disable` accept a backup code in addition to a TOTP code?**
   A user who lost their authenticator device and has only backup codes cannot
   currently disable MFA. This is a recovery operational gap, not a security
   finding in itself -- but if your recovery story is "use a backup code to
   disable MFA, then re-enroll", the disable route needs to accept the backup
   code path. Note that any change here must also apply the session-invalidation
   fix from F-A-02 and F-A-05.

3. **Are the existing `MfaConfig.backup_codes` JSON mutations safe with the
   default `db.JSON` column type?** The consume path at
   `app/routes/auth.py:315-317` reassigns the list wholesale
   (`mfa_config.backup_codes = [...]`), which triggers SQLAlchemy dirty tracking
   correctly. If a future contributor calls `mfa_config.backup_codes.remove(x)`
   in-place, SQLAlchemy will not detect the mutation because the column type is
   plain `db.JSON`, not `MutableList.as_mutable(db.JSON)`. Consider switching
   to `MutableList.as_mutable(db.JSON)` as a belt-and-braces fix before the
   next MFA-related refactor.

4. **Is F-A-04 (TOTP setup secret in client cookie) actually accepted
   behavior, or was it a "good enough for v1" tradeoff that should be
   re-evaluated?** The fix (server-side storage of the pending secret) is
   medium-complexity because it requires a schema change or a session-store
   swap. Worth discussing the tradeoff explicitly in Phase 2 triage before
   committing to a fix path.

5. **What is the intended session lifetime for unauthenticated state?**
   `PERMANENT_SESSION_LIFETIME` is not set in `app/config.py`, so Flask's
   default (31 days) applies. For the pending-MFA session keys specifically,
   F-A-01 recommends a 5-minute cap at the application layer regardless of
   the session cookie max-age. For general unauthenticated sessions (e.g. CSRF
   tokens on the login form), 31 days is excessive -- a few hours is typical.
   Worth a config decision.
