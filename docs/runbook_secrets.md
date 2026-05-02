# Secret Management -- Operations Runbook

## Secret Inventory

The Shekel application requires three secrets for production operation.
All other environment variables have safe defaults or are non-sensitive.

| Secret | Purpose | Generation Command | Rotation Impact |
|--------|---------|-------------------|-----------------|
| `SECRET_KEY` | Flask session cookie encryption | `python -c "import secrets; print(secrets.token_hex(32))"` | All active sessions are invalidated; users must log in again |
| `TOTP_ENCRYPTION_KEY` | Fernet encryption of TOTP secrets stored in database | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | **DESTRUCTIVE if changed**: all MFA configurations become unreadable; users must re-enroll MFA |
| `POSTGRES_PASSWORD` | PostgreSQL database authentication | Any strong password generator | Requires updating both the db service and app service configs simultaneously; restart both containers |

## Where Secrets Are Stored

Secrets are stored in the `.env` file on the Proxmox host, in the same
directory as the `docker-compose.yml` file (typically `/opt/shekel/.env`).

- The `.env` file is excluded from version control (`.gitignore`).
- The `.env` file should be readable only by root: `chmod 600 .env`
- Docker Compose reads `.env` automatically and injects values into
  container environment variables.

## Secret Rotation Procedures

### Rotating SECRET_KEY

1. Generate a new key: `python -c "import secrets; print(secrets.token_hex(32))"`
   The output is a 64-character hex string (256 bits of entropy).  The
   production config rejects any value shorter than 32 characters or
   matching a known placeholder.
2. Update `SECRET_KEY` in `.env`.
3. Restart the app container: `docker compose restart app`.
4. Run the global session-invalidation script (see next section) so
   that any cookies signed under the old key are rejected even if an
   attacker captured them before the key was rotated.
5. Impact: all active sessions are invalidated; users must
   re-authenticate.

### Post-rotation session invalidation

Rotating `SECRET_KEY` makes every previously-issued session cookie
unverifiable on the new key.  However, any cookie an attacker captured
before the rotation can still be reused by anyone who learns the
*old* key (for example, anyone with read access to git history that
contains a previously-leaked key).  The defense-in-depth control is
to bump `users.session_invalidated_at` for every row, which causes
the `load_user` callback in `app/__init__.py` to reject any session
older than the bump time -- regardless of which key it was signed
with.

Run after every `SECRET_KEY` rotation, and after any git history
rewrite that excises a historically-leaked key:

```bash
docker exec shekel-prod-app python scripts/rotate_sessions.py --confirm
```

The script bumps `session_invalidated_at` to `now()` for every row
in `auth.users`.  It is idempotent (repeated runs simply move the
timestamp forward) and emits a structured log event
`sessions_invalidated_global` at WARNING level so the audit log
captures the operation.

The `--confirm` flag is mandatory; running without it prints a usage
hint and exits with code 1.

#### Rewriting git history to remove a leaked SECRET_KEY

If a `SECRET_KEY` value was committed to git history (for example,
audit finding F-001), rotating the live key is necessary but not
sufficient: anyone with access to the repository's object store can
still extract the historical key from the dangling blob.  The full
remediation is:

1. **Inventory clones.**  Track every clone of the repository
   (developer machines, CI caches, audit-branch snapshots, NAS
   backups).  Each will need to be re-cloned after the rewrite.

2. **Rotate the live key first.**  Follow the `Rotating SECRET_KEY`
   procedure above.  This minimises the window during which an
   attacker can use a historical key against current sessions.

3. **Rewrite history.**  Use either tool below.  Both must run
   against a fresh, clean local clone -- not your working copy:

   - `git filter-repo`:
     ```bash
     git clone --mirror <remote-url> shekel.git
     cd shekel.git
     # Replace the literal leaked value with a placeholder marker:
     printf 'OLD_LEAKED_KEY_HERE==>REDACTED\n' > replacements.txt
     git filter-repo --replace-text replacements.txt
     ```
   - BFG Repo-Cleaner:
     ```bash
     git clone --mirror <remote-url> shekel.git
     cd shekel.git
     bfg --replace-text replacements.txt
     git reflog expire --expire=now --all
     git gc --prune=now --aggressive
     ```

4. **Force-push the rewritten history** to the affected branches.
   Coordinate with any other contributors first.  The Shekel
   repository is single-owner, so coordination is trivial.

5. **Run the session-invalidation script** described above so that
   any cookies signed under the historical key are invalidated even
   if an attacker preserved them.

6. **Re-clone everywhere.**  Every existing clone (developer machine,
   CI runner cache, audit-branch snapshot) still has the leaked key
   in its object store.  Delete each clone and re-clone from the
   rewritten remote.  Document this in the audit trail.

7. **Install a pre-commit hook** (gitleaks or detect-secrets) so the
   pattern cannot recur.  This is tracked separately in the audit
   remediation plan.

### Rotating TOTP_ENCRYPTION_KEY

**WARNING: Changing this key makes all existing MFA enrollments unreadable.**

1. Notify all MFA-enrolled users that they will need to re-enroll
2. Disable MFA for all users: `docker exec shekel-app python scripts/reset_mfa.py --all`
3. Generate a new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
4. Update `TOTP_ENCRYPTION_KEY` in `.env`
5. Restart the app container: `docker compose restart app`
6. Users re-enroll MFA via Settings > Security

### Rotating POSTGRES_PASSWORD

1. Generate a new password
2. Update `POSTGRES_PASSWORD` in `.env`
3. Restart both containers: `docker compose down && docker compose up -d`
4. The db container picks up the new password on startup; the app
   container's `DATABASE_URL` references `${POSTGRES_PASSWORD}` and
   is reconstructed from `.env`

## Disaster Recovery: Reconstructing .env

If the Proxmox host is lost and must be rebuilt from scratch:

1. **Restore the database** from NAS backups using `scripts/restore.sh`
   (see backup/restore runbook).

2. **Reconstruct `.env`** using `.env.example` as a template:
   - `SECRET_KEY`: generate a new one. Users will need to log in again.
   - `TOTP_ENCRYPTION_KEY`: if you have the original key backed up
     (see recommendation below), use it. If not, generate a new one
     and all users must re-enroll MFA.
   - `POSTGRES_PASSWORD`: use the password from the restored backup,
     or set a new one and update the PostgreSQL user password.
   - All other variables have defaults or are non-sensitive.

3. **Start the application**: `docker compose up -d`

### Recommendation: Back Up the .env File

Include the `.env` file in your NAS backup strategy. The simplest approach:

```bash
# Add to the backup cron job (after the database backup):
cp /opt/shekel/.env /mnt/nas/backups/shekel/env_backup
```

Alternatively, store the three critical secrets in a password manager
(e.g., Bitwarden, 1Password) as a separate record. This provides an
independent recovery path if both local and NAS storage are lost.

### Recommendation: Document Secrets in a Password Manager

Create a secure note in your password manager with:
- `SECRET_KEY` value
- `TOTP_ENCRYPTION_KEY` value
- `POSTGRES_PASSWORD` value
- Date each secret was last rotated

This is the fastest disaster recovery path: copy values from the password
manager into a fresh `.env` file.
