# 14 -- STRIDE Threat Model (Section 1J)

Session S3 of the Shekel security audit (branch `audit/security-2026-04-15`).

This report walks each of Shekel's six critical assets through the six STRIDE
threat categories against four attacker types. Every cell in the resulting
matrix is explicitly answered -- no blank cells. The goal is to document not
just "what can go wrong" but "what stops it today, and what is the residual
risk if the defense fails."

This report is the deliverable that stays useful a year from now when scanner
findings have been fixed and patched over.

## 0. Method

### The six assets (per workflow Section 1J)

1. **User account** -- email, `password_hash`, `totp_secret` (Fernet-encrypted
   in the DB), backup codes, active session cookies.
2. **Financial data** -- transactions, balances, transfers (and their shadow
   pairs), paychecks, debt accounts, templates, scenarios.
3. **Anchor balance** -- the single source of truth for balance projections.
   A mutation here invalidates every forward projection and every
   reconciliation in the app.
4. **Audit log** -- the `log_event()` records. Integrity of this table is
   the only technical defense against repudiation.
5. **Cloudflare Tunnel credentials** -- the `credentials-file` JSON
   referenced in `cloudflared/config.yml`. Possession of this file lets an
   attacker bind a *different* origin to the `shekel.saltyreformed.com`
   hostname and hijack traffic.
6. **Docker socket / host shell** -- `/var/run/docker.sock` plus anything
   else on the Arch host that grants daemon or root access. If an attacker
   owns this, every other asset is game over.

### The four attacker types

- **A (External unauthenticated).** Reaches Shekel over the Cloudflare Tunnel
  or the LAN nginx vhost. No valid credentials. Includes the internet at
  large and any hostile LAN peer.
- **B (Authenticated companion).** A logged-in user with the companion
  role. The companion feature is **active and in use** per developer
  confirmation. Sees what the owner shares; must not be able to mutate
  owner-only resources.
- **C (Compromised dependency).** A PyPI package in `requirements.txt`
  ships malicious code that executes inside the `shekel-prod-app`
  container during normal request handling. This is the threat model for
  supply-chain attacks like `ctx`, `colorama-wheels`, `ultralytics`,
  XZUtils, etc.
- **D (Compromised host).** An attacker has an interactive shell on the
  Arch Linux host as the `josh` user (or root). This is the post-exploit
  scenario after an unrelated exploit (e.g., a Jellyfin CVE chained to a
  container-escape, a compromised laptop restoring from backup, physical
  access).

### Cell format

Every cell answers three things in one line:

- **Result** -- Yes / Partial / No / N/A (with explanation)
- **Defense** -- citation of the specific defense (file:line or config) if
  one exists, else "(none)"
- **Residual** -- Critical / High / Medium / Low / None / N/A

For cells with residual risk **Medium or higher**, the table is followed
by a detailed note that expands the attack path, the defense's limits, and
recommended mitigation. Cells with residual None / Low / N/A are fully
covered by the one-liner and need no further prose.

### Severity interpretation (two contexts)

Shekel is currently **single-user, LAN-trusted, one-owner + one-or-more
companions, fronted by Cloudflare Tunnel with no Access policy**. Many
threats that are Low today become High if the app is opened to untrusted
public users or deployed outside the home LAN. Where this difference is
meaningful, the note states both: **Current deployment: X / Public
deployment: Y**.

### Cross-references

Where a cell's residual risk is influenced by an existing S1 or S2
finding, the note cites it. These are most often:

- S1 -- Flask-Limiter memory-backed (`app/extensions.py:31`)
- S1 -- HSTS absent
- S1 -- CSP contains `'unsafe-inline'` in `style-src` plus CDN allowlisting
- S1 -- `.env.dev` tracked (stale, not a live leak but still committed)
- S1 -- TOTP Fernet key rotation story unverified
- S3 -- WAN path bypasses both Nginx instances
- S3 -- Dev Postgres bound to LAN with committed credentials
- S3 -- Orphan containers and possibly-stale `shekel_pgdata` volume
- S3 -- Homelab network shared with jellyfin/immich/unifi/nginx

---

## 1. Asset 1 -- User Account

The user account encompasses the fields used to authenticate and maintain
session state: `users.email`, `users.password_hash` (bcrypt), `users.role_id`
(owner vs companion), `totp_secrets.*` (Fernet-encrypted secret + backup
codes), and the active Flask-Login session cookie. Compromise of any of
these leads to account takeover.

### 1.x STRIDE matrix (User Account)

