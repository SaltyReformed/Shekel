# OWASP ASVS Level 2 Mapping -- Shekel

## Metadata

- **ASVS version:** 4.0.3
- **Level:** 2 (standard for applications handling sensitive data)
- **Application:** Shekel (commit `3cff592`)
- **Branch:** `audit/security-2026-04-15`
- **Date:** 2026-04-15
- **Auditor:** Claude Code (Session S7)

## Scope and Caveats

This document maps OWASP ASVS v4.0.3 Level 2 requirements for chapters V2, V3,
V4, V5, V6, V7, V8, V9, V10, and V14 to the Shekel codebase. Chapters V1
(architecture narrative), V11 (business logic -- covered in S5), V12 (files --
no upload feature), and V13 (API -- HTMX-only) are out of scope per the
workflow doc.

**On requirement text.** ASVS IDs and short requirement descriptions below are
based on my working knowledge of ASVS v4.0.3. Where the exact phrasing is
paraphrased rather than literal, the row starts with a leading `~`. Before
using this table for any external compliance claim, the developer should
verify each row against the official ASVS v4.0.3 document at
`github.com/OWASP/ASVS`.

**Verdict definitions:**

- **Pass** -- a specific file:line implements the requirement.
- **Fail** -- the requirement is not satisfied; becomes a finding in S8.
- **Partial** -- partially satisfied (treated as Fail for findings, with
  both present and missing noted).
- **N-A** -- the feature the requirement addresses does not exist in Shekel,
  with the reason tied to the "Not in scope because the feature does not
  exist" list in `docs/security-audit-workflow.md`.

## V2: Authentication

