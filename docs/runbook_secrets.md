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
2. Update `SECRET_KEY` in `.env`
3. Restart the app container: `docker compose restart app`
4. Impact: all active sessions are invalidated; users must re-authenticate

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
