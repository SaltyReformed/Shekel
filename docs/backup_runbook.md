# Shekel Backup & Disaster Recovery Runbook

## 1. Overview

This runbook covers all backup, restore, retention, and verification procedures for the Shekel
budget application. The backup strategy uses `pg_dump` for PostgreSQL database dumps with gzip
compression and GPG encryption, tiered retention, and optional NAS replication.

### Script Inventory

| Script | Purpose | Schedule |
|--------|---------|----------|
| `scripts/backup.sh` | Create compressed database backup | cron (§3.1) |
| `scripts/backup_retention.sh` | Prune old backups per retention policy | cron (§3.1) |
| `scripts/restore.sh` | Restore database from a backup file | Manual |
| `scripts/verify_backup.sh` | Verify backup integrity via temp database | cron (§3.1) |
| `scripts/integrity_check.py` | Validate database referential and logical integrity | cron (§3.1) |

### Backup Strategy

- **Automated backups** via cron (§3.1)
- **Tiered retention**: daily, weekly (Sunday) and monthly (the 1st) tiers, each kept for its own
  period (§5)
- **Local + NAS storage**: backups written to the local backup directory (`BACKUP_LOCAL_DIR`) and
  copied to the NAS mount (`BACKUP_NAS_DIR`)
- **Encryption**: AES-256 via GPG symmetric encryption, keyed by one private key file (§3.3); an
  unencrypted backup is written only when `--no-encrypt` asks for one
- **Scheduled verification** via cron (§3.1): a restore to a temp database with sanity and integrity
  checks

---

## 2. Prerequisites

- **Host**: Arch Linux with Docker, Docker Compose and GnuPG (`gpg`) installed
- **Key file**: `/etc/shekel/backup-passphrase` (§3.3), unless every backup is `--no-encrypt`
- **Containers**: `shekel-prod-db` (the PostgreSQL image `docker-compose.yml` pins) running via
  `docker-compose.yml`
- **App container** (production only): `shekel-prod-app` running via `docker-compose.yml`
- **Disk space**: sufficient local storage for every backup the retention tiers keep (§5)
- **NAS** (optional): network share mounted on the host

---

## 3. Automated Backup Setup

### 3.1 Cron Configuration

Add these entries to the host's crontab (`crontab -e`):

```cron
# ── Shekel Backups ───────────────────────────────────────────────

# Daily backup at 2:00 AM.  No secret on this line: backup.sh reads the key file (§3.3).
0 2 * * * /path/to/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1

# Daily retention cleanup at 2:30 AM.  --force is what deletes: without it every run is a dry run.
30 2 * * * /path/to/shekel/scripts/backup_retention.sh --force >> /var/log/shekel_backup.log 2>&1

# Weekly backup verification (Sunday 3:00 AM).
0 3 * * 0 /path/to/shekel/scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1) >> /var/log/shekel_backup.log 2>&1

# Weekly integrity check on production database (Sunday 3:30 AM).
30 3 * * 0 docker exec shekel-prod-app python scripts/integrity_check.py >> /var/log/shekel_backup.log 2>&1

# Daily audit log retention cleanup at 3:00 AM.
0 3 * * * docker exec shekel-prod-app python scripts/audit_cleanup.py >> /var/log/shekel_backup.log 2>&1
```

Replace `/path/to/shekel/` with the actual path to the Shekel project directory on the host.

An install whose retention line ran without `--force` (as earlier versions of this runbook had it)
has never pruned: run `./scripts/backup_retention.sh --dry-run` first and read what the first
`--force` run will delete.

### 3.2 NAS Mount Configuration

#### NFS Option

```bash
# Install NFS utilities.
sudo pacman -S nfs-utils

# Create mount point.
sudo mkdir -p /mnt/nas/backups/shekel

# Add to /etc/fstab (replace nas-ip and volume path with your NAS details).
# The soft,timeo=150,retrans=3 options prevent the host from hanging if the NAS is unreachable.
echo 'nas-ip:/volume1/backups/shekel  /mnt/nas/backups/shekel  nfs  defaults,soft,timeo=150,retrans=3  0  0' | sudo tee -a /etc/fstab

# Mount and verify.
sudo mount -a
touch /mnt/nas/backups/shekel/.mount_test && rm /mnt/nas/backups/shekel/.mount_test
echo "NFS mount verified."
```

#### CIFS/SMB Option

