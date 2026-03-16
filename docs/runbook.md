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
[Cloudflare Edge]          — TLS termination, Access policy, WAF rate limiting
       |
       v
[cloudflared]              — Proxmox host, systemd service, encrypted tunnel
       |
       v
[Nginx :80]                — Docker container, reverse proxy, static files
       |
       v
[Gunicorn :8000]           — Docker container, WSGI server, 2 workers
       |
       v
[Flask Application]        — Shekel budget app, structured JSON logging
       |
       v
[PostgreSQL :5432]         — Docker container, multi-schema, audit triggers
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
| `shekel-db` | `postgres:16-alpine` | backend | `pg_isready` every 10s |
| `shekel-app` | Built from Dockerfile | backend, monitoring | `GET /health` every 30s |
| `shekel-nginx` | `nginx:1.27-alpine` | frontend, backend | `wget /health` every 30s |

### Script Inventory

| Script | Purpose | Usage |
|--------|---------|-------|
| `scripts/deploy.sh` | Deploy new version with rollback | `./scripts/deploy.sh [--skip-pull] [--skip-backup]` |
| `scripts/backup.sh` | Create database backup | `./scripts/backup.sh [--no-nas] [--local-dir DIR]` |
| `scripts/restore.sh` | Restore database from backup | `./scripts/restore.sh <backup_file>` |
| `scripts/verify_backup.sh` | Verify backup integrity | `./scripts/verify_backup.sh <backup_file>` |
| `scripts/backup_retention.sh` | Prune old backups | `./scripts/backup_retention.sh [--dry-run]` |
| `scripts/integrity_check.py` | Validate database integrity | `docker exec shekel-app python scripts/integrity_check.py [--verbose] [--category CAT]` |
| `scripts/audit_cleanup.py` | Clean old audit log entries | `docker exec shekel-app python scripts/audit_cleanup.py [--days N] [--dry-run]` |
| `scripts/reset_mfa.py` | Emergency MFA reset for a user | `docker exec shekel-app python scripts/reset_mfa.py <email>` |
| `scripts/seed_ref_tables.py` | Seed reference lookup tables | `docker exec shekel-app python scripts/seed_ref_tables.py` |
| `scripts/seed_user.py` | Create initial seed user | `docker exec shekel-app python scripts/seed_user.py` |
| `scripts/seed_tax_brackets.py` | Seed US tax brackets | `docker exec shekel-app python scripts/seed_tax_brackets.py` |

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
0 3 * * * docker exec shekel-app python scripts/audit_cleanup.py >> /var/log/shekel_backup.log 2>&1
0 3 * * 0 /opt/shekel/scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1) >> /var/log/shekel_backup.log 2>&1
30 3 * * 0 docker exec shekel-app python scripts/integrity_check.py >> /var/log/shekel_backup.log 2>&1
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

# Option B: If the previous image is still tagged:
docker tag shekel-app:previous shekel-shekel-app:latest
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
docker exec shekel-app python scripts/seed_ref_tables.py
docker exec shekel-app python scripts/seed_tax_brackets.py
docker exec shekel-app python scripts/seed_user.py

# 7. Verify the application.
curl -s http://localhost/health
# Open http://localhost in a browser and log in.
# Default credentials: admin@shekel.local / changeme

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
docker exec shekel-app python scripts/integrity_check.py --verbose
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
docker exec shekel-app python scripts/integrity_check.py

# Verbose output (shows every check, not just failures).
docker exec shekel-app python scripts/integrity_check.py --verbose

# Run only one category: referential, orphan, balance, or consistency.
docker exec shekel-app python scripts/integrity_check.py --category referential
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

1. Disable MFA for all users: `docker exec shekel-app python scripts/reset_mfa.py --all`
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
docker exec shekel-app python scripts/reset_mfa.py admin@shekel.local
```

Output: `MFA has been disabled for admin@shekel.local.`

The user can now log in with email + password only. They should re-enable MFA via Settings > Security after logging in.

### 4.6 Reviewing Audit Logs

**Via database (psql):**
```bash
# Recent changes to transactions.
docker exec shekel-db psql -U shekel_user -d shekel -c \
  "SELECT executed_at, operation, row_id, changed_fields
   FROM system.audit_log
   WHERE table_name = 'transactions'
   ORDER BY executed_at DESC LIMIT 20;"

