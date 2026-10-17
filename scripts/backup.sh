#!/bin/bash
# Shekel Budget App -- Automated Database Backup
#
# Creates a compressed, GPG-encrypted pg_dump of the Shekel database with a
# timestamped filename and copies it to local and NAS destinations.  The
# passphrase is the first line of the backup key file (SHEKEL_BACKUP_KEY_FILE
# in _backup_lib.sh; --help prints its path).  Without a usable key file the
# script REFUSES and writes nothing, unless --no-encrypt chooses an
# unencrypted backup (Josh's rulings R-BAL304 and R-BAL305).
#
# Options and settings, each setting with its default: ./scripts/backup.sh --help
#
# Exit codes:
#     0   Backup completed successfully (local backup always succeeds for exit 0)
#     1   Fatal error (no usable key file, database unreachable, pg_dump failure)
#
# Partial-file discipline (polyglot audit 2026-06-12, OPS/SH-04): every
# artifact is written under a partial name (partial_path_for in
# _backup_lib.sh) and renamed into place only after the producing pipeline
# succeeded, and an EXIT trap removes stragglers -- so a mid-stream
# pg_dump/gpg/cp failure can never leave a truncated file wearing a valid
# backup name for retention/verification to mistake for a real one.  Every
# file is created 0600 (finding BAL-663).
#
# Cron example (daily at 2:00 AM; the line carries no secret):
#     0 2 * * * /path/to/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_backup_lib.sh"

# ── Configuration ────────────────────────────────────────────────
# Each read from the environment, else its default in _backup_lib.sh.
read_settings \
    BACKUP_LOCAL_DIR "Local backup directory" \
    BACKUP_NAS_DIR "NAS backup directory" \
    DB_CONTAINER "Docker container name for PostgreSQL" \
    PGUSER "PostgreSQL user" \
    PGDATABASE "PostgreSQL database name"

# Timestamp format for filenames.  main adds .gpg for an encrypted backup.
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILENAME="shekel_backup_${TIMESTAMP}.sql.gz"

# Temp files pending rename; the EXIT trap sweeps whatever a failure left.
# if-form (not `[[ ]] &&`) because errexit is ACTIVE inside an EXIT trap: a
# falsy condition as the trap's last command would override the script's
# real exit status with 1 -- the same errexit-semantics class this whole
# family was audited for (caught by the Phase 2 test battery).
_TMP_FILES=()
# shellcheck disable=SC2329 # invoked indirectly via the EXIT trap registered below, never called by name
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

Create a compressed, encrypted PostgreSQL backup of the Shekel database.

The backup is encrypted with the passphrase on the first line of the key file
${SHEKEL_BACKUP_KEY_FILE} (owned by the user running this script, no access
for the group or others).  Without a usable key file nothing is written,
unless --no-encrypt.

Options:
    --local-dir DIR     Write the backup to DIR instead of BACKUP_LOCAL_DIR
    --nas-dir DIR       Copy it to DIR instead of BACKUP_NAS_DIR
    --no-nas            Skip NAS copy (local only)
    --no-encrypt        Write an UNENCRYPTED backup (.sql.gz)
    --help              Show this help message

EOF
    print_settings
}

check_prerequisites() {
    # shellcheck disable=SC2310 # require_db_container is a boolean predicate (explicit 'return 1' on every failure path, not set -e); the trailing '|| exit 1' aborts the script on its failure
    require_db_container "${DB_CONTAINER}" || exit 1
}

create_backup() {
    # Dump, compress and (for a .gpg name) encrypt in ONE stream into the
    # local directory, which write_dump_artifact creates 0700 when absent
    # (see header).  The EXIT trap is the backstop for its partial file when
    # a signal ends the run.
    local local_path="${BACKUP_LOCAL_DIR}/${BACKUP_FILENAME}"
    local partial

    log "INFO" "Starting backup: ${BACKUP_FILENAME}"
    log "INFO" "Database: ${PGDATABASE} | User: ${PGUSER} | Container: ${DB_CONTAINER}"

    partial_path_for partial "${local_path}"
    _TMP_FILES+=("${partial}")
    write_dump_artifact "${local_path}" "No backup was written; fix the cause above and re-run."

    local size
    size=$(du -h "${local_path}" | cut -f1)
    log "INFO" "Local backup created: ${local_path} (${size})"
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
    local nas_tmp
    partial_path_for nas_tmp "${nas_path}"

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
    # Created 0600 (finding BAL-663): cp gives a new file the source's mode
    # under the umask, and keeps an existing one's, so a straggler goes first.
    rm -f "${nas_tmp}"
    if ! (umask 077 && cp "${local_path}" "${nas_tmp}"); then
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
    local encrypt=true

    refuse_retired_passphrase_variable

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
            --no-encrypt)
                encrypt=false
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

    # Encrypted with a usable key file, unencrypted by explicit choice, or
    # refused here, before anything is touched (R-BAL304).
    if [[ "${encrypt}" == true ]]; then
        require_backup_key "Or pass --no-encrypt to write an UNENCRYPTED backup."
        BACKUP_FILENAME="${BACKUP_FILENAME}.gpg"
    else
        note_plaintext_choice "--no-encrypt" "the backup"
    fi

    check_prerequisites

    # Create the backup.
    create_backup

    # Copy to NAS (non-fatal on failure).
    local nas_status=0
    if [[ "${skip_nas}" == false ]]; then
        # shellcheck disable=SC2310 # copy_to_nas is a boolean predicate (explicit 'return 1' on every failure path per OPS/SH-03); NAS failure is intentionally non-fatal, captured here as nas_status=1 and surfaced as a WARNING
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
