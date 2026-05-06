# Shekel Operations Runbook

This is the single operational reference for the Shekel budget application. It covers deployment, backup and restore, security operations, monitoring, Cloudflare management, and troubleshooting. For detailed backup procedures, see also `docs/backup_runbook.md`. For secret management details, see also `docs/runbook_secrets.md`.

---

## Table of Contents

1. [Quick Reference](#1-quick-reference)
2. [Deployment](#2-deployment)
3. [Backup & Restore](#3-backup--restore)
4. [Security Operations](#4-security-operations)
5. [Monitoring & Observability](#5-monitoring--observability)
6. [Cloudflare Management](#6-cloudflare-management)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Quick Reference

### Service Architecture

```
[Client Browser]
       |
       v
[Cloudflare Edge]          -- TLS termination, Access policy, WAF rate limiting
       |
       v
[cloudflared]              -- Proxmox host, systemd service, encrypted tunnel
       |
       v
[Nginx :80]                -- Docker container, reverse proxy, static files
       |
       v
[Gunicorn :8000]           -- Docker container, WSGI server, 2 workers
       |
       v
[Flask Application]        -- Shekel budget app, structured JSON logging
       |
       +------> [Redis :6379]     -- Docker container, Flask-Limiter counters
       |                              (no persistence; counters evaporate on
       |                              restart by design)
       v
[PostgreSQL :5432]         -- Docker container, multi-schema, audit triggers
```

### Key Paths on the Proxmox Host

| Path | Purpose |
|------|---------|
| `/opt/shekel/` | Application directory (git repository clone) |
| `/opt/shekel/.env` | Environment configuration (secrets, settings) |
| `/opt/shekel/docker-compose.yml` | Production Docker Compose file |
| `/etc/cloudflared/config.yml` | Cloudflare Tunnel configuration |
| `/root/.cloudflared/` | Tunnel credentials (cert.pem, tunnel JSON) |
| `/var/backups/shekel/` | Local backup storage |
| `/mnt/nas/backups/shekel/` | NAS backup storage (off-site copy) |
| `/var/log/shekel_backup.log` | Backup and maintenance cron log |

### Container Inventory

| Container | Image | Network(s) | Health Check |
|-----------|-------|------------|--------------|
| `shekel-prod-db` | `postgres:16-alpine` | backend | `pg_isready` every 10s |
| `shekel-prod-redis` | `redis:7.4-alpine` | backend | `redis-cli ping` every 10s |
| `shekel-prod-app` | Built from Dockerfile | backend, monitoring | `GET /health` every 30s |
| `shekel-prod-nginx` | `nginx:1.27-alpine` | frontend, backend | `wget /health` every 30s |

### Script Inventory

| Script | Purpose | Usage |
|--------|---------|-------|
| `scripts/deploy.sh` | Deploy new version with rollback | `./scripts/deploy.sh [--skip-pull] [--skip-backup]` |
| `scripts/backup.sh` | Create database backup | `./scripts/backup.sh [--no-nas] [--local-dir DIR]` |
| `scripts/restore.sh` | Restore database from backup | `./scripts/restore.sh <backup_file>` |
| `scripts/verify_backup.sh` | Verify backup integrity | `./scripts/verify_backup.sh <backup_file>` |
| `scripts/backup_retention.sh` | Prune old backups | `./scripts/backup_retention.sh [--dry-run]` |
| `scripts/integrity_check.py` | Validate database integrity | `docker exec shekel-prod-app python scripts/integrity_check.py [--verbose] [--category CAT]` |
| `scripts/audit_cleanup.py` | Clean old audit log entries | `docker exec shekel-prod-app python scripts/audit_cleanup.py [--days N] [--dry-run]` |
| `scripts/reset_mfa.py` | Emergency MFA reset for a user | `docker exec shekel-prod-app python scripts/reset_mfa.py <email>` |
| `scripts/seed_ref_tables.py` | Seed reference lookup tables | `docker exec shekel-prod-app python scripts/seed_ref_tables.py` |
| `scripts/seed_user.py` | Create initial seed user | `docker exec shekel-prod-app python scripts/seed_user.py` |
| `scripts/seed_tax_brackets.py` | Seed US tax brackets | `docker exec shekel-prod-app python scripts/seed_tax_brackets.py` |

### Cron Schedule

| Time | Script | Purpose |
|------|--------|---------|
| 2:00 AM daily | `backup.sh` | Database backup (local + NAS) |
| 2:30 AM daily | `backup_retention.sh` | Prune old backups per retention policy |
| 3:00 AM daily | `audit_cleanup.py` | Delete audit log rows older than 365 days |
| 3:00 AM Sunday | `verify_backup.sh` | Restore latest backup to temp DB, run checks |
| 3:30 AM Sunday | `integrity_check.py` | Validate production DB integrity |

Crontab entries (replace `/opt/shekel` with your actual path):

```cron
# ── Shekel Backups & Maintenance ─────────────────────────────────
0 2 * * * /opt/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1
30 2 * * * /opt/shekel/scripts/backup_retention.sh >> /var/log/shekel_backup.log 2>&1
0 3 * * * docker exec shekel-prod-app python scripts/audit_cleanup.py >> /var/log/shekel_backup.log 2>&1
0 3 * * 0 /opt/shekel/scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1) >> /var/log/shekel_backup.log 2>&1
30 3 * * 0 docker exec shekel-prod-app python scripts/integrity_check.py >> /var/log/shekel_backup.log 2>&1
```

---

## 2. Deployment

### 2.1 Deploying a New Version

The deploy script automates the full deployment workflow with automatic rollback on failure.

```bash
cd /opt/shekel
./scripts/deploy.sh
```

**What it does (in order):**

1. Pulls the latest code from GitHub (`git pull --ff-only`)
2. Runs a pre-deploy database backup (`scripts/backup.sh`)
3. Tags the current Docker image as `:previous` for rollback
4. Builds the new Docker image (`docker compose build app`)
5. Restarts the app container (`docker compose up -d --no-deps --force-recreate app`)
6. Waits for the health endpoint to report healthy (default: 60s timeout)
7. On health check failure: rolls back to the `:previous` image automatically

**Options:**

| Flag | Effect |
|------|--------|
| `--skip-pull` | Skip `git pull` (deploy from current working tree) |
| `--skip-backup` | Skip pre-deploy database backup |
| `--health-timeout N` | Seconds to wait for health check (default: 60) |
| `--health-interval N` | Seconds between health check retries (default: 5) |

**Example: deploy from a specific branch without backup:**
```bash
cd /opt/shekel
git checkout feature-branch
./scripts/deploy.sh --skip-pull --skip-backup
```

### 2.2 Manual Deployment

If the deploy script is unavailable or you need more control:

```bash
cd /opt/shekel

# 1. Pull latest code.
git pull --ff-only

# 2. Back up the database.
./scripts/backup.sh

# 3. Build the new image.
docker compose build app

# 4. Restart the app container (migrations run automatically via entrypoint.sh).
docker compose up -d --no-deps --force-recreate app

# 5. Verify health.
curl -s http://localhost/health
# Expected: {"status":"healthy","timestamp":"..."}
```

### 2.3 Rolling Back

**Automatic rollback:** The deploy script automatically rolls back if the health check fails after restart. No manual action is needed.

**Manual rollback** (if the deploy script was not used or rollback failed):

```bash
cd /opt/shekel

# Option A: Roll back to a specific git commit.
git log --oneline -5          # Find the commit to roll back to
git checkout <commit-hash>
docker compose build app
docker compose up -d --no-deps --force-recreate app

# Option B: If the previous image is still tagged.
# deploy.sh tags the in-place image as ${APP_CONTAINER}:previous
# (so shekel-prod-app:previous) before each deploy, then on rollback
# re-tags it as <compose_image>:latest.  The compose image for the
# bundled deployment is ghcr.io/saltyreformed/shekel:latest -- adjust
# below if you build a custom image with a different name.
docker tag shekel-prod-app:previous ghcr.io/saltyreformed/shekel:latest
docker compose up -d --no-deps --force-recreate app
```

After rollback, verify health:
```bash
curl -s http://localhost/health
```

### 2.4 First-Time Setup

**Prerequisites:** Docker, Docker Compose v2, and git installed on the Proxmox host.

```bash
# 1. Clone the repository.
git clone https://github.com/<your-repo>/shekel.git /opt/shekel
cd /opt/shekel

# 2. Create the environment file.
cp .env.example .env
nano .env
# Fill in REQUIRED values: SECRET_KEY, POSTGRES_PASSWORD, TOTP_ENCRYPTION_KEY
# See .env.example for generation commands.

# 3. Create the monitoring network (required by docker-compose.yml).
docker network create monitoring

# 4. Start the stack.
docker compose up -d

# 5. Wait for all services to be healthy.
docker compose ps
# All three services (db, app, nginx) should show "healthy".

# 6. Seed the database (first run only).
docker exec shekel-prod-app python scripts/seed_ref_tables.py
docker exec shekel-prod-app python scripts/seed_tax_brackets.py
docker exec shekel-prod-app python scripts/seed_user.py

# 7. Verify the application.
curl -s http://localhost/health
# Open http://localhost in a browser and log in.
# Default credentials: admin@shekel.local / ChangeMe!2026

# 8. Set up cron jobs (see §1 Cron Schedule above).
crontab -e
```

---

## 3. Backup & Restore

This section summarizes the key procedures. For complete details including NAS mount setup, encryption configuration, retention policy mechanics, and verification internals, see `docs/backup_runbook.md`.

### 3.1 Creating a Backup

```bash
# Standard backup (local + NAS).
./scripts/backup.sh

# Local only (skip NAS copy).
./scripts/backup.sh --no-nas

# Custom local directory.
./scripts/backup.sh --local-dir /tmp/my_backup --no-nas
```

Backups are written to `/var/backups/shekel/` as `shekel_backup_YYYYMMDD_HHMMSS.sql.gz`. If `BACKUP_ENCRYPTION_PASSPHRASE` is set in the environment, the file is encrypted with GPG (`.sql.gz.gpg`).

### 3.2 Restoring from a Backup

```bash
# List available backups (newest first).
ls -lht /var/backups/shekel/

# Restore from a specific backup.
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz

# For encrypted backups, set the passphrase first.
export BACKUP_ENCRYPTION_PASSPHRASE="your-passphrase"
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz.gpg
```

The restore script will:

1. Display a confirmation prompt (skip with `--skip-confirm`)
2. Stop the application container
3. Drop and recreate the database with schemas
4. Restore from the backup file
5. Restart the application container (entrypoint runs Alembic migrations)
6. Run basic sanity checks

**Post-restore verification:**
```bash
curl -s http://localhost/health
docker exec shekel-prod-app python scripts/integrity_check.py --verbose
```

### 3.3 Restoring from NAS

If local backups are unavailable:

```bash
cp /mnt/nas/backups/shekel/shekel_backup_20260315_020000.sql.gz /tmp/
./scripts/restore.sh /tmp/shekel_backup_20260315_020000.sql.gz
rm /tmp/shekel_backup_20260315_020000.sql.gz
```

### 3.4 Verifying a Backup

```bash
# Verify the most recent backup.
./scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1)
```

This restores the backup to a temporary database (`shekel_verify`), runs sanity queries and integrity checks, then drops the temporary database. The production database is never touched.

### 3.5 Retention Policy

| Tier | Criteria | Kept For |
|------|----------|----------|
| Daily | All backups | 7 days |
| Weekly | Sunday backups | 4 weeks |
| Monthly | 1st of month backups | 6 months |

```bash
# Preview what would be deleted.
./scripts/backup_retention.sh --dry-run

# Run retention cleanup.
./scripts/backup_retention.sh

# Override retention periods.
RETENTION_DAILY_DAYS=14 RETENTION_WEEKLY_WEEKS=8 ./scripts/backup_retention.sh
```

### 3.6 Integrity Checks

```bash
# Run all checks inside the app container.
docker exec shekel-prod-app python scripts/integrity_check.py

# Verbose output (shows every check, not just failures).
docker exec shekel-prod-app python scripts/integrity_check.py --verbose

# Run only one category: referential, orphan, balance, or consistency.
docker exec shekel-prod-app python scripts/integrity_check.py --category referential
```

**Exit codes:** 0 = all passed, 1 = critical failures, 2 = warnings only, 3 = script error.

---

## 4. Security Operations

### 4.1 Secret Inventory

The application requires three secrets for production operation. All are stored in `.env` on the Proxmox host.

| Secret | Purpose | Generation Command |
|--------|---------|-------------------|
| `SECRET_KEY` | Flask session cookie encryption | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `TOTP_ENCRYPTION_KEY` | Fernet encryption of TOTP secrets in database | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `POSTGRES_PASSWORD` | PostgreSQL database authentication | Any strong password generator |

For complete secret rotation procedures, see `docs/runbook_secrets.md`.

### 4.2 Rotating SECRET_KEY

1. Generate a new key: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Update `SECRET_KEY` in `.env`
3. Restart the app: `docker compose restart app`
4. **Impact:** All active sessions are invalidated. Users must log in again.

### 4.3 Rotating TOTP_ENCRYPTION_KEY

**WARNING: Changing this key makes ALL existing MFA enrollments unreadable.**

1. Disable MFA for all users: `docker exec shekel-prod-app python scripts/reset_mfa.py --all`
2. Generate a new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
3. Update `TOTP_ENCRYPTION_KEY` in `.env`
4. Restart the app: `docker compose restart app`
5. Users re-enroll MFA via Settings > Security

### 4.4 Rotating POSTGRES_PASSWORD

1. Generate a new password
2. Update `POSTGRES_PASSWORD` in `.env`
3. Restart both containers: `docker compose down && docker compose up -d`

### 4.5 Resetting MFA for a User

**When:** A user has lost their TOTP device and exhausted all backup codes.

```bash
# SSH into the Proxmox host.
docker exec shekel-prod-app python scripts/reset_mfa.py admin@shekel.local
```

Output: `MFA has been disabled for admin@shekel.local.`

The user can now log in with email + password only. They should re-enable MFA via Settings > Security after logging in.

### 4.6 Reviewing Audit Logs

**Via database (psql):**
```bash
# Recent changes to transactions.
docker exec shekel-prod-db psql -U shekel_user -d shekel -c \
  "SELECT executed_at, operation, row_id, changed_fields
   FROM system.audit_log
   WHERE table_name = 'transactions'
   ORDER BY executed_at DESC LIMIT 20;"

# Changes by a specific user.
docker exec shekel-prod-db psql -U shekel_user -d shekel -c \
  "SELECT executed_at, table_name, operation, row_id
   FROM system.audit_log
   WHERE user_id = 1
   ORDER BY executed_at DESC LIMIT 20;"
```

**Via Grafana/Loki (application-level auth events):**

See [§5.3 Key LogQL Queries](#53-key-logql-queries).

### 4.7 Audit Log Retention

Old audit log rows are cleaned up automatically by cron (daily at 3:00 AM). The default retention period is 365 days, configurable via the `AUDIT_RETENTION_DAYS` environment variable.

```bash
# Preview what would be deleted.
docker exec shekel-prod-app python scripts/audit_cleanup.py --dry-run

# Manual cleanup with a custom retention period.
docker exec shekel-prod-app python scripts/audit_cleanup.py --days 90
```

### 4.8 Backing Up the .env File

The `.env` file contains all three production secrets. Include it in your backup strategy:

```bash
# Add to the backup cron job (after the database backup):
cp /opt/shekel/.env /mnt/nas/backups/shekel/env_backup
```

Alternatively, store the three secrets in a password manager (e.g., Bitwarden, 1Password) as a separate recovery path.

### 4.9 HSTS Preload Decision

The Flask after-request hook in `app/__init__.py:_register_security_headers`
emits `Strict-Transport-Security: max-age=31536000; includeSubDomains` on every
response.  The `preload` directive is intentionally OFF.

**Why this matters.** `preload` is an instruction to browsers to honor HSTS for
the domain even on the very first visit -- before any HTTP response could carry
the header.  It works by submitting the domain to a list maintained by the
Chromium project (consumed by Chrome, Firefox, Safari, Edge).  Once a domain is
on the list, **delisting takes 6-12 weeks AND requires removing the `preload`
directive from the HSTS header for the duration**.  During that window, every
subdomain MUST be HTTPS-only.

**To enable preload (ONLY when you are certain you are committing for years).**

1. Confirm every subdomain you operate is HTTPS-only.  Test with
   `curl -I http://<subdomain>.<your-domain>/` and verify a 301 to https://.
2. Edit `app/__init__.py` and add `; preload` to the
   `Strict-Transport-Security` value.
3. Deploy.  Run for at least one week to confirm no users hit
   `https://<subdomain>` and get a TLS error.
4. Submit the apex domain at <https://hstspreload.org/>.  Wait for the email
   confirmation (typically 4-12 weeks for inclusion in browser releases).

**To disable preload after submission.**

Removing `preload` from the header is a precondition for delisting; it does not
cause delisting on its own.  Submit a removal request at
<https://hstspreload.org/removal/>.  Allow 6-12 weeks before any subdomain can
go back to HTTP.

### 4.10 CDN Vendor Refresh Procedure

The application serves Bootstrap, Bootstrap Icons, htmx, Chart.js, Inter, and
JetBrains Mono from `app/static/vendor/` rather than a CDN (audit F-037).  The
CSP forbids external script/style/font origins so a refresh requires both
fetching the new file and updating the manifest.

**Manifest.** `app/static/vendor/VERSIONS.txt` records each upstream URL and
its SHA-384 hash.  The hash is the source of truth for which exact bytes are
served.

**Refresh procedure.**

1. **Bootstrap, Bootstrap Icons, htmx, Chart.js**

   ```bash
   cd app/static/vendor
   # Replace the file with the desired version.
   curl -fL <upstream URL> -o <vendor-path>
   # Compute the new SHA-384.
   openssl dgst -sha384 -binary <vendor-path> | openssl base64 -A
   ```

   Update the matching line in `VERSIONS.txt` with the new URL and hash.

2. **Inter / JetBrains Mono fonts.**  Run the helper script:

   ```bash
   python scripts/vendor_google_fonts.py
   ```

   The script fetches the upstream Google Fonts CSS, downloads the latin and
   latin-ext woff2 files, rewrites URLs to local paths, and emits a fresh
   `app/static/vendor/fonts/fonts.css`.  After running, recompute the SHA-384
   hashes for `fonts.css` and each `*.woff2` and update `VERSIONS.txt`.

3. **Verify.**  Start the app and exercise every dashboard
   (analytics, debt strategy, investment, loan, retirement) plus the budget
   grid.  The browser DevTools Network tab should show every asset loading
   from `/static/vendor/...` with no CSP violations in the Console.

4. **Test.**  Run the security-headers and cache-control test suites:

   ```bash
   pytest tests/test_integration/test_security_headers.py \
          tests/test_adversarial/test_cache_control.py -v
   ```

   The `test_static_asset_path_resolves` test ensures every vendored asset
   referenced by templates exists at its expected path.

5. **Commit.**  One commit per refresh; commit message names the package and
   version (e.g. `chore(vendor): bump Chart.js 4.4.7 -> 4.5.0`).

---

## 5. Monitoring & Observability

### 5.0 Architecture (Commit C-15)

Shekel logs flow through a four-stage pipeline:

```
[Flask app]
   |   structured JSON to stdout (one record per line)
   v
[Docker json-file driver]
   |   short-term local rotation (10 MiB x 5 files)
   |   readable via "docker logs shekel-prod-app"
   v
[Grafana Alloy]                <-- runs in /opt/docker/monitoring/
   |   reads container logs via /var/run/docker.sock (read-only mount)
   |   parses JSON via the loki.process.shekel stage
   |   tags records with compose_service, level, logger, event labels
   v
[Loki]                          <-- runs on the "monitoring" network
   |   filesystem-backed storage on /opt/docker/monitoring/loki/data
   |   30-day retention (744h) configured in loki.yaml
   v
[Grafana]                       <-- https://grafana.saltyreformed.com
       (LAN-only via the existing nginx + wildcard cert)
```

**Tamper-resistance property.** The Shekel app container shares no
volume and no network with the Loki storage volume. An attacker who
gains RCE in Gunicorn can spam new log records (which Alloy will
faithfully ingest) but cannot delete or rewrite records already
shipped to Loki. The local `json-file` driver buffer at
`/var/lib/docker/containers/<id>/<id>-json.log` IS rewritable by a
host-root attacker, but anything Alloy already scraped from it is
immutable in Loki. This satisfies ASVS V7.3.3 / V7.3.4 to the level
appropriate for a single-host deployment; the previous `applogs`
Docker volume that lived in the same trust boundary as the app was
removed in Commit C-15 (audit findings F-082, F-150). For an
absolute tamper-evident trail (off-site, write-once), see the
deferred S3-with-Object-Lock option in the C-15 architectural
decision notes.

The full collector and dashboard configuration is documented in
`observability.md`. This runbook section assumes Phase 0 -- 5 of
that plan are complete.

### 5.1 Checking Application Logs

```bash
# View recent Flask application logs (JSON format).
docker logs shekel-prod-app --tail 20

# Follow logs in real time.
docker logs shekel-prod-app -f

# View Nginx access logs (JSON format).
docker logs shekel-prod-nginx --tail 20

# View PostgreSQL logs.
docker logs shekel-prod-db --tail 20

# View cloudflared tunnel logs.
journalctl -u cloudflared --no-pager -n 20

# View backup cron logs.
tail -50 /var/log/shekel_backup.log
```

**Flask log format (JSON, RFC3339Nano timestamps):** Each line is a
single JSON object with these stable keys; additional structured
fields appear when the call site supplies them via `extra={...}`.

- `timestamp` -- RFC3339Nano UTC with microsecond precision and `Z` suffix, e.g. `2026-05-05T19:36:45.139287Z`.
- `level` -- `DEBUG`, `INFO`, `WARNING`, `ERROR`.
- `logger` -- Python logger name (e.g. `app.routes.auth`).
- `message` -- Human-readable description.
- `request_id` -- UUID4 correlating every log line from a single HTTP request. Returned to the client in the `X-Request-Id` header so a user-reported issue can be looked up directly.
- `event` -- Structured event name (e.g. `login_success`, `rate_limit_exceeded`, `slow_request`). The full registry lives in `app/utils/log_events.py:EVENT_REGISTRY`.
- `category` -- One of `auth`, `business`, `access`, `audit`, `error`, `performance`.
- `remote_addr` -- Client IP (forwarded by nginx via `X-Forwarded-For`).
- `user_id` -- Authenticated user ID, omitted on anonymous requests.

The Alloy `loki.process.shekel` stage promotes `level`, `logger`, and
`event` to Loki labels so dashboards can filter on them without a
`| json` parser stage in every query.

### 5.2 Querying Logs in Grafana

1. Open Grafana: `https://grafana.saltyreformed.com` (LAN-only).
2. Log in with the admin account (password in `/opt/docker/monitoring/secrets/grafana_admin_password`).
3. Navigate to **Explore** (compass icon in the left sidebar).
4. Select **Loki** as the data source. (Provisioned automatically per `observability.md` Phase 4 datasources.yaml.)
5. Enter a LogQL query (see below) and click **Run query**.

### 5.3 Key LogQL Queries

The Alloy pipeline exposes both the raw Docker container labels
(`compose_service`, `container`, `compose_project`) and the
JSON-extracted labels (`level`, `logger`, `event`). Queries below
prefer `compose_service` because it survives container renames.

| Purpose | Query |
|---------|-------|
| All app logs | `{compose_service="shekel-prod-app"}` |
| All auth events | `{compose_service="shekel-prod-app"} \| json \| category="auth"` |
| All access events (incl. rate-limit) | `{compose_service="shekel-prod-app"} \| json \| category="access"` |
| Login failures | `{compose_service="shekel-prod-app", event="login_failed"}` |
| Login successes | `{compose_service="shekel-prod-app", event="login_success"}` |
| Password changes | `{compose_service="shekel-prod-app", event="password_changed"}` |
| MFA events | `{compose_service="shekel-prod-app"} \| json \| event=~"mfa_.*"` |
| Rate-limit hits (F-146) | `{compose_service="shekel-prod-app", event="rate_limit_exceeded"}` |
| Rate-limit by path | `{compose_service="shekel-prod-app", event="rate_limit_exceeded"} \| json \| line_format "{{.path}} {{.remote_addr}}"` |
| Account lockouts | `{compose_service="shekel-prod-app", event="account_locked"}` |
| Slow requests | `{compose_service="shekel-prod-app", event="slow_request"}` |
| All errors | `{compose_service="shekel-prod-app", level="ERROR"}` |
| Business events | `{compose_service="shekel-prod-app"} \| json \| category="business"` |
| By user ID | `{compose_service="shekel-prod-app"} \| json \| user_id="1"` |
| Trace a request | `{compose_service="shekel-prod-app"} \| json \| request_id="<uuid>"` |

The `request_id` derived field declared in `datasources.yaml` makes
a UUID in any log line clickable -- it copies the value into a
prefilled trace-a-request query so a user-reported `X-Request-Id`
lookup is one click.

### 5.4 Monitoring Stack Management

The Loki / Grafana / Alloy stack runs from
`/opt/docker/monitoring/` per `observability.md`. The Shekel stack
does NOT need to share a Docker network with it -- Alloy reads
container logs via the docker socket, which works regardless of
the source container's network membership.

```bash
# Check monitoring stack status.
docker ps --filter "name=alloy" --filter "name=loki" --filter "name=grafana"

# Start / restart the monitoring stack.
cd /opt/docker/monitoring
docker compose up -d
docker compose restart alloy   # e.g. after editing alloy/config/config.alloy

# Verify Alloy is scraping containers.
docker exec alloy wget -qO- 'http://localhost:12345/api/v0/component/discovery.docker.containers/debug/info' | head -c 600

# Confirm Shekel records are arriving in Loki.
docker exec loki wget -qO- 'http://localhost:3100/loki/api/v1/labels'
docker exec loki wget -qO- \
  'http://localhost:3100/loki/api/v1/query?query=%7Bcompose_service%3D%22shekel-prod-app%22%7D' \
  | head -c 400

# Check Alloy logs for parser failures (a JSON shape regression
# would surface here -- "could not parse" / "skip due to error").
docker logs alloy --tail 50
```

If a deploy lands and Alloy's `loki.process.shekel` stage suddenly
shows `failed to parse` errors, the most likely cause is a
regression in `app/utils/logging_config.py` -- the formatter must
emit the keys `timestamp`, `level`, `logger`, `message`, `event`,
`request_id` for the parser to map fields cleanly. Re-run
`pytest tests/test_utils/test_logging_config.py` to catch shape
drifts before they reach production.

### 5.5 Alerting on Rate-Limit Pressure

Rate-limit hits are emitted as `event="rate_limit_exceeded"` records
under `category="access"` (audit Commit C-15 / finding F-146). The
intended alert in Grafana (provision under
`/opt/docker/monitoring/grafana/provisioning/alerting/`) is:

- **Datasource:** Loki
- **Query:** `count_over_time({compose_service="shekel-prod-app", event="rate_limit_exceeded"} [5m])`
- **Condition:** is above 10 (tune after a week of baseline data)
- **Evaluation:** every 1 minute, for at least 5 minutes

A burst of 10+ rate-limit hits in 5 minutes is well above the
single-user steady-state (effectively zero outside test windows)
and is the earliest queryable signal of a credential-stuffing
campaign that the per-route 5-per-15min ceiling is otherwise
silently absorbing. Pair the rule with Grafana contact-point
delivery (email or webhook) per the operator's preference.

### 5.5 Health Checks

The `/health` endpoint returns the application and database status:

```bash
# Via localhost (bypasses Cloudflare):
curl -s http://localhost/health
# Expected: {"status":"healthy","timestamp":"..."}

# Via Cloudflare Tunnel (full chain):
curl -s https://<domain>/health
# Expected: same response (may require Access bypass; see §6.4)
```

Health checks are also performed automatically by:

- **Docker:** Every 30 seconds for the `app` and `nginx` containers
- **deploy.sh:** After each deployment (60s timeout, 5s interval)

---

## 6. Cloudflare Management

### 6.1 Tunnel Status

```bash
# Check the systemd service.
sudo systemctl status cloudflared

# View recent tunnel logs.
journalctl -u cloudflared --no-pager -n 30

# List all tunnels on the account.
cloudflared tunnel list

# Check tunnel connectivity (should show active connections).
cloudflared tunnel info shekel
```

### 6.2 Restarting the Tunnel

```bash
sudo systemctl restart cloudflared
```

**When to restart:**
- After editing `/etc/cloudflared/config.yml`
- After a cloudflared package update
- If the tunnel shows connection errors in `journalctl`

**Note:** Restarting cloudflared causes a brief (<5 second) interruption in external access. Internal (LAN) access via `http://localhost` is not affected.

### 6.3 Tunnel Configuration Changes

```bash
# Edit the config file.
sudo nano /etc/cloudflared/config.yml

# Validate the config (dry run).
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate

# Restart to apply changes.
sudo systemctl restart cloudflared

# Verify the tunnel is running.
sudo systemctl status cloudflared
curl -s https://<domain>/health
```

### 6.4 Adding a New Authorized User (Cloudflare Access)

Cloudflare Access controls who can reach the application. Only email addresses in the Access policy can pass through.

1. Log in to the Cloudflare dashboard: `https://dash.cloudflare.com`
2. Navigate to **Zero Trust** (or `https://one.dash.cloudflare.com`)
3. Go to **Access** > **Applications**
4. Click on **Shekel Budget App**
5. Edit the **Allowed Users** policy
6. Under **Include** > **Emails**, add the new email address
7. Click **Save**

The new user can now authenticate via Cloudflare Access (email OTP or configured identity provider) and reach the Shekel login page.

**Note:** This only grants access through the Cloudflare layer. The user still needs a Shekel account (via seed script or future registration) to log into the application.

### 6.5 Removing an Authorized User

1. Navigate to **Zero Trust** > **Access** > **Applications** > **Shekel Budget App**
2. Edit the **Allowed Users** policy
3. Remove the email address from the **Include** > **Emails** list
4. Click **Save**

The user's existing Cloudflare Access session will expire (default: 24 hours). To revoke access immediately:

1. Navigate to **Zero Trust** > **Access** > **Applications** > **Shekel Budget App**
2. Click the **Overview** tab
3. Under **Active Sessions**, find and revoke the user's session

### 6.6 Updating WAF Rate Limit Rules

The Cloudflare WAF rate limits protect `/login` and `/auth/mfa/verify` against brute-force attacks at the network edge (before traffic reaches the origin).

Current configuration:

| Rule | Path | Method | Rate | Block Duration |
|------|------|--------|------|---------------|
| Login brute force protection | `/login` | POST | 20 per 10 seconds | 60 seconds |
| MFA brute force protection | `/auth/mfa/verify` | POST | 20 per 10 seconds | 60 seconds |

To modify thresholds:

1. Log in to the Cloudflare dashboard: `https://dash.cloudflare.com`
2. Select your domain
3. Navigate to **Security** > **WAF** > **Rate limiting rules**
4. Click the rule name (e.g., `Login brute force protection`)
5. Click **Edit**
6. Adjust the **Rate** or **Period** fields
7. Click **Save**

Changes take effect within seconds.

### 6.7 Rotating Tunnel Credentials

If tunnel credentials are compromised:

```bash
# Delete the old tunnel.
cloudflared tunnel delete shekel

# Create a new tunnel (generates new credentials).
cloudflared tunnel create shekel

# Update the DNS record.
cloudflared tunnel route dns shekel <domain>

# Update /etc/cloudflared/config.yml with the new tunnel ID.
sudo nano /etc/cloudflared/config.yml
# Replace the tunnel UUID and credentials-file path.

# Restart cloudflared.
sudo systemctl restart cloudflared

# Verify.
curl -s https://<domain>/health
```

---

## 7. Troubleshooting

### 7.1 Common Issues

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| App unreachable externally | cloudflared service down | `sudo systemctl status cloudflared` then `sudo systemctl restart cloudflared` |
| App unreachable externally | Docker containers down | `docker compose ps` then `docker compose up -d` |
| "Access Denied" on all requests | Cloudflare Access misconfigured | Check Access policy in Zero Trust dashboard; verify email is in the allowed list |
| Cloudflare 502 Bad Gateway | Nginx or app container unhealthy | `docker compose ps` to check health; `docker logs shekel-prod-nginx` and `docker logs shekel-prod-app` for errors |
| Cloudflare 522 Connection Timed Out | cloudflared cannot reach Nginx | Verify Nginx is running: `docker compose ps`. Check cloudflared logs: `journalctl -u cloudflared -n 20` |
| 429 on first login attempt | Cloudflare rate limit too aggressive | Check WAF rules in Cloudflare dashboard (Security > WAF > Rate limiting rules); increase threshold |
| 429 after a few login attempts | Flask-Limiter rate limit (5/15min) | Wait 15 minutes. To clear immediately: `docker exec shekel-prod-redis redis-cli FLUSHDB` -- counters are stored in Redis, not in app memory, so restarting the app does NOT reset them |
| 500 on every login attempt | Redis container down or unreachable | App is configured fail-closed: rate-limit storage outage rejects every limited request. `docker compose ps redis` to check status; `docker logs shekel-prod-redis --tail 50` for errors; `docker compose up -d redis` to restart. Login resumes immediately once Redis answers PING |
| Wrong IP in logs (127.0.0.1) | Nginx real IP config issue | Verify `set_real_ip_from` and `real_ip_header CF-Connecting-IP` in `nginx/nginx.conf` |
| No logs in Grafana | Promtail not scraping | `docker logs promtail`. Verify `monitoring` network exists. Verify app is on the network: `docker network inspect monitoring` |
| CSS/JS not loading | Static files volume issue | `docker exec shekel-prod-nginx ls /var/www/static/` to verify files exist. Rebuild app: `docker compose build app && docker compose up -d` |
| Health check returns 500 | Database connection issue | `docker exec shekel-prod-db pg_isready -U shekel_user -d shekel`. Check `docker logs shekel-prod-app --tail 20` |
| Database backup failed | Container down or disk full | `docker ps` to check shekel-prod-db. `df -h` to check disk space |
| NAS backup failed | NAS not mounted | `mount \| grep nas`. Remount: `sudo mount -a` |
| Deploy script rollback failed | No previous image tagged | Manual intervention: `docker logs shekel-prod-app` to diagnose, then fix and redeploy |

### 7.2 Log Locations

| Log | Command | Contents |
|-----|---------|----------|
| Flask app (JSON) | `docker logs shekel-prod-app` | Request logs, auth events, business events, errors |
| Nginx (JSON) | `docker logs shekel-prod-nginx` | HTTP access logs, upstream errors |
| PostgreSQL | `docker logs shekel-prod-db` | Database server logs, connection errors |
| Cloudflared | `journalctl -u cloudflared` | Tunnel connection logs, reconnects |
| Backups | `cat /var/log/shekel_backup.log` | Cron job output for backup, retention, verify, integrity |
| Grafana | `docker logs grafana` | Grafana server logs |
| Promtail | `docker logs promtail` | Log scraper status, discovery errors |

### 7.3 Emergency Procedures

#### Application is down -- restore service quickly

```bash
# 1. Identify which container is unhealthy.
docker compose ps

# 2. Check container logs for the failing service.
docker logs shekel-prod-app --tail 50
docker logs shekel-prod-nginx --tail 20
docker logs shekel-prod-db --tail 20

# 3. Restart the failing service.
docker compose restart app    # or: nginx, db

# 4. If the app container won't start, restart the full stack.
docker compose down && docker compose up -d

# 5. Verify recovery.
curl -s http://localhost/health
```

#### Database is corrupted -- restore from backup

```bash
# 1. Identify the latest good backup.
ls -lht /var/backups/shekel/

# 2. Restore (will prompt for confirmation).
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz

# 3. Verify the restore.
curl -s http://localhost/health
docker exec shekel-prod-app python scripts/integrity_check.py --verbose
```

If local backups are corrupted, restore from NAS:
```bash
cp /mnt/nas/backups/shekel/shekel_backup_20260315_020000.sql.gz /tmp/
./scripts/restore.sh /tmp/shekel_backup_20260315_020000.sql.gz
rm /tmp/shekel_backup_20260315_020000.sql.gz
```

#### Locked out of the app -- MFA device lost

```bash
# SSH to the Proxmox host.
docker exec shekel-prod-app python scripts/reset_mfa.py your-email@example.com
# Output: MFA has been disabled for your-email@example.com.

# Log in with email + password (MFA is now disabled).
# Re-enable MFA in Settings > Security after logging in.
```

#### Locked out of Cloudflare Access

1. Log into the Cloudflare dashboard directly at `https://dash.cloudflare.com` (this is independent of the tunnel and Access policy)
2. Navigate to **Zero Trust** > **Access** > **Applications**
3. Either:
   - Add your current email to the **Allowed Users** policy, or
   - Temporarily set the policy action to **Bypass** (allows everyone)
4. Access the app and verify the fix
5. Restore the original policy

#### Complete disaster recovery -- host is lost

1. Provision a new Proxmox host with Docker and Docker Compose
2. Clone the repository: `git clone <repo-url> /opt/shekel`
3. Reconstruct `.env` from `.env.example`:
   - `SECRET_KEY`: generate new (users must re-login)
   - `TOTP_ENCRYPTION_KEY`: use the backed-up key from password manager, or generate new (users must re-enroll MFA)
   - `POSTGRES_PASSWORD`: use the password from the backup, or set new
4. Create the monitoring network: `docker network create monitoring`
5. Start the stack: `docker compose up -d`
6. Restore from NAS backup: `./scripts/restore.sh /mnt/nas/backups/shekel/<latest>.sql.gz`
7. Re-install cloudflared and configure the tunnel (see `cloudflared/config.yml`)
8. Re-configure cron jobs (see §1 Cron Schedule)
9. Verify: `curl -s http://localhost/health` and `curl -s https://<domain>/health`

See `docs/runbook_secrets.md` for detailed secret reconstruction procedures.
