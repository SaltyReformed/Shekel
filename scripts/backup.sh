#!/bin/bash
# Shekel Budget App -- Automated Database Backup
#
# Creates a compressed pg_dump of the Shekel database with a timestamped
# filename, copies to local and NAS destinations, and optionally encrypts
# the output with GPG.
#
# Usage:
#     ./scripts/backup.sh [OPTIONS]
#
# Options:
#     --local-dir DIR     Local backup directory (default: /var/backups/shekel)
#     --nas-dir DIR       NAS backup directory (default: /mnt/nas/backups/shekel)
#     --no-nas            Skip NAS copy (local backup only)
#     --encrypt           Force encryption (requires BACKUP_ENCRYPTION_PASSPHRASE)
#     --help              Show this help message
#
# Exit codes:
#     0   Backup completed successfully (local backup always succeeds for exit 0)
#     1   Fatal error (database unreachable, pg_dump failure, empty output)
#
# Cron example (daily at 2:00 AM):
#     0 2 * * * /path/to/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
# All values can be overridden via environment variables.

BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/shekel}"
BACKUP_NAS_DIR="${BACKUP_NAS_DIR:-/mnt/nas/backups/shekel}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"

# Docker container names.
DB_CONTAINER="${DB_CONTAINER:-shekel-db}"

# Database connection (used inside the db container).
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"

# Timestamp format for filenames.
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILENAME="shekel_backup_${TIMESTAMP}.sql.gz"

# ── Functions ────────────────────────────────────────────────────

log() {
    # Structured log output: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
    local level="$1"
    shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Create a compressed PostgreSQL backup of the Shekel database.

Options:
    --local-dir DIR     Local backup directory (default: /var/backups/shekel)
    --nas-dir DIR       NAS backup directory (default: /mnt/nas/backups/shekel)
    --no-nas            Skip NAS copy (local only)
    --encrypt           Force encryption (requires BACKUP_ENCRYPTION_PASSPHRASE)
    --help              Show this help message

Environment Variables:
    BACKUP_LOCAL_DIR              Local backup directory
    BACKUP_NAS_DIR                NAS backup directory
    BACKUP_ENCRYPTION_PASSPHRASE  GPG passphrase for encryption (optional)
    DB_CONTAINER                  Docker container name for PostgreSQL
    PGUSER                        PostgreSQL user
    PGDATABASE                    PostgreSQL database name
EOF
}

check_prerequisites() {
    # Verify docker is available.
    if ! command -v docker &>/dev/null; then
        log "ERROR" "docker command not found"
        exit 1
    fi

    # Verify the database container is running.
    if ! docker inspect --format='{{.State.Running}}' "${DB_CONTAINER}" 2>/dev/null | grep -q true; then
        log "ERROR" "Database container '${DB_CONTAINER}' is not running"
        exit 1
    fi

    # Create local backup directory if it does not exist.
    mkdir -p "${BACKUP_LOCAL_DIR}"
}

create_backup() {
    # Run pg_dump inside the database container, compress, and write to local dir.
    local local_path="${BACKUP_LOCAL_DIR}/${BACKUP_FILENAME}"

    log "INFO" "Starting backup: ${BACKUP_FILENAME}"
    log "INFO" "Database: ${PGDATABASE} | User: ${PGUSER} | Container: ${DB_CONTAINER}"

    # pg_dump flags:
    #   --clean:          include DROP statements before CREATE
    #   --if-exists:      add IF EXISTS to DROP statements
    #   --no-owner:       omit ownership commands (portable across environments)
    #   --no-privileges:  omit GRANT/REVOKE (portable)
    #   --schema:         dump only application schemas (not pg_catalog, etc.)
    #
    # Schemas dumped:
    #   public  -- contains alembic_version (migration state)
    #   ref     -- lookup/reference tables
    #   auth    -- users, sessions, MFA
    #   budget  -- pay periods, transactions, accounts, templates
    #   salary  -- salary profiles, deductions, tax configs
    #   system  -- audit_log
    #
    # Output is piped through gzip for compression.
    docker exec "${DB_CONTAINER}" pg_dump \
        -U "${PGUSER}" \
        -d "${PGDATABASE}" \
        --clean \
        --if-exists \
        --no-owner \
        --no-privileges \
        --schema=public \
        --schema=ref \
        --schema=auth \
        --schema=budget \
        --schema=salary \
        --schema=system \
        | gzip > "${local_path}"

    # Verify the file was created and is not empty.
    if [[ ! -s "${local_path}" ]]; then
        log "ERROR" "Backup file is empty or was not created: ${local_path}"
        exit 1
    fi

    local size
    size=$(du -h "${local_path}" | cut -f1)
    log "INFO" "Local backup created: ${local_path} (${size})"
}