| STRIDE | A: External | B: Companion | C: Compromised dep | D: Compromised host |
|--------|-------------|--------------|---------------------|---------------------|
| Spoofing | Partial -- brute-force login. Defense: bcrypt + TOTP + Flask-Limiter. Residual: **Medium**. | No -- companion cannot change role_id or alter other accounts. Defense: `require_owner` (auth_helpers.py:52). Residual: Low. | Yes -- in-process code can read session cookies / SECRET_KEY / Fernet key from memory. Defense: (none). Residual: **Critical**. | Yes -- host has SECRET_KEY in `/opt/docker/shekel/.env`. Defense: (none). Residual: **Critical**. |
| Tampering | No -- no password-reset route (preliminary finding 5 confirmed). Defense: absence of /forgot-password. Residual: None. | No -- no route lets a companion modify owner creds. Defense: `require_owner`. Residual: None. | Yes -- `UPDATE users SET password_hash = ...`. Defense: (none inside container). Residual: **Critical**. | Yes -- direct DB write via `docker exec`. Defense: (none). Residual: **Critical**. |
| Repudiation | No -- no authenticated actions to repudiate. Residual: N/A. | Partial -- can deny own actions if audit log is incomplete. Defense: `log_event()` captures auth events. Residual: Low. | Yes -- can delete log rows for own actions. Defense: (none -- logs in the same DB the dep has access to). Residual: **High**. | Yes -- same. Residual: **High**. |
| Information disclosure | Partial -- email enumeration at `/login` / `/register` / timing. Defense: unknown (not S3 scope). Residual: Low. | Partial -- may see owner's email/display name via shared UI surfaces. Defense: route-level scoping. Residual: Low. | Yes -- reads everything in memory. Defense: (none). Residual: **Critical**. | Yes -- reads .env and DB. Defense: (none). Residual: **Critical**. |
| Denial of service | Partial -- flood `/login` to burn bcrypt CPU. Defense: Flask-Limiter (memory-backed, per-worker). Residual: **Medium**. | Partial -- spam any authenticated endpoint (not rate-limited). Residual: **Medium**. | Yes -- crash workers, fork-bomb inside container. Defense: Gunicorn worker restart + `restart: unless-stopped`. Residual: **High**. | Yes -- stop the container or the daemon. Defense: (none). Residual: **Critical**. |
| Elevation of privilege | No -- no public admin endpoint. Registration creates companion-role users only (if REGISTRATION_ENABLED=true). Residual: None. | Partial -- companion -> owner requires `role_id` mutation which no route offers. Defense: `require_owner` + no /users/:id/role endpoint. Residual: Low. | Yes -- sets own `role_id = 1` via raw SQL. Defense: (none). Residual: **Critical**. | Yes -- as C, plus can change container env/command. Residual: **Critical**. |

### 1.x Detailed notes (User Account)

**A / Spoofing -- Medium.** The externally-reachable `/login` endpoint
ultimately calls bcrypt on the submitted password. Flask-Limiter limits
authentication attempts (see `app/extensions.py:31`), BUT its storage is
`memory://`, and under `GUNICORN_WORKERS=2` each worker keeps its own
counter. An attacker who load-balances across workers gets effectively
`limit x 2` attempts per window; on container restart counters reset to
zero. The rate limit is real but degraded. Combined with **no edge auth at
Cloudflare**, this is the single externally-facing brute-force surface for
the owner account. TOTP adds a second factor (compensating control), so
straight password brute-force without TOTP does not succeed; however the
attacker can brute-force the TOTP code (1,000,000 possibilities, 30-second
window) if they ever learn the password. **Mitigation:** move
Flask-Limiter to a shared storage backend (Redis) OR enforce
single-worker Gunicorn for limiter correctness; add fail2ban-equivalent
at cloudflared or nginx layer; consider CAPTCHA after N failed attempts
per IP. **Current deployment: Medium** (single owner, TOTP enabled).
**Public deployment: High** (each new user is another brute-force
surface).

**C / Spoofing -- Critical.** A malicious PyPI package running inside the
app container can read anything the Python process has access to:
`app.config['SECRET_KEY']` (signs session cookies),
`os.environ['TOTP_ENCRYPTION_KEY']` (Fernet key for decrypting
`totp_secrets.secret_ciphertext`), and the raw session cookie of any
user currently authenticated (via request-context hooks). With SECRET_KEY
the dep can forge cookies for any user, including the owner, without
knowing the password or TOTP code. Defense: **none** -- trusting a
dependency means giving it everything the app has. **Mitigation:** pin
all dependencies to exact versions in `requirements.txt` (already done),
verify hashes via `--require-hashes` (not yet done per S2
`pip-audit` findings), SBOM review on every bump, and consider
`cyclonedx-py` output as a signal for unexpected adds. Compensating
control: make token/cookie compromise less useful by rotating SECRET_KEY
and TOTP_ENCRYPTION_KEY periodically -- though this invalidates existing
sessions and TOTP enrollments, which is why S1 preliminary finding 6
(TOTP rotation story unverified) matters.

**D / Spoofing -- Critical.** Host compromise trivially reads
`/opt/docker/shekel/.env`, which contains SECRET_KEY and
TOTP_ENCRYPTION_KEY in plaintext. There is no at-rest protection for
these secrets beyond file permissions (`.env` was `rw-r--r--` in
`ls -la /opt/docker/shekel/` output -- world-readable). Defense: host
hardening (Session S2 Lynis pass). Mitigation: tighten `.env`
permissions to `0600 josh:josh`; consider secrets management
(systemd credentials, Docker secrets, or a vault) for production.

**C / Tampering -- Critical.** `UPDATE users SET password_hash = '...'
WHERE email = 'owner@example.com'` from inside the Python process, using
the DATABASE_URL environment variable. The app container has
DB_PASSWORD in its env; no separate role is in use for the app's DB
connection -- it is the full `shekel_user` superuser for the `shekel`
database. Mitigation: principle of least privilege at the DB layer --
the app should use a Postgres role with SELECT/INSERT/UPDATE/DELETE on
the business tables but no SUPERUSER / no DDL. Shekel's migrations
already run under a separate context (entrypoint.sh runs psql under
`shekel_user`), so this split is plausible; requires dedicated app-role
design.

**D / Tampering -- Critical.** `docker exec shekel-prod-db psql -U
shekel_user -d shekel -c "UPDATE ..."`. Trivial from a shell on the
host. Mitigation: same as C; also restrict host user membership in the
`docker` group (only one admin).

**C / Repudiation -- High.** `log_event()` writes to the same database.
A compromised dep can `DELETE FROM audit_log WHERE user_id = <owner>` or
selectively `UPDATE` records to shift blame. There is no out-of-band log
shipping in Shekel. Mitigation: ship `log_event()` output to a
write-only destination (Loki, syslog, S3 with object-lock) so that
attacker writes in the local DB do not erase audit trail. **High**
because the attack path is direct but the impact is "invisibility of
other attacks," which is contingent on the attacker also doing
something to cover up.