# Changes by a specific user.
docker exec shekel-db psql -U shekel_user -d shekel -c \
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
docker exec shekel-app python scripts/audit_cleanup.py --dry-run

# Manual cleanup with a custom retention period.
docker exec shekel-app python scripts/audit_cleanup.py --days 90
```

### 4.8 Backing Up the .env File

The `.env` file contains all three production secrets. Include it in your backup strategy:

```bash
# Add to the backup cron job (after the database backup):
cp /opt/shekel/.env /mnt/nas/backups/shekel/env_backup
```

Alternatively, store the three secrets in a password manager (e.g., Bitwarden, 1Password) as a separate recovery path.

---

## 5. Monitoring & Observability

### 5.1 Checking Application Logs

```bash
# View recent Flask application logs (JSON format).
docker logs shekel-app --tail 20

# Follow logs in real time.
docker logs shekel-app -f

# View Nginx access logs (JSON format).
docker logs shekel-nginx --tail 20

# View PostgreSQL logs.
docker logs shekel-db --tail 20

# View cloudflared tunnel logs.
journalctl -u cloudflared --no-pager -n 20

# View backup cron logs.
tail -50 /var/log/shekel_backup.log
```

**Flask log format (JSON):** Each log entry contains:
- `timestamp` — ISO 8601 timestamp
- `level` — DEBUG, INFO, WARNING, ERROR
- `logger` — Python logger name (e.g., `app.routes.auth`)
- `message` — Human-readable description
- `request_id` — UUID for correlating all logs from a single request
- `event` — Structured event name (e.g., `login_success`, `slow_request`)
- `category` — Event category: `auth`, `business`, `error`, `performance`
- `remote_addr` — Client IP address
- `user_id` — Authenticated user ID (if applicable)

### 5.2 Querying Logs in Grafana

1. Open Grafana: `http://<proxmox-ip>:3000`
2. Log in (default: admin / admin; change password on first login)
3. Navigate to **Explore** (compass icon in the left sidebar)
4. Select **Loki** as the data source
5. Enter a LogQL query (see below) and click **Run query**

If Loki is not configured as a data source:

1. Navigate to **Connections** > **Data sources** > **Add data source**
2. Select **Loki**
3. Set URL to `http://loki:3100`
4. Click **Save & test**

### 5.3 Key LogQL Queries

| Purpose | Query |
|---------|-------|
| All app logs | `{container="shekel-app"}` |
| All auth events | `{container="shekel-app"} \| json \| category="auth"` |
| Login failures | `{container="shekel-app"} \| json \| event="login_failed"` |
| Login successes | `{container="shekel-app"} \| json \| event="login_success"` |
| Password changes | `{container="shekel-app"} \| json \| event="password_changed"` |
| MFA events | `{container="shekel-app"} \| json \| event=~"mfa_.*"` |
| Slow requests | `{container="shekel-app"} \| json \| event="slow_request"` |
| All errors | `{container="shekel-app"} \| json \| level="ERROR"` |
| Business events | `{container="shekel-app"} \| json \| category="business"` |
| By user ID | `{container="shekel-app"} \| json \| user_id="1"` |
| Trace a request | `{container="shekel-app"} \| json \| request_id="<uuid>"` |

### 5.4 Monitoring Stack Management

The monitoring stack (Loki, Grafana, Promtail) runs as a separate docker-compose stack on the Proxmox host. See `monitoring/README.md` for the full setup guide.

```bash
# Check monitoring stack status.
docker ps --filter "name=loki" --filter "name=promtail" --filter "name=grafana"

# Start the monitoring stack.
cd /path/to/monitoring
docker compose up -d

# Restart Promtail (e.g., after config changes).
docker restart promtail

# Check Promtail targets (verify it sees the shekel-app container).
curl -s http://localhost:9080/targets

# Check Promtail logs for errors.
docker logs promtail --tail 20
```

