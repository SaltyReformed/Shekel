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
#     --encrypt           Require encryption: fail fast when
#                         BACKUP_ENCRYPTION_PASSPHRASE is not set. (Encryption
#                         itself is keyed on the passphrase being present --
#                         with it set, backups are encrypted with or without
#                         this flag; the flag turns "passphrase missing" from
#                         a silent plaintext backup into a hard error.)
#     --help              Show this help message
#
# Exit codes:
#     0   Backup completed successfully (local backup always succeeds for exit 0)
#     1   Fatal error (database unreachable, pg_dump failure, empty output)
#
# Partial-file discipline (polyglot audit 2026-06-12, OPS/SH-04): every
# artifact is written to a .tmp path and renamed into place only after the
# producing pipeline succeeded, and an EXIT trap removes stragglers -- so a
# mid-stream pg_dump/gpg/cp failure can never leave a truncated file wearing
# a valid backup name for retention/verification to mistake for a real one.
#
# Cron example (daily at 2:00 AM):
#     0 2 * * * /path/to/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_backup_lib.sh"

# ── Configuration ────────────────────────────────────────────────
# All values can be overridden via environment variables.

BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/shekel}"
BACKUP_NAS_DIR="${BACKUP_NAS_DIR:-/mnt/nas/backups/shekel}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"

# Docker container names.
DB_CONTAINER="${DB_CONTAINER:-shekel-prod-db}"

# Database connection (used inside the db container).
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"

# Timestamp format for filenames.
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILENAME="shekel_backup_${TIMESTAMP}.sql.gz"

# Temp files pending rename; the EXIT trap sweeps whatever a failure left.
# if-form (not `[[ ]] &&`) because errexit is ACTIVE inside an EXIT trap: a
# falsy condition as the trap's last command would override the script's
# real exit status with 1 -- the same errexit-semantics class this whole
# family was audited for (caught by the Phase 2 test battery).
_TMP_FILES=()
_cleanup_tmp() {
    local f
    for f in "${_TMP_FILES[@]:-}"; do
        if [[ -n "${f}" && -f "${f}" ]]; then
            rm -f "${f}"
        fi
    done
    return 0
}
trap _cleanup_tmp EXIT

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Create a compressed PostgreSQL backup of the Shekel database.

Options:
    --local-dir DIR     Local backup directory (default: /var/backups/shekel)
    --nas-dir DIR       NAS backup directory (default: /mnt/nas/backups/shekel)
    --no-nas            Skip NAS copy (local only)
    --encrypt           Require encryption (fail fast without
                        BACKUP_ENCRYPTION_PASSPHRASE; see header)
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
    require_db_container "${DB_CONTAINER}" || exit 1
    # Create local backup directory if it does not exist.
    mkdir -p "${BACKUP_LOCAL_DIR}"
}

create_backup() {
    # Run pg_dump inside the database container, compress, and write to local
    # dir via temp-file + rename (see header).
    local local_path="${BACKUP_LOCAL_DIR}/${BACKUP_FILENAME}"
    local tmp_path="${local_path}.tmp"

    log "INFO" "Starting backup: ${BACKUP_FILENAME}"
    log "INFO" "Database: ${PGDATABASE} | User: ${PGUSER} | Container: ${DB_CONTAINER}"

    _TMP_FILES+=("${tmp_path}")

    # pg_dump flags:
    #   --clean:          include DROP statements before CREATE
    #   --if-exists:      add IF EXISTS to DROP statements
    #   --no-owner:       omit ownership commands (portable across environments)
    #   --no-privileges:  omit GRANT/REVOKE (portable)
    #   --schema:         dump only application schemas (not pg_catalog, etc.)
    #
    # The schema list is the shared SHEKEL_DUMP_SCHEMAS from _backup_lib.sh --
    # the single source restore.sh and verify_backup.sh also read, so a new
    # schema added there is automatically dumped here (OPS/SH-06).
    #
    # shellcheck disable=SC2046 # word splitting of the --schema flags is intended
    if ! docker exec "${DB_CONTAINER}" pg_dump \
        -U "${PGUSER}" \
        -d "${PGDATABASE}" \
        --clean \
        --if-exists \
        --no-owner \
        --no-privileges \
        $(shekel_pg_dump_schema_flags) \
        | gzip > "${tmp_path}"; then
        log "ERROR" "pg_dump pipeline failed; partial file removed by trap"
        exit 1
    fi

    # Verify the temp file is not empty before promoting it.
    if [[ ! -s "${tmp_path}" ]]; then
        log "ERROR" "Backup output is empty: ${tmp_path}"
        exit 1
    fi
    mv "${tmp_path}" "${local_path}"

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
    local tmp_path="${encrypted_path}.tmp"

    log "INFO" "Encrypting backup with AES-256..."
    _TMP_FILES+=("${tmp_path}")
    if ! echo "${BACKUP_ENCRYPTION_PASSPHRASE}" | gpg --batch --yes --passphrase-fd 0 \
        --symmetric --cipher-algo AES256 \
        --output "${tmp_path}" \
        "${local_path}"; then
        log "ERROR" "GPG encryption failed; unencrypted backup retained at ${local_path}"
        exit 1
    fi
    mv "${tmp_path}" "${encrypted_path}"

    # Remove the unencrypted file only after the encrypted one is in place.
    rm -f "${local_path}"

    # Update the filename to include .gpg extension.
    BACKUP_FILENAME="${BACKUP_FILENAME}.gpg"
    log "INFO" "Encrypted backup: ${encrypted_path}"
}

copy_to_nas() {
    # Copy the backup file to the NAS mount point.
    # Returns 0 on success, 1 on failure (non-fatal -- local backup already
    # exists). EXPLICIT failure handling throughout: this function is invoked
    # in a `|| nas_status=1` context, which disables errexit for the whole
    # body (audit OPS/SH-03 -- a partial `cp` previously fell through to the
    # success log and the script exited 0 with a corrupt NAS copy).
    local local_path="${BACKUP_LOCAL_DIR}/${BACKUP_FILENAME}"
    local nas_path="${BACKUP_NAS_DIR}/${BACKUP_FILENAME}"
    local nas_tmp="${nas_path}.tmp"

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

    _TMP_FILES+=("${nas_tmp}")
    if ! cp "${local_path}" "${nas_tmp}"; then
        log "WARNING" "NAS copy FAILED (disk full / IO error?); partial file removed"
        rm -f "${nas_tmp}"
        return 1
    fi
    # Byte-for-byte comparison: a touch-test proves writability, not that the
    # full copy landed intact (NAS-full mid-copy truncates without an error
    # from every filesystem/protocol combination).
    if ! cmp -s "${local_path}" "${nas_tmp}"; then
        log "WARNING" "NAS copy MISMATCH after write (truncated?); removed"
        rm -f "${nas_tmp}"
        return 1
    fi
    if ! mv "${nas_tmp}" "${nas_path}"; then
        log "WARNING" "NAS rename failed; partial file removed"
        rm -f "${nas_tmp}"
        return 1
    fi
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