encrypt_backup() {
    # Optionally encrypt the backup file with GPG symmetric encryption.
    if [[ -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
        return 0
    fi

    local local_path="${BACKUP_LOCAL_DIR}/${BACKUP_FILENAME}"
    local encrypted_path="${local_path}.gpg"

    log "INFO" "Encrypting backup with AES-256..."
    echo "${BACKUP_ENCRYPTION_PASSPHRASE}" | gpg --batch --yes --passphrase-fd 0 \
        --symmetric --cipher-algo AES256 \
        --output "${encrypted_path}" \
        "${local_path}"

    # Remove the unencrypted file.
    rm -f "${local_path}"

    # Update the filename to include .gpg extension.
    BACKUP_FILENAME="${BACKUP_FILENAME}.gpg"
    log "INFO" "Encrypted backup: ${encrypted_path}"
}

copy_to_nas() {
    # Copy the backup file to the NAS mount point.
    # Returns 0 on success, 1 on failure (non-fatal -- local backup already exists).
    local local_path="${BACKUP_LOCAL_DIR}/${BACKUP_FILENAME}"
    local nas_path="${BACKUP_NAS_DIR}/${BACKUP_FILENAME}"

    # Check if NAS is mounted and accessible.
    if [[ ! -d "${BACKUP_NAS_DIR}" ]]; then
        log "WARNING" "NAS directory does not exist: ${BACKUP_NAS_DIR}"
        return 1
    fi

    if ! touch "${BACKUP_NAS_DIR}/.backup_test" 2>/dev/null; then
        log "WARNING" "NAS directory is not writable: ${BACKUP_NAS_DIR}"
        rm -f "${BACKUP_NAS_DIR}/.backup_test" 2>/dev/null
        return 1
    fi
    rm -f "${BACKUP_NAS_DIR}/.backup_test"

    cp "${local_path}" "${nas_path}"
    log "INFO" "NAS copy complete: ${nas_path}"
    return 0
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local skip_nas=false

    # Parse command-line arguments.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --local-dir)
                BACKUP_LOCAL_DIR="$2"
                shift 2
                ;;
            --nas-dir)
                BACKUP_NAS_DIR="$2"
                shift 2
                ;;
            --no-nas)
                skip_nas=true
                shift
                ;;
            --encrypt)
                if [[ -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
                    log "ERROR" "--encrypt requires BACKUP_ENCRYPTION_PASSPHRASE to be set"
                    exit 1
                fi
                shift
                ;;
            --help)
                usage
                exit 0
                ;;
            *)
                log "ERROR" "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done

    check_prerequisites

    # Create the backup.
    create_backup

    # Encrypt if passphrase is set.
    encrypt_backup

    # Copy to NAS (non-fatal on failure).
    local nas_status=0
    if [[ "${skip_nas}" == false ]]; then
        copy_to_nas || nas_status=1
    else
        log "INFO" "NAS copy skipped (--no-nas)"
    fi

    # Final status.
    if [[ ${nas_status} -eq 0 ]]; then
        log "INFO" "Backup complete: ${BACKUP_FILENAME}"
        exit 0
    else
        log "WARNING" "Backup complete (local only). NAS copy failed."
        # Exit 0 because the local backup succeeded.
        # The NAS failure is logged as a warning.
        # Monitoring should alert on WARNING-level log entries.
        exit 0
    fi
}

main "$@"