```bash
# Install CIFS utilities.
sudo pacman -S cifs-utils

# Create mount point.
sudo mkdir -p /mnt/nas/backups/shekel

# Create credentials file (not world-readable).
sudo tee /root/.nas-credentials <<EOF
username=your_nas_user
password=your_nas_password
EOF
sudo chmod 600 /root/.nas-credentials

# Add to /etc/fstab (replace nas-ip and share name with your NAS details).
echo '//nas-ip/backups/shekel  /mnt/nas/backups/shekel  cifs  credentials=/root/.nas-credentials,uid=root,gid=root,file_mode=0600,dir_mode=0700  0  0' | sudo tee -a /etc/fstab

# Mount and verify.
sudo mount -a
touch /mnt/nas/backups/shekel/.mount_test && rm /mnt/nas/backups/shekel/.mount_test
echo "CIFS mount verified."
```

### 3.3 Encryption Setup

GPG symmetric encryption protects backup files at rest: the dump streams through gzip and gpg
(AES-256) into the backup file, so no unencrypted copy is ever written to disk. `backup.sh`,
`restore.sh` and `verify_backup.sh` read the passphrase from ONE file, the key file
`/etc/shekel/backup-passphrase` (its first line); there is no setting to move it, and no environment
variable carries it. Each script refuses the key file unless it is a regular file (not a symbolic
link) owned by the user running the script, with no permission for the group or others (mode `0600`,
say), and a non-empty first line. Run the three scripts as that user (with `sudo` for a root-owned
key file).

Without a usable key file, `backup.sh` writes nothing and exits 1, unless `--no-encrypt` asks for an
unencrypted backup; `restore.sh` stops before touching anything (§6.2). An install that still sets
the retired `BACKUP_ENCRYPTION_PASSPHRASE` variable is refused by all three scripts until it is
removed, wherever it is set.

Create the key file as the user whose crontab runs the backup scripts (`root` if root's crontab
does). Each command below refuses to overwrite an existing key file (`set -C`): replacing the
passphrase makes every backup encrypted with it unreadable by the scripts.

**Moving from the retired variable** (an install whose backups were encrypted with
`BACKUP_ENCRYPTION_PASSPHRASE`): write that variable's CURRENT value as the key file's first line,
so the backups it encrypted still decrypt, and only then remove the variable. Do NOT generate a new
passphrase while those backups are kept.

```bash
sudo mkdir -p /etc/shekel   # an existing directory keeps its mode
# Paste the passphrase the variable held, press Enter, then Ctrl-D.  It reaches the file
# through the terminal, never through a command line or the shell history.
sudo sh -c 'set -C && umask 077 && cat > /etc/shekel/backup-passphrase'
```

**A new install**: write a strong new passphrase.

```bash
sudo mkdir -p /etc/shekel   # an existing directory keeps its mode
sudo sh -c 'set -C && umask 077 \
    && python3 -c "import secrets; print(secrets.token_urlsafe(32))" > /etc/shekel/backup-passphrase'
sudo ls -l /etc/shekel/backup-passphrase   # -rw------- root root
```

For another user, `chown` the file to that user and keep the mode `0600`.

**Critical**: Copy the passphrase into a password manager, separately from the backups. If you store
backups on a NAS, the passphrase must not be on the NAS. A restore on a fresh machine needs the key
file written first, from that copy. A lost passphrase means unrecoverable backups. A backup decrypts
only with the passphrase it was made with: keep each passphrase for as long as its backups are kept,
and to restore or verify a backup made under an earlier one, put that passphrase in the key file for
the run, then put the current one back.

---

## 4. Manual Backup

```bash
# Standard backup (local + NAS).
./scripts/backup.sh

# Local only (skip NAS copy).
./scripts/backup.sh --no-nas

# Custom local directory.
./scripts/backup.sh --local-dir /tmp/my_backup --no-nas

# An UNENCRYPTED backup (.sql.gz), by explicit choice.
./scripts/backup.sh --no-encrypt

# Look at the backup file (as the key file's owner).
ls -lh /var/backups/shekel/shekel_backup_*
sudo gpg --batch --quiet --passphrase-file /etc/shekel/backup-passphrase \
    -d /var/backups/shekel/shekel_backup_LATEST.sql.gz.gpg | zcat | head -20
```

Each backup file is created readable by its owner only (`0600`), and a backup directory the script
creates is `0700`; an existing directory keeps its mode.

---

## 5. Retention Policy

| Tier | Criteria | Kept For |
|------|----------|----------|
| Daily | All backups | `RETENTION_DAILY_DAYS` days |
| Weekly | Backups from Sundays | `RETENTION_WEEKLY_WEEKS` weeks |
| Monthly | Backups from the 1st of the month | `RETENTION_MONTHLY_MONTHS` months |