**Shared network:** Both the Shekel stack and the monitoring stack must be on the `monitoring` Docker network:

```bash
# Create the network (one-time setup).
docker network create monitoring

# Verify the app container is on the network.
docker network inspect monitoring | grep shekel-app
```

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
| Cloudflare 502 Bad Gateway | Nginx or app container unhealthy | `docker compose ps` to check health; `docker logs shekel-nginx` and `docker logs shekel-app` for errors |
| Cloudflare 522 Connection Timed Out | cloudflared cannot reach Nginx | Verify Nginx is running: `docker compose ps`. Check cloudflared logs: `journalctl -u cloudflared -n 20` |
| 429 on first login attempt | Cloudflare rate limit too aggressive | Check WAF rules in Cloudflare dashboard (Security > WAF > Rate limiting rules); increase threshold |
| 429 after a few login attempts | Flask-Limiter rate limit (5/15min) | Wait 15 minutes. Or restart app to clear in-memory state: `docker compose restart app` |
| Wrong IP in logs (127.0.0.1) | Nginx real IP config issue | Verify `set_real_ip_from` and `real_ip_header CF-Connecting-IP` in `nginx/nginx.conf` |
| No logs in Grafana | Promtail not scraping | `docker logs promtail`. Verify `monitoring` network exists. Verify app is on the network: `docker network inspect monitoring` |
| CSS/JS not loading | Static files volume issue | `docker exec shekel-nginx ls /var/www/static/` to verify files exist. Rebuild app: `docker compose build app && docker compose up -d` |
| Health check returns 500 | Database connection issue | `docker exec shekel-db pg_isready -U shekel_user -d shekel`. Check `docker logs shekel-app --tail 20` |
| Database backup failed | Container down or disk full | `docker ps` to check shekel-db. `df -h` to check disk space |
| NAS backup failed | NAS not mounted | `mount \| grep nas`. Remount: `sudo mount -a` |
| Deploy script rollback failed | No previous image tagged | Manual intervention: `docker logs shekel-app` to diagnose, then fix and redeploy |

### 7.2 Log Locations

| Log | Command | Contents |
|-----|---------|----------|
| Flask app (JSON) | `docker logs shekel-app` | Request logs, auth events, business events, errors |
| Nginx (JSON) | `docker logs shekel-nginx` | HTTP access logs, upstream errors |
| PostgreSQL | `docker logs shekel-db` | Database server logs, connection errors |
| Cloudflared | `journalctl -u cloudflared` | Tunnel connection logs, reconnects |
| Backups | `cat /var/log/shekel_backup.log` | Cron job output for backup, retention, verify, integrity |
| Grafana | `docker logs grafana` | Grafana server logs |
| Promtail | `docker logs promtail` | Log scraper status, discovery errors |

### 7.3 Emergency Procedures

#### Application is down — restore service quickly

```bash
# 1. Identify which container is unhealthy.
docker compose ps

# 2. Check container logs for the failing service.
docker logs shekel-app --tail 50
docker logs shekel-nginx --tail 20
docker logs shekel-db --tail 20

# 3. Restart the failing service.
docker compose restart app    # or: nginx, db

# 4. If the app container won't start, restart the full stack.
docker compose down && docker compose up -d

# 5. Verify recovery.
curl -s http://localhost/health
```

#### Database is corrupted — restore from backup

```bash
# 1. Identify the latest good backup.
ls -lht /var/backups/shekel/

# 2. Restore (will prompt for confirmation).
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz

# 3. Verify the restore.
curl -s http://localhost/health
docker exec shekel-app python scripts/integrity_check.py --verbose
```

If local backups are corrupted, restore from NAS:
```bash
cp /mnt/nas/backups/shekel/shekel_backup_20260315_020000.sql.gz /tmp/
./scripts/restore.sh /tmp/shekel_backup_20260315_020000.sql.gz
rm /tmp/shekel_backup_20260315_020000.sql.gz
```

#### Locked out of the app — MFA device lost

```bash
# SSH to the Proxmox host.
docker exec shekel-app python scripts/reset_mfa.py your-email@example.com
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

#### Complete disaster recovery — host is lost

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