**D / Repudiation -- High.** Same as C plus ability to `docker rm`
containers, taking the logs with them if not on a persistent volume.
`applogs` volume is persistent (`docker-compose.yml:78`), but a host
attacker can delete the volume too. Mitigation: off-host log
shipping.

**A / Information disclosure -- Low (recorded but not detailed).**
Email enumeration is a generic weakness; mitigation is standard (uniform
response for "invalid credentials" regardless of whether the email
exists). Out of S3 scope, flagged for Section 1C.1 manual review
(already done in S1) and Section 1M (DAST, S4) for verification. Kept at
Low because Shekel is single-owner -- email is effectively a public
identifier -- but becomes Medium if registration scales up.

**B / Information disclosure -- Low.** Companion sees owner's display
name and possibly email via shared UI elements (e.g., a "Shared budget
with Owner@X" header). This is by design; mitigation is to check that
auth fields like `password_hash`, `totp_secret`, `role_id` are never
included in ORM queries consumed by companion-accessible views
(S1 findings cover this for the `/users` endpoint; verification for
other template contexts is S7 scope).

**C / Information disclosure -- Critical.** A dep can read the live
decrypted TOTP secret during an MFA verification call (because the
Fernet decrypt happens in-process and the plaintext is in scope of the
dep's code). This means even if the attacker does not yet have SECRET_KEY,
watching MFA verifications reveals TOTP secrets for every user who logs
in. Mitigation: this is the nature of in-process secrets; the
compensating control is preventing the dep compromise in the first
place.

**D / Information disclosure -- Critical.** Host can read everything:
env, DB, volume data. Same mitigation as D/Spoofing.

**A / DoS -- Medium.** Authenticated routes accept form data; any
handler doing substantial work (recurrence regen, tax calculation, scenario
fan-out) is a candidate for a resource exhaustion probe. However,
unauthenticated attackers can only hit `/login`, `/register`, `/health`,
and static assets before getting rejected. `/login` burns bcrypt CPU per
attempt -- default bcrypt cost (10-12 rounds) is intentionally slow. A
well-tuned attack flooding `/login` from many IPs can push 2 Gunicorn
workers into constant bcrypt computation, denying service to the
legitimate owner. Rate limiting is Flask-Limiter (same memory-backend
caveat). Mitigation: lower bcrypt rounds? No -- keeps attack cost high
for brute-force. Better: reduce Flask-Limiter key
to user+IP, add shared-storage backend, optionally add cloudflared/nginx
layer rate limiter. Gunicorn `timeout = 120` means a slow-bcrypt attack
doesn't hang workers forever, which bounds the blast.

**B / DoS -- Medium.** Authenticated endpoints are largely NOT
rate-limited (Flask-Limiter is only on auth paths). A logged-in
companion can loop POST requests to any route, exhausting the 2 workers.
With Gunicorn's `timeout = 120`, a single slow request ties up one
worker for up to 2 minutes. A companion gone rogue can make the app
unusable. Mitigation: add a second Flask-Limiter tier for
authenticated endpoints (say, 60 requests per minute per user) so that
even an insider cannot DoS trivially. Not a supply-chain bug, just a
hardening gap.

**C / DoS -- High.** A dep can `while True: pass`, `os.fork()`,
allocate memory until the container OOMs, or call `sys.exit(0)` inside
a request handler. Defense: Gunicorn restarts workers on death;
`restart: unless-stopped` restarts the whole container after fatal
crashes. This provides partial availability but the attacker keeps
winning. Mitigation: dep security via hash-pinning, SBOM diffs on bumps,
read-only rootfs where possible (shekel-prod-app currently does not use
`read_only: true`).

**D / DoS -- Critical.** `docker compose stop` or just `kill -9` the
gunicorn master process. Availability drops to 0 until the attacker lets
it come back.

**B / EoP -- Low.** Shekel's role boundary is enforced by the
`require_owner` decorator (preliminary finding 2, resolved in S1:
`auth_helpers.py:52` with `role_id` fallback verified safe). Companion
elevation requires one of: (a) a route bug that lets a companion write
to `users.role_id`, (b) a migration that changes role semantics, (c)
compromising the DB directly (which falls under C/D). No such route
exists in the current codebase per S2. **Current deployment: Low.**
**Public deployment: unchanged Low** (the boundary is purely
route-based, not network-based).

---

## 2. Asset 2 -- Financial Data

Transactions, balances, transfers (with shadow-transaction invariants),
paychecks, debt accounts, templates, and scenarios. This is what the app
exists to manage, and what "money mismanaged" from CLAUDE.md refers to.

### 2.x STRIDE matrix (Financial Data)

| STRIDE | A: External | B: Companion | C: Compromised dep | D: Compromised host |
|--------|-------------|--------------|---------------------|---------------------|
| Spoofing | No -- login required for all state-changing routes. Defense: `login_required` on every blueprint. Residual: None. | Partial -- can create own data as owner-visible; cannot impersonate owner identity. Defense: user_id scoping. Residual: Low. | Yes -- can INSERT with any user_id. Defense: (none). Residual: **Critical**. | Yes -- same. Residual: **Critical**. |
| Tampering | No -- IDOR probe (S4) verifies 404 for cross-user access. Defense: `auth_helpers.py` ownership pattern. Residual: Low (until S4 confirms). | **UNKNOWN** -- companion write scope is role-specific; require S7 ASVS V4 mapping. Residual: **Medium** pending verification. | Yes -- full DB write. Residual: **Critical**. | Yes -- same. Residual: **Critical**. |
| Repudiation | N/A -- no authenticated actions to repudiate. Residual: N/A. | Partial -- can deny edits to shared data. Defense: `log_event()` captures financial mutations. Residual: Low. | Yes -- can delete audit rows for own writes. Residual: **High**. | Yes -- same. Residual: **High**. |
| Information disclosure | Partial -- IDOR risk pending S4 DAST. Defense: ownership pattern + "404 for not yours" rule. Residual: Low (pending S4). | Yes -- by design, companion sees shared financial data. Defense: that IS the design. Residual: None. | Yes -- full read. Residual: **Critical**. | Yes -- full read. Residual: **Critical**. |
| Denial of service | No direct path without auth. Residual: None. | Partial -- can spam creates / expensive recompute endpoints. Residual: **Medium**. | Yes -- can DELETE all financial rows. Residual: **Critical** (data destruction). | Yes -- same + container/volume destruction. Residual: **Critical**. |
| Elevation of privilege | No -- financial data itself does not carry privilege. Residual: N/A. | No -- no scope to cross financial data types in a privilege-granting way. Residual: None. | Yes -- tamper with own role via DB. Residual: **Critical** (covered under Asset 1 / EoP). | Yes -- same. Residual: **Critical**. |

### 2.x Detailed notes (Financial Data)

**B / Tampering -- Medium (pending verification).** The companion role
is active and in use, per developer. The exact write scope of the
companion role is not enumerated in this session -- S2 identity report
(`01-identity.md`) and the S7 ASVS V4 access-control mapping are
authoritative. This cell is Medium because:
- If companion can write shared transactions AND the owner does not
  immediately review those writes, the companion can inject expenses
  that silently distort the owner's projections (anchor balance driven).
  This is a business-logic integrity concern, not a technical auth bypass.
- If companion can write their own data but not owner data, the
  threat is bounded. Defense is route-scoped `require_owner` on
  owner-only actions.
- Required verification: read each blueprint's decorator stack for
  every POST/PUT/DELETE and confirm the companion path is either
  explicit-allow (with justification in 01-identity.md) or
  `require_owner`. S4 DAST will probe this with a companion user.

**C / Tampering and D / Tampering -- Critical.** Same pattern as Asset
1 C/D. Mitigation: least-privilege DB role for the app (covered under
Asset 1), plus row-level CHECK constraints in Postgres for sanity
(e.g., `CHECK (amount > 0)` where applicable, referential integrity on
`user_id`). Session S6 / 1N migration audit verifies schema-level
integrity.

**C / Repudiation and D / Repudiation -- High.** Audit trail
integrity hinges on the `audit_log` table being outside the attacker's
write scope. A compromised dep / host has full write. Off-host
log shipping is the compensating control. **High, not Critical,**
because the attacker must also take another action to *benefit*
from erased logs -- the erasure alone does not damage finances.

**B / DoS -- Medium.** As with Asset 1 B/DoS -- no rate-limit on
authenticated endpoints. A logged-in companion repeatedly hitting
`/grid` or `/scenarios/<id>/regenerate` can keep Gunicorn workers busy.
Mitigation: second-tier Flask-Limiter on authenticated routes; shared
storage for the limiter.

**C / DoS -- Critical.** A dep can `TRUNCATE transactions, transfers,
paychecks` inside the app's DB session. Shekel's backup strategy
(`/opt/docker/backup.sh` per the `/opt/docker` listing from earlier
tool output, and `docker-backup.service` / `docker-backup.timer`) is
the only recovery path. Mitigation: verify backup freshness and
restorability in Session S6 or as a post-audit task; ensure backups
are off-host (not just on the same Arch box).

**D / DoS -- Critical.** Host can `docker volume rm
shekel-prod-pgdata`. Although the volume is declared `external: true`
specifically to survive `docker compose down -v`, a deliberate host
attacker is not bounded by that.

**C / Info disclosure and D / Info disclosure -- Critical.** Full read
access to all financial data, including companion-scoped subsets. No
encryption at rest for the DB volume (confirmed -- `shekel-prod-pgdata`
is a plain postgres volume). Mitigation: at-rest encryption on the host
filesystem (LUKS / eCryptfs on /var/lib/docker) is out of scope for the
app but documented as hardening.

---

## 3. Asset 3 -- Anchor Balance

The anchor balance is the single real-world checking-account number that
Shekel uses as the origin of every forward balance projection. Every
transaction AFTER the anchor date contributes to a period-by-period
projection. If the anchor is off by $X, every future period is off by $X
(or a multiple thereof after rollover effects). The anchor is a single row
(or a small handful) in a small table -- easy to mutate, easy to miss.

### 3.x STRIDE matrix (Anchor Balance)

| STRIDE | A: External | B: Companion | C: Compromised dep | D: Compromised host |
|--------|-------------|--------------|---------------------|---------------------|
| Spoofing | No -- anchor setting requires auth + owner role. Residual: None. | No -- anchor is owner-only per app design. Defense: `require_owner` on anchor routes. Residual: None. | Yes -- direct DB UPDATE. Residual: **Critical**. | Yes -- same. Residual: **Critical**. |
| Tampering | No -- route-protected. Residual: None. | No -- `require_owner`. Residual: None. | Yes -- single `UPDATE` invalidates all projections. Residual: **Critical**. | Yes -- same. Residual: **Critical**. |
| Repudiation | N/A. Residual: N/A. | Partial -- denial of setting anchor. Defense: `log_event()` captures anchor changes (verify). Residual: Low. | Yes -- delete log rows. Residual: **High**. | Yes -- same. Residual: **High**. |
| Information disclosure | No -- requires auth to view. Residual: None. | Partial -- companion may be able to view anchor (depends on role; needs verification). Residual: Low. | Yes -- full read. Residual: **Critical**. | Yes -- same. Residual: **Critical**. |
| Denial of service | No path. Residual: None. | Partial -- spamming anchor re-reads is expensive. Low. | Yes -- `UPDATE anchor_balances SET amount = -9999999` invalidates every projection until corrected. Residual: **High** (availability via correctness). | Yes -- same. Residual: **High**. |
| Elevation of privilege | N/A. | N/A. | N/A (anchor does not confer privilege). | N/A. |

### 3.x Detailed notes (Anchor Balance)

**Why the anchor is a distinct asset from general financial data.** A
silent $1 shift per day is below the threshold where any single
observation looks wrong. Compounded over 365 days, that is $365 drift
between Shekel's projected balance and reality; if the owner trusts the
projection and spends to it, a real overdraft results. An attacker
optimizing for undetected harm would attack the anchor over any other
field -- large transactions show up in history and are obvious,
paychecks are verifiable, but the anchor is a scalar the owner sets
once and rarely revisits.

**C / Tampering and D / Tampering -- Critical.** The attack path is
`UPDATE anchor_balances SET amount = amount + epsilon WHERE user_id =
<owner>`. Defense at the code level is route-based (`require_owner`),
but the attacker is below the code layer. **Mitigation:**
- **Immutable audit of anchor changes.** Every anchor UPDATE should be
  captured in `audit_log` with before/after values. If the app
  controls this, the attacker at C/D can still wipe the log, but at
  least a legitimate operator review can catch drift.
- **Daily reconciliation.** Shekel could cross-check anchor against
  the latest "settled" balance inferred from the last paycheck cycle
  and warn if drift exceeds a threshold.
- **Signed reconciliation tokens.** Overkill for a personal app; note
  here for completeness.
- **Off-host audit log shipping.** Same recommendation as Asset 1 -- if
  anchor changes are mirrored to a write-only external destination,
  tampering becomes visible.

**C / DoS -- High** (not Critical because the app still runs; it's the
*correctness* that is denied). The same UPDATE above with a
preposterous value (`amount = 0`, `amount = -1e6`) turns every balance
projection negative and every alert red until the owner re-anchors.
Not destructive in the sense of data loss, but effectively DoS on
trust in the app. Mitigation: Postgres CHECK constraint
`CHECK (amount >= -99999.99 AND amount <= 99999999.99)` (adjust
bounds to actual sanity); app-level warning if anchor change exceeds
X% in one edit.

**D / DoS -- High.** Same as C.

---

## 4. Asset 4 -- Audit Log

The `audit_log` (or wherever `log_event()` writes) is the only technical
defense Shekel has against repudiation. Integrity of this log matters
because every other asset's repudiation mitigation points back to it.

### 4.x STRIDE matrix (Audit Log)

| STRIDE | A: External | B: Companion | C: Compromised dep | D: Compromised host |
|--------|-------------|--------------|---------------------|---------------------|
| Spoofing | No -- external cannot write arbitrary audit events (no public endpoint). Residual: None. | No -- companion actions create audit rows in companion's own name, not owner's. Defense: `log_event()` hardcodes `user_id = current_user.id`. Residual: Low. | Yes -- can write fake audit events attributing actions to any user. Defense: (none). Residual: **Critical**. | Yes -- same. Residual: **Critical**. |
| Tampering | No -- no write route. Residual: None. | No -- no write route for audit log. Residual: None. | Yes -- `UPDATE audit_log` / `DELETE FROM audit_log`. Defense: (none; same DB). Residual: **Critical**. | Yes -- same plus `pg_dump` / restore with edits. Residual: **Critical**. |
| Repudiation | N/A -- no actions by A. | Partial -- companion denying actions is the meta-threat audit log defends against. Defense: log entries. Residual: Low. | Yes -- deletes entries for own actions. Defense: (none). Residual: **Critical** (this is the canonical repudiation scenario). | Yes -- same. Residual: **Critical**. |
| Information disclosure | No -- log not publicly readable. Residual: None. | Partial -- companion sees own audit rows; may see owner rows if UI leaks. Verify in S7. Residual: Low. | Yes -- full read. Residual: **High** (log contains request metadata including IPs, user_agents, possibly URL paths with IDs). | Yes -- same. Residual: **High**. |
| Denial of service | No direct path without auth. Residual: None. | Partial -- flood action endpoints to pad log, consume disk. Residual: Low-Medium. | Yes -- `TRUNCATE audit_log` clears history, or `INSERT`-spam fills disk. Residual: **High**. | Yes -- same. Residual: **High**. |
| Elevation of privilege | N/A. | N/A. | Yes -- by manipulating log state to remove traces of EoP actions elsewhere. Residual: **High** (enabler, not direct). | Yes -- same. Residual: **High**. |

### 4.x Detailed notes (Audit Log)

**C / Spoofing -- Critical.** An attacker can write audit rows that
make the owner appear to have performed actions the attacker performed,
muddying any forensic review. Defense: (none). Mitigation: off-host
log shipping (so a secondary source shows the truth); cryptographic
hash-chaining of log rows (each row commits to hash of previous row,
tampering breaks the chain). The latter is overkill for a personal app;
off-host shipping to syslog/Loki/S3 is the pragmatic defense.

**C / Tampering, D / Tampering -- Critical.** Direct DB write to
`audit_log` table. Mitigation as above.

**C / Repudiation, D / Repudiation -- Critical.** This is literally the
threat the audit log defends against; if the log is under the attacker's
control, the defense is void. **This is the single most important
cross-asset concern** -- every "Residual: High" on a Repudiation cell
elsewhere in this document falls back to this, because the other cells
assumed some audit exists to fall back on. **Mitigation (highest
priority architectural change):** ship `log_event()` output to an
append-only destination with no local mutation capability from the app
or host side. Simplest implementation: systemd-journal or rsyslog
forwarding to a remote collector with retention. Still recognized that
a determined host attacker can compromise the remote collector too; the
goal is to raise the bar from "one DELETE" to "two separate
compromises."

**C / Info disclosure, D / Info disclosure -- High.** The audit log
contains request metadata. If IPs, user_agents, or URL paths with IDs
are logged, this data is disclosed. Session S2's `08-runtime.md` should
confirm what fields `log_event()` writes; if it includes user email or
full-URL-with-query-string, consider reducing.

**C / DoS, D / DoS -- High.** Disk-fill attack via log spam. Shekel's
container-log-limits (10MB x 3 files per container via the compose
`logging:` blocks seen in `/opt/docker/docker-compose.yml`) bound this
for container stdout, but `audit_log` is a DB table and its growth
bound is whatever the Postgres volume can hold. Defense: monitoring
disk usage (out of scope); log rotation / pruning policy
(`AUDIT_RETENTION_DAYS: ${AUDIT_RETENTION_DAYS:-365}` in
docker-compose.yml:76 confirms a retention setting exists -- verify
the enforcement mechanism in Session S2 / 07-manual-deep-dives.md).

**C / EoP, D / EoP -- High.** An audit-log tampering path is an
enabler for stealthy EoP: the attacker elevates, performs actions,
then scrubs the log. Residual is High rather than Critical because
EoP itself is measured under Asset 1 -- this cell captures the
enabling capability.

---

## 5. Asset 5 -- Cloudflare Tunnel Credentials

The tunnel credentials file (`/opt/docker/cloudflared/<TUNNEL_ID>.json`
typical, volume-mounted read-only into the cloudflared container via
`/opt/docker/cloudflared:/etc/cloudflared:ro`). Possession of these
credentials lets an attacker register an alternate origin for the
tunnel, effectively taking over the `shekel.saltyreformed.com`
hostname. Recovery requires revoking and re-issuing the tunnel.

### 5.x STRIDE matrix (Cloudflare Tunnel Credentials)

| STRIDE | A: External | B: Companion | C: Compromised dep | D: Compromised host |
|--------|-------------|--------------|---------------------|---------------------|
| Spoofing | No -- credentials file is not network-reachable. Residual: None. | No -- companion has no host access. Residual: None. | No -- `/opt/docker/cloudflared` is NOT mounted into `shekel-prod-app`. Defense: compose volume set. Residual: None. | Yes -- read + exfiltrate. Residual: **Critical**. |
| Tampering | No. Residual: None. | No. Residual: None. | No -- not mounted. Residual: None. | Yes -- overwrite creds, replace tunnel. Residual: **Critical**. |
| Repudiation | N/A. | N/A. | N/A. | Partial -- host can edit cloudflared logs too. Residual: High. |
| Information disclosure | No -- not network-reachable. Residual: None. | No. Residual: None. | **Partial** -- dep in app can reach cloudflared's `--metrics 0.0.0.0:2000` endpoint over homelab network, gleaning operational data (not credentials). Residual: Low. | Yes -- full read. Residual: **Critical**. |
| Denial of service | No direct path (would need to reach the cloudflared container or tunnel endpoint). Residual: None. | No. Residual: None. | Partial -- dep can send malformed data through the tunnel as an in-process action, but this DoSes Shekel not cloudflared. Residual: Low. | Yes -- `docker stop cloudflared` or credentials revocation. Residual: **Critical**. |
| Elevation of privilege | N/A. | N/A. | N/A. | N/A (tunnel creds are a pivot to hosting spoof, not in-host EoP). |

### 5.x Detailed notes (Cloudflare Tunnel Credentials)

**D / Spoofing -- Critical.** Host attacker copies the credentials
file, runs `cloudflared tunnel run <TUNNEL_ID>` on an attacker-owned
machine, and the Cloudflare edge routes `shekel.saltyreformed.com`
traffic to the attacker's origin. The attacker now serves a fake
login page, phishes the owner's credentials, and optionally proxies
real traffic to the legitimate Shekel to avoid detection. Defense:
**none** at the credential layer. Mitigations:
- Cloudflare's `cloudflared tunnel token revoke <TUNNEL_ID>` after
  any suspected host compromise, as part of incident response.
- Short-lived tunnel tokens (if Cloudflare supports them for your
  account tier).
- File permissions `0600 josh:josh` on the creds file (currently
  `josh josh` ownership per `/opt/docker/cloudflared` listing --
  verify with `ls -la /opt/docker/cloudflared/*.json`).
- **Secondary compensating control:** Cloudflare Access policy with
  email-domain or IdP enforcement. If set, even a spoofed origin
  cannot serve users who have not satisfied the access gate. S3 found
  no Access policy currently -- this is a key recommendation.

**D / Tampering -- Critical.** Host can replace the creds with a
different tunnel's creds, breaking the deployment and hijacking the
DNS.

**D / DoS -- Critical.** `cloudflared tunnel delete` (requires
auth) or simpler, just `docker stop cloudflared`. The WAN path is
down until the host attacker releases the container -- this is the
same DoS risk as any container-stop by host; worth recording.

**C / Information disclosure -- Low.** The cloudflared `--metrics`
endpoint on `0.0.0.0:2000` is reachable from any container on the
homelab network, including `shekel-prod-app`. Metrics include
request counts, tunnel health, last-connection time. Not credentials,
not sensitive financial data. Mitigation: tighten
`--metrics 127.0.0.1:2000` inside the cloudflared container. S3
finding records this.

---

## 6. Asset 6 -- Docker Socket / Host Shell

Interactive shell as `josh` on the Arch Linux host, or root, or
`docker` group membership (equivalent to root for most purposes).

### 6.x STRIDE matrix (Docker Socket / Host Shell)

| STRIDE | A: External | B: Companion | C: Compromised dep | D: Compromised host |
|--------|-------------|--------------|---------------------|---------------------|
| Spoofing | Partial -- SSH brute-force. Defense: UNKNOWN (sshd_config not verified in S3). Residual: **Medium** pending verification. | No -- companion has no SSH access. Residual: None. | No -- app container does not mount the docker socket. Defense: `docker-compose.yml` volumes list has no `/var/run/docker.sock`. Residual: None. | Yes -- definitionally. Residual: **Critical**. |
| Tampering | No direct path -- SSH requires successful auth first. Residual: None. | No. Residual: None. | No -- not reachable. Residual: None. | Yes -- definitionally. Residual: **Critical**. |
| Repudiation | N/A (no actions by A without a shell). | N/A. | N/A. | Partial -- host can edit system logs, docker logs. Residual: **High**. |
| Information disclosure | No -- no path without a shell. Residual: None. | No. Residual: None. | No -- not reachable. Residual: None. | Yes -- reads everything. Residual: **Critical**. |
| Denial of service | No -- no path. Residual: None. | No. Residual: None. | No. Residual: None. | Yes -- `shutdown`, `kill -9 dockerd`, `rm -rf`. Residual: **Critical**. |
| Elevation of privilege | Partial -- SSH then sudo, if configured. Defense: UNKNOWN. Residual: **Medium** pending verification. | No. Residual: None. | No -- no path to escape the container without an unrelated kernel or docker bug. Defense: no --privileged, USER shekel in Dockerfile (line 47), no CAPs added, no docker.sock mount. Residual: Low. | Yes -- already has it. Residual: **Critical**. |

### 6.x Detailed notes (Docker Socket / Host Shell)

**A / Spoofing -- Medium pending verification.** SSH listens on
`0.0.0.0:22` (confirmed by `ss` output). If password auth is allowed
AND fail2ban is NOT installed AND no explicit `AllowUsers josh`
restriction is in place, brute-force is feasible. S3 did not enumerate
sshd_config; cross-reference against Lynis output from Session S2
(`scans/lynis.log`) to settle this. The residual is Medium, **not
High or Low**, precisely because the answer is unknown -- an unknown
auth gate is the worst case for an auditor. **Mitigation:** pubkey-only
(`PasswordAuthentication no`), `PermitRootLogin no`, `AllowUsers josh`,
fail2ban or sshguard with 10min ban after 3 failures. Post-audit
task for Section 2 (remediation plan).

**A / EoP -- Medium pending verification.** If SSH is solved (see
A/Spoofing) and `josh` is in the `docker` group (highly likely per
the group ownership observed on `/opt/docker/nginx/`), then the
moment the attacker has SSH they effectively have root via `docker
run --privileged --pid=host ...` or a similar container-escape via
docker socket. Mitigation: consider removing `josh` from `docker`
group for day-to-day usage and using `sudo docker ...` with a strict
sudoers rule (only specific commands); or use rootless Docker
(significant architectural change, noted for future).

**C / EoP -- Low.** Escape from a Docker container requires either:
(a) a kernel CVE exploit, (b) a docker / runc CVE, (c) a mounted
docker.sock or /proc/1/root or similar, or (d) `--privileged`
(none of which apply to shekel-prod-app per `docker inspect`). The
container runs as non-root `shekel` user (Dockerfile:31,47), with
default seccomp, no `--cap-add`, and no dangerous bind mounts. Low
is correct; Critical would require a specific CVE. Mitigation as
S2 hardening recommendations (`read_only: true`, `no-new-privileges`,
custom seccomp profile).

**D / Repudiation -- High.** Host attacker can modify `journalctl`
history, `/var/log`, `/var/lib/docker/containers/*/*.log`,
`audit_log` DB rows (via docker exec psql). The only defense is
off-host log shipping. Same mitigation as Asset 4.

---

## 7. Summary

### 7.1 Cell counts by residual risk

Counts are across all 144 cells:

| Residual risk | Count |
|---------------|-------|
| **Critical** | 40 |
| **High** | 16 |
| **Medium** | 7 |
| **Low** | ~24 (individually noted but not detailed) |
| **None** | ~42 |
| **N/A** | ~15 |

(Approximate where cells combine Low and None in one-liners; the
residual counts are taken from the cell tags above. The S8
consolidator should re-tally from the source tables when importing
into `findings.md`.)

### 7.2 Top three threats overall (ranked by blast radius x likelihood)

**Threat T-1 -- Compromised dependency (attacker type C) inside the
Shekel app container.** This single attacker type produces Critical
residual in 12+ cells across Assets 1, 2, 3, and 4. The attack path
is trivial (one unreviewed package bump bringing hostile code); the
blast radius is every piece of user data plus audit integrity.
Remediation path: `--require-hashes` in pip, SBOM diff review on
every dependency bump (Session S2's supply-chain report covers tooling),
post-deployment canary to catch weird behavior, and off-host
log shipping so even a dep that tries to cover its tracks cannot.
Same threat retains Critical even if Shekel stays strictly private
-- public vs private deployment does not change how much a hostile
dep can read.

**Threat T-2 -- Anchor-balance silent tampering.** Unique to this
app. A tiny, undetected shift in the anchor cascades through every
forward projection. The attack path is trivially small (one
UPDATE), the defense is purely route-based (defeated by C/D), and
the detection bar is "owner notices the math is slightly off over
several months." Remediation: CHECK constraints at the DB level,
audit-log entry on every anchor change, periodic reconciliation
alerts in the UI, off-host log shipping to prove a change was made
if the owner ever suspects drift.

**Threat T-3 -- Audit log integrity as a cross-asset keystone.**
Every Repudiation cell above with residual High/Critical points
here. Repudiation attacks work in two steps: perform the action,
then erase the evidence. Shekel's audit log lives in the same DB
as the data it describes, so any C/D compromise defeats both
steps at once. Remediation: off-host log shipping (syslog/Loki/S3
with object-lock). Optional harder defense: hash-chained rows.
Same remediation fixes ~8 High residuals at once.

### 7.3 Threats that change the most between private and public deployment

- **A / Spoofing on User Account.** Currently Medium (single owner,
  Flask-Limiter + TOTP strong enough for one brute-force target).
  Public: High (every new user is another target; Flask-Limiter's
  memory-backend becomes a more consequential rate-limit bypass).
- **A / DoS on User Account.** Same -- memory-backed rate limiting
  at `limit x worker_count` does not scale to N attackers
  hitting N accounts.
- **A / Information disclosure on User Account (email enumeration).**
  Currently Low (one known email). Public: Medium to High (every
  public user is a potential enumeration target).
- **LAN-only threats.** The dev Postgres bindings and the host
  sshd are only LAN-reachable by design. If Shekel is moved to a
  VPS or shared host, these LAN-only assumptions collapse and
  several Medium cells become High/Critical.

### 7.4 Threats with ZERO existing defense

These are the findings where the current code/config has no
protection, only convention or absence of attacker. Each becomes
High-or-higher the moment the attacker profile shifts:

1. **Off-host audit log shipping -- not present.** Every Repudiation
   cell above depends on audit logs being tamper-resistant; they
   are not.
2. **Cloudflare Access policy -- not present.** Every External A
   cell relies solely on Flask's own auth as the ONLY gate between
   the public internet and Shekel. For a purely-private
   deployment this is defensible; for any broader deployment it
   is the missing front door.
3. **Least-privilege DB role for the app -- not present.** The app
   connects as `shekel_user` which owns the `shekel` database. A
   compromised dep or host can issue DDL (`DROP TABLE`) freely.
4. **Anchor-balance CHECK constraint at DB level -- not present**
   (verify in Session S6 / 1N migration audit). App-level validation
   is bypassable by C/D.
5. **SSH hardening verification -- not done in S3.** See Asset 6
   notes; cross-reference with Lynis.

### 7.5 Cross-references to S1 / S2 findings (re-rating suggestions)

- **S1 preliminary finding 4 (Flask-Limiter memory backend) --
  currently Medium.** Supports Asset 1 A/Spoofing Medium and A/DoS
  Medium. Do NOT upgrade on S3 alone, but note: if public
  deployment is ever contemplated, this rises with Asset 1's
  A/Spoofing. Hold at Medium for now.
- **S1 preliminary finding 3 (HSTS absent, CSP unsafe-inline) --
  currently Medium.** HSTS absence relates to LAN MitM risk on
  Asset 1 Spoofing when a LAN device attempts a downgrade
  attack. CSP unsafe-inline relates to XSS exposure of session
  cookies (Asset 1 / Info disclosure). Hold at Medium.
- **S1 preliminary finding 6 (TOTP key rotation story
  unverified) --** upgrade this from Unverified to an explicit
  finding in S8. Rotation matters specifically because TOTP secret
  ciphertext is tied to the current `TOTP_ENCRYPTION_KEY`. If the
  key is leaked (Asset 1 / D/Info disclosure), rotation is the
  remediation, and if rotation is unsupported the remediation
  collapses to "force re-enrollment of every user's TOTP" which is
  a destructive operation. Recommend: **re-rate this finding to
  Medium once verified; if rotation is unsupported, High.**
- **S3 finding on dev Postgres host bindings --** already High in
  S3. This re-affirms: Attacker type A (external -- reachable from
  any LAN device with the public repo credentials) on financial
  data (dev instance) is effectively Critical, bounded only by
  the LAN trust model.
- **S3 finding on WAN bypassing Nginx --** reinforces several
  User Account / A cells. Nginx's `client_max_body_size 5M` and
  timeouts are inactive for WAN traffic. Memory/CPU exhaustion
  attacks via oversized payloads are bounded only by Gunicorn's
  `limit_request_line = 8190` and the 120s timeout.

### 7.6 The three biggest architectural changes implied by this threat model

If the developer does nothing else from this audit, these three are the
highest-leverage hardening changes:

1. **Off-host audit log shipping.** Closes ~8 Repudiation High/Critical
   cells at once. Simplest implementation: rsyslog forwarding to a
   remote collector with retention.
2. **Separate the Shekel WAN proxy path onto its own Docker network.**
   Cloudflared joins a dedicated `shekel-proxy` network plus the
   `homelab` network; `shekel-prod-app` only sits on
   `shekel-proxy` plus `shekel-prod_backend`. This removes
   jellyfin/immich/unifi from Shekel's lateral blast radius and
   turns ~4 Medium cells into Low.
3. **Add a Cloudflare Access policy.** Even a simple email-allowlist
   Access policy moves Attacker A from "unauthenticated public
   internet" to "authenticated-at-edge" for all WAN requests.
   Converts several Low cells (currently Low only because of LAN
   trust) into true Low-by-design regardless of deployment model.

---

End of 14-threat-model.md.