`./scripts/backup_retention.sh --help` prints each setting's default and the periods a run would
use.

Classification is based on the date in the backup filename (`shekel_backup_YYYYMMDD_HHMMSS.sql.gz`),
not the file modification time. A file that qualifies for a higher tier (e.g., a Sunday that is also
the 1st) is always retained by the highest applicable tier.

```bash
# Preview what would be deleted.
./scripts/backup_retention.sh --dry-run

# Run retention cleanup.  --force is what deletes: without it every run is a dry run.
./scripts/backup_retention.sh --force

# Preview with other retention periods (environment variables); --force to delete.
RETENTION_DAILY_DAYS=14 RETENTION_WEEKLY_WEEKS=8 ./scripts/backup_retention.sh --dry-run
```

---

## 6. Restore Procedure

### 6.1 Identify the Backup to Restore

```bash
# List local backups (newest first).
ls -lht /var/backups/shekel/

# List NAS backups.
ls -lht /mnt/nas/backups/shekel/

# Find a backup from a specific date.
ls /var/backups/shekel/shekel_backup_20260315_*.sql.gz*
```

### 6.2 Run the Restore

```bash
# Restore from a local backup.
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz

# The script will display a confirmation prompt:
#   WARNING: This will REPLACE ALL DATA in the Shekel database
#   Are you sure you want to continue? [y/N]
# Type "y" and press Enter to proceed.

# An encrypted backup decrypts with the key file (§3.3); on a fresh machine, write it first.
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz.gpg
```

The restore script will:

1. Validate the backup file end to end (decrypt, gzip integrity, non-empty) before anything else
2. Save a safety dump of the CURRENT database, encrypted with the key file, as
   `pre_restore_safety_<timestamp>.sql.gz.gpg` in the local backup directory (`BACKUP_LOCAL_DIR`)
3. Stop the application container (if it exists)
4. Drop and recreate the database
5. Restore from the backup file
6. Start the application container (which runs Alembic migrations via the entrypoint)
7. Run basic verification checks

Without a usable key file the script stops before step 1, touching nothing: an encrypted backup
cannot be read, and the safety dump cannot be encrypted. For an unencrypted backup,
`--plaintext-safety-dump` writes the safety dump unencrypted (`.sql.gz`) instead, and
`--skip-safety-dump` takes none (the disaster case, where the current database is already lost).

### 6.3 Verify the Restore

```bash
# Check the application.
curl -s http://localhost:5000/login | head -5

# Verify database contents.
docker exec shekel-prod-db psql -U shekel_user -d shekel -c "SELECT COUNT(*) FROM auth.users;"
docker exec shekel-prod-db psql -U shekel_user -d shekel -c "SELECT COUNT(*) FROM budget.pay_periods;"

# Run integrity checks.
docker exec shekel-prod-app python scripts/integrity_check.py --verbose
```

### 6.4 Restoring from NAS

If local backups are unavailable, copy from the NAS first:

```bash
cp /mnt/nas/backups/shekel/shekel_backup_20260315_020000.sql.gz.gpg /tmp/
./scripts/restore.sh /tmp/shekel_backup_20260315_020000.sql.gz.gpg
rm /tmp/shekel_backup_20260315_020000.sql.gz.gpg
```

---

## 7. Backup Verification

The verification script restores a backup to a temporary database (the `VERIFY_DB` setting;
`./scripts/verify_backup.sh --help` prints its default, a name no two runs at the same time share),
runs sanity queries and integrity checks, then drops the temporary database. The production database
is never touched.

```bash
# Verify the most recent backup.
./scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1)
```

### What It Checks

**Sanity queries** (via `psql`):

- `auth.users` has at least one row
- `budget.pay_periods` row count and date range
- `budget.transactions` row count (informational)
- `budget.accounts` has at least one row
- `ref.account_types` is populated
- `system.audit_log` table exists
- `public.alembic_version` has a value

**Integrity checks** (via `integrity_check.py`):

- 15 referential integrity checks (FK violations)
- 5 orphan detection checks
- 3 balance anomaly checks
- 11 data consistency checks

### Expected Output

| Status | Meaning |
|--------|---------|
| **PASS** | All sanity and integrity checks passed |
| **PASS WITH WARNINGS** | Sanity checks passed; integrity checks found warnings but no critical failures |
| **FAIL** | One or more sanity or critical integrity checks failed |

**Schedule**: cron (§3.1).

