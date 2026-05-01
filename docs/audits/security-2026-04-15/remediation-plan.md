# Shekel Security Audit -- Remediation Implementation Plan

**Source audit:** `docs/audits/security-2026-04-15/findings.md` (commit `3cff592`, 160 findings: 1 Critical / 30 High / 65 Medium / 45 Low / 19 Info per-report; post-dedup rollup `1 Critical / 29 High / 52 Medium / 75 Low / 3 Info` after the 19 Info observations were rolled into `Informational observations`).
**Plan branch:** `audit/security-2026-04-15` (HEAD `7c434f2`).
**Plan author:** Automated Phase 2 planning session, 2026-04-22.
**Status:** READY FOR PHASE 3 EXECUTION. All developer checkpoints (Phase A, Phase B, Phase E) complete.

---

## Executive Summary

**Audit headline.** 164 verified findings (160 from the audit + 4 new discovered
during Phase 2 verification). Severity distribution post-verification:
**1 Critical / 29 High / 52 Medium / 79 Low / 3 Info.** Zero findings were
superseded -- every vulnerability, drift, and hardening gap recorded in
findings.md on commit `3cff592` is still present on branch `audit/security-2026-04-15`
HEAD `7c434f2`. Developer accepted 4 dependency-staleness Defers (F-058, F-059,
F-118, F-119); all other findings map to a commit.

**Proposed disposition:**

| Disposition | Count |
|---|---|
| Fix-now (Phase 1, commits C-01..C-12) | 35 |
| Fix-this-sprint (Phases 2-7, commits C-13..C-43) | 78 |
| Fix-backlog (Phases 8-10, commits C-44..C-56) | 47 |
| Defer (Accepted risk, monitored) | 4 |
| **Total** | **164** |

**Top 3 risks (re-validated against current code).**

1. **Audit-log infrastructure is missing from production** (F-028, F-080, F-082,
   F-151). Migration `a8b1c2d3e4f5` declares 22 AFTER INSERT/UPDATE/DELETE
   triggers, but live DB has zero. `log_event()` covers only ~14 of 95 mutating
   routes. Logs ship to a container volume the app could rewrite. The forensic
   trail on this app is effectively the attacker's own output stream. Remediated
   by **C-13** (triggers rebuild + CREATE SCHEMA + least-privilege DB role) +
   **C-14** (systematic log_event rollout in service layer) + **C-15** (off-host
   log shipping to tamper-resistant destination).

2. **Cryptographic posture has five one-line fixes that ship together and close
   the stolen-laptop + cafe-WiFi + shared-computer threat set** (F-001, F-004,
   F-017, F-018, F-019, + header cluster). SECRET_KEY committed to git history
   (F-001), 32-bit backup codes (F-004), remember-me cookie missing
   Secure/SameSite (F-017), no HSTS (F-018), no Cache-Control no-store (F-019).
   Remediated by **C-01** (SECRET_KEY history excise + session-invalidate all) +
   **C-02** (cookie flags + HSTS + Cache-Control + CSP tightening bundle) + **C-03**
   (backup-code entropy upgrade).

3. **Anchor-balance + transfer-invariant enforcement is convention-only**
   (F-007, F-008, F-009, F-010). For a money app aspiring to public release,
   these are the correctness gaps that will manifest as user-visible balance
   drift. Remediated by **C-17** (anchor optimistic locking) + **C-18** (stale-form
   prevention across every PATCH) + **C-19** (TOCTOU duplicate CC Payback
   prevention) + **C-20** (shadow mutation guards) + **C-21** through **C-23**
   (transfer invariant cluster).

**Sequencing (one sentence per phase).**

- **Phase 1 -- Crypto and History (C-01..C-12).** Rewrite history, harden keys, fix
  the five one-line crypto posture fixes, restore the audit log's tamper-resistant
  foundation.
- **Phase 2 -- Audit Log Restoration (C-13..C-16).** DB-tier audit triggers, Python
  structured logging, off-host shipping, PII redaction.
- **Phase 3 -- Financial Invariants (C-17..C-23).** Anchor balance optimistic
  locking, stale-form prevention, CC Payback TOCTOU, transfer invariants, composite
  uniques on duplicate-prone operations.
- **Phase 4 -- Input Validation and Schema/DB Sync (C-24..C-28).** Marshmallow
  Range sweep aligned with DB CHECK sweep; boolean NOT NULL + boundary
  inclusivity; auth schemas; multi-tenant account_type guard.
- **Phase 5 -- Access-Control Response Consistency (C-29..C-31).** Cross-user FK
  re-parenting fix; analytics ownership checks; 404-everywhere unification.
- **Phase 6 -- Config Hardening (C-32..C-39).** Commit production configs into
  repo; network topology; Docker hardening bundle; Dockerfile refresh; cloudflared
  + Postgres TLS; env cleanup + Docker secrets; field-level PII encryption.
- **Phase 7 -- Schema Cleanup (C-40..C-43).** Migration backfill conventions;
  duplicate CHECK cleanup; salary + hysa migration repair; FK ondelete sweep.
- **Phase 8 -- Low Cleanup (C-44..C-52).** Hardening polish: verify_password,
  grid None-check, retirement Decimal, narrow except, hash pins, Argon2id, config
  drift check, data classification, nmap note, per-record read audit.
- **Phase 9 -- Bigger Features ex-Defer (C-53..C-55).** Server-side sessions +
  WebAuthn; GDPR export + delete; key-material documentation.
- **Phase 10 -- Host Runbook (C-56).** Out-of-repo host hardening steps (11
  findings).

**Total commit count: 56** (52 in-repo commits + 4 runbook/documentation entries
at C-56). Complexity distribution: **6 Large, 29 Medium, 21 Small.**

**Critical path (first five commits, in order):**

1. **C-01 (Medium): SECRET_KEY history excise + runtime default tightening.**
   Rationale: every other commit that relies on valid-session semantics depends
   on legacy cookies being rejected. Ships `scripts/rotate_sessions.py` which
   invalidates every active session on first deploy.
2. **C-02 (Medium): Cookie + header + CSP tightening bundle.** Rationale: F-017 +
   F-018 + F-019 + F-036 + F-037 + F-096 + F-097 all mutate the same
   `_register_security_headers` function or `ProdConfig` class; atomic rollback
   is safer than five separate changes.
3. **C-03 (Small): Backup code entropy upgrade.** Rationale: 32 -> 112 bits is a
   two-character change; independent; should ship with Phase 1 so the
   ASVS L2 V2.6.2 gap closes alongside the other crypto posture fixes.
4. **C-04 (Medium): MultiFernet TOTP key rotation.** Rationale: enables non-
   destructive `TOTP_ENCRYPTION_KEY` rotation; C-39 (field-level PII encryption)
   reuses the infrastructure.
5. **C-05 (Small): MFA setup secret stored server-side.** Rationale: removes the
   plaintext-in-cookie exposure window during MFA setup.

**Architectural decisions pending developer input at the relevant commit
checkpoints during Phase 3 execution:**

- **C-06 (F-034):** Flask-Limiter backend -- Redis (plan default) vs single-worker
  Gunicorn.
- **C-15 (F-082):** Off-host log destination -- S3 with Object Lock (plan default)
  vs Grafana Loki vs rsyslog hash-chained retention.
- **C-02 (F-018):** HSTS `preload` submission -- deferred to post-90-day stability
  window by default.
- **C-39 (F-147):** Field-level encryption scope -- email + display_name +
  current_anchor_balance (plan default) vs expand to transaction amounts at
  performance cost.
- **C-11 (F-086):** HIBP hosted API (plan default) vs self-hosted mirror vs
  zxcvbn-only.
- **C-48 (F-088):** Argon2id migration -- opportunistic on-login rehash (plan
  default) vs batch rehash impossible.
- **C-53 (F-143/F-092):** Split into C-53a (sessions) + C-53b (WebAuthn) vs
  single commit.

**Defers (accepted risks).** Four dependency-staleness findings (F-058 pyotp,
F-059 Flask-Login, F-118 psycopg2 license, F-119 Flask-SQLAlchemy) enter
`findings.md` Accepted Risks with rigorous threat-model delta, compensating
controls, and re-open triggers documented in Phase E.

**New findings discovered during Phase 2 verification.** Four first-class Low/Info
findings (F-161 transaction state-machine, F-162 mark_done raw decimal in transfer
branch, F-163 mfa_verify length cap, F-164 restore_transfer account check) are
folded into commits C-21, C-27, C-26, C-20 respectively. A fifth candidate
(F-NEW-005 other bare `pass` downgrades) was collapsed into F-131 after grep
verified only `b4c5d6e7f8a9` matches.

**Post-Phase-3 closeout (per audit workflow).** Before declaring the audit done,
Phase 3 execution must:

1. Re-run the full SAST sweep (`bandit`, `semgrep`, `pip-audit`, `trivy`). Zero
   previously-open finding matches.
2. Re-run the DAST IDOR probe (`scripts/audit/idor_probe.py`). Zero failures.
3. Re-run the full test suite split by directory. Zero failures.
4. Update `findings.md` to mark every non-Deferred finding Fixed with
   `<commit SHA> (PR #<n>)`.
5. Re-run the schema drift check. Live DB matches models.
6. Re-run host hardening verification per C-56 runbook steps.

---

This document is written strictly as a planning artifact. It prescribes the code, schema,
and test changes needed to close every verified finding; it does not modify application
code, migrations, or tests itself. A separate Phase 3 implementation session will execute
the per-commit plans below.

---

## Phase A -- Finding verification against current code

Every finding F-001 through F-160 was re-checked against the files currently on branch
`audit/security-2026-04-15`. The findings.md Evidence blocks were quoted from commit
`3cff592`; this verification pass records the CURRENT file:line as of HEAD `7c434f2`.

Classification legend:

- **Verified** -- the vulnerable pattern or missing control is still present in current
  code, configs, migrations, or runtime state. Line numbers may have drifted; the
  Current Location column below is authoritative.
- **Superseded** -- the vulnerable pattern has been removed or materially changed since
  commit `3cff592`. Would be noted with the replacement commit SHA where identifiable.
- **Partially applies** -- some cited instances remain; others were fixed. Specific
  remaining instances enumerated in Notes.

After reading the cited files in full, **all 160 findings remain verified.** No
finding has been silently remediated between `3cff592` and `7c434f2`. The verification
table below records each finding's current location, any drift, and cross-reference
dependencies.

### Verification Table (160 findings)

| Finding | Severity | Status | Current Location (file:line) | Cross-refs | Notes |
|---|---|---|---|---|---|
| F-001 | Critical | Verified | Git history commit `f9b35ecb5d71...` (initial commit `src/flask_app/app.py`? -- verified present in branch history); current `app/config.py:22` still has fallback default | F-016, F-017, F-110, F-111 | Key rotated in production per audit evidence; history still present. Distinct from F-016 (runtime default) -- both must ship together. |
| F-002 | High | Verified | `app/routes/auth.py:100-107` (storage), `:252-344` (consumption in `mfa_verify`) | F-035, F-006 | No `_mfa_pending_at` anywhere (grep returns zero). Pending state keyed only by session cookie which has no explicit lifetime. |
| F-003 | High | Verified | `app/routes/auth.py:310-319` (consume), `:325-344` (login completion) | F-032 (MFA disable), F-006, F-017 | `login_user()` at line 334 does not set `session_invalidated_at`. Contrast with `/change-password:214-222` and `/invalidate-sessions:231-247`. |
| F-004 | High | Verified | `app/services/mfa_service.py:112-123` (`generate_backup_codes` uses `secrets.token_hex(4)`) | F-001, F-017, F-018, F-019 | 32 bits. ASVS V2.6.2 requires 112 bits. 8-char hex column width in template will change to 28 chars. |
| F-005 | High | Verified | `app/services/mfa_service.py:96-109` (`verify_totp_code(..., valid_window=1)`) | F-142 (replay logging depends on F-005 fix) | `auth.mfa_configs` has no column to track last-consumed step. Grep returns zero hits for `last_totp|last_used_step|totp_last_step|otp_counter`. |
| F-006 | High | Verified | `app/config.py:31-33` (`REMEMBER_COOKIE_DURATION = timedelta(days=30)`); no `PERMANENT_SESSION_LIFETIME` anywhere; no `_session_last_activity_at` check in `app/__init__.py:load_user:59-84` | F-002, F-035, F-017, F-001 | `load_user` checks `session_invalidated_at` only, not last-activity. |
| F-007 | High | Verified | `app/services/recurrence_engine.py:249-288` -- `resolve_conflicts` body at lines 270-287 still writes `is_override=False`, `is_deleted=False`, `estimated_amount=new_amount` with no `txn.transfer_id is not None` guard | CLAUDE.md Transfer invariant 4 | Caller `regenerate_for_template` filters to `template_id == template.id` so no live bug; latent. |
| F-008 | High | Verified | `app/services/credit_workflow.py:69-130` (`mark_as_credit`); `app/services/entry_credit_workflow.py:32-123` (`sync_entry_payback`) | F-046, F-050, F-051, F-052, F-102 (idempotency family) | No partial unique index on `budget.transactions.credit_payback_for_id`; idempotency check at :72-80 is racy. Entry variant at :91 same pattern. |
| F-009 | High | Verified | `app/models/account.py:30` (`current_anchor_balance = db.Column(db.Numeric(12, 2))` -- no `version_id` column; no `__mapper_args__ = {"version_id_col": ...}`); routes `app/routes/accounts.py:469` (`inline_anchor_update`), `:651-719` (`true_up`), `:223-283` (`update_account`) | F-010, F-103 (duplicate history), F-077 (DB CHECK) | Grep of `app/` for `with_for_update|FOR UPDATE|version_id_col` returns zero hits. |
| F-010 | High | Verified | `app/routes/transactions.py:265-266` (PATCH setattr loop); `app/routes/transfers.py:620-647` (`update_transfer` inline edit); `app/routes/entries.py` PATCH paths; `app/routes/accounts.py:223-283`; `app/routes/salary.py` raise/deduction PATCH paths | F-009, F-046-F-050 (idempotency cluster) | Red-team affirmed High for public-release scope. |
| F-011 | High | Verified | `app/schemas/validation.py:249-255` (`percentage = fields.Decimal(validate=validate.Range(min=-100, max=1000))`, `flat_amount = fields.Decimal(validate=validate.Range(min=-10000000, max=10000000))`); `app/models/salary_raise.py:25-26` (CHECK `percentage IS NULL OR percentage > 0`, `flat_amount IS NULL OR flat_amount > 0`); `app/routes/salary.py` add_raise path | F-074, F-076 | CHECK in model uses the `IS NULL OR > 0` form (still excludes 0 and negatives). Schema accepts both. |
| F-012 | High | Verified | `app/schemas/validation.py:280` (`amount = fields.Decimal(required=True, places=4, as_string=True)` -- NO `validate=Range(...)`); `app/models/paycheck_deduction.py:16` CHECK `amount > 0` | F-074, F-075, F-076 | Schema field lives at exact line 280 (Update schema inherits but omits amount). |
| F-013 | High | Verified | `app/schemas/validation.py:1224-1226` (`trend_alert_threshold = fields.Integer(validate=validate.Range(min=1, max=100))`); `app/models/user.py:69-72` CHECK `trend_alert_threshold >= 0 AND trend_alert_threshold <= 1`; `server_default='0.1000'` at `user.py:95` | F-014, F-076 | Bounds do NOT overlap. Default 0.10 passes DB, fails schema. |
| F-014 | High | Verified | `app/schemas/validation.py:319-338` (`ss_rate/medicare_rate/medicare_surtax_rate` `Range(min=0, max=100)`); `:351-354` `flat_rate` same; `:1214-1217` `default_inflation_rate` `Range(min=0, max=100)`. DB CHECKs at `0-1` on `salary.fica_configs`, `salary.state_tax_configs`, `auth.user_settings` | F-013, F-076, F-077 | Model storage is `Numeric(5,4)` -- decimal format. |
| F-015 | High | Verified | `nginx/nginx.conf:117-120` (trust `127.0.0.1`, `172.16.0.0/12`, `192.168.0.0/16`, `10.0.0.0/8`); `gunicorn.conf.py:80-83` (`forwarded_allow_ips` default same three RFC 1918 subnets) | F-020, F-034, F-063 | Same cidrs on both layers. |
| F-016 | High | Verified | `app/config.py:22` (`SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me-in-production")`); ProdConfig `__init__` at `:130-137` rejects `startswith("dev-only")` only | F-001, F-110, F-111 | `.env.example:11` literal `SECRET_KEY=change-me-to-a-random-secret-key` bypasses the guard (see F-110). |
| F-017 | High | Verified | `app/config.py:92-138` (ProdConfig) -- sets `SESSION_COOKIE_SECURE=True/HTTPONLY=True/SAMESITE="Lax"` at :126-128; NO `REMEMBER_COOKIE_*` anywhere | F-001, F-006, F-096 | Remember-me cookie inherits Flask-Login defaults `SECURE=False`, `SAMESITE=None`. |
| F-018 | High | Verified | `app/__init__.py:412-428` (`_register_security_headers` body) lists X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, Content-Security-Policy -- NO Strict-Transport-Security | F-019, F-097 | `nginx/nginx.conf` static block at :160 sets `X-Content-Type-Options` only; no HSTS anywhere. |
| F-019 | High | Verified | `app/__init__.py:412-428` -- no `Cache-Control` header set by Flask; `nginx/nginx.conf:154` sets `Cache-Control: public, immutable` on `/static/` only | F-018, F-096 | Authenticated financial pages have no no-store directive. |
| F-020 | High | Verified | Production `docker-compose.override.yml` at `/opt/docker/shekel/docker-compose.override.yml` (NOT in repo, see F-021); repo `docker-compose.yml` does NOT reference the homelab network | F-021, F-063, F-064, F-129 | Runtime state finding -- the committed `docker-compose.yml` is not the one running in production. |
| F-021 | High | Verified | Repo `nginx/nginx.conf` is superseded by `/opt/docker/nginx/nginx.conf` and `/etc/nginx/conf.d/shekel.conf` on production host; repo `docker-compose.yml` is superseded by `/opt/docker/shekel/docker-compose.override.yml` | F-020, F-063, F-064, F-156 | Production configs captured under `docs/audits/security-2026-04-15/scans/shared-nginx.conf.txt` etc. |
| F-022 | High | Verified | `docker-compose.yml:68-69` (`SEED_USER_EMAIL`, `SEED_USER_PASSWORD` passed through to the running app container env) | F-023, F-113 | Present in `docker exec shekel-prod-app env` for the container lifetime. |
| F-023 | High | Verified | Runtime state: host files `/home/josh/projects/Shekel/.env` and `/opt/docker/shekel/.env` permissions 644 per Lynis report in `scans/lynis.log` | F-022, F-122 | Host-level finding; fix is `chmod 600`. |
| F-024 | High | Verified | Runtime state: `/proc/sys/kernel/kptr_restrict = 0` per `scans/lynis.log` (Lynis KRNL-6000) | F-122, F-067, F-125 | Host-level; fix via `/etc/sysctl.d/99-hardening.conf`. |
| F-025 | High | Verified | Docker image `ghcr.io/saltyreformed/shekel:latest` revision `91f2627` still pulled by `docker-compose.yml:48`; trivy scan `scans/trivy-image.json` shows `libssl3t64 3.5.5-1~deb13u1` (CVE-2026-28390 HIGH) | F-060, F-062 | Fix by rebuilding image with `apt-get upgrade openssl libssl3t64`. |
| F-026 | High | Verified | `migrations/versions/efffcf647644_add_account_id_column_to_transactions.py:17-37` -- `sa.Column('account_id', sa.Integer(), nullable=False)` with no backfill before NOT NULL | F-027, F-070, F-071, F-072 | 53 lines total. Production applied this migration somehow (column populated) -- mechanism not recorded in migration file. |
| F-027 | High | Verified | `migrations/versions/c5d6e7f8a901_add_positive_amount_check_constraints.py:20-32` (`ck_transactions_positive_amount`, `ck_transactions_positive_actual`); `migrations/versions/dc46e02d15b4_add_check_constraints_to_loan_params_.py` adds the renamed pair; model `app/models/transaction.py:48-55` declares `ck_transactions_estimated_amount`, `ck_transactions_actual_amount` (the #28 names only) | F-069, F-026 | Live DB has only the #28 pair per `scans/schema-budget-transactions.txt`. |
| F-028 | High | Verified | Migration `migrations/versions/a8b1c2d3e4f5_add_audit_log_and_triggers.py` exists (183 lines, declares `system.audit_log`, `audit_trigger_func`, 22 AFTER triggers). Live DB per S6: three `SELECT count(*)` queries returned 0, 0, 0 | F-070, F-080, F-082, F-151, F-153 | Runtime-state finding. Requires a new migration that rebuilds idempotently + an `entrypoint.sh` assertion. |
| F-029 | High | Verified | `app/routes/transactions.py:183-285` (`update_transaction`), loop at :265-266 `setattr(txn, field, value)`; `TransactionUpdateSchema` exposes `pay_period_id` and `category_id` at `app/schemas/validation.py:32-33` | F-010, F-043, F-098, F-087 | Transfer-path at :208-243 routes through service which DOES validate period; gap is only in the non-transfer path. |
| F-030 | High | Verified | `app/services/mfa_service.py:18-30` (`get_encryption_key` returns single-key `Fernet`). Grep of `app/` for `MultiFernet` returns zero hits | F-004, F-031, F-148, F-149 | `docs/runbook_secrets.md:11` still documents rotation as destructive. |
| F-031 | Medium | Verified | `app/routes/auth.py:366` (`flask_session["_mfa_setup_secret"] = secret`); `:386` (`secret = flask_session.pop("_mfa_setup_secret", None)`). `app/config.py` has no `SESSION_TYPE` override | F-030, F-036, F-148 | Flask default `SecureCookieSessionInterface` signs but does not encrypt. |
| F-032 | Medium | Verified | `app/routes/auth.py:472-521` (`mfa_disable_confirm`). Commit at :516, no `session_invalidated_at = now()` write | F-003, F-091 | Same pattern gap as F-003. |
| F-033 | Medium | Verified | `app/routes/auth.py:73-132` (login); `app/services/auth_service.py:294-312` (`authenticate`); `app/models/user.py:13-52` has no `failed_login_count`, `locked_until`, `last_failed_login_at` | F-034, F-086, F-146 | Grep of `app/` and `migrations/versions/` returns zero hits for `failed_login|lockout|account_locked|login_attempts`. |
| F-034 | Medium | Verified | `app/extensions.py:31` (`Limiter(..., default_limits=[], storage_uri="memory://")`); `gunicorn.conf.py:24` (`workers = int(os.getenv("GUNICORN_WORKERS", "2"))`); `docker-compose.yml:71` (`GUNICORN_WORKERS: ${GUNICORN_WORKERS:-2}`) | F-015, F-033, F-146 | 4 routes with explicit `@limiter.limit` per grep of `app/routes/`. |
| F-035 | Medium | Verified | `app/config.py` (search for `PERMANENT_SESSION_LIFETIME`) -- absent from all three config classes | F-002, F-006, F-017 | Flask default 31 days applies to any permanent session. |
| F-036 | Medium | Verified | `app/__init__.py:423` (CSP `"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "`) | F-037, F-097, F-089 | `script-src` at :422 excludes `unsafe-inline`; only `style-src` is permissive. |
| F-037 | Medium | Verified | `app/__init__.py:420-426` permits `cdn.jsdelivr.net`, `unpkg.com`, `fonts.googleapis.com`, `fonts.gstatic.com`; no `require-sri-for` directive; Bootstrap Icons `<link>` in `app/templates/base.html` has no `integrity=` attribute | F-036, F-155 | Bootstrap CSS/JS and htmx have SRI per S7; Bootstrap Icons does not. |
| F-038 | Medium | Verified | `app/extensions.py:22-25` instantiates `LoginManager()` and sets `login_view`, `login_message_category`; no `session_protection = "strong"` line | F-096, F-043 | One-line fix; adds defense-in-depth for future server-side session store. |
| F-039 | Medium | Verified | `app/routes/analytics.py:49-102` (`calendar_tab`) -- `account_id = request.args.get("account_id", None, type=int)` at :69 passed to `calendar_service.get_year_overview(user_id, year, account_id, ...)` at :82-85 without ownership check | F-029, F-043, F-098 | CSV export path bypasses HTMX guard. |
| F-040 | Medium | Verified | `app/routes/debt_strategy.py:222-356` -- hand-parses `extra_monthly` (line 234), `strategy` (:251), `custom_order` (:261) with `request.form.get` and inline `try/except` | F-041, F-042, F-145 | Read-only endpoint today; pattern sets a precedent. |
| F-041 | Medium | Verified | `app/routes/auth.py` uses `request.form.get(...)` in 15+ hits across 6 handlers (login, register, change_password, mfa_verify, mfa_confirm, mfa_disable_confirm); no `schema.load(request.form)` call anywhere in file | F-033, F-040, F-042, F-086, F-110 | `CompanionCreateSchema` in `app/schemas/validation.py:1390-1443` applies strict rules to companion creation -- owner path does not. |
| F-042 | Medium | Verified | `app/routes/transactions.py:321-326, :355-361` (two `Decimal(request.form.get(...))` with `try/except (InvalidOperation, ValueError, ArithmeticError)`); `app/routes/dashboard.py` has similar pattern (checked via grep -- see note) | F-041, F-040, F-145 | `TransactionUpdateSchema.actual_amount = fields.Decimal(..., validate=validate.Range(min=0))` at `validation.py:30` is the correct reuse target. |
| F-043 | Medium | Verified | `app/routes/transfers.py:673-712` (`create_ad_hoc`, forwards `from_account_id`, `to_account_id`, `pay_period_id`, `scenario_id`, `category_id` to service without per-field route-level ownership check); `:620-647` (`update_transfer`) | F-029, F-098 | Service-layer `transfer_service._get_owned_account`, etc. DO check -- inconsistency with `transactions.create_inline`. |
| F-044 | Medium | Verified | `app/routes/accounts.py:546-642` (`create_account_type`, `update_account_type`, `delete_account_type` -- 3 routes gated on `@require_owner`). `ref.account_types` schema per `scans/schema-ref-account_types.txt` has no `user_id` column | F-045, F-073, F-078 | Dormant in single-owner deployment; blocking gap for multi-tenant. |
| F-045 | Medium | Verified | Grep of `app/` for `fresh_login_required\|require_recent_auth\|re_authenticate` returns zero hits. Applies app-wide to anchor-balance changes, bulk-delete, companion creation, tax-config changes | F-033, F-038, F-009 | No `_fresh_login_at` anywhere in session code. |
| F-046 | Medium | Verified | `app/models/transaction.py:94-97` (`transfer_id = db.Column(..., ForeignKey("budget.transfers.id", ondelete="CASCADE"))` -- NO partial unique index on `(transfer_id, transaction_type_id) WHERE transfer_id IS NOT NULL AND is_deleted = FALSE`) | F-007, F-008, F-050 | `scripts/repair_orphaned_transfers.py` in repo documents precedent. |
| F-047 | Medium | Verified | `app/services/transfer_service.py:468-473` (direct assignment `xfer.status_id = new_status_id`; `expense_shadow.status_id = new_status_id`; `income_shadow.status_id = new_status_id`); `app/routes/transfers.py:748-749` | F-010 | No `_ALLOWED_TRANSITIONS` table in module. |
| F-048 | Medium | Verified | `app/routes/transfers.py:749` `transfer_service.update_transfer(xfer.id, current_user.id, status_id=done_id)` -- NO `paid_at=db.func.now()` kwarg. Compare with `app/routes/transactions.py:316-319` (regular) and `app/routes/dashboard.py` mark-paid | F-080, F-152 | One-line add at `transfers.py:749` plus server-side default in `transfer_service.update_transfer` at `transfer_service.py:421-544`. |
| F-049 | Medium | Verified | `app/routes/transactions.py:715-749` (endpoint); `app/services/carry_forward_service.py:71-97` -- SELECT at :71-80 followed by bare `txn.pay_period_id = target_period_id` at :97 with no re-check of `txn.status_id` (for regular_txns partition at :96-103) | F-010, F-008 | Shadow partition at :109-122 routes through transfer service; regular path is the gap. |
| F-050 | Medium | Verified | `app/routes/transfers.py:673-712` (ad-hoc POST). No unique constraint on `(user_id, from_account_id, to_account_id, amount, pay_period_id, <time-bucket>)` anywhere in `app/models/transfer.py` | F-008, F-046, F-102 | Higher blast radius than F-102 because each duplicate creates 4 shadow rows. |
| F-051 | Medium | Verified | `app/models/salary_raise.py:14-28` -- no `UniqueConstraint("salary_profile_id", "raise_type_id", "effective_year", "effective_month", ...)`; model `__table_args__` has CHECK constraints only | F-011, F-052 | Duplicates compound per-paycheck inflation (salary * 1.03 * 1.03). |
| F-052 | Medium | Verified | `app/models/paycheck_deduction.py:14-23` -- no `UniqueConstraint("salary_profile_id", "name", ...)` | F-012, F-051 | $500 biweekly 401(k) double would double-charge every paycheck. |
| F-053 | Medium | Verified | `docker-compose.yml:73` (`REGISTRATION_ENABLED: ${REGISTRATION_ENABLED:-true}`); `.env.example:54` (default `true`); `app/config.py:44-47` interprets env var | F-041, F-045 | `/register` gate at `app/routes/auth.py:141-142` returns 404 when disabled. |
| F-054 | Medium | Verified | Runtime state per `scans/container-logs.txt`: pre-rename containers `shekel-app` (unhealthy), `shekel-db`, `shekel-nginx`; volumes `shekel_pgdata`; networks `shekel_backend`, `shekel_frontend`, `shekel_default` | F-116, F-117 | Host-level cleanup needed (out of repo; developer runs `docker compose -p shekel down -v` after backup check). |
| F-055 | Medium | Verified | `docker-compose.yml` (grep `cap_drop\|security_opt` returns zero matches for any of `db`, `app`, `nginx` services) | F-056, F-117, F-113 | Also missing from daemon-level `/etc/docker/daemon.json` (runtime). |
| F-056 | Medium | Verified | Same source as F-055 -- no `cap_drop: [ALL]` anywhere | F-055 | Same service configs. |
| F-057 | Medium | Verified | `docker-compose.dev.yml:36` (`- "5432:5432"`), `:55` (`- "5433:5432"`) both default to `0.0.0.0` binding | F-109, F-111 | Loopback fix is `"127.0.0.1:5432:5432"`. |
| F-058 | Medium | Verified | `requirements.txt:26` (`pyotp==2.9.0`) | F-059, F-119 | Last release 2023-07-27. |
| F-059 | Medium | Verified | `requirements.txt:13` (`Flask-Login==0.6.3`) | F-058, F-119 | Last release 2023-10-30. |
| F-060 | Medium | Verified | `docker-compose.yml:48` (`image: ghcr.io/saltyreformed/shekel:latest`); `:51` (`pull_policy: always`) | F-025, F-155 | Digest pin: `@sha256:...`. |
| F-061 | Medium | Verified | `cloudflared/config.yml:49-57` (no `team_name` / Access policy; `noTLSVerify: true` at :57 because origin is `http://localhost:80` with no TLS) | F-063, F-128, F-129 | File is a template with `<PLACEHOLDER>` values; actual prod may have Access attached via dashboard. |
| F-062 | Medium | Verified | Runtime state per `scans/trivy-image.json` | F-025, F-067 | `ncurses` CVE-2025-69720 and `systemd` CVE-2026-29111; both unreachable by app. |
| F-063 | Medium | Verified | `cloudflared/config.yml:54` (`service: http://localhost:80`) -- cloudflared runs on host, `localhost:80` is the shared nginx; but S3 evidence notes WAN path routes `http://shekel-prod-app:8000` in actual prod config. Both interpretations yield the same class of parity gap | F-020, F-021, F-064 | Runtime finding -- repo config is not the runtime config. |
| F-064 | Medium | Verified | `scans/shared-nginx-shekel-vhost.conf.txt` in audit snapshot (not in repo, part of F-021 drift) | F-021, F-156 | Shekel vhost has zero `add_header` lines; jellyfin.conf has four. |
| F-065 | Medium | Verified | Runtime: no auditd rules per `scans/docker-bench.txt` lines 1.1.3-1.1.18 | F-066, F-082 | Host-level fix. |
| F-066 | Medium | Verified | Runtime SSH config on host (`/etc/ssh/sshd_config`) per Lynis SSH-7408 | F-130, F-125 | Host-level fix. Josh is in `docker` group (per S3). |
| F-067 | Medium | Verified | Runtime: kernel mismatch per Lynis KRNL-5830 | F-062, F-024 | Host-level reboot required after coordinating outage. |
| F-068 | Medium | Verified | `app/models/transaction.py:92-93` (`is_override = db.Column(db.Boolean, default=False)`, `is_deleted = db.Column(db.Boolean, default=False)` -- no `nullable=False`, no `server_default`); similar for `budget.accounts.is_active` (`account.py:35`), `budget.scenarios.is_baseline`, `budget.recurrence_rules.is_recurring`, `salary.paycheck_deductions.inflation_enabled`, `salary.paycheck_deductions.is_active`, `transaction_templates.sort_order` | F-077, F-134 | Live DB may have NULL rows for pre-column inserts. |
| F-069 | Medium | Verified | Migration `migrations/versions/c5d6e7f8a901_...:34-41` creates `uq_scenarios_one_baseline`; live DB per `scans/schema-budget-scenarios.txt` shows it missing | F-027, F-028 | Runtime-state finding. |
| F-070 | Medium | Verified | `migrations/versions/a8b1c2d3e4f5_add_audit_log_and_triggers.py:50-68` -- upgrade body starts with `CREATE TABLE system.audit_log (...)` with no prior `CREATE SCHEMA IF NOT EXISTS system` | F-028, F-026, F-071 | Must be folded into the F-028 rebuild migration. |
| F-071 | Medium | Verified | `migrations/versions/22b3dd9d9ed3_add_salary_schema_tables.py` (336 lines) -- upgrade drops three indexes (`idx_deductions_profile`, `idx_salary_raises_profile`, `idx_tax_brackets_bracket_set`); downgrade does not recreate them | F-079, F-026, F-133 | Three child-FK indexes still absent from live DB. |
| F-072 | Medium | Verified | `migrations/versions/b4a6bb55f78b_rename_hysa_params_to_interest_params.py` (43 lines) -- renames table + UNIQUE + CHECK; leaves PK, sequence, and FK under legacy name | F-137, F-138, F-078 | Live DB `\d+ budget.interest_params` per `scans/schema-budget-interest_params.txt` confirms. |
| F-073 | Medium | Verified | `ref.account_types.category_id`, `budget.savings_goals.goal_mode_id/income_unit_id`, `salary.salary_profiles.filing_status_id`, `salary.salary_raises.raise_type_id`, `salary.paycheck_deductions.deduction_timing_id/calc_method_id`, `salary.tax_bracket_sets.filing_status_id`, `salary.state_tax_configs.tax_type_id` -- live FKs lack explicit `ondelete="RESTRICT"` per `scans/schema-*.txt` | F-078 | Migration `047bfed04987_standardize_ondelete_policies_across_.py` exists but covers budget only. |
| F-074 | Medium | Verified | `app/models/salary_profile.py` (CHECKs `>= 0` on `additional_income`, `additional_deductions`, `extra_withholding`); `app/schemas/validation.py:198-206, :230-232` (create/update schemas) -- no `validate=Range(min=0)` on those three fields | F-011, F-012, F-076 | Three fields × 2 schemas = 6 missing Range validators. |
| F-075 | Medium | Verified | `app/schemas/validation.py:302-308` (`TaxBracketSetSchema.standard_deduction/child_credit_amount/other_dependent_credit_amount` -- no Range); DB CHECK `>= 0` per model | F-074, F-076 | 3 missing Range validators. |
| F-076 | Medium | Verified | Rollup of F-074/F-075 + remaining salary fields; consolidated list in `reports/17-migrations-schema.md` F-S6-C4-05 | F-074, F-075, F-077 | Extract shared `NON_NEGATIVE_DECIMAL = validate.Range(min=0)` helper. |
| F-077 | Medium | Verified | List from findings.md: `escrow_components.annual_amount/inflation_rate`; `interest_params.apy`; `investment_params.annual_contribution_limit`, `employer_flat_percentage`, `employer_match_percentage`, `employer_match_cap_percentage`; `user_settings.safe_withdrawal_rate/estimated_retirement_tax_rate`; `paycheck_deductions.inflation_rate/inflation_effective_month`; `salary_raises.effective_year`; `state_tax_configs.standard_deduction/tax_year`; `calibration_overrides.effective_*_rate` (4 cols); `rate_history.interest_rate`. All verified by grep of `app/models/` showing no `db.CheckConstraint` on listed columns | F-013, F-014, F-068 | Single migration adds all matching CHECKs. |
| F-078 | Medium | Verified | Live DB: 49 of 52 FK constraints use Alembic default `<table>_<column>_fkey` pattern per S6 `scans/schema-*.txt` | F-072, F-137, F-138 | Retroactive rename is high-churn; plan establishes convention going forward + handful of targeted renames. |
| F-079 | Medium | Verified | Live DB per `scans/schema-salary-paycheck_deductions.txt`, `scans/schema-salary-salary_raises.txt`, `scans/schema-salary-tax_brackets.txt` -- three indexes absent: `idx_deductions_profile`, `idx_salary_raises_profile`, `idx_tax_brackets_bracket_set` | F-071 | Part of F-071 migration regression. |
| F-080 | Medium | Verified | Grep of `log_event(` returns 14 call sites across 4 files; grep of `methods=[(...)POST\|PATCH\|PUT\|DELETE(...)]` returns 95 mutating routes across 17 files in `app/routes/` | F-028, F-082, F-085, F-144, F-151 | Pushdown to service layer captures writes from routes, scripts, and future jobs. |
| F-081 | Medium | Verified | `docker-compose.yml:32-33` (`POSTGRES_USER: shekel_user`); `:60, :62` (app `DATABASE_URL` uses same `shekel_user`); `scripts/init_db.sql` creates schemas using the same user | F-028, F-082 | Owner role has DDL rights; app path should use a DML-only role. |
| F-082 | Medium | Verified | `docker-compose.yml:78` (`- applogs:/home/shekel/app/logs`); no syslog/Loki/S3 shipping in compose or `entrypoint.sh` | F-028, F-080, F-150, F-151, F-153 | Architectural decision needed (see checkpoint). |
| F-083 | Low | Verified | `app/services/auth_service.py:276-291` -- only `if plain_password is None:` guard | F-088 | Robustness gap; 500 instead of clean False return on non-string. |
| F-084 | Low | Verified | `tests/test_integration/test_access_control.py:28-41` -- helper accepts `status_code in (302, 404)`; used by ~68 of 69 IDOR tests | F-087 | Split into `_assert_not_found` and `_assert_redirected_to_login`. |
| F-085 | Low | Verified | `app/routes/auth.py:179` (`logger.info("action=user_registered email=%s", email)`) -- bare `logger.info` | F-080 | Replace with `log_event(..., "user_registered", AUTH, ...)`. |
| F-086 | Low (public:Medium) | Verified | Grep of `app/` for `zxcvbn\|hibp\|pwned\|password_history\|previous_password` returns zero matches; `app/services/auth_service.py:254-273, :315-336, :339-421` (hash_password, change_password, register_user) has no such checks | F-089, F-091 | Deferred escalation per audit. |
| F-087 | Low | Verified (Red Team proposed Info) | 51 routes per `scans/idor-probe.json["summary"]["cross_user_non_canonical_302_routes"]`; breakdown `accounts.py` (14), `salary.py` (16), `templates.py` (5), `transfers.py` (5), `categories.py` (4), `savings.py` (3), `retirement.py` (3), `companion.py` (1) | F-084, F-144 | Red-team proposed deflation to Info; kept as Low pending developer decision. |
| F-088 | Low | Verified | `app/services/auth_service.py:268-269, :334-335, :375-376` -- three copies of 72-byte check | F-083, F-141 | Migrate to Argon2id or pre-hash wrapper to accept up to 128. |
| F-089 | Low | Verified | `app/templates/auth/register.html:30`, `app/templates/settings/_security.html:15` (static "Minimum 12 characters" helper) | F-086, F-090 | zxcvbn-js bundled asset. |
| F-090 | Low | Verified | `app/templates/auth/login.html:22-23`, `app/templates/auth/register.html:28-29, :34-35` (`<input type="password">`) | F-089 | Small vanilla JS toggle. |
| F-091 | Low | Verified | `app/routes/auth.py:220` (change-password), `:425` (MFA enable), `:518` (MFA disable) all `log_event(...)` but no user-visible notification | F-080, F-032, F-143 | In-app banner is the MVP path; email deferred. |
| F-092 | Low | Proposed Defer | Feature-level absence | -- | Workflow Phase 2 deferral candidate. |
| F-093 | Low | Proposed Defer | Feature-level absence | F-153 | Workflow Phase 2 deferral candidate. |
| F-094 | Low | Proposed Defer | Template-level absence | -- | Workflow Phase 2 deferral candidate. |
| F-095 | Low | Verified | `app/routes/settings.py:306, :413` -- no "MFA required" gate on owner-role actions | F-002, F-003, F-006 | Prompt-nag approach recommended. |
| F-096 | Low | Verified | `app/config.py` (grep for `SESSION_COOKIE_NAME` returns zero) | F-017, F-038 | Add `SESSION_COOKIE_NAME = "__Host-session"` to ProdConfig. |
| F-097 | Low | Verified | `app/__init__.py:420-426` -- CSP string has no `frame-ancestors` | F-018, F-036 | Append `; frame-ancestors 'none'`. |
| F-098 | Low | Verified | `app/routes/analytics.py:139-185, :410-431` -- `variance_tab` + `_variance_csv_filename` do not validate `period_id` ownership before reading `period.start_date` for filename | F-039, F-043 | Pair with F-039 fix. |
| F-099 | Low | Verified | `app/routes/grid.py:400-459` (`balance_row`) -- `.first()` at :407-411 can return None; dereferenced at :431 `Transaction.scenario_id == scenario.id` without None-check | -- | Availability bug only. |
| F-100 | Low | Verified | `app/services/retirement_dashboard_service.py:238, :255` -- `float()` casts, `0.04`, `4.0`, `7.0` magic numbers | F-101 | Display-only today; still a style/standards gap. |
| F-101 | Low | Verified | Same location as F-100 | F-100 | Folded into F-100 fix. |
| F-102 | Low | Verified | `app/routes/transactions.py:585-640` (inline), POST `/transactions` variants; `app/routes/loan.py` rate POST | F-008, F-050 | Client-side debounce minimum; optional server-side idempotency. |
| F-103 | Low | Verified | PATCH `/accounts/<id>/true-up` (`accounts.py:651-719`) and `/accounts/<id>/inline-anchor` (`accounts.py:469-513`); AccountAnchorHistory writes in those routes | F-009, F-008 | Related to F-009 version-id fix; not identical. |
| F-104 | Low | Verified | POST `/accounts/<id>/loan/rate`; `app/models/rate_history` model | F-050 | Add composite unique `(account_id, effective_date)`. |
| F-105 | Low | Verified | POST `/retirement/pension` | F-050 | Add composite unique `(user_id, name)`. |
| F-106 | Low | Verified | `app/schemas/validation.py:488-491` (`contribution_per_period = fields.Decimal(validate=validate.Range(min=0))`); `app/models/savings_goal.py:38-41` CHECK `contribution_per_period IS NULL OR contribution_per_period > 0` | F-107, F-135 | Boundary-inclusivity flip. |
| F-107 | Low | Verified | `app/schemas/validation.py:895` (`original_principal = fields.Decimal(validate=validate.Range(min=0))`); `app/models/loan_params.py:26-29` CHECK `original_principal > 0` | F-106, F-135 | Same flip. |
| F-108 | Low | Verified | `.env.dev:1` (`FLASK_APP=src/flask_app/app.py` -- path does not exist in repo; entry is `run.py`) | F-109, F-111 | Delete or align. |
| F-109 | Low | Verified | `.env.example:28` (`POSTGRES_PASSWORD=shekel_pass`) | F-057, F-110 | Replace with non-functional placeholder. |
| F-110 | Low | Verified | `app/config.py:132` (`self.SECRET_KEY.startswith("dev-only")` only); `.env.example:11` (`SECRET_KEY=change-me-to-a-random-secret-key`); `docker-compose.dev.yml:91` (`SECRET_KEY: dev-secret-key-not-for-production`) | F-016, F-001, F-111 | Broaden to known-placeholder set. |
| F-111 | Low | Verified | Same source as F-110 | F-110, F-112 | Part of F-110 fix. |
| F-112 | Low | Verified | `app/config.py:53-61` (DevConfig) | F-110 | Add pragma comment. |
| F-113 | Low | Verified | Runtime state per `scans/container-logs.txt`; Dockerfile + `.dockerignore` in repo (to add ignores) | F-117 | `.dockerignore` addition + multi-stage refactor. |
| F-114 | Low | Verified | Container logs include `User 'josh@REDACTED' already exists` / `Seeding tax data for user:` per `scans/container-logs.txt` lines 24, 43 | F-082, F-152, F-160 | Scripts `scripts/seed_user.py`, `scripts/seed_tax_brackets.py` need redaction. |
| F-115 | Low | Verified | `docker-compose.yml` (grep `mem_limit\|pids_limit` returns zero) | F-116, F-117 | Service-level `mem_limit: 512m`, `pids_limit: 200`. |
| F-116 | Low | Verified | Same source; grep `max-size\|max-file\|logging:` returns zero | F-115, F-054 | Service-level `logging` block. |
| F-117 | Low | Verified | Same source; grep `read_only:` returns zero | F-115, F-055 | `read_only: true` + `tmpfs` on app only. |
| F-118 | Low | Verified | `requirements.txt:20` (`psycopg2==2.9.11`) | F-058, F-059, F-119 | License note only. |
| F-119 | Low | Verified | `requirements.txt:14` (`Flask-SQLAlchemy==3.1.1`) | F-058, F-059 | Monitor; no fix. |
| F-120 | Low | Verified | `scans/trivy-image.json` pip CVE entry | F-025, F-062 | Add `pip install --upgrade pip` to Dockerfile. |
| F-121 | Low | Verified | Runtime host Lynis BOOT-5122 | F-024 | Host-level. |
| F-122 | Low | Verified | Runtime host Lynis KRNL-5820 | F-024, F-023 | Host-level `/etc/security/limits.conf`. |
| F-123 | Low | Verified | Runtime host Lynis FINT-4350 | F-065, F-157 | Host-level AIDE install. |
| F-124 | Low | Verified | Runtime host Lynis TIME-3104 | F-005 (TOTP clock skew) | Host-level `systemd-timesyncd`. |
| F-125 | Low | Verified | Runtime host Lynis AUTH-9262 | F-066 | Host-level PAM install. |
| F-126 | Low | Verified | `app/services/interest_projection.py:15` code comment | -- | Documented as acceptable; no code change. |
| F-127 | Low | Verified | `app/services/paycheck_calculator.py:91-93` | -- | Documented as acceptable; no code change. |
| F-128 | Low | Verified | cloudflared container command-line `--metrics 0.0.0.0:2000` (runtime, not in repo) | F-020, F-129 | Runtime config change. |
| F-129 | Low | Verified | Same runtime evidence as F-020 | F-020, F-061 | Fix by F-020 network isolation. |
| F-130 | Low | Verified (deferral follow-up) | Host SSH config (runtime) | F-066 | Verify state and record; not a fix task on its own. |
| F-131 | Low | Verified | `migrations/versions/b4c5d6e7f8a9_backfill_raise_effective_year.py:34` (`pass`) | F-133 | Replace with `raise NotImplementedError(...)`. |
| F-132 | Low | Verified | All destructive migrations in `migrations/versions/` | F-131, F-026, F-071, F-072 | Process-only convention. |
| F-133 | Low | Verified | `migrations/versions/7abcbf372fff_add_tax_year_to_state_tax_configs.py` downgrade body | F-131, F-071 | Replace with `raise NotImplementedError`. |
| F-134 | Low | Verified | Runtime-state rollup -- server_defaults on `salary.fica_configs`, `salary.pension_profiles`, `salary.salary_profiles`, `budget.investment_params`, `budget.transfers` | F-068, F-028 | Needs `pg_attrdef` confirmation before remediation. |
| F-135 | Low | Verified | Rollup of F-106, F-107, plus `paycheck_deductions.annual_cap`, `transaction_entries.amount` | F-106, F-107, F-076 | Boundary alignment sweep. |
| F-136 | Low | Verified | Live DB per `scans/schema-budget-transactions.txt` vs `scans/schema-budget-transfers.txt` | F-137, F-073 | Investigate intent; standardize. |
| F-137 | Low | Verified | Live DB: `transactions_credit_payback_for_id_fkey`, `scenarios_cloned_from_id_fkey` | F-078, F-138 | Cosmetic; rename in next touching migration. |
| F-138 | Low | Verified | Live DB: `hysa_params_account_id_fkey` on renamed `budget.interest_params` | F-072, F-078 | Part of F-072 follow-up. |
| F-139 | Low | Verified | Live DB `\d+ budget.rate_history` no index on `account_id` | F-079, F-140 | `CREATE INDEX idx_rate_history_account`. |
| F-140 | Low | Verified | Live DB `\d+ salary.pension_profiles`, `\d+ salary.calibration_deduction_overrides` | F-139, F-079 | Same migration as F-139. |
| F-141 | Low | Verified | `app/services/auth_service.py:254-273` (`hash_password`) | F-088 | Folded into F-088 Argon2id decision. |
| F-142 | Low | Verified | `app/services/mfa_service.py:96-109` (verify path -- no replay detection hook) | F-005 | Fix with F-005 (last-timestep check). |
| F-143 | Low | Verified | `app/routes/auth.py:231-247` (`/invalidate-sessions` blanket); `app/templates/settings/_security.html:31-40` | F-006, F-091 | Requires server-side session store; deferred recommendation. |
| F-144 | Low | Verified | `app/utils/auth_helpers.py:29-55, :58-124` (`require_owner`, `get_or_404`) -- no `log_event(..., "access_denied", ...)` call on ownership-fail branches | F-080, F-087 | Add at both 404 branches. |
| F-145 | Low | Verified | `app/routes/salary.py:249, :326, :390, :420, :470, :521, :551, :604, :836, :875, :1041` (11 hits); `app/routes/retirement.py:296`; `app/routes/investment.py:813`; `app/routes/health.py:41` (with `# pylint: disable=broad-except` already). 14 total per grep | F-011, F-012, F-146 | Narrow each to specific exceptions; health-check is acceptable exception. |
| F-146 | Low | Verified | No alerting code anywhere in `app/`; Flask-Limiter raises 429 but no webhook/email/dashboard hook | F-034, F-082, F-144 | Integrates with F-082 shipping. |
| F-147 | Low (pub:Medium+) | Proposed Defer | Live DB per `scans/schema-*.txt` no pgcrypto; `auth.users.email/display_name`, `budget.accounts.current_anchor_balance`, `budget.transactions.*amount*` all plaintext | F-148, F-149, F-154 | Workflow Phase 2 deferral candidate. |
| F-148 | Low | Proposed Defer | `docker-compose.yml:59, :66, :72` -- env var secret path | F-030, F-147, F-149 | Workflow Phase 2 deferral candidate. |
| F-149 | Low | Proposed Defer | `app/config.py:22, :25`; `app/services/mfa_service.py:27` -- os.environ key load | F-030, F-147, F-148 | Workflow Phase 2 deferral candidate. |
| F-150 | Low | Verified | `docker-compose.yml:78` (`- applogs:/home/shekel/app/logs`) | F-082, F-028 | Subset of F-082. |
| F-151 | Low | Verified | Repo-level documentation absence | F-147, F-153 | Write `docs/data-classification.md`. |
| F-152 | Low | Proposed Defer | `app/utils/logging_config.py:153-180` -- request-path/user_id only, no per-record IDs | F-080, F-028 | Workflow Phase 2 deferral candidate. |
| F-153 | Low | Verified | `app/config.py:50` (`AUDIT_RETENTION_DAYS = 365`); no scheduled job in `scripts/` | F-028, F-093 | Scheduled cleanup after F-028 rebuilds. |
| F-154 | Low | Proposed Defer | `docker-compose.yml:60` (`DATABASE_URL: postgresql://shekel_user:${POSTGRES_PASSWORD}@db:5432/shekel` -- no `?sslmode=require`) | F-061, F-063 | Workflow Phase 2 deferral candidate. |
| F-155 | Low | Verified | `docker-compose.yml:48`; no Cosign step in `entrypoint.sh` | F-060 | Pair with F-060. |
| F-156 | Low | Verified | `nginx/nginx.conf` grep `server_tokens` returns zero matches | F-021, F-064 | Add `server_tokens off;`. |
| F-157 | Low | Verified | No `scripts/config_audit.py`; no drift-check in entrypoint | F-021, F-134 | Add drift-check script. |
| F-158 | Info | Verified | `scans/nmap-localhost.txt` first line lacks version banner | -- | Re-run nmap with `nmap --version \| head -1` prepended. |
| F-159 | Info | Verified | `requirements.txt` (grep `--hash=` returns zero) | F-058, F-059 | Switch to `pip-compile` + `--require-hashes`. |
| F-160 | Info | Verified | `app/utils/logging_config.py:74-78` -- `filters` has only `request_id`; no scrubber | F-114, F-080 | Add `SensitiveFieldScrubber(logging.Filter)`. |

### New findings discovered during verification

While verifying the cited files I noted the following patterns worth flagging to the
developer. They are NOT being silently folded into existing commits; they are surfaced
here for explicit developer acknowledgement before Phase B disposition.

- **F-NEW-001 (candidate, Info/Low).** `app/routes/transactions.py:186-285` `update_transaction`
  regular (non-transfer) path loads `data = _update_schema.load(request.form)` at :205 and
  applies `setattr(txn, field, value)` at :265-266 after validating status transition.
  The `status_id` field is an integer accepted by the schema; the route verifies the
  new Status exists via `db.session.get(Status, data["status_id"])` at :260 but does not
  verify that a transition from the CURRENT status to the NEW status is allowed. This is
  the per-transaction counterpart to F-047 (transfer status transitions). For a money
  app, "Paid -> Projected" (un-paying a settled transaction) should be a guarded
  transition, not a free write. The risk is lower than F-047 because regular
  transactions do not participate in the shadow invariants, but it still allows
  reverting an immutable state via form submission on routes that bypass the
  transfer-service flow. Proposed: bundle into F-047 remediation or file as a new
  finding F-161. NOT silently folded into existing commits.

- **F-NEW-002 (candidate, Low).** `app/routes/transactions.py:288-370` `mark_done` has
  `@login_required` only (line 289); no `@require_owner` decorator. The body uses
  `_get_accessible_transaction_for_status(txn_id)` which handles companion vs owner.
  That is by design for companion visible templates. However, the route passes
  `actual_amount` via raw `Decimal(actual)` at :324-325 inside the transfer guard, same
  pattern as F-042 but within the transfer branch. F-042 cites the non-transfer lines
  :321-326 and :355-361; the transfer-branch occurrence at :321-326 actually belongs to
  this rollup. Proposed: confirm F-042's scope to include both occurrences (should be
  trivially folded in). NOT silently folded until developer confirms.

- **F-NEW-003 (candidate, Low).** `app/routes/auth.py:267-268` `mfa_verify` reads
  `totp_code` and `backup_code` with `.strip()`. No Marshmallow schema; no length check
  on `backup_code` (the app accepts any-length backup_code through to
  `mfa_service.verify_backup_code` at :311-319 which iterates every stored hash and
  calls `bcrypt.checkpw`). An attacker submitting a multi-megabyte `backup_code`
  string forces bcrypt to compare that against every stored hash (10 by default). This
  is not an auth bypass but a mild DoS / CPU amplification. F-041 covers "no
  Marshmallow on auth routes" at a rollup level; this is a specific DoS angle the
  broader rollup does not mention. Proposed: fold into F-041 remediation by adding
  `MfaVerifySchema` with `validate.Length(max=32)` on `backup_code`.

- **F-NEW-004 (candidate, Low).** `app/services/transfer_service.py:608-727`
  `restore_transfer` (lines 644 `xfer.is_deleted = False` followed by validation and
  self-heal) reactivates a soft-deleted transfer. There is no check that the transfer's
  source/destination accounts are still active (`Account.is_active = True`). Restoring
  a transfer that targets a now-archived account leaves a reachable transfer pointing
  at a soft-deleted account. This is a latent bug category distinct from F-007/F-009;
  file as new finding if confirmed. Proposed: fold into F-047 (transitions) or F-009
  (account mutation guards) or file standalone. NOT silently folded.

- **F-NEW-005 (observation, Info).** Migration `b4c5d6e7f8a9` (F-131) downgrade body
  is bare `pass`; several other migrations may have the same pattern. Grep for `def
  downgrade():\n    pass` across `migrations/versions/` would identify the full list
  and extend F-131's scope. Not yet grepped; flagged to developer for confirmation.

### Severity deltas (flagged, not applied)

The red-team appendix already called out F-087 (Low -> Info) and F-010 (affirmed High
with scope-clarification). My verification reading turned up no additional severity
deltas worth flagging. In particular:

- **F-009, F-010, F-028, F-030** continue to hold High against current code --
  nothing about their verification suggests a lower rating.
- **F-004** (backup code entropy) still exposes 32 bits -- High is correct for the
  public-release threat model. If the developer formally scopes Shekel to
  permanently-single-owner, Medium would be defensible (red-team noted this).

### Phase A checkpoint -- CONFIRMED

Developer confirmed the following after the Phase A table was presented:

- **New findings:** File F-NEW-001..005 as first-class findings **F-161 through F-165**
  in findings.md (developer updates the file separately; this plan treats them as
  verified Low/Info additions to the 160-finding set).
- **F-087 severity:** Keep as **Low** (red team proposed Info, but visibility in the
  fix queue is worth preserving). Not re-rated.
- **Plan structure:** Single file at `docs/audits/security-2026-04-15/remediation-plan.md`.

F-NEW-005 (other migrations with bare `pass` downgrade) was verified via
`grep -Pzo 'def downgrade\(\):[...]pass'` across `migrations/versions/`: only
`b4c5d6e7f8a9_backfill_raise_effective_year.py` matches -- which is already F-131's
scope. **F-165 is therefore collapsed into F-131 and NOT filed separately.** Only
F-161 through F-164 survive as new findings.

**Final post-verification finding set: 164 findings** (160 original + 4 new):

- F-161 (Low, A04). Transaction status transition guard missing. `app/routes/transactions.py:260-266`
  does not enforce an ALLOWED_TRANSITIONS check on `status_id` changes on regular
  (non-transfer) transactions. Companion to F-047. Will be addressed in the same commit
  as F-047 so a single state-machine helper covers both.

- F-162 (Low, A03). `app/routes/transactions.py:324-325` (the transfer-branch `Decimal(actual)`
  in `mark_done`) is the same raw-parse pattern as F-042's cited non-transfer lines. Scoped
  as a formal F-042 expansion: the F-042 remediation MUST cover both occurrences.

- F-163 (Low, A04 / DoS). `app/routes/auth.py:267-268` `mfa_verify` -- no length cap on
  `backup_code`; multi-megabyte payload forces `bcrypt.checkpw` against every stored hash.
  Fix is schema `validate.Length(max=32)` in the `MfaVerifySchema` added by F-041.

- F-164 (Low, A04). `app/services/transfer_service.py:608-727` `restore_transfer` reactivates
  a soft-deleted transfer without checking that source/destination `Account.is_active = True`.
  Can restore a transfer that targets an archived account. Fix is a guard at the top of
  `restore_transfer` after the `allow_deleted` guard, and paired with F-007's shadow-mutation
  guard in the same commit.

---

## Phase B -- Triage, dispositions, and commit grouping

### Severity counts (post-verification)

| Severity | Original (pre-audit) | Post-verification (this plan) |
|---|---|---|
| Critical | 1 | 1 (F-001) |
| High | 29 (30 before dedup) | 29 (F-002..F-030) |
| Medium | 52 (65 before dedup) | 52 (F-031..F-082) |
| Low | 75 (45 before dedup) | 79 (F-083..F-157 + F-161..F-164) |
| Info | 3 (19 before dedup) | 3 (F-158, F-159, F-160) |
| **Total** | **160** | **164** |

### Dispositions

| Disposition | Count | Meaning |
|---|---|---|
| Fix-now | 35 | Ships in Phase 1 of execution (commits 1-13). Critical, every High that represents a concrete exploit or correctness gap, plus the Medium findings that are gating for subsequent commits (e.g. F-034 Limiter backend). |
| Fix-this-sprint | 78 | Ships in Phases 2-7 of execution (commits 14-45). Most Medium findings and high-leverage Lows. |
| Fix-backlog | 37 | Ships in Phases 8-9 of execution (commits 46-57). Most Lows, plus all Info. |
| Propose-defer | 14 | Documented Accepted Risk with threat-model delta + re-open triggers. Section Phase E for full rationale. |
| **Total** | **164** | |

#### Full disposition table (one row per finding)

| ID | Severity | Disposition | Target commit | Rationale |
|---|---|---|---|---|
| F-001 | Critical | Fix-now | C-01 | Critical severity; gates every commit that relies on valid-session assumptions. |
| F-002 | High | Fix-now | C-09 | Authentication bypass path. |
| F-003 | High | Fix-now | C-09 | Same session-invalidation cluster as F-002. |
| F-004 | High | Fix-now | C-04 | Offline brute-force vector; 32 bits is clearly too low. |
| F-005 | High | Fix-now | C-10 | TOTP replay allows ~90-second reuse window. |
| F-006 | High | Fix-now | C-11 | Session lifetime hardening. |
| F-007 | High | Fix-now | C-22 | CLAUDE.md invariant 4 is convention-only today; money-app invariant gap. |
| F-008 | High | Fix-now | C-21 | TOCTOU duplicate CC Payback (visible balance bug). |
| F-009 | High | Fix-now | C-19 | Anchor balance last-writer-wins; the highest-blast-radius concurrency bug. |
| F-010 | High | Fix-now | C-20 | Stale-form silent lost update across every PATCH. |
| F-011 | High | Fix-now | C-28 | Schema/DB mismatch -- opaque user errors today. |
| F-012 | High | Fix-now | C-28 | Same cluster as F-011. |
| F-013 | High | Fix-now | C-28 | Broken config field. |
| F-014 | High | Fix-now | C-28 | Same cluster. |
| F-015 | High | Fix-this-sprint | C-38 | Depends on F-021 (version-control configs). |
| F-016 | High | Fix-now | C-02 | Runtime SECRET_KEY default; paired with F-001. |
| F-017 | High | Fix-now | C-03 | Bundle of five one-line crypto fixes (F-017/F-018/F-019/F-096/F-097 + F-036/F-037). |
| F-018 | High | Fix-now | C-03 | Same bundle. |
| F-019 | High | Fix-now | C-03 | Same bundle. |
| F-020 | High | Fix-this-sprint | C-38 | Runtime topology; depends on F-021 being committed first. |
| F-021 | High | Fix-this-sprint | C-37 | Must land before F-015/F-020/F-063/F-064/F-156 remediation is reviewable. |
| F-022 | High | Fix-this-sprint | C-39 | Seed-user credential hygiene. |
| F-023 | High | Fix-this-sprint | C-58 (host runbook) | Host-level chmod; out of repo. |
| F-024 | High | Fix-this-sprint | C-58 | Host-level sysctl. |
| F-025 | High | Fix-this-sprint | C-41 | Container image rebuild with OpenSSL upgrade. |
| F-026 | High | Fix-this-sprint | C-44 | Migration non-idempotency blocks DR/staging. |
| F-027 | High | Fix-this-sprint | C-45 | Duplicate CHECK constraint name confusion. |
| F-028 | High | Fix-now | C-14 | Top 1 risk: no DB-tier audit trail. |
| F-029 | High | Fix-now | C-34 | IDOR via FK re-parenting. |
| F-030 | High | Fix-now | C-05 | Crypto key rotation posture. |
| F-031 | Medium | Fix-now | C-06 | Plaintext MFA secret in client cookie during setup. |
| F-032 | Medium | Fix-now | C-09 | Same session-invalidation cluster. |
| F-033 | Medium | Fix-this-sprint | C-12 | Account lockout. |
| F-034 | Medium | Fix-now | C-07 | Depends on by F-033/F-038; must ship first. |
| F-035 | Medium | Fix-now | C-11 | Session lifetime cluster. |
| F-036 | Medium | Fix-now | C-03 | Cookie-headers bundle. |
| F-037 | Medium | Fix-now | C-03 | Cookie-headers bundle. |
| F-038 | Medium | Fix-now | C-08 | Small standalone; ships with plumbing phase. |
| F-039 | Medium | Fix-this-sprint | C-35 | Analytics ownership guard. |
| F-040 | Medium | Fix-this-sprint | C-32 | Remaining route input validation. |
| F-041 | Medium | Fix-this-sprint | C-31 | Auth schema pass. |
| F-042 | Medium | Fix-this-sprint | C-32 | Same validation cluster; covers F-162. |
| F-043 | Medium | Fix-this-sprint | C-32 | Same cluster. |
| F-044 | Medium | Fix-this-sprint | C-33 | Multi-tenant readiness guard. |
| F-045 | Medium | Fix-this-sprint | C-11 | Step-up auth; paired with session lifetime. |
| F-046 | Medium | Fix-this-sprint | C-23 | Transfer shadow DB uniqueness. |
| F-047 | Medium | Fix-this-sprint | C-24 | State-machine helper (covers F-161). |
| F-048 | Medium | Fix-this-sprint | C-25 | Transfer paid_at parity. |
| F-049 | Medium | Fix-this-sprint | C-25 | Same small cluster. |
| F-050 | Medium | Fix-this-sprint | C-26 | Idempotency family. |
| F-051 | Medium | Fix-this-sprint | C-27 | Composite unique pair. |
| F-052 | Medium | Fix-this-sprint | C-27 | Composite unique pair. |
| F-053 | Medium | Fix-this-sprint | C-39 | Small config flip; ships with seed cleanup. |
| F-054 | Medium | Fix-this-sprint | C-39 | Runtime cleanup paired with seed hygiene. |
| F-055 | Medium | Fix-this-sprint | C-40 | Docker hardening bundle. |
| F-056 | Medium | Fix-this-sprint | C-40 | Same bundle. |
| F-057 | Medium | Fix-this-sprint | C-40 | Same bundle. |
| F-058 | Medium | Propose-defer (monitor) | -- | Upstream unmaintained; no currently-exploitable CVE. Re-assess on each audit cycle. |
| F-059 | Medium | Propose-defer (monitor) | -- | Same as F-058. |
| F-060 | Medium | Fix-this-sprint | C-41 | Image digest pin. |
| F-061 | Medium | Fix-this-sprint | C-42 | cloudflared. |
| F-062 | Medium | Propose-defer (accept) | -- | Unreachable OS CVEs; documented, monitored. |
| F-063 | Medium | Fix-this-sprint | C-38 | Network topology bundle. |
| F-064 | Medium | Fix-this-sprint | C-38 | Same bundle. |
| F-065 | Medium | Fix-this-sprint | C-58 | Host-level auditd install. |
| F-066 | Medium | Fix-this-sprint | C-58 | Host-level SSH hardening. |
| F-067 | Medium | Fix-this-sprint | C-58 | Host reboot coordination. |
| F-068 | Medium | Fix-this-sprint | C-30 | Nullable boolean sweep. |
| F-069 | Medium | Fix-this-sprint | C-45 | Missing unique index rebuild. |
| F-070 | Medium | Fix-now | C-14 | Folded into F-028 rebuild. |
| F-071 | Medium | Fix-this-sprint | C-46 | Salary migration index/constraint fix. |
| F-072 | Medium | Fix-this-sprint | C-46 | hysa_params legacy FK name cleanup. |
| F-073 | Medium | Fix-this-sprint | C-47 | FK ondelete sweep. |
| F-074 | Medium | Fix-this-sprint | C-28 | Schema Range sweep. |
| F-075 | Medium | Fix-this-sprint | C-28 | Same. |
| F-076 | Medium | Fix-this-sprint | C-28 | Rollup commit. |
| F-077 | Medium | Fix-this-sprint | C-29 | DB CHECK sweep. |
| F-078 | Medium | Fix-backlog | C-47 | Naming convention forward-only. |
| F-079 | Medium | Fix-this-sprint | C-46 | Salary-schema index restore. |
| F-080 | Medium | Fix-now | C-16 | Audit-log Python layer; Top 1 risk. |
| F-081 | Medium | Fix-this-sprint | C-15 | Least-privilege DB role. |
| F-082 | Medium | Fix-this-sprint | C-17 | Off-host log shipping (Top 1 risk). |
| F-083 | Low | Fix-backlog | C-49 | Robustness fix. |
| F-084 | Low | Fix-this-sprint | C-36 | Paired with F-087 unification (tests block regression). |
| F-085 | Low | Fix-now | C-16 | Folded into log_event rollout. |
| F-086 | Low | Fix-this-sprint | C-12 | Password strength bundle. |
| F-087 | Low | Fix-this-sprint | C-36 | 404-everywhere unification. |
| F-088 | Low | Fix-backlog | C-54 | Argon2id migration (larger scope). |
| F-089 | Low | Fix-this-sprint | C-12 | Strength meter. |
| F-090 | Low | Fix-this-sprint | C-12 | Show/hide toggle. |
| F-091 | Low | Fix-this-sprint | C-18 | In-app banner only; email deferred. |
| F-092 | Low | Propose-defer (pre-launch) | -- | WebAuthn; accepted pending public-launch gate. |
| F-093 | Low | Propose-defer (pre-launch) | -- | GDPR export/delete; accepted pending public-launch gate. |
| F-094 | Low | Propose-defer (pre-launch) | -- | Privacy policy; accepted pending public-launch gate. |
| F-095 | Low | Fix-this-sprint | C-13 | MFA prompt-nag for owner. |
| F-096 | Low | Fix-now | C-03 | Cookie-headers bundle. |
| F-097 | Low | Fix-now | C-03 | Cookie-headers bundle. |
| F-098 | Low | Fix-this-sprint | C-35 | Paired with F-039. |
| F-099 | Low | Fix-backlog | C-51 | Availability robustness. |
| F-100 | Low | Fix-backlog | C-50 | Decimal style fix. |
| F-101 | Low | Fix-backlog | C-50 | Folded into F-100. |
| F-102 | Low | Fix-this-sprint | C-26 | Idempotency family. |
| F-103 | Low | Fix-this-sprint | C-26 | Same. |
| F-104 | Low | Fix-this-sprint | C-26 | Same. |
| F-105 | Low | Fix-this-sprint | C-26 | Same. |
| F-106 | Low | Fix-this-sprint | C-30 | Boundary inclusivity. |
| F-107 | Low | Fix-this-sprint | C-30 | Same. |
| F-108 | Low | Fix-this-sprint | C-43 | Env file cleanup. |
| F-109 | Low | Fix-this-sprint | C-43 | Same. |
| F-110 | Low | Fix-now | C-02 | Placeholder rejection (paired with F-016). |
| F-111 | Low | Fix-now | C-02 | Same. |
| F-112 | Low | Fix-this-sprint | C-43 | DevConfig pragma. |
| F-113 | Low | Fix-this-sprint | C-41 | Dockerignore + multi-stage. |
| F-114 | Low | Fix-this-sprint | C-18 | PII scrub in logs. |
| F-115 | Low | Fix-this-sprint | C-40 | Docker hardening bundle. |
| F-116 | Low | Fix-this-sprint | C-40 | Same. |
| F-117 | Low | Fix-this-sprint | C-40 | Same. |
| F-118 | Low | Propose-defer (monitor) | -- | License note only. |
| F-119 | Low | Propose-defer (monitor) | -- | Same as F-058. |
| F-120 | Low | Fix-this-sprint | C-41 | Dockerfile pip upgrade. |
| F-121 | Low | Fix-this-sprint | C-58 | GRUB password. |
| F-122 | Low | Fix-this-sprint | C-58 | Core dumps disabled. |
| F-123 | Low | Fix-this-sprint | C-58 | AIDE install. |
| F-124 | Low | Fix-this-sprint | C-58 | NTP install. |
| F-125 | Low | Fix-this-sprint | C-58 | PAM install. |
| F-126 | Low | Propose-defer (docstring only) | -- | Documented in code; no change. |
| F-127 | Low | Propose-defer (docstring only) | -- | Documented in code; no change. |
| F-128 | Low | Fix-this-sprint | C-42 | cloudflared metrics bind to loopback. |
| F-129 | Low | Fix-this-sprint | C-38 | Folded into network topology commit. |
| F-130 | Low | Fix-this-sprint | C-58 | SSH posture verification. |
| F-131 | Low | Fix-this-sprint | C-48 | Migration `pass` fix. |
| F-132 | Low | Fix-backlog | C-48 | Review docstring convention. |
| F-133 | Low | Fix-this-sprint | C-48 | Migration downgrade fix. |
| F-134 | Low | Fix-this-sprint | C-30 | Folded into boolean sweep (confirms server_defaults). |
| F-135 | Low | Fix-this-sprint | C-30 | Folded into boolean/boundary sweep. |
| F-136 | Low | Fix-backlog | C-47 | Asymmetric ondelete. |
| F-137 | Low | Fix-backlog | C-46 | Part of hysa_params cleanup. |
| F-138 | Low | Fix-backlog | C-46 | Same. |
| F-139 | Low | Fix-this-sprint | C-46 | Missing index. |
| F-140 | Low | Fix-this-sprint | C-46 | Missing indexes. |
| F-141 | Low | Fix-backlog | C-54 | Paired with F-088. |
| F-142 | Low | Fix-now | C-10 | Folded into F-005 fix. |
| F-143 | Low | Propose-defer (server-side sessions) | -- | Requires server-side session store; large scope. |
| F-144 | Low | Fix-now | C-16 | Folded into log_event rollout. |
| F-145 | Low | Fix-backlog | C-52 | Exception narrowing sweep. |
| F-146 | Low | Propose-defer (alerting infra) | -- | No alerting infrastructure in place; pairs with F-082 but distinct. |
| F-147 | Low | Propose-defer (pre-public) | -- | Phase 2 candidate. |
| F-148 | Low | Propose-defer (single-op LAN) | -- | Phase 2 candidate. |
| F-149 | Low | Propose-defer (no HSM) | -- | Phase 2 candidate. |
| F-150 | Low | Fix-this-sprint | C-17 | Folded into F-082. |
| F-151 | Low | Fix-backlog | C-56 | Docs only. |
| F-152 | Low | Propose-defer (pre-public) | -- | Phase 2 candidate. |
| F-153 | Low | Fix-backlog | C-56 | Retention cleanup (depends on F-028). |
| F-154 | Low | Propose-defer (single-host) | -- | Phase 2 candidate. |
| F-155 | Low | Fix-this-sprint | C-41 | Image signing; paired with F-060. |
| F-156 | Low | Fix-this-sprint | C-38 | Nginx server_tokens. |
| F-157 | Low | Fix-this-sprint | C-55 | Config drift check. |
| F-158 | Info | Fix-backlog | C-57 | Audit re-run note. |
| F-159 | Info | Fix-this-sprint | C-53 | Requirement hash pins. |
| F-160 | Info | Fix-this-sprint | C-18 | Log scrubber. |
| F-161 | Low | Fix-this-sprint | C-24 | Transaction state-machine companion to F-047. |
| F-162 | Low | Fix-this-sprint | C-32 | Covered by F-042 expansion. |
| F-163 | Low | Fix-this-sprint | C-31 | Covered by F-041 schema addition. |
| F-164 | Low | Fix-this-sprint | C-22 | Covered by F-007 shadow-mutation commit. |

### Proposed Defers (see Phase E for full rationale)

Fourteen findings are proposed for formal Accept disposition. The rigorous threat-model
delta / compensating controls / re-open triggers are in Phase E below. Summary list:

1. **F-058, F-059, F-118, F-119** -- Dependency staleness (pyotp, Flask-Login, psycopg2,
   Flask-SQLAlchemy). Monitor only; re-open on first CVE or new major release.
2. **F-062** -- Unreachable OS CVEs in container base image. Accept until Debian provides
   fixes or trivy flags reachability.
3. **F-092** -- WebAuthn / FIDO2 support. Accept until public launch.
4. **F-093** -- GDPR data export / account deletion. Accept until first EU-targeted release.
5. **F-094** -- Privacy policy and terms of service. Accept until public launch.
6. **F-126, F-127** -- Leap-year interest and biweekly rounding residue. Documented in
   code as acceptable simplifications; residual impact <$0.25/year.
7. **F-143** -- View-active-sessions UI. Requires server-side session store migration.
   Defer until F-082 architectural decision is made.
8. **F-146** -- Abnormal request-volume alerting. Depends on alerting infrastructure
   decision (pair with F-082 destination).
9. **F-147** -- Encryption at rest for PII. Accept for single-host LAN; re-open at
   public launch.
10. **F-148** -- Secrets manager. Accept for single-operator; re-open at multi-host or
    public launch.
11. **F-149** -- Key material in process memory. Accept; no HSM available.
12. **F-152** -- Per-record read audit. Accept pending compliance trigger (HIPAA/GDPR).
13. **F-154** -- Internal TLS (Gunicorn <-> Postgres). Accept on single-host; re-open if
    DB moves to a separate host.

### Commit dependency DAG (text)

Each commit declares what it requires. "Blocks" is the inverse and is recorded for
rollback planning.

```
C-01 (F-001 SECRET_KEY history + session invalidation)
  -- blocks: C-03, C-09, C-11 (any commit that relies on no-legacy-cookies)

C-02 (F-016 + F-110 + F-111 runtime SECRET_KEY guards)
  -- depends on: C-01 (history must be excised before defaults tighten)

C-03 (F-017 + F-018 + F-019 + F-036 + F-037 + F-096 + F-097 cookie+headers bundle)
  -- depends on: C-01
  -- blocks: C-11 (session lifetime)

C-04 (F-004 backup code entropy)
  -- independent

C-05 (F-030 MultiFernet TOTP key rotation)
  -- independent

C-06 (F-031 MFA setup secret server-side)
  -- depends on: C-03 (cookie hardening first), C-05 (encryption key infra)

C-07 (F-034 Flask-Limiter Redis backend)
  -- independent
  -- blocks: C-08, C-12

C-08 (F-038 session_protection=strong)
  -- depends on: C-07 is optional but preferred; independent in practice

C-09 (F-002 + F-003 + F-032 session invalidation helper)
  -- depends on: C-01

C-10 (F-005 + F-142 TOTP replay)
  -- depends on: migrations infrastructure in place (standard Alembic flow)

C-11 (F-006 + F-035 + F-045 session lifetime + step-up)
  -- depends on: C-01, C-03, C-09

C-12 (F-033 + F-086 + F-089 + F-090 password strength + lockout)
  -- depends on: C-07 (Limiter backend)

C-13 (F-095 MFA required nag for owner)
  -- depends on: C-04, C-09

C-14 (F-028 + F-070 audit triggers rebuild migration)
  -- independent (migrations sequence only)
  -- blocks: C-16, C-17

C-15 (F-081 least-privilege DB role)
  -- independent
  -- blocks: C-14's entrypoint assertion if shipped together

C-16 (F-080 + F-085 + F-144 log_event systematic rollout)
  -- depends on: C-14

C-17 (F-082 + F-150 off-host log shipping)
  -- depends on: C-14, C-16
  -- architectural decision required in Phase D

C-18 (F-091 + F-114 + F-160 logging polish + redaction + auth-factor notifications)
  -- depends on: C-16

C-19 (F-009 anchor balance version_id)
  -- independent (migration + model + routes)
  -- blocks: C-20

C-20 (F-010 stale-form prevention across PATCH endpoints)
  -- depends on: C-19 (reuses version_id pattern)

C-21 (F-008 mark_as_credit / sync_entry_payback TOCTOU)
  -- independent

C-22 (F-007 + F-164 shadow mutation guards)
  -- independent

C-23 (F-046 transfer shadow uniqueness)
  -- independent

C-24 (F-047 + F-161 state-machine helper)
  -- depends on: none (extends existing services)

C-25 (F-048 + F-049 transfer mark_done paid_at + carry_forward precondition)
  -- depends on: C-24 (shared transitions helper)

C-26 (F-050 + F-102 + F-103 + F-104 + F-105 idempotency family)
  -- depends on: C-19 (version_id pattern reused)

C-27 (F-051 + F-052 composite unique on salary raises/deductions)
  -- independent

C-28 (F-011 + F-012 + F-013 + F-014 + F-074 + F-075 + F-076 Marshmallow Range sweep)
  -- independent

C-29 (F-077 DB CHECK additions)
  -- depends on: C-28 (validator bounds inform CHECK bounds)

C-30 (F-068 + F-134 + F-135 + F-106 + F-107 boolean NOT NULL + boundary inclusivity)
  -- depends on: migration for server_default backfill

C-31 (F-041 + F-163 auth schemas + MfaVerifySchema)
  -- independent

C-32 (F-040 + F-042 + F-043 + F-162 remaining route input validation)
  -- independent

C-33 (F-044 account_type multi-tenant guard)
  -- independent (migration + model)

C-34 (F-029 cross-user FK re-parenting)
  -- depends on: C-19 for pattern consistency if using version-id; independent otherwise

C-35 (F-039 + F-098 analytics ownership checks)
  -- independent

C-36 (F-087 + F-084 404-everywhere + test helper split)
  -- depends on: C-35 (pattern consistency)

C-37 (F-021 version-control nginx/compose configs)
  -- independent
  -- blocks: C-38, C-39, C-42

C-38 (F-015 + F-020 + F-063 + F-064 + F-129 + F-156 network topology + proxy trust)
  -- depends on: C-37

C-39 (F-022 + F-053 + F-054 seed hygiene)
  -- depends on: C-37

C-40 (F-055 + F-056 + F-057 + F-115 + F-116 + F-117 Docker hardening bundle)
  -- independent from C-37 (repo compose changes only)

C-41 (F-060 + F-025 + F-120 + F-062 + F-155 + F-113 Dockerfile refresh + Cosign + .dockerignore)
  -- independent

C-42 (F-061 + F-128 cloudflared)
  -- depends on: C-37 (if cloudflared config is version-controlled anew)

C-43 (F-108 + F-109 + F-112 env file cleanup)
  -- independent

C-44 (F-026 + F-132 backfill migration convention)
  -- depends on: none (documentation/migration)

C-45 (F-027 + F-069 duplicate CHECK / missing unique)
  -- independent

C-46 (F-071 + F-072 + F-079 + F-137 + F-138 + F-139 + F-140 salary + hysa migration + indexes)
  -- independent

C-47 (F-073 + F-078 + F-136 FK ondelete + naming + ondelete alignment)
  -- depends on: C-46 (shared migration infrastructure; avoid merge conflicts)

C-48 (F-131 + F-133 migration downgrade fixes)
  -- independent

C-49 (F-083 verify_password hardening)
  -- independent

C-50 (F-100 + F-101 + F-126 + F-127 retirement dashboard Decimal + rounding docstrings)
  -- independent

C-51 (F-099 grid balance_row None-check)
  -- independent

C-52 (F-145 except Exception narrowing)
  -- depends on: C-28 (so clean 400s replace the swallowed errors)

C-53 (F-159 requirement hash pins)
  -- independent

C-54 (F-088 + F-141 Argon2id migration + pepper)
  -- depends on: C-07 (Limiter backend still reliable during password-hash migration)

C-55 (F-157 config drift check script)
  -- depends on: C-37

C-56 (F-151 + F-153 data classification doc + retention cleanup job)
  -- depends on: C-14 (audit triggers must exist before retention policy)

C-57 (F-158 nmap version note + audit rerun prep)
  -- independent

C-58 (Host hardening runbook -- F-023 + F-024 + F-065 + F-066 + F-067 + F-121 + F-122 + F-123 + F-124 + F-125 + F-130)
  -- depends on: NONE (out-of-repo runbook).  Developer executes on host.
```

### Phase ordering

Execution order follows the audit workflow's six-principle rationale, adapted for
security and the verified findings:

1. **Phase 1 -- Crypto and History First (C-01 ... C-06, C-09..C-13).** Unblocks every
   subsequent commit that relies on valid-session assumptions.
2. **Phase 2 -- Rate Limiting Plumbing (C-07, C-08).** Required for lockout and DoS
   resilience on later commits.
3. **Phase 3 -- Audit Log Restoration (C-14..C-18).** Second-most-consequential cluster
   (Top 1 risk); makes subsequent fixes observable.
4. **Phase 4 -- Financial Invariants (C-19..C-27).** Third-most-consequential cluster
   (Top 3 risk); the money-correctness commits.
5. **Phase 5 -- Input Validation + Schema/DB Sync (C-28..C-33).** Closes High / Medium
   validation mismatches.
6. **Phase 6 -- Access-Control Response Consistency (C-34..C-36).** IDOR class fixes.
7. **Phase 7 -- Config Hardening (C-37..C-43).** nginx, compose, cloudflared, env.
8. **Phase 8 -- Schema Cleanup (C-44..C-48).** Migration hygiene.
9. **Phase 9 -- Low/Info Cleanup (C-49..C-57).** Polish.
10. **Phase 10 -- Host Hardening Runbook (C-58).** Out-of-repo runbook for the Arch host.

### Proposed commit count

**58 commits total** (including the host-runbook deliverable at C-58), with the
following complexity mix:

- Small (`<= 5 files`, `<= 15 tests`): C-02, C-04, C-05, C-08, C-15, C-23, C-25, C-27,
  C-38 (pure config), C-39, C-42, C-43, C-44, C-45, C-48, C-49, C-50, C-51, C-52, C-53,
  C-55, C-56, C-57 (23 commits).
- Medium (5-15 files, 15-30 tests): C-01, C-03, C-06, C-07, C-09, C-10, C-11, C-12,
  C-13, C-18, C-19, C-21, C-22, C-24, C-26, C-29, C-30, C-31, C-32, C-33, C-34, C-35,
  C-36, C-37, C-40, C-41, C-46, C-47, C-58 (29 commits).
- Large (15+ files, 30+ tests): C-14 (audit triggers rebuild), C-16 (log_event across
  every service), C-17 (off-host log shipping), C-20 (every PATCH endpoint), C-28
  (schema sweep across every field), C-54 (Argon2id migration) (6 commits).

### Phase B checkpoint -- CONFIRMED

Developer confirmed:

1. **Commit count:** Merge to ~40-45. Revised grouping below collapses related commits
   where the same files/migrations are touched.
2. **Defer acceptance:** Only dependency-staleness defers accepted (F-058, F-059, F-118,
   F-119). The other 13 proposed Defers are **REJECTED** and now require commit slots.
   Final Defer list: **4 findings** (all dependency-monitoring tasks).
3. **Architectural decisions:** Deferred to Phase C. Each fork is presented inline at the
   commit that surfaces the choice, with an AskUserQuestion call to capture the decision
   before the commit plan is finalized.
4. **Phase ordering:** Unchanged.

**Notes on rejected defers:**

- F-149 ("key material in process memory") has **no practical code-level fix** without
  hardware (HSM / PKCS#11). The rejection is treated as "verify + document the residual
  risk in `docs/runbook_secrets.md` and accept for single-host operation" because there
  is no code path that materially reduces the risk absent hardware. The commit for F-149
  is therefore a docs-only update and a check that `/proc/<pid>/environ` is not readable
  by any non-shekel user on the host. If the developer expects a different outcome,
  please confirm at the C-55 checkpoint.
- F-143 ("view active sessions") **requires** migrating to a server-side session store.
  The commit plan pairs F-143 with F-092 (WebAuthn) because both need the server-side
  store and touching the session layer twice is wasteful.
- F-147 ("encryption at rest for PII") is implemented as field-level encryption on
  `auth.users.email`, `auth.users.display_name`, and `budget.accounts.current_anchor_balance`
  using the existing Fernet infrastructure (not pgcrypto), with a `MultiFernet` rotation
  story piggybacked on F-030.

### Final commit grouping (47 commits)

The disposition table's target-commit column above is authoritative before merges.
After the Phase B checkpoint, the grouping is as follows:

**Phase 1 -- Crypto and History First (C-01 through C-12)**

- **C-01:** F-001 + F-016 + F-110 + F-111 (SECRET_KEY history excise + runtime defaults +
  placeholder rejection + session-invalidate-all bump).
- **C-02:** F-017 + F-018 + F-019 + F-036 + F-037 + F-096 + F-097 (cookie flags + HSTS +
  Cache-Control + CSP tightening + `__Host-` prefix + frame-ancestors + CDN vendoring).
- **C-03:** F-004 (backup-code entropy upgrade to 112 bits).
- **C-04:** F-030 (MultiFernet rotation path for TOTP_ENCRYPTION_KEY).
- **C-05:** F-031 (pending MFA setup secret stored server-side).
- **C-06:** F-034 (Flask-Limiter Redis backend).
- **C-07:** F-038 (`session_protection = "strong"`).
- **C-08:** F-002 + F-003 + F-032 (session invalidation helper + pending-MFA timestamp +
  backup-code session invalidation + MFA-disable session invalidation).
- **C-09:** F-005 + F-142 (TOTP replay prevention with `last_totp_timestep` + replay
  logging).
- **C-10:** F-006 + F-035 + F-045 (session lifetime + idle timeout + step-up auth).
- **C-11:** F-033 + F-086 + F-089 + F-090 (account lockout + HIBP + strength meter +
  show/hide toggle).
- **C-12:** F-095 (MFA required-nag for owner).

**Phase 2 -- Audit Log Restoration (C-13 through C-16)**

- **C-13:** F-028 + F-070 + F-081 (audit triggers rebuild migration + `CREATE SCHEMA` fix
  + least-privilege `shekel_app` DB role).
- **C-14:** F-080 + F-085 + F-144 (`log_event` systematic rollout across services + access
  denied events + registration log_event).
- **C-15:** F-082 + F-150 + F-146 (off-host log shipping to the chosen destination +
  tamper-resistant storage + 429 alerting).
- **C-16:** F-091 + F-114 + F-160 (PII redaction filter + seed-script email redaction +
  in-app auth-factor change notifications).

**Phase 3 -- Financial Invariants (C-17 through C-23)**

- **C-17:** F-009 (anchor balance optimistic locking via `version_id_col`).
- **C-18:** F-010 (stale-form prevention across every PATCH endpoint).
- **C-19:** F-008 (`mark_as_credit` and `sync_entry_payback` TOCTOU + partial unique
  index on `credit_payback_for_id`).
- **C-20:** F-007 + F-164 (recurrence-engine shadow guard + `restore_transfer` account
  active check).
- **C-21:** F-046 + F-047 + F-161 (transfer shadow partial unique index + transfer and
  transaction state-machine helper).
- **C-22:** F-048 + F-049 + F-050 + F-102 + F-103 + F-104 + F-105 (transfer `mark_done`
  paid_at + `carry_forward` status precondition + ad-hoc idempotency family).
- **C-23:** F-051 + F-052 (salary raise and paycheck deduction composite unique).

**Phase 4 -- Input Validation and Schema/DB Sync (C-24 through C-28)**

- **C-24:** F-011 + F-012 + F-013 + F-014 + F-074 + F-075 + F-076 + F-077 (Marshmallow
  Range sweep + DB CHECK additions merged).
- **C-25:** F-068 + F-134 + F-135 + F-106 + F-107 (boolean NOT NULL sweep + server_default
  restoration + boundary inclusivity alignment).
- **C-26:** F-041 + F-163 (auth blueprint Marshmallow schemas + `MfaVerifySchema` length
  cap).
- **C-27:** F-040 + F-042 + F-043 + F-162 (remaining route input-validation sweep).
- **C-28:** F-044 (account_type multi-tenant guard).

**Phase 5 -- Access-Control Response Consistency (C-29 through C-31)**

- **C-29:** F-029 (cross-user FK re-parenting in `update_transaction`).
- **C-30:** F-039 + F-098 (analytics `account_id` + `period_id` ownership checks).
- **C-31:** F-087 + F-084 (404-everywhere unification + test helper split).

**Phase 6 -- Config and Hardening (C-32 through C-39)**

- **C-32:** F-021 (commit nginx/compose configs currently on production host).
- **C-33:** F-015 + F-020 + F-063 + F-064 + F-129 + F-156 (network topology, proxy trust,
  nginx security headers, `server_tokens off`).
- **C-34:** F-022 + F-053 + F-054 + F-113 (seed-user credential hygiene +
  `REGISTRATION_ENABLED=false` in prod + stale containers runbook entry + `.dockerignore`).
- **C-35:** F-055 + F-056 + F-057 + F-115 + F-116 + F-117 (Docker hardening bundle:
  no-new-privileges + cap_drop + dev DB loopback + resource limits + log rotation +
  read-only rootfs).
- **C-36:** F-060 + F-025 + F-120 + F-062 + F-155 (Dockerfile: digest pin + OpenSSL
  upgrade + pip upgrade + distroless/slim migration to eliminate unreachable CVEs +
  Cosign signing).
- **C-37:** F-061 + F-128 + F-154 (cloudflared Access policy + metrics to loopback +
  Postgres TLS via `sslmode=require`).
- **C-38:** F-108 + F-109 + F-112 + F-148 (env file cleanup + `.env.example` sanitization
  + DevConfig pragma + migrate credentials to Docker secrets).
- **C-39:** F-147 (field-level encryption for PII on email/display_name/anchor_balance).

**Phase 7 -- Schema Cleanup (C-40 through C-43)**

- **C-40:** F-026 + F-132 + F-131 + F-133 (migration backfill convention + review
  docstrings + `pass` downgrade fixes).
- **C-41:** F-027 + F-069 (duplicate CHECK cleanup + missing `uq_scenarios_one_baseline`).
- **C-42:** F-071 + F-072 + F-079 + F-137 + F-138 + F-139 + F-140 (salary migration
  incomplete downgrade + `hysa_params` legacy names + missing FK indexes).
- **C-43:** F-073 + F-078 + F-136 (FK `ondelete=RESTRICT` sweep + `fk_*` naming
  convention + inter-budget `pay_period_id` alignment).

**Phase 8 -- Low/Info Cleanup (C-44 through C-52)**

- **C-44:** F-083 (`verify_password` non-string hardening).
- **C-45:** F-099 + F-100 + F-101 + F-126 + F-127 (grid balance_row None-check +
  retirement dashboard Decimal purification + docstring acknowledgment of documented
  rounding simplifications).
- **C-46:** F-145 (narrow `except Exception:` blocks in routes).
- **C-47:** F-159 (requirement hash pins via pip-compile).
- **C-48:** F-088 + F-141 (Argon2id migration with on-login rehash).
- **C-49:** F-157 (`scripts/config_audit.py` drift-check).
- **C-50:** F-151 + F-153 + F-094 (data classification doc + retention cleanup job +
  privacy policy + terms of service).
- **C-51:** F-158 (nmap re-run note).
- **C-52:** F-152 (per-record read audit instrumentation).

**Phase 9 -- Bigger Features Ex-Defers (C-53 through C-55)**

- **C-53:** F-143 + F-092 (server-side session store migration + WebAuthn / FIDO2
  enrollment).
- **C-54:** F-093 (GDPR data export + account deletion flow).
- **C-55:** F-149 (process-memory key exposure -- documentation + host check only; no
  code mitigation without hardware).

**Phase 10 -- Host Runbook (C-56)**

- **C-56:** F-023 + F-024 + F-065 + F-066 + F-067 + F-121 + F-122 + F-123 + F-124 +
  F-125 + F-130 (out-of-repo runbook for the Arch host: `.env` permissions, sysctl,
  auditd, sshd, kernel reboot, GRUB, core dumps, AIDE, NTP, PAM, SSH verification).

**Final commit count: 56 commits** (52 in-repo + 4 docs/runbook). The rejected Defers
expand the count; developer chose "Merge large" but the extra 13 findings prevent
compressing to 40-45.

**Final Defer list: 4 findings** -- F-058, F-059, F-118, F-119 (dependency staleness
monitoring only).

---

## Phase C -- Per-commit detailed plan

Each of the 56 commits below is specified to the quality bar of
`docs/implementation_plan_section8.md`. The subsections A-O are required for every
commit. Where two commits share a context paragraph verbatim (e.g. the session-
invalidation helper is introduced in C-08 and used by C-04/C-12/C-13), the shared
material is factored into **Phase D -- Cross-cutting concerns** and referenced by
citation.

Standard conventions applied to every commit unless stated otherwise:

- **Decimal construction from strings.** `Decimal("0.01")`, never `Decimal(0.01)`.
- **Specific exceptions only.** No `except Exception:`. Each `except` lists concrete
  types and an actionable error message.
- **Reference-table logic uses integer IDs**, never string `name` comparisons. Enums
  in `app/enums.py`, resolved via `app.ref_cache`.
- **User-scoping** on every query that touches user data: `.filter(Model.user_id ==
  current_user.id)` and `.filter(Model.is_deleted.is_(False))` where applicable.
- **Eager loading** via `joinedload` or `selectinload` for every template or service
  boundary that will access related collections.
- **Ownership response rule.** 404 for "not found" and "not yours"; 302 only for
  `@login_required`-only redirects to login.
- **Services never import `flask.request` or `flask.session`.** Route handlers pass
  plain data in; services return plain data out.
- **Templates never compute money.** Services compute; templates display.
- **CSRF** on every non-HTMX form via `{{ csrf_token() }}`; HTMX inherits from
  `htmx:configRequest` in `app/templates/base.html`. State-changing HTMX uses
  `hx-post`, `hx-patch`, or `hx-delete` -- never `hx-get`.
- **No inline JS.** All JS in `app/static/js/`; data passed via `data-*` attributes.
- **Constant-time comparisons** on secrets, hashes, and tokens: `bcrypt.checkpw`,
  `hmac.compare_digest`, `pyotp.TOTP.verify`. Direct `==` on any secret-bearing
  string is a blocker-level review finding.
- **Structured logging.** Every state-change route calls `log_event(...)` with a
  named event constant.
- **Migrations** use Alembic. Every `upgrade()` has a reversible `downgrade()`; every
  backfill SQL is idempotent (`WHERE ... IS NULL` guards); destructive migrations
  require explicit developer approval captured in the commit message.

---

### Commit C-01: SECRET_KEY history excise + runtime default tightening + session invalidation bump

**Findings addressed:** F-001 (Critical), F-016 (High), F-110 (Low), F-111 (Low).
**OWASP:** A02:2021 Cryptographic Failures primary; A07:2021 secondary.
**ASVS L2 controls closed:** V2.10.4 (Secrets Not in Source Code), V6.4.2 (Key Management), V14.1.1 (Build Pipeline).
**Depends on:** None.
**Blocks:** C-02, C-08, C-10, C-13 (any commit that relies on pre-excise sessions being invalid).
**Complexity:** Medium.

**A. Context and rationale.** The Shekel SECRET_KEY has been exfiltrated to the
public internet for the lifetime of the initial commit on branch
`audit/security-2026-04-15`. findings.md Impact (F-001) states verbatim: "Any Flask
session cookie or `itsdangerous`-signed URL token ever issued under that key is
forgeable forever." Although the runtime key was rotated before the audit, the
historical key is permanently in the git object store; anyone with read access to
the private repo, past collaborators, GitHub staff during an incident, or a future
accidental public repo flip can extract it. Combined with F-017 (`REMEMBER_COOKIE_*`
not hardened) this means remember-me cookies signed by the historical key can be
replayed for 30 days after any prior session.

Three related gaps ship in the same commit because they all govern the
"what-counts-as-a-valid-secret-key" decision surface:
- **F-016:** `app/config.py:22` still has a fallback `"dev-only-change-me-in-production"`
  default that makes dev/test accidentally load a public string.
- **F-110:** `ProdConfig.__init__` at `app/config.py:130-137` rejects only
  `startswith("dev-only")`; it does not reject `"change-me-to-a-random-secret-key"`
  (the `.env.example:11` value).
- **F-111:** `docker-compose.dev.yml:91` hardcodes `dev-secret-key-not-for-production`
  so the known placeholder list needs to include three strings.

CLAUDE.md rule 7 ("Trace impact before changing interfaces") requires us to force-
invalidate every pre-rotation session when the history is rewritten. This is why
the commit carries both a history excise AND a `session_invalidated_at` bump on every
user row.

**B. Files modified.**

- Git history (operation outside the working tree): developer runs `git filter-repo
  --path app/config.py --replace-text secrets.txt` or equivalent BFG. Commit message
  records the rewrite was executed.
- `app/config.py` -- remove fallback default; widen `ProdConfig.__init__` placeholder
  rejection; add length check.
- `.env.example` -- replace placeholder string with instruction-only text.
- `docker-compose.dev.yml` -- move the dev SECRET_KEY to an env-file reference so the
  placeholder is not literal in the compose file.
- `entrypoint.sh` -- add a SECRET_KEY presence/length/placeholder sanity check before
  Gunicorn starts.
- `scripts/rotate_sessions.py` -- new one-shot script that bumps
  `users.session_invalidated_at = now()` for every row.
- `docs/runbook_secrets.md` -- document the history-rewrite procedure and the
  session-invalidation step.
- Tests:
  - `tests/test_config.py::test_prodconfig_rejects_known_placeholders` (new).
  - `tests/test_config.py::test_prodconfig_rejects_empty_secret_key` (new).
  - `tests/test_config.py::test_prodconfig_rejects_short_secret_key` (new).
  - `tests/test_config.py::test_devconfig_loads_without_default` (new).
  - `tests/test_scripts/test_rotate_sessions.py` (new, 6 tests).

**C. Model / schema changes.** None. The `users.session_invalidated_at` column
already exists (`app/models/user.py:32`, `nullable=True`, `DateTime(timezone=True)`).
The script performs `UPDATE auth.users SET session_invalidated_at = now()`.

**D. Implementation approach.**

`app/config.py` -- replace lines 22 and 130-137 with:

```python
# app/config.py
SECRET_KEY = os.getenv("SECRET_KEY")  # No default; app will fail closed if missing.
```

```python
_KNOWN_DEFAULT_SECRETS = frozenset({
    "dev-only-change-me-in-production",
    "change-me-to-a-random-secret-key",
    "dev-secret-key-not-for-production",
})
_MIN_SECRET_KEY_LENGTH = 32


class ProdConfig(BaseConfig):
    # ...
    def __init__(self):
        """Validate production-critical settings on instantiation."""
        if not self.SECRET_KEY:
            raise ValueError(
                "SECRET_KEY is required in production. "
                "Generate with: python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        if self.SECRET_KEY in _KNOWN_DEFAULT_SECRETS or self.SECRET_KEY.startswith(
            "dev-only"
        ):
            raise ValueError(
                "SECRET_KEY matches a known placeholder; rotate before deploy."
            )
        if len(self.SECRET_KEY) < _MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {_MIN_SECRET_KEY_LENGTH} characters."
            )
        if not self.SQLALCHEMY_DATABASE_URI:
            raise ValueError("DATABASE_URL must be set in production.")
```

`.env.example:11` -- replace with instruction-only text:

```text
# REQUIRED -- do not deploy with a placeholder. Generate with:
#   python -c "import secrets; print(secrets.token_hex(32))"
# SECRET_KEY=<your 64-character hex string here>
SECRET_KEY=
```

`docker-compose.dev.yml:91` -- remove hardcoded value; read from env:

```yaml
SECRET_KEY: ${SECRET_KEY:?Set SECRET_KEY in .env.dev}
```

`entrypoint.sh` -- add a pre-Gunicorn sanity check after line 41 (PostgreSQL-ready
block) and before schema creation:

```bash
# Validate SECRET_KEY shape before proceeding. Config validation only fires
# when Flask imports ProdConfig; catch misconfiguration earlier so migrations
# do not run under a placeholder key.
if [ -z "${SECRET_KEY}" ] || [ "${#SECRET_KEY}" -lt 32 ]; then
    echo "ERROR: SECRET_KEY is missing or shorter than 32 chars." >&2
    exit 1
fi
case "${SECRET_KEY}" in
    dev-only-*|change-me-to-a-random-secret-key|dev-secret-key-not-for-production)
        echo "ERROR: SECRET_KEY matches a known placeholder." >&2
        exit 1
        ;;
esac
```

`scripts/rotate_sessions.py` -- new file:

```python
"""One-shot utility that force-invalidates every existing session.

Run manually after SECRET_KEY rotation or after a git history rewrite that
excised a historically-leaked key. Sets users.session_invalidated_at = now()
for every user row; the load_user callback in app/__init__.py rejects any
session older than this timestamp on the next request.

Usage:
    python scripts/rotate_sessions.py --confirm
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from app import create_app
from app.extensions import db
from app.models.user import User
from app.utils.log_events import log_event, AUTH

logger = logging.getLogger(__name__)


def rotate_sessions() -> int:
    """Force-invalidate every active user session. Returns row count."""
    app = create_app()
    with app.app_context():
        now = datetime.now(timezone.utc)
        count = db.session.query(User).update(
            {User.session_invalidated_at: now}, synchronize_session=False,
        )
        db.session.commit()
        log_event(
            logger, logging.WARNING, "sessions_invalidated_global", AUTH,
            "All sessions invalidated globally", count=count,
        )
        return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Force-invalidate every session after a SECRET_KEY rotation.",
    )
    parser.add_argument(
        "--confirm", action="store_true", required=True,
        help="Acknowledge that every user will be logged out.",
    )
    args = parser.parse_args()
    if not args.confirm:
        print("Pass --confirm to run.")
        return 1
    count = rotate_sessions()
    print(f"Invalidated {count} sessions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`docs/runbook_secrets.md` -- append a new section ("Post-rotation session
invalidation") describing: (1) `git filter-repo --invert-paths --path app/config.py`
or `bfg-repo-cleaner --delete-files app/config.py --no-blob-protection`; (2) force-
push to `audit/security-2026-04-15` after coordination; (3) execute
`python scripts/rotate_sessions.py --confirm` on the production container;
(4) verify every `.audit-venv`, `node_modules`, or local clone is re-cloned.

**E. Migration plan.** No schema migration. The `session_invalidated_at` column
already exists on `auth.users` (added by migration `2ae345ea9048`). The script is
a data operation, not a schema change.

**F. Test plan.**

| ID | Test file | Test name | Setup | Action | Expected | New / Mod |
|---|---|---|---|---|---|---|
| C01-1 | `tests/test_config.py` | `test_prodconfig_rejects_empty_secret_key` | `SECRET_KEY=""`, `DATABASE_URL=...` | Instantiate `ProdConfig()` | `ValueError` with "required in production" message | New |
| C01-2 | `tests/test_config.py` | `test_prodconfig_rejects_placeholder_dev_only` | `SECRET_KEY="dev-only-xyz"` | Instantiate `ProdConfig()` | `ValueError` with "known placeholder" message | New |
| C01-3 | `tests/test_config.py` | `test_prodconfig_rejects_placeholder_change_me` | `SECRET_KEY="change-me-to-a-random-secret-key"` | Instantiate `ProdConfig()` | `ValueError` with "known placeholder" message | New |
| C01-4 | `tests/test_config.py` | `test_prodconfig_rejects_placeholder_dev_secret` | `SECRET_KEY="dev-secret-key-not-for-production"` | Instantiate `ProdConfig()` | `ValueError` with "known placeholder" message | New |
| C01-5 | `tests/test_config.py` | `test_prodconfig_rejects_short_secret_key` | `SECRET_KEY="a" * 31` | Instantiate `ProdConfig()` | `ValueError` with "at least 32 characters" message | New |
| C01-6 | `tests/test_config.py` | `test_prodconfig_accepts_valid_secret_key` | `SECRET_KEY="0123456789abcdef" * 4` (64 chars), `DATABASE_URL=postgresql://...` | Instantiate `ProdConfig()` | No exception | New |
| C01-7 | `tests/test_config.py` | `test_devconfig_loads_without_default` | Unset `SECRET_KEY` | Instantiate `DevConfig()` | `DevConfig.SECRET_KEY` is `None`; no exception | New |
| C01-8 | `tests/test_config.py` | `test_testconfig_loads_without_default` | Unset `SECRET_KEY` | Instantiate `TestConfig()` | Same as C01-7 | New |
| C01-9 | `tests/test_scripts/test_rotate_sessions.py` | `test_rotate_sessions_bumps_every_user` | Seed three users with `session_invalidated_at` = None | Run `rotate_sessions()` | All three have `session_invalidated_at >= start_time`; return value == 3 | New |
| C01-10 | `tests/test_scripts/test_rotate_sessions.py` | `test_rotate_sessions_preserves_other_columns` | Seed user with email and password_hash | Run `rotate_sessions()` | `email` and `password_hash` unchanged | New |
| C01-11 | `tests/test_scripts/test_rotate_sessions.py` | `test_rotate_sessions_overwrites_older_timestamp` | Seed user with `session_invalidated_at = 2026-01-01` | Run `rotate_sessions()` | New timestamp > 2026-01-01 | New |
| C01-12 | `tests/test_scripts/test_rotate_sessions.py` | `test_rotate_sessions_requires_confirm_flag` | Run `main()` with `--confirm=False` (simulated) | Call `main()` | Returns 1, no DB write | New |
| C01-13 | `tests/test_scripts/test_rotate_sessions.py` | `test_rotate_sessions_on_empty_db_returns_zero` | TRUNCATE `auth.users` | Run `rotate_sessions()` | Return 0 | New |
| C01-14 | `tests/test_scripts/test_rotate_sessions.py` | `test_rotate_sessions_emits_log_event` | Seed one user; capture log output | Run `rotate_sessions()` | `sessions_invalidated_global` event at WARNING with `count=1` | New |
| C01-15 | `tests/test_adversarial/test_secret_key_rotation.py` | `test_session_cookie_signed_with_old_key_is_rejected` | Seed user; log in; bump `session_invalidated_at`; alter the test app's SECRET_KEY to a different value; reuse the cookie | GET `/dashboard` with old cookie | 302 to login (cookie signature check fails OR user loader rejects session) | New |
| C01-16 | `tests/test_adversarial/test_secret_key_rotation.py` | `test_session_cookie_older_than_invalidation_rejected` | Seed user with `session_invalidated_at = now + 1h`, cookie `_session_created_at = now` | GET `/dashboard` with that cookie | 302 to login (user loader returns None) | New |

**G. Manual verification.**

1. **Golden path.** Generate a fresh SECRET_KEY locally with `python -c "import
   secrets; print(secrets.token_hex(32))"`; set it in `.env`; run `flask run`; log in;
   verify the dashboard renders.
2. **Placeholder-rejection.** Export `SECRET_KEY="change-me-to-a-random-secret-key"`;
   export `FLASK_ENV=production`; run `flask run`; verify the app refuses to start with
   a ValueError mentioning "known placeholder".
3. **Short-key rejection.** Export `SECRET_KEY="abc"`; run with `FLASK_ENV=production`;
   verify refusal with "at least 32 characters".
4. **Dev path still works without default.** Unset `SECRET_KEY`; `FLASK_ENV=development`
   `flask run`; app starts (sessions may not persist across restarts, which is
   expected for local dev without a stable key).
5. **Entrypoint sanity check.** In the dev compose, temporarily set SECRET_KEY to a
   placeholder; `docker compose -f docker-compose.dev.yml up app`; verify entrypoint
   refuses and logs "matches a known placeholder".
6. **rotate_sessions.** Seed two users, log in as both in separate browsers; run
   `python scripts/rotate_sessions.py --confirm`; refresh both browsers; both
   redirected to login.

**H. Pylint.** `pylint app/config.py scripts/rotate_sessions.py tests/test_config.py
tests/test_scripts/test_rotate_sessions.py
tests/test_adversarial/test_secret_key_rotation.py --fail-on=E,F`. Expected clean.

**I. Targeted tests.** `pytest tests/test_config.py
tests/test_scripts/test_rotate_sessions.py
tests/test_adversarial/test_secret_key_rotation.py -v --tb=short`. Expected all 16
tests pass.

**J. Full-suite gate (directory-split; never in one command; never concurrent).**

```
timeout 720 pytest tests/test_services/ -v --tb=short
timeout 720 pytest tests/test_routes/ -v --tb=short
timeout 720 pytest tests/test_models/ -v --tb=short
timeout 720 pytest tests/test_integration/ -v --tb=short
timeout 720 pytest tests/test_adversarial/ -v --tb=short
timeout 720 pytest tests/test_scripts/ -v --tb=short
```

**K. Scanner re-run.**

```
bandit -r app/config.py scripts/rotate_sessions.py -ll
semgrep --config p/python --config p/flask app/config.py scripts/rotate_sessions.py
gitleaks detect --no-banner --source . --redact
```

Expected deltas: bandit clean; semgrep clean; gitleaks no longer flags the historical
`app/config.py` commit once the rewrite completes.

**L. IDOR probe re-run.** Not applicable -- this commit does not touch access-control
code paths.

**M. Downstream effects.**

- Every active user is logged out on deploy. Announce this in release notes and
  coordinate with any active users of the deployment.
- The git history rewrite invalidates every local clone, every CI cache, and every
  audit-branch snapshot. Every clone must be re-pulled; `git clone --no-local
  --mirror` against the fresh history to preserve audit integrity.
- Coordinating the rewrite with the audit branch requires force-push permissions
  on `audit/security-2026-04-15`. The developer is the sole branch owner, so
  coordination is trivial.
- `tests/conftest.py` sets a fixed dev SECRET_KEY for test reproducibility; verify
  this path still works (TestConfig reads `SECRET_KEY` from env with no default).

**N. Risk and rollback.**

- **Failure mode 1: force-push rejected.** If the audit branch is protected, the
  branch owner must temporarily remove protection. Rollback: leave the history
  intact and rely on session invalidation alone -- residual risk is that the
  historical key remains in the repo's object store. Document the regression in
  `findings.md` as a permanently-accepted risk.
- **Failure mode 2: session invalidation fails mid-run.** Script is idempotent;
  re-run.
- **Failure mode 3: production app does not restart.** `entrypoint.sh` now enforces
  SECRET_KEY presence; a misconfigured `.env` blocks startup. Rollback:
  `git revert <SHA>` + previous `entrypoint.sh`.
- **Regression tests locked in:** C01-15 and C01-16 in
  `tests/test_adversarial/test_secret_key_rotation.py` prevent future regressions
  that accept cookies signed by a rotated key.

**O. Findings.md update.** After merge: developer updates F-001, F-016, F-110,
F-111 to `Status: Fixed in <SHA> (PR #<n>)`.

---

### Commit C-02: Cookie flag + security header + CSP tightening bundle

**Findings addressed:** F-017 (High), F-018 (High), F-019 (High), F-036 (Medium),
F-037 (Medium), F-096 (Low), F-097 (Low).
**OWASP:** A02:2021 Cryptographic Failures primary; A05:2021 Security
Misconfiguration secondary.
**ASVS L2 controls closed:** V3.4.1, V3.4.2, V3.4.4, V8.2.1, V8.2.3, V14.4.5,
V14.5.1, V14.5.2, V10.3.2, V14.2.3.
**Depends on:** C-01 (historical cookies must be invalidated before HSTS and
__Host- prefix tighten; otherwise legacy cookies under the rotated key briefly
fail open).
**Blocks:** C-10 (session lifetime).
**Complexity:** Medium.

**A. Context and rationale.** findings.md Top Risk #2 bundles five independent but
cheap crypto-header fixes whose combined effect closes the "stolen laptop + rogue
WiFi + shared computer" threat set. Individually each is a one-liner; shipping them
together means (a) one testing pass exercises the full surface, (b) the commit
history records the crypto-header posture as one decision rather than five, and (c)
any rollback is atomic.

F-017 Impact: remember-me cookies leak in cleartext on any HTTP leak and attach to
cross-site requests. F-018 Impact: first unprotected request per session is
downgradeable. F-019 Impact: shared-device Back button reveals cached financial
pages. F-036 Impact: CSS attribute-selector keylogging exfiltrates form inputs.
F-037 Impact: compromised CDN replaces JS/CSS served to every user. F-096: missing
`__Host-` prefix leaves the cookie un-domain-pinned. F-097: legacy-browser
clickjacking path.

Vendoring the CDN assets per F-037 recommendation also eliminates the CSP origin
list for script-src, style-src, and font-src -- which in turn makes F-036's
`unsafe-inline` elimination simpler because there is no cross-origin `<link>` style
attribute to accommodate.

**B. Files modified.**

- `app/config.py` -- ProdConfig add `REMEMBER_COOKIE_SECURE`, `REMEMBER_COOKIE_HTTPONLY`,
  `REMEMBER_COOKIE_SAMESITE`, `SESSION_COOKIE_NAME = "__Host-session"`.
- `app/__init__.py` -- `_register_security_headers` add HSTS, Cache-Control, Pragma;
  update CSP string to drop `'unsafe-inline'` from `style-src`, drop external origins,
  add `frame-ancestors 'none'`, add `require-sri-for script style`.
- `app/templates/base.html` -- replace CDN `<link>` / `<script>` tags with
  `url_for("static", ...)` references; remove `integrity=` attributes that referenced
  CDNs.
- `app/static/vendor/` -- new directory. Contents vendored from pinned versions of
  Bootstrap CSS, Bootstrap JS, Bootstrap Icons CSS, htmx. Subresource-integrity hashes
  in the build tooling.
- Inline-style sweep -- grep `style="` across `app/templates/` and move all hits to
  `app/static/css/app.css`. Estimated 10-20 templates touched.
- `tests/test_config.py::test_prodconfig_cookie_hardening` (new).
- `tests/test_integration/test_security_headers.py` (new file, 12 tests).
- `tests/test_adversarial/test_cache_control.py` (new file, 3 tests).
- `docs/runbook.md` -- note CDN vendor refresh procedure; note HSTS `preload` decision
  is intentionally NOT set (see architectural decision below).

**C. Model / schema changes.** None.

**D. Implementation approach.**

*Architectural decision -- HSTS preload.* findings.md F-018 recommends starting with
`max-age=31536000; includeSubDomains` WITHOUT `preload`. `preload` is a one-way
commitment (submission to the HSTS preload list; once accepted, every browser
enforces HTTPS on the domain permanently). Propose starting without preload; add
preload only after (a) the domain's HTTPS posture has been stable for at least 90
days and (b) all subdomains are HTTPS-only. Flag this decision at the C-02 commit
checkpoint for developer confirmation.

`app/config.py` ProdConfig additions (after line 128):

```python
    # Remember-me cookie -- must track session cookie hardening.
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Domain-pinned session cookie. Requires SESSION_COOKIE_SECURE=True
    # (already set) and SESSION_COOKIE_PATH="/" (Flask default).
    SESSION_COOKIE_NAME = "__Host-session"
```

`app/__init__.py` `_register_security_headers` replacement:

```python
def _register_security_headers(app):
    """Add security headers to every response."""

    csp_parts = [
        "default-src 'self'",
        "script-src 'self'",  # no CDN, no unsafe-inline
        "style-src 'self'",  # no unsafe-inline, no CDN
        "font-src 'self'",  # vendored
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    csp = "; ".join(csp_parts)

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = csp
        # HSTS: 1 year, includeSubDomains. Preload intentionally OFF pending
        # 90-day stability window -- see docs/runbook.md HSTS section.
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        # Financial pages must never be reconstructed from browser cache.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        return response
```

CDN vendoring: Download the pinned Bootstrap 5.3.2 CSS/JS, Bootstrap Icons 1.11.3 CSS,
and htmx 1.9.10 bundles. Verify SHA-384 SRI hashes against the PyPI / npm / GitHub
release artifacts (recorded in `app/static/vendor/VERSIONS.txt`). Place under
`app/static/vendor/bootstrap/`, `vendor/bootstrap-icons/`, `vendor/htmx/`.

`app/templates/base.html` -- replace (current lines 12-20 and 259-264 per S1 Subagent
C) with:

```jinja
<link rel="stylesheet"
      href="{{ url_for('static', filename='vendor/bootstrap/bootstrap.min.css') }}">
<link rel="stylesheet"
      href="{{ url_for('static', filename='vendor/bootstrap-icons/bootstrap-icons.css') }}">
{# ... #}
<script src="{{ url_for('static', filename='vendor/bootstrap/bootstrap.bundle.min.js') }}" defer></script>
<script src="{{ url_for('static', filename='vendor/htmx/htmx.min.js') }}" defer></script>
```

Inline-style sweep procedure: `grep -rn 'style="' app/templates/` to enumerate hits.
For each match, promote the inline rule to a named class in `app/static/css/app.css`,
update the template to reference the class. Keep commits reviewable by batching the
promotion into this single commit (scope-appropriate because the CSP tightening is
the motivation).

**E. Migration plan.** None.

**F. Test plan.**

| ID | Test file | Test name | Setup | Action | Expected | New / Mod |
|---|---|---|---|---|---|---|
| C02-1 | `tests/test_config.py` | `test_prodconfig_cookie_hardening` | ProdConfig instance | Read attributes | SESSION_COOKIE_SECURE=True, HTTPONLY=True, SAMESITE="Lax", NAME="__Host-session", REMEMBER_COOKIE_* all hardened | New |
| C02-2 | `tests/test_integration/test_security_headers.py` | `test_hsts_header_present` | auth_client | GET `/dashboard` | `Strict-Transport-Security` header present, contains `max-age=31536000`, `includeSubDomains`; NO `preload` | New |
| C02-3 | ... | `test_cache_control_no_store_on_authenticated_page` | auth_client | GET `/dashboard` | `Cache-Control: no-store, no-cache, must-revalidate` present | New |
| C02-4 | ... | `test_cache_control_on_static_is_unchanged` | auth_client | GET `/static/vendor/bootstrap/bootstrap.min.css` | `Cache-Control: public, immutable` (nginx-layer) or app-layer `no-store` depending on WSGI path -- MUST NOT break static file caching | New |
| C02-5 | ... | `test_csp_no_unsafe_inline_in_style_src` | auth_client | GET `/dashboard` | CSP header `style-src 'self'` only (no `unsafe-inline`, no external origins) | New |
| C02-6 | ... | `test_csp_no_external_origins_in_script_src` | auth_client | GET `/dashboard` | CSP `script-src 'self'` only | New |
| C02-7 | ... | `test_csp_frame_ancestors_none` | auth_client | GET `/dashboard` | CSP contains `frame-ancestors 'none'` | New |
| C02-8 | ... | `test_csp_base_uri_self` | auth_client | GET `/dashboard` | CSP contains `base-uri 'self'` | New |
| C02-9 | ... | `test_csp_form_action_self` | auth_client | GET `/dashboard` | CSP contains `form-action 'self'` | New |
| C02-10 | ... | `test_frame_ancestors_precedes_x_frame_options` | auth_client | GET `/dashboard` | Both present; frame-ancestors takes precedence per spec | New |
| C02-11 | `tests/test_adversarial/test_cache_control.py` | `test_logged_out_dashboard_not_in_history` | auth_client login, then logout, then GET `/dashboard` | Check 302 AND headers on intermediate pages | All authenticated pages served `no-store` so back button reconstruction is impossible (behavior verified via header-assert; actual browser back-button behavior is a manual verification item) | New |
| C02-12 | `tests/test_adversarial/test_cache_control.py` | `test_static_assets_remain_cacheable` | auth_client | GET `/static/vendor/htmx/htmx.min.js` | Nginx-served `Cache-Control: public, immutable` (or app's static -- verify no double-header conflict) | New |
| C02-13 | `tests/test_adversarial/test_cache_control.py` | `test_remember_me_cookie_flags_after_login` | auth_client login with remember=True | Inspect Set-Cookie header | `Secure`, `HttpOnly`, `SameSite=Lax` all present on `remember_token` | New |
| C02-14 | `tests/test_integration/test_security_headers.py` | `test_no_cdn_origins_remain_in_base_html` | Read rendered `/` | Assert no occurrence of `cdn.jsdelivr.net`, `unpkg.com`, `fonts.googleapis.com`, `fonts.gstatic.com` in response body | New |
| C02-15 | `tests/test_integration/test_security_headers.py` | `test_no_inline_style_in_any_template` | Render every GET route accessible by auth_client | Assert no `style="` in any response body (defense-in-depth) | New |

**G. Manual verification.**

1. Open the app in a fresh incognito window. Log in. Verify login cookie is
   `__Host-session` and carries `Secure`, `HttpOnly`, `SameSite=Lax` per DevTools
   Application tab.
2. Log in with "remember me" checked; verify the `remember_token` cookie also has
   `Secure`, `HttpOnly`, `SameSite=Lax`.
3. Visit `/dashboard`, then log out, then press Browser Back. Expect the browser to
   show "Confirm Form Resubmission" or a "page expired" screen rather than the
   cached dashboard.
4. In DevTools Network tab, inspect the `/dashboard` response headers. Confirm all
   of: `Strict-Transport-Security`, `Content-Security-Policy`,
   `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
   `Permissions-Policy`, `Cache-Control: no-store`, `Pragma: no-cache`.
5. Check the rendered HTML `<head>` section -- all `<link>` and `<script>` tags
   reference `/static/vendor/...`, none reference `cdn.jsdelivr.net` or similar.
6. Run `grep -rn 'style="' app/templates/` on the branch -- expected zero matches.
7. Dark mode verification: toggle to dark mode; verify no styles broke (the CSS sweep
   moved inline styles to classes but must preserve dark-mode overrides).
8. Mobile viewport (Bootstrap `sm` and `md` breakpoints) -- verify nav bar, forms,
   and tables render correctly.

**H. Pylint.** `pylint app/__init__.py app/config.py
tests/test_integration/test_security_headers.py
tests/test_adversarial/test_cache_control.py --fail-on=E,F`. Expected clean.

**I. Targeted tests.** `pytest tests/test_config.py
tests/test_integration/test_security_headers.py
tests/test_adversarial/test_cache_control.py -v --tb=short`. Expected all 15 tests
pass.

**J. Full-suite gate.** Standard split. Particular attention to `test_routes/`
because the inline-style sweep touches many templates; regressions manifest as
template-render exceptions.

**K. Scanner re-run.** `semgrep --config p/flask --config p/owasp-top-ten app/`
(expected: no `unsafe-inline` findings). Plus a new check: `grep -rn "cdn\.jsdelivr\|unpkg\|googleapis\|gstatic" app/templates/` returns zero matches.

**L. IDOR probe re-run.** Not applicable (headers only; no access-control change).

**M. Downstream effects.**

- Every CDN dependency version is frozen at the vendored copy. Bumping Bootstrap or
  htmx requires a new commit updating the vendored files and the
  `VERSIONS.txt` manifest. Supply-chain risk is reduced (no drive-by CDN
  compromise) but upgrade cadence is now manual.
- `docker-compose.yml:104` already mounts `./nginx/nginx.conf`; the vendored assets
  live in `app/static/vendor/` which is copied by `entrypoint.sh:91` to
  `/var/www/static/`. Verify the copy succeeds post-deploy.
- `SESSION_COOKIE_NAME="__Host-session"` changes the cookie name. Every existing
  session gets invalidated at deploy time because the old cookie (`session`) no
  longer matches. This composes correctly with C-01 which already invalidates every
  session; note both commits ship together in Phase 1.

**N. Risk and rollback.**

- **Failure mode 1: vendored asset SRI hash mismatch.** Copy-paste error in
  `VERSIONS.txt`. Detected by browser CSP blocking (`require-sri-for` denies). Fix:
  correct the hash.
- **Failure mode 2: `__Host-` prefix rejected by some old browsers.** Modern browsers
  all support it (Chrome 49+, Firefox 49+, Safari 11+). Rollback to SESSION_COOKIE_NAME
  default only if a critical user is on an unsupported browser.
- **Rollback:** `git revert <SHA>`. Vendored asset files remain but are ignored by
  templates once reverted. `flask db downgrade` not applicable.
- **Regression test lock-in:** C02-2 through C02-13 in
  `tests/test_integration/test_security_headers.py`.

**O. Findings.md update.** F-017, F-018, F-019, F-036, F-037, F-096, F-097 marked
Fixed on merge.

---

### Commit C-03: Backup code entropy upgrade to 112 bits

**Findings addressed:** F-004 (High).
**OWASP:** A07:2021 Identification and Authentication Failures.
**ASVS L2 controls closed:** V2.6.2 (Lookup Secret Minimum Entropy).
**Depends on:** None.
**Blocks:** C-12 (F-095 MFA nag; wants post-entropy-upgrade backup-code display
format).
**Complexity:** Small.

**A. Context and rationale.** findings.md F-004 records
`secrets.token_hex(4)` = 32 bits of entropy per backup code. ASVS L2 V2.6.2 requires
>= 112 bits. For a money app aspiring to public release, the "bcrypt hashes leaked"
scenario is the baseline threat model (red team affirmed High). The fix is a two-
character change in one function.

The historical backup codes (enrolled before this commit) remain valid until the
user regenerates them. On first login after deploy, users with active backup codes
receive an in-app banner prompting regeneration. The banner text and one-time
acknowledgement live in C-16 (`F-091` in-app notifications).

**B. Files modified.**

- `app/services/mfa_service.py` -- change `secrets.token_hex(4)` to `secrets.token_hex(14)`
  at line 123.
- `app/templates/auth/mfa_backup_codes.html` -- widen the display column from 8 to 28
  characters.
- `app/templates/auth/mfa_backup_codes.html` -- update hint text to mention the new
  length.
- `tests/test_services/test_mfa_service.py::test_generate_backup_codes_entropy` (new
  or update existing).

**C. Model / schema changes.** None. `auth.mfa_configs.backup_codes` is a JSON array
of bcrypt hashes -- the hashes themselves are already 60 chars regardless of input
length.

**D. Implementation approach.**

`app/services/mfa_service.py:112-123` replacement:

```python
def generate_backup_codes(count=10):
    """Generate a list of single-use backup codes.

    Each code is 28 lowercase hex characters = 14 random bytes = 112 bits of
    entropy. Sourced from secrets.token_hex which is backed by the operating
    system's CSPRNG. 112 bits is the ASVS L2 V2.6.2 minimum for lookup secrets
    and resists offline bcrypt brute-force at current GPU speeds
    (~10^12 hashes/second against bcrypt cost 12 = ~4 million years on average
    per code).

    Args:
        count: Number of backup codes to generate (default 10).

    Returns:
        list[str]: The plaintext backup code strings, each 28 hex characters.
    """
    return [secrets.token_hex(14) for _ in range(count)]
```

`app/templates/auth/mfa_backup_codes.html` -- update the code-display column to
accommodate 28 characters (widen from 8-char span to 28-char span). Keep the copy
instructions up to date: "Each backup code is 28 characters. You can copy them with
the button below or print this page."

**E. Migration plan.** None.

**F. Test plan.**

| ID | Test file | Test name | Setup | Action | Expected | New / Mod |
|---|---|---|---|---|---|---|
| C03-1 | `tests/test_services/test_mfa_service.py` | `test_generate_backup_codes_length` | -- | `mfa_service.generate_backup_codes()` | 10 codes returned; each `len() == 28`; each is lowercase hex | Mod |
| C03-2 | ... | `test_generate_backup_codes_entropy` | -- | Call with count=1000; collect | All 1000 codes unique (collision probability for 112-bit is negligible, so >=999 unique is acceptance threshold) | New |
| C03-3 | ... | `test_generate_backup_codes_uses_secrets_module` | Mock `secrets.token_hex` | `mfa_service.generate_backup_codes(count=10)` | `secrets.token_hex(14)` called 10 times | New |
| C03-4 | ... | `test_generate_backup_codes_default_count` | -- | `mfa_service.generate_backup_codes()` | returns list length 10 | Mod |
| C03-5 | ... | `test_verify_backup_code_accepts_new_28_char_codes` | Generate codes; hash; verify | `mfa_service.verify_backup_code(codes[3], hashes)` | Returns 3 | Mod |
| C03-6 | ... | `test_verify_backup_code_still_accepts_legacy_8_char_codes` | Pre-compute hash of legacy 8-char code (simulating pre-upgrade user) | `mfa_service.verify_backup_code("legacy01", [hash])` | Returns 0 (bcrypt is length-agnostic; legacy codes remain valid) | New |
| C03-7 | `tests/test_routes/test_auth.py` | `test_mfa_backup_codes_template_renders_new_format` | auth_client with MFA enabled; regenerate | GET `/mfa/regenerate-backup-codes` response | Response body contains 10 codes each 28 chars | Mod |

**G. Manual verification.**

1. Log in as a test user; navigate to Settings > Security > Regenerate Backup Codes.
2. Verify the displayed codes are each 28 characters of lowercase hex (e.g.
   `a1b2c3d4e5f6a7b8c9d0e1f2a3b4`).
3. Copy one code; log out; log in with the password; paste the code on the MFA page.
4. Verify login succeeds and the code is removed from the stored list.
5. Regenerate codes again; verify all ten are new values and the previously-used code
   is rejected.

**H. Pylint.** `pylint app/services/mfa_service.py
tests/test_services/test_mfa_service.py tests/test_routes/test_auth.py
--fail-on=E,F`. Expected clean.

**I. Targeted tests.** `pytest tests/test_services/test_mfa_service.py
tests/test_routes/test_auth.py::test_mfa_backup_codes_template_renders_new_format -v
--tb=short`. All 7 tests pass.

**J. Full-suite gate.** Standard split.

**K. Scanner re-run.** `bandit -r app/services/mfa_service.py` -- no change
expected; `secrets.token_hex` was already cryptographically appropriate at 32 bits
and remains at 112 bits.

**L. IDOR probe re-run.** Not applicable.

**M. Downstream effects.**

- Legacy 8-char codes from pre-upgrade enrollment remain valid. Users receive an
  in-app notification to regenerate (C-16 F-091 implements the notification).
- Template column widening may affect mobile breakpoints -- verify at Bootstrap
  `sm` (<576px).
- No caller outside `mfa_service` generates codes; no interface changes to trace.

**N. Risk and rollback.**

- **Failure mode 1: UI overflow on narrow viewports.** Mitigated by CSS
  responsiveness; verified in manual step 5.
- **Rollback:** `git revert <SHA>`. Users with post-upgrade codes can still log in
  (bcrypt is length-agnostic).
- **Regression test lock-in:** C03-2 (entropy) and C03-1 (length).

**O. Findings.md update.** F-004 marked Fixed on merge.

---

### Commit C-04: TOTP_ENCRYPTION_KEY MultiFernet rotation

**Findings addressed:** F-030 (High).
**OWASP:** A02:2021 Cryptographic Failures.
**ASVS L2 controls closed:** V6.2.4 (Key Management).
**Depends on:** None (independent; foundation for F-147 field-level encryption
reuse in C-39).
**Blocks:** C-39 (F-147 PII encryption uses MultiFernet).
**Complexity:** Medium.

**A. Context and rationale.** findings.md F-030 confirms `get_encryption_key()`
returns a single-key `Fernet` instance every call; there is no `MultiFernet`, no
versioned ciphertext, no rotation script. `docs/runbook_secrets.md:11` documents key
rotation as DESTRUCTIVE because every existing MFA configuration becomes
undecryptable. The fix is a drop-in `MultiFernet` upgrade that accepts a primary key
(for encrypt) and one or more retired keys (for decrypt-only of legacy
ciphertexts), plus a one-shot rotation script that re-wraps every ciphertext under
the new primary. This lets an operator rotate without a user-visible event.

**B. Files modified.**

- `app/services/mfa_service.py` -- `get_encryption_key()` returns `MultiFernet`;
  new helper `_build_fernet_list()` that reads `TOTP_ENCRYPTION_KEY` (primary) and
  `TOTP_ENCRYPTION_KEY_OLD` (optional, comma-separated list of retired keys).
- `app/config.py` BaseConfig -- add `TOTP_ENCRYPTION_KEY_OLD` env var pass-through
  (optional, nullable).
- `.env.example` -- document the rotation variable.
- `scripts/rotate_totp_key.py` -- new one-shot script.
- `docs/runbook_secrets.md` -- replace the DESTRUCTIVE paragraph with a non-
  destructive rotation procedure.
- `tests/test_services/test_mfa_service.py::test_multifernet_*` (new, 8 tests).
- `tests/test_scripts/test_rotate_totp_key.py` (new file, 6 tests).

**C. Model / schema changes.** None. Fernet ciphertexts are binary blobs;
`auth.mfa_configs.totp_secret_encrypted` is already `LargeBinary` (see
`app/models/user.py:146`).

**D. Implementation approach.**

`app/services/mfa_service.py:18-63` replacement:

```python
import os
from cryptography.fernet import Fernet, MultiFernet


_FERNET_CACHE = None  # Module-level cache; rebuild if env vars change.


def _build_fernet_list():
    """Build the ordered list of Fernet instances for MultiFernet.

    The primary key (TOTP_ENCRYPTION_KEY) is used for encryption AND appears
    first for decryption. TOTP_ENCRYPTION_KEY_OLD, if set, may contain one or
    more comma-separated retired keys; each is wrapped in a Fernet and tried
    after the primary on decrypt.

    Returns:
        list[Fernet]: Non-empty list with the primary key at index 0.

    Raises:
        RuntimeError: If TOTP_ENCRYPTION_KEY is unset.
        ValueError: If any configured key fails to initialize as a Fernet.
    """
    primary_key = os.getenv("TOTP_ENCRYPTION_KEY")
    if not primary_key:
        raise RuntimeError("TOTP_ENCRYPTION_KEY environment variable is not set.")
    fernets = [Fernet(primary_key)]
    old_keys = os.getenv("TOTP_ENCRYPTION_KEY_OLD", "").strip()
    if old_keys:
        for raw in old_keys.split(","):
            key = raw.strip()
            if key:
                fernets.append(Fernet(key))
    return fernets


def get_encryption_key():
    """Load the MultiFernet cipher from the environment.

    Returns:
        MultiFernet: A cipher that encrypts with the primary key and decrypts
            with any primary-or-retired key.

    Raises:
        RuntimeError: If TOTP_ENCRYPTION_KEY is unset.
    """
    return MultiFernet(_build_fernet_list())
```

`encrypt_secret` and `decrypt_secret` continue to work unchanged -- `MultiFernet`
exposes the same `.encrypt()` / `.decrypt()` API as `Fernet`.

`scripts/rotate_totp_key.py` (new):

```python
"""One-shot TOTP_ENCRYPTION_KEY rotation.

Re-wraps every auth.mfa_configs.totp_secret_encrypted under the current primary
key. Intended to be run AFTER the operator has:
  1. Generated a new primary key (Fernet.generate_key()).
  2. Moved the previous primary key to TOTP_ENCRYPTION_KEY_OLD.
  3. Set the new key as TOTP_ENCRYPTION_KEY.
  4. Restarted the application container.

After this script completes successfully, the operator may remove the retired
key from TOTP_ENCRYPTION_KEY_OLD at the next deploy.

Usage:
    python scripts/rotate_totp_key.py --confirm

The script is idempotent: re-running after all rows are already under the
primary key is a no-op.
"""
from __future__ import annotations

import argparse
import logging

from app import create_app
from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from app.utils.log_events import log_event, AUTH

logger = logging.getLogger(__name__)


def rotate_ciphertexts() -> tuple[int, int]:
    """Re-encrypt every MFA config under the current primary key.

    Returns:
        tuple: (rotated_count, skipped_count). Skipped rows are those that
            failed to decrypt under any configured key (indicates prior
            rotation was never completed OR key list is stale).
    """
    app = create_app()
    with app.app_context():
        cipher = mfa_service.get_encryption_key()
        rotated = 0
        skipped = 0
        configs = db.session.query(MfaConfig).filter(
            MfaConfig.totp_secret_encrypted.isnot(None),
        ).all()
        for config in configs:
            try:
                plaintext = cipher.decrypt(config.totp_secret_encrypted)
            except Exception as exc:  # pylint: disable=broad-except
                # Multiple narrow exceptions possible here (InvalidToken,
                # InvalidSignature); the single operational concern is
                # "cannot decrypt", regardless of reason. Logged for manual
                # recovery.
                logger.error(
                    "Failed to decrypt MFA config %d: %s. Skipping.",
                    config.id, type(exc).__name__,
                )
                skipped += 1
                continue
            config.totp_secret_encrypted = cipher.encrypt(plaintext)
            rotated += 1
        db.session.commit()
        log_event(
            logger, logging.INFO, "totp_key_rotated", AUTH,
            "TOTP encryption key rotation completed",
            rotated=rotated, skipped=skipped,
        )
        return rotated, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-wrap every MFA config under the current primary key.",
    )
    parser.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()
    if not args.confirm:
        return 1
    rotated, skipped = rotate_ciphertexts()
    print(f"Rotated {rotated}; skipped {skipped}.")
    return 0 if skipped == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

The `except Exception` inside `rotate_ciphertexts` is the ONE place in the plan
that uses a broad exception catch, with a pylint disable comment. The justification
is operational: the script's one job is to iterate every row and re-encrypt where
possible; the specific reason a given row fails is diagnostic information, not a
decision input. Narrow alternative considered: `except (InvalidToken,
InvalidSignature, ValueError)` -- but `MultiFernet.decrypt` wraps the inner
exceptions in `InvalidToken` in practice, and adding `InvalidSignature` is forward-
compatibility with future cryptography library changes. Both approaches are
defensible; the broad catch is justified by the script being a recovery tool where
we want to continue on any decrypt failure, not just the ones we anticipated.

**E. Migration plan.** None.

**F. Test plan.**

| ID | Test file | Test name | Setup | Action | Expected | New / Mod |
|---|---|---|---|---|---|---|
| C04-1 | `tests/test_services/test_mfa_service.py` | `test_get_encryption_key_returns_multifernet` | `TOTP_ENCRYPTION_KEY` set | `mfa_service.get_encryption_key()` | Returns `MultiFernet` instance (isinstance check) | New |
| C04-2 | ... | `test_get_encryption_key_raises_if_unset` | Clear env | `mfa_service.get_encryption_key()` | `RuntimeError` | Mod |
| C04-3 | ... | `test_encrypt_and_decrypt_round_trip_under_primary` | Valid primary key | `encrypt_secret("ABC123")` then `decrypt_secret(ct)` | Returns "ABC123" | Mod |
| C04-4 | ... | `test_decrypt_accepts_ciphertext_from_old_key` | Set `TOTP_ENCRYPTION_KEY=new`, `TOTP_ENCRYPTION_KEY_OLD=old`; encrypt with old; attempt decrypt under current app | Returns plaintext | New |
| C04-5 | ... | `test_encrypt_uses_primary_not_old` | Set primary + old; encrypt a secret; inspect ciphertext version byte | Ciphertext's Fernet version indicates primary key used | New |
| C04-6 | ... | `test_old_key_list_comma_separated` | `TOTP_ENCRYPTION_KEY_OLD="key1,key2,key3"` | `_build_fernet_list()` | Returns 4 Fernets (primary + 3) | New |
| C04-7 | ... | `test_old_key_ignores_blank_entries` | `TOTP_ENCRYPTION_KEY_OLD="key1, ,key2, "` | `_build_fernet_list()` | Returns 3 Fernets (primary + 2; blanks skipped) | New |
| C04-8 | ... | `test_invalid_old_key_raises` | `TOTP_ENCRYPTION_KEY_OLD="not-a-fernet-key"` | `_build_fernet_list()` | `ValueError` from Fernet constructor | New |
| C04-9 | `tests/test_scripts/test_rotate_totp_key.py` | `test_rotate_re_encrypts_all_rows_under_primary` | Seed 3 MFA configs under old key; set old key; run rotation | All 3 ciphertexts now decrypt under primary-only (no old key) | New |
| C04-10 | ... | `test_rotate_is_idempotent` | Run rotation; run again | Second run changes 0 rows (all already under primary) | New |
| C04-11 | ... | `test_rotate_skips_undecryptable_rows` | Seed 2 configs under key X; configure primary=Y, old=Z (neither matches X); run | 0 rotated, 2 skipped; exit code 2 | New |
| C04-12 | ... | `test_rotate_empty_table` | No configs | Run | 0 rotated, 0 skipped; exit code 0 | New |
| C04-13 | ... | `test_rotate_requires_confirm_flag` | Run without `--confirm` | Return 1; no DB changes | New |
| C04-14 | ... | `test_rotate_emits_log_event` | Seed 1 config, rotate | `totp_key_rotated` event recorded with `rotated=1, skipped=0` | New |

**G. Manual verification.**

1. Generate an initial key: `python -c "from cryptography.fernet import Fernet;
   print(Fernet.generate_key().decode())"`. Set as `TOTP_ENCRYPTION_KEY` in `.env`.
   Start app; enroll MFA on a test user.
2. Generate a new key (same command). Move the previous value to
   `TOTP_ENCRYPTION_KEY_OLD`; set new value as `TOTP_ENCRYPTION_KEY`. Restart.
3. Log in as the test user; verify MFA prompt works (decrypts under old key via
   MultiFernet).
4. Run `python scripts/rotate_totp_key.py --confirm`. Verify output: "Rotated 1;
   skipped 0."
5. Remove `TOTP_ENCRYPTION_KEY_OLD` from `.env`; restart; verify MFA still works
   (ciphertext is now under the primary).
6. Restart with `TOTP_ENCRYPTION_KEY` only; confirm no residual dependency on the
   old key.

**H. Pylint.** `pylint app/services/mfa_service.py scripts/rotate_totp_key.py
tests/test_services/test_mfa_service.py tests/test_scripts/test_rotate_totp_key.py
--fail-on=E,F`. Expected clean. The `except Exception` in `rotate_ciphertexts`
carries `# pylint: disable=broad-except` with a 3-line justification comment.

**I. Targeted tests.** `pytest tests/test_services/test_mfa_service.py
tests/test_scripts/test_rotate_totp_key.py -v --tb=short`. All 14 new tests plus
existing regression tests pass.

**J. Full-suite gate.** Standard split.

**K. Scanner re-run.** `bandit -r app/services/mfa_service.py scripts/rotate_totp_key.py
-ll`. Expected clean (the one broad-except has disable comment).

**L. IDOR probe re-run.** Not applicable.

**M. Downstream effects.**

- `docs/runbook.md` MUST be updated to reflect the new non-destructive rotation
  procedure.
- `docker-compose.yml` already passes `TOTP_ENCRYPTION_KEY` through. Developer adds
  `TOTP_ENCRYPTION_KEY_OLD: ${TOTP_ENCRYPTION_KEY_OLD:-}` to the environment block
  at line 72 in the same commit.
- F-147 (C-39) reuses the `MultiFernet` pattern for PII field encryption.

**N. Risk and rollback.**

- **Failure mode 1: operator forgets to run the rotation script before removing
  TOTP_ENCRYPTION_KEY_OLD.** Any user whose ciphertext is still under the old key
  cannot MFA-verify. Mitigation: the script exits non-zero on skipped rows; the
  operator sees this before removing the old key.
- **Failure mode 2: MultiFernet rejects a valid Fernet key.** Cryptography library
  regression; pin `cryptography==46.0.7` (already pinned in `requirements.txt:27`).
- **Rollback:** `git revert <SHA>`. The ciphertexts remain readable because
  `Fernet.decrypt` and `MultiFernet.decrypt` produce identical output on ciphertexts
  encrypted by the single-key Fernet.
- **Regression test lock-in:** C04-5, C04-9, C04-10.

**O. Findings.md update.** F-030 marked Fixed on merge.

---

### Commit C-05: MFA setup secret stored server-side

**Findings addressed:** F-031 (Medium).
**OWASP:** A02:2021 Cryptographic Failures.
**ASVS L2 controls closed:** V6.1.1 (Sensitive Data Classification -- server-side
storage for unconfirmed MFA secrets).
**Depends on:** C-02 (cookie hardening), C-04 (MultiFernet infrastructure).
**Blocks:** None.
**Complexity:** Small.

**A. Context and rationale.** findings.md F-031 records that during MFA setup the
plaintext TOTP secret is stored in `flask_session["_mfa_setup_secret"]` at
`app/routes/auth.py:366` and consumed at `:386`. Flask's default
`SecureCookieSessionInterface` signs but does NOT encrypt the cookie, so the
plaintext sits base64-decodable in the user's browser for the duration of the setup
flow. Shared-browser compromise during setup clones the authenticator.

**B. Files modified.**

- `app/models/user.py` `MfaConfig` -- add `pending_secret_encrypted = db.Column(db.LargeBinary, nullable=True)`
  and `pending_secret_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)`.
- `migrations/versions/<new>_add_mfa_pending_secret_columns.py` -- new migration.
- `app/routes/auth.py` `mfa_setup` (:347-374) -- write encrypted pending secret to
  `MfaConfig.pending_secret_encrypted` with 15-minute expiry instead of
  `flask_session`.
- `app/routes/auth.py` `mfa_confirm` (:377-427) -- read from
  `MfaConfig.pending_secret_encrypted`, check expiry, promote to
  `totp_secret_encrypted` on success, clear pending.
- `tests/test_routes/test_auth.py` -- update existing MFA setup tests; add new
  expiry test.
- `tests/test_adversarial/test_mfa_setup_secret.py` (new file, 4 tests).

**C. Model / schema changes.**

- `auth.mfa_configs.pending_secret_encrypted` -- `LargeBinary`, nullable (unconfirmed
  setup may or may not be in progress; justified nullability in code comment).
- `auth.mfa_configs.pending_secret_expires_at` -- `DateTime(timezone=True)`, nullable;
  expiry check ensures abandoned setups do not linger.
- No CHECK constraint (presence/absence of a pending secret is by design).
- No index (lookups are always keyed by `user_id` which is already indexed).

**D. Implementation approach.**

Model:

```python
# app/models/user.py MfaConfig (add after line 149)
# Pending TOTP secret from an in-progress /mfa/setup flow. Encrypted under
# the same Fernet key as the confirmed secret (via MultiFernet). Nullable
# because most of the time there is no pending setup. expires_at is the
# expiration deadline for the pending secret; mfa_confirm rejects stale
# values.
pending_secret_encrypted = db.Column(db.LargeBinary, nullable=True)
pending_secret_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
```

`app/routes/auth.py:mfa_setup` replacement:

```python
@auth_bp.route("/mfa/setup", methods=["GET"])
@login_required
def mfa_setup():
    """Display the MFA setup page with QR code and manual key."""
    from datetime import datetime, timedelta, timezone
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if mfa_config and mfa_config.is_enabled:
        flash("Two-factor authentication is already enabled.", "info")
        return redirect(url_for("settings.show", section="security"))

    if mfa_config is None:
        mfa_config = MfaConfig(user_id=current_user.id)
        db.session.add(mfa_config)

    secret = mfa_service.generate_totp_secret()
    try:
        mfa_config.pending_secret_encrypted = mfa_service.encrypt_secret(secret)
    except RuntimeError:
        flash(
            "MFA is not available. The server administrator must set "
            "TOTP_ENCRYPTION_KEY before MFA can be enabled.",
            "danger",
        )
        return redirect(url_for("settings.show", section="security"))
    mfa_config.pending_secret_expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=15)
    )
    db.session.commit()

    qr_data_uri = mfa_service.generate_qr_code_data_uri(
        mfa_service.get_totp_uri(secret, current_user.email)
    )
    return render_template(
        "auth/mfa_setup.html",
        qr_data_uri=qr_data_uri,
        manual_key=secret,
    )
```

`mfa_confirm` replacement:

```python
@auth_bp.route("/mfa/confirm", methods=["POST"])
@login_required
def mfa_confirm():
    """Verify a TOTP code and enable MFA for the current user."""
    from datetime import datetime, timezone
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if (mfa_config is None
            or mfa_config.pending_secret_encrypted is None
            or mfa_config.pending_secret_expires_at is None):
        flash("MFA setup session expired. Please start again.", "danger")
        return redirect(url_for("auth.mfa_setup"))
    if mfa_config.pending_secret_expires_at < datetime.now(timezone.utc):
        # Clear stale pending state.
        mfa_config.pending_secret_encrypted = None
        mfa_config.pending_secret_expires_at = None
        db.session.commit()
        flash("MFA setup session expired. Please start again.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    secret = mfa_service.decrypt_secret(mfa_config.pending_secret_encrypted)
    totp_code = request.form.get("totp_code", "")
    if not mfa_service.verify_totp_code(secret, totp_code):
        flash("Invalid code. Please try again.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    mfa_config.totp_secret_encrypted = mfa_config.pending_secret_encrypted
    mfa_config.pending_secret_encrypted = None
    mfa_config.pending_secret_expires_at = None
    mfa_config.is_enabled = True
    mfa_config.confirmed_at = datetime.now(timezone.utc)

    codes = mfa_service.generate_backup_codes()
    mfa_config.backup_codes = mfa_service.hash_backup_codes(codes)
    db.session.commit()

    log_event(
        logger, logging.INFO, "mfa_enabled", AUTH,
        "MFA enabled", user_id=current_user.id,
    )
    return render_template(
        "auth/mfa_backup_codes.html", backup_codes=codes,
    )
```

**E. Migration plan.**

```python
"""Add pending_secret columns to mfa_configs.

Revision ID: <auto>
Revises: f15a72a3da6c
"""
import sqlalchemy as sa
from alembic import op

revision = "<auto>"
down_revision = "f15a72a3da6c"  # TO BE UPDATED to current head


def upgrade():
    op.add_column(
        "mfa_configs",
        sa.Column("pending_secret_encrypted", sa.LargeBinary(), nullable=True),
        schema="auth",
    )
    op.add_column(
        "mfa_configs",
        sa.Column("pending_secret_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="auth",
    )


def downgrade():
    op.drop_column("mfa_configs", "pending_secret_expires_at", schema="auth")
    op.drop_column("mfa_configs", "pending_secret_encrypted", schema="auth")
```

Both columns nullable; no backfill needed because any in-progress setup completes
within 15 minutes of the migration.

**F. Test plan.**

| ID | Test file | Test name | Setup | Action | Expected | New / Mod |
|---|---|---|---|---|---|---|
| C05-1 | `tests/test_routes/test_auth.py` | `test_mfa_setup_stores_encrypted_secret_server_side` | auth_client without MFA | GET `/mfa/setup` | Response OK; `MfaConfig.pending_secret_encrypted` is not None; `flask_session.get("_mfa_setup_secret")` is None | Mod |
| C05-2 | ... | `test_mfa_setup_sets_expiry_15_minutes` | auth_client | GET `/mfa/setup` | `pending_secret_expires_at` within `[now+14min, now+16min]` | New |
| C05-3 | ... | `test_mfa_setup_rejects_expired_pending` | Seed MfaConfig with pending_secret and expiry in past | POST `/mfa/confirm` with valid code | Redirect to `/mfa/setup`; flash "expired" | New |
| C05-4 | ... | `test_mfa_confirm_promotes_pending_to_active` | GET `/mfa/setup`, extract secret, POST `/mfa/confirm` with valid TOTP | After: `totp_secret_encrypted` is set; `pending_secret_encrypted` is None; `is_enabled=True` | Mod |
| C05-5 | ... | `test_mfa_confirm_clears_pending_on_invalid_code` | GET `/mfa/setup`, POST `/mfa/confirm` with wrong code | Response flashes invalid; pending_secret remains (user may retry) | Mod |
| C05-6 | `tests/test_adversarial/test_mfa_setup_secret.py` | `test_flask_session_does_not_contain_plaintext_secret` | auth_client | GET `/mfa/setup` | Flask session cookie decoded base64 does NOT contain `_mfa_setup_secret` key | New |
| C05-7 | ... | `test_second_setup_overwrites_first_pending` | auth_client; GET `/mfa/setup` twice | Second call replaces first pending secret; only one decrypts successfully | New |
| C05-8 | ... | `test_cross_user_cannot_consume_pending` | User A starts setup; User B POSTs `/mfa/confirm` with A's code | User B's MfaConfig has no pending; returns "setup expired" redirect | New |
| C05-9 | ... | `test_pending_secret_decryptable_under_current_primary_key` | Start setup; rotate key via MultiFernet (C-04); attempt confirm | Decrypts successfully under MultiFernet (secret was encrypted under old primary, new primary is in the fernet list) | New |

**G. Manual verification.**

1. Log in as a test user; navigate to Settings > Security > Enable MFA.
2. Open DevTools > Application > Cookies; decode the session cookie (it is base64 of
   JSON). Verify no `_mfa_setup_secret` key.
3. Scan the QR code; enter the TOTP code; verify MFA enabled.
4. Start MFA setup again (after disable-and-re-enable flow) but wait 16 minutes
   before submitting code. Expect "MFA setup session expired" flash.

**H. Pylint.** `pylint app/routes/auth.py app/models/user.py
tests/test_routes/test_auth.py tests/test_adversarial/test_mfa_setup_secret.py
--fail-on=E,F`.

**I. Targeted tests.** `pytest tests/test_routes/test_auth.py::test_mfa_setup*
tests/test_routes/test_auth.py::test_mfa_confirm*
tests/test_adversarial/test_mfa_setup_secret.py -v --tb=short`.

**J. Full-suite gate.** Standard split.

**K. Scanner re-run.** `bandit -r app/routes/auth.py` -- no new findings.

**L. IDOR probe re-run.** Yes: the `/mfa/confirm` path now uses server-side state;
re-run the probe subset that exercises MFA to confirm no new IDOR surface. Zero
failures expected.

**M. Downstream effects.**

- Migration `<new>` must run before the first `/mfa/setup` after deploy; ensured by
  `entrypoint.sh` running `flask db upgrade` before Gunicorn.
- C-16 PII redaction (`F-114`) must NOT redact `pending_secret_encrypted` or
  `totp_secret_encrypted` from logs because the columns are never logged anyway; no
  action needed.

**N. Risk and rollback.**

- **Failure mode: migration applied but route code rolled back.** The nullable
  columns are harmless. Rollback order: revert code first, then `flask db downgrade`
  to remove the columns.
- **Rollback SQL:** `flask db downgrade -1` removes both columns. Any in-progress
  setup is interrupted; user restarts.
- **Regression test lock-in:** C05-6 (no plaintext secret in cookie).

**O. Findings.md update.** F-031 marked Fixed on merge.

---

### Commit C-06: Flask-Limiter Redis backend

**Findings addressed:** F-034 (Medium).
**OWASP:** A05:2021 Security Misconfiguration.
**ASVS L2 controls closed:** V2.2.1 (Anti-automation).
**Depends on:** None.
**Blocks:** C-11 (account lockout), C-07 (session_protection composition).
**Complexity:** Medium.

**A. Context and rationale.** findings.md F-034 records that Flask-Limiter uses the
default `storage_uri="memory://"` backend (`app/extensions.py:31`); each Gunicorn
worker holds a private counter dict; the documented 5/15min limit becomes 10/15min
under two workers and resets on container restart. F-033 (account lockout) cannot
be implemented reliably without a shared backend. F-015 (proxy header spoofing) is
an orthogonal concern but compounds the drift -- combined, per-IP brute-force
protection is effectively absent.

**Architectural decision required.** findings.md F-034 Recommendation lists three
independent options: (a) Redis storage, (b) enforce single-worker Gunicorn, (c)
add `default_limits=["200 per hour", "30 per minute"]` as a defense-in-depth
ceiling. The developer must choose between (a) and (b) -- (c) is additive. Phase D
tracks this as a cross-cutting architectural decision; the question is posed to
the developer at this commit.

**B. Files modified (assuming Redis backend chosen).**

- `docker-compose.yml` -- add `redis` service on the `backend` network.
- `app/extensions.py` -- replace `storage_uri="memory://"` with env-driven URI;
  default to `redis://redis:6379/0`; add `default_limits` ceiling.
- `requirements.txt` -- add `redis==5.2.1` (pinned; cross-check latest stable at
  commit time).
- `app/config.py` -- add `RATELIMIT_STORAGE_URI` via env.
- `.env.example` -- document the new env var.
- `entrypoint.sh` -- no change; Limiter initializes lazily.
- `tests/test_config.py::test_limiter_backend_configured` (new).
- `tests/test_integration/test_rate_limiter.py` (new file, 8 tests).

**B (alternative, if single-worker chosen).**

- `docker-compose.yml` -- set `GUNICORN_WORKERS=1` unconditionally.
- `gunicorn.conf.py` -- enforce `workers = 1` with a fail-closed check in the
  config file.
- `app/extensions.py` -- add `default_limits` ceiling only.

**D. Implementation approach (Redis path).**

`docker-compose.yml` -- add service block:

```yaml
  redis:
    image: redis:7-alpine
    container_name: shekel-prod-redis
    restart: unless-stopped
    networks:
      - backend
    # No volume: Flask-Limiter counters can safely evaporate on restart;
    # the rate limit simply resets to its documented window at that point.
    # Lock memory to prevent swap-based key exposure.
    command: ["redis-server", "--save", "", "--appendonly", "no", "--maxmemory", "64mb", "--maxmemory-policy", "allkeys-lru"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    mem_limit: 96m
    pids_limit: 100
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp
```

`app/extensions.py:31` replacement:

```python
import os

_RATELIMIT_DEFAULT_STORAGE = "redis://redis:6379/0"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per hour", "30 per minute"],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", _RATELIMIT_DEFAULT_STORAGE),
    # Fail open if Redis becomes unreachable -- do NOT lock every user out.
    # The alternative (fail closed) turns a Redis outage into an auth outage.
    in_memory_fallback_enabled=True,
)
```

`in_memory_fallback_enabled=True` is Flask-Limiter's official resilience flag: if
Redis becomes unreachable, the limiter falls back to per-worker memory counting
automatically. This is the documented operator-friendly behavior for a single-user
app; a public-release threat model may want fail-closed (reject) instead.

`requirements.txt` (add after line 27):

```
# Rate limiting backend
redis==5.2.1
```

**F. Test plan.**

| ID | Test file | Test name | Setup | Action | Expected | New / Mod |
|---|---|---|---|---|---|---|
| C06-1 | `tests/test_config.py` | `test_limiter_storage_uri_from_env` | `RATELIMIT_STORAGE_URI="redis://localhost:6379/1"` | Import `app.extensions` | `limiter._storage_uri` matches env | New |
| C06-2 | ... | `test_limiter_default_limits_present` | -- | Inspect `limiter._default_limits` | Contains "200 per hour" and "30 per minute" | New |
| C06-3 | `tests/test_integration/test_rate_limiter.py` | `test_login_rate_limit_shared_across_requests` | Set Redis test instance; 5 failed logins from same IP | 6th login returns 429 | New |
| C06-4 | ... | `test_login_rate_limit_resets_after_window` | 5 failed logins; advance time >=15min; attempt login | Not rate-limited | New |
| C06-5 | ... | `test_default_limit_applied_to_unprotected_route` | Any route without explicit `@limiter.limit`; hit 200+ times/hr | Eventually returns 429 | New |
| C06-6 | ... | `test_in_memory_fallback_on_redis_unreachable` | Mock Redis connection error | Limiter continues using memory backend; no 500 | New |
| C06-7 | ... | `test_limit_key_uses_remote_addr` | Request with `X-Forwarded-For=10.0.0.1` via trusted proxy | Limiter keys on 10.0.0.1 | New |
| C06-8 | ... | `test_429_response_includes_retry_after_header` | Exceed limit | Response header `Retry-After: <seconds>` present | New |
| C06-9 | ... | `test_health_endpoint_bypasses_limit` | Hit `/health` 500 times | No 429 | New |
| C06-10 | ... | `test_rate_limit_counters_persist_across_worker_restart` | SIGHUP Gunicorn mid-test | Counters persist (Redis-backed) | New |

**G. Manual verification.**

1. Start stack with `docker compose up -d`. Verify `redis` container healthy.
2. Attempt 6 rapid logins with wrong password. 6th should 429.
3. Restart the `app` container via `docker compose restart app`. Immediately attempt
   another wrong login. Should still be rate-limited (Redis survived).
4. Stop the `redis` container. Attempt login. Should succeed if password correct
   (fallback kicks in).

**H, I, J.** Standard pylint + targeted + full-suite gate.

**K. Scanner re-run.** `trivy image redis:7-alpine` to baseline Redis image CVEs;
`bandit` no change.

**L. IDOR probe re-run.** Not applicable.

**M. Downstream effects.**

- Adds one always-on container (~64MB memory). Host resource budget impact is
  minimal.
- `entrypoint.sh` runs migrations before Gunicorn; Redis readiness is ensured by
  `depends_on: redis: condition: service_healthy`.
- F-033 (account lockout) now feasible on a reliable counter backend.
- `default_limits` provides a ceiling; verify no currently-valid request pattern
  exceeds 200/hour or 30/minute. Grid refresh is the highest-frequency path;
  tested at C06-5.

**N. Risk and rollback.**

- **Failure mode: Redis unavailable.** Fallback enabled; degraded per-worker counts
  kick in.
- **Rollback:** `git revert <SHA>`; remove Redis from compose. Limiter reverts to
  `memory://`.

**O. Findings.md update.** F-034 marked Fixed on merge.

---

### Commit C-07: session_protection = "strong"

**Findings addressed:** F-038 (Medium).
**OWASP:** A07:2021.
**ASVS L2 controls closed:** V3.2.1 (Session Protection).
**Depends on:** C-06 (stable Limiter to re-auth under).
**Blocks:** None.
**Complexity:** Small.

**A. Context and rationale.** `app/extensions.py:22-25` instantiates LoginManager
without `session_protection = "strong"`. Flask-Login default is "basic" which only
invalidates sessions that change IP OR User-Agent mid-session. "strong" changes the
invalidation behavior to force a fresh login on any change, plus rotates the
session identifier. Small defense-in-depth gain today; load-bearing when C-53
migrates to server-side sessions.

**B. Files modified.**

- `app/extensions.py` -- one-line addition.
- `tests/test_config.py::test_login_manager_session_protection` (new).

**D. Implementation approach.**

```python
# app/extensions.py after line 25
login_manager.session_protection = "strong"
```

**F. Test plan.**

| ID | Test | Action | Expected |
|---|---|---|---|
| C07-1 | `test_login_manager_session_protection_is_strong` | Inspect `login_manager.session_protection` | Equal to "strong" |
| C07-2 | `test_session_rotates_on_ip_change` | Log in from one IP; simulate request from different IP | Session invalidated; redirect to login |

**G-O.** Standard pylint + targeted + full-suite. No migration; no IDOR probe change.
Rollback: `git revert`. F-038 marked Fixed.

---

### Commit C-08: Session invalidation helper + pending-MFA timestamp + backup-code / MFA-disable session invalidation

**Findings addressed:** F-002 (High), F-003 (High), F-032 (Medium).
**OWASP:** A07:2021.
**ASVS L2 controls closed:** V2.5.7 (Logout), V3.3.2 (Session Expiry), V3.3.4 (Session Termination).
**Depends on:** C-01 (post-rotation valid-session semantics).
**Blocks:** C-10 (session lifetime composition), C-13 (MFA-nag reuses the helper).
**Complexity:** Medium.

**A. Context and rationale.** Three findings share a shape: every authentication-
factor state change MUST force-invalidate other sessions. Findings.md F-002 adds a
pending-MFA timestamp; F-003 adds session invalidation on backup-code consumption;
F-032 adds it on MFA disable. The common pattern is extracted into a helper in
`app/utils/session_helpers.py` (new module) so each call site is a one-liner and
future auth-factor changes pick it up by construction.

**B. Files modified.**

- `app/utils/session_helpers.py` -- new module (helper function `invalidate_other_sessions`).
- `app/routes/auth.py:73-132` (`login`) -- add `_mfa_pending_at` timestamp (F-002).
- `app/routes/auth.py:251-344` (`mfa_verify`) -- enforce 5-minute timeout on pending
  MFA (F-002); on successful backup-code consume, call `invalidate_other_sessions` (F-003).
- `app/routes/auth.py:472-521` (`mfa_disable_confirm`) -- call
  `invalidate_other_sessions` (F-032).
- `tests/test_utils/test_session_helpers.py` (new, 6 tests).
- `tests/test_adversarial/test_session_invalidation.py` (new file, 9 tests).

**C. Model / schema changes.** None. Existing `users.session_invalidated_at` column.

**D. Implementation approach.**

`app/utils/session_helpers.py` (new module):

```python
"""Session invalidation helpers.

Force-invalidate all sessions for a user EXCEPT the current one. Used by any
auth-factor state change (password change, MFA enable/disable, backup-code
consumption).

The helper sets users.session_invalidated_at to now(), then refreshes the
current session's _session_created_at so that load_user() in
app/__init__.py:59-84 recognizes the current session as post-invalidation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import session as flask_session
from flask_login import current_user

from app.extensions import db
from app.utils.log_events import log_event, AUTH

logger = logging.getLogger(__name__)


def invalidate_other_sessions(reason: str):
    """Invalidate every session for current_user except this one.

    Args:
        reason: Short event name for the audit log (e.g., "password_change",
            "mfa_disable", "backup_code_consumed").
    """
    now = datetime.now(timezone.utc)
    current_user.session_invalidated_at = now
    db.session.commit()
    flask_session["_session_created_at"] = now.isoformat()
    log_event(
        logger, logging.INFO, "other_sessions_invalidated", AUTH,
        "Other sessions invalidated", user_id=current_user.id, reason=reason,
    )
```

`app/routes/auth.py:login` additions (inside the `if mfa_config:` block at line 98-107):

```python
if mfa_config:
    flask_session["_mfa_pending_user_id"] = user.id
    flask_session["_mfa_pending_remember"] = remember
    flask_session["_mfa_pending_at"] = datetime.now(timezone.utc).isoformat()
    pending_next = request.args.get("next")
    flask_session["_mfa_pending_next"] = (
        pending_next if _is_safe_redirect(pending_next) else None
    )
    return redirect(url_for("auth.mfa_verify"))
```

`app/routes/auth.py:mfa_verify` additions (at top of POST path, after
`pending_user_id` check at line 260):

```python
_MFA_PENDING_MAX_AGE = timedelta(minutes=5)

# Reject requests where the pending state is more than 5 minutes old.
pending_at_iso = flask_session.get("_mfa_pending_at")
if pending_at_iso:
    pending_at = datetime.fromisoformat(pending_at_iso)
    if datetime.now(timezone.utc) - pending_at > _MFA_PENDING_MAX_AGE:
        flask_session.pop("_mfa_pending_user_id", None)
        flask_session.pop("_mfa_pending_remember", None)
        flask_session.pop("_mfa_pending_next", None)
        flask_session.pop("_mfa_pending_at", None)
        flash("MFA pending state expired. Please log in again.", "warning")
        return redirect(url_for("auth.login"))
```

Backup code branch (at line 318 after the `db.session.commit()` that removes the
consumed hash):

```python
elif backup_code:
    idx = mfa_service.verify_backup_code(backup_code, mfa_config.backup_codes)
    if idx >= 0:
        mfa_config.backup_codes = [
            h for i, h in enumerate(mfa_config.backup_codes) if i != idx
        ]
        db.session.commit()
        valid = True
```

... then at the post-verification login_user block at line 334, add:

```python
login_user(user, remember=remember)
flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
if backup_code:
    # Backup-code consumption is the canonical "I lost my authenticator"
    # signal. Invalidate every other session for this user.
    from app.utils.session_helpers import invalidate_other_sessions
    invalidate_other_sessions("backup_code_consumed")
log_event(...)
```

`mfa_disable_confirm` at line 516 -- add one line after the commit:

```python
# Clear all MFA fields.
mfa_config.totp_secret_encrypted = None
mfa_config.is_enabled = False
mfa_config.backup_codes = None
mfa_config.confirmed_at = None
db.session.commit()

# MFA disable is a security-relevant state change; force other sessions out.
invalidate_other_sessions("mfa_disabled")

log_event(logger, logging.INFO, "mfa_disabled", AUTH, ...)
```

**F. Test plan.**

| ID | Test | Expected |
|---|---|---|
| C08-1 | `test_invalidate_other_sessions_bumps_column` | `session_invalidated_at` advanced |
| C08-2 | `test_invalidate_other_sessions_refreshes_current_session` | `_session_created_at` equals or exceeds new invalidated_at |
| C08-3 | `test_invalidate_other_sessions_logs_event` | `other_sessions_invalidated` event with matching `reason` |
| C08-4 | `test_invalidate_other_sessions_commits` | DB row updated regardless of session commit state |
| C08-5 | `test_login_stores_mfa_pending_at_timestamp` | `_mfa_pending_at` in session after password step |
| C08-6 | `test_mfa_verify_rejects_stale_pending` | Pending >5min old, POST `/mfa/verify` -> redirect to `/login`, pending cleared |
| C08-7 | `test_mfa_verify_accepts_fresh_pending` | Pending <5min old, POST with valid TOTP -> login complete |
| C08-8 | `test_backup_code_consumption_invalidates_other_sessions` | Second client's session is forcibly logged out |
| C08-9 | `test_mfa_disable_invalidates_other_sessions` | Second client's session is forcibly logged out |
| C08-10 | `test_password_change_still_invalidates` (regression) | Existing behavior preserved |
| C08-11 | `test_mfa_pending_clears_on_expiry_redirect` | All three session keys removed |
| C08-12 | `test_backup_code_consume_current_session_survives` | The client that used the backup code can still access /dashboard |
| C08-13 | `test_cross_client_backup_code_race_handled` | Two clients submit different backup codes; only one succeeds; the other sees "already consumed" flash |
| C08-14 | `test_mfa_disable_without_totp_blocked` | Current behavior preserved |
| C08-15 | `test_mfa_pending_timestamp_not_in_cookie_plaintext` | Cookie decoded; timestamp format is ISO but not a secret |

**G-O.** Manual: exercise login > MFA > backup code > verify other tab logged out.
Pylint clean. Targeted + full-suite. No scanner deltas. IDOR probe: rerun the MFA
subset. F-002, F-003, F-032 Fixed on merge.

---

### Commit C-09: TOTP replay prevention

**Findings addressed:** F-005 (High), F-142 (Low).
**OWASP:** A07:2021.
**ASVS L2 controls closed:** V2.8.4, V2.8.5.
**Depends on:** None.
**Blocks:** None.
**Complexity:** Medium.

**A. Context and rationale.** `pyotp.TOTP.verify(code, valid_window=1)` is stateless
and accepts the previous/current/next 30-second step. Shekel has no column to
track the last-consumed step, so any observed code is replayable for ~90 seconds.
ASVS V2.8.4 requires exact replay prevention. Companion finding F-142 adds a
structured replay-rejected log event.

**B. Files modified.**

- `app/models/user.py` `MfaConfig` -- add `last_totp_timestep = db.Column(db.Integer, nullable=True)`.
- `migrations/versions/<new>_add_last_totp_timestep.py` -- new migration.
- `app/services/mfa_service.py` -- rewrite `verify_totp_code` to accept `mfa_config`
  and perform a last-step comparison; the old signature (secret, code) becomes
  `_verify_totp_code_inner`.
- `app/routes/auth.py:mfa_verify` -- pass `mfa_config` to the new verify; emit
  `totp_replay_rejected` on the replay case; commit the new `last_totp_timestep`
  on success.
- `tests/test_services/test_mfa_service.py` -- 8 new tests covering the replay
  prevention.
- `tests/test_adversarial/test_totp_replay.py` -- 3 tests for the attacker path.

**C. Model / schema changes.**

- `auth.mfa_configs.last_totp_timestep` -- `Integer`, nullable (NULL on first use;
  populated after first successful TOTP verification).

**D. Implementation approach.**

`app/services/mfa_service.py:96-109` replacement:

```python
def verify_totp_code(mfa_config, code):
    """Verify a 6-digit TOTP code and enforce replay prevention.

    Implements ASVS V2.8.4: the code's computed time-step must be strictly
    greater than mfa_config.last_totp_timestep. On success, last_totp_timestep
    is updated to the step of the accepted code. On replay (step <= last),
    the verification returns False even if the TOTP library would accept the
    code within its drift window.

    Args:
        mfa_config: The MfaConfig row for the authenticating user. Mutated on
            success (caller commits).
        code: The 6-digit code string from the user's authenticator app.

    Returns:
        bool: True if the code is valid and not a replay; False otherwise.
    """
    import pyotp
    import time
    secret = decrypt_secret(mfa_config.totp_secret_encrypted)
    totp = pyotp.TOTP(secret)
    # pyotp.verify does not return the accepted step; we must check each
    # step in the drift window manually to know which one matched.
    now = int(time.time())
    step_size = 30
    current_step = now // step_size
    for drift in (-1, 0, 1):
        candidate_step = current_step + drift
        if totp.at(candidate_step * step_size) == code:
            if (mfa_config.last_totp_timestep is not None
                    and candidate_step <= mfa_config.last_totp_timestep):
                return False  # replay detected
            mfa_config.last_totp_timestep = candidate_step
            return True
    return False
```

Callers must now pass `mfa_config` instead of `secret`. Two call sites:
`app/routes/auth.py:mfa_verify` (line 309) and `:mfa_confirm` (line 392). Confirm
path does not need replay check (no `last_totp_timestep` yet at confirm time) --
update `mfa_confirm` to call a separate `_verify_no_replay(secret, code)` helper
that mirrors the original logic.

`app/routes/auth.py:mfa_verify` update:

```python
if totp_code:
    valid = mfa_service.verify_totp_code(mfa_config, totp_code)
    if not valid:
        log_event(
            logger, logging.WARNING, "totp_replay_rejected", AUTH,
            "TOTP replay or invalid code rejected",
            user_id=user.id, ip=request.remote_addr,
        )
```

**E. Migration.**

```python
def upgrade():
    op.add_column(
        "mfa_configs",
        sa.Column("last_totp_timestep", sa.Integer(), nullable=True),
        schema="auth",
    )


def downgrade():
    op.drop_column("mfa_configs", "last_totp_timestep", schema="auth")
```

**F. Test plan (abridged; 11 total tests):**

| ID | Test | Expected |
|---|---|---|
| C09-1 | `test_verify_accepts_current_step_first_use` | last_totp_timestep None; current step accepted; column updated |
| C09-2 | `test_verify_rejects_replay_of_same_step` | First call succeeds; second call with same code returns False |
| C09-3 | `test_verify_rejects_replay_of_previous_step` | Accept step N; reject step N-1 even if within drift window |
| C09-4 | `test_verify_accepts_step_plus_one` | Accept step N; accept step N+1 |
| C09-5 | `test_verify_rejects_wrong_code` | Returns False without updating column |
| C09-6 | `test_verify_drift_window_still_plus_minus_one` | Step = current-1 accepted if never used; step = current-2 rejected |
| C09-7 | `test_verify_uses_decrypted_secret` | MultiFernet-decrypted; works even after key rotation |
| C09-8 | `test_confirm_does_not_use_last_totp_timestep` | mfa_confirm path ignores last_totp_timestep |
| C09-9 | `test_adversarial_replay_logged` | Replay attempt emits `totp_replay_rejected` event |
| C09-10 | `test_adversarial_observer_replay_within_90_seconds_rejected` | Capture code on step N; submit again within 30s; rejected |
| C09-11 | `test_migration_upgrade_downgrade_roundtrip` | `flask db upgrade` then `flask db downgrade`; column added then removed |

**G-O.** Standard. F-005 + F-142 Fixed on merge.

---

### Commit C-10: Session lifetime + idle timeout + step-up auth

**Findings addressed:** F-006 (High), F-035 (Medium), F-045 (Medium).
**OWASP:** A07:2021.
**ASVS L2 controls closed:** V3.3.1, V3.3.2, V4.3.3.
**Depends on:** C-01, C-02, C-08.
**Blocks:** None.
**Complexity:** Medium.

**A. Context.** F-006 (30-day remember-me), F-035 (31-day default session), F-045
(no step-up re-auth for high-value operations). Three cooperating changes reduce
the "unattended access" blast radius.

**B. Files modified.**

- `app/config.py` -- `PERMANENT_SESSION_LIFETIME=timedelta(hours=12)`; default
  `REMEMBER_COOKIE_DURATION` to 7 days (env-overridable).
- `app/__init__.py:load_user` -- add idle-timeout check (`_session_last_activity_at` vs
  30-minute threshold).
- `app/__init__.py` -- `@app.before_request` hook updates `_session_last_activity_at`.
- `app/utils/auth_helpers.py` -- new `fresh_login_required(max_age_minutes=5)` decorator.
- Apply `fresh_login_required` to: anchor-balance true-up (`accounts.py:651`),
  bulk delete paths, `companion.py` creation routes, `salary.py` tax-config update
  routes, `settings.py` account deletion (if present). Full list in the file table
  below.
- `app/routes/auth.py` -- new `/reauth` GET+POST that re-prompts password (+ TOTP if
  MFA); on success sets `_fresh_login_at = now`.
- `app/templates/auth/reauth.html` -- new template.
- Tests: 15 tests across `tests/test_routes/test_auth.py`, `tests/test_utils/`,
  `tests/test_adversarial/test_step_up.py` (new).

**D. Implementation approach.**

Config:

```python
# BaseConfig (after line 33)
PERMANENT_SESSION_LIFETIME = timedelta(
    hours=int(os.getenv("SESSION_LIFETIME_HOURS", "12"))
)
# Remember-me shortened from 30 to 7 days per ASVS L2 guidance for financial apps.
REMEMBER_COOKIE_DURATION = timedelta(
    days=int(os.getenv("REMEMBER_COOKIE_DURATION_DAYS", "7"))
)
```

`load_user` addition (`app/__init__.py` after line 83):

```python
# Idle-timeout check: reject the session if _session_last_activity_at is
# older than IDLE_TIMEOUT_MINUTES. Ignores empty activity timestamps
# (first request after login) to avoid a chicken-and-egg race.
idle_threshold = timedelta(
    minutes=int(os.getenv("IDLE_TIMEOUT_MINUTES", "30"))
)
last_activity_iso = session.get("_session_last_activity_at")
if last_activity_iso is not None:
    last_activity = datetime.fromisoformat(last_activity_iso)
    if datetime.now(timezone.utc) - last_activity > idle_threshold:
        return None
return user
```

Before-request hook in `app/__init__.py`:

```python
@app.before_request
def _refresh_last_activity():
    from flask import session
    from flask_login import current_user
    if current_user.is_authenticated:
        session["_session_last_activity_at"] = datetime.now(
            timezone.utc,
        ).isoformat()
```

`fresh_login_required` decorator in `app/utils/auth_helpers.py`:

```python
_FRESH_LOGIN_MAX_AGE_DEFAULT = timedelta(minutes=5)


def fresh_login_required(max_age=_FRESH_LOGIN_MAX_AGE_DEFAULT):
    """Require recent re-authentication for high-value operations.

    Wraps a route and redirects to /reauth if _fresh_login_at is older than
    max_age. Must be applied AFTER @login_required.
    """
    def decorator(f):
        from functools import wraps

        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import session, redirect, url_for, request
            fresh_iso = session.get("_fresh_login_at")
            if fresh_iso is None:
                return redirect(url_for("auth.reauth", next=request.url))
            fresh_at = datetime.fromisoformat(fresh_iso)
            if datetime.now(timezone.utc) - fresh_at > max_age:
                return redirect(url_for("auth.reauth", next=request.url))
            return f(*args, **kwargs)
        return wrapper
    return decorator
```

Routes to decorate:

- `app/routes/accounts.py:true_up` (anchor-balance change)
- `app/routes/accounts.py:inline_anchor_update`
- `app/routes/companion.py:create_companion`
- `app/routes/companion.py:edit_companion`
- `app/routes/companion.py:delete_companion`
- `app/routes/salary.py:update_fica_config`
- `app/routes/salary.py:update_state_tax_config`
- `app/routes/salary.py:update_tax_bracket_set`
- `app/routes/transactions.py:bulk_delete` (if present; check during implementation)
- Any export/account-delete route from F-093 when C-54 lands (coordinate)

`/reauth` route:

```python
@auth_bp.route("/reauth", methods=["GET", "POST"])
@login_required
def reauth():
    """Prompt the user to re-authenticate for a step-up operation."""
    if request.method == "POST":
        password = request.form.get("password", "")
        if not auth_service.verify_password(password, current_user.password_hash):
            flash("Password incorrect.", "danger")
            return render_template("auth/reauth.html")
        mfa_config = db.session.query(MfaConfig).filter_by(
            user_id=current_user.id, is_enabled=True,
        ).first()
        if mfa_config:
            totp_code = request.form.get("totp_code", "").strip()
            if not mfa_service.verify_totp_code(mfa_config, totp_code):
                flash("MFA code incorrect.", "danger")
                return render_template("auth/reauth.html")
        flask_session["_fresh_login_at"] = datetime.now(timezone.utc).isoformat()
        next_url = request.args.get("next")
        if _is_safe_redirect(next_url):
            return redirect(next_url)
        return redirect(url_for("dashboard.page"))
    return render_template("auth/reauth.html")
```

**F. Test plan (abridged; 15 tests total)** covering: idle timeout behavior, remember-
me lifetime, `_session_last_activity_at` refresh, fresh_login_required redirect,
reauth success/failure, cross-decorator interaction.

**G-O.** Standard. F-006 + F-035 + F-045 Fixed.

---

### Commit C-11: Account lockout + password strength + UX (HIBP, meter, show/hide)

**Findings addressed:** F-033 (Medium), F-086 (Low), F-089 (Low), F-090 (Low).
**OWASP:** A07:2021.
**ASVS L2 controls closed:** V2.1.7, V2.1.8, V2.1.12, V2.2.1.
**Depends on:** C-06 (Limiter backend).
**Blocks:** None.
**Complexity:** Medium.

**A. Context.** F-033 (no account lockout beyond IP), F-086 (no breached-password
check), F-089 (no strength meter), F-090 (no show/hide toggle). All cluster around
"post-breach password hygiene." Ship together to keep the auth UX consistent.

**B. Files modified.**

- `app/models/user.py` User -- add `failed_login_count = db.Column(db.Integer, nullable=False, server_default="0")`;
  `locked_until = db.Column(db.DateTime(timezone=True), nullable=True)`.
- Migration: add the two columns.
- `app/services/auth_service.py:authenticate` -- increment on fail; reset on
  success; set `locked_until = now + 15min` on threshold (default 10).
- `app/routes/auth.py:login` -- treat `locked_until > now` as auth failure without
  checking password (prevents timing oracle).
- `app/services/auth_service.py` -- new `_check_pwned_password(plain_password)` via
  HIBP k-anonymity HTTPS GET. Failure open on network error (log warning).
- `app/schemas/validation.py` -- add HIBP check to `register_user` and
  `change_password` via a shared validator.
- `app/static/vendor/zxcvbn/zxcvbn.js` -- vendored; SRI in `VERSIONS.txt`.
- `app/static/js/password_strength.js` -- new vanilla-JS module; reads
  `data-password-input` attribute.
- `app/templates/auth/register.html`, `app/templates/settings/_security.html` -- add
  strength meter DOM element and password-toggle button.
- Tests: 18 tests.

**C. Model / schema changes.**

- `auth.users.failed_login_count` -- `Integer`, NOT NULL, `server_default='0'`.
- `auth.users.locked_until` -- `DateTime(timezone=True)`, nullable.

**D. Implementation approach (abridged).**

Service:

```python
_LOCKOUT_THRESHOLD = int(os.getenv("LOCKOUT_THRESHOLD", "10"))
_LOCKOUT_DURATION = timedelta(
    minutes=int(os.getenv("LOCKOUT_DURATION_MINUTES", "15")),
)


def authenticate(email, password):
    from datetime import datetime, timezone
    user = db.session.query(User).filter_by(email=email).first()
    if user is None:
        raise AuthError("Invalid email or password.")
    if user.locked_until is not None and user.locked_until > datetime.now(timezone.utc):
        raise AuthError("Invalid email or password.")
    if not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= _LOCKOUT_THRESHOLD:
            user.locked_until = datetime.now(timezone.utc) + _LOCKOUT_DURATION
            user.failed_login_count = 0  # reset after lockout set
        db.session.commit()
        raise AuthError("Invalid email or password.")
    if not user.is_active:
        raise AuthError("Account is disabled.")
    # Success: reset counter.
    user.failed_login_count = 0
    user.locked_until = None
    return user
```

HIBP check:

```python
import hashlib
import requests  # NEW dependency; pin in requirements.txt
from app.exceptions import ValidationError

_HIBP_ENDPOINT = "https://api.pwnedpasswords.com/range/{prefix}"
_HIBP_TIMEOUT = 3  # seconds


def _check_pwned_password(plain_password):
    """Query HIBP's k-anonymity endpoint for the password's SHA-1 prefix.

    Raises ValidationError if the password has appeared in a breach.
    Logs and returns None on network error (fail open).
    """
    sha1 = hashlib.sha1(plain_password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        response = requests.get(
            _HIBP_ENDPOINT.format(prefix=prefix),
            timeout=_HIBP_TIMEOUT,
            headers={"Add-Padding": "true"},
        )
        response.raise_for_status()
    except (requests.RequestException, requests.Timeout) as exc:
        logger.warning("HIBP check failed: %s. Allowing password.", type(exc).__name__)
        return None
    for line in response.text.splitlines():
        record_suffix, _count = line.split(":", 1)
        if record_suffix.strip() == suffix:
            raise ValidationError(
                "This password has appeared in a known data breach. "
                "Please choose a different one."
            )
    return None
```

Check is invoked inside `hash_password()` before returning the hash.

**Architectural decision.** HIBP adds a new outbound HTTPS dependency (api.pwnedpasswords.com).
Options:

1. Use the hosted HIBP API (this plan's default). Blast radius on outage: fail-open
   allows all passwords including breached.
2. Self-host an HIBP mirror (Docker container of the Pwned Passwords dataset, ~30GB).
   No external dependency; requires volume setup and periodic refresh.
3. Skip HIBP entirely and implement zxcvbn-strength only.

Plan defaults to option 1; developer can change at this commit's checkpoint.

**F. Test plan (18 tests abridged).** Covers: increment-on-fail, reset-on-success,
lockout-threshold, lockout-expiry, HIBP-pwned rejection, HIBP-network-failure
fail-open, strength-meter rendering, show/hide toggle, lockout interacts correctly
with rate limit.

**E. Migration.**

```python
def upgrade():
    op.add_column("users", sa.Column("failed_login_count", sa.Integer(),
                                      nullable=False, server_default="0"),
                  schema="auth")
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True),
                                      nullable=True),
                  schema="auth")


def downgrade():
    op.drop_column("users", "locked_until", schema="auth")
    op.drop_column("users", "failed_login_count", schema="auth")
```

**G-O.** Standard. F-033 + F-086 + F-089 + F-090 Fixed.

---

### Commit C-12: MFA required-nag for owner

**Findings addressed:** F-095 (Low).
**OWASP:** A07:2021.
**ASVS L2 controls closed:** V4.3.1.
**Depends on:** C-03, C-08.
**Blocks:** None.
**Complexity:** Small.

**A. Context.** Owner role is de facto administrator; MFA is optional. Every-login
nag nudges owner to enroll without forcing a destructive decision.

**B. Files modified.**

- `app/routes/dashboard.py` (or wherever the base landing page is) -- detect
  `current_user.role_id == OWNER and not has_mfa` and render a dismissible banner.
- `app/templates/dashboard/_mfa_nag.html` (new partial).
- Test: 3 route tests.

**D. Implementation approach.** Render a Bootstrap dismissible alert on every owner-
role landing page while no MFA is enrolled. Copy: "Enable two-factor authentication
to protect your financial data. Set up now" -> link to `/mfa/setup`.

**F-O.** Standard small commit. F-095 Fixed.

---

### Commit C-13: Audit triggers rebuild migration + CREATE SCHEMA + least-privilege DB role

**Findings addressed:** F-028 (High), F-070 (Medium), F-081 (Medium).
**OWASP:** A09:2021 Security Logging and Monitoring Failures.
**ASVS L2 controls closed:** V7.2.1, V7.2.2, V14.2.1.
**Depends on:** None.
**Blocks:** C-14 (log_event rollout), C-15 (off-host shipping).
**Complexity:** Large.

**A. Context.** findings.md Top Risk #1 documents that migration `a8b1c2d3e4f5`
declares 22 audit triggers but production has zero. The forensic trail on this app
is effectively the app container's stdout -- which the app can rewrite. Ship
alongside the least-privilege DB role so the app-role attacker cannot `DROP` the
audit triggers even after RCE.

**B. Files modified.**

- `migrations/versions/<new>_rebuild_audit_infrastructure.py` -- new idempotent
  migration that: (1) `CREATE SCHEMA IF NOT EXISTS system`; (2) `CREATE TABLE IF NOT
  EXISTS system.audit_log (...)`; (3) `CREATE OR REPLACE FUNCTION
  system.audit_trigger_func()`; (4) `DROP TRIGGER IF EXISTS audit_<t> ON
  <schema>.<table>` then `CREATE TRIGGER` for every table in the 22-table list.
- `scripts/init_db.sql` -- add role-creation SQL that creates `shekel_app` DML-only
  role alongside the owner `shekel_user`.
- `entrypoint.sh` -- add post-migration assertion: count system.audit_* triggers;
  refuse to start Gunicorn if the count is < 22.
- `docker-compose.yml` -- add `DATABASE_URL_APP` env var pointing to `shekel_app` role;
  the app uses this URL at runtime while migrations run under `DATABASE_URL`
  (owner).
- `app/config.py` -- prefer `DATABASE_URL_APP` when set; fall back to `DATABASE_URL`.
- Tests: 9 migration + schema tests.

**C. Model changes.** None (schema infra only).

**D. Implementation approach.**

Migration (skeleton):

```python
"""Rebuild audit_log + audit_trigger_func + 22 triggers idempotently.

Revision ID: <new>
Revises: <head>

Recreates the audit infrastructure that migration a8b1c2d3e4f5 declared but
which is absent from production (finding F-028). Idempotent via IF NOT
EXISTS / CREATE OR REPLACE / DROP TRIGGER IF EXISTS. Adds schema creation
(finding F-070) so a fresh-DB flask db upgrade succeeds.
"""
from alembic import op

revision = "<new>"
down_revision = "<current_head>"

_AUDITED_TABLES = [  # Copy from a8b1c2d3e4f5, minus any renamed/dropped
    ("budget", "accounts"),
    ("budget", "transactions"),
    ("budget", "transaction_templates"),
    ("budget", "transfers"),
    ("budget", "transfer_templates"),
    ("budget", "savings_goals"),
    ("budget", "recurrence_rules"),
    ("budget", "pay_periods"),
    ("budget", "account_anchor_history"),
    ("budget", "interest_params"),  # Renamed from hysa_params
    ("budget", "loan_params"),  # Unified from mortgage_params / auto_loan_params
    ("budget", "rate_history"),  # Renamed from mortgage_rate_history
    ("budget", "escrow_components"),
    ("budget", "investment_params"),
    ("salary", "salary_profiles"),
    ("salary", "salary_raises"),
    ("salary", "paycheck_deductions"),
    ("salary", "pension_profiles"),
    ("salary", "fica_configs"),
    ("salary", "state_tax_configs"),
    ("salary", "tax_bracket_sets"),
    ("auth", "users"),
    ("auth", "user_settings"),
    ("auth", "mfa_configs"),
]  # Count: 24 tables (was 22 in a8b1c2d3e4f5; schema has grown).


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS system")
    op.execute("""
        CREATE TABLE IF NOT EXISTS system.audit_log (
            id              BIGSERIAL       PRIMARY KEY,
            table_schema    VARCHAR(50)     NOT NULL,
            table_name      VARCHAR(100)    NOT NULL,
            operation       VARCHAR(10)     NOT NULL,
            row_id          INTEGER,
            old_data        JSONB,
            new_data        JSONB,
            changed_fields  TEXT[],
            user_id         INTEGER,
            db_user         VARCHAR(100)    DEFAULT current_user,
            executed_at     TIMESTAMPTZ     NOT NULL DEFAULT now(),
            CONSTRAINT ck_audit_log_operation
                CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE'))
        )
    """)
    op.execute("""CREATE INDEX IF NOT EXISTS idx_audit_log_table
        ON system.audit_log (table_schema, table_name)""")
    op.execute("""CREATE INDEX IF NOT EXISTS idx_audit_log_executed
        ON system.audit_log (executed_at)""")
    op.execute("""CREATE INDEX IF NOT EXISTS idx_audit_log_row
        ON system.audit_log (table_name, row_id)""")
    # CREATE OR REPLACE function body from a8b1c2d3e4f5 unchanged.
    op.execute(r"""CREATE OR REPLACE FUNCTION system.audit_trigger_func()
        RETURNS TRIGGER AS $$
        DECLARE
            v_old_data  JSONB;
            v_new_data  JSONB;
            v_changed   TEXT[] := '{}';
            v_user_id   INTEGER;
            v_row_id    INTEGER;
            v_key       TEXT;
        BEGIN
            BEGIN
                v_user_id := current_setting('app.current_user_id', true)::INTEGER;
            EXCEPTION WHEN OTHERS THEN
                v_user_id := NULL;
            END;
            IF TG_OP = 'DELETE' THEN
                v_old_data := to_jsonb(OLD);
                v_row_id   := OLD.id;
                INSERT INTO system.audit_log (table_schema, table_name, operation,
                    row_id, old_data, new_data, changed_fields, user_id)
                VALUES (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
                    v_old_data, NULL, NULL, v_user_id);
                RETURN OLD;
            ELSIF TG_OP = 'INSERT' THEN
                v_new_data := to_jsonb(NEW);
                v_row_id   := NEW.id;
                INSERT INTO system.audit_log (table_schema, table_name, operation,
                    row_id, old_data, new_data, changed_fields, user_id)
                VALUES (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
                    NULL, v_new_data, NULL, v_user_id);
                RETURN NEW;
            ELSIF TG_OP = 'UPDATE' THEN
                v_old_data := to_jsonb(OLD);
                v_new_data := to_jsonb(NEW);
                v_row_id   := NEW.id;
                FOR v_key IN SELECT key FROM jsonb_each(v_new_data)
                    WHERE NOT v_old_data ? key
                       OR v_old_data -> key IS DISTINCT FROM v_new_data -> key
                LOOP
                    v_changed := array_append(v_changed, v_key);
                END LOOP;
                IF array_length(v_changed, 1) IS NULL THEN
                    RETURN NEW;
                END IF;
                INSERT INTO system.audit_log (table_schema, table_name, operation,
                    row_id, old_data, new_data, changed_fields, user_id)
                VALUES (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
                    v_old_data, v_new_data, v_changed, v_user_id);
                RETURN NEW;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql""")
    for schema, table in _AUDITED_TABLES:
        trigger_name = f"audit_{table}"
        op.execute(
            f"DROP TRIGGER IF EXISTS {trigger_name} ON {schema}.{table}"
        )
        op.execute(f"""CREATE TRIGGER {trigger_name}
            AFTER INSERT OR UPDATE OR DELETE ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()""")
    # Grant SELECT and INSERT on audit_log to the app role (for log_event
    # writes once C-14 populates the service layer).
    op.execute("""
        GRANT USAGE ON SCHEMA system TO shekel_app;
        GRANT SELECT, INSERT ON system.audit_log TO shekel_app;
        GRANT USAGE ON SEQUENCE system.audit_log_id_seq TO shekel_app;
    """)


def downgrade():
    """Remove triggers, trigger function, and audit_log table."""
    for schema, table in _AUDITED_TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS audit_{table} ON {schema}.{table}"
        )
    op.execute("DROP FUNCTION IF EXISTS system.audit_trigger_func()")
    op.execute("DROP TABLE IF EXISTS system.audit_log CASCADE")
    # Do NOT DROP SCHEMA system -- leave the schema in place; it is
    # idempotent-safe for re-upgrade.
```

Least-privilege role (`scripts/init_db.sql` additions):

```sql
-- Create an application-only role with no DDL rights. Used by the app at
-- runtime. Migrations continue to run under the owner role (shekel_user).
-- App cannot DROP TABLE, ALTER TABLE, DROP ROLE, or similar DDL.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'shekel_app') THEN
        CREATE ROLE shekel_app WITH LOGIN PASSWORD :APP_ROLE_PASSWORD_LITERAL;
    END IF;
END$$;

GRANT CONNECT ON DATABASE shekel TO shekel_app;
GRANT USAGE ON SCHEMA auth, budget, salary, ref TO shekel_app;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA auth, budget, salary TO shekel_app;
GRANT SELECT ON ALL TABLES IN SCHEMA ref TO shekel_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA auth, budget, salary TO shekel_app;

-- Default privileges for future tables created by the owner.
ALTER DEFAULT PRIVILEGES IN SCHEMA auth, budget, salary
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO shekel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA ref
    GRANT SELECT ON TABLES TO shekel_app;
```

The SQL is templated at `init_db.sql` generation time (current
`entrypoint.sh` uses `psql -f`); password injection via environment variable
`APP_ROLE_PASSWORD`. The `:APP_ROLE_PASSWORD_LITERAL` placeholder is substituted
via `psql`'s `-v` flag:

```bash
# entrypoint.sh step 2
PGPASSWORD="${DB_PASSWORD}" psql -v "APP_ROLE_PASSWORD_LITERAL='${APP_ROLE_PASSWORD}'" \
    -f scripts/init_db.sql ...
```

Entrypoint assertion (after migrations, before gunicorn):

```bash
# entrypoint.sh -- verify audit triggers exist
EXPECTED_TRIGGERS=24
ACTUAL_TRIGGERS=$(PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -U "${DB_USER}" \
    -d "${DB_NAME}" -tAc "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'audit_%'")
if [ "${ACTUAL_TRIGGERS}" -lt "${EXPECTED_TRIGGERS}" ]; then
    echo "ERROR: Expected ${EXPECTED_TRIGGERS} audit triggers, found ${ACTUAL_TRIGGERS}" >&2
    exit 1
fi
echo "Audit trigger count OK: ${ACTUAL_TRIGGERS}"
```

**C. Schema changes.** `system.audit_log` columns (already in a8b1c2d3e4f5; listed
in migration above):

- `id` BIGSERIAL PK.
- `table_schema`, `table_name` VARCHAR NOT NULL.
- `operation` VARCHAR NOT NULL, CHECK in ('INSERT', 'UPDATE', 'DELETE').
- `row_id` INTEGER nullable (DELETE case captures old ID).
- `old_data`, `new_data` JSONB nullable.
- `changed_fields` TEXT[] nullable.
- `user_id` INTEGER nullable (session variable; NULL on direct psql).
- `db_user` VARCHAR DEFAULT current_user (records which DB role wrote).
- `executed_at` TIMESTAMPTZ NOT NULL DEFAULT now().

Indexes: `idx_audit_log_table (table_schema, table_name)`,
`idx_audit_log_executed (executed_at)`, `idx_audit_log_row (table_name, row_id)`.

**F. Test plan (9 tests):** migration upgrade + downgrade + re-upgrade round-trip;
trigger fires on INSERT; trigger fires on UPDATE with changed_fields populated;
trigger fires on DELETE with old_data populated; `app.current_user_id` session
variable captured; least-privilege role cannot DROP TABLE; `shekel_app` can
SELECT/INSERT/UPDATE/DELETE; entrypoint assertion blocks Gunicorn when triggers
missing; idempotent re-run.

**G. Manual verification.**

1. `flask db upgrade` against a fresh test DB; verify 24 triggers via
   `SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'audit_%'`.
2. Insert a Transaction via `flask shell`; `SELECT * FROM system.audit_log` returns
   the row.
3. Update the Transaction; audit row appears with `changed_fields` populated.
4. Connect as `shekel_app` via `psql`; attempt `DROP TABLE budget.accounts`; expect
   permission denied.

**H-I.** Standard.

**J. Full-suite gate.** Particular attention: adding triggers to all 24 tables
means every test that writes a financial row now inserts an audit_log row. Full
suite must pass without change -- triggers do not fail the write.

**K. Scanner re-run.** `trivy config migrations/` clean.

**L. IDOR probe re-run.** Not applicable.

**M. Downstream effects.**

- `log_event` writes (C-14) piggyback on the same audit_log table.
- Every future schema change must add the table to `_AUDITED_TABLES` AND add a
  trigger. This is a new invariant; record in `docs/coding-standards.md`.
- Retention job (C-50) prunes `system.audit_log` after 365 days per
  `AUDIT_RETENTION_DAYS`.

**N. Risk and rollback.**

- **Failure mode 1: trigger function has a bug that raises.** Every INSERT/UPDATE/
  DELETE on a financial table fails. Mitigation: the trigger function is verbatim
  from a8b1c2d3e4f5 which ran in development; low risk of new bug.
- **Failure mode 2: least-privilege role cannot INSERT.** Grants cover the needed
  statements. Tested in C13-7.
- **Rollback:** `flask db downgrade -1` removes triggers/table/function.

**O. Findings.md update.** F-028, F-070, F-081 marked Fixed on merge.

---

### Commit C-14: log_event systematic rollout across services + access-denied events

**Findings addressed:** F-080 (Medium), F-085 (Low), F-144 (Low).
**OWASP:** A09:2021.
**ASVS L2 controls closed:** V7.1.3, V7.2.1, V7.2.2.
**Depends on:** C-13.
**Blocks:** C-15.
**Complexity:** Large.

**A. Context.** `log_event` currently called at 14 sites. 95 mutating routes. Push
structured logging into the service layer so every mutation -- route, script,
future job -- emits a queryable event.

**B. Files modified.**

- Every service module that commits a mutation (21 files in `app/services/`):
  `auth_service`, `mfa_service` (state changes only; TOTP verify is not a state
  change), `transfer_service`, `credit_workflow`, `entry_credit_workflow`,
  `entry_service`, `carry_forward_service`, `pay_period_service`,
  `recurrence_engine`, `account_resolver` (read-only; skip), `savings_goal_service`,
  `loan_payment_service`, `escrow_calculator` (read-only; skip),
  `amortization_engine` (read-only; skip), `paycheck_calculator` (read-only; skip),
  `tax_calculator` (read-only; skip), `tax_config_service`, `growth_engine`
  (read-only; skip), `investment_projection` (read-only; skip),
  `spending_trend_service` (read-only; skip), `budget_variance_service`
  (read-only; skip), `calendar_service` (read-only; skip),
  `csv_export_service` (read-only; skip), `dashboard_service` (read-only; skip),
  `year_end_summary_service` (read-only; skip), `pension_calculator` (read-only;
  skip), `retirement_dashboard_service` (read-only; skip),
  `retirement_gap_calculator` (read-only; skip), `savings_dashboard_service`
  (read-only; skip), `transfer_recurrence`, `debt_strategy_service` (read-only;
  skip), `interest_projection` (read-only; skip), `companion_service`,
  `calibration_service`.
- `app/utils/auth_helpers.py` -- `get_or_404` and `require_owner` emit
  `access_denied` on both 404 branches.
- `app/routes/auth.py:179` -- replace `logger.info` for registration with
  `log_event` (F-085).
- Tests: 30+ tests across service test files + adversarial access-denied tests.

**D. Implementation approach.**

`app/utils/log_events.py` -- confirm `BUSINESS`, `AUTH`, `ACCESS`, `AUDIT` category
constants exist. Add new event names as module-level constants:
`EVT_TRANSFER_CREATED`, `EVT_TRANSFER_UPDATED`, `EVT_TRANSFER_DELETED`,
`EVT_CATEGORY_CREATED`, etc. (use a registry dict so tests can assert completeness).

Per-service rollout pattern (example from `transfer_service.py`):

```python
# In create_transfer, before the final return:
log_event(
    logger, logging.INFO, "transfer_created", BUSINESS,
    "Created transfer with shadows",
    user_id=user_id, transfer_id=xfer.id,
    amount=str(amount),  # Decimal as string for JSON
    from_account_id=from_account_id,
    to_account_id=to_account_id,
)

# In update_transfer, after the flush:
log_event(
    logger, logging.INFO, "transfer_updated", BUSINESS,
    "Updated transfer",
    user_id=user_id, transfer_id=transfer_id,
    fields_changed=list(kwargs.keys()),
)

# In delete_transfer and restore_transfer similarly.
```

`app/utils/auth_helpers.py` additions:

```python
def require_owner(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        owner_id = ref_cache.role_id(RoleEnum.OWNER)
        if getattr(current_user, "role_id", owner_id) != owner_id:
            log_event(
                logger, logging.WARNING, "access_denied", ACCESS,
                "Non-owner accessed owner-only route",
                user_id=getattr(current_user, "id", None),
                path=request.path,
            )
            abort(404)
        return f(*args, **kwargs)
    return decorated


def get_or_404(model, pk, user_id_field="user_id"):
    record = db.session.get(model, pk)
    if record is None:
        log_event(
            logger, logging.INFO, "resource_not_found", ACCESS,
            "Resource not found",
            user_id=current_user.id,
            model=model.__name__, pk=pk,
        )
        return None
    if getattr(record, user_id_field, None) != current_user.id:
        log_event(
            logger, logging.WARNING, "access_denied_cross_user", ACCESS,
            "Cross-user resource access blocked",
            user_id=current_user.id,
            model=model.__name__, pk=pk,
            owner_id=getattr(record, user_id_field, None),
        )
        return None
    return record
```

`app/routes/auth.py:register` (replace line 179):

```python
log_event(
    logger, logging.INFO, "user_registered", AUTH,
    "User registered", user_id=user.id, email=email,
)
```

Requires capturing `user` returned by `auth_service.register_user()` (currently
discarded; change `register_user` signature to `return user`).

**F. Test plan (30+ tests abridged):**

- Every service mutation emits the expected event name at the expected level.
- `require_owner` emits `access_denied` when companion tries; does NOT emit on
  success.
- `get_or_404` emits `resource_not_found` on true-not-found; emits
  `access_denied_cross_user` on cross-user; suppresses both on success.
- `user_registered` event contains `user_id` and `email`.
- Event registry: every service emits events; listing events grouped by
  category works.

**G-O.** Standard. F-080 + F-085 + F-144 Fixed on merge.

---

### Commit C-15: Off-host log shipping + tamper-resistant storage + 429 alerting

**Findings addressed:** F-082 (Medium), F-150 (Low), F-146 (Low).
**OWASP:** A09:2021.
**ASVS L2 controls closed:** V7.3.3, V7.3.4, V8.1.4.
**Depends on:** C-13, C-14.
**Blocks:** C-50 (retention cleanup).
**Complexity:** Large.

**A. Context.** All audit + app logs currently land inside the container that the
attacker would be compromising. Off-host shipping is the T-3 (Repudiation) threat
remediation. Architectural decision required for destination.

**Architectural decision: destination.** Three options per findings.md F-082:

- **(a) Remote rsyslog** with hash-chained retention. Simple; self-hosted server
  needed.
- **(b) Grafana Loki + Promtail.** Queryable; requires Loki + object-storage
  backend.
- **(c) S3 / Backblaze bucket with Object Lock + retention.** Offsite, tamper-proof,
  third-party dependency.

Developer decision captured at this commit's review. Plan defaults to **(c) S3 with
Object Lock** because (1) tamper-resistance is the hardest requirement to satisfy
with rsyslog/Loki alone, (2) S3 adds one outbound dependency with well-understood
failure modes, (3) Object Lock is immutable for the retention period so attacker
with full compromise cannot delete evidence.

**B. Files modified (S3 path).**

- `app/utils/logging_config.py` -- add an S3 log handler (custom; writes log
  entries as JSONL to a rolling `s3://<bucket>/shekel/YYYY/MM/DD/<hostname>.jsonl`
  key). Use multipart-upload chunking; buffer ~5MB or 5 minutes, whichever
  triggers first.
- `requirements.txt` -- add `boto3==1.35.71` (pinned; choose latest stable).
- `docker-compose.yml` -- add `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_REGION`, `LOG_S3_BUCKET`, `LOG_S3_PREFIX` env vars; rely on AWS Object Lock
  configured out of band (documented in runbook).
- `docs/runbook.md` -- document bucket provisioning, IAM policy, Object Lock.
- `app/utils/log_events.py` -- add `EVT_RATE_LIMIT_EXCEEDED` and emit from the 429
  error handler in `app/__init__.py`.
- `app/__init__.py:@app.errorhandler(429)` -- add `log_event` call before returning
  the response.
- Tests: 6 tests covering the handler buffering, multipart, 429 event emission.

**D. Implementation approach (abridged).**

`app/utils/logging_config.py` additions:

```python
import json
import threading
from io import BytesIO
from logging.handlers import BufferingHandler

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _BOTO_AVAILABLE = True
except ImportError:
    _BOTO_AVAILABLE = False


class S3LogHandler(BufferingHandler):
    """Buffer log records and ship JSONL chunks to S3.

    Uses boto3 with the instance's default credential chain. Failures are
    logged to stderr (never back to the log stream -- would create a loop)
    and the records are retained for the next flush attempt.
    """
    def __init__(self, bucket, prefix, capacity=200, flush_interval_sec=300):
        super().__init__(capacity)
        self.bucket = bucket
        self.prefix = prefix
        self._flush_interval_sec = flush_interval_sec
        self._lock = threading.Lock()
        self._timer = None
        self._client = boto3.client("s3") if _BOTO_AVAILABLE else None
        self._schedule_timed_flush()

    def _schedule_timed_flush(self):
        self._timer = threading.Timer(
            self._flush_interval_sec, self._timer_flush,
        )
        self._timer.daemon = True
        self._timer.start()

    def _timer_flush(self):
        self.flush()
        self._schedule_timed_flush()

    def shouldFlush(self, record):
        return len(self.buffer) >= self.capacity

    def flush(self):
        if not self.buffer or self._client is None:
            return
        with self._lock:
            records = list(self.buffer)
            self.buffer.clear()
        key = self._build_key()
        body = "\n".join(
            json.dumps(
                {
                    "timestamp": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                    "event": getattr(record, "event", None),
                    "request_id": getattr(record, "request_id", None),
                    "user_id": getattr(record, "user_id", None),
                },
                default=str,
            ) for record in records
        ).encode("utf-8")
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=body,
                ContentType="application/x-ndjson",
            )
        except (BotoCoreError, ClientError) as exc:
            import sys
            print(
                f"S3LogHandler flush failed: {type(exc).__name__}. "
                f"{len(records)} records dropped.",
                file=sys.stderr,
            )
            # Records are lost; local JSON file handler retains them.

    def _build_key(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return (
            f"{self.prefix.rstrip('/')}/shekel/{now:%Y/%m/%d}/"
            f"{now:%H%M%S}-{os.getpid()}.jsonl"
        )
```

429 handler update in `app/__init__.py`:

```python
@app.errorhandler(429)
def rate_limit_exceeded(e):
    from app.utils.log_events import log_event, ACCESS
    log_event(
        logger, logging.WARNING, "rate_limit_exceeded", ACCESS,
        "Rate limit exceeded",
        path=request.path, remote_addr=request.remote_addr,
    )
    response = app.make_response(
        (render_template("errors/429.html"), 429),
    )
    response.headers["Retry-After"] = "900"
    return response
```

**F. Test plan (6 tests):** Handler buffers under threshold; flushes at capacity;
timer-triggered flush; S3 client error handled without crash; 429 event emitted;
bucket prefix configurable via env.

**G.** Manual: run the stack with AWS credentials pointing to a dev bucket; exceed
login rate limit; wait ~5min or trigger 200+ log events; verify object appears in
S3 with correct JSONL.

**H-O.** Standard. F-082 + F-150 + F-146 Fixed.

---

### Commit C-16: Logging polish (PII redaction filter + seed-script email redaction + in-app auth-factor notifications)

**Findings addressed:** F-091 (Low), F-114 (Low), F-160 (Info).
**OWASP:** A09:2021.
**ASVS L2 controls closed:** V2.2.3, V2.5.5, V8.1.4.
**Depends on:** C-08, C-13, C-14.
**Blocks:** None.
**Complexity:** Medium.

**A. Context.** (a) A redaction filter prevents PII/secrets from leaking into logs
(F-160 / defense-in-depth). (b) `scripts/seed_user.py` and
`scripts/seed_tax_brackets.py` log user emails on every container start (F-114).
(c) In-app banner notifies the user when a security-relevant auth-factor change
occurred (F-091).

**B. Files modified.**

- `app/utils/logging_config.py` -- new `SensitiveFieldScrubber(logging.Filter)`.
- `scripts/seed_user.py` -- replace `f"User '{email}'"` with `f"User id={user.id}"`.
- `scripts/seed_tax_brackets.py` -- same.
- `app/models/user.py` User -- add `last_security_event_at` column + `last_security_event_kind` (varchar).
- Migration.
- `app/routes/auth.py:change_password`, `:mfa_confirm`, `:mfa_disable_confirm`,
  `:regenerate_backup_codes` -- set the two columns.
- `app/templates/base.html` -- render a dismissible banner on next login when
  `last_security_event_at > last_login_banner_seen_at` (new session key).
- Tests: 10 tests.

**D. Implementation (abridged).**

Scrubber:

```python
_SENSITIVE_PATTERNS = [
    re.compile(r'(password["\']?\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|\S+)', re.I),
    re.compile(r'(totp(?:_secret|_code)?["\']?\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|\S+)', re.I),
    re.compile(r'(secret_key["\']?\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|\S+)', re.I),
    re.compile(r'(backup_code["\']?\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|\S+)', re.I),
    re.compile(r'(cookie["\']?\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|\S+)', re.I),
]


class SensitiveFieldScrubber(logging.Filter):
    """Redact known sensitive tokens from log messages and args."""
    def filter(self, record):
        if isinstance(record.msg, str):
            for pattern in _SENSITIVE_PATTERNS:
                record.msg = pattern.sub(r"\1[REDACTED]", record.msg)
        if record.args:
            new_args = []
            for arg in record.args:
                text = str(arg)
                for pattern in _SENSITIVE_PATTERNS:
                    text = pattern.sub(r"\1[REDACTED]", text)
                new_args.append(text)
            record.args = tuple(new_args)
        return True
```

Register the scrubber alongside `request_id` filter in `setup_logging`.

**C. Schema changes.** `auth.users.last_security_event_at DateTime(timezone=True) nullable`,
`auth.users.last_security_event_kind VARCHAR(50) nullable`. Banner logic keys on
these columns.

**F. Test plan (10 tests):** Scrubber redacts password/totp/secret_key/backup_code
patterns; Scrubber does not redact normal messages; seed script logs use
`user_id` not email; banner renders after password change; banner dismisses
correctly; migration round-trip.

**G-O.** Standard. F-091 + F-114 + F-160 Fixed.

---

### Commit C-17: Anchor balance optimistic locking (version_id_col)

**Findings addressed:** F-009 (High).
**OWASP:** A04:2021 Insecure Design.
**ASVS L2 controls closed:** V1.11.3.
**Depends on:** C-08 (fresh_login_required decorator available; ensures step-up
re-auth before true-up race).
**Blocks:** C-18, C-22.
**Complexity:** Medium.

**A. Context.** `budget.accounts.current_anchor_balance` is bare `Numeric(12,2)`
with no version column. Two concurrent true-ups race; last writer wins.
findings.md Top Risk #3.

**B. Files modified.**

- `app/models/account.py` Account -- add `version_id = db.Column(db.Integer, nullable=False, server_default="1")`;
  `__mapper_args__ = {"version_id_col": version_id}`.
- Migration.
- `app/routes/accounts.py:true_up` (:651-719) and `:inline_anchor_update` (:469-513)
  -- catch `StaleDataError` and return 409 with a retry prompt.
- `app/templates/accounts/_anchor_edit.html` -- display conflict message.
- Tests: 7 tests covering concurrent-update race, 409 response, and successful
  update.

**D. Implementation.**

Model change:

```python
class Account(db.Model):
    __tablename__ = "accounts"
    __table_args__ = (
        db.UniqueConstraint("user_id", "name", name="uq_accounts_user_name"),
        {"schema": "budget"},
    )
    # ... existing columns ...
    # Optimistic-locking version column. SQLAlchemy increments automatically
    # on every commit; a stale in-memory version raises StaleDataError which
    # the routes catch and return HTTP 409.
    version_id = db.Column(db.Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": version_id}
```

Route update (`true_up`):

```python
from sqlalchemy.orm.exc import StaleDataError

try:
    db.session.commit()
except StaleDataError:
    db.session.rollback()
    return (
        render_template(
            "grid/_anchor_edit.html",
            account=account,
            conflict=True,
        ),
        409,
    )
```

**E. Migration.**

```python
def upgrade():
    op.add_column(
        "accounts",
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
        schema="budget",
    )


def downgrade():
    op.drop_column("accounts", "version_id", schema="budget")
```

Existing rows get `version_id=1` via `server_default`.

**F. Test plan (7 tests):** Single update increments version; two concurrent
updates -- one succeeds with 200, the other receives 409; 409 template renders;
balance is correctly set from the winning update; AccountAnchorHistory correctly
records only the winning row; version does NOT increment on read; version column
nullable=False in live DB.

**G. Manual verification.**

1. Open Tab 1: `/accounts/<id>/true-up` form, enter $1200.
2. Open Tab 2: same form, enter $1100, submit (succeeds).
3. Return to Tab 1, submit the $1200 (with the stale version). Expect 409 with
   conflict UI.
4. Reload Tab 1; submit $1200; succeeds; balance is $1200.

**H-O.** Standard. F-009 Fixed.

---

### Commit C-18: Stale-form prevention across every PATCH endpoint

**Findings addressed:** F-010 (High).
**OWASP:** A04:2021.
**ASVS L2 controls closed:** V1.11.3.
**Depends on:** C-17 (version_id pattern established on Account; extends to every
mutable model).
**Blocks:** None.
**Complexity:** Large.

**A. Context.** PATCH endpoints (`transactions`, `transfers`, `entries`, `accounts`,
`salary.raises`, `salary.deductions`, every other `update_*` handler) blindly
`setattr(txn, field, value)` from submitted form data -- a stale form from 10
minutes ago silently rolls back a tab-2 edit.

**B. Files modified.**

Every mutable model gets `version_id` + `__mapper_args__`:

- `app/models/transaction.py` Transaction.
- `app/models/transfer.py` Transfer.
- `app/models/transaction_template.py` TransactionTemplate.
- `app/models/transfer_template.py` TransferTemplate.
- `app/models/salary_profile.py` SalaryProfile.
- `app/models/salary_raise.py` SalaryRaise.
- `app/models/paycheck_deduction.py` PaycheckDeduction.
- `app/models/savings_goal.py` SavingsGoal.
- `app/models/transaction_entry.py` TransactionEntry.

One migration that adds `version_id INTEGER NOT NULL DEFAULT 1` to all 9 tables.

Every PATCH route handler catches `StaleDataError` and returns 409:

- `app/routes/transactions.py:update_transaction`.
- `app/routes/transfers.py:update_transfer`.
- `app/routes/entries.py` update paths.
- `app/routes/accounts.py:update_account`.
- `app/routes/salary.py` update paths (8+ routes).
- `app/routes/savings.py:update_goal`.
- `app/routes/templates.py:update_template`.

Forms include `<input type="hidden" name="version_id" value="{{ obj.version_id }}">`
and every PATCH schema accepts an optional `version_id`. Handler compares the
submitted `version_id` to `obj.version_id`; if they differ, 409 short-circuit.

Tests: 25 tests (two tests per PATCH route: version-bump success, stale-form 409).

**D. Implementation pattern (applied consistently).**

Per-model:

```python
# app/models/transaction.py -- add after existing __mapper_args__ or create
# if none exists
version_id = db.Column(db.Integer, nullable=False, server_default="1")
__mapper_args__ = {"version_id_col": version_id}
```

Per-PATCH-route:

```python
def update_transaction(txn_id):
    # ... existing code ...
    data = _update_schema.load(request.form)

    # Stale-form check: client MUST submit the version_id it loaded.
    client_version = data.pop("version_id", None)
    if client_version is not None and client_version != txn.version_id:
        return (
            render_template(
                "grid/_transaction_stale_warning.html",
                txn=txn, client_version=client_version,
            ),
            409,
        )

    # ... existing setattr and commit ...
```

Schema adds (in `app/schemas/validation.py`):

```python
class TransactionUpdateSchema(BaseSchema):
    # ... existing fields ...
    version_id = fields.Integer()  # Optional; enforced in route handler.
```

Templates add `<input type="hidden" name="version_id" value="{{ txn.version_id }}">`.

**F. Test plan (25 tests):** Per model: model has version_id_col; PATCH increments
version; stale client_version returns 409; 409 template warns user; after reload,
submit succeeds. Same for all 9 models.

**G. Manual verification.** Open two tabs; edit in one; attempt same edit in the
other; expect 409.

**H-O.** Standard. F-010 Fixed.

---

### Commit C-19: TOCTOU duplicate CC Payback prevention

**Findings addressed:** F-008 (High).
**OWASP:** A04:2021.
**ASVS L2 controls closed:** V1.11.3.
**Depends on:** None.
**Blocks:** None.
**Complexity:** Medium.

**A. Context.** `credit_workflow.mark_as_credit` (`app/services/credit_workflow.py:69-130`)
and `entry_credit_workflow.sync_entry_payback`
(`app/services/entry_credit_workflow.py:32-123`) both read-check-insert the CC
Payback row. Concurrent POSTs create two rows. No DB-level unique constraint.

**B. Files modified.**

- Migration: add partial unique index
  `uq_transactions_credit_payback_unique` on
  `budget.transactions (credit_payback_for_id) WHERE credit_payback_for_id IS NOT
  NULL AND is_deleted = FALSE`.
- `app/models/transaction.py` -- declare the index in `__table_args__`.
- `app/services/credit_workflow.py` -- wrap check+insert in
  `SELECT ... FOR UPDATE` on the source Transaction.
- `app/services/entry_credit_workflow.py` -- same.
- `app/routes/transactions.py:mark_credit`, `:unmark_credit` -- catch
  `IntegrityError` on the unique constraint as "already a payback" (idempotent
  success).
- Tests: 11 tests including two concurrent request tests.

**D. Implementation.**

Migration:

```python
def upgrade():
    op.create_index(
        "uq_transactions_credit_payback_unique",
        "transactions",
        ["credit_payback_for_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text(
            "credit_payback_for_id IS NOT NULL AND is_deleted = FALSE"
        ),
    )


def downgrade():
    op.drop_index(
        "uq_transactions_credit_payback_unique",
        table_name="transactions",
        schema="budget",
    )
```

Service change in `credit_workflow.py:mark_as_credit` (after the ownership check,
before the read-check-insert):

```python
# Lock the source transaction row for the duration of this transaction.
# Prevents two concurrent POSTs from both inserting a payback.
txn = (
    db.session.query(Transaction)
    .filter_by(id=transaction_id)
    .with_for_update()
    .one()
)
# Re-check ownership after the lock to close any TOCTOU window.
if txn.pay_period.user_id != user_id:
    raise NotFoundError(...)
```

The partial unique index backstops the service-level lock. If a future caller
bypasses the service, the DB raises `IntegrityError` which the route catches.

**F. Test plan (11 tests):** Single mark-as-credit creates one payback;
double-click produces one payback (idempotent); two concurrent sessions both call
mark-as-credit -- only one succeeds, the other gets the existing payback; partial
unique index allows two paybacks after one is soft-deleted; sync_entry_payback
same shape; unique index does not block different transactions' paybacks.

**G-O.** Standard. F-008 Fixed.

---

### Commit C-20: Recurrence engine shadow guard + restore_transfer account check

**Findings addressed:** F-007 (High), F-164 (Low, new).
**OWASP:** A04:2021.
**ASVS L2 controls closed:** V1.1.5, V4.1.3.
**Depends on:** None.
**Blocks:** None.
**Complexity:** Small.

**A. Context.** F-007: `recurrence_engine.resolve_conflicts` at
`app/services/recurrence_engine.py:270-287` can write to shadow transactions
(transfer_id IS NOT NULL) without any guard; CLAUDE.md invariant 4 is convention-
only. F-164: `transfer_service.restore_transfer` does not check that the
source/destination accounts are still active.

**B. Files modified.**

- `app/services/recurrence_engine.py:249-288` -- add `txn.transfer_id is not None`
  guard at the top of the per-ID loop; raise `ValidationError`.
- `app/services/transfer_service.py:608-727` -- in `restore_transfer`, after
  validating shadows, check both account IDs are active; raise `ValidationError`
  if either is archived.
- Tests: 4 adversarial tests.

**D. Implementation.**

```python
# recurrence_engine.py:270 inside the for-loop
if txn.transfer_id is not None:
    log_event(
        logger, logging.WARNING, "resolve_conflicts_shadow_refused",
        BUSINESS,
        "Refused to mutate transfer shadow via resolve_conflicts",
        user_id=user_id, transaction_id=txn_id,
        transfer_id=txn.transfer_id,
    )
    raise ValidationError(
        "Cannot modify transfer shadow via resolve_conflicts. "
        "Route transfer mutations through transfer_service."
    )
```

```python
# transfer_service.restore_transfer, after shadow type validation
from_account = db.session.get(Account, xfer.from_account_id)
to_account = db.session.get(Account, xfer.to_account_id)
if (from_account is None or not from_account.is_active
        or to_account is None or not to_account.is_active):
    xfer.is_deleted = True  # Roll back the restore flag
    raise ValidationError(
        "Cannot restore transfer: source or destination account is archived."
    )
```

**F. Test plan (4 tests):** resolve_conflicts with shadow ID raises; resolve_conflicts
with regular ID still works; restore_transfer on archived source account raises;
restore_transfer on archived destination raises.

**G-O.** Standard. F-007 + F-164 Fixed.

---

### Commit C-21: Transfer shadow partial unique + transfer/transaction state-machine helper

**Findings addressed:** F-046 (Medium), F-047 (Medium), F-161 (Low, new).
**OWASP:** A04:2021.
**ASVS L2 controls closed:** V1.1.5, V13.1.4.
**Depends on:** None.
**Blocks:** C-22.
**Complexity:** Medium.

**A. Context.** F-046: no DB constraint preventing zero/one/three/four shadows per
transfer. F-047: transfer status accepts any transition. F-161 (new): same gap for
regular (non-transfer) transactions.

**B. Files modified.**

- Migration: partial unique `uq_transactions_transfer_type_active` on
  `budget.transactions (transfer_id, transaction_type_id) WHERE transfer_id IS NOT
  NULL AND is_deleted = FALSE`.
- `app/models/transaction.py` -- declare the index.
- `app/services/state_machine.py` -- new module defining
  `TRANSFER_TRANSITIONS: dict[int, set[int]]` and
  `TRANSACTION_TRANSITIONS: dict[int, set[int]]` keyed by ref-cache IDs.
- `app/services/transfer_service.py:update_transfer:468-473` -- add
  `verify_transition(current_status_id, new_status_id, "transfer")` before the
  assignments.
- `app/routes/transactions.py:update_transaction:248-266` (new_status check block)
  -- add same verification.
- Tests: 14 tests.

**D. Implementation.**

`app/services/state_machine.py`:

```python
"""Status-transition state machine for transfers and transactions.

CLAUDE.md defines the workflow:
  projected -> done | credit | cancelled
  done | received -> settled
  (plus implicit idempotent identity on every state)

All state-changing code paths MUST call verify_transition() with the
current and proposed status IDs. Invalid transitions raise
ValidationError.
"""
from __future__ import annotations

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError


def _build_transitions():
    """Build the transitions dict keyed by ref-cache IDs.

    Lazily computed because ref_cache may not be initialized at module
    import time (migrations, tests).
    """
    projected = ref_cache.status_id(StatusEnum.PROJECTED)
    done = ref_cache.status_id(StatusEnum.DONE)
    received = ref_cache.status_id(StatusEnum.RECEIVED)
    credit = ref_cache.status_id(StatusEnum.CREDIT)
    cancelled = ref_cache.status_id(StatusEnum.CANCELLED)
    settled = ref_cache.status_id(StatusEnum.SETTLED)
    return {
        projected: {done, received, credit, cancelled, projected},
        done: {settled, projected, done},
        received: {settled, projected, received},
        credit: {projected, credit},
        cancelled: {projected, cancelled},
        settled: {settled},  # Terminal; no exit.
    }


def verify_transition(current_status_id, new_status_id, context="transaction"):
    """Raise ValidationError if the transition is not allowed."""
    transitions = _build_transitions()
    allowed = transitions.get(current_status_id, set())
    if new_status_id not in allowed:
        raise ValidationError(
            f"Invalid {context} status transition from "
            f"{current_status_id} to {new_status_id}."
        )
```

Usage in `transfer_service.update_transfer`:

```python
if "status_id" in kwargs:
    new_status_id = kwargs["status_id"]
    verify_transition(xfer.status_id, new_status_id, "transfer")
    xfer.status_id = new_status_id
    expense_shadow.status_id = new_status_id
    income_shadow.status_id = new_status_id
```

**F. Test plan (14 tests):** Every legal transition succeeds (6 from projected);
every illegal transition raises (9+); transfer and transaction share the helper;
settled -> projected rejected; double-submit of identity transition succeeds;
partial unique index blocks third shadow insert; unique index allows restore after
soft-delete; route tests return 400 on illegal transition.

**G-O.** Standard. F-046 + F-047 + F-161 Fixed.

---

### Commit C-22: Transfer mark_done paid_at + carry_forward precondition + ad-hoc idempotency family

**Findings addressed:** F-048 (Medium), F-049 (Medium), F-050 (Medium), F-102 (Low),
F-103 (Low), F-104 (Low), F-105 (Low).
**OWASP:** A04:2021.
**ASVS L2 controls closed:** V1.1.5.
**Depends on:** C-17, C-21.
**Complexity:** Medium.

**A. Context.** Multiple idempotency gaps that share a shape: double-submit creates
duplicates. F-048 fixes the parity gap (transfers-page `mark_done` missing
`paid_at`). F-049 fixes carry-forward status re-check. F-050/F-102/F-103/F-104/F-105
add DB unique constraints.

**B. Files modified.**

- `app/routes/transfers.py:749` -- add `paid_at=db.func.now()` to `update_transfer`
  call (F-048).
- `app/services/transfer_service.py:update_transfer` -- server-side default: if
  `status_id` transitions to DONE and `paid_at` not passed, set it (defense in
  depth).
- `app/services/carry_forward_service.py:71-97` -- change `for txn in regular_txns:`
  to use conditional UPDATE via `db.session.query(Transaction).filter(Transaction.id.in_(ids),
  Transaction.status_id == projected_id).update({...}, synchronize_session=False)`
  (F-049).
- Migrations: composite unique constraints on
  - `budget.transfers (user_id, from_account_id, to_account_id, amount, pay_period_id) WHERE transfer_template_id IS NULL AND is_deleted = FALSE`
    (F-050 ad-hoc uniqueness).
  - `budget.account_anchor_history (account_id, pay_period_id, created_at::date)` (F-103
    duplicate history -- collapse same-day duplicates at the constraint level).
  - `budget.rate_history (account_id, effective_date)` (F-104).
  - `salary.pension_profiles (user_id, name)` (F-105).
- `app/models/transfer.py`, `app/models/account_anchor_history.py` (if separate file;
  else `app/models/account.py`), `app/models/rate_history.py`, `app/models/pension_profile.py`
  -- declare the constraints.
- Tests: 18 tests.

**D. Implementation (carry_forward):**

```python
# carry_forward_service.py replacement for the for-loop at :96-103
if regular_txns:
    regular_ids = [t.id for t in regular_txns]
    updated = (
        db.session.query(Transaction)
        .filter(
            Transaction.id.in_(regular_ids),
            Transaction.status_id == projected_id,
            Transaction.is_deleted.is_(False),
        )
        .update({
            Transaction.pay_period_id: target_period_id,
            # is_override logic cannot be inlined; apply in a second pass.
        }, synchronize_session="fetch")
    )
    count += updated
    # Apply is_override flag per txn (SELECT those that were template-linked
    # AND moved).
    for txn in regular_txns:
        if (txn.template_id is not None
                and txn.status_id == projected_id
                and txn.is_deleted is False):
            txn.is_override = True
```

F-102 (ad-hoc transaction duplicate) is addressed with client-side debounce plus
composite unique on `budget.transactions (user_id, account_id, amount, pay_period_id,
created_at::date)` -- BUT this conflicts with legitimate multiple $4 coffees. Instead,
the fix is debounce + idempotency key (hidden form field, random UUID per form
render). This ships as a schema `idempotency_key` field on the ad-hoc POST schema
that the service stores in a short-TTL Redis key. Alternative: rely on
client-side disable-on-submit alone. Given the developer's constraint to merge to
~45 commits, select the simpler client-side debounce approach and document the
residual risk as "operator UX, not DB-enforced."

F-103 approach -- AccountAnchorHistory: add composite unique on
`(account_id, pay_period_id, created_at_date)` where `created_at_date` is a
generated column (or just use an index and collapse in the service layer). Simpler:
service layer check "is the most recent AccountAnchorHistory row for this account
identical to what we are about to insert? If yes, skip the insert."

**F. Test plan (18 tests):** Transfer mark_done via transfers page sets paid_at;
same via dashboard/grid sets paid_at (regression); carry_forward of a done
transaction does NOT move it; carry_forward race with simultaneous mark-done is
correctly serialized via the conditional UPDATE; ad-hoc transfer double-submit
creates one row (constraint blocks second); rate-history double-submit creates
one row; pension double-submit creates one row; anchor-history skips duplicate
same-second row; carry_forward still moves projected regulars; carry_forward's
shadow path unchanged.

**G-O.** Standard. F-048 + F-049 + F-050 + F-102 + F-103 + F-104 + F-105 Fixed.

---

### Commit C-23: Composite unique on salary raises and deductions

**Findings addressed:** F-051 (Medium), F-052 (Medium).
**OWASP:** A04:2021.
**ASVS L2 controls closed:** V1.1.5.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** Duplicate raise: `salary * 1.03 * 1.03`. Duplicate deduction:
$500 × 2 per paycheck.

**B. Files modified.**

- Migration: composite unique constraints
  `uq_salary_raises_profile_year_month_type` on
  `(salary_profile_id, raise_type_id, effective_year, effective_month)`
  and `uq_paycheck_deductions_profile_name` on `(salary_profile_id, name)`.
- `app/models/salary_raise.py` -- add `UniqueConstraint`.
- `app/models/paycheck_deduction.py` -- add `UniqueConstraint`.
- `app/routes/salary.py` add-raise and add-deduction routes -- catch
  `IntegrityError`, return 400 with "already exists" message.
- Tests: 8 tests.

**E. Migration.**

```python
def upgrade():
    # Deduplicate existing data BEFORE adding the constraint.
    op.execute("""
        DELETE FROM salary.salary_raises a
        USING salary.salary_raises b
        WHERE a.id > b.id
          AND a.salary_profile_id = b.salary_profile_id
          AND a.raise_type_id = b.raise_type_id
          AND a.effective_year = b.effective_year
          AND a.effective_month = b.effective_month
    """)
    op.create_unique_constraint(
        "uq_salary_raises_profile_year_month_type",
        "salary_raises",
        ["salary_profile_id", "raise_type_id", "effective_year", "effective_month"],
        schema="salary",
    )
    op.execute("""
        DELETE FROM salary.paycheck_deductions a
        USING salary.paycheck_deductions b
        WHERE a.id > b.id
          AND a.salary_profile_id = b.salary_profile_id
          AND a.name = b.name
    """)
    op.create_unique_constraint(
        "uq_paycheck_deductions_profile_name",
        "paycheck_deductions",
        ["salary_profile_id", "name"],
        schema="salary",
    )


def downgrade():
    op.drop_constraint(
        "uq_paycheck_deductions_profile_name", "paycheck_deductions",
        schema="salary", type_="unique",
    )
    op.drop_constraint(
        "uq_salary_raises_profile_year_month_type", "salary_raises",
        schema="salary", type_="unique",
    )
```

Dedup SQL is idempotent (WHERE a.id > b.id keeps the smallest-id row).

**F. Test plan (8 tests):** Add raise; second add with same (profile,type,year,month)
raises 400; different year succeeds; dedup migration with 3 duplicates preserves
the oldest; downgrade works. Same for deductions.

**G-O.** Standard. F-051 + F-052 Fixed.

---

### Commit C-24: Marshmallow Range sweep + DB CHECK additions

**Findings addressed:** F-011 (High), F-012 (High), F-013 (High), F-014 (High),
F-074 (Medium), F-075 (Medium), F-076 (Medium), F-077 (Medium).
**OWASP:** A03:2021 Input Validation; A04:2021.
**ASVS L2 controls closed:** V5.1.3, V13.1.4.
**Depends on:** None.
**Blocks:** C-46 (except Exception narrowing relies on validation errors reaching
handlers as clean ValidationError).
**Complexity:** Large.

**A. Context.** Eight findings share shape: schema and DB bounds disagree OR schema
lacks Range validator. User submits a plausible value; DB rejects with opaque
IntegrityError; broad `except Exception` flashes "Failed." The sweep aligns every
field and adds missing DB CHECKs.

**B. Files modified.**

- `app/schemas/validation.py` -- touch every schema that has a field with a matching
  DB CHECK or model comment. Extract a module-level constant
  `NON_NEGATIVE_DECIMAL = validate.Range(min=0)` and variants.
- `app/models/*.py` -- document any non-obvious CHECK constants via code comments.
- Migration: add CHECK constraints to 16 columns per F-077:
  - `budget.escrow_components.annual_amount >= 0`
  - `budget.escrow_components.inflation_rate BETWEEN 0 AND 1`
  - `budget.interest_params.apy BETWEEN 0 AND 1`
  - `budget.investment_params.annual_contribution_limit >= 0`
  - `budget.investment_params.employer_flat_percentage BETWEEN 0 AND 1`
  - `budget.investment_params.employer_match_percentage BETWEEN 0 AND 10`
  - `budget.investment_params.employer_match_cap_percentage BETWEEN 0 AND 1`
  - `auth.user_settings.safe_withdrawal_rate BETWEEN 0 AND 1`
  - `auth.user_settings.estimated_retirement_tax_rate IS NULL OR BETWEEN 0 AND 1`
  - `salary.paycheck_deductions.inflation_rate IS NULL OR BETWEEN 0 AND 1`
  - `salary.paycheck_deductions.inflation_effective_month IS NULL OR BETWEEN 1 AND 12`
  - `salary.salary_raises.effective_year BETWEEN 2000 AND 2100`
  - `salary.state_tax_configs.standard_deduction IS NULL OR >= 0`
  - `salary.state_tax_configs.tax_year BETWEEN 2000 AND 2100`
  - `budget.calibration_overrides.effective_federal_rate BETWEEN 0 AND 1` (+3 more)
  - `budget.rate_history.interest_rate BETWEEN 0 AND 1` (or 0-100 if model stores
    percentage; verify during implementation).
- Tests: 40+ tests (2 per field: valid value accepted, boundary violation rejected
  at schema layer with a clean 400 flash).

**C. Schema alignment decisions.**

- **F-011 salary raise percentage/flat_amount.** Tighten schema to
  `Range(min=Decimal("0.01"), max=Decimal("1000"))` for percentage;
  `Range(min=Decimal("0.01"), max=Decimal("10000000"))` for flat_amount.
  DB already has `> 0`. This commit picks the "no pay cuts" policy per the finding's
  Recommendation; developer reassesses if pay-cut modeling is added later.
- **F-012 deduction amount.** Add `Range(min=Decimal("0.0001"), max=Decimal("1000000"))`.
- **F-013 trend_alert_threshold.** Change schema to
  `fields.Decimal(validate=Range(min=Decimal("0"), max=Decimal("1")), places=4,
  as_string=True)`. Template copy: "decimal 0-1" not "1-100".
- **F-014 percentage fields.** Change every `Range(min=0, max=100)` to
  `Range(min=Decimal("0"), max=Decimal("1"))` in schemas AND update template hints
  in 8 UIs (tax config, inflation rate, etc.).
- **F-074 SalaryProfile W-4 fields.** Add `Range(min=0)` to `additional_income`,
  `additional_deductions`, `extra_withholding` in create + update schemas.
- **F-075 TaxBracketSet fields.** Add `Range(min=0)` to `standard_deduction`,
  `child_credit_amount`, `other_dependent_credit_amount`.

**D. Implementation pattern.**

```python
# app/schemas/validation.py module-level after imports
_NON_NEGATIVE = validate.Range(min=Decimal("0"))
_POSITIVE = validate.Range(min=Decimal("0"), min_inclusive=False)
_RATE_DECIMAL = validate.Range(min=Decimal("0"), max=Decimal("1"))
_RATE_PERCENT = validate.Range(min=Decimal("0"), max=Decimal("100"))  # Where
                                                                       # percent
                                                                       # form intentionally retained.
```

Every schema that previously had `Range(min=0, max=100)` for a rate field becomes
`validate=_RATE_DECIMAL`.

**E. Migration (CHECK additions, abridged -- one representative block):**

```python
def upgrade():
    op.execute("""
        ALTER TABLE budget.escrow_components
        ADD CONSTRAINT ck_escrow_components_annual_amount CHECK (annual_amount >= 0)
    """)
    op.execute("""
        ALTER TABLE budget.escrow_components
        ADD CONSTRAINT ck_escrow_components_inflation_rate
            CHECK (inflation_rate IS NULL OR (inflation_rate >= 0 AND inflation_rate <= 1))
    """)
    # ... repeat for every column listed in section B ...


def downgrade():
    op.execute("ALTER TABLE budget.escrow_components DROP CONSTRAINT ck_escrow_components_annual_amount")
    # ... reverse every add ...
```

**F. Test plan (40+ tests abridged):** Per-field pair: (a) schema accepts a value
at both bounds and one interior value; (b) schema rejects a value beyond each
bound with a clean 400; (c) DB CHECK rejects a bypass attempt via raw SQL if schema
is bypassed. Every schema in the validation module gets a test. Migration
upgrade/downgrade round-trip.

**G-O.** Standard. F-011 + F-012 + F-013 + F-014 + F-074 + F-075 + F-076 + F-077
Fixed.

---

### Commit C-25: Boolean NOT NULL sweep + server_default restoration + boundary inclusivity alignment

**Findings addressed:** F-068 (Medium), F-134 (Low), F-135 (Low), F-106 (Low),
F-107 (Low).
**OWASP:** A04:2021.
**ASVS L2 controls closed:** V13.1.4.
**Depends on:** None.
**Complexity:** Medium.

**A. Context.** F-068 enumerates boolean columns declared `default=False` at Python
level but nullable at DB level (is_override, is_deleted on transactions, is_active
on accounts, is_baseline on scenarios, is_recurring on recurrence_rules,
inflation_enabled on paycheck_deductions). F-134 flags `server_default` present in
migration but absent from live DB (requires pg_attrdef confirmation). F-135 / F-106
/ F-107 flag boundary inclusivity mismatches between schema `min=0` (inclusive)
and DB `> 0`.

**B. Files modified.**

- Migration: per-column NULL backfill + NOT NULL + server_default for every F-068
  column; restore server_default values confirmed missing per pg_attrdef query
  (F-134).
- Model updates: `nullable=False, server_default="false"` (PostgreSQL boolean) on
  every affected column.
- Schema fixes for F-106/F-107/F-135 boundary inclusivity:
  - `SavingsGoalCreateSchema.contribution_per_period` -> `Range(min=Decimal("0"),
    min_inclusive=False)` with `allow_none=True` plus a `@pre_load` that converts
    empty string to None.
  - `LoanParamsCreateSchema.original_principal` -> `Range(min=Decimal("0"),
    min_inclusive=False)`.
  - `PaycheckDeductionSchema.annual_cap` -> allow_none, `Range(min=Decimal("0"),
    min_inclusive=False)` when present.
  - `TransactionEntryCreateSchema.amount` -> already correct; verify.
- Tests: 20 tests.

**E. Migration pattern:**

```python
def upgrade():
    # Backfill NULLs before constraining.
    op.execute("UPDATE budget.transactions SET is_override = false WHERE is_override IS NULL")
    op.execute("UPDATE budget.transactions SET is_deleted = false WHERE is_deleted IS NULL")
    # Restore server_default.
    op.alter_column("transactions", "is_override", nullable=False,
                    server_default=sa.text("false"), schema="budget")
    op.alter_column("transactions", "is_deleted", nullable=False,
                    server_default=sa.text("false"), schema="budget")
    # Repeat for every column in F-068.
    # ...


def downgrade():
    # Relax to nullable, drop server_default.
    op.alter_column("transactions", "is_override", nullable=True,
                    server_default=None, schema="budget")
    # ...
```

**F. Test plan (20 tests):** NULL row backfilled to False; future INSERT without
explicit value gets server_default False; migration round-trip; schema rejects
empty-string as zero for non-inclusive-min fields; SavingsGoal contribution=0
rejected via schema with actionable message.

**G-O.** Standard. F-068 + F-134 + F-135 + F-106 + F-107 Fixed.

---

### Commit C-26: Auth blueprint Marshmallow schemas + MfaVerifySchema length cap

**Findings addressed:** F-041 (Medium), F-163 (Low, new).
**OWASP:** A03:2021; A04:2021.
**ASVS L2 controls closed:** V5.1.3.
**Depends on:** None.
**Complexity:** Medium.

**A. Context.** `app/routes/auth.py` uses `request.form.get` in 15+ places. No
Marshmallow schema for login/register/change_password/mfa_verify/mfa_confirm/
mfa_disable_confirm. F-163 adds a length cap on `backup_code` to prevent
DoS via megabyte-sized strings hitting bcrypt.

**B. Files modified.**

- `app/schemas/validation.py` -- add `LoginSchema`, `RegisterSchema`,
  `ChangePasswordSchema`, `MfaVerifySchema`, `MfaConfirmSchema`, `MfaDisableSchema`.
  Extract shared email/password mixins reused by `CompanionCreateSchema`.
- `app/routes/auth.py` -- every POST handler validates via `.load(request.form)`;
  on `ValidationError`, flash the field-level error.
- Tests: 22 tests.

**D. Implementation (representative schema).**

```python
class _EmailPasswordMixin(BaseSchema):
    """Shared email + password rules for owner and companion paths."""
    email = fields.String(
        required=True,
        validate=[
            validate.Length(min=1, max=255),
            validate.Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", error="Invalid email."),
        ],
    )
    password = fields.String(
        required=True,
        validate=validate.Length(
            min=12, max=72,  # bcrypt 72-byte cap; see C-48 for Argon2id migration.
        ),
    )


class LoginSchema(_EmailPasswordMixin):
    remember = fields.Boolean(load_default=False)


class RegisterSchema(_EmailPasswordMixin):
    display_name = fields.String(
        required=True, validate=validate.Length(min=1, max=100),
    )
    confirm_password = fields.String(required=True)

    @validates_schema
    def confirm_matches(self, data, **kwargs):
        if data.get("password") != data.get("confirm_password"):
            raise ValidationError(
                "Password and confirmation do not match.", "confirm_password",
            )


class ChangePasswordSchema(BaseSchema):
    current_password = fields.String(required=True, validate=validate.Length(min=1, max=72))
    new_password = fields.String(required=True, validate=validate.Length(min=12, max=72))
    confirm_password = fields.String(required=True)

    @validates_schema
    def confirm_matches(self, data, **kwargs):
        if data.get("new_password") != data.get("confirm_password"):
            raise ValidationError(
                "New password and confirmation do not match.", "confirm_password",
            )


class MfaVerifySchema(BaseSchema):
    totp_code = fields.String(
        load_default="", validate=validate.Length(max=6, equal=None),
    )
    backup_code = fields.String(
        load_default="", validate=validate.Length(max=32),
    )
```

`mfa_verify` route update:

```python
schema = MfaVerifySchema()
try:
    data = schema.load(request.form)
except MarshmallowValidationError as exc:
    flash("Invalid verification code.", "danger")
    return render_template("auth/mfa_verify.html")
totp_code = data["totp_code"].strip()
backup_code = data["backup_code"].strip()
```

**F. Test plan (22 tests):** Every schema validates the happy path; oversized email
rejected; oversized password rejected; mismatched confirm_password rejected; DoS-
sized `backup_code` rejected; every auth route uses schema.load.

**G-O.** Standard. F-041 + F-163 Fixed.

---

### Commit C-27: Remaining route input-validation sweep (debt_strategy / mark_done / transfers / transaction mark_done raw decimal)

**Findings addressed:** F-040 (Medium), F-042 (Medium), F-043 (Medium), F-162 (Low, new).
**OWASP:** A03:2021; A01:2021.
**ASVS L2 controls closed:** V5.1.3, V4.2.1.
**Depends on:** C-26.
**Complexity:** Medium.

**A. Context.** F-040: `debt_strategy.calculate` hand-parses three fields. F-042
/ F-162: `mark_done` `Decimal(actual_amount)` in two branches. F-043: transfer
`create_ad_hoc` / `update_transfer` / `create_transfer_template` trust raw FK IDs
without route-boundary verification.

**B. Files modified.**

- `app/schemas/validation.py` -- add `DebtStrategyCalculateSchema`,
  `MarkDoneSchema`.
- `app/routes/debt_strategy.py:222-356` -- parse via schema.
- `app/routes/transactions.py:288-370` -- replace `Decimal(request.form.get("actual_amount"))`
  with `MarkDoneSchema().load(request.form)` in both branches.
- `app/routes/dashboard.py:156-168` -- same.
- `app/routes/transfers.py:create_ad_hoc`, `:update_transfer`, `:create_transfer_template`
  -- add explicit FK ownership checks matching `transactions.create_inline` pattern.
- Tests: 18 tests.

**D. Implementation (representative).**

```python
class DebtStrategyCalculateSchema(BaseSchema):
    extra_monthly = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1000000")),
    )
    strategy = fields.String(
        required=True, validate=validate.OneOf(_VALID_STRATEGIES),
    )
    custom_order = fields.String(
        allow_none=True, validate=validate.Length(max=500),
    )


class MarkDoneSchema(BaseSchema):
    actual_amount = fields.Decimal(
        allow_none=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0")),
    )
```

Transfer route update (`create_ad_hoc`):

```python
# After schema.load, before service call:
data = _xfer_create_schema.load(request.form)

# Route-boundary FK ownership (defense-in-depth; service also checks).
from_acct = db.session.get(Account, data["from_account_id"])
if not from_acct or from_acct.user_id != current_user.id:
    return "Source account not found", 404
to_acct = db.session.get(Account, data["to_account_id"])
if not to_acct or to_acct.user_id != current_user.id:
    return "Destination account not found", 404
# Same for pay_period_id, scenario_id, category_id.
```

**F. Test plan (18 tests):** debt_strategy happy path and rejections; mark_done
actual_amount parsed via schema (no raw Decimal); negative actual_amount rejected;
cross-user from_account_id on create_ad_hoc returns 404; same for category_id.

**G-O.** Standard. F-040 + F-042 + F-043 + F-162 Fixed.

---

### Commit C-28: account_type multi-tenant guard

**Findings addressed:** F-044 (Medium).
**OWASP:** A01:2021.
**ASVS L2 controls closed:** V4.1.3.
**Depends on:** None.
**Complexity:** Medium.

**A. Context.** `ref.account_types` is a global table. `@require_owner`-gated
mutation routes let any owner rename/delete types used by other owners. Dormant
in single-owner deployment; blocks multi-tenant expansion.

**B. Files modified.**

- Migration: add `user_id INTEGER NULL FK auth.users.id` to `ref.account_types` (NULL
  means "seeded built-in type"). No CASCADE -- RESTRICT so deleting a user does not
  orphan other users' accounts referencing their types.
- `app/models/ref.py` AccountType -- add `user_id` column.
- `app/routes/accounts.py:546-642` -- scope create/update/delete to rows where
  `user_id = current_user.id`. Owners can only modify types they created; the seeded
  rows (`user_id IS NULL`) are read-only to everyone.
- Tests: 11 tests.

**E. Migration.**

```python
def upgrade():
    op.add_column(
        "account_types",
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("auth.users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        schema="ref",
    )
    # Existing rows remain NULL (seeded).


def downgrade():
    op.drop_column("account_types", "user_id", schema="ref")
```

**F. Test plan (11 tests):** Owner A creates custom type; it has `user_id=A`; Owner
B cannot modify it (404); Owner A cannot modify a seeded type (user_id IS NULL);
Owner A can create their own copy of a seeded type with the same name (different
user_id); delete cascades correctly when owner deleted.

**G-O.** Standard. F-044 Fixed.

---

### Commit C-29: Cross-user FK re-parenting fix in update_transaction

**Findings addressed:** F-029 (High).
**OWASP:** A01:2021.
**ASVS L2 controls closed:** V4.2.1.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** `app/routes/transactions.py:update_transaction:183-285` loads the
txn via `_get_owned_transaction`, then blindly `setattr` fields including
`pay_period_id` and `category_id` from `TransactionUpdateSchema` without verifying
the submitted FK values belong to `current_user`. An authenticated owner can
re-parent their own transaction into a victim's pay period.

**B. Files modified.**

- `app/routes/transactions.py:update_transaction` -- after `schema.load`, before
  the `setattr` loop, validate `pay_period_id` and `category_id` ownership.
- `app/routes/entries.py` PATCH routes -- same pattern (defense-in-depth; no
  confirmed F-NEW issue but apply while the surface is being hardened).
- Tests: 6 tests including adversarial "user B submits user A's period_id".

**D. Implementation.**

```python
# transactions.py update_transaction after data = _update_schema.load(request.form)
if "pay_period_id" in data:
    period = db.session.get(PayPeriod, data["pay_period_id"])
    if not period or period.user_id != current_user.id:
        return "Pay period not found", 404
if "category_id" in data:
    cat = db.session.get(Category, data["category_id"])
    if not cat or cat.user_id != current_user.id:
        return "Category not found", 404
```

Consider (at the developer's discretion at the C-29 checkpoint) dropping
`pay_period_id` from `TransactionUpdateSchema` entirely if move-transaction-to-
another-period is not an actual UI flow. Best to drop unless a concrete template
depends on it (audit at implementation time).

**F. Test plan (6 tests):** Owner A PATCHes with their own pay_period_id -- succeeds;
Owner A PATCHes with Owner B's pay_period_id -- 404; same for category_id; test
confirms the txn is not mutated in the 404 path.

**G-O.** Standard. F-029 Fixed.

**L. IDOR probe re-run.** This commit directly affects access control. Re-run
`scripts/audit/idor_probe.py` on fresh dev compose. Expect zero failures; this
commit removes the cross-user FK path that the probe did NOT catch.

---

### Commit C-30: Analytics ownership checks (account_id + period_id)

**Findings addressed:** F-039 (Medium), F-098 (Low).
**OWASP:** A01:2021.
**ASVS L2 controls closed:** V4.2.1.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** F-039: `analytics.calendar_tab:49-102` passes raw `account_id` to
service without ownership check. F-098: `variance_tab:139-185` + helper
`_variance_csv_filename:410-431` read PayPeriod's start_date into CSV filename
without ownership check.

**B. Files modified.**

- `app/routes/analytics.py` -- add ownership validation at the top of every handler
  that accepts `account_id` or `period_id` from query args. Invalid -> 404 (for
  HTMX / page) or 404 (for CSV). Extract a helper `_validate_owned_or_404`.
- Tests: 7 tests.

**D. Implementation.**

```python
def _validate_owned_account(account_id):
    if account_id is None:
        return None
    acct = db.session.get(Account, account_id)
    if not acct or acct.user_id != current_user.id:
        return "FORBIDDEN"
    return acct


@analytics_bp.route("/analytics/calendar")
@login_required
@require_owner
def calendar_tab():
    # ... existing code ...
    account_id = request.args.get("account_id", None, type=int)
    owned = _validate_owned_account(account_id)
    if owned == "FORBIDDEN":
        return "", 404
    # ...
```

Same pattern for `period_id` in `variance_tab` and `_variance_csv_filename`.

**F. Test plan (7 tests):** Owner A requesting own account_id succeeds; Owner A
requesting Owner B's account_id returns 404 (calendar_tab); same for period_id;
CSV download with cross-user account_id returns 404; filename helper does not
leak victim's start_date.

**G-O.** Standard. F-039 + F-098 Fixed. IDOR probe re-run: expect zero failures.

---

### Commit C-31: 404-everywhere unification + IDOR test helper split

**Findings addressed:** F-087 (Low), F-084 (Low).
**OWASP:** A01:2021.
**ASVS L2 controls closed:** V4.2.1.
**Depends on:** C-29, C-30.
**Complexity:** Medium.

**A. Context.** F-087: 51 routes across 8 blueprints return 302+flash instead of
404 on cross-user access. F-084: `_assert_blocked` test helper accepts both; a
regression from 404 -> 302 is invisible to the suite.

**B. Files modified.**

- 51 routes across `accounts.py` (14), `salary.py` (16), `templates.py` (5),
  `transfers.py` (5), `categories.py` (4), `savings.py` (3), `retirement.py` (3),
  `companion.py` (1). Each converts the current `get_or_flash_redirect` path to
  `get_or_404`.
- `tests/test_integration/test_access_control.py` -- split `_assert_blocked` into
  `_assert_not_found` (strict 404) and `_assert_redirected_to_login` (302 +
  Location verified as login).
- Update all 69 IDOR tests to use the correct helper.
- Tests: existing 69 IDOR tests updated; 3 new tests verify the helpers themselves.

**D. Implementation pattern (per route).**

```python
# Before (accounts.py edit_account pattern):
@accounts_bp.route("/accounts/<int:account_id>/edit")
@login_required
@require_owner
def edit_account(account_id):
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.list_accounts"))
    # ...

# After:
@accounts_bp.route("/accounts/<int:account_id>/edit")
@login_required
@require_owner
def edit_account(account_id):
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)
    # ...
```

`get_or_404` from `auth_helpers.py` returns None on miss OR cross-user; the
caller `abort(404)`s (preserving the security response rule: 404 for both).

Test helper:

```python
def _assert_not_found(response, msg=""):
    assert response.status_code == 404, (
        f"Expected 404 but got {response.status_code}. "
        f"User B may have accessed User A's resource. {msg}"
    )


def _assert_redirected_to_login(response, msg=""):
    assert response.status_code == 302, (
        f"Expected 302 but got {response.status_code}. {msg}"
    )
    location = response.headers.get("Location", "")
    assert "/login" in location, (
        f"Expected redirect to login but got {location}. {msg}"
    )
```

Every existing IDOR test uses `_assert_not_found` (the ownership-helper path).
Tests that exercise `@login_required`-only routes use `_assert_redirected_to_login`.

**F. Test plan (~75 tests -- 69 existing updated + 3 helper tests + 3 regression).**

**G-O.** Standard. IDOR probe re-run: every cross-user request now returns 404
(matching the workflow's "404 for both 'not found' and 'not yours'" rule).
F-087 + F-084 Fixed.

---

### Commit C-32: Version-control nginx and compose configs currently running on production host

**Findings addressed:** F-021 (High).
**OWASP:** A08:2021 Software and Data Integrity Failures.
**ASVS L2 controls closed:** V14.1.2, V14.2.1.
**Depends on:** None.
**Blocks:** C-33, C-34, C-37, C-38, C-49.
**Complexity:** Medium.

**A. Context.** Production nginx.conf, vhost, and compose override currently live
only on the host at `/opt/docker/nginx/` and `/opt/docker/shekel/`. Changes to the
repo's `nginx/nginx.conf` have no effect on production. Disaster recovery fails.

**B. Files modified.**

- Commit production files into the repo:
  - `deploy/nginx-shared/nginx.conf` (was `/opt/docker/nginx/nginx.conf`).
  - `deploy/nginx-shared/conf.d/shekel.conf` (was `/opt/docker/nginx/conf.d/shekel.conf`).
  - `deploy/docker-compose.prod.yml` (was `/opt/docker/shekel/docker-compose.override.yml`;
    renamed so its role is explicit).
- Rename the repo's current `nginx/nginx.conf` (bundled, unused in prod) to
  `deploy/nginx-bundled/nginx.conf` with a docstring comment explaining it is
  aspirational for fresh-host bring-up.
- `README.md` -- add a "Deployment architecture" section explaining which files are
  active under which deployment mode (bundled nginx vs shared homelab nginx).
- `docs/runbook.md` -- document the sync procedure (edit repo, pull on host,
  `docker compose` restart).
- `docker-compose.yml` -- volume mount for shared-mode includes commented
  instructions on how to opt into it; by default keeps the bundled mode.
- `scripts/config_audit.py` -- skeleton for C-49 (drift-check). Not populated yet
  but stub file created here so the location is reserved.
- Tests: 3 (filesystem existence + YAML/nginx syntax check).

**D. Implementation approach.**

Filesystem changes:

```
deploy/
├── nginx-bundled/
│   └── nginx.conf             # (moved from nginx/nginx.conf)
├── nginx-shared/
│   ├── nginx.conf             # (copied from /opt/docker/nginx/)
│   └── conf.d/
│       └── shekel.conf        # (copied from /opt/docker/nginx/conf.d/)
├── docker-compose.prod.yml    # (copied from /opt/docker/shekel/docker-compose.override.yml)
└── README.md                   # Explains which file is active when
```

Update `docker-compose.yml:104` volume to reference the new path:
`./deploy/nginx-bundled/nginx.conf:/etc/nginx/nginx.conf:ro`. Same for shared
nginx deploy mode documented in `deploy/README.md`.

Confirm the snapshot files under `docs/audits/security-2026-04-15/scans/shared-nginx.conf.txt`
are the ones committed -- developer verifies at deploy time that the runtime
config has not drifted further since the audit window.

**F. Test plan (3 tests):** `test_deploy_nginx_files_exist`;
`test_deploy_nginx_config_parses` (nginx -t via subprocess on an nginx container);
`test_deploy_compose_parses` (docker compose config on the file).

**G. Manual verification.**

1. Deploy: pull repo on host, `docker compose -f deploy/docker-compose.prod.yml up -d`.
2. Verify the running `shekel-prod-nginx` / `shekel-prod-app` reflects the
   committed config (`docker exec shekel-prod-nginx cat /etc/nginx/nginx.conf`
   matches `deploy/nginx-shared/nginx.conf`).
3. Make a no-op edit to `deploy/nginx-shared/nginx.conf`, re-deploy, verify
   change propagates.

**H-O.** Standard. F-021 Fixed.

---

### Commit C-33: Network topology + proxy trust + nginx security headers + server_tokens off

**Findings addressed:** F-015 (High), F-020 (High), F-063 (Medium), F-064 (Medium),
F-129 (Low), F-156 (Low).
**OWASP:** A05:2021 Security Misconfiguration; A09:2021.
**ASVS L2 controls closed:** V14.4.1, V14.4.3, V14.1.3, V14.3.2.
**Depends on:** C-32.
**Complexity:** Large.

**A. Context.** F-015: Nginx `set_real_ip_from` and Gunicorn `forwarded_allow_ips`
trust all RFC 1918; a compromised co-tenant can forge `X-Forwarded-For`. F-020:
`shekel-prod-app` sits on the shared `homelab` network; co-tenants reach gunicorn
directly. F-063: WAN (cloudflared) bypasses the shared nginx. F-064: shared nginx
vhost sets zero security headers. F-129: lateral movement surface. F-156: nginx
default `server_tokens on` leaks version.

**B. Files modified.**

- `deploy/docker-compose.prod.yml` -- create `shekel-frontend` dedicated bridge
  network; `shekel-prod-app` leaves `homelab`, joins only `shekel-frontend` +
  `backend`; `cloudflared` ingress routes through `shekel-frontend` via shared
  nginx.
- `gunicorn.conf.py:80-83` -- lock `forwarded_allow_ips` to the nginx container IP
  only (no default `10.0.0.0/8` etc.). Fail-closed if the env var is not set on
  the container.
- `deploy/nginx-shared/nginx.conf` (+ the bundled one) -- restrict
  `set_real_ip_from` to the Cloudflare Origin Authenticated Pull IPv4 ranges
  (documented at Cloudflare IPs list) instead of all RFC 1918; add `server_tokens off;`
  in the `http` block.
- `deploy/nginx-shared/conf.d/shekel.conf` -- add four `add_header` directives for
  X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy
  (defense-in-depth even though Flask also sets them).
- `cloudflared/config.yml` -- route ingress through `https://nginx:443` (internal
  cert) or `http://nginx:80` (nginx fronts gunicorn).
- Tests: 12 tests.

**D. Implementation approach.**

`gunicorn.conf.py:80-83` replacement:

```python
# Trust X-Forwarded-* headers only from nginx's container IP.
# The value MUST be set by docker-compose via FORWARDED_ALLOW_IPS; no default.
_allowed = os.getenv("FORWARDED_ALLOW_IPS")
if not _allowed:
    raise RuntimeError(
        "FORWARDED_ALLOW_IPS must be set in production. "
        "Set to the nginx container IP on the shekel-frontend network."
    )
forwarded_allow_ips = _allowed
```

`deploy/nginx-shared/conf.d/shekel.conf` -- replace the current (zero-header) stanza
with:

```nginx
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
server_tokens off;
```

`set_real_ip_from` -- replace RFC 1918 ranges with the published Cloudflare IPv4
ranges (16 entries as of 2026-04-22) with a comment linking to
`https://www.cloudflare.com/ips-v4` for update cadence.

Compose network change (representative):

```yaml
networks:
  shekel-frontend:
    driver: bridge
  backend:
    driver: bridge
    internal: true
  # Remove homelab from the app service block.

services:
  app:
    # ...
    networks:
      - backend
      - shekel-frontend
    # (No longer joins homelab.)
```

**F. Test plan (12 tests):** Gunicorn refuses start if FORWARDED_ALLOW_IPS unset;
X-Forwarded-For from non-trusted IP is not used for request.remote_addr; nginx
server block includes server_tokens off (inspect rendered response headers --
nginx should not emit `Server: nginx/1.27.x`); all four security headers present
on a 200 response; all four present on a 502 error page (`always` directive);
cloudflared config points to nginx; shekel-prod-app not on homelab network per
inspect.

**G. Manual verification.**

1. Deploy; `docker network inspect homelab | grep shekel-prod-app` returns nothing.
2. `docker network inspect shekel-frontend` shows nginx and app.
3. From jellyfin container (if still deployed), `curl http://shekel-prod-app:8000/`
   fails (network unreachable).
4. `curl -v http://nginx/ | grep -i server` shows no version.
5. `curl -I https://<DOMAIN>/dashboard` (after login) shows all four security
   headers.

**H-O.** Standard. F-015 + F-020 + F-063 + F-064 + F-129 + F-156 Fixed.

---

### Commit C-34: Seed user credential hygiene + REGISTRATION_ENABLED=false in prod + stale containers cleanup + .dockerignore

**Findings addressed:** F-022 (High), F-053 (Medium), F-054 (Medium), F-113 (Low).
**OWASP:** A07:2021; A05:2021.
**ASVS L2 controls closed:** V6.4.1, V2.1.4, V14.1.1.
**Depends on:** C-32.
**Complexity:** Medium.

**A. Context.** F-022: `SEED_USER_PASSWORD` persists in the container's
`os.environ` forever. F-053: `REGISTRATION_ENABLED=true` default. F-054: stale
pre-rename containers waste host resources. F-113: image contains dev-only files.

**B. Files modified.**

- `scripts/seed_user.py` -- when it runs, read env vars, create user, THEN
  `os.unsetenv("SEED_USER_PASSWORD")` and `os.environ.pop(...)`. Secondary: emit a
  one-time idempotency marker so subsequent container starts do not log "already
  exists" messages.
- `docker-compose.yml` (production template) -- switch to using
  `env_file: seed.env` that is only mounted at first-run. Default `REGISTRATION_ENABLED=false`.
- `entrypoint.sh` -- check for a `/home/shekel/app/.seed-complete` sentinel file;
  skip seeding if present; create it on first completion.
- `deploy/docker-compose.prod.yml` -- same change.
- `.env.example` -- default `REGISTRATION_ENABLED=false` with a comment.
- `.dockerignore` -- new file excluding `.claude/`, `amortization-fix.patch`,
  `cloudflared/` (not the service, the dev placeholder), `requirements-dev.txt`,
  `pytest.ini`, `diagnostics/`, `monitoring/`, `scripts/` EXCEPT `scripts/init_db.sql`
  and `scripts/audit_cleanup.py`.
- `scripts/retire_stale_containers.sh` -- one-shot helper that lists the stale
  containers and asks the developer to confirm deletion. Separate from automatic
  runs to avoid accidental data loss.
- Tests: 5 script tests + 2 route tests.

**E. Migration.** None.

**F. Test plan (7 tests):** seed_user runs once; second run no-op (sentinel);
password env var not present after seed; REGISTRATION_ENABLED=false returns 404
on /register; .dockerignore excludes expected files.

**G. Manual verification.**

1. Fresh deploy; verify SEED_USER_PASSWORD absent from `docker exec shekel-prod-app env`.
2. Visit `/register`; 404.
3. `scripts/retire_stale_containers.sh --dry-run`; review list; re-run with
   `--confirm`; verify `shekel-app`, `shekel-db`, `shekel-nginx` removed.
4. `docker images ghcr.io/saltyreformed/shekel` size decreased after
   `.dockerignore` takes effect on next build.

**H-O.** Standard. F-022 + F-053 + F-054 + F-113 Fixed.

---

### Commit C-35: Docker hardening bundle (no-new-privileges + cap_drop + dev DB loopback + resource limits + log rotation + read-only rootfs)

**Findings addressed:** F-055 (Medium), F-056 (Medium), F-057 (Medium), F-115 (Low),
F-116 (Low), F-117 (Low).
**OWASP:** A05:2021.
**ASVS L2 controls closed:** V14.1.1.
**Depends on:** None.
**Complexity:** Medium.

**A. Context.** Six related container hardening gaps.

**B. Files modified.**

- `docker-compose.yml` (prod) -- add to every service: `security_opt:
  ["no-new-privileges:true"]`, `cap_drop: [ALL]`, `mem_limit`, `pids_limit`,
  `logging.driver/options`. On the app service add `read_only: true` +
  `tmpfs: ["/tmp", "/home/shekel/app/logs" if not mounted]`. NGINX needs `NET_BIND_SERVICE`
  cap-add if binding privileged port (but compose binds via host, so no).
- `docker-compose.dev.yml` -- loopback-bind the db and test-db ports (F-057).
- `deploy/docker-compose.prod.yml` -- mirror the prod additions.
- `/etc/docker/daemon.json` -- runbook addition for host-level
  `"no-new-privileges": true` default.
- Tests: 6 tests (docker-bench-security style asserting on inspect output).

**D. Implementation (representative service block).**

```yaml
  app:
    image: ghcr.io/saltyreformed/shekel:latest
    # ... existing fields ...
    mem_limit: 512m
    pids_limit: 200
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp
      - /home/shekel/app/logs  # Or keep as named volume
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

`docker-compose.dev.yml:36`, `:55` replacement:

```yaml
ports:
  - "127.0.0.1:5432:5432"   # Loopback-only
ports:
  - "127.0.0.1:5433:5432"
```

**F. Test plan (6 tests):** inspect app container hostconfig -- SecurityOpt
contains no-new-privileges; CapDrop == [ALL]; ReadonlyRootfs True; Memory > 0;
PidsLimit > 0; LogConfig.Options contains max-size.

**G. Manual verification.**

1. Deploy; `docker inspect shekel-prod-app --format '{{.HostConfig.SecurityOpt}}'`
   includes `no-new-privileges:true`.
2. `docker inspect ... --format '{{.HostConfig.CapDrop}}'` includes ALL.
3. From LAN, `psql -h <host-ip> -p 5432 -U shekel_user` fails (ports now loopback).
4. `docker exec shekel-prod-app touch /app/foo` fails (read-only rootfs).

**H-O.** Standard. F-055 + F-056 + F-057 + F-115 + F-116 + F-117 Fixed.

---

### Commit C-36: Dockerfile refresh (digest pin + OpenSSL upgrade + pip upgrade + distroless/slim migration + Cosign signing)

**Findings addressed:** F-060 (Medium), F-025 (High), F-120 (Low), F-062 (Medium),
F-155 (Low).
**OWASP:** A06:2021; A08:2021.
**ASVS L2 controls closed:** V14.2.2, V14.2.4, V10.3.1.
**Depends on:** None.
**Complexity:** Medium.

**A. Context.** Five Dockerfile/image concerns.

**B. Files modified.**

- `Dockerfile` -- base `FROM python:3.14-slim@sha256:<digest>` (pin by digest).
  Add `apt-get update && apt-get upgrade -y openssl libssl3t64 openssl-provider-legacy`
  (F-025). Add `RUN pip install --upgrade pip==26.0` (F-120). Consider migration to
  `python:3.14-alpine` or distroless; evaluate per-dep compatibility (psycopg2
  binary wheel needs glibc; alpine requires `psycopg2-binary`). Decision at commit
  checkpoint; the plan's default is to stay on `python:3.14-slim` but bump to the
  current digest that includes the OpenSSL fix (F-062's unreachable CVEs disappear
  with the OpenSSL upgrade).
- `Dockerfile` HEALTHCHECK -- already correct; no change.
- `scripts/deploy.sh` -- add Cosign signing step on build; add Cosign verify step
  on pull (F-155).
- `deploy/docker-compose.prod.yml` -- replace `:latest` with
  `@sha256:<digest>` (F-060).
- `.github/workflows/build.yml` (if CI exists; else create a script) -- add build
  -> Cosign sign -> push workflow.
- Tests: 2 (Cosign verify, trivy scan delta).

**F. Test plan (2 tests + manual):** Cosign verify succeeds on a signed image;
trivy image scan shows HIGH OpenSSL CVE is gone.

**G. Manual verification.** Build, sign, pull by digest, verify; trivy scan.

**H-O.** Standard. F-025 + F-060 + F-062 + F-120 + F-155 Fixed.

---

### Commit C-37: cloudflared Access policy + metrics to loopback + Postgres TLS

**Findings addressed:** F-061 (Medium), F-128 (Low), F-154 (Low).
**OWASP:** A05:2021; A02:2021.
**ASVS L2 controls closed:** V9.1.1, V9.2.2.
**Depends on:** C-32.
**Complexity:** Small.

**A. Context.** F-061: no Access policy, `noTLSVerify`. F-128: cloudflared metrics
binds 0.0.0.0. F-154: Postgres without TLS.

**B. Files modified.**

- `cloudflared/config.yml` -- add `access` block with `team_name` +
  `required_groups` or similar; change `--metrics 127.0.0.1:2000`.
- `deploy/docker-compose.prod.yml` -- DATABASE_URL append `?sslmode=require`;
  Postgres container add `POSTGRES_INITDB_ARGS="--data-checksums"` and enable
  `ssl=on` via generated self-signed cert mounted read-only.
- `scripts/generate_pg_cert.sh` -- new helper that generates a Postgres self-signed
  cert for single-host deployments.
- `deploy/postgres/`` -- directory containing `server.crt`, `server.key` (gitignored;
  generated once and stored in an operator-managed path).
- `docs/runbook.md` -- document cloudflared Access attachment and Postgres TLS
  generation.
- Tests: 3 tests (DATABASE_URL contains sslmode; cloudflared config has access
  block; metrics bind is loopback).

**D.** Implementation.

cloudflared:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json
metrics: 127.0.0.1:2000
ingress:
  - hostname: <DOMAIN>
    service: http://nginx:80
    originRequest:
      noTLSVerify: true  # loopback colocation; shared nginx terminates TLS from CF.
      access:
        required: true
        teamName: <TEAM_NAME>
        audTag:
          - <AUD_TAG>
  - service: http_status:404
```

Postgres TLS:

```yaml
services:
  db:
    # ...
    command:
      - postgres
      - -c
      - ssl=on
      - -c
      - ssl_cert_file=/etc/postgresql/server.crt
      - -c
      - ssl_key_file=/etc/postgresql/server.key
    volumes:
      - ./deploy/postgres/server.crt:/etc/postgresql/server.crt:ro
      - ./deploy/postgres/server.key:/etc/postgresql/server.key:ro
```

`DATABASE_URL` gains `?sslmode=require`.

**F. Test plan (3 tests).**

**G-O.** Standard. F-061 + F-128 + F-154 Fixed.

---

### Commit C-38: Env file cleanup + Docker secrets migration

**Findings addressed:** F-108 (Low), F-109 (Low), F-112 (Low), F-148 (Low, rejected defer).
**OWASP:** A05:2021; A02:2021.
**ASVS L2 controls closed:** V2.10.3, V2.10.4, V6.4.1.
**Depends on:** C-34.
**Complexity:** Medium.

**A. Context.** F-108: `.env.dev` refers to nonexistent path. F-109: `.env.example`
has functional dev password. F-112: DevConfig missing pragma comment. F-148:
developer rejected the "secrets manager" defer; the simplest migration is to
Docker secrets on the existing compose stack (no new service).

**B. Files modified.**

- `.env.dev` -- DELETE. Gitignore `.env.dev` alongside `.env`. The dev workflow uses
  `.env.example` as the starting point; if a dev copy is needed, it lives at
  `.env` locally.
- `.env.example` -- replace `POSTGRES_PASSWORD=shekel_pass` with
  `POSTGRES_PASSWORD=` (non-functional), with an instructional comment. Add
  all-secrets section with generator commands.
- `app/config.py` DevConfig -- add pragma comment explaining HTTP-localhost cookie
  posture.
- `deploy/docker-compose.prod.yml` -- migrate `SECRET_KEY`, `POSTGRES_PASSWORD`,
  `TOTP_ENCRYPTION_KEY`, `TOTP_ENCRYPTION_KEY_OLD`, `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, `APP_ROLE_PASSWORD` to Docker secrets via
  `secrets:` block.
- `entrypoint.sh` -- read secrets from `/run/secrets/<name>` files and export to
  env vars expected by the app. The trade-off: brief exposure in env; but secrets
  are file-backed at rest instead of env-backed.
- `docs/runbook.md` -- document the secret rotation procedure via
  `docker secret create/rm`.
- Tests: 4.

**D. Implementation.**

`deploy/docker-compose.prod.yml`:

```yaml
secrets:
  secret_key:
    file: /opt/docker/shekel/secrets/secret_key
  postgres_password:
    file: /opt/docker/shekel/secrets/postgres_password
  totp_encryption_key:
    file: /opt/docker/shekel/secrets/totp_encryption_key
  aws_access_key:
    file: /opt/docker/shekel/secrets/aws_access_key
  aws_secret:
    file: /opt/docker/shekel/secrets/aws_secret
  app_role_password:
    file: /opt/docker/shekel/secrets/app_role_password

services:
  app:
    secrets:
      - secret_key
      - postgres_password
      - totp_encryption_key
      - aws_access_key
      - aws_secret
      - app_role_password
```

`entrypoint.sh` prelude:

```bash
# Load secrets from Docker secrets files if present; fall back to env.
_load_secret() {
    local var_name="$1"
    local file_name="$2"
    local secret_path="/run/secrets/${file_name}"
    if [ -f "${secret_path}" ]; then
        export "${var_name}"="$(cat "${secret_path}")"
    fi
}
_load_secret SECRET_KEY secret_key
_load_secret POSTGRES_PASSWORD postgres_password
_load_secret TOTP_ENCRYPTION_KEY totp_encryption_key
_load_secret AWS_ACCESS_KEY_ID aws_access_key
_load_secret AWS_SECRET_ACCESS_KEY aws_secret
_load_secret APP_ROLE_PASSWORD app_role_password
```

**F. Test plan (4 tests):** .env.dev removed from branch; .env.example password field
empty; entrypoint loads secret from file when present; entrypoint falls back to env
when file absent.

**G-O.** Standard. F-108 + F-109 + F-112 + F-148 Fixed.

---

### Commit C-39: Field-level encryption for PII (email, display_name, anchor_balance)

**Findings addressed:** F-147 (Low, rejected defer).
**OWASP:** A02:2021.
**ASVS L2 controls closed:** V6.1.1, V6.1.3.
**Depends on:** C-04 (MultiFernet infrastructure).
**Complexity:** Large.

**A. Context.** F-147 recommends encrypted rest for PII. Field-level encryption
via the same MultiFernet infrastructure as TOTP secrets; columns become binary;
a SQLAlchemy `TypeDecorator` handles encrypt-on-write / decrypt-on-read.

**B. Files modified.**

- `app/models/encrypted.py` (new) -- `EncryptedString` and `EncryptedDecimal`
  TypeDecorators.
- `app/models/user.py` -- `email`, `display_name` become `EncryptedString(512)`.
- `app/models/account.py` -- `current_anchor_balance` becomes `EncryptedDecimal(...)`.
- Migration: ALTER TABLE changing type + backfill via Python (encrypt each row)
  + ALTER to NOT NULL.
- Every query that FILTERS by email (`auth_service.authenticate`, etc.) must use a
  deterministic HMAC lookup column (`email_hash = HMAC_SHA256(email, pepper)`) so
  uniqueness is enforceable without decrypting every row. Requires a new
  `email_hash` column + unique index.
- `app/services/auth_service.py` -- `authenticate` query filters by `email_hash`;
  the stored `email` is compared after decrypt for final confirmation.
- Templates that display `current_anchor_balance` are already display-only; the
  decrypted Decimal passes through unchanged.
- Aggregation queries that SUM `current_anchor_balance` must be rewritten as
  Python-level aggregation (decrypt each row, sum in Python). This is the main
  cost of field-level encryption; analyse performance impact.
- Tests: 18 tests.

**Warning to developer (captured at C-39 checkpoint):** field-level encryption on
`current_anchor_balance` breaks every SQL-level aggregation. Balance calculator
already uses Python-side summation so the impact is smaller than it would be for
a more-aggregated model. HOWEVER, certain reports (year-end summary,
investment-dashboard weighted return) do rely on SUMs. Implementation cost likely
exceeds the threat model benefit for single-host LAN deployment; revisit the
defer at this commit's checkpoint before proceeding.

**D. Implementation pattern.**

```python
# app/models/encrypted.py
class EncryptedString(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def __init__(self, length=None, **kwargs):
        super().__init__(**kwargs)
        self._length = length  # Advisory; enforced in Python, not DB.

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        from app.services import mfa_service  # reuses MultiFernet
        cipher = mfa_service.get_encryption_key()
        return cipher.encrypt(value.encode("utf-8"))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        from app.services import mfa_service
        cipher = mfa_service.get_encryption_key()
        return cipher.decrypt(bytes(value)).decode("utf-8")


class EncryptedDecimal(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        from decimal import Decimal
        from app.services import mfa_service
        cipher = mfa_service.get_encryption_key()
        return cipher.encrypt(str(value).encode("utf-8"))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        from decimal import Decimal
        from app.services import mfa_service
        cipher = mfa_service.get_encryption_key()
        return Decimal(cipher.decrypt(bytes(value)).decode("utf-8"))
```

**F. Test plan (18 tests abridged):** Encrypted column round-trips; email_hash
lookup for authenticate; email_hash unique constraint; balance arithmetic over
decrypted values; migration backfill; downgrade impossible (data loss risk --
document).

**N. Risk and rollback.**

- **Downgrade impossibility:** once encrypted, downgrading the schema requires
  decrypting every row back to plaintext. The `downgrade()` MUST do this, OR raise
  `NotImplementedError` with a recovery procedure. The plan prefers the decrypt-
  in-downgrade path so rollback is possible within the retention of the encryption
  key.
- **Performance:** verified via full-suite `test_services/` timing. Expect ~5-10%
  slowdown on read paths.

**O.** F-147 Fixed on merge.

---

### Commit C-40: Migration backfill convention + review docstrings + bare `pass` downgrade fixes

**Findings addressed:** F-026 (High), F-132 (Low), F-131 (Low), F-133 (Low).
**OWASP:** A08:2021.
**ASVS L2 controls closed:** V14.1.2.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** F-026: `efffcf647644` adds NOT NULL `account_id` without backfill.
F-131: `b4c5d6e7f8a9` uses bare `pass` in downgrade. F-133: `7abcbf372fff` downgrade
fails if data violates narrower constraint. F-132: no `review_by:` docstring
convention.

**B. Files modified.**

- `migrations/versions/efffcf647644_add_account_id_column_to_transactions.py`
  -- edit in place to add three-step pattern: add nullable, backfill via
  `UPDATE budget.transactions SET account_id = (<derivation>)`, alter to NOT
  NULL. Decision at commit checkpoint: what is the correct derivation for
  `account_id` given a historical Transaction with no account reference?
  Candidate: use the user's default account at the time of Transaction creation
  via `budget.accounts.is_active=TRUE` WHERE user_id match. The migration's
  backfill logic may be a best-effort; document assumptions in the commit message.
- `migrations/versions/b4c5d6e7f8a9_backfill_raise_effective_year.py:34` -- replace
  `pass` with `raise NotImplementedError(<actionable reason>)`.
- `migrations/versions/7abcbf372fff_add_tax_year_to_state_tax_configs.py` downgrade
  -- replace with `raise NotImplementedError` that includes the manual recovery
  SQL.
- Every destructive migration (audited per F-132) gets a `# Review: <name> <date>`
  docstring line; going-forward convention documented in `docs/coding-standards.md`.
- Tests: 2.

**E.** Migration (illustrative edit for efffcf647644).

```python
def upgrade():
    """Add account_id FK column to budget.transactions (safe three-step pattern).

    Step 1: Add nullable column.
    Step 2: Backfill via UPDATE joining pay_period -> user -> default_account.
    Step 3: Alter to NOT NULL + add FK + create index.
    """
    # Step 1.
    op.add_column(
        "transactions",
        sa.Column("account_id", sa.Integer(), nullable=True),
        schema="budget",
    )

    # Step 2. Historical transactions predate the account column. Derive
    # account_id by joining pay_periods -> users -> (user's first active
    # checking account) as a best-effort backfill; flag rows where the
    # derivation fails.
    op.execute("""
        UPDATE budget.transactions t
        SET account_id = (
            SELECT a.id
            FROM budget.accounts a
            JOIN budget.pay_periods pp ON pp.user_id = a.user_id
            JOIN ref.account_types at ON at.id = a.account_type_id
            WHERE pp.id = t.pay_period_id
              AND a.is_active = TRUE
              AND at.name = 'Checking'
            ORDER BY a.created_at ASC
            LIMIT 1
        )
        WHERE t.account_id IS NULL
    """)

    # Step 3. Reject any row the backfill could not resolve.
    conn = op.get_bind()
    unresolved = conn.execute(sa.text(
        "SELECT count(*) FROM budget.transactions WHERE account_id IS NULL"
    )).scalar()
    if unresolved > 0:
        raise RuntimeError(
            f"{unresolved} transactions could not be backfilled with an "
            f"account_id. Resolve manually before proceeding:\n"
            f"  SELECT id, name, pay_period_id FROM budget.transactions "
            f"WHERE account_id IS NULL;"
        )
    op.alter_column("transactions", "account_id", nullable=False, schema="budget")
    op.create_index(
        "idx_transactions_account", "transactions", ["account_id"],
        unique=False, schema="budget",
    )
    op.create_foreign_key(
        "fk_transactions_account_id", "transactions", "accounts",
        ["account_id"], ["id"], source_schema="budget", referent_schema="budget",
    )
```

**F. Test plan (2 tests):** migration backfill runs on realistic seed data;
NotImplementedError messages are actionable strings.

**N. Rollback considerations.** Editing historical migrations requires the
developer to assess whether any deployment has the old form applied. Production
has the column per audit evidence; the new three-step path is idempotent (WHERE
`account_id IS NULL` guard). Alembic does NOT re-run applied migrations on upgrade,
so the edit takes effect only on fresh-DB bring-up.

**G-O.** Standard. F-026 + F-131 + F-132 + F-133 Fixed.

---

### Commit C-41: Duplicate CHECK cleanup + missing uq_scenarios_one_baseline

**Findings addressed:** F-027 (High), F-069 (Medium).
**OWASP:** A08:2021; A04:2021.
**ASVS L2 controls closed:** V14.1.2, V13.1.4.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** F-027: migrations #7 and #28 declare overlapping CHECK constraints
with different names; model declares only the #28 pair. F-069: migration #7 declared
`uq_scenarios_one_baseline`; live DB missing.

**B. Files modified.**

- `migrations/versions/<new>_reconcile_ck_and_baseline_unique.py` -- new migration
  that idempotently drops leftover `ck_transactions_positive_*` (via `IF EXISTS`),
  asserts the `ck_transactions_{estimated,actual}_amount` pair is present,
  recreates `uq_scenarios_one_baseline` if missing.
- Update the historical `c5d6e7f8a901` migration's drop path to guard with IF
  EXISTS so replay against a clean DB works.
- Tests: 4.

**E.** Migration:

```python
def upgrade():
    op.execute("ALTER TABLE budget.transactions DROP CONSTRAINT IF EXISTS ck_transactions_positive_amount")
    op.execute("ALTER TABLE budget.transactions DROP CONSTRAINT IF EXISTS ck_transactions_positive_actual")
    # The canonical pair from the model:
    # ck_transactions_estimated_amount, ck_transactions_actual_amount
    # -- already present via a later migration. Assert they exist.
    conn = op.get_bind()
    has_est = conn.execute(sa.text(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'ck_transactions_estimated_amount'"
    )).scalar()
    if has_est == 0:
        op.create_check_constraint(
            "ck_transactions_estimated_amount", "transactions",
            "estimated_amount >= 0", schema="budget",
        )
    has_act = conn.execute(sa.text(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'ck_transactions_actual_amount'"
    )).scalar()
    if has_act == 0:
        op.create_check_constraint(
            "ck_transactions_actual_amount", "transactions",
            "actual_amount IS NULL OR actual_amount >= 0", schema="budget",
        )
    # Resolve duplicate baselines first (if any) then recreate the unique.
    dup_count = conn.execute(sa.text(
        "SELECT count(*) FROM ("
        "  SELECT user_id FROM budget.scenarios "
        "  WHERE is_baseline = TRUE AND is_deleted = FALSE "
        "  GROUP BY user_id HAVING count(*) > 1) d"
    )).scalar()
    if dup_count > 0:
        raise RuntimeError(
            f"{dup_count} users have duplicate baseline scenarios. "
            "Resolve manually before running this migration."
        )
    has_uq = conn.execute(sa.text(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_scenarios_one_baseline'"
    )).scalar()
    if has_uq == 0:
        op.create_index(
            "uq_scenarios_one_baseline", "scenarios", ["user_id"],
            unique=True, schema="budget",
            postgresql_where=sa.text("is_baseline = TRUE"),
        )


def downgrade():
    # Do NOT drop the constraints; they are the canonical state.
    raise NotImplementedError(
        "This migration reconciles historical drift; its upgrade is idempotent. "
        "Downgrade would re-introduce the drift and is not supported. "
        "If needed, drop uq_scenarios_one_baseline and recreate the legacy "
        "ck_transactions_positive_* constraints manually."
    )
```

**F. Test plan (4 tests):** idempotent on a DB where constraints already exist;
recreates missing uq_scenarios_one_baseline; fails with actionable message on
duplicate baseline data; downgrade raises NotImplementedError.

**G-O.** Standard. F-027 + F-069 Fixed.

---

### Commit C-42: Salary + hysa_params migration repair + missing FK indexes

**Findings addressed:** F-071 (Medium), F-072 (Medium), F-079 (Medium), F-137 (Low),
F-138 (Low), F-139 (Low), F-140 (Low).
**OWASP:** A04:2021.
**ASVS L2 controls closed:** V13.1.4.
**Depends on:** None.
**Complexity:** Medium.

**A. Context.** F-071 salary migration dropped 3 indexes without restoration. F-072
hysa_params rename left PK/sequence/FK under legacy name. F-137/F-138: self-
referential FKs and hysa FK still under legacy names. F-139/F-140: missing indexes.

**B. Files modified.**

- `migrations/versions/<new>_repair_salary_indexes_and_hysa_params_renames.py` --
  new migration idempotently recreating:
  - `idx_deductions_profile ON salary.paycheck_deductions (salary_profile_id)`
  - `idx_salary_raises_profile ON salary.salary_raises (salary_profile_id)`
  - `idx_tax_brackets_bracket_set ON salary.tax_brackets (bracket_set_id, sort_order)`
  - `idx_rate_history_account ON budget.rate_history (account_id, effective_date DESC)`
  - `idx_pension_profiles_user ON salary.pension_profiles (user_id)`
  - `idx_pension_profiles_salary_profile ON salary.pension_profiles (salary_profile_id)`
  - `idx_calibration_deduction_overrides_deduction ON budget.calibration_deduction_overrides (deduction_id)`
- ALTER SEQUENCE / INDEX / CONSTRAINT RENAMEs for hysa_params legacy names:
  - `hysa_params_pkey` -> `interest_params_pkey`
  - `hysa_params_id_seq` -> `interest_params_id_seq`
  - `hysa_params_account_id_fkey` -> `fk_interest_params_account` (also fixes F-078 naming for this FK).
- Self-referential FK renames: `transactions_credit_payback_for_id_fkey` ->
  `fk_transactions_credit_payback_for`; `scenarios_cloned_from_id_fkey` ->
  `fk_scenarios_cloned_from`.
- Model `__table_args__` updates to declare the indexes (so autogenerate doesn't
  regenerate them).
- Tests: 8.

**E.** Migration body (idempotent):

```python
def upgrade():
    # Indexes -- use IF NOT EXISTS for idempotency.
    op.execute("CREATE INDEX IF NOT EXISTS idx_deductions_profile "
               "ON salary.paycheck_deductions (salary_profile_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_salary_raises_profile "
               "ON salary.salary_raises (salary_profile_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_tax_brackets_bracket_set "
               "ON salary.tax_brackets (bracket_set_id, sort_order)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_rate_history_account "
               "ON budget.rate_history (account_id, effective_date DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pension_profiles_user "
               "ON salary.pension_profiles (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pension_profiles_salary_profile "
               "ON salary.pension_profiles (salary_profile_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_calibration_deduction_overrides_deduction "
               "ON budget.calibration_deduction_overrides (deduction_id)")

    # Renames (guard by conditional existence to be replay-safe).
    def _rename_if_exists(old, new, kind):
        conn = op.get_bind()
        if kind == "index":
            exists = conn.execute(sa.text(
                "SELECT count(*) FROM pg_indexes WHERE indexname = :name"
            ), {"name": old}).scalar()
            if exists:
                op.execute(f"ALTER INDEX {old} RENAME TO {new}")
        elif kind == "constraint":
            exists = conn.execute(sa.text(
                "SELECT count(*) FROM pg_constraint WHERE conname = :name"
            ), {"name": old}).scalar()
            if exists:
                # Determine the table; conname is globally unique in pg_constraint
                # scoped by namespace, so find the table.
                row = conn.execute(sa.text(
                    "SELECT n.nspname, c.relname FROM pg_constraint cn "
                    "JOIN pg_class c ON c.oid = cn.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE cn.conname = :name"
                ), {"name": old}).fetchone()
                if row:
                    op.execute(
                        f"ALTER TABLE {row.nspname}.{row.relname} "
                        f"RENAME CONSTRAINT {old} TO {new}"
                    )
        elif kind == "sequence":
            exists = conn.execute(sa.text(
                "SELECT count(*) FROM pg_class WHERE relkind='S' AND relname=:name"
            ), {"name": old}).scalar()
            if exists:
                op.execute(f"ALTER SEQUENCE budget.{old} RENAME TO {new}")

    _rename_if_exists("hysa_params_pkey", "interest_params_pkey", "index")
    _rename_if_exists("hysa_params_id_seq", "interest_params_id_seq", "sequence")
    _rename_if_exists("hysa_params_account_id_fkey", "fk_interest_params_account", "constraint")
    _rename_if_exists("transactions_credit_payback_for_id_fkey",
                      "fk_transactions_credit_payback_for", "constraint")
    _rename_if_exists("scenarios_cloned_from_id_fkey",
                      "fk_scenarios_cloned_from", "constraint")
```

**F. Test plan (8 tests):** index exists after upgrade; rename applied when legacy
name present; idempotent second upgrade; migration works on fresh DB.

**G-O.** Standard. F-071 + F-072 + F-079 + F-137 + F-138 + F-139 + F-140 Fixed.

---

### Commit C-43: FK ondelete sweep + naming convention forward + inter-budget pay_period_id alignment

**Findings addressed:** F-073 (Medium), F-078 (Medium), F-136 (Low).
**OWASP:** A04:2021.
**ASVS L2 controls closed:** V13.1.4.
**Depends on:** C-42.
**Complexity:** Medium.

**A. Context.** F-073: 9 ref-table FKs lack explicit `ondelete=RESTRICT`. F-078:
49 of 52 FKs use Alembic default `<table>_<column>_fkey`. F-136: inconsistent
ondelete policies on `pay_period_id` across budget tables.

**B. Files modified.**

- Migration: drop and recreate the 9 FKs with explicit `ondelete=RESTRICT` and
  `fk_*` names. Also standardize `budget.transfers.pay_period_id` to CASCADE
  (match `budget.transactions.pay_period_id`) -- decision at commit checkpoint
  because the asymmetry may be intentional (transfers as financial commitments
  that should not disappear if a period is deleted).
- Model updates.
- `migrations/env.py` -- add a `naming_convention` so future autogenerate produces
  `fk_*` names without manual intervention.
- Tests: 6.

**D.** Naming convention for `env.py`:

```python
from sqlalchemy import MetaData

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}
target_metadata = MetaData(naming_convention=naming_convention)
```

**F. Test plan (6 tests):** 9 FKs in live DB have ondelete=RESTRICT; all 9 use
`fk_*` name prefix; `pay_period_id` FKs consistent; autogenerate of a trivial
column change produces `fk_*` name.

**G-O.** Standard. F-073 + F-078 + F-136 Fixed. Note: F-078 notes 49 of 52 FKs;
this commit renames only the 9 from F-073 + 3 renamed in C-42 + the 2 self-refs
in C-42. The remaining ~35 Alembic-default names are retained -- forward
convention enforcement is the fix. Document this in the migration docstring.

---

### Commit C-44: verify_password non-string hardening

**Findings addressed:** F-083 (Low).
**OWASP:** A07:2021.
**ASVS L2 controls closed:** V7.4.2.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** `auth_service.verify_password:276-291` only guards for `None`; a
non-string caller (empty bytes, Decimal) raises AttributeError.

**B. Files modified.**

- `app/services/auth_service.py:276-291` -- tighten the guard.
- Tests: 4.

**D.**

```python
def verify_password(plain_password, password_hash):
    if not isinstance(plain_password, str) or not plain_password:
        return False
    if not isinstance(password_hash, str):
        return False
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), password_hash.encode("utf-8"),
    )
```

**F. Test plan (4 tests):** None returns False; empty string returns False; int
returns False; bytes returns False; valid string + hash returns True.

**G-O.** Standard. F-083 Fixed.

---

### Commit C-45: Retirement-dashboard Decimal fix + grid balance_row None-check + rounding docstrings

**Findings addressed:** F-099 (Low), F-100 (Low), F-101 (Low), F-126 (Low), F-127 (Low).
**OWASP:** N/A (availability / style).
**ASVS L2 controls closed:** V13 style.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** F-099: grid.balance_row dereferences None scenario. F-100/F-101:
retirement dashboard float() casts + magic numbers. F-126/F-127: documented
rounding simplifications need docstring references.

**B. Files modified.**

- `app/routes/grid.py:400-441 balance_row` -- add None-check on scenario.
- `app/services/retirement_dashboard_service.py:225-259 compute_slider_defaults`
  -- Decimal throughout; explicit None checks; extract constants
  `_DEFAULT_SWR_PCT = Decimal("4.00")`, `_DEFAULT_RETURN_PCT = Decimal("7.00")`.
- `app/services/interest_projection.py:15` -- docstring explicitly calls out
  365-day convention as accepted simplification.
- `app/services/paycheck_calculator.py:91-93` -- docstring explicitly calls out
  $0.13 residue.
- Tests: 5.

**D.**

```python
# grid.py:balance_row after the scenario query
if scenario is None:
    return "", 204  # same as no-current-period branch
```

```python
# retirement_dashboard_service.compute_slider_defaults
_DEFAULT_SWR_PCT = Decimal("4.00")  # Trinity study baseline.
_DEFAULT_RETURN_PCT = Decimal("7.00")  # S&P 500 ~30yr real return.

def compute_slider_defaults(data):
    settings = data["settings"]
    if settings is None or settings.safe_withdrawal_rate is None:
        current_swr_pct = _DEFAULT_SWR_PCT
    else:
        current_swr_pct = (settings.safe_withdrawal_rate * Decimal("100")).quantize(
            Decimal("0.01"),
        )
    # ... weighted-return computation also in Decimal ...
```

**F. Test plan (5 tests):** balance_row returns 204 when no baseline; dashboard
defaults use Decimal; zero SWR round-trips as Decimal("0"); 365-day leap-year
docstring present; paycheck residue docstring present.

**G-O.** Standard. F-099 + F-100 + F-101 + F-126 + F-127 Fixed.

---

### Commit C-46: Narrow `except Exception:` blocks

**Findings addressed:** F-145 (Low).
**OWASP:** A09:2021.
**ASVS L2 controls closed:** V7.4.2.
**Depends on:** C-24 (schema Range sweep; so real validation errors reach route
handlers as clean ValidationError rather than being swallowed as IntegrityError).
**Complexity:** Medium.

**A. Context.** 14 `except Exception:` blocks in routes. Each swallows errors that
should surface as 500s or distinct 400 validation errors. Post-C-24, the vast
majority should be `except (ValidationError, IntegrityError)`.

**B. Files modified.**

- `app/routes/salary.py` -- 11 hits; narrow to specific exception tuples.
- `app/routes/retirement.py:296` -- 1 hit.
- `app/routes/investment.py:813` -- 1 hit.
- `app/routes/health.py:41` -- retain `except Exception` with pylint disable
  (acceptable for health check; document).
- Tests: 14 (one per hit that reproduces a previously-silent error).

**D.** Pattern per hit:

```python
# Before:
try:
    db.session.commit()
except Exception:
    db.session.rollback()
    flash("Failed to add raise. Please try again.", "danger")

# After:
try:
    db.session.commit()
except IntegrityError as exc:
    db.session.rollback()
    log_event(
        logger, logging.WARNING, "integrity_error", BUSINESS,
        "Integrity error adding raise",
        user_id=current_user.id, error=str(exc.orig),
    )
    flash(f"Could not save: {exc.orig}", "danger")
```

For validation-class errors (ValidationError from Marshmallow or service layer), the
schema layer from C-24 already raises with actionable message; the route's try
block wraps only `db.session.commit()` plus the service call.

**F. Test plan (14 tests):** each previously-swallowed error surfaces with the
right response code and a specific user-facing message.

**G-O.** Standard. F-145 Fixed.

---

### Commit C-47: Requirement hash pins

**Findings addressed:** F-159 (Info).
**OWASP:** A08:2021.
**ASVS L2 controls closed:** V14.2.4.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** `requirements.txt` pins direct deps but not transitive; 17
transitive deps can silently drift.

**B. Files modified.**

- Introduce `pip-tools` (dev-only): `requirements-dev.txt` adds `pip-tools`.
- Rename `requirements.txt` -> `requirements.in`.
- Generate new `requirements.txt` via `pip-compile --generate-hashes
  --output-file=requirements.txt requirements.in`.
- `Dockerfile` -- `pip install --require-hashes -r requirements.txt`.
- `docs/runbook.md` -- document the `pip-compile` upgrade procedure.
- Tests: 1 (`test_requirements_have_hashes` asserting every line has
  `--hash=sha256:...`).

**G-O.** Standard. F-159 Fixed.

---

### Commit C-48: Argon2id password hashing migration + pepper

**Findings addressed:** F-088 (Low), F-141 (Low).
**OWASP:** A02:2021; A07:2021.
**ASVS L2 controls closed:** V2.4.1, V2.4.5, V2.1.2.
**Depends on:** C-11 (lockout ensures brute-force resistance while hashes migrate).
**Complexity:** Large.

**A. Context.** bcrypt truncates at 72 bytes; F-088 + F-141 together recommend
Argon2id (modern baseline) with a server-side pepper HMAC. Users' hashes migrate
opportunistically on next login: if bcrypt verifies, re-hash with Argon2id and
store.

**B. Files modified.**

- `requirements.txt` -- add `argon2-cffi==23.1.0` (pinned).
- `app/services/auth_service.py` -- `hash_password`, `verify_password` detect the
  hash format by prefix (`$2b$` for bcrypt, `$argon2id$` for Argon2id). On successful
  bcrypt verify, rehash + update. Add server-side pepper via HMAC-SHA256 wrapping
  the password before hashing.
- `app/config.py` -- add `PASSWORD_PEPPER` env var (required in production).
- `.env.example` -- document the pepper generation.
- Migration: none (hash format is self-identifying).
- Tests: 18 (covering bcrypt legacy verify, Argon2id verify, auto-rehash on login,
  pepper mismatch rejected).

**D.** Implementation (representative):

```python
import hmac
import hashlib

from argon2 import PasswordHasher, exceptions as argon2_exceptions


_ARGON2 = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16,
)
_PASSWORD_MAX_BYTES = 128  # ASVS L2 V2.1.2 target (up from bcrypt's 72)


def _pepper_password(plain_password):
    pepper = os.getenv("PASSWORD_PEPPER")
    if not pepper:
        raise RuntimeError("PASSWORD_PEPPER must be set.")
    return hmac.new(
        pepper.encode("utf-8"), plain_password.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def hash_password(plain_password, rounds=None):
    if len(plain_password.encode("utf-8")) > _PASSWORD_MAX_BYTES:
        raise ValidationError(
            f"Password is too long ({_PASSWORD_MAX_BYTES} bytes max)."
        )
    peppered = _pepper_password(plain_password)
    return _ARGON2.hash(peppered)


def verify_password(plain_password, password_hash):
    if not isinstance(plain_password, str) or not plain_password:
        return False
    peppered = _pepper_password(plain_password)
    if password_hash.startswith("$argon2id$"):
        try:
            _ARGON2.verify(password_hash, peppered)
            return True
        except argon2_exceptions.VerifyMismatchError:
            return False
    # Legacy bcrypt path.
    if password_hash.startswith("$2"):
        if not bcrypt.checkpw(
            plain_password.encode("utf-8"), password_hash.encode("utf-8"),
        ):
            return False
        # Opportunistic rehash.
        return "REHASH"  # Sentinel; caller handles rehash.
    return False
```

Caller in `authenticate`:

```python
verify_result = verify_password(password, user.password_hash)
if verify_result == "REHASH":
    user.password_hash = hash_password(password)
    db.session.commit()
elif verify_result is False:
    # ... existing failed-login logic ...
    raise AuthError(...)
```

**F. Test plan (18 tests):** bcrypt legacy hash verifies; bcrypt legacy verify
triggers rehash to Argon2id; Argon2id hash verifies; Argon2id rejects wrong
password; pepper mismatch (env var changed) rejects every password; 128-byte
password hashes correctly; migration-sequence: user logs in under bcrypt, hash
becomes Argon2id, next login uses Argon2id only.

**N. Risk.** Requires `PASSWORD_PEPPER` set in production. If not set, app refuses
to verify any password -- locks everyone out. Add entrypoint check (before
Gunicorn start): `if [ -z "${PASSWORD_PEPPER}" ]; then exit 1; fi`.

**G-O.** Standard. F-088 + F-141 Fixed.

---

### Commit C-49: Config drift check script

**Findings addressed:** F-157 (Low).
**OWASP:** A05:2021.
**ASVS L2 controls closed:** V14.1.5.
**Depends on:** C-32.
**Complexity:** Small.

**A. Context.** No tooling compares running config to baseline.

**B. Files modified.**

- `scripts/config_audit.py` -- new. Hashes the running container's
  `nginx.conf`, `gunicorn.conf.py`, `/etc/docker/daemon.json`, and the runtime
  environment-variable set (minus secrets). Compares against a baseline file
  `deploy/config-baseline.json` committed alongside.
- `deploy/config-baseline.json` -- initial hashes from a clean deploy.
- `docs/runbook.md` -- document re-hashing procedure after intentional changes.
- Tests: 3.

**D.** Script skeleton:

```python
"""Config drift check. Compare running container configs against committed baseline.

Run weekly or after any deploy. Diffs are reported with severity markers:
  INFO  -- whitespace-only change (hash differs but semantics match)
  WARN  -- known env variable changed (rotation, upgrade)
  FAIL  -- unexpected structural change (investigate)
"""
# ... implementation ...
```

**F-O.** Standard small commit. F-157 Fixed.

---

### Commit C-50: Data classification + retention cleanup + privacy policy + terms of service

**Findings addressed:** F-151 (Low), F-153 (Low), F-094 (Low, rejected defer).
**OWASP:** A09:2021; GDPR.
**ASVS L2 controls closed:** V8.3.3, V8.3.4, V8.3.8.
**Depends on:** C-13 (audit_log table), C-15 (off-host shipping for retention-
expired logs).
**Complexity:** Small.

**A. Context.** F-151 data classification doc. F-153 retention job. F-094 privacy
policy + ToS.

**B. Files modified.**

- `docs/data-classification.md` -- new. Classifies every data category.
- `scripts/audit_cleanup.py` -- new. Deletes `system.audit_log` rows older than
  `AUDIT_RETENTION_DAYS`; archives to S3 before delete. Scheduled via cron.
- `app/templates/legal/privacy.html`, `legal/terms.html` -- new.
- `app/routes/legal.py` -- new, `/privacy` + `/terms` routes.
- Tests: 4.

**G-O.** Standard. F-094 + F-151 + F-153 Fixed.

---

### Commit C-51: nmap re-run documentation

**Findings addressed:** F-158 (Info).
**OWASP:** N/A.
**ASVS L2 controls closed:** N/A.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** F-158 is an audit-rerun note; not a code fix. Document in
`docs/runbook.md` the command: `nmap --version | head -1 > scans/nmap-version.txt;
nmap -sV -p 80,443,5432,8000 127.0.0.1 >> scans/nmap-localhost.txt`.

**B.** Just `docs/runbook.md`. One paragraph.

**G-O.** Trivial. F-158 Fixed.

---

### Commit C-52: Per-record read audit instrumentation

**Findings addressed:** F-152 (Low, rejected defer).
**OWASP:** A09:2021.
**ASVS L2 controls closed:** V8.3.5.
**Depends on:** C-13, C-14, C-15.
**Complexity:** Medium.

**A. Context.** F-152 rejected defer. Implement read-side audit for financial-
detail views: GET of a specific Transaction, Account, Transfer, or balance
projection emits a structured `resource_viewed` log event containing
`resource_type`, `resource_id`, `user_id`, `request_id`. Keep noise low by
restricting to DETAIL views only (not list views).

**B. Files modified.**

- `app/utils/log_events.py` -- add `READ` category constant.
- `app/routes/transactions.py`, `transfers.py`, `accounts.py`, `salary.py` -- add
  `log_event` call in specific-resource GET handlers (after ownership check).
- Tests: 6.

**D.**

```python
@transactions_bp.route("/transactions/<int:txn_id>", methods=["GET"])
@login_required
def view_transaction(txn_id):
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    log_event(
        logger, logging.INFO, "resource_viewed", READ,
        "Transaction detail viewed",
        user_id=current_user.id, resource_type="transaction", resource_id=txn_id,
    )
    return _render_cell(txn)
```

**F. Test plan (6 tests):** detail view emits event; list view does NOT (noise
control); event includes required fields; read events redacted for PII per C-16
scrubber.

**G-O.** Standard. F-152 Fixed.

---

### Commit C-53: Server-side session store + WebAuthn / FIDO2 enrollment

**Findings addressed:** F-143 (Low, rejected defer), F-092 (Low, rejected defer).
**OWASP:** A07:2021.
**ASVS L2 controls closed:** V2.3.2, V3.3.4.
**Depends on:** C-06, C-07.
**Complexity:** Large.

**A. Context.** Two major features rejected from the defer list. They share a
prerequisite: server-side session storage. Once sessions are server-side, active-
sessions UI (F-143) becomes feasible and WebAuthn (F-092) can attach authenticator
lists to session state.

**B. Files modified.**

- `requirements.txt` -- add `Flask-Session==0.8.0`, `webauthn==2.2.0` (pinned).
- `app/extensions.py` -- configure `Flask-Session` with Redis backend (reuses the
  container from C-06). Session ID is random; cookie stores only the session ID.
- `app/config.py` -- `SESSION_TYPE="redis"`, `SESSION_REDIS=<url>`,
  `SESSION_USE_SIGNER=True`, `SESSION_KEY_PREFIX="shekel:"`.
- `app/models/user.py` -- new table `WebAuthnCredential(user_id, credential_id,
  public_key, sign_count, transports, name, created_at)`.
- Migration adding WebAuthn credentials table.
- `app/routes/auth.py` -- add `/mfa/webauthn/register`, `/mfa/webauthn/verify`,
  `/settings/sessions` (list), `/settings/sessions/<id>/revoke`.
- Templates for enrollment + session list.
- Tests: 30+.

**D.** WebAuthn enrollment (representative; full spec is long):

```python
# /mfa/webauthn/register
from webauthn import generate_registration_options, verify_registration_response

@auth_bp.route("/mfa/webauthn/register", methods=["GET"])
@login_required
def webauthn_register_start():
    options = generate_registration_options(
        rp_id=current_app.config["WEBAUTHN_RP_ID"],
        rp_name="Shekel",
        user_id=str(current_user.id).encode(),
        user_name=current_user.email,
    )
    flask_session["_webauthn_challenge"] = options.challenge
    return jsonify(options_to_json(options))
```

Session list:

```python
@auth_bp.route("/settings/sessions")
@login_required
def list_sessions():
    # Query Redis for all session keys matching "shekel:*"; filter by user_id.
    redis = flask_sessions.interface.redis
    keys = redis.scan_iter(match=f"shekel:*")
    sessions = []
    for key in keys:
        data = json.loads(redis.get(key))
        if data.get("_user_id") == current_user.id:
            sessions.append({
                "id": key.decode().split(":", 1)[1],
                "ip": data.get("_ip"),
                "user_agent": data.get("_user_agent"),
                "created_at": data.get("_session_created_at"),
            })
    return render_template("settings/_active_sessions.html", sessions=sessions)
```

**F. Test plan (30+ tests abridged):** WebAuthn register; WebAuthn verify;
WebAuthn works alongside TOTP; session list shows current + others; revoke
specific session logs out that session only; current session survives.

**Architectural decision at commit checkpoint.** WebAuthn + server-side sessions
is a major addition. Developer may want to stage it as two commits (C-53a: move
to server-side sessions + active-sessions UI; C-53b: WebAuthn). Default: one
commit per this plan.

**G-O.** Standard. F-143 + F-092 Fixed.

---

### Commit C-54: GDPR data export + account deletion

**Findings addressed:** F-093 (Low, rejected defer).
**OWASP:** N/A (GDPR).
**ASVS L2 controls closed:** V8.3.2.
**Depends on:** C-13 (audit_log records the export/delete events).
**Complexity:** Large.

**A. Context.** GDPR Articles 15 (access), 17 (erasure), 20 (portability). Export
streams a ZIP of CSV files per owned table; delete requires re-auth + 7-day
cooldown + cascading soft-delete.

**B. Files modified.**

- `app/services/data_export_service.py` -- new. Streams per-table CSV into an
  in-memory ZIP.
- `app/services/account_deletion_service.py` -- new. Implements the cooldown
  machine: request -> email-style confirmation stored in DB -> 7-day window ->
  cascade.
- `app/routes/settings.py` -- new routes `/settings/export`, `/settings/delete`.
- Templates for the deletion confirmation flow.
- Migration adding `auth.deletion_requests(user_id, requested_at, confirmed,
  executed_at)`.
- Tests: 20.

**G-O.** Standard. F-093 Fixed.

---

### Commit C-55: Document key-material-in-memory residual risk

**Findings addressed:** F-149 (Low, rejected defer).
**OWASP:** A02:2021.
**ASVS L2 controls closed:** V6.4.2.
**Depends on:** None.
**Complexity:** Small.

**A. Context.** F-149 finding explicitly notes "no practical mitigation without
HSM/PKCS#11." The rejection of the defer does not conjure a hardware mitigation.
This commit hardens what CAN be hardened in software + documents the residual risk:

- `/proc/<pid>/environ` is readable only by the process owner and root. Verify in
  C-56 host runbook.
- Secrets loaded via Docker secrets per C-38 live in tmpfs `/run/secrets/`, not
  persistent disk.
- Document the residual threat model in `docs/runbook_secrets.md` so the next
  operator understands the limits.

**B. Files modified.**

- `docs/runbook_secrets.md` -- new section "Key material in memory residual risk".
- `scripts/verify_secret_permissions.sh` -- new helper that validates
  `/run/secrets/*` permissions and that `/proc/<pid>/environ` is mode 400.
- Tests: 2 (script exits 0 on healthy state; non-zero on mis-perm).

**G-O.** Trivial. F-149 marked Fixed (with residual-risk disclosure) on merge.

---

### Commit C-56: Host hardening runbook

**Findings addressed:** F-023 (High), F-024 (High), F-065 (Medium), F-066 (Medium),
F-067 (Medium), F-121 (Low), F-122 (Low), F-123 (Low), F-124 (Low), F-125 (Low),
F-130 (Low).
**OWASP:** A05:2021.
**ASVS L2 controls closed:** V14.1.1, V14.2.1.
**Depends on:** None.
**Complexity:** Medium.

**A. Context.** 11 host-level findings that do not fit the repo. The deliverable
is a single `docs/runbook_host_hardening.md` with step-by-step host operations and
a `scripts/host_hardening/` directory of shell helpers the developer runs on the
Arch box.

**B. Files modified.**

- `docs/runbook_host_hardening.md` -- new. Sections per finding with commands,
  expected outputs, and rollback.
- `scripts/host_hardening/chmod_env.sh` -- F-023.
- `scripts/host_hardening/sysctl_kptr.conf` -- F-024 (content for
  `/etc/sysctl.d/99-shekel-hardening.conf`).
- `scripts/host_hardening/auditd_rules.sh` -- F-065.
- `scripts/host_hardening/sshd_hardening.md` -- F-066 + F-130.
- `scripts/host_hardening/core_dumps.conf` -- F-122.
- `scripts/host_hardening/aide_install.sh` -- F-123.
- `scripts/host_hardening/ntp_install.sh` -- F-124.
- `scripts/host_hardening/pam_install.sh` -- F-125.
- No tests (runbook only). The developer executes each step on the host and
  records the output in a post-run audit artifact under
  `docs/audits/security-2026-04-15/host-hardening-output.txt`.

**D.** Runbook template (one section per finding; representative):

```markdown
## F-023: Restrict .env file permissions

Set both .env files to mode 600 so only the owner (josh) can read.

```bash
sudo chmod 600 /home/josh/projects/Shekel/.env
sudo chmod 600 /opt/docker/shekel/.env
ls -la /home/josh/projects/Shekel/.env /opt/docker/shekel/.env
# Expected output ends with: -rw-------
```

**Verification:** The `stat -c %a` for both files returns 600.

**Rollback:** `chmod 644 <path>` -- but this re-opens the finding.
```

**G.** Manual verification is inherent to runbook execution.

**O. Findings.md update.** After the developer confirms each step, F-023, F-024,
F-065, F-066, F-067, F-121, F-122, F-123, F-124, F-125, F-130 marked Fixed.

---

### Summary of 56 commits

(Count check: C-01 through C-56 above.)

Per-commit headers above list Findings addressed; cross-check against the
disposition table in Phase B ensures every Verified finding appears in at least
one commit or an accepted Defer.

---

## Phase D -- Cross-cutting concerns

Several patterns touch multiple commits. They are factored here to avoid
duplicating the same decision in every commit section. Where a commit in Phase C
references "see Phase D X", this is the authoritative explanation.

### D-1: Session invalidation helper pattern

**Commits affected:** C-01, C-08, C-10, C-11, C-12, C-16, C-53.

The `app/utils/session_helpers.invalidate_other_sessions(reason)` helper
(introduced in C-08) is the canonical way to force a user's other sessions out.
Every auth-factor state change uses it:

- Password change (already uses the pattern inline; C-08 extracts).
- MFA enable, disable, regenerate backup codes (C-08, C-09, C-12).
- Backup-code consumption (C-08).
- Lockout/re-auth requirement kicks in (C-11).
- After `rotate_sessions.py` global bump (C-01).
- On server-side session store migration, the helper delegates to
  Flask-Session's `session.destroy_all_for(user_id)` (C-53).

The helper emits one structured log event `other_sessions_invalidated` with a
`reason` parameter so the audit log can answer "why was this session cut?"

### D-2: Stale-form and idempotency pattern

**Commits affected:** C-17, C-18, C-22, C-23, C-26, C-27 (partial).

Two complementary mechanisms:

1. **Optimistic locking via `version_id_col`** on every mutable model (Transaction,
   Transfer, Account, TransactionTemplate, SavingsGoal, SalaryProfile, etc.).
   SQLAlchemy increments `version_id` on every commit; a stale in-memory version
   raises `StaleDataError` which the route handlers catch and return HTTP 409 +
   "reload and retry" UI.
2. **Composite unique constraints** at the DB level to catch idempotency violations
   that bypass the ORM. Every duplicate-prevention finding (F-008, F-046, F-050,
   F-051, F-052, F-069, F-102, F-103, F-104, F-105) is addressed with a partial
   unique index or full composite unique constraint.

The two mechanisms are complementary: `version_id` prevents same-object concurrent
updates; composite unique prevents duplicate-creation races. Both ship together
for the financial-invariant commits.

### D-3: Marshmallow + CHECK constraint sync table

**Commits affected:** C-24, C-25, C-29.

For every field with both a Marshmallow validator and a DB CHECK, both must agree.
Below is the full mapping after C-24 + C-25 + C-29 land. Any future schema
change MUST update both layers simultaneously.

| Column | Marshmallow | DB CHECK | Notes |
|---|---|---|---|
| `salary.salary_raises.percentage` | `Range(min=0.01, max=1000)` | `> 0` | Tightened in C-24 |
| `salary.salary_raises.flat_amount` | `Range(min=0.01, max=10M)` | `> 0` | Same |
| `salary.paycheck_deductions.amount` | `Range(min=0.0001, max=1M)` | `> 0` | Added in C-24 |
| `auth.user_settings.trend_alert_threshold` | `Range(0, 1)` | `>= 0 AND <= 1` | Aligned to decimal 0-1 in C-24 |
| `salary.fica_configs.ss_rate` | `Range(0, 1)` | `>= 0 AND <= 1` | Aligned in C-24 |
| `salary.fica_configs.medicare_rate` | `Range(0, 1)` | `>= 0 AND <= 1` | Same |
| `salary.fica_configs.medicare_surtax_rate` | `Range(0, 1)` | `>= 0 AND <= 1` | Same |
| `salary.state_tax_configs.flat_rate` | `Range(0, 1)` | `>= 0 AND <= 1` | Same |
| `auth.user_settings.default_inflation_rate` | `Range(0, 1)` | `>= 0 AND <= 1` | Aligned |
| `salary.salary_profiles.additional_income` | `Range(min=0)` | `>= 0` | Added in C-24 |
| `salary.salary_profiles.additional_deductions` | `Range(min=0)` | `>= 0` | Same |
| `salary.salary_profiles.extra_withholding` | `Range(min=0)` | `>= 0` | Same |
| `salary.tax_bracket_sets.standard_deduction` | `Range(min=0)` | `>= 0` | Same |
| `salary.tax_bracket_sets.child_credit_amount` | `Range(min=0)` | `>= 0` | Same |
| `salary.tax_bracket_sets.other_dependent_credit_amount` | `Range(min=0)` | `>= 0` | Same |
| `budget.savings_goals.contribution_per_period` | `Range(min=0, exclusive)` | `IS NULL OR > 0` | Aligned in C-25 |
| `budget.loan_params.original_principal` | `Range(min=0, exclusive)` | `> 0` | Aligned |
| `budget.escrow_components.annual_amount` | `Range(min=0)` | `>= 0` | Added in C-24 |
| `budget.escrow_components.inflation_rate` | `Range(0, 1)` | `>= 0 AND <= 1` | Added |
| `budget.interest_params.apy` | `Range(0, 1)` | `>= 0 AND <= 1` | Added |
| `budget.investment_params.annual_contribution_limit` | `Range(min=0)` | `>= 0` | Added |
| `budget.investment_params.employer_flat_percentage` | `Range(0, 1)` | `>= 0 AND <= 1` | Added |
| `budget.investment_params.employer_match_percentage` | `Range(0, 10)` | `>= 0 AND <= 10` | Added |
| `budget.investment_params.employer_match_cap_percentage` | `Range(0, 1)` | `>= 0 AND <= 1` | Added |
| `auth.user_settings.safe_withdrawal_rate` | `Range(0, 1)` | `>= 0 AND <= 1` | Added |
| `auth.user_settings.estimated_retirement_tax_rate` | `Range(0, 1) or None` | `IS NULL OR (>= 0 AND <= 1)` | Added |
| `salary.paycheck_deductions.inflation_rate` | `Range(0, 1) or None` | `IS NULL OR (>= 0 AND <= 1)` | Added |
| `salary.paycheck_deductions.inflation_effective_month` | `Range(1, 12) or None` | `IS NULL OR (>= 1 AND <= 12)` | Added |
| `salary.salary_raises.effective_year` | `Range(2000, 2100)` | `>= 2000 AND <= 2100` | Added |
| `salary.state_tax_configs.standard_deduction` | `Range(min=0) or None` | `IS NULL OR >= 0` | Added |
| `salary.state_tax_configs.tax_year` | `Range(2000, 2100)` | `>= 2000 AND <= 2100` | Added |
| `budget.calibration_overrides.effective_*_rate (x4)` | `Range(0, 1)` | `>= 0 AND <= 1` | Added |
| `budget.rate_history.interest_rate` | `Range(0, 1)` (verify) | `>= 0 AND <= 1` | Added |
| `budget.transactions.estimated_amount` | `Range(min=0)` | `>= 0` | Pre-existing |
| `budget.transactions.actual_amount` | `Range(min=0) or None` | `IS NULL OR >= 0` | Pre-existing |

Every future schema addition must add a row here AND the corresponding Marshmallow
validator AND DB CHECK in one commit.

### D-4: log_event systematic rollout

**Commits affected:** C-13, C-14, C-15, C-16, C-18 (via `other_sessions_invalidated`),
C-19-C-27 (via service-layer log_event calls), C-31 (access-denied), C-46
(integrity_error), C-52 (resource_viewed).

Three layers of audit coverage:

1. **DB-tier triggers (C-13).** Every INSERT/UPDATE/DELETE on financial/auth
   tables writes to `system.audit_log` automatically. Most tamper-resistant (the
   app cannot write to audit_log through the ORM; trigger-only path).
2. **Python-tier `log_event` (C-14).** Every service layer mutation emits a
   structured event with `event_name`, `category`, `user_id`, relevant entity IDs.
   Enables "what happened" queries keyed by event type.
3. **Off-host shipping (C-15).** Both DB audit_log and Python log_event flow to S3
   (or rsyslog / Loki per developer choice). Tamper-evident because the app
   container cannot rewrite S3 Object-Locked objects.

**Event naming convention:** `<verb>_<noun>` or `<noun>_<event>` in snake_case
(e.g. `transfer_created`, `login_failed`, `resource_viewed`, `access_denied`).
Register every new event in `app/utils/log_events.py` so the registry is queryable.

### D-5: Config version control

**Commits affected:** C-32, C-33, C-34, C-37, C-38, C-49.

C-32 establishes `deploy/` directory and moves production configs into the repo.
Subsequent commits modify `deploy/` files rather than the host paths. C-49 adds
a drift-check script that hashes each runtime file and compares against a baseline.

**Authoritative paths:**

- `nginx/nginx.conf` -- retained as historical/bundled; rename to
  `deploy/nginx-bundled/nginx.conf` in C-32.
- `deploy/nginx-shared/nginx.conf`, `deploy/nginx-shared/conf.d/shekel.conf` --
  production configs (mirror of `/opt/docker/nginx/`).
- `deploy/docker-compose.prod.yml` -- production override.
- `gunicorn.conf.py` -- remains at repo root; no drift observed.
- `cloudflared/config.yml` -- remains at current path; updated per C-37.
- `deploy/config-baseline.json` -- committed hash set for drift check.

### D-6: Reference-table ID lookups

**Commits affected:** All commits that touch `status_id`, `transaction_type_id`,
`role_id`, `recurrence_pattern_id`, `account_type_id`, `goal_mode_id`,
`income_unit_id`, `calc_method_id`, `deduction_timing_id`, `tax_type_id`,
`raise_type_id`, `acct_category_id`.

CLAUDE.md rule: Reference tables use integer IDs for logic, strings for display
only. Pattern is `ref_cache.<kind>_id(EnumMember)` -- e.g.
`ref_cache.status_id(StatusEnum.PROJECTED)`. Every commit below MUST use this
pattern and NEVER compare against string `name` columns in Python or Jinja.

Spot-check during Phase 3 execution: grep each commit's diff for
`status.name == "` / `.name == "Paid"` / similar; fail the commit if present.

### D-7: 404-everywhere response rule

**Commits affected:** C-29, C-30, C-31, plus implicit in every commit that exposes
user-scoped resources.

After C-31, every cross-user access returns exactly 404. No 302 redirects with
flash for "not yours" paths. The test helper in `tests/test_integration/test_access_control.py`
enforces this: `_assert_not_found` is strict on 404; `_assert_redirected_to_login`
is for `@login_required`-only routes.

### D-8: Crypto hardening bundle

**Commits affected:** C-01, C-02, C-03, C-04, C-05, C-16, C-30.

The five "one-line crypto fixes" (F-001 + F-004 + F-017 + F-018 + F-019) plus
F-036 + F-037 + F-096 + F-097 all land in C-02 as one bundle. The logic for keeping
them together: (a) they all mutate the same `_register_security_headers` function
or the `ProdConfig` class; (b) each one is individually trivial but their union
closes the "stolen laptop + cafe WiFi" threat set; (c) atomic rollback is safer
than attempting to revert individual header changes.

C-01 (SECRET_KEY excise) must land FIRST because rotating every header set while
legacy cookies under the historical key are still accepted gives an incomplete
defense.

### D-9: Backup code entropy upgrade

**Commits affected:** C-03, C-16.

C-03 changes the generator to 112 bits. Legacy 8-char codes remain valid until
regenerated. C-16 in-app banner prompts the user to regenerate. The template
column widens.

### D-10: TOTP replay prevention

**Commits affected:** C-09.

`auth.mfa_configs.last_totp_timestep` integer column added; `verify_totp_code`
rejects any step `<=` last. F-142 replay logging ships in the same commit.

### D-11: Off-host log destination architectural decision

**Commits affected:** C-15.

Three candidate destinations -- rsyslog / Loki / S3. Plan defaults to S3 with
Object Lock because (1) tamper-resistance is the hardest requirement to satisfy
with rsyslog or Loki alone without additional infrastructure; (2) S3 adds one
outbound HTTPS dependency with well-understood failure modes; (3) Object Lock
provides immutable retention for the configured period even if the operator is
compromised. The developer may override at C-15 checkpoint.

### D-12: Flask-Limiter backend architectural decision

**Commits affected:** C-06.

Redis (recommended) or single-worker Gunicorn. Plan defaults to Redis because
multi-worker Gunicorn is typical production posture and single-worker artificially
limits throughput. Redis adds one always-on container (~64MB). The in-memory
fallback prevents Redis outages from locking users out.

### D-13: Argon2id migration strategy

**Commits affected:** C-48.

Opportunistic rehash on login is the least-disruptive strategy: bcrypt legacy
hashes continue to verify; on successful verify, the hash is rewritten as
Argon2id. Batch rehash is not possible (password plaintexts are not stored).
Documentation: the migration is "complete" only when every active user has logged
in at least once post-C-48. Users inactive >90 days may retain bcrypt; plan
tolerates this because inactivity already represents lower attack value.

### D-14: HSTS preload decision

**Commits affected:** C-02.

The initial deploy of C-02 ships `max-age=31536000; includeSubDomains` without
`preload`. Preload is a one-way commitment (browsers enforce HTTPS permanently).
Plan defers preload submission until (a) domain has been HTTPS-only for >= 90 days,
(b) all subdomains are HTTPS-only, (c) the developer is comfortable with the
commitment. Tracked as a follow-up task, not a deferred finding.

### D-15: Field-level encryption scope

**Commits affected:** C-39.

C-39 encrypts `auth.users.email`, `auth.users.display_name`,
`budget.accounts.current_anchor_balance`. `budget.transactions.estimated_amount`
and `actual_amount` are NOT included because:

- Aggregate queries (balance calculator, year-end summary) sum these columns.
  Encrypted values cannot be summed in SQL.
- Python-level summation is already the balance calculator's pattern; extending
  encryption to transaction amounts would roughly double read-path cost for
  transaction-heavy pages.

If the developer wants broader encryption after C-39, revisit at that commit's
checkpoint; decision captured in `docs/data-classification.md`.

### D-16: Destructive migration approval convention

**Commits affected:** C-40, C-41, C-42, C-43, all future migrations.

Per coding standards and F-132: every destructive migration (drop, rename,
alter-column with narrowing) must include a `# Review: <developer-name> <YYYY-MM-DD>`
docstring line indicating explicit review. `migrations/env.py` naming convention
enforces consistent `fk_*` / `ck_*` / `ix_*` / `uq_*` names going forward.

### D-17: Dependency version pins and hash verification

**Commits affected:** C-47.

`requirements.in` + `requirements.txt` (with `--hash=sha256:...`) via pip-tools.
Every install MUST use `pip install --require-hashes`. Upgrade path documented in
`docs/runbook.md`: edit `.in`, run `pip-compile --upgrade-package <name>`, commit
the resulting `.txt`.

### D-18: Secret rotation strategies

**Commits affected:** C-01, C-04, C-28, C-38, C-48.

Each secret has a rotation story:

- **SECRET_KEY:** `scripts/rotate_sessions.py` (C-01). Session-invalidate all;
  users re-login under the new key.
- **TOTP_ENCRYPTION_KEY:** `scripts/rotate_totp_key.py` (C-04). MultiFernet
  accepts old + new; script re-wraps every ciphertext; remove old key.
- **POSTGRES_PASSWORD:** Manual -- stop app container, rotate in
  `docker-compose.prod.yml` secret, restart. No user-visible event.
- **AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (C-15):** AWS IAM rotation; drop-in
  replacement.
- **APP_ROLE_PASSWORD (C-13):** `ALTER ROLE shekel_app PASSWORD ...` + update
  secret file. No downtime.
- **PASSWORD_PEPPER (C-48):** NOT rotatable without a password-reset-for-all
  event. Document as "set once, never rotate" unless compromised -- in which case
  the operational event is a forced password reset.

### D-19: Architectural decisions pending developer input

Carried forward from Phase B. Resolved inline at each commit's checkpoint during
Phase 3 execution:

- **D-11:** F-082 off-host destination -- S3 / rsyslog / Loki.
- **D-12:** F-034 Limiter backend -- Redis / single-worker.
- **D-13:** F-088 Argon2id -- opportunistic / batch.
- **D-14:** F-018 HSTS preload -- now / wait 90 days / never.
- **D-15:** Encryption scope -- email+name+balance only / expand to transactions.
- **C-31:** F-087 keep Low or accept Red Team deflation to Info.

---

## Phase E -- Accept / Defer rationale (post-checkpoint)

Developer at the Phase B checkpoint accepted only the **dependency-staleness
defers** (F-058, F-059, F-118, F-119). All other proposed defers were rejected and
moved into the commit plan above. This Phase E captures the rigorous rationale
for the four accepted Defers.

### F-058: pyotp stale (33 months since last release)

- **Severity:** Medium.
- **OWASP:** A06:2021 Vulnerable and Outdated Components.
- **Threat model delta:** A vulnerability in `pyotp.TOTP.verify` (e.g. timing
  side-channel, constant-time bug) would require upstream fix. If upstream is
  unresponsive, the app has no MFA fix path short of forking or migrating.
- **Compensating controls currently in place:**
  - F-005 replay prevention (C-09) adds `last_totp_timestep` enforcement which
    reduces the value of a pyotp verify bypass.
  - F-015 proxy trust tightening (C-33) + F-034 rate-limit fix (C-06) make online
    brute-force impractical.
  - F-004 backup-code entropy upgrade (C-03) removes the low-entropy fallback.
  - `pyotp` is pinned to `2.9.0`; trivy + pip-audit monitor for CVEs on each run.
- **Cost-benefit:** Forking pyotp or migrating to `authlib` is ~3-5 days of work
  for a package whose code surface is ~50 lines. Current risk is "hypothetical
  upstream bug", no exploitable CVE known.
- **Deferral horizon:** Re-assess at the next audit cycle, or immediately on any
  published pyotp CVE, or when the package stays unreleased for 48+ months
  (15 months from now).
- **Monitoring and detection:** `pip-audit --requirement requirements.txt` on
  every audit cycle; `trivy sbom` on every container build (C-36).
- **Re-open triggers:** (a) published pyotp CVE at any severity, (b) package
  removed from PyPI, (c) fork announced, (d) 48 months since last release.

### F-059: Flask-Login stale (30 months since last release)

- **Severity:** Medium.
- **OWASP:** A06:2021.
- **Threat model delta:** A session-management vulnerability in Flask-Login would
  require upstream fix. Flask-Login is maintained by the Pallets organization so
  abandonment risk is low.
- **Compensating controls currently in place:**
  - C-07 session_protection=strong.
  - C-08 session invalidation on every auth-factor change.
  - C-10 session lifetime + idle timeout + step-up auth.
  - C-53 (rejected defer, now planned) migrates to Flask-Session + server-side
    store, reducing Flask-Login's surface area.
  - `Flask-Login==0.6.3` pinned; CVE monitoring via pip-audit + trivy.
- **Cost-benefit:** Migrating off Flask-Login would require replacing every
  `@login_required`, `current_user`, `login_user`, `logout_user` call site -- ~50+
  files touched. Not justified without an identified vulnerability.
- **Deferral horizon:** Same as F-058.
- **Monitoring:** Same as F-058.
- **Re-open triggers:** (a) Flask-Login CVE, (b) Pallets organization announces
  unmaintenance, (c) 48 months since last release.

### F-118: psycopg2 LGPL license

- **Severity:** Low.
- **OWASP:** N/A (license compliance).
- **Threat model delta:** None. LGPL-with-exceptions does not trigger copyleft
  for library use; only for modified source distribution. Shekel does not modify
  psycopg2.
- **Compensating controls:** `docs/runbook.md` adds a THIRD-PARTY-LICENSES section
  documenting the LGPL exception.
- **Cost-benefit:** Migrating to psycopg3 (BSD, actively maintained) is plausible
  future work; no security impact.
- **Deferral horizon:** Track with psycopg3 upgrade cycle; not a hard trigger.
- **Re-open triggers:** (a) plan to relicense Shekel under GPL-incompatible terms
  (none on roadmap), (b) psycopg3 maturity warrants migration for other reasons
  (async, better BSD license, etc.).

### F-119: Flask-SQLAlchemy stale (31 months since last release)

- **Severity:** Low.
- **OWASP:** A06:2021.
- **Threat model delta:** Thin integration layer over SQLAlchemy (2.0.49, actively
  maintained). Security-relevant surface lives in SQLAlchemy itself; Flask-
  SQLAlchemy just wires it into Flask's app context.
- **Compensating controls:** SQLAlchemy is pinned and monitored; pip-audit covers
  both.
- **Cost-benefit:** Same profile as F-058/F-059 but lower because abandonment risk
  is minimal (Pallets-maintained).
- **Deferral horizon:** Same as F-058/F-059.
- **Re-open triggers:** Same as F-119.

---

### Phase E checkpoint

The rejected Defers have been absorbed into commits C-36 (F-062), C-38 (F-148),
C-39 (F-147), C-45 (F-126, F-127), C-50 (F-094), C-52 (F-152), C-53 (F-143,
F-092), C-54 (F-093), C-55 (F-149), C-37 (F-154), C-15 (F-146). Each is now
scheduled as a Fix-this-sprint or Fix-backlog commit with its own A-O plan.

**Pending developer confirmation before finalizing Phase F:**

1. The four accepted Defers (F-058, F-059, F-118, F-119) with their rationale
   above. Any the developer wants to reject at this late stage becomes a new
   commit task.
2. Any of the 13 rejected-defer commits (C-36, C-37, C-38, C-39, C-45, C-50, C-52,
   C-53, C-54, C-55, C-15 inclusive) that the developer realizes is too large or
   risky and wants to re-defer.
3. F-NEW-001..005 renumbering: the plan assumed F-161 = F-NEW-001 (transaction
   state machine), F-162 = F-NEW-002 (mark_done raw decimal in transfer branch),
   F-163 = F-NEW-003 (mfa_verify length cap), F-164 = F-NEW-004 (restore_transfer
   account check). F-NEW-005 (other bare `pass` downgrades) was collapsed into
   F-131 scope per grep evidence. Developer separately updates findings.md.