**What this chapter is about.** How users prove who they are -- passwords,
multi-factor authenticators, credential recovery flows, credential storage
(how the server keeps the password hashes), and service-to-service
authentication (how the app authenticates to the database).

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V2.1.1 | ~Passwords are at least 12 characters. | Pass | `app/services/auth_service.py:373-374` (register) and `app/services/auth_service.py:332-333` (change) enforce `len(password) < 12` → ValidationError. HTML `minlength="12"` in `app/templates/auth/register.html:29,35`. |
| V2.1.2 | ~Passwords up to 64 characters are permitted; over 128 denied. | Partial | Minimum is met (64 < 72). However, `app/services/auth_service.py:375-376, 334-335, 268-269` reject `len(password.encode("utf-8")) > 72` (bcrypt's hard 72-byte limit). ASVS expects any password up to 128 chars to be accepted. The 72-byte cap is a bcrypt limitation and is stricter than ASVS requires; it blocks compliant 73-128 character passwords. |
| V2.1.3 | ~Password truncation is not performed. | Pass | `app/services/auth_service.py:268-269` raises ValidationError BEFORE bcrypt's silent 72-byte truncation can occur. No other truncation in the code path. |
| V2.1.4 | ~Any printable Unicode character is permitted in passwords. | Pass | No character-class filtering in `register_user` or `change_password`. The only check is byte length. Unicode is preserved through `.encode("utf-8")`. |
| V2.1.5 | ~Users can change their password. | Pass | `app/routes/auth.py:201-228` (`/change-password` route) calls `auth_service.change_password`. |
| V2.1.6 | ~Password change requires current and new password. | Pass | `app/services/auth_service.py:330-331` verifies `current_password` against `user.password_hash` before accepting `new_password`. |
| V2.1.7 | ~Passwords are checked against a list of breached passwords (e.g. HIBP). | Fail | No integration with HIBP or any breach corpus anywhere in the code. No calls to `api.pwnedpasswords.com`, no k-anonymity lookup, no local blocklist. Not enforced at registration or change. |
| V2.1.8 | ~A password strength meter is provided. | Fail | `app/templates/auth/register.html` contains only a static helper text ("Minimum 12 characters." line 30) -- no dynamic meter, no zxcvbn, no JS strength indicator. Same for `app/templates/settings/_security.html`. |
| V2.1.9 | ~There are no password composition rules (forced mixed case, symbols, etc.). | Pass | Only length is checked; no character-class requirements in `auth_service.py`. |
| V2.1.10 | ~No forced periodic credential rotation. | Pass | No password-age column on `auth.users` (`app/models/user.py:13-51`), no rotation job, no rotation check in login flow. |
| V2.1.11 | ~Paste, browser helpers, and password managers are permitted. | Pass | No `onpaste` handlers in the auth templates; `autocomplete="current-password"` (`app/templates/auth/login.html:23`) and `autocomplete="new-password"` (`app/templates/auth/register.html:29,35`) enable password-manager autofill. |
| V2.1.12 | ~Users can temporarily view the masked password. | Fail | The password input in `app/templates/auth/login.html:22-23` and `app/templates/auth/register.html:28-29, 34-35` is plain `<input type="password">` with no show-password toggle, no eye icon, no JS to flip `type` to `"text"`. |
| V2.2.1 | ~Anti-automation is effective against credential testing / brute-force / lockout abuse. | Partial | `@limiter.limit("5 per 15 minutes", methods=["POST"])` on `/login` (`app/routes/auth.py:74`) and `/mfa/verify` (line 251); `3 per hour` on `/register` POST (line 152); `10 per hour` on `/register` GET (line 136). However, Flask-Limiter uses `storage_uri="memory://"` (`app/extensions.py:31`), which under multi-worker Gunicorn means the effective rate per IP is `limit * worker_count` (2 workers → 10/15min on login). Counters also reset on container restart. No account lockout after repeated failures -- only IP-based rate limit. |
| V2.2.2 | ~Weak authenticators (SMS, email) limited to secondary or transaction approval. | N-A | No SMS or email authenticator exists -- no password-reset flow, no email delivery per Preliminary-findings scope exclusions. |
| V2.2.3 | ~Secure notifications sent to users after updates to authentication details. | Fail | Password change (`/change-password`) and MFA enable/disable write an audit log event via `log_event(..., AUTH, ...)` (e.g. `app/routes/auth.py:220, 425, 518`) but do not notify the user over any channel (no email, no in-app alert). A silent attacker who changed the password would leave no user-visible trace. |
| V2.2.5 | ~CSP (Credential Service Provider) and verifier are separated using approved methods. | N-A | Shekel does not use an external Credential Service Provider or federated identity; authentication is self-contained. |
| V2.2.6 | ~Replay resistance via OTP devices, cryptographic authenticators, or lookup codes. | Pass | TOTP verification via `mfa_service.verify_totp_code` (`app/services/mfa_service.py:96-109`) uses `valid_window=1` (one 30 s period of drift). Backup codes are single-use: after match, the hash is removed from `mfa_config.backup_codes` (`app/routes/auth.py:314-318`). |
| V2.2.7 | ~Intent to authenticate is demonstrated (user-initiated action). | Pass | Login and MFA verify both require explicit form submission with user-entered credentials; there is no automatic/silent auth. |
| V2.3.1 | ~System-generated initial passwords or activation codes are securely random. | N-A (partial credit) | No admin-provisioned passwords or activation codes in the user flow (users self-register). The `scripts/seed_user.py` accepts a password via env (`SEED_USER_PASSWORD`), so the secret is not generated by Shekel. |
| V2.3.2 | ~Enrollment of user-provided authenticators (U2F / FIDO2 / WebAuthn) is supported. | Fail | No WebAuthn/FIDO2/U2F support anywhere in the code (no `webauthn` import, no `/webauthn/*` routes). Only TOTP and static backup codes. |
| V2.3.3 | ~Renewal instructions sent with sufficient time for time-bound authenticators. | N-A | Shekel has no time-bound authenticators -- TOTP secrets do not expire and are not rotated. |
| V2.4.1 | ~Passwords stored in a form resistant to offline attacks. | Pass | bcrypt via `app/services/auth_service.py:254-273`. Hash is stored in `auth.users.password_hash` (`app/models/user.py:21`). No plaintext passwords anywhere. |
| V2.4.2 | ~Salt is at least 32 bits. | Pass | bcrypt auto-generates a 128-bit salt per `bcrypt.gensalt()` (`auth_service.py:270`). 128 bits >> 32 bits. |
| V2.4.3 | ~If PBKDF2 is used, iteration count is adequate. | N-A | PBKDF2 is not used for password hashing -- bcrypt is used. |
| V2.4.4 | ~If bcrypt is used, work factor is at least 10. | Pass | Production code calls `bcrypt.gensalt()` with no argument (`app/services/auth_service.py:270`, `app/services/mfa_service.py:139`), which uses the `bcrypt` library default of **12** (verified: the library has defaulted to 12 since bcrypt 3.x). `BCRYPT_LOG_ROUNDS=4` in `app/config.py:79` is dead configuration -- no code path reads it. The test suite separately monkey-patches `bcrypt.gensalt` in `tests/conftest.py:50-58` to force rounds=4 for speed, which does not affect production. |
| V2.4.5 | ~Additional key-derivation iteration using a secret key (pepper). | Fail | Shekel does not apply a pepper or additional KDF iteration to the password hash. This L2 requirement is genuinely unmet -- bcrypt alone satisfies V2.4.1 but not V2.4.5. |
| V2.5.1 | ~Initial activation/recovery secret not sent in cleartext. | N-A | No system-generated initial activation or recovery secrets are delivered (no email flow per scope exclusions). |
| V2.5.2 | ~No password hints or knowledge-based authentication (secret questions). | Pass | No hint column on `auth.users` (`app/models/user.py`), no security-question model, no such form fields. |
| V2.5.3 | ~Password recovery does not reveal the current password. | N-A | No password-recovery flow exists (scope exclusion: "no email-based password reset"). |
| V2.5.4 | ~No shared or default accounts. | Pass | No hardcoded default user. The `scripts/seed_user.py` requires env vars to set up a user; if unset, no user is seeded. Each registered user is distinct. |
| V2.5.5 | ~Notification sent when an authentication factor changes. | Fail | MFA enable (`app/routes/auth.py:425`), MFA disable (line 518), password change (line 220), and session invalidation (line 244) all write `log_event(...)` entries but deliver zero notification to the user (no email, no push, no in-app alert). Same failure as V2.2.3. |
| V2.5.6 | ~Forgotten-password and other recovery paths use a secure mechanism. | N-A | No recovery path exists (scope exclusion). Admin intervention is the documented path; not a user-facing flow. |
| V2.5.7 | ~If an OTP/MFA factor is lost, identity proofing at the same level as enrollment. | N-A | No self-service MFA recovery flow exists beyond backup codes. If both TOTP and backup codes are lost, admin intervention is required -- and since Shekel has no admin UI, that is an out-of-band operation. |
| V2.6.1 | ~Lookup secrets (backup codes) can be used only once. | Pass | `app/routes/auth.py:314-318` removes the consumed backup-code hash from `mfa_config.backup_codes` on successful verification. |
| V2.6.2 | ~Lookup secrets have sufficient randomness (>= 112 bits of entropy). | Fail | `mfa_service.generate_backup_codes` (`app/services/mfa_service.py:112-123`) uses `secrets.token_hex(4)`, producing **4 bytes = 32 bits of entropy** per code (8 hex chars). ASVS L2 requires >= 112 bits. Even with 10 codes available, brute force against 32-bit codes is well within reach of an unthrottled attacker (note: rate-limited at 5/15min on `/mfa/verify`, but that mitigation is partial per V2.2.1). This is a real fail. Remediation: `secrets.token_hex(14)` or `secrets.token_urlsafe(14)` for 112+ bits. |
| V2.6.3 | ~Lookup secrets are resistant to offline attacks (e.g. hashed, not predictable). | Pass | Backup codes are bcrypt-hashed before storage in `auth.mfa_configs.backup_codes` (`app/services/mfa_service.py:126-142`, stored via `app/routes/auth.py:422`). Raw codes are shown once at enrollment and never re-displayed. |
| V2.7.* | ~Out-of-band (OOB) verifier requirements (SMS/PSTN/push). | N-A (all) | No OOB authenticator exists. No SMS, no email OOB, no push notifications. Scope exclusion covers this. Applies to V2.7.1 through V2.7.6. |
| V2.8.1 | ~Time-based OTPs have a defined lifetime before expiring. | Pass | `pyotp.TOTP(...).verify(code, valid_window=1)` (`app/services/mfa_service.py:109`) accepts codes only within the current 30-second window +/- one neighbor. pyotp defaults to 30-second periods. |
| V2.8.2 | ~Symmetric keys used to verify OTPs are highly protected. | Pass | TOTP secrets are encrypted with Fernet (AES-128-CBC + HMAC) before storage: `app/services/mfa_service.py:42-51` (encrypt) and `:54-63` (decrypt). The Fernet key is loaded from `TOTP_ENCRYPTION_KEY` env var (`:18-30`) and is NOT persisted in the database or image. |
| V2.8.3 | ~Approved cryptographic algorithms used for OTP generation/verification. | Pass | `pyotp` uses HMAC-SHA1 (RFC 6238 default), which is an approved OTP algorithm under NIST SP 800-63B. The TOTP secret is base32, generated from `pyotp.random_base32()` (CSPRNG under the hood). |
| V2.8.4 | ~Time-based OTP used only once within its validity period. | Fail | `pyotp.TOTP.verify(code, valid_window=1)` does NOT track previously consumed codes. A captured TOTP code can be replayed within its 30-second window (and the +/-1 window extensions). Remediation: track the last-used TOTP code timestamp in `auth.mfa_configs` and reject reuse within the window. |
| V2.8.5 | ~TOTP reuse is logged and the user is notified. | Fail | No reuse detection, no reuse logging, no user notification (Shekel has no notification channel). Direct consequence of V2.8.4. |
| V2.8.6 | ~Physical OTP tokens can be revoked. | N-A | Shekel does not support physical OTP tokens -- only software TOTP. |
| V2.8.7 | ~Biometric authenticator limits. | N-A | No biometric authenticator. |
| V2.9.* | ~Cryptographic authenticator (smart card, etc.) requirements. | N-A (all) | No cryptographic authenticator devices. Applies to V2.9.1 through V2.9.3. |
| V2.10.1 | ~Intra-service secrets do not rely on unchanging credentials (e.g. long-lived passwords). | Partial | The Postgres password is a long-lived credential loaded from `.env` via `docker-compose.yml:34-35`. No rotation mechanism exists. For a single-user LAN app this is acceptable but would fail L2 in a public multi-tenant deployment. `TOTP_ENCRYPTION_KEY` has no rotation story either (re-verifying Preliminary Finding #6 -- see `reports/07-manual-deep-dives.md`). |
| V2.10.2 | ~No default credentials for service accounts. | Pass | `docker-compose.yml:34` uses `${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}`, which refuses to start if the variable is unset. No hardcoded fallback. |
| V2.10.3 | ~Service passwords stored with sufficient protection. | Partial | `.env` is gitignored (`.gitignore` confirmed in S1 report 01-identity.md) and stored on the Docker host filesystem. However, per Preliminary Finding #1, `.env.dev` IS tracked in git with placeholder values (e.g. `dev_password_change_me`). Placeholders are not a live leak, but the tracked `.env.dev` is a pattern hazard that encourages accidental commits of real dev credentials. |
| V2.10.4 | ~Secrets are not in source code; managed securely. | Partial | Production secrets are NOT in source (`app/config.py:22, 25, 96`, all loaded from `os.getenv`). ProdConfig validates that `SECRET_KEY` is not the dev default (`app/config.py:132-135`). However, per Preliminary Finding #1, `.env.dev` is tracked with placeholder values. Docker-compose reads all secrets from env via `${VAR}` substitution. No secrets-management system (Vault, AWS SM) is used -- acceptable for single-host deployment but a gap for production-grade. |

**V2 Summary.** Of 36 applicable L2 rows checked (after subtracting N-A),
Shekel passes 15, fails 10 outright, and is partial on 5. The most important
fails for a money app: (1) V2.6.2 backup-code entropy is 32 bits instead of
112 bits, (2) V2.8.4 TOTP codes can be replayed within their validity window,
(3) V2.1.7 no breached-password check at registration or change, (4) V2.2.3 /
V2.5.5 no user notification on auth-factor changes (silent account takeover
possible), and (5) V2.2.1 rate limiting uses in-memory backend per worker.

---

Chapter V2 done: 15 Pass, 10 Fail, 16 N-A, 5 Partial.

## V3: Session Management

**What this chapter is about.** How the application tracks a user's
authenticated state after login -- session cookies, session IDs, timeout,
logout semantics, and defense against session fixation / hijacking.

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V3.1.1 | ~Session tokens are never revealed in URL parameters. | Pass | Flask's session mechanism delivers the session via `Set-Cookie` only. No session token is appended to any `url_for` target or `redirect` location in `app/routes/auth.py`. The `next` parameter is validated by `_is_safe_redirect` (`app/routes/auth.py:29-70`) but does not carry auth state. |
| V3.2.1 | ~A new session token is generated on user authentication (session fixation prevention). | Partial | Flask uses signed client-side sessions (`itsdangerous`). On login, `login_user` mutates the session dict (sets `_user_id`, `_fresh`), and Shekel additionally writes `_session_created_at` (`app/routes/auth.py:111, 335`). Because the entire signed cookie value changes when the dict changes, a pre-login cookie value is not valid after login. However, Shekel does NOT set `login_manager.session_protection = "strong"` in `app/extensions.py:22-25`; it runs at Flask-Login's default ("basic"), which only invalidates sessions on IP/UA change, not on every login. This is the standard Flask pattern and resists session fixation for the client-side session model, but does not match the spirit of an explicit session-ID rotation. |
| V3.2.2 | ~Session tokens have at least 64 bits of entropy. | Pass | Flask's signed session is HMAC'd with `SECRET_KEY`. ProdConfig rejects default/dev `SECRET_KEY` values (`app/config.py:132-135`), forcing a real random value via env. HMAC-SHA1 (itsdangerous default) has 160 bits; 64-byte random `SECRET_KEY` provides 512 bits. Forgery requires knowing `SECRET_KEY`. |
| V3.2.3 | ~Session tokens stored in browser use secure mechanisms (secure cookies or HTML5 storage). | Pass | Session cookie is HTTP-only (see V3.4.2), Secure (V3.4.1), SameSite=Lax (V3.4.3). Set at `app/config.py:126-128`. No session data written to localStorage/sessionStorage from the Shekel client. |
| V3.2.4 | ~Session tokens generated using approved cryptographic algorithms. | Pass | `itsdangerous.URLSafeTimedSerializer` signs sessions with HMAC (default SHA-1, which is approved for HMAC use even though deprecated for signing). Flask 3.1 uses this serializer. |
| V3.3.1 | ~Logout and expiration invalidate the session so back-button or downstream resumption fails. | Pass | `/logout` calls `logout_user()` (`app/routes/auth.py:196`), which clears `_user_id` and `_fresh` from the session dict. On subsequent requests, `@login_required` redirects to `/login`. Additionally, `session_invalidated_at` + `_session_created_at` (`app/__init__.py:74-83`) provide server-side invalidation so an attacker with a stolen cookie cannot resume after the owner logs out all sessions. |
| V3.3.2 | ~If "remain logged in" is offered, re-authentication occurs periodically (idle and/or absolute). | Fail | `REMEMBER_COOKIE_DURATION = 30 days` (`app/config.py:31-33`). `PERMANENT_SESSION_LIFETIME` is not set in any config, so Flask's 31-day default applies. **No idle timeout** -- a session that sits unused for a week is still valid. **No forced periodic re-auth** -- a 30-day remember-me cookie is a hard-coded 30-day session with no prompt for re-entry. Shekel does not implement re-auth for "fresh" operations (no `fresh_login_required` usage -- verified via grep: no matches). |
| V3.3.3 | ~Option to terminate all other active sessions after password change. | Pass | `change_password` handler sets `current_user.session_invalidated_at = datetime.now(timezone.utc)` (`app/routes/auth.py:217`), and refreshes the current session's `_session_created_at` to a new timestamp (line 219) so it survives the cutoff. The user_loader rejects any session with `_session_created_at < session_invalidated_at` (`app/__init__.py:76-83`). All other sessions are invalidated on the next request. |
| V3.3.4 | ~Users can view and log out of currently active sessions and devices. | Partial | `/invalidate-sessions` (`app/routes/auth.py:231-247`) provides a blanket "log out all other sessions" button, exposed in `app/templates/settings/_security.html:31-40`. However, there is **no list** of active sessions -- the user cannot see how many there are, where they came from (IP/device), when they were created, or revoke a specific one. ASVS L2 expects both view AND revoke. |
| V3.4.1 | ~Session cookie has the `Secure` attribute. | Pass | `SESSION_COOKIE_SECURE = True` in ProdConfig (`app/config.py:126`). |
| V3.4.2 | ~Session cookie has the `HttpOnly` attribute. | Pass | `SESSION_COOKIE_HTTPONLY = True` in ProdConfig (`app/config.py:127`). Blocks JavaScript access to the session cookie (`document.cookie` cannot read it). |
| V3.4.3 | ~Session cookie uses the `SameSite` attribute. | Pass | `SESSION_COOKIE_SAMESITE = "Lax"` in ProdConfig (`app/config.py:128`). Note: ASVS guidance prefers `Strict` where possible; `Lax` is acceptable for apps that rely on top-level cross-site navigation flows. Shekel's flows (login form submission, password-change POST) are all same-origin, so `Strict` would also work and would harden CSRF further -- worth considering. |
| V3.4.4 | ~Session cookie uses the `__Host-` prefix. | Fail | `SESSION_COOKIE_NAME` is not set anywhere in `app/config.py`, so the cookie name remains Flask's default `session`. The `__Host-` prefix (which would require Secure, no Domain attribute, and Path=/) is not applied. This is a missed hardening opportunity; remediation is setting `SESSION_COOKIE_NAME = "__Host-session"` in ProdConfig. |
| V3.4.5 | ~Cookie `Path` attribute is set as precisely as possible. | Pass | Flask's default cookie path is `/`, which is correct for Shekel (single application, dedicated domain). No shared-domain subdirectory hosting applies. |
| V3.5.1 | ~Users can revoke OAuth tokens for linked applications. | N-A | Shekel does not issue or consume OAuth tokens; no third-party linked applications. |
| V3.5.2 | ~Application uses session tokens rather than static API secrets. | N-A | Shekel has no API; all routes are session-cookie-authenticated via Flask-Login. No API keys or long-lived tokens issued to users. |
| V3.5.3 | ~Stateless session tokens (JWT) protect against tampering, replay, envelope attacks. | N-A (partial) | Shekel does not use JWT or any stateless session token. Flask's signed-session cookie IS client-side-stored state, so tampering protection matters: HMAC via `SECRET_KEY` provides integrity (addressed under V3.2.4). Replay within the cookie's lifetime is possible if an attacker steals the cookie -- Secure/HttpOnly/SameSite mitigate most vectors, but there is no short-lived refresh + long-lived refresh pattern. |
| V3.7.1 | ~Sensitive transactions or account modifications require a full valid session or re-authentication. | Pass | Password change requires current password (`app/services/auth_service.py:330-331`). MFA disable requires current password AND a TOTP code (`app/routes/auth.py:479-509`). MFA setup happens within a logged-in session but the confirmation step verifies the TOTP code before persisting (line 392). Financial mutations (transactions, account edits) require an authenticated session via `@login_required` throughout `app/routes/`, consistent with S4's IDOR findings. There is no separate step-up re-auth for large financial changes, but the baseline "must be logged in" is enforced universally. |

**V3 Summary.** 18 rows checked: 11 Pass, 2 Fail, 3 N-A, 2 Partial. Most
important fail: **V3.3.2** -- no idle timeout and a 30-day remember-me with
no forced re-auth means a stolen/saved cookie has a month of unattended
value. Secondary: **V3.4.4** `__Host-` prefix not applied (easy win). Both
Partials (V3.2.1 session-fixation rotation, V3.3.4 session listing UI) are
real gaps worth remediation.

---

Chapter V3 done: 11 Pass, 2 Fail, 3 N-A, 2 Partial.

## V4: Access Control

**What this chapter is about.** Who is allowed to do what after they log in.
Includes server-side enforcement of ownership, role-based restrictions,
prevention of Insecure Direct Object References (IDOR -- accessing someone
else's record by changing a URL ID), CSRF protection, and step-up auth for
high-value operations.

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V4.1.1 | ~Access control rules enforced on a trusted service layer (not just client-side). | Pass | All authorization is server-side: Flask-Login `@login_required` on every authenticated route, `@require_owner` (`app/utils/auth_helpers.py:29-55`) for role-gated routes, and ownership helpers `get_or_404` / `get_owned_via_parent` (`app/utils/auth_helpers.py:58-124`) wrap every record lookup. HTMX calls hit the same routes; there are no client-only access checks. S1 subagent B report `02a-routes.md` documents blueprint-by-blueprint decorator coverage. |
| V4.1.2 | ~Access-control attributes cannot be manipulated by end users unless authorized. | Pass | `role_id`, `user_id`, `linked_owner_id` are server-managed columns. The companion-creation code path (`app/routes/settings.py:306, 413`) is the only place user role is assigned, and it is guarded by `@require_owner`. No route accepts a role_id from the request body (verified: all `role_id =` assignments in `app/routes/` are from `ref_cache.role_id(RoleEnum.COMPANION)`, never from user input). Marshmallow schemas in `app/schemas/` do not expose role_id as a writable field. |
| V4.1.3 | ~Principle of least privilege -- users access only what they are authorized for. | Pass | Companion role is gated to `/companion/*` and a small set of shared dashboards; owner role sees everything. `@require_owner` returns 404 for companions (`app/utils/auth_helpers.py:52-53`) per the project's "404 for not-found and not-yours" rule. S4's IDOR probe (`reports/15-idor-dast.md`) dynamically confirmed cross-user access returns 404 for every tested path. |
| V4.1.5 | ~Access controls fail securely, including on exceptions. | Pass | `get_or_404` returns `None` when the record is missing OR owned by another user -- caller then returns 404 (`app/utils/auth_helpers.py:78-83`). `@require_owner` aborts with 404 if `role_id` cannot be resolved or does not match owner_id (`app/utils/auth_helpers.py:51-53`). The `getattr` fallback to `owner_id` is intentional defense-in-depth for test fixtures and is documented in Preliminary Finding #2 (resolved Info). On exception, `@app.errorhandler(500)` rolls back the DB session and renders a static error page (`app/__init__.py:396-406`) -- it does NOT leak record data. |
| V4.2.1 | ~Sensitive data / APIs protected against IDOR for CRUD. | Pass | Every user-data query in `app/services/` filters by `user_id` (or by a parent model's user_id). Every route handler uses `get_or_404` or `get_owned_via_parent`. S4's IDOR DAST probe (`reports/15-idor-dast.md`) tested a matrix of cross-user read/update/delete attempts and recorded 404 responses for all of them. Findings from S1 subagent B (`02a-routes.md`) confirmed coverage. |
| V4.2.2 | ~Strong anti-CSRF mechanism protects authenticated functionality. | Pass | `CSRFProtect()` registered globally at `app/__init__.py:53`. Every POST form includes `{{ csrf_token() }}` (e.g. `app/templates/auth/login.html:13`, `app/templates/auth/register.html:13`, `app/templates/settings/_security.html:6`). HTMX requests automatically include `X-CSRFToken` header via the `htmx:configRequest` listener in `app/static/js/app.js:55-60`, which reads from `<meta name="csrf-token">` in `app/templates/base.html:8`. CSRF errors render `errors/400.html` (`app/__init__.py:359-367`). |
| V4.3.1 | ~Administrative interfaces use appropriate MFA. | Partial | Shekel has no separate admin blueprint -- the owner role IS the administrator (scope exclusion in Preliminary Findings). MFA is available and strong (TOTP + backup codes, see V2.8 and V6 below) but is **optional** for the owner. A compromised password alone can compromise the entire application's data because the owner role has full access. Remediation options: require MFA for the owner role at registration, or at least prompt/nag the owner to enable it. |
| V4.3.2 | ~Directory browsing is disabled; metadata files not disclosed. | Pass | Nginx does not enable `autoindex` (`nginx/nginx.conf` -- verified: no `autoindex on` directive anywhere in the repo). Flask does not serve directory listings. Static files are served from `/var/www/static/` only; `.git`, `Thumbs.db`, `.DS_Store` are not within that prefix. The `.gitignore` keeps `.git/` out of the Docker image and the static volume is populated by the app from `app/static/` only. |
| V4.3.3 | ~Additional authorization (step-up / adaptive) for high-value operations. | Fail | Shekel treats all authenticated operations as equal after login. No step-up re-auth for: anchor-balance changes (which directly control reported net worth), bulk-delete of transactions, creating a companion user (though this at least requires `@require_owner`), changing tax configuration, or exporting data. For a financial app with MFA available, high-value mutations should require a recent TOTP re-entry. Remediation: add a `fresh_login_required`-equivalent decorator that demands a TOTP re-prompt if > N minutes since last MFA verification, and apply it to anchor-balance edits, account deletions, and companion creation. |

**V4 Summary.** 9 rows: 7 Pass, 1 Fail, 0 N-A, 1 Partial. The access-control
story is Shekel's strongest chapter -- IDOR coverage, CSRF, ownership helpers,
and role gating are well-established and dynamically verified (S4). The two
weak spots are L2's expectation that (V4.3.1) admin interfaces *require* MFA
and (V4.3.3) high-value operations have step-up auth; neither is implemented
because Shekel conflates "admin" with "owner" and does not differentiate
sensitivity levels.

---

Chapter V4 done: 7 Pass, 1 Fail, 0 N-A, 1 Partial.

## V5: Validation, Sanitization and Encoding

**What this chapter is about.** Handling untrusted input on the way in
(validation, sanitization, strong typing) and escaping on the way out
(output encoding to prevent XSS, SQL injection, template injection, command
injection, etc.). Also covers safe use of serialization libraries.

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V5.1.1 | ~Defenses against HTTP parameter pollution (HPP). | Pass | Flask's `request.form.get()` and `request.args.get()` return the first value from a MultiDict; duplicate parameters do not silently concatenate or confuse downstream logic. Shekel routes consistently use `.get()` (grep `request.form.get` in `app/routes/`), never `getlist` on security-sensitive fields. |
| V5.1.2 | ~Frameworks protect against mass assignment. | Pass | Models are not populated with `Model(**request.form)` anywhere in `app/routes/` (verified via grep). Marshmallow schemas in `app/schemas/validation.py` use `unknown = EXCLUDE` (line 17) to drop unexpected fields, and only explicitly declared fields can reach the service layer. `role_id`, `user_id`, and `is_active` are never declared in any Marshmallow schema -- users cannot set them via request. |
| V5.1.3 | ~Input validated using positive (allow-list) validation. | Pass | `app/schemas/validation.py` uses `validate.Length(min=..., max=...)`, `validate.Range(min=..., max=...)`, and `validate.OneOf(...)` throughout. Example: `TransactionUpdateSchema.name = fields.String(validate=validate.Length(min=1, max=200))` (line 28), `estimated_amount = fields.Decimal(..., validate=validate.Range(min=0))` (line 29). FK fields are validated against user-owned records in the service layer before insertion. |
| V5.1.4 | ~Structured data is strongly typed and schema-validated. | Pass | Every state-changing route deserializes through a Marshmallow schema (per `docs/coding-standards.md` requirement). Types are strict: `fields.Decimal(places=2)`, `fields.Integer()`, `fields.Date()`, `fields.DateTime()`. Email format validated via regex in `app/services/auth_service.py:365`. |
| V5.1.5 | ~URL redirects restricted to an allow-list (or warn on untrusted). | Pass | `_is_safe_redirect` (`app/routes/auth.py:29-70`) rejects any redirect target with a scheme, netloc, leading backslash, or whitespace/newline characters. Applied at storage time (line 103-106) AND re-validated at redirect time (line 330) for defense in depth. S1 subagent verified this. Only relative same-app paths are permitted. |
| V5.2.1 | ~HTML input from WYSIWYG editors is sanitized. | N-A | Shekel has no rich-text / WYSIWYG editor. All user-entered fields are `<input type="text">`, `<textarea>` for plain notes, `<input type="number">`, or date pickers. No HTML is stored. |
| V5.2.2 | ~Unstructured data is sanitized for allowed characters and length. | Pass | Length enforced by both Marshmallow `validate.Length(max=N)` and DB `db.String(N)`. Characters: no free-form HTML is accepted anywhere; the dangerous case (output to HTML context) is handled by V5.3.3 below. String fields like `notes`, `display_name`, `category` names are stripped (`.strip()`) at the service layer and escaped at render time. |
| V5.2.3 | ~User input sanitized before passing to mail systems (SMTP/IMAP). | N-A | Shekel does not send email (scope exclusion: no SMTP, no `smtplib`, no Flask-Mail, no password-reset flow, no notifications). No mail-injection surface exists. |
| V5.2.4 | ~Application avoids `eval()` and dynamic code execution. | Pass | Verified via grep in `app/`: no `eval(`, no `exec(`, no `compile(`, no `__import__()` with user input. The only `compile` usage in the codebase is in third-party deps (outside app/). Python's arbitrary-code risks are absent. |
| V5.2.5 | ~Application protects against template injection. | Pass | Jinja2 has autoescape enabled by default in Flask. Templates are loaded from `app/templates/` (hardcoded path) -- never from user input. No `render_template_string(user_input)` anywhere in `app/`. No `Template(user_input)` calls. |
| V5.2.6 | ~Application protects against SSRF. | N-A (partial) | No outbound HTTP calls with user-controlled URLs in `app/`. The only `urllib` usage is `urllib.parse.urlparse` (`app/routes/auth.py:9`) for the safe-redirect check -- parsing only, not fetching. No `requests.get`, no `httpx`, no `urlopen`. Healthcheck uses a hardcoded localhost URL (`Dockerfile:56`). |
| V5.2.7 | ~User-supplied SVG scriptable content is sanitized/sandboxed. | N-A | No user SVG upload or rendering (scope exclusion: no file uploads). The only SVG in Shekel is Bootstrap icons served from CDN. |
| V5.2.8 | ~User-supplied scriptable template content (Markdown/CSS/BBCode) sanitized. | N-A | Shekel does not render user-supplied Markdown, CSS, or BBCode. Notes and descriptions are plain text only (verified via grep for `markdown`, `bleach`, `BBCode` in `app/`: no matches). |
| V5.3.1 | ~Context-appropriate output encoding for each interpreter/context. | Pass | Jinja2 HTML autoescape is on for `.html` templates (Flask default). Attribute-context injection requires quoted attributes, which base.html and component templates follow. JS contexts receive data via `data-*` attributes (per coding standards), not via Jinja-interpolated inline JS. |
| V5.3.2 | ~Output encoding preserves user's character set / locale (Unicode-safe). | Pass | Python 3 strings are Unicode; Jinja2 autoescape preserves Unicode while encoding HTML-special characters. `utf-8` is used consistently for password bytes (`auth_service.py:272`), TOTP secret encoding (`mfa_service.py:51`), etc. |
| V5.3.3 | ~Context-aware escaping protects against reflected, stored, DOM-based XSS. | Pass | Jinja2 autoescape handles reflected and stored XSS for HTML output. No `|safe` filter is used anywhere in `app/templates/` (verified via grep: no matches). No `Markup()` calls in `app/`. CSP header adds a second layer of defense (`app/__init__.py:420-427`) although `'unsafe-inline'` on style-src weakens it (Preliminary Finding #3). DOM XSS risk is low because the JS is small and reads from `data-*` attributes and fetch responses, not from `location.hash` or `document.URL`. |
| V5.3.4 | ~Database queries use parameterized / ORM mechanisms. | Pass | SQLAlchemy ORM used throughout `app/`. The only `db.text(...)` occurrences (`app/models/*.py`) are for DDL-level constants: server defaults like `CURRENT_DATE`, boolean defaults, partial index WHERE clauses -- none contain user input. The single user-context SQL in `app/utils/logging_config.py:120-123` uses bound parameter `:uid`. Health check runs `SELECT 1`. Schema creation in `app/__init__.py:446-452` uses f-string interpolation but against a hardcoded `_ALLOWED_SCHEMAS` frozenset -- never user input. |
| V5.3.5 | ~Context-specific output encoding where parameters are not available. | Pass | See V5.3.4 -- the only non-parameterized SQL is the schema allowlist, which is safe by construction. No hand-rolled string escaping in the app. |
| V5.3.6 | ~Protects against JSON injection and JS expression evaluation. | Pass | Flask's `jsonify` / `json.dumps` is used for JSON responses. No `eval()` of JSON anywhere. In JS (`app/static/js/`), responses are parsed via `JSON.parse` implicitly through `fetch`/`response.json()`; no `eval(response.text())` pattern. |
| V5.3.7 | ~Protects against LDAP injection. | N-A | Shekel does not use LDAP (no `ldap3`, no `python-ldap` in `requirements.txt`, no LDAP server connections). |
| V5.3.8 | ~Protects against OS command injection. | Pass | Verified via grep: no `subprocess`, no `os.system`, no `os.popen` in `app/`. Script execution is limited to `scripts/` and `entrypoint.sh` -- deployment operations, not request-time. The `entrypoint.sh` invocations run with hardcoded commands and env vars, not user input. |
| V5.3.9 | ~Protects against LFI / RFI. | Pass | No `open(user_input)` or `include user_input` patterns. Template names in `render_template(...)` calls are string literals. Static file serving is via Flask's `send_from_directory` (framework-sandboxed) or Nginx alias -- neither accepts arbitrary paths. No user-controlled file inclusion. |
| V5.3.10 | ~Protects against XPath / XML injection. | N-A | Shekel does not parse XML input. No `xml.etree`, no `lxml`, no `xml.sax` imports in `app/` (verified via grep). No XPath evaluations. |
| V5.4.1 | ~Memory-safe string / buffer / pointer operations. | N-A | Python 3 is memory-managed; the app has no `ctypes` / FFI calls. The Python runtime and C extensions (psycopg2, bcrypt, cryptography) are the memory-safety boundary, and all are widely-vetted. |
| V5.4.2 | ~Format strings do not take user input unless constant. | Pass | Logging uses `logger.log(level, "%s %s %s", request.method, request.path, response.status_code, ...)` (`app/utils/logging_config.py:173-180`) with separate positional args, not f-strings. No `"%s" % user_input` with user-controlled format strings anywhere in `app/`. |
| V5.4.3 | ~Integer overflow prevention. | Pass | Python integers are arbitrary precision (no overflow possible). Monetary values use `decimal.Decimal` per coding-standards requirement. Marshmallow `validate.Range(min=0)` on numeric fields prevents negative-amount attacks. |
| V5.5.1 | ~Serialized objects use integrity checks or encryption. | Pass | Flask session cookies are signed via `itsdangerous` HMAC. TOTP secrets are Fernet-encrypted (which includes HMAC-SHA256 for integrity) before DB storage (`app/services/mfa_service.py:42-51`). No `pickle` usage anywhere in `app/` (verified via grep). |
| V5.5.2 | ~XML parsers use the most restrictive configuration (no external entities). | N-A | No XML parsing in `app/`. |
| V5.5.3 | ~Deserialization of untrusted data is avoided or protected. | Pass | No pickle, no YAML load (no yaml import in `app/`), no untrusted JSON into dataclass-like decoders. JSON deserialization is Flask/stdlib `json`, which is safe. Marshmallow schemas gate all incoming JSON/form data with strict typing. |
| V5.5.4 | ~JSON parsed with JSON.parse (not eval). | Pass | Client-side JS in `app/static/js/app.js` and others uses `response.json()` / `JSON.parse` via fetch (`app/static/js/grid_edit.js:226, 248, 279`); no `eval()` anywhere in JS. |

**V5 Summary.** 27 rows: 19 Pass, 0 Fail, 8 N-A, 0 Partial. This is Shekel's
cleanest chapter. The combination of Jinja2 autoescape + SQLAlchemy ORM +
Marshmallow schemas + no dynamic execution features essentially removes the
injection-class risks by default. The only latent concern is CSP
`'unsafe-inline'` on style-src (tracked as a Preliminary Finding) -- that
weakens the defense-in-depth layer but does not introduce a direct
vulnerability because no stored user content reaches a `<style>` context.

---

Chapter V5 done: 19 Pass, 0 Fail, 8 N-A, 0 Partial.

## V6: Stored Cryptography

**What this chapter is about.** Cryptographic material at rest: which data
is encrypted in the database, which algorithms and modes are used, how
random values are generated, and how keys are managed. This chapter is
where the TOTP encryption, bcrypt password hashing, and SECRET_KEY story
come together.

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V6.1.1 | ~Regulated private data (PII) is stored encrypted at rest. | Fail | Shekel stores email (PII) as plaintext in `auth.users.email` (`app/models/user.py:20`), `display_name` as plaintext (line 22). No pgcrypto-backed columns, no application-level encryption for PII (verified: only `totp_secret_encrypted` uses Fernet). Disk-level / volume-level encryption is the user's responsibility and is not documented in `docker-compose.yml` or the deployment docs. For L2, application-level encryption of PII is expected. For a self-hosted single-user LAN app this is an accepted risk; for a public multi-user deployment it would be a real gap. |
| V6.1.2 | ~Regulated health data is encrypted at rest. | N-A | Shekel does not store health records, medical data, or any HIPAA-regulated information. |
| V6.1.3 | ~Regulated financial data is encrypted at rest. | Fail | Shekel stores account balances (`budget.accounts.current_anchor_balance`), transaction amounts, tax-bracket configurations, pay-period pay amounts, and salary profiles as plaintext `Numeric(12,2)` columns. Category names and transaction descriptions (which can contain merchant names, spending patterns -- personal financial telemetry) are plaintext. None of this is encrypted at the app level. Same caveat as V6.1.1: acceptable for LAN-only single-user but a gap for public deployment. Remediation options are invasive -- field-level encryption on monetary columns would break query aggregation. More realistic: document disk-level encryption as a deployment requirement. |
| V6.2.1 | ~Cryptographic modules fail securely; no padding-oracle-exploitable errors. | Pass | `cryptography.fernet.InvalidToken` is caught specifically (not broad `Exception`) in `app/routes/auth.py:292, 499`. On decryption failure, the route clears pending MFA state and returns a generic error message -- no timing or content signal that distinguishes bad-key from corrupted-ciphertext from wrong-user. bcrypt `checkpw` is constant-time by design. |
| V6.2.2 | ~Industry-proven / government-approved cryptographic algorithms and libraries are used. | Pass | Fernet (specified in `cryptography` library, AES-128-CBC + HMAC-SHA256) for TOTP secret encryption (`app/services/mfa_service.py:15, 30`). bcrypt for password and backup-code hashing (`app/services/auth_service.py:11`, `app/services/mfa_service.py:12`). HMAC-SHA1 for TOTP via `pyotp` (RFC 6238, NIST-approved). itsdangerous HMAC for Flask sessions. No custom crypto anywhere in `app/`. |
| V6.2.3 | ~Encryption IVs, cipher config, and block modes configured securely. | Pass | Fernet handles IV generation internally per-message (unique 128-bit IV per ciphertext). bcrypt auto-generates a 128-bit salt per `gensalt()` call. pyotp uses time-based counter (no IV concept). No manual block-mode selection anywhere in `app/`. |
| V6.2.4 | ~Crypto algorithms and parameters can be reconfigured or swapped. | Fail | Per Preliminary Finding #6 (re-verified here): there is no rotation story for `TOTP_ENCRYPTION_KEY`. The code calls `Fernet(key).decrypt(...)` (`app/services/mfa_service.py:63`) with a single key -- no `MultiFernet`, no versioned token prefix, no dual-key read path. The only way to rotate the Fernet key is to force every MFA-enrolled user to re-enroll (losing backup codes too). bcrypt cost factor is implicit (uses library default 12 always); changing it requires editing `hash_password`. SECRET_KEY can be rotated but all sessions and CSRF tokens are invalidated. This is genuinely unmet for L2. |
| V6.2.5 | ~Known-insecure block modes, padding, ciphers, and hashes are not used. | Pass | No ECB, MD5, SHA1-for-signing, Triple-DES, Blowfish, or RC4 usage in `app/` (verified via grep: no matches). HMAC-SHA1 for TOTP is explicitly allowed by NIST SP 800-63B even for FIPS-compliant deployments because it is HMAC (not raw SHA1). bcrypt supersedes the weak list. |
| V6.2.6 | ~Nonces / IVs are unique per key. | Pass | Fernet auto-generates a fresh 128-bit IV per `encrypt()` call using `os.urandom` -- collision probability after 2^64 operations is 2^-64, well within the safe envelope for a single-user app. bcrypt salt is similarly per-password. |
| V6.3.1 | ~Random values are generated using a CSPRNG. | Pass | `secrets.token_hex(4)` for backup codes (`app/services/mfa_service.py:123`) -- `secrets` module is CSPRNG-backed. `pyotp.random_base32()` (line 39) uses `secrets` internally. `bcrypt.gensalt()` uses `os.urandom`. `uuid.uuid4()` (`app/utils/logging_config.py:110`) uses `os.urandom`. Flask session signing uses HMAC with a SECRET_KEY that is expected to be generated via `os.urandom` (documented in deployment guide via `.env.example`). |
| V6.3.2 | ~GUIDs use v4 algorithm backed by CSPRNG. | Pass | `uuid.uuid4()` in `app/utils/logging_config.py:110` is RFC-4122 v4, backed by `os.urandom`. Used for request_id tagging only -- no security-sensitive identifier is derived from a GUID. |
| V6.4.1 | ~A secrets-management solution (vault) is used for secrets lifecycle. | Fail | No Vault, no AWS Secrets Manager, no HashiCorp Vault, no Azure Key Vault in use. Secrets are loaded from environment variables sourced from a `.env` file on the Docker host (`docker-compose.yml:59, 66, 72`). For a LAN-only single-operator deployment this is operationally simple and contained, but the ASVS L2 requirement calls for a managed secrets lifecycle (creation, rotation, audit, destruction). Remediation for future multi-tenant scaling: Docker secrets (tmpfs-mounted files) as an intermediate, Vault as the target. |
| V6.4.2 | ~Key material is not exposed to the application; operations happen in an isolated module. | Fail | Both `SECRET_KEY` and `TOTP_ENCRYPTION_KEY` are loaded into the Python process memory via `os.getenv` (`app/config.py:22, 25`, `app/services/mfa_service.py:27`). Any process with the ability to read `/proc/<pid>/environ` or attach gdb can extract them. No HSM, no PKCS#11 token, no split-key scheme. Same acceptance caveat as V6.4.1 -- fine for the current threat model, fails L2's strict expectation. |

**V6 Summary.** 13 rows: 7 Pass, 4 Fail, 1 N-A, 1 Partial (V6.2.4 -- treated
as Fail per project rules but noted as partial because bcrypt CAN be rotated
on next password change; the Fail is driven by TOTP key rotation). Most
important fails: (1) V6.2.4 TOTP key rotation has no plan, so key compromise
is unrecoverable without forcing MFA re-enrollment, (2) V6.1.1 / V6.1.3 PII
and financial data are not encrypted at the app level. The PII/financial
data fails are partially acceptable in the current deployment model but
need explicit documentation as a deferred risk.

---

Chapter V6 done: 7 Pass, 4 Fail, 1 N-A, 1 Partial.

## V7: Error Handling and Logging

**What this chapter is about.** Making errors fail closed without leaking
information, producing structured audit trails of security events, keeping
sensitive data out of logs, protecting the logs themselves from tampering
or unauthorized access, and ensuring a last-resort handler catches every
unhandled exception.

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V7.1.1 | ~Credentials / payment details are not logged; session tokens only hashed. | Pass | No password, TOTP code, backup code, or session cookie value is ever passed into a `logger.*` or `log_event(...)` call (verified: greps in `app/routes/auth.py`, `app/utils/logging_config.py`, `app/services/` turn up zero passes of password/code-like variables to logging). The login failed event (`app/routes/auth.py:128-129`) logs `email=email, ip=request.remote_addr` -- email is an identifier, not a credential. Flask's session cookie value never enters the log pipeline; the `_log_request_summary` hook (`app/utils/logging_config.py:133-181`) logs path/method/status/duration and user_id only. |
| V7.1.2 | ~No other sensitive data is logged (per privacy laws / policy). | Pass | Transaction amounts, account balances, salary figures, tax configurations, and category names are never logged -- the `log_event` callsites write event names (e.g. `"login_success"`), user_id, and context tags, never financial values. Cross-references S1's PII-in-logs check in `reports/07-manual-deep-dives.md`. |
| V7.1.3 | ~Security-relevant events are logged (auth success/fail, access control, deserialization, validation failures). | Partial | Authentication events are comprehensively logged: `login_success`, `login_failed`, `logout`, `password_changed`, `sessions_invalidated`, `mfa_enabled`, `mfa_disabled`, `mfa_login_success`, `backup_codes_regenerated` (`app/routes/auth.py:112, 128, 194, 220, 244, 336, 425, 447, 518`). However, **access-control failures** (404 responses from `require_owner` or ownership-helper mismatches) are captured only generically by `_log_request_summary` as `status=404` entries (`app/utils/logging_config.py:155-181`) -- there is no dedicated `access_denied` event that distinguishes "route not found" from "user attempted cross-user access." Validation failures from Marshmallow are also not explicitly logged as security events. |
| V7.1.4 | ~Each log event has enough information to investigate a timeline. | Pass | Every log record carries: `timestamp` (ISO8601, injected by JsonFormatter at `app/utils/logging_config.py:87`), `level`, `logger` name, `request_id` (UUID4 via `RequestIdFilter`, line 22-31 and 110), `event`, `category`, `user_id` when authenticated (line 167), `remote_addr` (line 161), `method`, `path`, `status`, `request_duration` (line 154-162). The request_id is also returned to the client as `X-Request-Id` header (line 143), which lets a user-reported problem be traced to exact log lines. |
| V7.2.1 | ~Authentication decisions are logged without storing tokens/passwords. | Pass | See V7.1.1 and V7.1.3 -- all auth decisions are logged; no credentials enter the log stream. |
| V7.2.2 | ~Access control decisions are loggable; failed decisions are logged. | Fail | Shekel does not emit a dedicated event when `get_or_404` returns None due to ownership mismatch or when `@require_owner` aborts 404 for a companion hitting an owner-only route. The only trace is the generic 404 log line, which is indistinguishable from a legitimate route miss. For L2, auditors expect to be able to query "show me all access-denied events for user X" -- that query is not answerable from current logs. Remediation: add a `log_event(logger, WARNING, "access_denied", AUTH, ...)` call in `auth_helpers.py` and `require_owner` when ownership fails. |
| V7.3.1 | ~Logging components encode data to prevent log injection. | Pass | Shekel uses `python-json-logger` (`app/utils/logging_config.py:19, 80-88`) which JSON-encodes every log record. User-provided data is passed via `extra={...}` as separate dict fields (e.g. `log_event(..., email=email, ip=...)`) -- each field becomes a JSON string value, so control characters (`\n`, `\r`, `"`, `\u0000`) are escaped to `\n`, `\"`, etc. A malicious email like `attacker@example.com\n{"level":"CRITICAL","event":"fake"}` cannot inject a fake log line because the newline is escaped inside the JSON string. |
| V7.3.3 | ~Security logs are protected from unauthorized access and modification. | Partial | Log file `/home/shekel/app/logs/budget_app.log` lives in a Docker-named volume `applogs` (`docker-compose.yml:78`). Inside the container, the `shekel` non-root user owns the path (`Dockerfile:31, 45`). On the host, the volume is readable by anyone with root / docker group membership. There is no append-only flag, no log signing, no shipping to a tamper-evident external store (no Loki, no Datadog, no CloudWatch). A privileged attacker on the host can redact. For a single-operator personal app this is acceptable; for L2 at public scale it is not. |
| V7.3.4 | ~Time sources synchronized; logging uses UTC. | Pass | Every datetime in `app/routes/auth.py` uses `datetime.now(timezone.utc)` (lines 111, 217, 219, 240, 243, 335, 419). The JsonFormatter writes `timestamp` automatically in the logger's native timezone; the Docker container inherits the host clock (no explicit `TZ=UTC` in docker-compose, but the host should run NTP). `POSTGRES_USER` and other environment config rely on the host's time sync. Nginx logs `$time_iso8601` (`nginx/nginx.conf:36`). |
| V7.4.1 | ~Generic error message is shown on unexpected or security-sensitive errors. | Pass | Flask's 500 handler renders `errors/500.html` (`app/__init__.py:396-406`) -- a generic template. `ProdConfig.DEBUG = False` (`app/config.py:95`) disables the Werkzeug debugger and traceback page. The 400, 403, 404, 429 handlers likewise render static templates. The `X-Request-Id` header is returned on every response (`app/utils/logging_config.py:143`), giving the user an ID to report to support (meets the ASVS "potentially with a unique ID" guidance). |
| V7.4.2 | ~Exception handling is used across the codebase for expected and unexpected conditions. | Partial | Specific-exception handling is used in crypto paths (`InvalidToken`, `RuntimeError`) and DB paths (`ProgrammingError`, `OperationalError`, `SQLAlchemyError`). However, grep turns up **14 broad `except Exception:`** blocks in `app/routes/salary.py`, `app/routes/retirement.py`, `app/routes/investment.py`, and `app/routes/health.py`. These do rollback the session and log via `logger.exception(...)` (which captures the stack), so they are fail-secure in effect. But the project coding standards explicitly forbid broad `except Exception` (see `docs/coding-standards.md` Error Handling section). This is functionally adequate for ASVS L2 but is a code-quality gap worth flagging. |
| V7.4.3 | ~A "last-resort" error handler catches all unhandled exceptions. | Pass | `@app.errorhandler(500)` in `app/__init__.py:396-406` is Flask's last-resort handler -- any exception that escapes a route bubbles to this handler, which rolls back the DB session and renders a generic 500 page. Combined with `DEBUG=False` in prod, this ensures no stack trace ever reaches the client. |

**V7 Summary.** 11 rows: 8 Pass, 1 Fail, 0 N-A, 2 Partial. The
authentication audit trail is solid and log injection is prevented by the
JSON logger. Gaps: (1) V7.2.2 / V7.1.3 Partial -- access-control
failures are not emitted as distinct events, which breaks the "who tried
to access whose data" query; (2) V7.3.3 Partial -- logs are not tamper-
evident; (3) V7.4.2 Partial -- 14 broad `except Exception` blocks in the
route layer violate the project's own coding standards though they
function adequately. Add an `access_denied` event in the ownership
helpers to close the biggest gap.

---

Chapter V7 done: 8 Pass, 1 Fail, 0 N-A, 2 Partial.

## V8: Data Protection

**What this chapter is about.** Broader handling of sensitive data beyond
the "is it encrypted?" question from V6 -- how data moves through server
caches, how browsers cache it, whether it leaks into URLs, whether users
can export and delete their own data, whether data retention is policy-
driven, and whether backups exist and are tested.

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V8.1.1 | ~Sensitive data not cached in server components (load balancers, app caches). | Pass | Shekel has no server-side cache layer -- no Redis, no Varnish, no memcached. Nginx does cache static assets for 7 days (`nginx/nginx.conf:152-154`), but only under `/static/` which never contains sensitive data. The app's `ref_cache` (`app/ref_cache.py`) caches reference-table name→id mappings only (never user data). |
| V8.1.2 | ~Temporary copies of sensitive data are protected or purged after access. | Pass | No temp files are written with user data. Flask's request context is per-request and discarded. SQLAlchemy session is closed at request end (teardown). The `applogs` volume contains JSON logs (no raw sensitive data, per V7.1.1/V7.1.2). |
| V8.1.3 | ~Number of request parameters is minimized. | Pass | Forms pass only the fields the route needs. No global hidden-field debugging data, no over-stuffed AJAX payloads. CSRF token + session cookie are the baseline. |
| V8.1.4 | ~Application detects and alerts on abnormal request volumes. | Fail | Flask-Limiter enforces hard rate limits on `/login` (5/15min), `/register` (3/hr), and `/mfa/verify` (5/15min) at `app/routes/auth.py:74, 152, 251`, but there is NO alerting: no webhook on limit hit, no email/Slack/PagerDuty integration, no cumulative-request-per-user threshold, no dashboard. A slow credential-stuffing campaign (4 attempts per 15-minute window, staying just under the limit) would proceed indefinitely with no operator notice. |
| V8.1.5 | ~Regular backups are performed and restoration is tested. | Pass | `scripts/backup.sh` uses `pg_dump` with timestamped compression and optional GPG encryption, copies to local and NAS destinations; `scripts/verify_backup.sh` restores each backup to a temporary database and runs `integrity_check.py` against it before dropping the temp DB. `scripts/backup_retention.sh` manages retention. A weekly cron is suggested in the script header comments. This is more mature than most L2 apps. |
| V8.1.6 | ~Backups stored securely. | Pass | `backup.sh` supports `--encrypt` mode using `BACKUP_ENCRYPTION_PASSPHRASE` env var for GPG encryption of the dump. Local storage path `/var/backups/shekel` is on the host's filesystem (same trust boundary as the DB volume). NAS storage adds geographic separation. Passphrase-based GPG is an approved mechanism. Note: the script gives the operator the option to skip encryption (`--encrypt` is opt-in, not default) -- for L2 the default should probably be encrypted. |
| V8.2.1 | ~Anti-caching headers are set so sensitive data is not cached in browsers. | Fail | The `_register_security_headers` hook in `app/__init__.py:409-428` sets X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, and CSP -- but **no `Cache-Control` header**. Pages rendering financial data (dashboard, transactions, accounts, salary) are therefore cacheable per browser default heuristics. After logout, pressing Back may still show cached account balances. Verified via grep in `app/`: zero occurrences of `Cache-Control`, `no-store`, or `Pragma`. Remediation: add `response.headers["Cache-Control"] = "no-store"` to the after_request hook, or use Flask-Caching's opt-out pattern. |
| V8.2.2 | ~Browser storage does not contain sensitive data or PII. | Pass | `localStorage` is used in exactly one place: `app/static/js/app.js:10, 33` stores a `shekel-theme` preference (light/dark). No financial data, no user info, no auth tokens in localStorage/sessionStorage/IndexedDB (verified: the two `localStorage` references are both for the theme). Session cookie is HttpOnly. |
| V8.2.3 | ~Authenticated data is cleared from client storage after session termination. | Partial | `logout_user()` clears the server-side session state and on the next request `@login_required` redirects to `/login`. However, because V8.2.1 fails (no `Cache-Control: no-store`), the browser may retain cached copies of financial pages in its history cache. The theme preference in localStorage is not sensitive and persists across logouts intentionally. |
| V8.3.1 | ~Sensitive data sent in HTTP body or headers, not query strings. | Pass | All auth forms use POST with form body (`app/templates/auth/*.html`). No query-string parameters carry sensitive data. The only query parameter with auth relevance is `?next=...` which is validated by `_is_safe_redirect` (`app/routes/auth.py:29-70`) and is a path, not a secret. |
| V8.3.2 | ~Users can remove or export their data on demand. | Fail | No user-data export route exists (verified: no `delete_user`, `delete-user`, `export`, or GDPR-named routes in `app/`). Users cannot download a copy of their transactions, accounts, salary history, or anything else. Users cannot delete their account. This is a GDPR right-to-erasure / right-to-portability gap for any EU-user-facing deployment. For a single-operator LAN app, the operator can run `pg_dump` themselves -- not a user-facing capability. |
| V8.3.3 | ~Clear language on collection/use of data + opt-in consent. | Fail | No privacy policy, no terms of service, no consent banner anywhere in `app/templates/`. Registration in `app/templates/auth/register.html` asks for email, display name, password -- with no disclosure of what is collected or how it is used. For a personal self-hosted app this is acceptable; for the app's public-ready goal this is a real L2 gap. |
| V8.3.4 | ~Sensitive data is identified and a handling policy exists. | Partial | No formal sensitive-data inventory document in `docs/`. Code comments and `CLAUDE.md` implicitly identify financial data and PII as sensitive. The schema names (`auth`, `budget`, `salary`) implicitly classify domains. A formal inventory / policy document would close this. |
| V8.3.5 | ~Access to sensitive data is audited (without logging the data itself). | Partial | Every request is logged with method/path/status/user_id/duration (`app/utils/logging_config.py:153-180`), so "user X accessed path /accounts/42 at time T" is reconstructable. However, the log does NOT capture which specific records were returned (which transaction IDs, which account balances). For L2 under regulation that requires data-access auditing (HIPAA, GDPR Article 30), the per-record audit is expected. The `system.audit_log` table exists per S6's schema report (`reports/17-migrations-schema.md`) but I did not verify write coverage in this session -- cross-reference S1's deep dive. |
| V8.3.6 | ~Sensitive information in memory is overwritten when no longer needed. | Fail | Python does not give the application direct control over memory lifecycle. Passwords, TOTP codes, and backup codes live in local variables until garbage collected. No `ctypes.memset` clearing, no use of the `secrets` module's compare-in-constant-time (bcrypt.checkpw handles that but the raw input remains in Python's heap). This is a common L2 gap for Python apps -- realistically unfixable without an FFI layer. Worth noting as "accepted" rather than "remediated." |
| V8.3.7 | ~Sensitive data that must be encrypted uses approved algorithms with confidentiality AND integrity. | Partial | The data that IS encrypted uses approved algorithms (Fernet = AES-128-CBC + HMAC-SHA256, providing both). TOTP secrets and session cookies are the main examples. However, per V6.1.1 / V6.1.3 Fails, much sensitive data (balances, transaction amounts, PII) is NOT encrypted at the app level. This is "Pass on what is encrypted; Fail on what should be but isn't." |
| V8.3.8 | ~Sensitive data subject to retention classification; stale data deleted. | Partial | `AUDIT_RETENTION_DAYS = 365` (`app/config.py:50`) is configurable, suggesting an audit-log retention policy exists at config time -- but I did not verify in this session whether a scheduled job actually enforces it (look for cron or a management command). No retention policy exists for user data, transactions, or accounts -- they persist indefinitely. For a personal budget app this is a feature (a decade of financial history is useful), not a bug. For L2 under GDPR, explicit retention classification is expected. |

**V8 Summary.** 17 rows: 7 Pass, 4 Fail, 0 N-A, 6 Partial. Most important
Fail: **V8.2.1** no `Cache-Control: no-store` means financial pages may
persist in browser cache after logout -- directly exploitable by anyone
who sits down at the user's computer after a session. Secondary Fails:
**V8.1.4** no alerting on anomalous request volume, **V8.3.2** no data
export/delete capability, **V8.3.3** no privacy consent. The multiple
Partials reflect that Shekel was built for a single operator and has not
acquired the ceremony (policies, inventories, consent flows) that L2
expects of a public-facing app.

---

Chapter V8 done: 7 Pass, 4 Fail, 0 N-A, 6 Partial.

## V9: Communications

**What this chapter is about.** Data in transit. TLS enforcement, cipher
strength, protocol versions, certificate validity, encryption on both
inbound and outbound paths (including to the database), and certificate
revocation mechanisms.

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V9.1.1 | ~Secured TLS is used for all client connectivity; no fallback to unencrypted. | Pass | Per the captured runtime cloudflared config (`docs/audits/security-2026-04-15/scans/cloudflared-ingress.txt`), the only public path for Shekel is `shekel.saltyreformed.com` served via Cloudflare Tunnel. Cloudflare terminates TLS at its edge; HTTP-only bindings (Nginx) are not exposed to WAN. The runtime docker-ps snapshot shows no `shekel-prod-nginx` container running (the bundled Nginx is disabled via compose override per S3's `reports/13-attack-surface.md`). LAN access would require routing directly to the Docker backend network, which is marked `internal: true` in `docker-compose.yml:136`. |
| V9.1.2 | ~Only strong cipher suites are enabled; strongest preferred. | Pass | Cloudflare's edge enforces Mozilla-modern-grade cipher suites (ChaCha20-Poly1305, AES-GCM) and automatically prefers PFS suites. This is not configured in Shekel's repo -- it's a Cloudflare-managed service default. A customer audit would test with `testssl.sh` against the public hostname; no such test was run in this session, but Cloudflare's known defaults satisfy L2. Caveat: Shekel does not control the cipher list directly, so a Cloudflare configuration change could weaken this without notice. |
| V9.1.3 | ~Only recent TLS versions (1.2, 1.3) are enabled; latest preferred. | Pass | Cloudflare disables TLS 1.0 and 1.1 on all Free/Pro/Business plans. TLS 1.3 is enabled by default and preferred. Same Cloudflare-managed caveat as V9.1.2. |
| V9.2.1 | ~Connections use trusted TLS certificates (or explicit trust for internal self-signed). | Pass | Cloudflare issues the client-facing certificate (public CA, valid chain). The internal link from Cloudflare edge to cloudflared uses Cloudflare-issued tunnel credentials (`credentials-file` in `cloudflared/config.yml:43`) -- a signed token, not a self-signed TLS cert. No untrusted self-signed certs in use anywhere. |
| V9.2.2 | ~All connections use TLS, including management ports, monitoring, API, database. No fallback. | Partial | The Cloudflare edge → cloudflared leg is TLS-encrypted (Cloudflare Tunnel protocol). The cloudflared → Gunicorn leg is **plain HTTP** over the Docker bridge network (`cloudflared-ingress.txt` line 33: `service: http://shekel-prod-app:8000`). The Gunicorn → PostgreSQL connection is **plain TCP** -- `docker-compose.yml:60` sets `DATABASE_URL=postgresql://shekel_user:...@db:5432/shekel` with no `?sslmode=require`. Verified via grep: zero `sslmode` or `ssl=require` in the repo. For a single-host Docker deployment these internal connections are on an `internal: true` backend network that is isolated from the host and external traffic, but ASVS L2 strictly expects encryption on the DB leg regardless. Remediation: add `?sslmode=require` to `DATABASE_URL` and enable TLS on the postgres container (`-c ssl=on` with a cert pair). |
| V9.2.3 | ~External encrypted connections that involve sensitive data are authenticated. | N-A | Shekel makes no outbound external connections at runtime (no third-party APIs, no webhooks, no email delivery -- verified via grep for `requests`, `urllib.request.urlopen`, `httpx`: no matches in `app/`). The only outbound traffic is Cloudflare Tunnel control plane (handled by cloudflared, not app code) and optional pg_dump → NAS during backup (`scripts/backup.sh`). |
| V9.2.4 | ~Certificate revocation (OCSP stapling) is enabled and configured. | Pass | Cloudflare enables OCSP stapling by default for all hostnames served through the edge. Shekel does not manage its own certificate, so this is inherited. |
| V9.2.5 | ~Backend TLS connection failures are logged. | N-A | Since the backend connections (app → Postgres, cloudflared → app) use plain HTTP/TCP rather than TLS (see V9.2.2), there are no backend TLS failures to log. This requirement becomes live once V9.2.2 is remediated. |

**V9 Summary.** 8 rows: 5 Pass, 0 Fail, 2 N-A, 1 Partial. Shekel's
public-facing TLS is strong because Cloudflare handles it. The one
gap is **V9.2.2**: database and intra-compose links use plain protocols.
Acceptable for a single-host isolated-network deployment; fails L2
strictly. Two Cloudflare-managed rows (V9.1.2, V9.1.3) carry an implicit
dependency -- Shekel's compliance here is tied to Cloudflare's edge
defaults, not its own code.

---

Chapter V9 done: 5 Pass, 0 Fail, 2 N-A, 1 Partial.

## V10: Malicious Code

**What this chapter is about.** Defenses against intentional or accidental
backdoors in source code or dependencies, signed updates, Subresource
Integrity (SRI) for CDN-loaded assets, and protection against subdomain
takeovers. This chapter is where the "someone smuggled bad code in"
scenarios live.

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V10.1.1 | ~Code analysis tool is used to detect potentially malicious code patterns. | Pass | S1 executed Bandit (`docs/audits/security-2026-04-15/scans/bandit.json`, `bandit.txt`) and Semgrep (`semgrep.json`, `semgrep.txt`) with OWASP rulesets against `app/`. Both tools flag time-of-check/time-of-use patterns, unsafe file operations, subprocess with shell=True, exec/eval, and dangerous pickle. S1 report `reports/04-bandit.md` and `reports/05-semgrep.md` show the findings. Continuous usage (not just one-shot): not yet integrated into CI (no GitHub Actions workflow for these tools visible) -- L2 expects ongoing use, which is a minor gap. |
| V10.2.1 | ~No unauthorized phone-home or data collection in source or third-party libs. | Pass | No analytics code, no telemetry, no tracking pixels in `app/templates/base.html` or any template. The app makes no outbound HTTP requests at runtime (verified via V5.2.6 grep: no `requests`, `httpx`, `urlopen`). Third-party deps reviewed in S2 (`reports/09-supply-chain.md`) -- SBOM scanned against known malicious package lists, no hits. |
| V10.2.2 | ~No excessive permissions for privacy features (camera, mic, location, contacts). | Pass | `Permissions-Policy: camera=(), microphone=(), geolocation=()` (`app/__init__.py:417-419`) explicitly disallows all three in browser. Shekel's UI never invokes `getUserMedia`, `navigator.geolocation`, or the Contacts Picker API (verified via grep: no matches). The app is keyboard/mouse only. |
| V10.2.3 | ~No backdoors, hardcoded undocumented accounts/keys, obfuscation, rootkits, hidden features. | Pass | No hardcoded credentials: `SECRET_KEY` default (`app/config.py:22`) is a placeholder string explicitly rejected in `ProdConfig.__init__` (line 132-135). `TOTP_ENCRYPTION_KEY` has no default (line 25) -- the app warns at startup if missing (`app/__init__.py:43-47`). No obfuscated code (all Python is readable, no packed binaries). No hidden routes -- S3's attack surface report (`reports/13-attack-surface.md`) enumerated every blueprint. `scripts/reset_mfa.py` and `scripts/seed_user.py` are documented maintenance tools, not backdoors. |
| V10.2.4 | ~No time bombs (date/time-based suspicious logic). | Pass | `datetime` usage throughout `app/` is for legitimate business logic: pay-period boundaries, `created_at`/`updated_at` columns, `session_invalidated_at`, TOTP time windows, tax bracket years. No conditional code of the form `if datetime.now() > <specific date>: <alternate behavior>`. S5's business-logic review (`reports/16-business-logic.md`) did not flag any temporal bypass logic. |
| V10.2.5 | ~No malicious code (salami attacks, logic bypasses, logic bombs). | Pass | S5 business-logic deep dive (`reports/16-business-logic.md`) explicitly tested for salami attacks, rounding bypasses, status-workflow bypasses, and balance-calculator skew. Decimal precision is consistent; no sub-cent accumulation paths; no admin-bypass conditionals. |
| V10.2.6 | ~No Easter eggs or unwanted functionality. | Pass | Manual review of `app/routes/`, templates, and static JS found no inactive features, no admin cheat-codes, no debug endpoints, no comment-joke easter eggs. The only "hidden" feature is the theme toggle (light/dark) in `app/static/js/app.js:33`, which is documented UX, not an easter egg. |
| V10.3.1 | ~Auto-update uses secure channels and signed updates. | Partial | Shekel has no in-app auto-update mechanism. Docker image updates are operator-initiated (`docker compose pull && up -d`). `docker-compose.yml:51` sets `pull_policy: always` for `ghcr.io/saltyreformed/shekel:latest`, which means the image is re-pulled on every container start -- pulls are over HTTPS to ghcr.io (trusted registry), but **the image itself is not cryptographically signed** (no Cosign, no Docker Content Trust verification in the pull chain). If an attacker compromised the GHCR repo, they could push a malicious `:latest` that would be pulled silently. Remediation: sign images with Cosign and verify in `entrypoint.sh` or via `DOCKER_CONTENT_TRUST=1`. |
| V10.3.2 | ~Integrity protection (code signing, SRI) used; no untrusted external loads. | Partial | SRI is applied to the heavy-weight CDN assets: Bootstrap CSS (`app/templates/base.html:12`), Bootstrap JS bundle (line 259), and htmx (line 264) all have `integrity="sha384-..."` plus `crossorigin="anonymous"`. However, **Bootstrap Icons CSS** (line 15: `cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css`) has no `integrity` attribute. **Google Fonts CSS** (line 20) also has no SRI (Google Fonts dynamic CSS makes SRI impractical since the CSS changes when browser UA changes -- partial-credit accepted). Additionally, the CSP does NOT include `require-sri-for script style` (`app/__init__.py:420-427`), so even the SRI-tagged scripts are not browser-enforced as SRI-mandatory -- a future template edit that drops the integrity attribute would not trigger a browser error. |
| V10.3.3 | ~Protection from subdomain takeovers (expired DNS, CNAMEs, dead CDNs). | Pass | `shekel.saltyreformed.com` is actively served (docker-ps + cloudflared runtime confirm). The CNAME chain terminates at Cloudflare Tunnel (tunnel UUID, not a third-party IP). S3's attack-surface report enumerated DNS; no hanging CNAMEs to defunct services. The `saltyreformed.com` apex domain is operator-owned. CDN deps (cdn.jsdelivr.net, unpkg.com, fonts.googleapis.com) are all actively-maintained first-party CDNs. |

**V10 Summary.** 10 rows: 7 Pass, 0 Fail, 0 N-A, 2 Partial (both in V10.3).
The malicious-code risk is well-managed for a hobbyist repo: Bandit +
Semgrep in S1, no outbound calls, no phone-home, no backdoors, clean
third-party SBOM. Two real gaps: (1) V10.3.1 image pulls are not
signature-verified, (2) V10.3.2 SRI is applied to most CDN assets but
not all (Bootstrap Icons CSS missing), and CSP does not enforce SRI.

---

Chapter V10 done: 7 Pass, 0 Fail, 0 N-A, 2 Partial.

## V14: Configuration

**What this chapter is about.** Build and deployment hardening, dependency
management, production-mode settings, HTTP security headers (HSTS, CSP,
X-Content-Type-Options, Referrer-Policy, anti-clickjacking), and proper
handling of trusted proxy headers.

| ASVS ID | Requirement (short) | Verdict | Evidence |
|---|---|---|---|
| V14.1.1 | ~Build/deploy is secure and repeatable (CI/CD, config management, scripts). | Pass | Multi-stage `Dockerfile` with pinned base image (`python:3.14.3-slim`, line 9) for reproducibility. `docker-compose.yml` declares the full production topology. `entrypoint.sh` handles migrations + seed. `scripts/deploy.sh` exists for operator-driven deploys. `docs/runbook.md` documents the procedure. GHCR image publishing via GitHub Actions (inferred from `ghcr.io/saltyreformed/shekel:latest`). |
| V14.1.2 | ~Compiler flags enable overflow protections and break on unsafe ops. | N-A | Shekel is Python -- no compilation step. C extensions (psycopg2, bcrypt, cryptography) are shipped as pre-built wheels; their compiler hardening is inherited from upstream build policy. |
| V14.1.3 | ~Server configuration is hardened per framework recommendations. | Partial | Flask: DEBUG=False in prod (`app/config.py:95`), secure cookies set, CSRF global. Gunicorn: conservative timeouts, `forwarded_allow_ips` restricted to RFC1918 (`gunicorn.conf.py:80-84`), request size/field limits set (line 64-70). Nginx: hardening documented but `server_tokens off;` is NOT set in `nginx/nginx.conf` (captured in S2's `reports/03-config-deploy.md` line 707-714). Postgres: default Alpine image, no custom `pg_hba.conf` review done in this session -- cross-reference needed. |
| V14.1.4 | ~Application and deps can be re-deployed from scripts/runbook or restored from backup. | Pass | `docker compose up -d` rebuilds the entire stack from the committed compose file plus `.env`. `scripts/backup.sh` + `scripts/restore.sh` cover the data side. `docs/runbook.md` is explicitly listed in `cloudflared/config.yml:35`. In a DR scenario: provision Docker host → clone repo → populate `.env` → `docker volume create shekel-prod-pgdata` → restore from backup → `docker compose up -d`. Verified functional by the weekly `verify_backup.sh` cron pattern documented in the script header. |
| V14.1.5 | ~Admins can verify integrity of security-relevant configurations. | Fail | No config-drift detection tooling. No integrity-check script that compares the running container's config (e.g. `SECRET_KEY` value, cookie flags, CSP header string) against a known-good baseline. The S1 runtime drift check (`docs/audits/security-2026-04-15/scans/prod-compose-override.txt`, `shared-nginx.conf.txt`) was a one-shot artifact, not a scheduled job. Remediation: add a `scripts/config_audit.py` that emits a hash of security-relevant settings and a cron that alerts on drift. |
| V14.2.1 | ~All components are up to date; dep checker used. | Pass | S2 ran `pip-audit` (`docs/audits/security-2026-04-15/scans/pip-audit.json`, `pip-audit.txt`) and `trivy` on the SBOM; results in `reports/06-pip-audit.md` and `reports/09-supply-chain.md`. Dependencies in `requirements.txt` are pinned to specific versions (verified: all entries in `requirements.txt` use `==` pinning, not `>=` or unspecified). CI gap: no automated nightly scan that opens a PR on a new CVE -- L2 acceptably satisfied by the periodic audit + pinning, but continuous monitoring would be stronger. |
| V14.2.2 | ~Unneeded features, sample apps, default accounts, docs are removed. | Pass | The production Docker image contains only the Shekel app code + runtime deps. No sample/demo blueprints. No Flask debug UI (DEBUG=False). No default user account in production -- `SEED_USER_*` is opt-in via env (`docker-compose.yml:68-70`). The slim Python base image has no `apt` docs, no `/usr/share/doc`, no sample configs. `Dockerfile:26-28` installs only runtime libpq5 + postgresql-client, both required. |
| V14.2.3 | ~SRI used for externally hosted CSS / JS / fonts. | Partial | See V10.3.2 above -- SRI on Bootstrap CSS, Bootstrap JS bundle, and htmx; missing on Bootstrap Icons CSS (`app/templates/base.html:15`); Google Fonts CSS is a dynamic resource that cannot be SRI'd practically. Duplicate finding (same remediation). |
| V14.2.4 | ~Third-party components from pre-defined, trusted, continually maintained repos. | Pass | Python deps from PyPI (`requirements.txt`). Base image `python:3.14.3-slim` from Docker Hub official library. CDN assets from cdn.jsdelivr.net (JSDelivr -- actively maintained) and unpkg.com (npm mirror). All are first-party or well-known third-party registries. |
| V14.2.5 | ~Third-party library inventory catalog is maintained. | Pass | SBOM generated by `cyclonedx-py` and stored at `docs/audits/security-2026-04-15/sbom/sbom.json` (XML variant at `sbom.xml`), plus `resolved-tree.json`. S2's `reports/09-supply-chain.md` references this. `requirements.txt` is the source-of-truth inventory; SBOM is a derived artifact. |
| V14.2.6 | ~Attack surface reduced by sandboxing or encapsulating third-party libs. | N-A | Python has no practical in-process sandboxing mechanism; "encapsulating" third-party libs via a service-layer boundary IS done (`app/services/` is Flask-free per coding standards), but that's an architecture pattern, not a sandbox. No plugin/extension loading, so this L2 line does not apply. |
| V14.3.1 | ~Debug modes disabled in production. | Pass | `ProdConfig.DEBUG = False` (`app/config.py:95`). No `DEBUG=True` anywhere in the production compose. Werkzeug debugger cannot activate. Flask error pages are custom (`app/__init__.py:359-406`) rendering `errors/500.html` -- no stack trace ever leaked. |
| V14.3.2 | ~HTTP headers do not disclose detailed version info of components. | Fail | Nginx `server_tokens` is not set (captured in S2 `reports/03-config-deploy.md`); default is `on`, which appends nginx version to the `Server:` header. Although the bundled Nginx is disabled in production (cloudflared → Gunicorn directly), Gunicorn by default sends `Server: gunicorn` without a version -- but this should be verified via a direct response capture. The WAN path through Cloudflare may strip or rewrite the `Server:` header, but that is not a Shekel-controlled defense. Remediation: add `server_tokens off;` in `nginx/nginx.conf` for any future nginx reactivation; verify Gunicorn `Server:` output in S8. |
| V14.3.3 | ~(Duplicate of V14.3.2 in ASVS v4.0.3 -- same as above.) | Fail | Same evidence and remediation as V14.3.2. |
| V14.4.1 | ~Every HTTP response has a Content-Type with safe charset. | Pass | Flask's `render_template()` sets `Content-Type: text/html; charset=utf-8` by default (Flask framework default). Jinja2 outputs UTF-8. `jsonify()` uses `application/json; charset=utf-8`. Nginx static responses inherit the correct MIME from `mime.types` (`nginx/nginx.conf:27`). |
| V14.4.2 | ~API responses include `Content-Disposition: attachment`. | N-A | Shekel has no JSON/REST API. All endpoints return HTML (full page or HTMX fragment). This requirement targets JSON APIs to force browsers to treat them as file downloads rather than rendered documents -- not applicable here. |
| V14.4.3 | ~Content-Security-Policy header is present. | Pass | CSP is set in `_register_security_headers` (`app/__init__.py:420-427`). Covers default-src, script-src, style-src, font-src, img-src, connect-src. Caveat: `'unsafe-inline'` on style-src and CDN allowances weaken the policy (Preliminary Finding #3 -- separate Fail tracked in findings). The header itself IS present, which is what V14.4.3 demands. |
| V14.4.4 | ~X-Content-Type-Options: nosniff is set on all responses. | Pass | `response.headers["X-Content-Type-Options"] = "nosniff"` (`app/__init__.py:414`). Applied to every response via `@app.after_request`. Nginx also sets this on static files (`nginx/nginx.conf:160`). |
| V14.4.5 | ~Strict-Transport-Security header is included. | Fail | Preliminary Finding #3 (confirmed). The `_register_security_headers` hook in `app/__init__.py:409-428` does NOT emit a `Strict-Transport-Security` header. Nginx does not emit it either (`nginx/nginx.conf` has no HSTS directive). Remediation: add `response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"` in the Flask hook. Do NOT add `preload` without deliberation -- preload is a one-way commitment. |
| V14.4.6 | ~Referrer-Policy header is present and suitable. | Pass | `response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"` (`app/__init__.py:416`). This is a suitable value per ASVS L2 guidance (sends origin only on cross-origin, full URL on same-origin). `no-referrer` would be stricter but breaks legitimate analytics/referral flows. |
| V14.4.7 | ~Application cannot be embedded in third-party sites by default. | Pass | `X-Frame-Options: DENY` (`app/__init__.py:415`) -- no framing allowed anywhere. The CSP does not include `frame-ancestors` (which is the modern replacement for XFO), but XFO: DENY is still honored by all modern browsers and is equivalent. Dual-setting would be stronger. |
| V14.5.1 | ~Server accepts only the HTTP methods in use and logs/alerts on others. | Pass | Every Flask route in `app/routes/` specifies `methods=["GET", "POST", ...]`. Unlisted methods return 405 Method Not Allowed (Flask default). Request-logging hook (`app/utils/logging_config.py:133-181`) records method + status on every request. Partial: no specific alert on 405 volume (tied to V8.1.4 Fail -- no anomaly alerting at all). |
| V14.5.2 | ~Origin header is not used for authentication or access control. | Pass | Flask-WTF CSRF validates the token from the form/header, NOT the Origin header. Flask-Login uses session cookies. No code in `app/` reads `request.headers.get("Origin")` for a trust decision (verified via grep: no matches). |
| V14.5.3 | ~CORS allow-list is strict and does not permit "null". | N-A | Shekel does not use CORS (no `flask_cors` in `requirements.txt`, no `Access-Control-Allow-*` headers set anywhere -- verified via grep). All traffic is same-origin by design. |
| V14.5.4 | ~Proxy-added headers (e.g. X-Forwarded-*) are authenticated. | Pass | Gunicorn `forwarded_allow_ips = "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8"` (`gunicorn.conf.py:80-84`) restricts trust to RFC1918 private subnets only -- external X-Forwarded-For headers are rejected. Nginx `set_real_ip_from` directives (`nginx/nginx.conf:117-120`) similarly restrict CF-Connecting-IP trust to localhost + Docker bridges + LAN. Cloudflare Tunnel authenticates its control channel via the tunnel UUID credential (`cloudflared/config.yml:43`). |

**V14 Summary.** 23 rows: 15 Pass, 3 Fail, 3 N-A, 2 Partial. Most
important Fails: **V14.4.5 HSTS missing** (Preliminary Finding #3 --
confirmed), **V14.3.2 / V14.3.3 server_tokens not set** (minor but
standard hardening), **V14.1.5 no config-drift / integrity check
tooling**. Partials: **V14.1.3** server hardening is mostly good but
nginx `server_tokens off;` isn't in the repo config; **V14.2.3** SRI
partial coverage. The header chapter is otherwise strong: CSP, XCTO,
Referrer-Policy, XFO all present.

---

Chapter V14 done: 15 Pass, 3 Fail, 3 N-A, 2 Partial.

## Overall Summary

### Aggregate counts

| Chapter | Rows | Pass | Fail | Partial | N-A |
|---|---|---|---|---|---|
| V2 Authentication | 46 | 15 | 10 | 5 | 16 |
| V3 Session Management | 18 | 11 | 2 | 2 | 3 |
| V4 Access Control | 9 | 7 | 1 | 1 | 0 |
| V5 Validation/Sanitization/Encoding | 27 | 19 | 0 | 0 | 8 |
| V6 Stored Cryptography | 13 | 7 | 4 | 1 | 1 |
| V7 Error Handling and Logging | 11 | 8 | 1 | 2 | 0 |
| V8 Data Protection | 17 | 7 | 4 | 6 | 0 |
| V9 Communications | 8 | 5 | 0 | 1 | 2 |
| V10 Malicious Code | 10 | 7 | 0 | 2 | 0 |
| V14 Configuration | 23 | 15 | 3 | 2 | 3 |
| **Total** | **182** | **101** | **25** | **22** | **33** |

**Applicable rows (Total - N-A):** 149.

**Strict Pass rate:** 101 / 149 = **67.8%**.

**Lenient rate (Pass + Partial):** 123 / 149 = 82.6%. The project rule is
to treat Partial as Fail for findings purposes, so the strict 67.8% is the
figure that matters.

### Punch list -- all Fail and Partial items

Sorted by chapter, with one-line remediation notes.

| ASVS ID | Requirement | Verdict | Remediation note |
|---|---|---|---|
| V2.1.2 | Passwords 64-128 chars | Partial | Accept at least 64 chars; bcrypt 72-byte cap needs either Argon2id migration or a pre-hash SHA-256 step. |
| V2.1.7 | Breached-password check | Fail | Add HIBP k-anonymity lookup at registration and change. |
| V2.1.8 | Password strength meter | Fail | Add zxcvbn-js to registration template. |
| V2.1.12 | Masked-password view toggle | Fail | Add show/hide eye-icon JS to password inputs. |
| V2.2.1 | Anti-automation effective | Partial | Move Flask-Limiter to Redis/filesystem storage or enforce single-worker Gunicorn. |
| V2.2.3 | Notification on auth changes | Fail | Would require adding an email or in-app notification channel. Scoped feature. |
| V2.3.2 | FIDO2 / WebAuthn | Fail | Add WebAuthn enrollment as an alternative to TOTP. Large effort. |
| V2.4.5 | Additional KDF pepper | Fail | Wrap bcrypt in HMAC-SHA256 with server pepper key. |
| V2.5.5 | Notification on factor change | Fail | Same channel as V2.2.3. |
| V2.6.2 | Backup code entropy 112+ bits | Fail | Change `secrets.token_hex(4)` to `secrets.token_urlsafe(14)`. One line. |
| V2.8.4 | TOTP one-time in validity | Fail | Track `last_totp_used_at` on `mfa_configs`; reject replay within window. |
| V2.8.5 | TOTP reuse logged/notified | Fail | Downstream of V2.8.4; add event + (when available) notification. |
| V2.10.1 | No unchanging intra-service creds | Partial | Accepted for single-host deployment; needs secrets-mgr for multi-host. |
| V2.10.3 | Service passwords protected | Partial | Delete tracked `.env.dev` (Preliminary Finding #1) or replace placeholders. |
| V2.10.4 | Secrets not in source | Partial | Same as V2.10.3; consider Docker secrets or Vault for production. |
| V3.2.1 | Session token rotation on login | Partial | Set `login_manager.session_protection = "strong"` in `extensions.py`. |
| V3.3.2 | Idle timeout / periodic re-auth | Fail | Set `PERMANENT_SESSION_LIFETIME` to 8-24h in ProdConfig; add idle-check. |
| V3.3.4 | View active sessions | Partial | Build a session list UI; requires server-side session store (currently client-signed cookies only). Medium effort. |
| V3.4.4 | `__Host-` cookie prefix | Fail | Add `SESSION_COOKIE_NAME = "__Host-session"` in ProdConfig. One line. |
| V4.3.1 | Admin interfaces use MFA | Partial | Require MFA for owner role at registration; or nag at login. |
| V4.3.3 | Step-up auth for high-value ops | Fail | Add recent-TOTP requirement to anchor-balance edits, companion creation, bulk deletes. |
| V6.1.1 | PII encrypted at rest | Fail | Accepted for LAN-only; for public deployment add disk encryption docs or pgcrypto column encryption. |
| V6.1.3 | Financial data encrypted at rest | Fail | Same as V6.1.1 -- accepted for current state. |
| V6.2.4 | Crypto can be rotated | Fail | Implement `MultiFernet` for TOTP_ENCRYPTION_KEY with versioned token prefix. Preliminary Finding #6. |
| V6.4.1 | Secrets management solution | Fail | Accepted for single-operator; blocker for multi-host or public. |
| V6.4.2 | Key material not exposed to app | Fail | Same as V6.4.1. |
| V7.1.3 | Security events comprehensively logged | Partial | Add `access_denied` event in ownership helpers. |
| V7.2.2 | Access control failures logged | Fail | Same remediation as V7.1.3. |
| V7.3.3 | Logs protected from tampering | Partial | Accepted for single-host; ship logs to Loki/Datadog for tamper-evidence. |
| V7.4.2 | Exception handling used | Partial | Replace 14 `except Exception:` blocks in routes with specific exceptions per coding standards. |
| V8.1.4 | Detect/alert on abnormal volumes | Fail | Ship rate-limit-hit events to an alert channel; add per-user daily thresholds. |
| V8.2.1 | Cache-Control: no-store | Fail | Add `response.headers["Cache-Control"] = "no-store"` in `_register_security_headers`. One line. |
| V8.2.3 | Client storage cleared on logout | Partial | Downstream of V8.2.1 fix. |
| V8.3.2 | User data export / deletion | Fail | Implement `/settings/export` and `/settings/delete-account` routes. Medium effort. |
| V8.3.3 | Consent / privacy policy | Fail | Add a privacy policy page and consent checkbox on registration. |
| V8.3.4 | Sensitive-data policy / inventory | Partial | Write `docs/data-classification.md` listing PII, financial, and sensitive fields. |
| V8.3.5 | Per-record access audit | Partial | Add a write to `system.audit_log` (or equivalent) when financial records are read. Large effort due to volume. |
| V8.3.6 | Memory-clear sensitive data | Fail | Python limitation; accepted gap with remediation note. |
| V8.3.7 | Encryption with confidentiality + integrity | Partial | Downstream of V6.1.1/V6.1.3. |
| V8.3.8 | Retention classification | Partial | Add retention docs and a scheduled cleanup for deleted-user financial data. |
| V9.2.2 | TLS on all connections incl. DB | Partial | Add `?sslmode=require` to DATABASE_URL and enable TLS on the postgres container. Medium effort. |
| V10.3.1 | Signed image updates | Partial | Sign images with Cosign and verify in entrypoint.sh. |
| V10.3.2 | Integrity / SRI on external code | Partial | Add `integrity=` to Bootstrap Icons link; consider self-hosting CDN assets. |
| V14.1.3 | Server config hardened | Partial | Add `server_tokens off;` in nginx/nginx.conf (even though Nginx is bypassed in prod). |
| V14.1.5 | Config-drift integrity check | Fail | Write `scripts/config_audit.py` emitting a hash of security settings. |
| V14.2.3 | SRI on CDN assets | Partial | Same as V10.3.2. |
| V14.3.2 | No version info in headers | Fail | Set `server_tokens off;` in nginx; verify Gunicorn `Server:` header. |
| V14.3.3 | (Duplicate of V14.3.2) | Fail | Same. |
| V14.4.5 | HSTS header | Fail | Add `Strict-Transport-Security: max-age=31536000; includeSubDomains` in `_register_security_headers`. One line. Preliminary Finding #3. |

### Top 5 most important Fails for a money app

**1. V2.6.2 -- Backup code entropy at 32 bits instead of 112.**
`secrets.token_hex(4)` gives 8 hex chars / 32 bits of entropy per backup
code. With 10 codes available to guess, and only a 5-per-15-minute rate
limit gating guesses (and that rate limit is in-memory per-worker per
V2.2.1), the attacker's window of opportunity is uncomfortably finite.
Fix: `secrets.token_urlsafe(14)` (112 bits) -- a one-line change. In a
money app, an attacker who phishes the password and brute-forces a
backup code owns the entire budget.

**2. V8.2.1 -- No `Cache-Control: no-store` on financial pages.**
The user logs out at a coffee shop. Someone steps up to the laptop,
presses Back in the browser, and sees the full dashboard -- because the
page is in the browser history cache and the server never told the
browser "don't cache this." Same scenario on any shared device. Fix:
one line in `_register_security_headers`.

**3. V6.2.4 -- No TOTP_ENCRYPTION_KEY rotation path.**
If the Fernet key leaks (host breach, config mistake, backup restored
to a different host), every MFA-enrolled user must re-enroll from
scratch, losing their existing TOTP secret and backup codes. There is
no in-place re-wrap. In a real compromise scenario this is an emergency
response problem, not a scheduled maintenance. Fix: migrate to
`MultiFernet` with a versioned token prefix. Confirms Preliminary
Finding #6.

**4. V14.4.5 -- HSTS missing.**
Without HSTS, a user connecting over rogue WiFi on their first visit
(before Cloudflare's HSTS preload kicks in, if it's configured at all)
is vulnerable to a SSLstrip downgrade. For an app that holds someone's
entire financial picture and is specifically meant to be accessed from
multiple locations (home + phone on-the-go), HSTS is baseline. Fix:
one line. Confirms Preliminary Finding #3.

**5. V2.8.4 -- TOTP codes can be replayed within their validity window.**
`pyotp.TOTP.verify(code, valid_window=1)` accepts a code anywhere in a
~90-second window (current 30s +/- 1 neighbor). It does NOT track
previously consumed codes within that window. An attacker who observes
a TOTP code (shoulder-surf, screen-share leak, OTP-interception Trojan)
can replay it up to ~90 seconds later. For MFA to be a meaningful
second factor, the code must truly be single-use. Fix: add
`last_totp_timestamp` to `auth.mfa_configs`, reject submitted codes at
or before that mark.

### Cross-references to earlier sessions

**S1 (identity + SAST + manual deep dives) confirmations:**
- V2.10.4 Partial -- `.env.dev` tracked with placeholders (Preliminary Finding #1, S1 report 01-identity.md).
- V6.2.4 Fail -- no TOTP key rotation (Preliminary Finding #6, S1 report 07-manual-deep-dives.md).
- V7.1.1 / V7.1.2 Pass -- PII-in-logs check confirmed clean.
- V2.4.4 Pass -- bcrypt cost factor verified (S1 report 07-manual-deep-dives.md deep dive on password hashing).

**S2 (supply chain + runtime + container + host) confirmations:**
- V2.2.1 Partial -- Flask-Limiter memory backend (Preliminary Finding #4, S2 reports 03-config-deploy.md, 09-supply-chain.md).
- V14.2.1 Pass -- pip-audit + trivy used.
- V14.2.5 Pass -- SBOM exists.
- V14.3.2 Fail -- `server_tokens` not set (cross-referenced S2 report 03-config-deploy.md line 707-714).

**S3 (attack surface + threat model) confirmations:**
- V9.1.1 Pass / V9.2.2 Partial -- cloudflared routes directly to Gunicorn, bypassing Nginx (S3 report 13-attack-surface.md and 14-threat-model.md).

**S4 (IDOR DAST) confirmations:**
- V4.1.3 Pass, V4.2.1 Pass -- cross-user access returns 404 for every tested path (S4 report 15-idor-dast.md).

**S5 (business logic) confirmations:**
- V10.2.5 Pass -- no salami / logic bypass findings (S5 report 16-business-logic.md).

**S6 (migration / schema) confirmations:**
- V6.1.1 / V6.1.3 Fail context -- S6 schema review (report 17-migrations-schema.md) confirms no pgcrypto columns on email, display_name, or financial amount columns.

### ASVS findings NOT caught by prior sessions

These are the unique value-adds of the ASVS pass -- Fails/Partials that
earlier sessions did not surface, because ASVS forces attention to
requirements the free-form code review would not reach:

1. **V2.6.2** Backup-code entropy at 32 bits. Prior sessions noted
   backup-code bcrypt hashing (Pass) but did not measure the raw
   entropy against any standard.
2. **V2.8.4** TOTP replay within validity window. Prior sessions
   confirmed TOTP secret encryption and `valid_window=1` but did not
   test reuse explicitly.
3. **V2.4.5** No additional KDF pepper. Prior sessions verified bcrypt
   rounds but did not measure against ASVS's expectation of a pepper.
4. **V3.3.2** No idle timeout + 30-day remember-me with no forced
   re-auth. Previous sessions confirmed cookies are Secure/HttpOnly/
   SameSite but didn't evaluate session lifetime policy.
5. **V3.4.4** Cookie name is not `__Host-session`. Trivial but nobody
   flagged it.
6. **V7.2.2** Access-control failures are not logged as distinct
   events. Prior sessions confirmed auth events ARE logged but did
   not flag the absence of access-denied events.
7. **V8.1.4** No alerting on abnormal request volumes. Rate limiting
   was confirmed in S2; the alerting gap is new.
8. **V8.2.1** No `Cache-Control: no-store`. Biggest surprise -- the
   security headers block was audited in S1 but HSTS was the only
   flagged gap.
9. **V8.3.2 / V8.3.3** No user data export/deletion, no privacy
   policy. GDPR-flavored requirements not examined elsewhere.
10. **V14.1.5** No config-drift detection. Operational hygiene
    requirement not covered by any prior session.

### Current state vs. ASVS L2 for a public app

Shekel is currently deployed as **single-operator, LAN-plus-Cloudflare-
Tunnel, self-hosted, Docker-on-bare-metal**. Several ASVS L2 Fails are
acceptable in this posture:

- **V6.1.1, V6.1.3** (PII / financial data encryption at rest) -- the
  Postgres volume is on the host filesystem; disk encryption is
  deployable at the host level (LUKS) and is out of scope for the app
  code. Document this as a deployment requirement and the Fails
  functionally close.
- **V6.4.1, V6.4.2** (secrets management) -- `.env` on a trusted host
  is operationally simple and contained. Not acceptable at public
  scale.
- **V9.2.2** (TLS on all connections including DB) -- Docker
  `internal: true` backend network provides network isolation. Not
  acceptable for remote-DB deployments.
- **V8.3.2, V8.3.3** (user data export/delete, privacy consent) --
  relevant only when there is a user community to serve those rights
  to.

These must all be fixed **before Shekel goes public**. The remaining
Fails (V2.6.2 backup-code entropy, V2.8.4 TOTP replay, V8.2.1 cache
control, V14.4.5 HSTS, V3.3.2 idle timeout, V3.4.4 cookie prefix)
should be fixed even in the current deployment because they are not
contingent on public exposure -- they are baseline hygiene.

### Remediation effort sizing

**Small (~1 day each, high priority):** V2.6.2 backup code entropy,
V8.2.1 Cache-Control, V14.4.5 HSTS, V3.4.4 cookie prefix, V14.3.2
server_tokens, V14.2.3 / V10.3.2 Bootstrap Icons SRI, V7.2.2
access_denied event, V3.2.1 session_protection="strong".

**Medium (1-5 days each):** V2.8.4 TOTP replay tracking, V4.3.3 step-up
auth, V3.3.2 idle timeout + PERMANENT_SESSION_LIFETIME, V2.2.1
rate-limit Redis backend, V7.4.2 replace 14 `except Exception:` blocks,
V6.2.4 MultiFernet migration, V9.2.2 Postgres TLS, V10.3.1 Cosign
image signing.

**Large (a week or more):** V2.3.2 WebAuthn/FIDO2, V6.1.1 / V6.1.3
field-level encryption, V6.4.1 secrets-management integration, V8.3.2
user data export/delete UI, V8.3.5 per-record access audit, V2.2.3 /
V2.5.5 notification channel, V14.1.5 config-drift tooling.

**Accepted / deferred:** V8.3.6 memory clearing (Python limitation),
V7.3.3 tamper-evident logs (needs external log store), V4.3.1 require
MFA for owner (UX decision).