---

## 8. Integrity Checks

The integrity check script validates the database without modifying any data.

```bash
# Run all checks (inside the app container in production).
docker exec shekel-prod-app python scripts/integrity_check.py

# Run all checks with verbose output.
docker exec shekel-prod-app python scripts/integrity_check.py --verbose

# Run only referential integrity checks.
docker exec shekel-prod-app python scripts/integrity_check.py --category referential

# Run locally in development.
DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5432/shekel \
    python scripts/integrity_check.py --verbose
```

### Check Categories

| Category | Checks | Severity | What It Detects |
|----------|--------|----------|-----------------|
| `referential` | FK-01 to FK-16 (no FK-03) | Critical | Foreign key references to nonexistent rows |
| `orphan` | OR-01, OR-03 to OR-06 | Warning | Records disconnected from the data model |
| `balance` | BA-01, BA-05, BA-06 | Mixed | Anchor balance and pay period anomalies |
| `consistency` | DC-02 to DC-12 | Mixed | Cross-table logical inconsistencies |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | One or more critical checks failed |
| 2 | Warnings only (no critical failures) |
| 3 | Script error (connection failure, bad arguments) |

### What to Do When a Check Fails

- **Critical failures (FK-*, DC-01, DC-06, DC-07, DC-08)**: investigate immediately. These indicate
  data corruption or a broken restore. Check recent changes, restore from a known-good backup if
  needed.
- **Warnings (OR-*, BA-*, DC-02 to DC-05, DC-09)**: investigate when convenient. These indicate
  potential issues but the application will function correctly. Common causes: unused categories
  from initial seed, accounts not yet configured with type-specific parameters.

---

## 9. Troubleshooting

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Database container is not running" | `shekel-prod-db` stopped | `docker start shekel-prod-db` |
| "NAS directory does not exist" | NAS not mounted | `sudo mount -a` and verify with `mount \| grep nas` |
| "Could not write ...: pg_dump exited N, gzip N, gpg N" | Disk full, a pg_dump failure, or gpg failing | Check `df -h`, `docker logs shekel-prod-db` and the gpg message above it |
| "Cannot use the backup key file" | The key file is missing, a link, not yours, readable by others, or its first line is empty | Write it as §3.3 says |
| "BACKUP_ENCRYPTION_PASSPHRASE is set, but no backup script reads it" | The retired variable is still set (crontab, profile, wrapper) | Write the variable's value into the key file (§3.3, "Moving from the retired variable"), then remove the variable |
| "Restore failed: permission denied" | `PGUSER` lacks CREATEDB privilege | Run: `docker exec shekel-prod-db psql -U postgres -c "ALTER USER shekel_user CREATEDB;"` |
| "Integrity CRITICAL failures after restore" | Corrupt or partial backup | Try restoring from an older backup |
| A `shekel_verify_<pid>` database left behind | A verify run ended without dropping its temporary database: it was killed with `SIGKILL` or the host went down, so its exit cleanup never ran, or that cleanup's `DROP` failed, its error not shown (for example, a signal stopped the run mid-restore while the restore still held the database). Each run names its database for its own process id, so a later run drops a leftover only if it reuses that id | List them: `docker exec shekel-prod-db psql -U shekel_user -d postgres -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'shekel\_verify\_%'"`; skip any whose `<pid>` is a verify run still going (`ps -p <pid> -o args=`), then drop each by name: `docker exec shekel-prod-db psql -U shekel_user -d postgres -c "DROP DATABASE shekel_verify_<pid>;"` |

### Log Files

| Log | Location | Contents |
|-----|----------|----------|
| Backup/retention/verify logs | `/var/log/shekel_backup.log` | Cron job output (if redirected) |
| Application structured logs | `docker logs shekel-prod-app` | JSON-formatted request and event logs |
| PostgreSQL logs | `docker logs shekel-prod-db` | Database server logs |

---

## 10. Environment Variables Reference

The backup scripts read their settings from the environment of the shell, or the cron line, that
runs them, never from `.env` (the passphrase is no setting: it is the key file, §3.3). Each one's
`--help` lists every setting it reads, with its default:

```bash
./scripts/backup.sh --help
./scripts/backup_retention.sh --help
./scripts/restore.sh --help
./scripts/verify_backup.sh --help
```

Each default's one home is the table `SHEKEL_SETTING_DEFAULTS` in `scripts/_settings_lib.sh` (the
production containers' names come from `scripts/_docker_lib.sh`); the paths in this runbook's
commands are examples.
